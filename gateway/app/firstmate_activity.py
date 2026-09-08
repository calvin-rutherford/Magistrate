"""Read-only Firstmate structured source adapter.

The adapter consumes only ``fm-fleet-snapshot.sh --json`` and Firstmate's
append-only ``branch-outcomes.jsonl`` / ``fm-captain-event.v1`` contracts. It
never calls Herdr, reads a pane, parses ANSI, or turns terminal prose into a
semantic event.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import stat as stat_module
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote

from app.activity_store import (
    MAX_ACTIVITY_FOCUS_RECORDS,
    MAX_ACTIVITY_SUMMARY_CHARS,
    MAX_SAFE_INTEGER,
    SourceEventConflict,
    SourceUnavailable,
    apply_source_event_batch,
    canonical_json,
    get_source_state,
    initialize_source,
    mark_source_fault,
    payload_sha256,
    reconcile_snapshot,
    safe_pull_request_url,
    source_diagnostics,
)
from app.firstmate_client import FirstmateClient

SOURCE_INSTANCE_ID = 'firstmate:main'
BRANCH_STREAM = 'branch-outcomes'
CAPTAIN_EVENT_STREAM = 'captain-events'
SNAPSHOT_STREAM = 'fleet-snapshot'
MAX_BRANCH_BYTES = 4 * 1024 * 1024
MAX_CAPTAIN_EVENT_ROWS = 10_000
MAX_CAPTAIN_EVENT_RECORD_BYTES = 8 * 1024
MAX_CAPTAIN_EVENT_BYTES = MAX_CAPTAIN_EVENT_ROWS * MAX_CAPTAIN_EVENT_RECORD_BYTES
MAX_CAPTAIN_COMMAND_STDERR_BYTES = 64 * 1024
MAX_CAPTAIN_COMMAND_SECONDS = 30.0
MAX_BRANCH_ROWS = 10_000
MAX_EVENTS_PER_RECONCILE = 500
MAX_SNAPSHOT_TASKS = 1_000
MAX_SNAPSHOT_RECORDS = 2_000
MAX_SNAPSHOT_DECISIONS = 1_000
_ALLOWED_BRANCH_KEYS = {
    'seq', 'epoch', 'task', 'wake', 'verdict', 'summary', 'silent',
    # Optional fields in newer Firstmate's additive contract. They are hashed
    # as provenance but are not exported to clients.
    'statusEndpoint', 'statusIdent',
}
# Runtime state is accepted only when Firstmate labels a semantic source. Pane,
# terminal, Herdr, missing, and future unknown provenance all fail closed.
_SEMANTIC_STATE_PROVENANCE = {'firstmate', 'structured', 'status', 'status-endpoint', 'task', 'journal'}
_CAPTAIN_EVENT_KINDS = {
    'primary.message', 'primary.final', 'worker.message', 'worker.final',
}
_CAPTAIN_EVENT_KEYS = {
    'schema', 'seq', 'event_id', 'published_at_ms', 'occurred_at_ms',
    'source_home', 'source_role', 'task_id', 'incarnation', 'producer',
    'harness_event_id', 'audience', 'kind', 'summary', 'summary_truncated', 'refs',
}
_CAPTAIN_REF_KEYS = {'pr_url', 'report_id', 'report_path', 'branch_outcome_seq'}
_SAFE_SOURCE_SLUG = re.compile(r'^(?!\.)[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
_SAFE_SOURCE_TOKEN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$')
_SAFE_PRODUCER = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')
_SAFE_EVENT_ID = re.compile(r'^sha256:[0-9a-f]{64}$')
_SAFE_REPORT_PATH = re.compile(r'^data/[A-Za-z0-9][A-Za-z0-9._-]{0,127}/report\.md$')
_ENV_ASSIGNMENT = re.compile(
    r'(?i)(?<![A-Za-z0-9_])(?:export\s+)?[A-Za-z_][A-Za-z0-9_]{0,127}\s*(?:\+\s*)?='
)
_SENSITIVE_TEXT = re.compile(
    r'''(?ix)(?:
      (?:proxy[-_ ]?)?authorization\s*["']?\s*[:=]\s*[^\s,;}]+(?:\s+[^\s,;}]+)?
      |["']?[A-Za-z0-9_. -]{0,96}(?:secret|pass(?:word|wd)?|pwd|token|auth|key|credential)
       [A-Za-z0-9_. -]{0,96}["']?\s*[:=]\s*["']?[^\s,;}"']+
      |\b[A-Z][A-Z0-9_]{1,63}\s*=\s*[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@
      |\b[a-z][a-z0-9+.-]{0,31}://[^\s/@]+@[^\s,;]+
      |-----BEGIN [A-Z ]*PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{8,}|\bsk-[A-Za-z0-9]{8,}
      |\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b
    )'''
)
def _contains_sensitive_text(value: str) -> bool:
    decoded = value
    for _ in range(3):
        if _ENV_ASSIGNMENT.search(decoded) or _SENSITIVE_TEXT.search(decoded):
            return True
        expanded = unquote(decoded)
        if expanded == decoded:
            return False
        decoded = expanded
    return bool(_ENV_ASSIGNMENT.search(decoded) or _SENSITIVE_TEXT.search(decoded))


class _CaptainCommandOutputTooLarge(Exception):
    pass


def _reject_nonfinite_json(_: str) -> None:
    raise SourceEventConflict('A Firstmate journal contains a non-finite JSON number.')


async def _read_bounded_stream(stream: asyncio.StreamReader, maximum: int) -> bytes:
    chunks: List[bytes] = []
    size = 0
    while True:
        chunk = await stream.read(min(64 * 1024, maximum + 1 - size))
        if not chunk:
            return b''.join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if size > maximum:
            raise _CaptainCommandOutputTooLarge


class FirstmateActivityAdapter:
    def __init__(
        self,
        firstmate: FirstmateClient,
        *,
        fm_home: Optional[str] = None,
        source_instance_id: str = SOURCE_INSTANCE_ID,
        expected_source_home: str = 'main',
        bootstrap_policy: Optional[str] = None,
        captain_event_path: Optional[str] = None,
        captain_bootstrap_policy: Optional[str] = None,
    ) -> None:
        self.firstmate = firstmate
        self.fm_home = fm_home or firstmate.fm_home
        self.source_instance_id = source_instance_id
        if (
            not isinstance(source_instance_id, str) or not source_instance_id
            or len(source_instance_id) > 128 or _contains_sensitive_text(source_instance_id)
            or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in source_instance_id)
        ):
            raise ValueError('A configured Firstmate source instance must have canonical identity.')
        if expected_source_home != 'main' and not (
            expected_source_home.startswith('secondmate:')
            and _SAFE_SOURCE_SLUG.fullmatch(expected_source_home.split(':', 1)[1])
        ):
            raise ValueError('A configured captain-event source home must have canonical identity.')
        if expected_source_home != 'main' and source_instance_id == SOURCE_INSTANCE_ID:
            raise ValueError('A secondmate captain source requires its own source instance.')
        self.expected_source_home = expected_source_home
        self.branch_path = Path(self.fm_home) / 'state' / 'branch-outcomes.jsonl'
        configured_captain_path = captain_event_path
        self.captain_event_path = Path(configured_captain_path) if configured_captain_path else Path(self.fm_home) / 'state' / 'captain-events' / 'events.jsonl'
        self.captain_event_script = Path(self.fm_home) / 'bin' / 'fm-captain-event.sh'
        self.captain_consumer_id = os.getenv('MAGISTRATE_FIRSTMATE_CAPTAIN_CONSUMER', 'magistrate').strip()
        if not re.fullmatch(r'^(?!\.)[A-Za-z0-9][A-Za-z0-9._-]{0,127}$', self.captain_consumer_id):
            raise ValueError('The Firstmate captain-event consumer id is invalid.')
        self.captain_event_path_explicit = bool(captain_event_path)
        self.bootstrap_policy = (
            bootstrap_policy
            or os.getenv('MAGISTRATE_FIRSTMATE_ACTIVITY_BOOTSTRAP', 'tail').strip()
            or 'tail'
        )
        self.captain_bootstrap_policy = (
            captain_bootstrap_policy
            or os.getenv('MAGISTRATE_FIRSTMATE_CAPTAIN_BOOTSTRAP', 'from-start').strip()
            or 'from-start'
        )
        policy_pattern = r'(?:tail|from-start|after:(?:0|[1-9][0-9]{0,15}))'
        if not re.fullmatch(policy_pattern, self.bootstrap_policy):
            raise ValueError('The Firstmate activity bootstrap policy is invalid.')
        if not re.fullmatch(policy_pattern, self.captain_bootstrap_policy):
            raise ValueError('The Firstmate captain bootstrap policy is invalid.')
        self._locks: Dict[str, asyncio.Lock] = {}

    def _lock(self, user_id: str) -> asyncio.Lock:
        lock = self._locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[user_id] = lock
        return lock

    @staticmethod
    def _unicode_scalar_text(value: Any, *, maximum: int, required: bool = False) -> Optional[str]:
        if not isinstance(value, str):
            if required:
                raise SourceEventConflict('A source text field is missing.')
            return None
        if len(value) > maximum:
            raise SourceEventConflict('A source text field exceeds its contract bound.')
        if any(
            unicodedata.category(character).startswith('C')
            or unicodedata.category(character) in {'Zl', 'Zp'}
            for character in value
        ):
            raise SourceEventConflict('A source text field contains invalid or control Unicode.')
        text = value.strip()
        if required and not text:
            raise SourceEventConflict('A source text field is empty.')
        if len(text) > maximum:
            raise SourceEventConflict('A source text field exceeds its contract bound.')
        return text or None

    @staticmethod
    def _captain_uint(value: Any, *, allow_zero: bool = True) -> int:
        minimum = 0 if allow_zero else 1
        if type(value) is not int or value < minimum or value > 9_007_199_254_740_991:
            raise SourceEventConflict('The captain outbox contains an invalid unsigned integer.')
        return value

    @staticmethod
    def _captain_source_home(value: Any) -> str:
        if value == 'main':
            return value
        if (isinstance(value, str) and value.startswith('secondmate:')
                and _SAFE_SOURCE_SLUG.fullmatch(value.split(':', 1)[1])):
            return value
        raise SourceEventConflict('The captain outbox contains an invalid source home.')

    @staticmethod
    def _captain_token(value: Any, *, field: str) -> str:
        if not isinstance(value, str) or not _SAFE_SOURCE_TOKEN.fullmatch(value):
            raise SourceEventConflict(f'The captain outbox contains an invalid {field}.')
        return value

    def _normalize_captain_event(
        self,
        row: Dict[str, Any],
        titles: Optional[Dict[str, Tuple[str, Optional[str], str, Optional[str]]]] = None,
    ) -> Dict[str, Any]:
        if set(row) != _CAPTAIN_EVENT_KEYS:
            raise SourceEventConflict('The captain outbox event does not have the exact v1 field set.')
        if row['schema'] != 'fm-captain-event.v1':
            raise SourceEventConflict('The captain outbox has an unknown schema version.')
        sequence = self._captain_uint(row['seq'], allow_zero=False)
        if sequence > MAX_CAPTAIN_EVENT_ROWS:
            raise SourceEventConflict('The captain outbox sequence exceeds its hard P0 bound.')
        event_id = row['event_id']
        if not isinstance(event_id, str) or not _SAFE_EVENT_ID.fullmatch(event_id):
            raise SourceEventConflict('The captain outbox has an invalid event id.')
        published_at = self._captain_uint(row['published_at_ms'])
        occurred_at = row['occurred_at_ms']
        if occurred_at is not None:
            occurred_at = self._captain_uint(occurred_at)
        source_home = self._captain_source_home(row['source_home'])
        if source_home != self.expected_source_home:
            raise SourceEventConflict('The captain event does not belong to the configured source home.')
        source_role = row['source_role']
        task_id = row['task_id']
        kind = row['kind']
        if source_role == 'primary':
            if task_id is not None or kind not in {'primary.message', 'primary.final'}:
                raise SourceEventConflict('A primary captain event has invalid task or kind semantics.')
        elif source_role == 'worker':
            if (not isinstance(task_id, str) or not _SAFE_SOURCE_SLUG.fullmatch(task_id)
                    or kind not in {'worker.message', 'worker.final'}):
                raise SourceEventConflict('A worker captain event has invalid task or kind semantics.')
        else:
            raise SourceEventConflict('The captain outbox has an unknown source role.')
        incarnation = self._captain_token(row['incarnation'], field='incarnation')
        producer = row['producer']
        if not isinstance(producer, str) or not _SAFE_PRODUCER.fullmatch(producer):
            raise SourceEventConflict('The captain outbox has an invalid producer.')
        harness_event_id = self._captain_token(row['harness_event_id'], field='harness event id')
        if row['audience'] != 'captain' or kind not in _CAPTAIN_EVENT_KINDS:
            raise SourceEventConflict('The captain outbox lacks an explicit known captain audience/kind.')
        summary = row['summary']
        if (not isinstance(summary, str) or not summary or len(summary) > 600
                or unicodedata.normalize('NFC', summary) != summary
                or ' '.join(summary.split()) != summary
                or any(unicodedata.category(character).startswith('C')
                       or unicodedata.category(character) in {'Zl', 'Zp'} for character in summary)):
            raise SourceEventConflict('The captain outbox summary is not canonical inert text.')
        if _contains_sensitive_text(summary):
            raise SourceEventConflict('The captain outbox summary resembles credential material.')
        if type(row['summary_truncated']) is not bool:
            raise SourceEventConflict('The captain outbox summary truncation flag is invalid.')

        raw_refs = row['refs']
        if not isinstance(raw_refs, dict) or not set(raw_refs).issubset(_CAPTAIN_REF_KEYS):
            raise SourceEventConflict('The captain outbox contains a non-allowlisted reference.')
        refs: List[Dict[str, str]] = []
        if 'pr_url' in raw_refs:
            pr_url = self._safe_https_url(raw_refs['pr_url'])
            if not pr_url:
                raise SourceEventConflict('The captain outbox PR reference is unsafe.')
            refs.append({'kind': 'pull-request', 'url': pr_url})
        if 'report_id' in raw_refs:
            report_id = raw_refs['report_id']
            if not isinstance(report_id, str) or not _SAFE_SOURCE_SLUG.fullmatch(report_id):
                raise SourceEventConflict('The captain outbox report id is invalid.')
            refs.append({'kind': 'report', 'id': report_id})
        if 'report_path' in raw_refs and (
            not isinstance(raw_refs['report_path'], str)
            or not _SAFE_REPORT_PATH.fullmatch(raw_refs['report_path'])
            or task_id is None
            or raw_refs['report_path'] != f'data/{task_id}/report.md'
        ):
            raise SourceEventConflict('The captain outbox report path is invalid.')
        if 'branch_outcome_seq' in raw_refs:
            self._captain_uint(raw_refs['branch_outcome_seq'], allow_zero=False)

        identity = {
            'schema': 'fm-captain-event.v1',
            'source_home': source_home,
            'source_role': source_role,
            'task_id': task_id,
            'incarnation': incarnation,
            'producer': producer,
            'harness_event_id': harness_event_id,
        }
        expected_event_id = 'sha256:' + hashlib.sha256(
            canonical_json(identity).encode('utf-8')
        ).hexdigest()
        if event_id != expected_event_id:
            raise SourceEventConflict('The captain outbox event id does not match its identity tuple.')

        source_instance_id = self.source_instance_id
        causal_key = task_id or 'primary'
        objective_id = self._causal_id('obj', causal_key)
        run_id = self._causal_id('run', causal_key, incarnation)
        # `final` is Pi's stopReason for this persisted assistant turn, not
        # evidence that the surrounding Firstmate task/objective completed.
        # Every outbox row itself is finalised at turn_end; task lifecycle stays
        # owned by fleet snapshot / branch-outcome semantics.
        state = 'completed'
        activity_kind = kind
        title_row = (titles or {}).get(task_id or '')
        title = title_row[0] if title_row else (task_id or 'Firstmate')
        project = title_row[1] if title_row else None
        source_hash = payload_sha256(row)
        return {
            'source_instance_id': source_instance_id,
            'source_cursor': sequence,
            'source_event_id': event_id,
            'payload': row,
            'payload_sha256': source_hash,
            'audience': 'captain',
            'event_kind': kind,
            'occurred_at': occurred_at,
            'activity': {
                'record_key': f'captain-event:{event_id}',
                'kind': activity_kind,
                'state': state,
                'importance': 'routine',
                'title': title,
                'summary': summary,
                'summary_truncated': row['summary_truncated'],
                'task_id': task_id,
                'decision_key': None,
                'objective_id': objective_id,
                'run_id': run_id,
                'project': project,
                'occurred_at': occurred_at,
                'observed_at': published_at,
                'refs': refs,
            },
        }

    def _read_captain_event_rows(
        self,
        titles: Optional[Dict[str, Tuple[str, Optional[str], str, Optional[str]]]] = None,
    ) -> List[Dict[str, Any]]:
        descriptor: Optional[int] = None
        try:
            parent_stat = os.lstat(self.captain_event_path.parent)
            if (
                not stat_module.S_ISDIR(parent_stat.st_mode)
                or stat_module.S_ISLNK(parent_stat.st_mode)
                or stat_module.S_IMODE(parent_stat.st_mode) != 0o700
                or parent_stat.st_uid != os.geteuid()
            ):
                raise SourceEventConflict('The Firstmate captain outbox directory must be private and owned.')
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
            descriptor = os.open(self.captain_event_path, flags)
            stat = os.fstat(descriptor)
            if (not stat_module.S_ISREG(stat.st_mode)
                    or stat_module.S_IMODE(stat.st_mode) != 0o600
                    or stat.st_nlink != 1 or stat.st_uid != os.geteuid()):
                raise SourceEventConflict('The Firstmate captain outbox must be an owned single-link mode-0600 regular file.')
            if stat.st_size > MAX_CAPTAIN_EVENT_BYTES:
                raise SourceEventConflict('The Firstmate captain outbox exceeds the bounded read size.')
            with os.fdopen(descriptor, 'rb') as handle:
                descriptor = None
                raw = handle.read(MAX_CAPTAIN_EVENT_BYTES + 1)
        except FileNotFoundError:
            if self.captain_event_path_explicit:
                raise SourceUnavailable('The configured Firstmate captain outbox is unavailable.')
            return []
        except SourceEventConflict:
            raise
        except OSError as exc:
            raise SourceUnavailable('The Firstmate captain outbox is unavailable.') from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if len(raw) > MAX_CAPTAIN_EVENT_BYTES:
            raise SourceEventConflict('The Firstmate captain outbox exceeds the bounded read size.')
        if not raw:
            return []
        if not raw.endswith(b'\n'):
            raise SourceEventConflict('The Firstmate captain outbox has a torn final record.')
        # The producer's canonical byte contract uses LF only. ``splitlines``
        # would silently normalize CRLF and bare-CR rewrites before validation.
        lines = raw[:-1].split(b'\n')
        if len(lines) > MAX_CAPTAIN_EVENT_ROWS:
            raise SourceEventConflict('The Firstmate captain outbox has too many records.')

        def strict_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
            result: Dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise SourceEventConflict('The Firstmate captain outbox has duplicate JSON keys.')
                result[key] = value
            return result

        rows: List[Dict[str, Any]] = []
        seen_event_ids: set[str] = set()
        for expected, line in enumerate(lines, start=1):
            if not line or len(line) + 1 > MAX_CAPTAIN_EVENT_RECORD_BYTES:
                raise SourceEventConflict('A Firstmate captain event exceeds its record bound.')
            try:
                decoded = line.decode('utf-8', errors='strict')
                raw_row = json.loads(
                    decoded, object_pairs_hook=strict_object,
                    parse_constant=_reject_nonfinite_json,
                )
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
                raise SourceEventConflict('The Firstmate captain outbox contains malformed JSON.') from exc
            if not isinstance(raw_row, dict):
                raise SourceEventConflict('The Firstmate captain outbox contains a non-object event.')
            if decoded != canonical_json(raw_row):
                raise SourceEventConflict('The Firstmate captain outbox event is not canonical JSON.')
            normalized = self._normalize_captain_event(raw_row, titles)
            if normalized['source_cursor'] != expected:
                raise SourceEventConflict('The Firstmate captain outbox is gapped, duplicated, or reordered.')
            if normalized['source_event_id'] in seen_event_ids:
                raise SourceEventConflict('The Firstmate captain outbox repeats an event identity.')
            seen_event_ids.add(normalized['source_event_id'])
            normalized['_source_record_sha256'] = payload_sha256(raw_row)
            normalized['_source_line'] = line + b'\n'
            rows.append(normalized)
        return rows

    async def _run_captain_command(
        self, *arguments: str, maximum_stdout: int,
    ) -> Tuple[int, bytes, bytes]:
        try:
            script_stat = self.captain_event_script.lstat()
        except FileNotFoundError as exc:
            raise SourceUnavailable('The Firstmate captain-event reader is unavailable.') from exc
        if (not stat_module.S_ISREG(script_stat.st_mode)
                or stat_module.S_ISLNK(script_stat.st_mode)
                or script_stat.st_nlink != 1 or script_stat.st_mode & 0o002
                or script_stat.st_uid != os.geteuid()
                or not os.access(self.captain_event_script, os.X_OK)):
            raise SourceUnavailable('The Firstmate captain-event reader is not a trusted executable.')
        # Reader tooling needs no Gateway credentials. Pass only process basics
        # plus the explicitly bound home instead of inheriting secret-bearing
        # provider/session/database environment variables.
        environment = {
            'FM_HOME': self.fm_home,
            'PATH': '/usr/local/bin:/usr/bin:/bin',
            'HOME': '/nonexistent',
            'LANG': 'C.UTF-8',
            'LC_ALL': 'C.UTF-8',
        }
        try:
            process = await asyncio.create_subprocess_exec(
                str(self.captain_event_script), *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.fm_home,
                env=environment,
                start_new_session=True,
            )
            async def collect() -> Tuple[bytes, bytes, int]:
                stdout, stderr, returncode = await asyncio.gather(
                    _read_bounded_stream(process.stdout, maximum_stdout),
                    _read_bounded_stream(process.stderr, MAX_CAPTAIN_COMMAND_STDERR_BYTES),
                    process.wait(),
                )
                return stdout, stderr, returncode

            try:
                stdout, stderr, returncode = await asyncio.wait_for(
                    collect(), timeout=MAX_CAPTAIN_COMMAND_SECONDS,
                )
            except asyncio.CancelledError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
                raise
            except (asyncio.TimeoutError, _CaptainCommandOutputTooLarge) as exc:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
                raise SourceUnavailable('The Firstmate captain-event reader exceeded a runtime bound.') from exc
            return returncode, stdout, stderr
        except SourceUnavailable:
            raise
        except (OSError, ValueError) as exc:
            raise SourceUnavailable('The Firstmate captain-event reader could not be executed.') from exc

    async def _read_validated_captain_events(
        self,
        titles: Dict[str, Tuple[str, Optional[str], str, Optional[str]]],
        *,
        after: Optional[int],
        expected_prefix_sha256: Optional[str] = None,
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        if self.captain_event_path_explicit:
            return True, await asyncio.to_thread(self._read_captain_event_rows, titles)
        if not self.captain_event_script.exists():
            if self.captain_event_path.exists():
                raise SourceUnavailable('Captain-event state exists without its versioned Firstmate reader.')
            return False, []
        returncode, stdout, stderr = await self._run_captain_command(
            'enabled', maximum_stdout=128,
        )
        if returncode == 1 and not stdout and not stderr:
            return False, []
        if returncode != 0 or stdout or stderr:
            raise SourceUnavailable('Firstmate captain-event activation could not be validated.')

        rows = await asyncio.to_thread(self._read_captain_event_rows, titles)
        reader_after = self._captain_bootstrap_cursor(len(rows))[0] if after is None else after
        if reader_after < 0 or reader_after > len(rows):
            raise SourceEventConflict('The Magistrate captain-event cursor is beyond the source tail.')
        if after is not None and after > 0:
            if expected_prefix_sha256 != self._captain_prefix_hash(rows, after):
                raise SourceEventConflict('The validated Firstmate captain-event prefix changed or was truncated.')
            # Reassert only the cursor already committed in Magistrate. This is
            # the producer-owned recovery path for an acknowledgement temp file
            # left by a crash; it never acknowledges a merely parsed batch.
            await self._acknowledge_captain_events(after, rows[after - 1]['source_event_id'])
        returncode, reader_rows, reader_stderr = await self._run_captain_command(
            'read', '--after', str(reader_after), '--limit', str(MAX_EVENTS_PER_RECONCILE),
            maximum_stdout=MAX_EVENTS_PER_RECONCILE * MAX_CAPTAIN_EVENT_RECORD_BYTES,
        )
        expected = b''.join(
            row['_source_line']
            for row in rows[reader_after:reader_after + MAX_EVENTS_PER_RECONCILE]
        )
        if returncode != 0 or reader_stderr:
            raise SourceUnavailable('The Firstmate captain-event reader refused the source state.')
        if reader_rows != expected:
            raise SourceEventConflict('The Firstmate captain-event journal changed during validation.')
        return True, rows

    async def _acknowledge_captain_events(self, through: int, event_id: str) -> None:
        if through <= 0 or self.captain_event_path_explicit:
            return
        returncode, stdout, stderr = await self._run_captain_command(
            'ack', '--consumer', self.captain_consumer_id,
            '--through', str(through), '--event-id', event_id,
            maximum_stdout=128,
        )
        if returncode != 0 or stderr or stdout.strip() != str(through).encode('ascii'):
            raise SourceUnavailable('Firstmate refused the durable captain-event ingestion acknowledgement.')

    @staticmethod
    def _captain_prefix_hash(rows: List[Dict[str, Any]], through: int) -> str:
        if through <= 0:
            return ''
        digest = hashlib.sha256()
        for row in rows[:through]:
            digest.update(row['_source_record_sha256'].encode('ascii'))
            digest.update(b'\n')
        return digest.hexdigest()

    def _captain_bootstrap_cursor(self, tail: int) -> Tuple[int, str]:
        policy = self.captain_bootstrap_policy
        if policy == 'from-start':
            return 0, policy
        if policy == 'tail':
            return tail, policy
        if policy.startswith('after:') and policy[6:].isdigit():
            cursor = int(policy[6:])
            if cursor <= tail:
                return cursor, policy
        raise SourceEventConflict('The configured Firstmate captain bootstrap cursor is invalid.')

    async def _reconcile_captain_events(
        self,
        user_id: str,
        titles: Dict[str, Tuple[str, Optional[str], str, Optional[str]]],
    ) -> List[Dict[str, Any]]:
        state = get_source_state(
            user_id, self.source_instance_id, CAPTAIN_EVENT_STREAM,
        )
        uninitialized_fault = bool(
            state is not None and state['state'] == 'fault' and state['cursor'] == 0
            and not state['prefix_sha256'] and state['source_tail'] == 0
            and state['accepted_count'] == 0
        )
        reader_after = None if state is None or uninitialized_fault else int(state['cursor'])
        enabled, rows = await self._read_validated_captain_events(
            titles,
            after=reader_after,
            expected_prefix_sha256=(state['prefix_sha256'] if state is not None else None),
        )
        if not enabled:
            if state is not None:
                raise SourceUnavailable('A previously bound Firstmate captain outbox is disabled or unavailable.')
            return []
        tail = len(rows)
        if state is None or uninitialized_fault:
            cursor, policy = self._captain_bootstrap_cursor(tail)
            state = initialize_source(
                user_id,
                self.source_instance_id,
                CAPTAIN_EVENT_STREAM,
                bootstrap_policy=policy,
                cursor=cursor,
                prefix_sha256=self._captain_prefix_hash(rows, cursor),
                source_tail=tail,
            )
        cursor = int(state['cursor'])
        if cursor > tail:
            raise SourceEventConflict('The Magistrate captain-event cursor is beyond the source tail.')
        if cursor and state['prefix_sha256'] != self._captain_prefix_hash(rows, cursor):
            raise SourceEventConflict('The validated Firstmate captain-event prefix changed or was truncated.')
        selected = rows[cursor:cursor + MAX_EVENTS_PER_RECONCILE]
        new_cursor = cursor + len(selected)
        changed = apply_source_event_batch(
            user_id,
            self.source_instance_id,
            CAPTAIN_EVENT_STREAM,
            expected_cursor=cursor,
            events=selected,
            new_cursor=new_cursor,
            new_prefix_sha256=self._captain_prefix_hash(rows, new_cursor),
            source_tail=tail,
        )
        if new_cursor:
            await self._acknowledge_captain_events(
                new_cursor, rows[new_cursor - 1]['source_event_id'],
            )
        return changed

    def _read_branch_rows(self) -> List[Dict[str, Any]]:
        descriptor: Optional[int] = None
        try:
            descriptor = os.open(self.branch_path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            stat = os.fstat(descriptor)
            if (
                not stat_module.S_ISREG(stat.st_mode) or stat.st_nlink != 1
                or stat.st_uid != os.geteuid() or stat.st_mode & 0o002
            ):
                raise SourceEventConflict('The Firstmate outcome journal must be an owned single-link regular file.')
            if stat.st_size > MAX_BRANCH_BYTES:
                raise SourceEventConflict('The Firstmate outcome journal exceeds the bounded read size.')
            with os.fdopen(descriptor, 'rb') as handle:
                descriptor = None
                raw = handle.read(MAX_BRANCH_BYTES + 1)
        except FileNotFoundError:
            return []
        except SourceEventConflict:
            raise
        except OSError as exc:
            raise SourceUnavailable('The Firstmate outcome journal is unavailable.') from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if len(raw) > MAX_BRANCH_BYTES:
            raise SourceEventConflict('The Firstmate outcome journal exceeds the bounded read size.')
        if raw and not raw.endswith(b'\n'):
            raise SourceEventConflict('The Firstmate outcome journal has a torn final record.')
        if b'\r' in raw:
            raise SourceEventConflict('The Firstmate outcome journal has non-canonical separators.')
        line_bytes = raw[:-1].split(b'\n') if raw else []
        if len(line_bytes) > MAX_BRANCH_ROWS:
            raise SourceEventConflict('The Firstmate outcome journal has too many records.')
        def strict_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
            result: Dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise SourceEventConflict('The Firstmate outcome journal has duplicate JSON keys.')
                result[key] = value
            return result

        rows: List[Dict[str, Any]] = []
        for expected, raw_line in enumerate(line_bytes, start=1):
            try:
                line = raw_line.decode('utf-8', errors='strict')
                row = json.loads(
                    line, object_pairs_hook=strict_object,
                    parse_constant=_reject_nonfinite_json,
                )
            except UnicodeDecodeError as exc:
                raise SourceEventConflict('The Firstmate outcome journal is not valid UTF-8.') from exc
            except (json.JSONDecodeError, RecursionError) as exc:
                raise SourceEventConflict('The Firstmate outcome journal contains malformed JSON.') from exc
            if not isinstance(row, dict) or not set(row).issubset(_ALLOWED_BRANCH_KEYS):
                raise SourceEventConflict('The Firstmate outcome journal contains an unknown record shape.')
            if set(row) - {'silent', 'statusEndpoint', 'statusIdent'} != {
                'seq', 'epoch', 'task', 'wake', 'verdict', 'summary'
            }:
                raise SourceEventConflict('The Firstmate outcome journal is missing required fields.')
            if type(row.get('seq')) is not int or row['seq'] != expected:
                raise SourceEventConflict('The Firstmate outcome journal is gapped, duplicated, or reordered.')
            if (
                type(row.get('epoch')) is not int or row['epoch'] < 0
                or row['epoch'] > MAX_SAFE_INTEGER // 1000
            ):
                raise SourceEventConflict('The Firstmate outcome journal has an invalid timestamp.')
            task_id = self._unicode_scalar_text(row.get('task'), maximum=200, required=True)
            if task_id != row.get('task'):
                raise SourceEventConflict('The Firstmate outcome journal has a non-canonical task identity.')
            if not isinstance(row.get('wake'), str):
                raise SourceEventConflict('The Firstmate outcome journal has an invalid wake field.')
            self._unicode_scalar_text(row.get('wake'), maximum=16_384)
            self._unicode_scalar_text(row.get('summary'), maximum=16_384, required=True)
            if row.get('verdict') not in {'routine', 'captain'}:
                raise SourceEventConflict('The Firstmate outcome journal has an unknown audience verdict.')
            if 'silent' in row and type(row['silent']) is not bool:
                raise SourceEventConflict('The Firstmate outcome journal has an invalid silent flag.')
            for optional in ('statusEndpoint', 'statusIdent'):
                if optional in row:
                    self._unicode_scalar_text(row[optional], maximum=512, required=True)
            row['_source_line_sha256'] = hashlib.sha256(raw_line + b'\n').hexdigest()
            rows.append(row)
        return rows

    @staticmethod
    def _prefix_hash(rows: List[Dict[str, Any]], through: int) -> str:
        if through <= 0:
            return ''
        digest = hashlib.sha256()
        for row in rows[:through]:
            digest.update(row['_source_line_sha256'].encode('ascii'))
            digest.update(b'\n')
        return digest.hexdigest()

    def _bootstrap_cursor(self, tail: int) -> Tuple[int, str]:
        policy = self.bootstrap_policy
        if policy == 'tail':
            return tail, policy
        if policy == 'from-start':
            return 0, policy
        if policy.startswith('after:') and policy[6:].isdigit():
            cursor = int(policy[6:])
            if cursor <= tail:
                return cursor, policy
        raise SourceEventConflict('The configured Firstmate activity bootstrap cursor is invalid.')

    def _causal_id(self, prefix: str, *parts: Optional[str]) -> str:
        material = '\0'.join([self.source_instance_id, *(part or '' for part in parts)])
        return f'{prefix}_' + hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]

    @staticmethod
    def _safe_summary(value: str) -> str:
        if _contains_sensitive_text(value):
            raise SourceEventConflict('A Firstmate supervision summary resembles credential material.')
        if any(
            unicodedata.category(character).startswith('C')
            or unicodedata.category(character) in {'Zl', 'Zp'}
            for character in value
        ):
            raise SourceEventConflict('A Firstmate supervision summary contains control Unicode.')
        cleaned = value.strip()
        bounded = ''.join(list(cleaned)[:MAX_ACTIVITY_SUMMARY_CHARS])
        return bounded or 'Firstmate recorded a supervision outcome.'

    async def _reconcile_branch(
        self,
        user_id: str,
        titles: Dict[str, Tuple[str, Optional[str], str, Optional[str]]],
    ) -> List[Dict[str, Any]]:
        rows = await asyncio.to_thread(self._read_branch_rows)
        tail = len(rows)
        state = get_source_state(user_id, self.source_instance_id, BRANCH_STREAM)
        uninitialized_fault = bool(
            state is not None and state['state'] == 'fault' and state['cursor'] == 0
            and not state['prefix_sha256'] and state['source_tail'] == 0
            and state['accepted_count'] == 0
        )
        if state is None or uninitialized_fault:
            cursor, policy = self._bootstrap_cursor(tail)
            state = initialize_source(
                user_id,
                self.source_instance_id,
                BRANCH_STREAM,
                bootstrap_policy=policy,
                cursor=cursor,
                prefix_sha256=self._prefix_hash(rows, cursor),
                source_tail=tail,
            )
        cursor = int(state['cursor'])
        if cursor > tail:
            raise SourceEventConflict('The Magistrate activity cursor is beyond the Firstmate journal tail.')
        if cursor and state['prefix_sha256'] != self._prefix_hash(rows, cursor):
            raise SourceEventConflict('The validated Firstmate journal prefix changed or was truncated.')
        selected = rows[cursor:cursor + MAX_EVENTS_PER_RECONCILE]
        if not selected:
            # Refresh tail/availability without moving a cursor. This also makes
            # a missing journal an explicit, healthy empty source.
            apply_source_event_batch(
                user_id,
                self.source_instance_id,
                BRANCH_STREAM,
                expected_cursor=cursor,
                events=[],
                new_cursor=cursor,
                new_prefix_sha256=self._prefix_hash(rows, cursor),
                source_tail=tail,
            )
            return []
        normalized: List[Dict[str, Any]] = []
        for row in selected:
            seq = row['seq']
            task_id = row['task']
            if _contains_sensitive_text(task_id):
                raise SourceEventConflict('A Firstmate task identity resembles credential material.')
            summary = self._safe_summary(row['summary'])
            summary_truncated = len(row['summary'].strip()) > MAX_ACTIVITY_SUMMARY_CHARS
            source_row = {key: value for key, value in row.items() if not key.startswith('_source_')}
            source_hash = payload_sha256(source_row)
            source_event_id = f'branch-outcome:{seq}'
            occurred_at = row['epoch'] * 1000
            public_payload = {
                'schema_version': 'magistrate.firstmate-event.v1',
                'source_instance_id': self.source_instance_id,
                'source_event_id': source_event_id,
                'cursor': seq,
                'task_id': task_id,
                'occurred_at': occurred_at,
                'audience': 'captain',
                'surface': 'activity',
                'kind': 'supervision.outcome',
                'importance': 'attention' if row['verdict'] == 'captain' else 'routine',
                'summary': summary,
                'summary_truncated': summary_truncated,
            }
            title, project, objective_id, run_id = titles.get(
                task_id,
                (task_id, None, self._causal_id('obj', task_id), None),
            )
            if _contains_sensitive_text(title):
                raise SourceEventConflict('A Firstmate task identity resembles credential material.')
            public_payload['objective_id'] = objective_id
            public_payload['run_id'] = run_id
            activity = None if row.get('silent') is True else {
                'record_key': f'branch:{seq}',
                'kind': 'supervision.outcome',
                'state': 'completed',
                'importance': public_payload['importance'],
                'title': title,
                'summary': summary,
                'summary_truncated': summary_truncated,
                'task_id': task_id,
                'decision_key': None,
                'objective_id': objective_id,
                'run_id': run_id,
                'project': project,
                'occurred_at': occurred_at,
                'observed_at': occurred_at,
                'refs': [],
            }
            normalized.append({
                'source_event_id': source_event_id,
                'source_cursor': seq,
                'payload_sha256': source_hash,
                'audience': 'captain',
                'event_kind': 'supervision.outcome',
                'occurred_at': occurred_at,
                'payload': public_payload,
                'activity': activity,
            })
        new_cursor = selected[-1]['seq']
        return apply_source_event_batch(
            user_id,
            self.source_instance_id,
            BRANCH_STREAM,
            expected_cursor=cursor,
            events=normalized,
            new_cursor=new_cursor,
            new_prefix_sha256=self._prefix_hash(rows, new_cursor),
            source_tail=tail,
        )

    @staticmethod
    def _timestamp(value: Any) -> Optional[int]:
        if type(value) is int and 0 <= value <= 253_402_300_799_999:
            # Firstmate journal epochs are seconds; larger values are accepted
            # only when they are already bounded Unix milliseconds.
            return value * 1000 if value < 10_000_000_000 else value
        if (
            not isinstance(value, str) or not value or value != value.strip()
            or any(
                unicodedata.category(character).startswith('C')
                or unicodedata.category(character) in {'Zl', 'Zp'}
                for character in value
            )
        ):
            return None
        text = value
        if len(text) == 10:
            text += 'T00:00:00Z'
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            timestamp = int(parsed.astimezone(timezone.utc).timestamp() * 1000)
        except (ValueError, OverflowError, OSError):
            return None
        return timestamp if 0 <= timestamp <= 253_402_300_799_999 else None

    @staticmethod
    def _safe_https_url(value: Any) -> Optional[str]:
        return safe_pull_request_url(value)

    @staticmethod
    def _project_name(value: Any) -> Optional[str]:
        if not isinstance(value, str) or not value.strip():
            return None
        if len(value) > 1024:
            raise SourceEventConflict('A Firstmate project label exceeds its contract bound.')
        if _contains_sensitive_text(value):
            raise SourceEventConflict('A Firstmate project label resembles credential material.')
        if any(
            unicodedata.category(character).startswith('C')
            or unicodedata.category(character) in {'Zl', 'Zp'}
            for character in value
        ):
            raise SourceEventConflict('A Firstmate project label contains control characters.')
        name = value.strip().rstrip('/').rsplit('/', 1)[-1]
        if len(name) > 160:
            raise SourceEventConflict('A Firstmate project label exceeds its contract bound.')
        if _contains_sensitive_text(name):
            raise SourceEventConflict('A Firstmate project label resembles credential material.')
        return name or None

    @staticmethod
    def _lifecycle_state(task: Optional[Dict[str, Any]], record: Optional[Dict[str, Any]], has_decision: bool) -> Optional[str]:
        if has_decision:
            return 'awaiting-user'
        record_state = record.get('state') if record else None
        if record_state == 'done':
            return 'completed'
        current = task.get('current_state') if task and isinstance(task.get('current_state'), dict) else {}
        provenance = str(current.get('source') or '').strip().lower()
        current_state = str(current.get('state') or '').strip().lower()
        if provenance in _SEMANTIC_STATE_PROVENANCE:
            if current_state in {'working', 'running', 'busy', 'idle', 'queued', 'pending', 'blocked'}:
                return 'active'
            if current_state in {'done', 'complete', 'completed'}:
                return 'completed'
            if current_state in {'failed', 'error'}:
                return 'failed'
            if current_state in {'cancelled', 'canceled'}:
                return 'cancelled'
        if record_state in {'queued', 'in_flight'}:
            return 'active'
        return None

    def _normalize_snapshot(
        self, snapshot: Dict[str, Any],
    ) -> Tuple[
        int, str, List[Dict[str, Any]], List[str],
        Dict[str, Tuple[str, Optional[str], str, Optional[str]]],
    ]:
        if not isinstance(snapshot, dict) or snapshot.get('schema') != 'fm-fleet-snapshot.v1':
            raise SourceUnavailable('Firstmate returned an unknown fleet snapshot schema.')
        if snapshot.get('error'):
            raise SourceUnavailable('Firstmate reported that the fleet snapshot is unavailable.')
        snapshot_home = snapshot.get('fm_home')
        if snapshot_home is not None and (
            not isinstance(snapshot_home, str)
            or os.path.realpath(snapshot_home) != os.path.realpath(self.fm_home)
        ):
            raise SourceEventConflict('The fleet snapshot belongs to another Firstmate home.')
        tasks = snapshot.get('tasks')
        backlog = snapshot.get('backlog')
        records = backlog.get('records') if isinstance(backlog, dict) else None
        if not isinstance(tasks, list) or not isinstance(records, list):
            raise SourceUnavailable('Firstmate returned an incomplete fleet snapshot.')
        if len(tasks) > MAX_SNAPSHOT_TASKS or len(records) > MAX_SNAPSHOT_RECORDS:
            raise SourceUnavailable('Firstmate returned an oversized fleet snapshot.')
        landed_container = snapshot.get('secondmate_landed')
        landed = landed_container.get('records', []) if isinstance(landed_container, dict) else []
        if not isinstance(landed, list) or len(landed) > MAX_SNAPSHOT_RECORDS:
            raise SourceUnavailable('Firstmate returned an invalid landed-work projection.')

        generated_at = self._timestamp(snapshot.get('generated'))
        if generated_at is None:
            raise SourceUnavailable('Firstmate returned no valid snapshot observation time.')
        tasks_by_id: Dict[str, Dict[str, Any]] = {}
        for raw in tasks:
            if not isinstance(raw, dict):
                raise SourceUnavailable('Firstmate returned an invalid task projection.')
            task_id = self._unicode_scalar_text(raw.get('id'), maximum=200, required=True)
            if task_id != raw.get('id'):
                raise SourceEventConflict('Firstmate returned a non-canonical task identity.')
            if task_id in tasks_by_id:
                raise SourceEventConflict('Firstmate returned a duplicate task identity.')
            tasks_by_id[task_id] = raw
        records_by_id: Dict[str, Dict[str, Any]] = {}
        for collection in (records, landed):
            collection_ids: set[str] = set()
            for raw in collection:
                if not isinstance(raw, dict):
                    raise SourceUnavailable('Firstmate returned an invalid backlog projection.')
                if raw.get('structured') is False:
                    continue
                record_id = self._unicode_scalar_text(raw.get('id'), maximum=200, required=True)
                if record_id != raw.get('id'):
                    raise SourceEventConflict('Firstmate returned a non-canonical backlog identity.')
                if record_id in collection_ids:
                    raise SourceEventConflict('Firstmate returned a duplicate backlog identity.')
                collection_ids.add(record_id)
                if record_id not in records_by_id:
                    records_by_id[record_id] = raw

        candidates: List[Dict[str, Any]] = []
        open_decisions: List[str] = []
        titles: Dict[str, Tuple[str, Optional[str], str, Optional[str]]] = {}
        decision_count = 0
        all_ids = list(dict.fromkeys([*records_by_id, *tasks_by_id]))
        for task_id in all_ids:
            task = tasks_by_id.get(task_id)
            record = records_by_id.get(task_id)
            raw_title = record.get('title') if record else None
            title = self._unicode_scalar_text(raw_title, maximum=240) or task_id
            if _contains_sensitive_text(title):
                raise SourceEventConflict('A Firstmate task title resembles credential material.')
            project = self._project_name(
                (record.get('repo') if record else None) or (task.get('project') if task else None)
            )
            objective_id = self._causal_id('obj', task_id)
            raw_spawn_gen = task.get('spawn_gen') if task else None
            spawn_gen = self._unicode_scalar_text(raw_spawn_gen, maximum=128)
            if spawn_gen is not None and spawn_gen != raw_spawn_gen:
                raise SourceEventConflict('Firstmate returned a non-canonical run identity.')
            run_id = self._causal_id('run', task_id, spawn_gen) if spawn_gen else None
            titles[task_id] = (title, project, objective_id, run_id)
            if task and 'hints' in task and not isinstance(task.get('hints'), dict):
                raise SourceUnavailable('Firstmate returned an invalid keyed-decision projection.')
            hints = task.get('hints') if task else {}
            decision_rows = hints.get('open_decisions', [])
            if not isinstance(decision_rows, list) or len(decision_rows) > 100:
                raise SourceUnavailable('Firstmate returned an invalid keyed-decision projection.')
            decision_count += len(decision_rows)
            if decision_count > MAX_SNAPSHOT_DECISIONS:
                raise SourceUnavailable('Firstmate returned an oversized keyed-decision projection.')
            valid_decisions: List[Tuple[str, str]] = []
            seen_decision_keys: set[str] = set()
            for decision in decision_rows:
                if (
                    not isinstance(decision, dict)
                    or set(decision) != {'verb', 'key', 'summary'}
                    or decision.get('verb') not in {'needs-decision', 'blocked'}
                ):
                    # An omitted/unknown row cannot safely close a previously
                    # open keyed decision, so reject the complete snapshot.
                    raise SourceEventConflict('Firstmate returned unknown keyed-decision semantics.')
                key = self._unicode_scalar_text(
                    decision.get('key'), maximum=128, required=True,
                )
                if key != decision.get('key'):
                    raise SourceEventConflict('Firstmate returned a non-canonical decision identity.')
                summary = self._unicode_scalar_text(
                    decision.get('summary'), maximum=600, required=True,
                )
                if key in seen_decision_keys:
                    raise SourceEventConflict('Firstmate returned a duplicate open decision key.')
                seen_decision_keys.add(key)
                if _contains_sensitive_text(summary):
                    raise SourceEventConflict('A Firstmate decision summary resembles credential material.')
                valid_decisions.append((key, summary))
                open_decisions.append(f'{task_id}\0{key}')
                decision_payload = {
                    'kind': 'decision.requested', 'state': 'awaiting-user',
                    'task_id': task_id, 'decision_key': key, 'summary': summary,
                    'objective_id': objective_id, 'run_id': run_id,
                }
                decision_record_key = 'decision:' + hashlib.sha256(
                    f'{task_id}\0{key}'.encode('utf-8')
                ).hexdigest()[:32]
                candidates.append({
                    'record_key': decision_record_key,
                    'kind': 'decision.requested',
                    'state': 'awaiting-user',
                    'importance': 'attention',
                    'title': title,
                    'summary': summary,
                    'source_payload_sha256': payload_sha256(decision_payload),
                    'source_event_id': None,
                    'task_id': task_id,
                    'decision_key': key,
                    'objective_id': objective_id,
                    'run_id': run_id,
                    'project': project,
                    'occurred_at': None,
                    'refs': [],
                })
            state = self._lifecycle_state(task, record, bool(valid_decisions))
            if state is None:
                continue
            kind = {
                'active': 'objective.started',
                'awaiting-user': 'objective.progress',
                'completed': 'objective.completed',
                'failed': 'objective.failed',
                'cancelled': 'objective.cancelled',
            }[state]
            if record and record.get('completion') not in (None, '') and not isinstance(record.get('completion'), dict):
                raise SourceEventConflict('Firstmate returned an invalid completion projection.')
            completion = record.get('completion') if record and isinstance(record.get('completion'), dict) else {}
            if completion and (
                set(completion) != {'verb', 'date'}
                or completion.get('verb') not in {'done', 'merged', 'reported'}
                or state != 'completed'
            ):
                raise SourceEventConflict('Firstmate returned unknown completion semantics.')
            verb = completion.get('verb')
            completion_date = completion.get('date') if state == 'completed' else None
            occurred_at = self._timestamp(completion_date)
            if completion_date not in (None, '') and occurred_at is None:
                raise SourceEventConflict('Firstmate returned an invalid completion timestamp.')
            if occurred_at is None and record:
                since = record.get('since')
                occurred_at = self._timestamp(since)
                if since not in (None, '') and occurred_at is None:
                    raise SourceEventConflict('Firstmate returned an invalid objective timestamp.')
            if state == 'completed':
                summary = {
                    'merged': 'Objective was recorded as merged by Firstmate.',
                    'reported': 'Objective report was recorded as complete by Firstmate.',
                }.get(verb, 'Objective was recorded as completed by Firstmate.')
            elif state == 'awaiting-user':
                summary = 'Objective is waiting on an explicit keyed captain decision.'
            elif state == 'failed':
                summary = 'Objective is recorded as failed by Firstmate.'
            elif state == 'cancelled':
                summary = 'Objective is recorded as cancelled by Firstmate.'
            else:
                summary = 'Objective is active in Firstmate.'
            refs: List[Dict[str, str]] = []
            raw_pr_url = record.get('pr_url') if record else None
            pr_url = self._safe_https_url(raw_pr_url)
            if raw_pr_url not in (None, '') and not pr_url:
                raise SourceEventConflict('Firstmate returned an unsafe pull-request reference.')
            if pr_url:
                refs.append({'kind': 'pull-request', 'url': pr_url})
            report_path = record.get('report_path') if record else None
            if report_path not in (None, ''):
                if (
                    not isinstance(report_path, str)
                    or not _SAFE_REPORT_PATH.fullmatch(report_path)
                    or report_path != f'data/{task_id}/report.md'
                ):
                    raise SourceEventConflict('Firstmate returned an unsafe report reference.')
                refs.append({'kind': 'report', 'id': task_id})
            objective_payload = {
                'kind': kind, 'state': state, 'task_id': task_id, 'title': title,
                'objective_id': objective_id, 'run_id': run_id,
                'project': project, 'occurred_at': occurred_at, 'refs': refs,
            }
            candidates.append({
                'record_key': f'objective:{task_id}',
                'kind': kind,
                'state': state,
                'importance': 'attention' if state == 'awaiting-user' else 'routine',
                'title': title,
                'summary': summary,
                'source_payload_sha256': payload_sha256(objective_payload),
                'source_event_id': None,
                'task_id': task_id,
                'decision_key': None,
                'objective_id': objective_id,
                'run_id': run_id,
                'project': project,
                'occurred_at': occurred_at,
                'refs': refs,
            })
        recoverable_focus_count = sum(
            1 for candidate in candidates
            if (
                candidate['kind'] in {'objective.started', 'objective.progress'}
                and candidate['state'] in {'active', 'awaiting-user'}
            ) or (
                candidate['kind'] == 'decision.requested'
                and candidate['state'] == 'awaiting-user'
            )
        )
        if recoverable_focus_count > MAX_ACTIVITY_FOCUS_RECORDS:
            raise SourceUnavailable('Firstmate returned an oversized recoverable-focus projection.')
        semantic_snapshot = {
            'schema': 'fm-fleet-snapshot.v1',
            'generated': snapshot.get('generated'),
            'records': [{key: candidate.get(key) for key in (
                'record_key', 'kind', 'state', 'importance', 'title', 'summary',
                'task_id', 'decision_key', 'objective_id', 'run_id',
                'project', 'occurred_at', 'refs',
            )} for candidate in candidates],
            'open_decisions': open_decisions,
        }
        return generated_at, payload_sha256(semantic_snapshot), candidates, open_decisions, titles

    async def _reconcile_snapshot(
        self, user_id: str, snapshot: Dict[str, Any],
    ) -> Tuple[
        List[Dict[str, Any]],
        Dict[str, Tuple[str, Optional[str], str, Optional[str]]],
    ]:
        observed_at, snapshot_hash, candidates, open_decisions, titles = self._normalize_snapshot(snapshot)
        changed = reconcile_snapshot(
            user_id,
            self.source_instance_id,
            observed_at=observed_at,
            snapshot_sha256=snapshot_hash,
            records=candidates,
            open_decision_keys=open_decisions,
        )
        return changed, titles

    async def reconcile(self, user_id: str) -> Dict[str, Any]:
        """Reconcile both source contracts while preserving partial success."""
        if (
            not isinstance(user_id, str) or not user_id or len(user_id) > 128
            or any(
                unicodedata.category(character).startswith('C')
                or unicodedata.category(character) in {'Zl', 'Zp'}
                for character in user_id
            )
        ):
            raise ValueError('A bounded authenticated principal is required.')
        async with self._lock(user_id):
            changed: List[Dict[str, Any]] = []
            errors: List[Dict[str, str]] = []
            titles: Dict[str, Tuple[str, Optional[str], str, Optional[str]]] = {}
            try:
                snapshot = await self.firstmate.get_snapshot()
                snapshot_changed, titles = await self._reconcile_snapshot(user_id, snapshot)
                changed.extend(snapshot_changed)
            except (SourceUnavailable, SourceEventConflict, ValueError, OSError) as exc:
                mark_source_fault(
                    user_id, self.source_instance_id, SNAPSHOT_STREAM,
                    'snapshot-invalid' if isinstance(exc, SourceEventConflict) else 'snapshot-unavailable',
                    'The structured Firstmate snapshot could not be reconciled safely.',
                    bootstrap_policy='snapshot-current',
                    conflict=isinstance(exc, SourceEventConflict),
                )
                errors.append({'stream': SNAPSHOT_STREAM, 'code': type(exc).__name__})
            try:
                changed.extend(await self._reconcile_branch(user_id, titles))
            except (SourceUnavailable, SourceEventConflict, ValueError, OSError) as exc:
                mark_source_fault(
                    user_id, self.source_instance_id, BRANCH_STREAM,
                    'journal-conflict' if isinstance(exc, SourceEventConflict) else 'journal-unavailable',
                    'The append-only Firstmate outcome journal could not be reconciled safely.',
                    bootstrap_policy=self.bootstrap_policy,
                    conflict=isinstance(exc, SourceEventConflict),
                )
                errors.append({'stream': BRANCH_STREAM, 'code': type(exc).__name__})
            try:
                changed.extend(await self._reconcile_captain_events(user_id, titles))
            except (SourceUnavailable, SourceEventConflict, ValueError, OSError) as exc:
                mark_source_fault(
                    user_id, self.source_instance_id, CAPTAIN_EVENT_STREAM,
                    'captain-event-conflict' if isinstance(exc, SourceEventConflict) else 'captain-event-unavailable',
                    'The private Firstmate captain-event outbox could not be reconciled safely.',
                    bootstrap_policy=self.captain_bootstrap_policy,
                    conflict=isinstance(exc, SourceEventConflict),
                )
                errors.append({'stream': CAPTAIN_EVENT_STREAM, 'code': type(exc).__name__})
            diagnostics = source_diagnostics(user_id)
            return {
                'status': 'degraded' if errors or any(source['state'] == 'fault' for source in diagnostics) else 'available',
                'changed': changed,
                'errors': errors,
                'sources': diagnostics,
            }
