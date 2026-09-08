import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.activity_store import list_activity, source_diagnostics
from app.conversation_store import list_messages
from app.firstmate_activity import FirstmateActivityAdapter
from app.firstmate_producer import FIRSTMATE_PRODUCER_PIN, validate_producer_root

PINNED_ROOT = os.getenv('MAGISTRATE_TEST_PINNED_FIRSTMATE_ROOT')
pytestmark = pytest.mark.skipif(
    not PINNED_ROOT,
    reason='set MAGISTRATE_TEST_PINNED_FIRSTMATE_ROOT via the immutable installer runtime gate',
)


class SemanticFirstmate:
    def __init__(self, home: Path, root: Path):
        self.fm_home = str(home)
        self.fm_root = str(root)
        self.fm_root_is_explicit = True
        self.captain_producer_required = True
        self.phase = 0

    def validate_producer_contract(self):
        validate_producer_root(self.fm_root, fm_home=self.fm_home)

    async def get_snapshot(self):
        decision = []
        state = 'in_flight'
        completion = None
        current_state = {'source': 'firstmate', 'state': 'working'}
        if self.phase == 1:
            decision = [{
                'verb': 'needs-decision',
                'key': 'soak-release-choice',
                'summary': 'Choose whether the harmless soak candidate may advance.',
            }]
        elif self.phase == 2:
            state = 'done'
            completion = {'verb': 'done', 'date': '2026-09-08'}
            current_state = {'source': 'firstmate', 'state': 'completed'}
        record = {
            'id': 'soak-runtime-task',
            'title': 'Pinned semantic producer compatibility',
            'state': state,
            'repo': '/safe/projects/Magistrate',
        }
        if completion:
            record['completion'] = completion
        return {
            'schema': 'fm-fleet-snapshot.v1',
            'fm_home': self.fm_home,
            'generated': f'2026-09-08T20:5{self.phase}:00Z',
            'tasks': [{
                'id': 'soak-runtime-task',
                'spawn_gen': 'spawn-runtime-001',
                'current_state': current_state,
                'hints': {'open_decisions': decision},
            }],
            'backlog': {'records': [record]},
            'secondmate_landed': {'records': []},
        }


def _installer(root: Path, home: Path, command: str, *extra: str, check: bool = True):
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'install_firstmate_producer.sh'
    return subprocess.run(
        [str(script), command, '--root', str(root), '--fm-home', str(home), *extra],
        check=check,
        capture_output=True,
        text=True,
        timeout=60,
        env={
            'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
            'HOME': str(home.parent),
            'LANG': 'C.UTF-8',
            'LC_ALL': 'C.UTF-8',
        },
    )


def _run_real_producer(root: Path, home: Path) -> str:
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the pinned Pi producer compatibility fixture')
    fixture = Path(__file__).parent / 'fixtures' / 'run_pinned_firstmate_producer.mjs'
    result = subprocess.run(
        [node, '--experimental-strip-types', str(fixture), str(root), str(home)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env={
            'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
            'HOME': str(home.parent),
            'LANG': 'C.UTF-8',
            'LC_ALL': 'C.UTF-8',
            'NODE_NO_WARNINGS': '1',
        },
    )
    return result.stdout.strip()


@pytest.mark.asyncio
async def test_exact_pinned_pi_producer_reaches_gateway_identity_replay_decision_and_completion(tmp_path):
    root = Path(PINNED_ROOT).resolve()
    assert validate_producer_root(str(root)) == root
    assert FIRSTMATE_PRODUCER_PIN.commit == '2af0d17014cb2e244aa441bfe6df16c4f630475b'

    home = tmp_path / 'firstmate-runtime-home'
    (home / 'state' / 'captain-events').mkdir(parents=True, mode=0o755)
    (home / 'config').mkdir(mode=0o700)
    (home / 'state' / 'branch-outcomes.jsonl').write_text('', encoding='utf-8')

    # Activation is an independent atomic switch. Invalid producer state must
    # roll back a newly written flag before the repaired retry can succeed.
    failed_activation = _installer(
        root, home, 'activate', '--sessions-reloaded', check=False,
    )
    assert failed_activation.returncode != 0
    assert not (home / 'config' / 'captain-event-outbox').exists()
    (home / 'state' / 'captain-events').chmod(0o700)
    assert 'Activated Firstmate producer' in _installer(
        root, home, 'activate', '--sessions-reloaded',
    ).stdout
    assert 'Firstmate producer ready' in _installer(root, home, 'ready').stdout

    assert _run_real_producer(root, home) == (
        'producer-operations=12 failure-after-start=recovered crash-replay=deduplicated'
    )
    journal = home / 'state' / 'captain-events' / 'events.jsonl'
    rows = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 12
    assert [row['seq'] for row in rows] == list(range(1, 13))
    assert len({row['event_id'] for row in rows}) == 12
    assert rows[-1]['kind'] == 'worker.final'
    assert all(row['audience'] == 'captain' for row in rows)
    assert all('private reasoning' not in row['summary'] and '/private' not in row['summary'] for row in rows)

    firstmate = SemanticFirstmate(home, root)
    adapter = FirstmateActivityAdapter(firstmate, fm_home=str(home))
    await adapter.require_captain_producer_ready()

    owner_a = 'pinned-producer-owner-a'
    owner_b = 'pinned-producer-owner-b'
    first = await adapter.reconcile(owner_a)
    assert first['status'] == 'available'
    operations = [record for record in first['changed'] if record['kind'].startswith('worker.')]
    assert len(operations) == 12
    assert [record['summary'] for record in operations] == [
        f'Captain-visible semantic operation {number:02d}.' for number in range(1, 13)
    ]
    objective = next(record for record in first['changed'] if record['kind'] == 'objective.started')
    assert all(
        (record['objective_id'], record['run_id']) == (objective['objective_id'], objective['run_id'])
        for record in operations
    )
    assert list_messages(owner_a, 'captain')['messages'] == [], (
        'source-native activity must not invent an autonomous conversation turn'
    )

    # The same source facts project into separately owned rows. Source-native
    # causal ids remain stable, while canonical ids and replay ledgers do not cross tenants.
    second_tenant = await adapter.reconcile(owner_b)
    tenant_b_operations = [record for record in second_tenant['changed'] if record['kind'].startswith('worker.')]
    assert len(tenant_b_operations) == 12
    assert {record['id'] for record in operations}.isdisjoint({record['id'] for record in tenant_b_operations})
    assert list_activity('pinned-producer-unrelated')['records'] == []

    # A new adapter instance resumes the durable cursor and acknowledges the
    # already-committed pair without replaying a duplicate projection.
    restarted = FirstmateActivityAdapter(firstmate, fm_home=str(home))
    assert (await restarted.reconcile(owner_a))['changed'] == []
    source = next(
        item for item in source_diagnostics(owner_a)
        if item['stream'] == 'captain-events'
    )
    assert source['cursor'] == source['source_tail'] == source['accepted_count'] == 12
    receipt = json.loads((journal.parent / 'acks' / 'magistrate.json').read_text(encoding='utf-8'))
    assert receipt['through'] == 12
    assert receipt['event_id'] == rows[-1]['event_id']

    # Fleet facts own mutable lifecycle and exact decisions. Their stable rows
    # revise in place and replay after the prior delivery cursor.
    before_decision = list_activity(owner_a)['latest_cursor']
    firstmate.phase = 1
    decision_change = await restarted.reconcile(owner_a)
    decision = next(record for record in decision_change['changed'] if record['kind'] == 'decision.requested')
    awaiting = next(record for record in decision_change['changed'] if record['kind'] == 'objective.progress')
    assert decision['state'] == 'awaiting-user'
    assert decision['decision_key'] == 'soak-release-choice'
    assert awaiting['id'] == objective['id'] and awaiting['revision'] == 2
    decision_replay = list_activity(owner_a, after=before_decision)
    assert {record['id'] for record in decision_replay['records']} >= {decision['id'], objective['id']}

    before_completion = decision_replay['latest_cursor']
    firstmate.phase = 2
    completion_change = await restarted.reconcile(owner_a)
    completed = next(record for record in completion_change['changed'] if record['kind'] == 'objective.completed')
    resolved = next(record for record in completion_change['changed'] if record['kind'] == 'decision.resolved')
    assert completed['id'] == objective['id'] and completed['revision'] == 3
    assert resolved['id'] == decision['id'] and resolved['revision'] == 2
    completion_replay = list_activity(owner_a, after=before_completion)
    assert {record['id'] for record in completion_replay['records']} >= {completed['id'], resolved['id']}
    assert len({record['id'] for record in list_activity(owner_a)['records']}) == 14

    # Tenant B remains on its independently observed active revision.
    tenant_b_objective = next(
        record for record in list_activity(owner_b)['records']
        if record['kind'] == 'objective.started'
    )
    assert tenant_b_objective['revision'] == 1

    journal_before_deactivation = journal.read_bytes()
    assert 'Deactivated Firstmate producer' in _installer(root, home, 'deactivate').stdout
    assert journal.read_bytes() == journal_before_deactivation
    assert not (home / 'config' / 'captain-event-outbox').exists()
