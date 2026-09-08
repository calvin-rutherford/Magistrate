import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import activity_store
from app.activity_store import (
    MAX_ACTIVITY_FOCUS_RECORDS, SourceEventConflict, list_activity,
    reconcile_snapshot, snapshot_activity, source_diagnostics,
)
from app.auth import issue_session
from app.firstmate_activity import FirstmateActivityAdapter
from app.main import app, firstmate_activity
from conftest import TEST_HEADERS, TEST_SESSION_TOKEN


class EmptyFirstmate:
    fm_home = '/unused'

    async def get_snapshot(self):
        return {
            'schema': 'fm-fleet-snapshot.v1',
            'generated': '2026-09-08T04:00:00Z',
            'tasks': [],
            'backlog': {'records': []},
            'secondmate_landed': {'records': []},
        }


def captain_event(
    sequence: int,
    *,
    event_id: str | None = None,
    home: str = 'main',
    task_id: str | None = 'soak-task',
    spawn_generation: int | str = 3,
    kind: str = 'worker.message',
    summary: str | None = None,
    **extra,
):
    source_role = 'primary' if kind.startswith('primary.') else 'worker'
    source_home = home if home == 'main' or home.startswith('secondmate:') else f'secondmate:{home}'
    identity = {
        'schema': 'fm-captain-event.v1',
        'source_home': source_home,
        'source_role': source_role,
        'task_id': None if source_role == 'primary' else task_id,
        'incarnation': str(spawn_generation),
        'producer': 'pi',
        'harness_event_id': f'harness-event-{sequence}',
    }
    derived_id = 'sha256:' + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    ).hexdigest()
    row = {
        **identity,
        'seq': sequence,
        'event_id': event_id or derived_id,
        'published_at_ms': 1_788_840_000_000 + sequence,
        'occurred_at_ms': None,
        'audience': 'captain',
        'kind': kind,
        'summary': summary or f'Durable progress {sequence}.',
        'summary_truncated': False,
        'refs': {},
    }
    row.update(extra)
    return row


def write_outbox(path: Path, rows, *, newline: bool = True, mode: int = 0o600):
    payload = '\n'.join(json.dumps(row, separators=(',', ':'), sort_keys=True) for row in rows)
    if newline:
        payload += '\n'
    path.write_text(payload, encoding='utf-8')
    path.chmod(mode)


def adapter(tmp_path: Path, rows, *, policy: str = 'from-start', firstmate=None) -> FirstmateActivityAdapter:
    state = tmp_path / 'state'
    state.mkdir(parents=True, exist_ok=True)
    (state / 'branch-outcomes.jsonl').write_text('', encoding='utf-8')
    outbox = state / 'captain-events' / 'events.jsonl'
    outbox.parent.mkdir(mode=0o700)
    write_outbox(outbox, rows)
    return FirstmateActivityAdapter(
        firstmate or EmptyFirstmate(),
        fm_home=str(tmp_path),
        captain_event_path=str(outbox),
        captain_bootstrap_policy=policy,
    )


@pytest.mark.asyncio
async def test_real_semantic_outbox_persists_twelve_ordered_progress_records_and_restarts(tmp_path):
    user = 'activity-outbox-owner'
    rows = [
        captain_event(index, **({'refs': {
            'pr_url': 'https://github.com/acme/project/pull/12',
            'report_id': 'soak-report',
            'report_path': 'data/soak-task/report.md',
        }, 'summary_truncated': True} if index == 1 else {}))
        for index in range(1, 13)
    ]
    source = adapter(tmp_path, rows)

    first = await source.reconcile(user)
    records = [record for record in first['changed'] if record['source']['instance_id'] == 'firstmate:main']
    assert first['status'] == 'available'
    assert len(records) == 12
    assert [record['summary'] for record in records] == [f'Durable progress {index}.' for index in range(1, 13)]
    assert {(record['kind'], record['state']) for record in records} == {('worker.message', 'completed')}
    assert len({record['objective_id'] for record in records}) == 1
    assert len({record['run_id'] for record in records}) == 1
    assert records[0]['objective_id'] != records[0]['run_id']
    assert records[0]['summary_truncated'] is True
    assert records[0]['refs'] == [
        {'kind': 'pull-request', 'url': 'https://github.com/acme/project/pull/12'},
        {'kind': 'report', 'id': 'soak-report'},
    ]

    # A new adapter instance uses the durable cursor and emits no duplicates.
    restarted = FirstmateActivityAdapter(
        EmptyFirstmate(),
        fm_home=str(tmp_path),
        captain_event_path=str(tmp_path / 'state' / 'captain-events' / 'events.jsonl'),
        captain_bootstrap_policy='from-start',
    )
    assert not [
        record for record in (await restarted.reconcile(user))['changed']
        if record['source']['instance_id'] == 'firstmate:main'
    ]

    rows.append(captain_event(13, kind='worker.final', summary='The worker result is durable.'))
    write_outbox(tmp_path / 'state' / 'captain-events' / 'events.jsonl', rows)
    appended = await restarted.reconcile(user)
    [final] = [
        record for record in appended['changed']
        if record['source']['instance_id'] == 'firstmate:main'
    ]
    assert (final['kind'], final['state']) == ('worker.final', 'completed')
    # Pi's final stop reason finalises this message, not the surrounding task;
    # task completion remains owned by the structured fleet/outcome sources.
    assert final['objective_id'] == records[0]['objective_id']
    diagnostics = next(
        item for item in source_diagnostics(user)
        if item['source_instance_id'] == 'firstmate:main' and item['stream'] == 'captain-events'
    )
    assert diagnostics['cursor'] == diagnostics['source_tail'] == 13
    assert diagnostics['lag'] == 0
    assert diagnostics['accepted_count'] == 13

    # Once bound, disappearance is unavailable rather than a fabricated empty
    # healthy stream; accepted activity remains replayable.
    (tmp_path / 'state' / 'captain-events' / 'events.jsonl').unlink()
    unavailable = await restarted.reconcile(user)
    assert unavailable['status'] == 'degraded'
    assert len([record for record in list_activity(user)['records'] if record['source']['instance_id'] == 'firstmate:main']) == 13


@pytest.mark.asyncio
async def test_versioned_reader_is_activated_explicitly_and_acked_only_after_ingestion(tmp_path):
    state = tmp_path / 'state'
    journal = state / 'captain-events' / 'events.jsonl'
    journal.parent.mkdir(parents=True, mode=0o700)
    rows = [captain_event(1), captain_event(2, kind='worker.final')]
    write_outbox(journal, rows)
    (state / 'branch-outcomes.jsonl').write_text('', encoding='utf-8')
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'captain-event-outbox').write_text('enabled\n', encoding='utf-8')
    binary = tmp_path / 'bin'
    binary.mkdir()
    reader = binary / 'fm-captain-event.sh'
    reader.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
home = pathlib.Path(os.environ["FM_HOME"])
command = sys.argv[1]
if command == "enabled":
    raise SystemExit(0 if (home / "config/captain-event-outbox").exists() else 1)
rows = [json.loads(line) for line in (home / "state/captain-events/events.jsonl").read_text().splitlines()]
ack_dir = home / "state/captain-events/acks"
ack_dir.mkdir(exist_ok=True)
ack_temps = list(ack_dir.glob(".magistrate.json.*.tmp"))
if command == "read":
    if ack_temps:
        print("ack recovery required", file=sys.stderr)
        raise SystemExit(2)
    after = int(sys.argv[sys.argv.index("--after") + 1])
    limit = int(sys.argv[sys.argv.index("--limit") + 1])
    for row in rows[after:after + limit]:
        print(json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
elif command == "ack":
    through = int(sys.argv[sys.argv.index("--through") + 1])
    event_id = sys.argv[sys.argv.index("--event-id") + 1]
    (home / "state/captain-events/ack-observed.json").write_text(json.dumps({"through": through, "event_id": event_id}))
    for temporary in ack_temps:
        temporary.unlink()
    print(through)
else:
    raise SystemExit(2)
''', encoding='utf-8')
    # Shared Firstmate worktrees may retain the collaboration group's write bit;
    # ownership, regular-file/single-link identity, and executable status bind it.
    reader.chmod(0o775)

    source = FirstmateActivityAdapter(EmptyFirstmate(), fm_home=str(tmp_path))
    result = await source.reconcile('activity-reader-owner')
    assert result['status'] == 'available'
    receipt = json.loads((journal.parent / 'ack-observed.json').read_text(encoding='utf-8'))
    assert receipt == {'through': 2, 'event_id': rows[-1]['event_id']}
    assert [record['kind'] for record in list_activity('activity-reader-owner')['records']] == [
        'worker.message', 'worker.final',
    ]

    # A crash can leave Firstmate's atomic acknowledgement temporary after the
    # canonical transaction committed. Reasserting only the durable cursor
    # clears that producer-owned recovery state before the next bounded read.
    ack_temporary = journal.parent / 'acks' / ('.magistrate.json.123.' + ('a' * 32) + '.tmp')
    ack_temporary.write_text('partial', encoding='utf-8')
    ack_temporary.chmod(0o600)
    assert (await source.reconcile('activity-reader-owner'))['status'] == 'available'
    assert not ack_temporary.exists()

    # Deactivation is not interpreted as an empty/truncated source; accepted
    # canonical rows survive and source diagnostics become explicitly degraded.
    (config / 'captain-event-outbox').unlink()
    disabled = await source.reconcile('activity-reader-owner')
    assert disabled['status'] == 'degraded'
    assert len(list_activity('activity-reader-owner')['records']) == 2


@pytest.mark.asyncio
async def test_branch_outcome_uses_an_independent_default_tail_cursor_and_immutable_prefix(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    journal = state / 'branch-outcomes.jsonl'
    rows = [{
        'seq': index, 'epoch': 1_788_840_000 + index, 'task': 'branch-task',
        'wake': f'internal wake {index}', 'verdict': 'routine',
        'summary': f'Historical supervision {index}.',
    } for index in range(1, 3)]
    journal.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    # This sidecar belongs to Firstmate/Pi and must never influence or be
    # changed by Magistrate's independent consumer.
    sidecar = state / '.branch-outcomes-cursor'
    sidecar.write_text('999\n', encoding='utf-8')
    source = FirstmateActivityAdapter(EmptyFirstmate(), fm_home=str(tmp_path))

    initial = await source.reconcile('activity-branch-owner')
    assert not [record for record in initial['changed'] if record['source']['event_id']]
    branch_state = next(item for item in source_diagnostics('activity-branch-owner') if item['stream'] == 'branch-outcomes')
    assert (branch_state['bootstrap_policy'], branch_state['cursor']) == ('tail', 2)

    rows.append({
        'seq': 3, 'epoch': 1_788_840_003, 'task': 'branch-task',
        'wake': 'private routing evidence', 'verdict': 'captain',
        'summary': 'New supervision outcome.',
    })
    journal.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    [new] = [
        record for record in (await source.reconcile('activity-branch-owner'))['changed']
        if record['source']['event_id'] == 'branch-outcome:3'
    ]
    assert new['summary'] == 'New supervision outcome.'
    assert 'wake' not in json.dumps(new)
    assert sidecar.read_text(encoding='utf-8') == '999\n'

    original_bytes = journal.read_bytes()
    journal.write_bytes(original_bytes.replace(b'\n', b'  \n', 1))
    assert (await source.reconcile('activity-branch-owner'))['status'] == 'degraded'
    journal.write_bytes(original_bytes)
    assert (await source.reconcile('activity-branch-owner'))['status'] == 'available'

    rows[0]['summary'] = 'rewritten old prefix'
    journal.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    conflict = await source.reconcile('activity-branch-owner')
    assert conflict['status'] == 'degraded'
    assert next(item for item in source_diagnostics('activity-branch-owner') if item['stream'] == 'branch-outcomes')['cursor'] == 3


@pytest.mark.asyncio
async def test_outbox_is_idempotent_per_tenant_and_never_accepts_client_ownership(tmp_path):
    rows = [captain_event(1)]
    source = adapter(tmp_path, rows)
    await source.reconcile('activity-owner-a')
    await source.reconcile('activity-owner-b')

    [a] = [record for record in list_activity('activity-owner-a')['records'] if record['summary'] == 'Durable progress 1.']
    [b] = [record for record in list_activity('activity-owner-b')['records'] if record['summary'] == 'Durable progress 1.']
    assert a['id'] != b['id']
    assert a['objective_id'] == b['objective_id']
    assert list_activity('activity-owner-c')['records'] == []

    # Reconciliation is idempotent inside each authenticated tenant.
    await source.reconcile('activity-owner-a')
    assert len([record for record in list_activity('activity-owner-a')['records'] if record['summary'] == 'Durable progress 1.']) == 1


@pytest.mark.asyncio
async def test_one_home_journal_can_interleave_primary_and_worker_causality(tmp_path):
    rows = [
        captain_event(1, home='main', task_id=None, kind='primary.message'),
        captain_event(2, home='main'),
        captain_event(3, home='main', task_id=None, kind='primary.final'),
        captain_event(4, home='main', kind='worker.final'),
    ]
    source = adapter(tmp_path, rows)
    result = await source.reconcile('activity-home-owner')
    emitted = [
        record for record in result['changed']
        if record['source']['instance_id'] == 'firstmate:main'
    ]
    assert len(emitted) == 4
    assert len({record['objective_id'] for record in emitted}) == 2
    state = next(
        item for item in source_diagnostics('activity-home-owner')
        if item['source_instance_id'] == 'firstmate:main' and item['stream'] == 'captain-events'
    )
    assert state['cursor'] == state['source_tail'] == 4


@pytest.mark.asyncio
async def test_prefix_rewrite_gap_torn_tail_and_payload_conflict_fail_closed(tmp_path):
    user = 'activity-conflict-owner'
    rows = [captain_event(1)]
    source = adapter(tmp_path, rows)
    await source.reconcile(user)

    # Immutable-prefix verification catches a rewritten already-accepted row.
    write_outbox(tmp_path / 'state' / 'captain-events' / 'events.jsonl', [captain_event(1, summary='rewritten')])
    rewritten = await source.reconcile(user)
    assert rewritten['status'] == 'degraded'
    assert rewritten['errors'][-1]['stream'] == 'captain-events'
    assert list_activity(user)['records'][-1]['summary'] == 'Durable progress 1.'

    gap_source = adapter(tmp_path / 'gap', [captain_event(1), captain_event(3)])
    assert (await gap_source.reconcile('activity-gap-owner'))['status'] == 'degraded'
    assert list_activity('activity-gap-owner')['records'] == []

    torn_source = adapter(tmp_path / 'torn', [captain_event(1)])
    write_outbox(tmp_path / 'torn' / 'state' / 'captain-events' / 'events.jsonl', [captain_event(1)], newline=False)
    assert (await torn_source.reconcile('activity-torn-owner'))['status'] == 'degraded'
    assert list_activity('activity-torn-owner')['records'] == []

    duplicate = captain_event(1)
    duplicate_rows = [
        duplicate,
        {**duplicate, 'seq': 2, 'published_at_ms': duplicate['published_at_ms'] + 1,
         'summary': 'different content'},
    ]
    duplicate_source = adapter(tmp_path / 'duplicate', duplicate_rows)
    assert (await duplicate_source.reconcile('activity-duplicate-owner'))['status'] == 'degraded'
    assert list_activity('activity-duplicate-owner')['records'] == []
    failed_state = next(item for item in source_diagnostics('activity-duplicate-owner') if item['stream'] == 'captain-events')
    assert failed_state['cursor'] == 0, 'event projection and cursor rollback together on conflict'
    write_outbox(
        tmp_path / 'duplicate' / 'state' / 'captain-events' / 'events.jsonl',
        [captain_event(1), captain_event(2)],
    )
    recovered = await duplicate_source.reconcile('activity-duplicate-owner')
    assert recovered['status'] == 'available'
    assert len([record for record in recovered['changed'] if record['source']['instance_id'] == 'firstmate:main']) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(('case', 'patch'), [
    ('audience', {'audience': 'worker'}),
    ('source-home-binding', {'source_home': 'secondmate:other'}),
    ('terminal', {'terminal': 'raw pane bytes'}),
    ('summary', {'summary': 'x' * 601}),
    ('kind', {'kind': 'unknown.message'}),
    ('timestamp', {'published_at_ms': 9_007_199_254_740_992}),
    ('credential-summary', {'summary': 'token=ghp_1234567890 should never persist'}),
    ('credential-uri', {'summary': 'database_url=postgres://owner:password@db.example/internal'}),
    ('generic-environment', {'summary': 'DB_PASS="opaque value with spaces" must not persist'}),
    ('append-environment', {'summary': 'SAFE+=opaque-value must not persist'}),
    ('spaced-append-environment', {'summary': 'MiXeD + = opaque-value must not persist'}),
    ('bare-credential-uri', {'summary': 'Connection postgres://owner:opaque-pass@db.example/internal'}),
    ('bare-userinfo-uri', {'summary': 'Connection ssh://private-user@host.example/internal'}),
    ('basic-authorization', {'summary': 'Authorization: Basic dXNlcjpwYXNz'}),
    ('quoted-secret-label', {'summary': '"client_secret":"opaquevalue"'}),
    ('compound-secret-label', {'summary': 'Azure AD Client Secret: opaquevalue'}),
    ('compact-jwt', {'summary': 'eyJabcdefghijk.eyJabcdefghijk.signature123'}),
    ('credential-url', {'refs': {'pr_url': 'https://user:secret@example.com/pull/1'}}),
    ('credential-query', {'refs': {'pr_url': 'https://github.com/acme/repo/pull/1?token=secret'}}),
    ('bare-query', {'refs': {'pr_url': 'https://github.com/acme/repo/pull/1?'}}),
    ('bare-fragment', {'refs': {'pr_url': 'https://github.com/acme/repo/pull/1#'}}),
    ('credential-path', {'refs': {'pr_url': 'https://github.com/token=ghp_1234567890/repo/pull/1'}}),
    ('encoded-credential-path', {'refs': {'pr_url': 'https://github.com/token%253Dghp_1234567890/repo/pull/1'}}),
    ('non-forge-url', {'refs': {'pr_url': 'https://example.com/safe-looking/path'}}),
    ('cross-task-report-path', {'refs': {'report_path': 'data/another-task/report.md'}}),
])
async def test_unknown_audience_private_terminal_fields_and_bounds_fail_closed(tmp_path, case, patch):
    row = captain_event(1)
    row.update(patch)
    source = adapter(tmp_path, [row])
    result = await source.reconcile(f'activity-invalid-{case}')
    assert result['status'] == 'degraded'
    assert result['errors'][-1]['stream'] == 'captain-events'


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['duplicate-key', 'noncanonical-json', 'crlf', 'hard-link', 'symbolic-link'])
async def test_outbox_rejects_ambiguous_serialization_and_link_swaps(tmp_path, mutation):
    source = adapter(tmp_path, [captain_event(1)])
    outbox = tmp_path / 'state' / 'captain-events' / 'events.jsonl'
    if mutation == 'duplicate-key':
        raw = outbox.read_text(encoding='utf-8').replace('"seq":1', '"seq":1,"seq":1', 1)
        outbox.write_text(raw, encoding='utf-8')
        outbox.chmod(0o600)
    elif mutation == 'noncanonical-json':
        outbox.write_text(json.dumps(captain_event(1), sort_keys=True) + '\n', encoding='utf-8')
        outbox.chmod(0o600)
    elif mutation == 'crlf':
        outbox.write_bytes(outbox.read_bytes().replace(b'\n', b'\r\n'))
        outbox.chmod(0o600)
    elif mutation == 'hard-link':
        outbox.with_name('alias.jsonl').hardlink_to(outbox)
    else:
        real = outbox.with_name('real.jsonl')
        outbox.rename(real)
        outbox.symlink_to(real.name)
    result = await source.reconcile(f'activity-file-{mutation}')
    assert result['status'] == 'degraded'
    assert list_activity(f'activity-file-{mutation}')['records'] == []


@pytest.mark.asyncio
async def test_outbox_requires_private_regular_state_and_tail_bootstrap_is_explicit(tmp_path):
    source = adapter(tmp_path, [captain_event(1)], policy='tail')
    outbox = tmp_path / 'state' / 'captain-events' / 'events.jsonl'
    outbox.chmod(0o644)
    invalid = await source.reconcile('activity-mode-owner')
    assert invalid['status'] == 'degraded'
    assert list_activity('activity-mode-owner')['records'] == []

    outbox.chmod(0o600)
    bootstrapped = await source.reconcile('activity-tail-owner')
    assert bootstrapped['status'] == 'available'
    assert list_activity('activity-tail-owner')['records'] == []
    state = next(item for item in source_diagnostics('activity-tail-owner') if item['stream'] == 'captain-events')
    assert (state['bootstrap_policy'], state['cursor']) == ('tail', 1)


def test_snapshot_reconciliation_never_regresses_or_reuses_an_observation_identity():
    user = 'activity-snapshot-order-owner'
    base = {
        'record_key': 'objective:ordered',
        'kind': 'objective.progress', 'state': 'active', 'importance': 'routine',
        'title': 'Ordered snapshot', 'summary': 'Newer state.',
        'source_payload_sha256': 'c' * 64, 'source_event_id': None,
        'task_id': 'ordered', 'decision_key': None,
        'objective_id': 'obj_ordered', 'run_id': 'run_ordered',
        'project': None, 'occurred_at': None, 'refs': [],
    }
    reconcile_snapshot(
        user, 'test:snapshot-order', observed_at=2000,
        snapshot_sha256='d' * 64, records=[base], open_decision_keys=[],
    )
    assert reconcile_snapshot(
        user, 'test:snapshot-order', observed_at=1000,
        snapshot_sha256='e' * 64,
        records=[{**base, 'state': 'failed', 'kind': 'objective.failed', 'summary': 'Stale state.'}],
        open_decision_keys=[],
    ) == []
    [record] = list_activity(user)['records']
    assert (record['kind'], record['summary']) == ('objective.progress', 'Newer state.')
    with pytest.raises(SourceEventConflict, match='timestamp'):
        reconcile_snapshot(
            user, 'test:snapshot-order', observed_at=2000,
            snapshot_sha256='f' * 64,
            records=[{**base, 'summary': 'Conflicting same-time state.'}],
            open_decision_keys=[],
        )
    with pytest.raises(ValueError, match='kind, state'):
        reconcile_snapshot(
            'activity-invalid-semantic-owner', 'test:snapshot-invalid', observed_at=2000,
            snapshot_sha256='a' * 64,
            records=[{**base, 'kind': 'objective.completed', 'state': 'active'}],
            open_decision_keys=[],
        )
    with pytest.raises(ValueError, match='observed_at'):
        reconcile_snapshot(
            'activity-invalid-time-owner', 'test:snapshot-invalid', observed_at=-1,
            snapshot_sha256='a' * 64, records=[], open_decision_keys=[],
        )


def test_snapshot_recovers_every_accepted_active_objective_and_pending_decision():
    user = 'activity-focus-capacity-owner'
    candidates = []
    open_decisions = []
    for index in range(1_000):
        task_id = f'focus-task-{index}'
        objective_id = f'obj_focus_{index}'
        common = {
            'importance': 'routine', 'title': f'Focus task {index}',
            'source_event_id': None, 'task_id': task_id, 'objective_id': objective_id,
            'run_id': f'run_focus_{index}', 'project': None, 'occurred_at': None, 'refs': [],
        }
        candidates.append({
            **common, 'record_key': f'objective:{index}', 'kind': 'objective.progress',
            'state': 'active', 'summary': 'Active objective.', 'decision_key': None,
            'source_payload_sha256': hashlib.sha256(f'objective:{index}'.encode()).hexdigest(),
        })
        decision_key = f'decision-{index}'
        candidates.append({
            **common, 'record_key': f'decision:{index}', 'kind': 'decision.requested',
            'state': 'awaiting-user', 'importance': 'attention', 'summary': 'Choose an option.',
            'decision_key': decision_key,
            'source_payload_sha256': hashlib.sha256(f'decision:{index}'.encode()).hexdigest(),
        })
        open_decisions.append(f'{task_id}\0{decision_key}')
    reconcile_snapshot(
        user, 'firstmate:main', observed_at=2_000, snapshot_sha256='a' * 64,
        records=candidates, open_decision_keys=open_decisions,
    )

    projection = snapshot_activity(user, limit=1)
    assert len(projection['focus_records']) == MAX_ACTIVITY_FOCUS_RECORDS
    assert projection['focus_truncated'] is False
    assert projection['summary'] == {
        'active_objectives': 1_000, 'operation_count': 0, 'pending_decisions': 1_000,
    }


def test_replay_cursor_records_and_summary_share_one_read_snapshot(monkeypatch):
    user = 'activity-replay-snapshot-owner'
    active = {
        'record_key': 'objective:replay-snapshot',
        'kind': 'objective.progress', 'state': 'active', 'importance': 'routine',
        'title': 'Replay snapshot', 'summary': 'Objective is active.',
        'source_payload_sha256': 'b' * 64, 'source_event_id': None,
        'task_id': 'replay-snapshot', 'decision_key': None,
        'objective_id': 'obj_replay_snapshot', 'run_id': 'run_replay_snapshot',
        'project': None, 'occurred_at': None, 'refs': [],
    }
    reconcile_snapshot(
        user, 'test:replay-snapshot', observed_at=1_000,
        snapshot_sha256='c' * 64, records=[active], open_decision_keys=[],
    )
    before_update = list_activity(user)['latest_cursor']
    with sqlite3.connect(activity_store.db.DB_PATH) as conn:
        conn.execute('PRAGMA journal_mode=WAL')

    original_summary = activity_store._activity_summary
    update_committed = False

    def update_before_summary(conn, owner):
        nonlocal update_committed
        if owner == user and not update_committed:
            update_committed = True
            reconcile_snapshot(
                user, 'test:replay-snapshot', observed_at=2_000,
                snapshot_sha256='d' * 64,
                records=[{
                    **active, 'kind': 'objective.completed', 'state': 'completed',
                    'summary': 'Objective completed.', 'source_payload_sha256': 'e' * 64,
                }],
                open_decision_keys=[],
            )
        return original_summary(conn, owner)

    monkeypatch.setattr(activity_store, '_activity_summary', update_before_summary)
    replay = list_activity(user)
    assert replay['latest_cursor'] == before_update
    assert [(row['revision'], row['state']) for row in replay['records']] == [(1, 'active')]
    assert replay['summary'] == {
        'active_objectives': 1, 'operation_count': 0, 'pending_decisions': 0,
    }

    current = list_activity(user, after=before_update)
    assert current['latest_cursor'] > replay['latest_cursor']
    assert [(row['revision'], row['state']) for row in current['records']] == [(2, 'completed')]
    assert current['summary']['active_objectives'] == 0


@pytest.mark.asyncio
async def test_snapshot_ignores_pane_derived_runtime_state_but_keeps_keyed_decisions(tmp_path):
    class SnapshotFirstmate(EmptyFirstmate):
        async def get_snapshot(self):
            snapshot = await super().get_snapshot()
            snapshot['tasks'] = [{
                'id': 'pane-only', 'spawn_gen': '1',
                'current_state': {'source': 'pane', 'state': 'working'},
                'hints': {},
            }, {
                'id': 'decision-task', 'spawn_gen': '4',
                'current_state': {'source': 'pane', 'state': 'working'},
                'hints': {'open_decisions': [{
                    'verb': 'needs-decision', 'key': 'ship-or-hold',
                    'summary': 'Choose whether to ship.',
                }]},
            }]
            return snapshot

    state = tmp_path / 'state'
    state.mkdir()
    (state / 'branch-outcomes.jsonl').write_text('', encoding='utf-8')
    source = FirstmateActivityAdapter(SnapshotFirstmate(), fm_home=str(tmp_path))
    result = await source.reconcile('activity-pane-owner')
    assert not any(record['task_id'] == 'pane-only' for record in result['changed'])
    [decision] = [record for record in result['changed'] if record['kind'] == 'decision.requested']
    assert decision['decision_key'] == 'ship-or-hold'
    assert decision['state'] == 'awaiting-user'


@pytest.mark.asyncio
async def test_outbox_messages_share_snapshot_objective_and_run_causality(tmp_path):
    class RunningFirstmate(EmptyFirstmate):
        async def get_snapshot(self):
            snapshot = await super().get_snapshot()
            snapshot['tasks'] = [{
                'id': 'soak-task', 'spawn_gen': '3',
                'current_state': {'source': 'firstmate', 'state': 'working'},
                'hints': {},
            }]
            snapshot['backlog'] = {'records': [{
                'id': 'soak-task', 'title': 'SOAK task', 'state': 'in_flight',
            }]}
            return snapshot

    source = adapter(tmp_path, [captain_event(1)], firstmate=RunningFirstmate())
    changed = (await source.reconcile('activity-causal-owner'))['changed']
    objective = next(record for record in changed if record['kind'] == 'objective.started')
    message = next(record for record in changed if record['kind'] == 'worker.message')
    assert (message['objective_id'], message['run_id']) == (
        objective['objective_id'], objective['run_id'],
    )


@pytest.mark.asyncio
async def test_keyed_decision_disappearance_records_only_resolved_not_an_outcome(tmp_path):
    class DecisionFirstmate(EmptyFirstmate):
        def __init__(self):
            self.open = True
            self.unknown = False
            self.observation = 0

        async def get_snapshot(self):
            self.observation += 1
            decision = [{
                'verb': 'future-unknown' if self.unknown else 'needs-decision',
                'key': 'deploy-choice',
                'summary': 'Choose a deployment channel.',
            }] if self.open else []
            return {
                'schema': 'fm-fleet-snapshot.v1',
                'generated': f'2026-09-08T04:00:0{self.observation}Z',
                'tasks': [{
                    'id': 'decision-lifecycle', 'spawn_gen': '1',
                    'hints': {'open_decisions': decision},
                }],
                'backlog': {'records': [{
                    'id': 'decision-lifecycle', 'title': 'Decision lifecycle',
                    'state': 'in_flight',
                }]},
                'secondmate_landed': {'records': []},
            }

    state = tmp_path / 'state'
    state.mkdir()
    (state / 'branch-outcomes.jsonl').write_text('', encoding='utf-8')
    firstmate = DecisionFirstmate()
    source = FirstmateActivityAdapter(firstmate, fm_home=str(tmp_path))
    first = await source.reconcile('activity-decision-owner')
    [requested] = [record for record in first['changed'] if record['kind'] == 'decision.requested']
    assert requested['state'] == 'awaiting-user'
    before_resolution = list_activity('activity-decision-owner')['latest_cursor']

    firstmate.unknown = True
    refused = await source.reconcile('activity-decision-owner')
    assert refused['status'] == 'degraded'
    assert next(
        record for record in list_activity('activity-decision-owner')['records']
        if record['id'] == requested['id']
    )['state'] == 'awaiting-user'

    firstmate.unknown = False
    firstmate.open = False
    second = await source.reconcile('activity-decision-owner')
    [resolved] = [record for record in second['changed'] if record['id'] == requested['id']]
    assert (resolved['kind'], resolved['state']) == ('decision.resolved', 'resolved')
    replay = list_activity('activity-decision-owner', after=before_resolution)
    replayed_resolution = next(record for record in replay['records'] if record['id'] == requested['id'])
    assert (replayed_resolution['revision'], replayed_resolution['state']) == (2, 'resolved')
    assert replayed_resolution['delivery_sequence'] > before_resolution
    assert 'approved' not in resolved['summary'].lower()
    assert 'rejected' not in resolved['summary'].lower()


def test_activity_http_replay_and_opt_in_websocket_are_principal_scoped(monkeypatch):
    user = 'default_user'
    before = list_activity(user)['latest_cursor']
    reconcile_snapshot(
        user,
        'test:semantic-source',
        observed_at=1_788_840_000_000,
        snapshot_sha256='a' * 64,
        records=[{
            'record_key': 'objective:http-replay',
            'kind': 'objective.progress', 'state': 'active', 'importance': 'routine',
            'title': 'HTTP replay objective', 'summary': 'Actual semantic progress.',
            'source_payload_sha256': 'b' * 64, 'source_event_id': None,
            'task_id': 'http-replay', 'decision_key': None,
            'objective_id': 'obj_http_replay', 'run_id': 'run_http_replay',
            'project': 'Magistrate', 'occurred_at': None, 'refs': [],
        }],
        open_decision_keys=[],
    )
    monkeypatch.setattr(firstmate_activity, 'reconcile', AsyncMock(return_value={
        'status': 'available', 'changed': [], 'errors': [], 'sources': [],
    }))
    monkeypatch.setattr('app.main._ingest_target_snapshot', AsyncMock(return_value=None))
    client = TestClient(app)

    assert client.get('/api/v1/activity/replay').status_code == 401
    assert client.get('/api/v1/activity/snapshot?reconcile=false').status_code == 401
    replay = client.get(f'/api/v1/activity/replay?after={before}&limit=10', headers=TEST_HEADERS)
    assert replay.status_code == 200
    assert [row['summary'] for row in replay.json()['records']] == ['Actual semantic progress.']
    assert replay.json()['records'][0]['objective_id'] == 'obj_http_replay'
    snapshot = client.get(
        '/api/v1/activity/snapshot?limit=1&reconcile=false&user_id=isolated-http-owner',
        headers=TEST_HEADERS,
    )
    assert snapshot.status_code == 200
    assert snapshot.json()['snapshot_cursor'] == replay.json()['latest_cursor']
    assert snapshot.json()['focus_records'][0]['id'] == replay.json()['records'][0]['id']
    assert snapshot.json()['summary'] == {
        'active_objectives': 1, 'operation_count': 0, 'pending_decisions': 0,
    }
    assert client.get(
        f"/api/v1/activity/replay?after={replay.json()['latest_cursor'] + 1}",
        headers=TEST_HEADERS,
    ).status_code == 422
    assert client.get('/api/v1/diagnostics/soak').status_code == 401
    diagnostics = client.get('/api/v1/diagnostics/soak', headers=TEST_HEADERS)
    assert diagnostics.status_code == 200
    assert diagnostics.json()['schema_version'] == 'soak-diagnostics.v1'
    assert {'conversation_ingest', 'turn_lifecycle', 'activity_sources'} <= diagnostics.json().keys()
    assert client.post(
        '/api/v1/activity/catch-up', headers=TEST_HEADERS,
        json={'after': before, 'limit': 10, 'reconcile': False, 'user_id': 'another-owner'},
    ).status_code == 422
    catch_up = client.post(
        '/api/v1/activity/catch-up', headers=TEST_HEADERS,
        json={'after': before, 'limit': 10, 'reconcile': False},
    )
    assert [row['summary'] for row in catch_up.json()['records']] == ['Actual semantic progress.']

    monkeypatch.setenv('MAGISTRATE_BOOTSTRAP_USER_ID', 'isolated-http-owner')
    other_token = issue_session('test-bootstrap-secret')['session_token']
    other = client.get(
        f'/api/v1/activity/replay?after={before}&limit=10',
        headers={'Authorization': f'Bearer {other_token}'},
    )
    assert other.status_code == 200
    assert other.json()['records'] == []
    isolated_snapshot = client.get(
        '/api/v1/activity/snapshot?reconcile=false',
        headers={'Authorization': f'Bearer {other_token}'},
    )
    assert isolated_snapshot.status_code == 200
    assert isolated_snapshot.json()['records'] == []
    assert isolated_snapshot.json()['focus_records'] == []

    with client.websocket_connect('/api/v1/events') as socket:
        socket.send_json({
            'type': 'auth', 'token': TEST_SESSION_TOKEN, 'target': 'captain',
            'activity_after': before,
        })
        assert socket.receive_json()['type'] == 'connected'
        activity = socket.receive_json()
        if activity['type'] == 'conversation_messages':
            activity = socket.receive_json()
        assert activity['type'] == 'activity_records'
        assert [row['summary'] for row in activity['records']] == ['Actual semantic progress.']


@pytest.mark.parametrize('control', [
    {'target': 7},
    {'target': 'captain', 'activity_after': -1},
    {'activity_after': '0'},
    {'activity_after': True},
])
def test_websocket_control_targets_and_activity_cursors_fail_closed(control):
    client = TestClient(app)
    with client.websocket_connect('/api/v1/events') as socket:
        socket.send_json({'type': 'auth', 'token': TEST_SESSION_TOKEN, 'target': 'captain'})
        assert socket.receive_json()['type'] == 'connected'
        socket.send_json(control)
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1008


def test_websocket_auth_and_control_frames_have_a_hard_utf8_byte_bound():
    client = TestClient(app)
    with client.websocket_connect('/api/v1/events') as socket:
        socket.send_text(json.dumps({
            'type': 'auth', 'token': TEST_SESSION_TOKEN, 'target': 'captain',
            'padding': 'x' * 4096,
        }))
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1009

    with client.websocket_connect('/api/v1/events') as socket:
        socket.send_json({'type': 'auth', 'token': TEST_SESSION_TOKEN, 'target': 'captain'})
        assert socket.receive_json()['type'] == 'connected'
        socket.send_text(json.dumps({'target': 'captain', 'padding': 'x' * 4096}))
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1009
