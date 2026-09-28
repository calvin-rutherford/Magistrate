"""Content-free, bounded-cardinality operations telemetry.

No request bodies, raw paths, URLs, headers, user IDs or exception strings enter
this sink. A collector may ingest the JSON logger or install the error reporter;
that hook receives only the same sanitized record, never an exception object.
Metrics are per process and reset on restart (scrape every worker separately).
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import logging
import threading
import time
from typing import Callable

request_id: ContextVar[str | None] = ContextVar('request_id', default=None)
logger = logging.getLogger('magistrate.operations')
_error_reporter: Callable[[dict], None] | None = None
_lock = threading.Lock()
_metrics: dict[tuple[str, str, str], list[float]] = defaultdict(lambda: [0, 0.0])
_OPERATIONS = frozenset({'http', 'provider', 'routing', 'execution_ingress', 'billing_webhook',
                         'recovery', 'notifications', 'attention'})
_OUTCOMES = frozenset({'ok', 'error', 'rejected', 'timeout'})


def set_error_reporter(reporter: Callable[[dict], None] | None) -> None:
    """In-process trusted integration; no arbitrary caller-controlled telemetry URL."""
    global _error_reporter
    _error_reporter = reporter


def record(operation: str, *, outcome: str, duration: float = 0,
           route: str = 'none', status: int = 0, objective_id: str | None = None) -> None:
    if operation not in _OPERATIONS or outcome not in _OUTCOMES:
        raise ValueError('Unknown telemetry operation/outcome')
    # Route must be a server-owned template supplied by middleware, never a URL.
    duration = max(0.0, duration)
    entry = {'schema': 'magistrate.operations.v1', 'event': operation,
             'outcome': outcome, 'request_id': request_id.get(),
             'duration_ms': round(duration * 1000, 2), 'status': status}
    if objective_id:
        # Stable correlation without reflecting model/client-controlled bytes.
        entry['objective_ref'] = hashlib.sha256(objective_id.encode()).hexdigest()[:24]
    with _lock:
        # Defense in depth for extensions accidentally passing high cardinality.
        key = (operation, route, outcome)
        if key not in _metrics and len(_metrics) >= 1024:
            key = (operation, 'overflow', outcome)
        bucket = _metrics[key]
        bucket[0] += 1
        bucket[1] += duration
    logger.info(json.dumps(entry, separators=(',', ':')))
    if outcome in {'error', 'timeout'} and _error_reporter:
        try:
            _error_reporter(dict(entry))
        except Exception:
            # Reporting cannot take down a product request or echo sink secrets.
            pass


@contextmanager
def operation_span(operation: str, *, objective_id: str | None = None):
    start = time.monotonic()
    try:
        yield
    except Exception:
        record(operation, outcome='error', duration=time.monotonic() - start,
               objective_id=objective_id)
        raise
    else:
        record(operation, outcome='ok', duration=time.monotonic() - start,
               objective_id=objective_id)


def prometheus_metrics() -> str:
    with _lock:
        snapshot = [(key, values[:]) for key, values in _metrics.items()]
    lines = ['# TYPE magistrate_operations_total counter',
             '# TYPE magistrate_operation_seconds_total counter']
    for (operation, route, outcome), (count, duration) in sorted(snapshot):
        labels = f'operation={json.dumps(operation)},route={json.dumps(route)},outcome={json.dumps(outcome)}'
        lines.append(f'magistrate_operations_total{{{labels}}} {int(count)}')
        lines.append(f'magistrate_operation_seconds_total{{{labels}}} {duration:.6f}')
    lines.extend(persisted_metrics())
    return '\n'.join(lines) + '\n'


def persisted_metrics() -> list[str]:
    """Operator-only aggregate evidence; no tools, tenant labels or scheduler polling."""
    from app import db
    from app.persistence import observation_connection
    try:
        with observation_connection(db.DB_PATH) as conn:
            submitting = conn.execute("SELECT COUNT(*) FROM magi_objective_submissions WHERE status='submitting'").fetchone()[0]
            pending_chat = conn.execute("SELECT COUNT(*) FROM magi_messages WHERE role='assistant' AND status='pending'").fetchone()[0]
            unobserved = conn.execute('''SELECT COUNT(*) FROM magi_objective_submissions s
                WHERE status='accepted' AND NOT EXISTS (SELECT 1 FROM firstmate_execution_objectives o
                WHERE o.owner_user_id=s.owner_user_id AND o.objective_id=s.objective_id)''').fetchone()[0]
            lines = ['magistrate_persisted_metrics_available 1',
                     f'magistrate_intake_submitting {submitting}',
                     f'magistrate_accepted_unobserved {unobserved}',
                     f'magistrate_chat_pending {pending_chat}']
            for phase in ('objective.completed', 'objective.failed', 'objective.cancelled'):
                count, elapsed = conn.execute('''SELECT COUNT(*), COALESCE(SUM(CASE WHEN t.occurred_at>a.occurred_at THEN t.occurred_at-a.occurred_at ELSE 0 END),0)
                    FROM firstmate_execution_objectives o
                    JOIN firstmate_execution_events a ON a.owner_user_id=o.owner_user_id AND a.event_id=o.accepted_event_id
                    JOIN firstmate_execution_events t ON t.owner_user_id=o.owner_user_id AND t.event_id=o.terminal_event_id
                    WHERE o.terminal_phase=?''', (phase,)).fetchone()
                label = json.dumps(phase)
                lines.extend([f'magistrate_observed_terminal_count{{phase={label}}} {count}',
                              f'magistrate_observed_execution_seconds_sum{{phase={label}}} {elapsed / 1000:.3f}'])
            return lines
    except Exception:
        return ['magistrate_persisted_metrics_available 0']
