"""The canonical conversation record replaces the terminal-derived transcript.

Every case here is a duplicate or leak class the old architecture produced by
treating a mutable Herdr snapshot as the chat database.
"""
from pathlib import Path
import sqlite3
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import conversation_store as store
from app import db
from app.herdr_client import HERDR_MAX_READ_LINES, classify_history_rows, parse_agent_history
from app.main import app
from conftest import TEST_HEADERS, TEST_SESSION_TOKEN

client = TestClient(app)

USER = 'default_user'
TARGET = 'captain'


@pytest.fixture(autouse=True)
def clean_conversation():
    store.reset_conversation(USER, TARGET)
    yield
    store.reset_conversation(USER, TARGET)


def rows(*entries):
    return [{'role': role, 'kind': kind, 'text': text} for role, kind, text in entries]


def visible(user_id=USER, target=TARGET):
    payload = store.list_messages(user_id, target)
    return [(item['role'], item['type'], item['text']) for item in payload['messages']]


def test_the_same_client_message_id_records_exactly_one_turn():
    first = store.record_prompt(USER, TARGET, 'u-1', 'redeploy the demo')
    second = store.record_prompt(USER, TARGET, 'u-1', 'redeploy the demo')

    assert first['created'] is True
    assert second['created'] is False
    assert second['turn_id'] == first['turn_id']
    assert visible() == [('user', 'conversation', 'redeploy the demo')]
    assert [item['client_message_id'] for item in store.list_messages(USER, TARGET)['messages']] == ['u-1']


def test_preview_schema_gains_attachment_metadata_and_millisecond_time(monkeypatch, tmp_path):
    preview_db = tmp_path / 'preview.db'
    with sqlite3.connect(preview_db) as conn:
        conn.execute('''CREATE TABLE conversation_messages (
            id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
            role TEXT NOT NULL, type TEXT NOT NULL, slot TEXT NOT NULL, text TEXT NOT NULL,
            visible_in_chat INTEGER NOT NULL, sequence_index INTEGER NOT NULL,
            revision INTEGER NOT NULL, source TEXT NOT NULL,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            UNIQUE(turn_id, slot)
        )''')
        conn.execute('''INSERT INTO conversation_messages
            VALUES ('cm_preview', 'ct_preview', 'cv_preview', 'user', 'conversation',
                    'prompt', 'preview row', 1, 0, 1, 'text', 1756000000, 1756000001)''')
    monkeypatch.setattr(db, 'DB_PATH', str(preview_db))

    db.init_db()

    with sqlite3.connect(preview_db) as conn:
        row = conn.execute(
            'SELECT attachments_json, created_at, updated_at FROM conversation_messages WHERE id = ?',
            ('cm_preview',),
        ).fetchone()
    assert row == ('[]', 1_756_000_000_000, 1_756_000_001_000)


def test_canonical_timestamps_keep_gateway_millisecond_precision(monkeypatch):
    monkeypatch.setattr(store.time, 'time', lambda: 1_756_000_000.456)

    turn = store.record_prompt(USER, TARGET, 'u-time', 'what time was this accepted')
    [message] = store.list_messages(USER, TARGET)['messages']

    assert turn['messages'][0]['created_at'] == 1_756_000_000_456
    assert message['created_at'] == 1_756_000_000_456


def test_schema_reinitialization_preserves_canonical_rows_across_restart():
    turn = store.record_prompt(USER, TARGET, 'u-restart', 'keep this after restart')
    store.record_primary_reply(USER, TARGET, turn['turn_id'], 'The persisted reply.')
    before = store.list_messages(USER, TARGET)['messages']

    # Gateway startup calls init_db() again against the same external SQLite
    # path. Its additive schema initialization must never reset the record.
    db.init_db()

    after = store.list_messages(USER, TARGET)['messages']
    assert [(row['id'], row['turn_id'], row['text'], row['revision']) for row in after] == [
        (row['id'], row['turn_id'], row['text'], row['revision']) for row in before
    ]


def test_an_edited_resubmission_replaces_its_own_text_and_keeps_one_turn():
    """An edit reuses the submission id, so it must correct the turn, not add one."""
    first = store.record_prompt(USER, TARGET, 'u-edit', 'redeploy the demo once the chat fix lands')
    second = store.record_prompt(USER, TARGET, 'u-edit', 'redeploy the demo')

    assert second['turn_id'] == first['turn_id']
    assert visible() == [('user', 'conversation', 'redeploy the demo')]
    # The shortened text is also the key the adapter matches against now.
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'redeploy the demo'),
        ('assistant', 'conversation', 'Deploying now.'),
    ))
    assert visible() == [
        ('user', 'conversation', 'redeploy the demo'),
        ('assistant', 'conversation', 'Deploying now.'),
    ]


def test_mutable_snapshot_output_revises_one_assistant_message():
    turn = store.record_prompt(USER, TARGET, 'u-2', 'run the tests')
    growth = ['The tests are running', 'The tests are running and 30 of them have passed',
              'The tests are running and 30 of them have passed; all 42 now pass.']
    for text in growth:
        store.ingest_terminal_rows(USER, TARGET, rows(
            ('user', 'conversation', 'run the tests'),
            ('assistant', 'conversation', text),
        ))

    messages = store.list_messages(USER, TARGET)['messages']
    replies = [item for item in messages if item['role'] == 'assistant']
    assert len(replies) == 1, replies
    assert replies[0]['text'] == growth[-1]
    assert replies[0]['revision'] == 3
    assert replies[0]['turn_id'] == turn['turn_id']


def test_sliding_reply_windows_merge_without_losing_prefix_or_duplicating_overlap():
    store.record_prompt(USER, TARGET, 'u-slide', 'stream the alphabet')
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'stream the alphabet'),
        ('assistant', 'conversation', 'A B C'),
    ))
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'stream the alphabet'),
        ('assistant', 'conversation', 'B C D'),
    ))

    assert visible() == [
        ('user', 'conversation', 'stream the alphabet'),
        ('assistant', 'conversation', 'A B C D'),
    ]


def test_a_scrolled_snapshot_cannot_shrink_a_recorded_reply():
    """Retained scrollback drops the head of a long reply on a later read."""
    store.record_prompt(USER, TARGET, 'u-3', 'summarize the deploy')
    full = 'The deploy finished at 09:12 and every check passed.'
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'summarize the deploy'), ('assistant', 'conversation', full)))
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'summarize the deploy'),
        ('assistant', 'conversation', 'every check passed.')))

    assert visible() == [
        ('user', 'conversation', 'summarize the deploy'),
        ('assistant', 'conversation', full),
    ]


def test_an_unsafe_disjoint_replacement_cannot_erase_the_captured_reply():
    prompt = 'keep this reply intact'
    store.record_prompt(USER, TARGET, 'u-unsafe-replacement', prompt)
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', prompt),
        ('assistant', 'conversation', 'The captured prefix must survive a bad re-read.'),
    ), response_complete=False)
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', prompt),
        ('assistant', 'conversation', 'Unrelated prose with no safe overlap.'),
    ), response_complete=False)

    [reply] = [item for item in store.list_messages(USER, TARGET)['messages'] if item['role'] == 'assistant']
    assert reply['text'] == 'The captured prefix must survive a bad re-read.'
    assert reply['revision'] == 1


def test_promptless_sliding_windows_keep_growing_after_the_prompt_leaves_a_long_snapshot():
    prompt = 'explain every retained line'
    tokens = [f'token-{index:04d}' for index in range(900)]
    store.record_prompt(USER, TARGET, 'u-long-window', prompt)
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', prompt),
        ('assistant', 'conversation', ' '.join(tokens[:300])),
    ), response_complete=False)

    # Every later read is a viewport-sized sliding window. The original prompt
    # is outside the newest 400 terminal rows, but each window has substantial
    # overlap with the reply already attributed to this exact turn.
    for start in (150, 300, 450, 600):
        store.ingest_terminal_rows(USER, TARGET, rows(
            ('assistant', 'conversation', ' '.join(tokens[start:start + 300])),
        ), response_complete=False)

    [reply] = [item for item in store.list_messages(USER, TARGET)['messages'] if item['role'] == 'assistant']
    assert reply['text'] == ' '.join(tokens)
    assert reply['revision'] == 5
    assert reply['turn_status'] == 'streaming'


def test_repeated_prose_blocks_in_one_turn_stay_one_primary_reply():
    store.record_prompt(USER, TARGET, 'u-4', 'do the work')
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'do the work'),
        ('assistant', 'conversation', 'Starting with the gateway.'),
        ('assistant', 'tool', 'Running 3 shell commands'),
        ('assistant', 'conversation', 'Done: the gateway is updated.'),
    ))

    replies = [item for item in visible() if item[0] == 'assistant' and item[1] == 'conversation']
    assert replies == [('assistant', 'conversation', 'Starting with the gateway.\n\nDone: the gateway is updated.')]


def test_prose_and_repeated_tools_remain_one_ordered_logical_assistant_reply():
    store.record_prompt(USER, TARGET, 'u-prose-tools', 'work in three stages')
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'work in three stages'),
        ('assistant', 'conversation', 'Stage one prose.'),
        ('assistant', 'tool', 'Read gateway/app/main.py'),
        ('assistant', 'conversation', 'Stage two prose.'),
        ('assistant', 'tool', 'Bash(pytest -q)'),
        ('assistant', 'conversation', 'Stage three prose.'),
    ), response_complete=True)

    messages = store.list_messages(USER, TARGET)['messages']
    assert [item['text'] for item in messages if item['type'] == 'conversation'] == [
        'work in three stages',
        'Stage one prose.\n\nStage two prose.\n\nStage three prose.',
    ]
    assert [item['text'] for item in messages if item['type'] == 'tool'] == ['Read', 'Bash']
    assert all('gateway/app' not in item['text'] and 'pytest' not in item['text'] for item in messages)


def test_terminal_reflow_is_idempotent_and_only_new_prose_is_appended():
    prompt = 'write two paragraphs'
    store.record_prompt(USER, TARGET, 'u-reflow', prompt)
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', prompt),
        ('assistant', 'conversation', 'Alpha beta gamma delta.\n\nSecond paragraph stays intact.'),
    ), response_complete=False)
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', prompt),
        ('assistant', 'conversation', 'Alpha beta\ngamma delta. Second paragraph stays intact.'),
    ), response_complete=False)
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', prompt),
        ('assistant', 'conversation', 'gamma delta.\nSecond paragraph stays intact. Final tail.'),
    ), response_complete=True)

    [reply] = [item for item in store.list_messages(USER, TARGET)['messages'] if item['role'] == 'assistant']
    assert ' '.join(reply['text'].split()) == 'Alpha beta gamma delta. Second paragraph stays intact. Final tail.'
    assert reply['text'].count('Alpha beta') == 1
    assert reply['text'].count('Second paragraph stays intact.') == 1
    assert reply['turn_status'] == 'answered'


def test_tool_events_are_hidden_bounded_labels_and_never_prose():
    store.record_prompt(USER, TARGET, 'u-5', 'check the deploy')
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'check the deploy'),
        ('assistant', 'tool', 'Running npm test --token hidden-secret'),
        ('assistant', 'conversation', 'The deploy is healthy.'),
    ))

    messages = store.list_messages(USER, TARGET)['messages']
    tools = [item for item in messages if item['type'] == 'tool']
    assert [item['text'] for item in tools] == ['Running…']
    assert all(item['visible_in_chat'] is False for item in tools)
    assert 'hidden-secret' not in str(messages)
    assert [item['text'] for item in messages if item['visible_in_chat']] == [
        'check the deploy', 'The deploy is healthy.',
    ]


def test_deployed_tool_only_poll_later_upserts_assistant_prose_exactly_once():
    """The deployed failure shape: tools were visible before any primary reply."""
    turn = store.record_prompt(USER, TARGET, 'u-deployed', 'reply exactly READY')
    tool_only = rows(
        ('user', 'conversation', 'reply exactly READY'),
        ('assistant', 'tool', 'Read gateway/app/herdr_client.py'),
        ('assistant', 'tool', 'Bash(git status --short)'),
    )
    store.ingest_terminal_rows(USER, TARGET, tool_only)
    assert not [
        item for item in store.list_messages(USER, TARGET)['messages']
        if item['role'] == 'assistant' and item['type'] == 'conversation'
    ]

    completed = tool_only + rows(
        ('assistant', 'conversation', 'READY'),
    )
    store.ingest_terminal_rows(USER, TARGET, completed)
    store.ingest_terminal_rows(USER, TARGET, completed)

    messages = store.list_messages(USER, TARGET)['messages']
    replies = [item for item in messages if item['role'] == 'assistant' and item['type'] == 'conversation']
    assert [(item['turn_id'], item['text'], item['revision']) for item in replies] == [
        (turn['turn_id'], 'READY', 1),
    ]
    assert [item['text'] for item in messages if item['type'] == 'tool'] == ['Read', 'Bash']


def test_harness_metadata_and_worker_audiences_never_enter_visible_chat():
    """The leak classes reported on the deployed demo, at their real source."""
    store.record_prompt(USER, TARGET, 'u-6', 'summarize the deploy')
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'summarize the deploy'),
        ('assistant', 'conversation', 'The deploy is healthy.'),
        ('assistant', 'conversation', '{"jsonrpc":"2.0","result":{"ok":true}}'),
        ('assistant', 'conversation', 'pane_id=w1:p9 tab_id=secret'),
        ('assistant', 'conversation', '/calm animation status'),
        ('assistant', 'conversation', '$ cat /tmp/raw-pane-output'),
        ('assistant', 'conversation', 'model: claude-opus-5'),
        ('user', 'conversation', 'FIRSTMATE_OP: v1 launch-brief: you are a crewmate'),
        ('assistant', 'conversation', 'Scout report for Firstmate only.'),
    ))

    rendered = str(visible())
    assert visible()[0] == ('user', 'conversation', 'summarize the deploy')
    for leak in ('jsonrpc', 'pane_id', 'tab_id', 'calm', 'raw-pane', 'claude-opus-5',
                 'FIRSTMATE_OP', 'Scout report'):
        assert leak not in rendered, leak


def test_terminal_rows_with_no_submitted_turn_are_not_recorded():
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'a prompt this gateway never received'),
        ('assistant', 'conversation', 'A reply nobody in this app asked for.'),
    ))
    assert visible() == []

    store.record_prompt(USER, TARGET, 'u-7', 'a real captain turn')
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'a prompt this gateway never received'),
        ('assistant', 'conversation', 'A reply nobody in this app asked for.'),
        ('user', 'conversation', 'a real captain turn'),
        ('assistant', 'conversation', 'The real reply.'),
    ))
    assert visible() == [
        ('user', 'conversation', 'a real captain turn'),
        ('assistant', 'conversation', 'The real reply.'),
    ]


def test_two_identical_prompts_keep_their_own_replies_in_order():
    store.record_prompt(USER, TARGET, 'u-8a', 'same wording')
    store.record_prompt(USER, TARGET, 'u-8b', 'same wording')
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'same wording'), ('assistant', 'conversation', 'First reply.'),
        ('user', 'conversation', 'same wording'), ('assistant', 'conversation', 'Second reply.'),
    ))

    assert visible() == [
        ('user', 'conversation', 'same wording'),
        ('assistant', 'conversation', 'First reply.'),
        ('user', 'conversation', 'same wording'),
        ('assistant', 'conversation', 'Second reply.'),
    ]


def test_a_scrolled_window_attributes_a_repeated_prompt_to_the_newest_turn():
    store.record_prompt(USER, TARGET, 'u-9a', 'same wording')
    store.record_prompt(USER, TARGET, 'u-9b', 'same wording')
    # The retained snapshot has scrolled past the first turn entirely.
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'same wording'), ('assistant', 'conversation', 'Reply to the newest turn.'),
    ))

    messages = store.list_messages(USER, TARGET)['messages']
    replies = [item for item in messages if item['role'] == 'assistant']
    assert len(replies) == 1
    assert replies[0]['turn_id'] == messages[-2]['turn_id']


def test_a_prompt_carrying_an_attachment_manifest_still_matches_its_reply():
    """The provider receives a manifest the captain never typed."""
    store.record_prompt(
        USER, TARGET, 'u-10', 'Review the attached manifest',
        submitted_text='Review the attached manifest\n\nAttached files: package.json (application/json, 123 bytes)',
    )
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'Review the attached manifest Attached files: package.json (application/json, 123 bytes)'),
        ('assistant', 'conversation', 'The manifest looks correct.'),
    ))

    assert visible() == [
        ('user', 'conversation', 'Review the attached manifest'),
        ('assistant', 'conversation', 'The manifest looks correct.'),
    ]


def test_a_synchronous_provider_reply_is_recorded_then_revised_not_duplicated():
    turn = store.record_prompt(USER, TARGET, 'u-11', 'status please')
    store.record_primary_reply(USER, TARGET, turn['turn_id'], 'Working on it now.')
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'status please'),
        ('assistant', 'conversation', 'Working on it now. All checks pass.'),
    ))

    assert visible() == [
        ('user', 'conversation', 'status please'),
        ('assistant', 'conversation', 'Working on it now. All checks pass.'),
    ]


def test_internal_events_are_recorded_but_never_delivered_to_chat():
    store.record_prompt(USER, TARGET, 'u-12', 'keep working')
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'keep working'),
        ('assistant', 'control', 'FIRSTMATE_OP: WAKE_ACK'),
        ('assistant', 'conversation', 'Still working.'),
    ))

    assert visible() == [('user', 'conversation', 'keep working'), ('assistant', 'conversation', 'Still working.')]
    audited = store.list_messages(USER, TARGET, include_internal=True)['messages']
    assert [item['type'] for item in audited if item['type'] == 'internal'] == ['internal']


def test_reset_discards_a_poisoned_canonical_record():
    store.record_prompt(USER, TARGET, 'u-13', 'a turn')
    assert visible()
    store.reset_conversation(USER, TARGET)
    assert visible() == []


def test_streaming_status_survives_reload_and_completion_is_not_inferred_from_first_prose():
    prompt = 'give a growing reply'
    turn = store.record_prompt(USER, TARGET, 'u-stream-state', prompt)
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', prompt),
        ('assistant', 'conversation', 'The first partial response is deliberately long enough to remain attributed.'),
    ), response_complete=False)

    before_restart = store.list_messages(USER, TARGET)['messages']
    [partial] = [item for item in before_restart if item['role'] == 'assistant']
    assert partial['turn_id'] == turn['turn_id']
    assert partial['turn_status'] == 'streaming'

    db.init_db()
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('assistant', 'conversation', 'The first partial response is deliberately long enough to remain attributed. The durable tail arrived.'),
    ), response_complete=False)
    evolving = store.list_messages(USER, TARGET)['messages']
    [same_reply] = [item for item in evolving if item['role'] == 'assistant']
    assert same_reply['id'] == partial['id']
    assert same_reply['revision'] == partial['revision'] + 1
    assert same_reply['turn_status'] == 'streaming'

    # Seeing the harness become idle is a separate observation from seeing
    # prose. Completion updates the turn even when the final text is unchanged.
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('assistant', 'conversation', same_reply['text']),
    ), response_complete=True)
    [complete] = [item for item in store.list_messages(USER, TARGET)['messages'] if item['role'] == 'assistant']
    assert complete['id'] == partial['id']
    assert complete['text'] == 'The first partial response is deliberately long enough to remain attributed. The durable tail arrived.'
    assert complete['turn_status'] == 'answered'


def test_duplicate_poll_and_socket_snapshots_converge_without_extra_revision():
    prompt = 'one logical response'
    store.record_prompt(USER, TARGET, 'u-converge', prompt)
    snapshot = rows(
        ('user', 'conversation', prompt),
        ('assistant', 'conversation', 'Stable response identity.'),
    )
    first = store.ingest_terminal_rows(USER, TARGET, snapshot, response_complete=False)
    duplicate_socket = store.ingest_terminal_rows(USER, TARGET, snapshot, response_complete=False)
    duplicate_poll = store.ingest_terminal_rows(USER, TARGET, snapshot, response_complete=False)

    assert len(first) == 1
    assert duplicate_socket == []
    assert duplicate_poll == []
    [reply] = [item for item in store.list_messages(USER, TARGET)['messages'] if item['role'] == 'assistant']
    assert reply['revision'] == 1
    assert reply['turn_status'] == 'streaming'


# --- HTTP contract -----------------------------------------------------------

def test_prompt_endpoint_is_idempotent_on_message_id(monkeypatch):
    prompt = AsyncMock(return_value={'status': 'submitted', 'target': 'w1:p1', 'response': None})
    monkeypatch.setattr('app.main.herdr_client.prompt_agent', prompt)
    body = {'text': 'redeploy the demo', 'message_id': 'u-http-1'}

    first = client.post('/api/v1/captain/prompt', headers=TEST_HEADERS, json=body).json()
    second = client.post('/api/v1/captain/prompt', headers=TEST_HEADERS, json=body).json()

    assert first['conversation']['turn_id'] == second['conversation']['turn_id']
    assert [item['text'] for item in second['conversation']['messages']] == ['redeploy the demo']
    assert first['message_id'] == 'u-http-1'
    assert visible() == [('user', 'conversation', 'redeploy the demo')]


def test_prompt_endpoint_records_a_synchronous_reply_canonically(monkeypatch):
    prompt = AsyncMock(return_value={'status': 'submitted', 'target': 'w1:p1', 'response': 'Understood.'})
    monkeypatch.setattr('app.main.herdr_client.prompt_agent', prompt)

    response = client.post('/api/v1/captain/prompt', headers=TEST_HEADERS, json={
        'text': 'status please', 'message_id': 'u-http-2'}).json()

    assert [(item['role'], item['text']) for item in response['conversation']['messages']] == [
        ('user', 'status please'), ('assistant', 'Understood.'),
    ]


def test_prompt_endpoint_marks_a_rejected_turn_failed(monkeypatch):
    prompt = AsyncMock(return_value={'status': 'error', 'target': 'w1:p1', 'error': 'Herdr is unavailable.'})
    monkeypatch.setattr('app.main.herdr_client.prompt_agent', prompt)

    client.post('/api/v1/captain/prompt', headers=TEST_HEADERS, json={
        'text': 'will not land', 'message_id': 'u-http-3'})

    statuses = {item['turn_status'] for item in store.list_messages(USER, TARGET)['messages']}
    assert statuses == {'failed'}


def test_conversation_endpoint_ingests_the_snapshot_and_returns_canonical_messages(monkeypatch):
    store.record_prompt(USER, TARGET, 'u-http-4', 'summarize the deploy')
    typed = AsyncMock(return_value={'target': 'w1:p1', 'rows': rows(
        ('user', 'conversation', 'summarize the deploy'),
        ('assistant', 'tool', 'Running npm test --token hidden-secret'),
        ('assistant', 'conversation', 'The deploy is healthy.'),
        ('user', 'conversation', 'FIRSTMATE_OP: launch-brief for a worker'),
        ('assistant', 'conversation', 'Worker-only report.'),
    )})
    monkeypatch.setattr('app.main.herdr_client.read_typed_rows', typed)

    payload = client.get(f'/api/v1/conversations/{TARGET}/messages', headers=TEST_HEADERS).json()

    typed.assert_awaited_once_with(TARGET, lines=HERDR_MAX_READ_LINES)
    assert payload['schema_version'] == store.CONVERSATION_SCHEMA
    assert [(item['role'], item['type'], item['text']) for item in payload['messages']] == [
        ('user', 'conversation', 'summarize the deploy'),
        ('assistant', 'tool', 'Running…'),
        ('assistant', 'conversation', 'The deploy is healthy.'),
    ]
    assert 'hidden-secret' not in str(payload)
    assert 'Worker-only report' not in str(payload)


def test_conversation_endpoint_keeps_prose_streaming_until_herdr_is_observed_idle(monkeypatch):
    prompt = 'write a complete report'
    store.record_prompt(USER, TARGET, 'u-http-stream', prompt)
    status = {'value': 'working'}

    async def typed(target, lines=None):
        return {
            'target': target,
            'agent_status': status['value'],
            'rows': rows(
                ('user', 'conversation', prompt),
                ('assistant', 'conversation', 'A visible response that may still grow.'),
            ),
        }

    monkeypatch.setattr('app.main.herdr_client.read_typed_rows', typed)
    streaming = client.get(f'/api/v1/conversations/{TARGET}/messages', headers=TEST_HEADERS).json()
    [partial] = [item for item in streaming['messages'] if item['role'] == 'assistant']
    assert partial['turn_status'] == 'streaming'

    status['value'] = 'idle'
    completed = client.get(f'/api/v1/conversations/{TARGET}/messages', headers=TEST_HEADERS).json()
    [final] = [item for item in completed['messages'] if item['role'] == 'assistant']
    assert final['id'] == partial['id']
    assert final['revision'] == partial['revision']
    assert final['text'] == partial['text']
    assert final['turn_status'] == 'answered'


def test_conversation_endpoint_requires_authentication():
    assert client.get(f'/api/v1/conversations/{TARGET}/messages').status_code == 401


def test_conversation_endpoint_reports_an_unreadable_snapshot_without_hiding_the_record(monkeypatch):
    store.record_prompt(USER, TARGET, 'u-http-5', 'a recorded turn')
    failing = AsyncMock(side_effect=RuntimeError('herdr socket closed'))
    monkeypatch.setattr('app.main.herdr_client.read_typed_rows', failing)

    payload = client.get(f'/api/v1/conversations/{TARGET}/messages', headers=TEST_HEADERS).json()

    assert 'herdr socket closed' in payload['ingest_error']
    assert [item['text'] for item in payload['messages']] == ['a recorded turn']


def test_a_cancelled_turn_never_gains_a_later_reply():
    turn = store.record_prompt(USER, TARGET, 'u-cancel', 'stop this one')
    store.set_turn_status(USER, TARGET, 'u-cancel', 'cancelled')
    # The provider's eventual error result must not overwrite the stop outcome.
    store.set_turn_status(USER, TARGET, 'u-cancel', 'failed')
    # Both reply paths can race a stop: a terminal poll may see later output,
    # and the still-running prompt request may return a synchronous response
    # after the client aborted its HTTP request. The cancelled turn is frozen
    # against both.
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'stop this one'),
        ('assistant', 'conversation', 'A terminal reply produced after the captain stopped it.'),
    ))
    changed = store.record_primary_reply(
        USER, TARGET, turn['turn_id'], 'A synchronous reply returned after the stop.'
    )

    assert changed == []
    assert visible() == [('user', 'conversation', 'stop this one')]
    first_reload = store.list_messages(USER, TARGET)['messages']
    second_reload = store.list_messages(USER, TARGET)['messages']
    assert {item['turn_status'] for item in first_reload} == {'cancelled'}
    assert second_reload == first_reload
    assert not [item for item in first_reload if item['type'] == 'status']


def test_events_stream_delivers_canonical_messages_once_per_revision(monkeypatch):
    store.record_prompt(USER, TARGET, 'u-ws-1', 'run the tests')
    reply = {'text': 'The tests are running'}

    async def typed(target, lines=None):
        return {'target': target, 'rows': rows(
            ('user', 'conversation', 'run the tests'),
            ('assistant', 'conversation', reply['text']),
        )}

    monkeypatch.setattr('app.main.herdr_client.read_typed_rows', typed)
    with client.websocket_connect('/api/v1/events') as websocket:
        websocket.send_json({'type': 'auth', 'token': TEST_SESSION_TOKEN, 'target': TARGET})
        assert websocket.receive_json() == {'type': 'connected', 'target': TARGET}
        first = websocket.receive_json()
        assert first['type'] == 'conversation_messages'
        assert [item['text'] for item in first['messages']] == ['run the tests', 'The tests are running']
        reply['text'] = 'The tests are running and all 42 pass.'
        update = websocket.receive_json()
        # Only the revised reply is redelivered, carrying the id already rendered.
        assert [item['text'] for item in update['messages']] == ['The tests are running and all 42 pass.']
        assert update['messages'][0]['id'] == first['messages'][1]['id']
        assert update['messages'][0]['revision'] == 2


def test_events_stream_delivers_same_revision_completion_status(monkeypatch):
    prompt = 'finish after streaming'
    store.record_prompt(USER, TARGET, 'u-ws-status', prompt)
    status = {'value': 'working'}

    async def typed(target, lines=None):
        return {
            'target': target,
            'agent_status': status['value'],
            'rows': rows(
                ('user', 'conversation', prompt),
                ('assistant', 'conversation', 'Final prose already rendered.'),
            ),
        }

    monkeypatch.setattr('app.main.herdr_client.read_typed_rows', typed)
    with client.websocket_connect('/api/v1/events') as websocket:
        websocket.send_json({'type': 'auth', 'token': TEST_SESSION_TOKEN, 'target': TARGET})
        websocket.receive_json()
        first = websocket.receive_json()
        [streaming_reply] = [item for item in first['messages'] if item['role'] == 'assistant']
        assert streaming_reply['turn_status'] == 'streaming'

        status['value'] = 'idle'
        completed = websocket.receive_json()
        [final_reply] = [item for item in completed['messages'] if item['role'] == 'assistant']
        assert final_reply['id'] == streaming_reply['id']
        assert final_reply['revision'] == streaming_reply['revision']
        assert final_reply['turn_status'] == 'answered'


def test_voice_moves_record_the_shared_captain_turn(monkeypatch):
    async def fake_handle(request, principal_id):
        return {'status': 'completed', 'move_id': 'vm_1', 'utterance': request.utterance,
                'response': 'Fleet is quiet.', 'target': 'w1:p1'}

    monkeypatch.setattr('app.main.voice_move_service.handle', fake_handle)
    payload = client.post('/api/v1/voice/moves', headers=TEST_HEADERS, json={
        'utterance': 'what is the fleet doing', 'idempotency_key': 'voice-key-1',
        'client_message_id': 'voice-u-1', 'execute': True}).json()

    assert [(item['role'], item['text']) for item in payload['conversation']['messages']] == [
        ('user', 'what is the fleet doing'), ('assistant', 'Fleet is quiet.'),
    ]
    assert visible() == [
        ('user', 'conversation', 'what is the fleet doing'),
        ('assistant', 'conversation', 'Fleet is quiet.'),
    ]


# --- Real captured Herdr snapshots ---------------------------------------------
# tests/fixtures/live-*.ansi are live
# `HerdrClient.read_agent_output(..., 'ansi')` captures taken read-only from
# running panes on 2026-08-31. They carry the exact shapes a hand-written history
# array does not: Pi boxing a tool-envelope body like a user turn, Claude's
# prompt scrolling off its alternate-screen viewport, unmarked tool summaries,
# mid-frame status overlays, and real hard wrapping. The small
# production-claude-update-leak fixture preserves the exact offending row and
# surrounding prose recovered from the production canonical record/session
# after that viewport had already scrolled away. Recapture live fixtures with
# the command in docs/chat-evidence-package.md rather than editing them by hand.
FIXTURES = Path(__file__).parent / 'fixtures'


def typed_rows(fixture: str):
    return classify_history_rows(parse_agent_history((FIXTURES / fixture).read_text()))


CURRENT_PI_FOOTER = typed_rows('production-pi-footer-no-ch-current.ansi')[0]
OLD_PI_FOOTER = {
    'role': 'assistant', 'kind': 'control',
    'text': '↑130k ↓14k R4.8M CH99.1% $3.461 (sub) 31.7%/272k (auto) (openai-codex) gpt-5.6-sol • medium',
}


@pytest.mark.parametrize(('activity', 'expected_reply'), [
    ([
        ('assistant', 'conversation', 'Reply before footer.'),
        ('assistant', 'control', CURRENT_PI_FOOTER['text']),
    ], 'Reply before footer.'),
    ([
        ('assistant', 'control', CURRENT_PI_FOOTER['text']),
        ('assistant', 'conversation', 'Reply after footer.'),
    ], 'Reply after footer.'),
    ([
        ('assistant', 'conversation', 'Opening prose.'),
        ('assistant', 'tool', 'Running 2 commands'),
        ('assistant', 'conversation', 'Closing prose.'),
        ('assistant', 'control', CURRENT_PI_FOOTER['text']),
    ], 'Opening prose.\n\nClosing prose.'),
])
def test_pi_footer_orderings_preserve_prose_once_and_never_render_metadata(activity, expected_reply):
    prompt = 'exercise footer ordering'
    store.record_prompt(USER, TARGET, 'u-footer-order', prompt)
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', prompt), *activity,
    ), response_complete=True)

    messages = store.list_messages(USER, TARGET)['messages']
    replies = [item for item in messages if item['role'] == 'assistant' and item['type'] == 'conversation']
    assert [item['text'] for item in replies] == [expected_reply]
    assert not any('↑' in item['text'] or 'gpt-5.6' in item['text'] for item in messages)


def test_streaming_footer_updates_keep_one_reply_identity_and_revision_sequence():
    prompt = 'stream around changing footer counters'
    turn = store.record_prompt(USER, TARGET, 'u-footer-stream', prompt)
    for reply, footer in (
        ('A legitimate partial response.', CURRENT_PI_FOOTER),
        ('A legitimate partial response. Final sentence.', OLD_PI_FOOTER),
    ):
        store.ingest_terminal_rows(USER, TARGET, rows(
            ('user', 'conversation', prompt),
            ('assistant', 'conversation', reply),
            ('assistant', 'control', footer['text']),
        ), response_complete=False)

    [reply] = [item for item in store.list_messages(USER, TARGET)['messages'] if item['role'] == 'assistant']
    assert reply['turn_id'] == turn['turn_id']
    assert reply['text'] == 'A legitimate partial response. Final sentence.'
    assert reply['revision'] == 2
    assert reply['turn_status'] == 'streaming'


def test_structural_footer_cleanup_removes_only_poison_and_reingests_real_prose():
    prompt = 'recover this turn'
    turn = store.record_prompt(USER, TARGET, 'u-footer-repair', prompt)
    store.record_primary_reply(
        USER, TARGET, turn['turn_id'], 'legacy placeholder', source='terminal',
    )
    # Model a row persisted by the pre-fix writer. The current synchronous and
    # terminal write boundaries both reject this footer before insertion.
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            "UPDATE conversation_messages SET text = ? WHERE turn_id = ? AND slot = 'primary'",
            (CURRENT_PI_FOOTER['text'], turn['turn_id']),
        )

    # Ingestion first removes the metadata-only legacy primary, then uses the
    # corrected typed snapshot through the normal prompt/segment path.
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', prompt),
        ('assistant', 'control', CURRENT_PI_FOOTER['text']),
        ('assistant', 'conversation', 'The recovered conversational response.'),
    ), response_complete=True)
    assert visible() == [
        ('user', 'conversation', prompt),
        ('assistant', 'conversation', 'The recovered conversational response.'),
    ]


def test_structural_footer_cleanup_does_not_delete_legitimate_model_prose():
    turn = store.record_prompt(USER, TARGET, 'u-footer-prose', 'discuss the model')
    prose = 'GPT-5.6 used automatic mode; the request cost was $3.002.'
    store.record_primary_reply(USER, TARGET, turn['turn_id'], prose, source='terminal')

    assert visible() == [
        ('user', 'conversation', 'discuss the model'),
        ('assistant', 'conversation', prose),
    ]


def test_a_real_pi_pane_snapshot_adds_nothing_to_a_conversation_it_does_not_belong_to():
    """The live leak class: Pi boxes a file excerpt exactly like a user turn."""
    rows = typed_rows('live-pi-pane.ansi')
    assert [row['role'] for row in rows if row['kind'] == 'conversation'] == ['user', 'user'], rows
    store.record_prompt(USER, TARGET, 'u-live-1', 'a turn this app actually submitted')

    store.ingest_terminal_rows(USER, TARGET, rows)

    assert visible() == [('user', 'conversation', 'a turn this app actually submitted')]


def test_a_real_claude_reply_with_no_submitted_prompt_never_becomes_chat():
    rows = typed_rows('live-claude-captain-pane.ansi')
    reply = next(row['text'] for row in rows if row['kind'] == 'conversation')
    assert 'three agents now working in parallel' in reply
    store.record_prompt(USER, TARGET, 'u-live-2', 'an unrelated submitted turn')

    store.ingest_terminal_rows(USER, TARGET, rows)

    # The pane is on an alternate screen, so the prompt that produced this reply
    # has scrolled out of the snapshot. Attribution fails closed: the reply is
    # omitted rather than pinned to whichever turn happens to be open.
    assert visible() == [('user', 'conversation', 'an unrelated submitted turn')]


def test_a_real_reply_growing_between_polls_stays_one_message():
    """The real reply text, re-read at three points while it renders."""
    reply = next(row['text'] for row in typed_rows('live-claude-captain-pane.ansi') if row['kind'] == 'conversation')
    prompt = 'status on the agents'
    store.record_prompt(USER, TARGET, 'u-live-3', prompt)
    for cut in (len(reply) // 3, 2 * len(reply) // 3, len(reply)):
        store.ingest_terminal_rows(USER, TARGET, rows(
            ('user', 'conversation', prompt),
            ('assistant', 'tool', 'Ran 2 shell commands'),
            ('assistant', 'conversation', reply[:cut]),
        ))

    messages = store.list_messages(USER, TARGET)['messages']
    replies = [item for item in messages if item['role'] == 'assistant' and item['type'] == 'conversation']
    assert len(replies) == 1, [item['text'][:40] for item in replies]
    assert replies[0]['text'] == reply
    assert replies[0]['revision'] == 3
    assert [item['text'] for item in messages if item['type'] == 'tool'] == ['Ran']


def test_live_long_reply_keeps_upserting_after_its_prompt_scrolls_off_viewport():
    """Five read-only captures from the production Claude captain pane.

    The prompt is present through snapshot 3, then Claude's alternate screen
    pushes it away while the same assistant blocks continue growing in snapshots
    4 and 5. The adapter must use observed prose continuity, not guess that any
    unmatched output belongs to the latest open turn.
    """
    fixtures = [f'live-claude-growing-0{index}-{suffix}.ansi' for index, suffix in (
        (1, 'opening'), (2, 'tool'), (3, 'before-scroll'),
        (4, 'after-scroll'), (5, 'complete'),
    )]
    first_rows = typed_rows(fixtures[0])
    prompt = next(row['text'] for row in first_rows if row['role'] == 'user')
    turn = store.record_prompt(USER, TARGET, 'u-live-growing', prompt)

    for fixture in fixtures:
        store.ingest_terminal_rows(USER, TARGET, typed_rows(fixture))

    final_rows = typed_rows(fixtures[-1])
    final_reply = store.join_primary_text([
        row['text'] for row in final_rows
        if row['role'] == 'assistant' and row['kind'] == 'conversation'
    ])
    messages = store.list_messages(USER, TARGET)['messages']
    replies = [item for item in messages if item['role'] == 'assistant' and item['type'] == 'conversation']
    assert [(item['turn_id'], item['text'], item['revision']) for item in replies] == [
        (turn['turn_id'], final_reply, 5),
    ]
    assert [item['text'] for item in messages if item['type'] == 'tool'] == ['Read']
    assert 'Read 1 file' not in replies[0]['text']


def test_promptless_prose_without_unique_continuity_still_fails_closed():
    first = store.record_prompt(USER, TARGET, 'u-anchor-1', 'first prompt')
    second = store.record_prompt(USER, TARGET, 'u-anchor-2', 'second prompt')
    shared = 'A deliberately substantial opening shared by two different assistant turns.'
    store.record_primary_reply(USER, TARGET, first['turn_id'], shared)
    store.record_primary_reply(USER, TARGET, second['turn_id'], shared)

    store.ingest_terminal_rows(USER, TARGET, rows(
        ('assistant', 'conversation', shared + ' This continuation has no visible prompt.'),
    ))
    replies = [item for item in store.list_messages(USER, TARGET)['messages'] if item['role'] == 'assistant']
    assert [(item['turn_id'], item['text'], item['revision']) for item in replies] == [
        (first['turn_id'], shared, 1), (second['turn_id'], shared, 1),
    ]


def test_real_pi_boundary_fixture_produces_one_canonical_row_per_role():
    """The retained ANSI capture is the production cross-role defect shape.

    The damaged capture retains the next Pi prompt's foreground submission
    marker but loses its background fill. That marker is structural evidence;
    the parser must not fold the prompt into the preceding assistant prose.
    """
    parsed = typed_rows('production-pi-cross-role-boundary-dropped-box.ansi')
    assert [(row['role'], row['kind']) for row in parsed if row['text'] in {
        'Still testing. Just want to check chat persistence.', 'Hello'
    }] == [('user', 'conversation'), ('user', 'conversation')]
    first = store.record_prompt(USER, TARGET, 'u-real-boundary-1', 'Still testing. Just want to check chat persistence.')
    second = store.record_prompt(USER, TARGET, 'u-real-boundary-2', 'Hello')

    # This is the same parse -> classify -> store path used by both the polling
    # endpoint and the socket loop. Replaying it is the poll/socket convergence
    # case and must not mint a duplicate user row or revision.
    store.ingest_terminal_rows(USER, TARGET, parsed, response_complete=True)
    before_reload = store.list_messages(USER, TARGET)['messages']
    store.ingest_terminal_rows(USER, TARGET, parsed, response_complete=True)
    after_reload = store.list_messages(USER, TARGET)['messages']

    conversation = [(row['role'], row['type'], row['text']) for row in after_reload if row['type'] == 'conversation']
    assert conversation == [
        ('user', 'conversation', 'Still testing. Just want to check chat persistence.'),
        ('assistant', 'conversation', 'Understood, captain. Chat persistence check received.'),
        ('user', 'conversation', 'Hello'),
    ]
    assert 'Hello' not in conversation[1][2]
    assert len([row for row in after_reload if row['role'] == 'user']) == 2
    assert after_reload == before_reload
    assert first['turn_id'] != second['turn_id']


def test_real_pi_boundary_is_clean_in_the_canonical_http_payload(monkeypatch):
    parsed = typed_rows('production-pi-cross-role-boundary.ansi')
    store.record_prompt(USER, TARGET, 'u-http-real-1', 'Still testing. Just want to check chat persistence.')
    store.record_prompt(USER, TARGET, 'u-http-real-2', 'Hello')

    async def captured_snapshot(target, lines=None):
        return {'target': target, 'agent_status': 'idle', 'rows': parsed}

    monkeypatch.setattr('app.main.herdr_client.read_typed_rows', captured_snapshot)
    payload = client.get(f'/api/v1/conversations/{TARGET}/messages', headers=TEST_HEADERS).json()
    visible_payload = [item for item in payload['messages'] if item['type'] == 'conversation']
    assert [(item['role'], item['text']) for item in visible_payload] == [
        ('user', 'Still testing. Just want to check chat persistence.'),
        ('assistant', 'Understood, captain. Chat persistence check received.'),
        ('user', 'Hello'),
        ('assistant', 'Hello, captain.'),
    ]
    assert len([item for item in visible_payload if item['role'] == 'user']) == 2
    assert not any(item['type'] in {'internal', 'status'} for item in payload['messages'])
    assert not any('FIRSTMATE_OP' in item['text'] or 'pane_id' in item['text'] for item in payload['messages'])


def test_real_pi_complete_boundary_retains_the_later_assistant_reply():
    parsed = typed_rows('production-pi-cross-role-boundary.ansi')
    store.record_prompt(USER, TARGET, 'u-real-complete-1', 'Still testing. Just want to check chat persistence.')
    store.record_prompt(USER, TARGET, 'u-real-complete-2', 'Hello')
    store.ingest_terminal_rows(USER, TARGET, parsed, response_complete=True)
    assert [(row['role'], row['type'], row['text']) for row in store.list_messages(USER, TARGET)['messages'] if row['type'] == 'conversation'] == [
        ('user', 'conversation', 'Still testing. Just want to check chat persistence.'),
        ('assistant', 'conversation', 'Understood, captain. Chat persistence check received.'),
        ('user', 'conversation', 'Hello'),
        ('assistant', 'conversation', 'Hello, captain.'),
    ]


def test_reply_must_not_cross_known_user_boundary_without_structural_prompt_evidence():
    """The store-side defense does not search reply text for the later prompt."""
    first = store.record_prompt(USER, TARGET, 'u-boundary-store-1', 'first turn')
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'first turn'),
        ('assistant', 'conversation', 'The first reply is complete.'),
    ))
    store.record_prompt(USER, TARGET, 'u-boundary-store-2', 'Hello')

    # Deliberately model a degraded terminal frame with the later user row
    # missing entirely. Text overlap with "Hello" must not authorize an
    # extension of turn one; only a separately observed structural prompt does.
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'first turn'),
        ('assistant', 'conversation', 'The first reply is complete. Hello'),
    ))
    replies = [row for row in store.list_messages(USER, TARGET)['messages'] if row['role'] == 'assistant']
    assert [(row['turn_id'], row['text'], row['revision']) for row in replies] == [
        (first['turn_id'], 'The first reply is complete.', 1),
    ]


def test_structural_boundary_repairs_a_prior_cross_role_revision_without_text_subtraction():
    first = store.record_prompt(USER, TARGET, 'u-repair-1', 'Still testing')
    # Reproduce the persisted production corruption before the next prompt was
    # known to the store.
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'Still testing'),
        ('assistant', 'conversation', 'Understood, captain. Hello'),
    ))
    store.record_prompt(USER, TARGET, 'u-repair-2', 'Hello')
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'Still testing'),
        ('assistant', 'conversation', 'Understood, captain.'),
        ('user', 'conversation', 'Hello'),
        ('assistant', 'conversation', 'Hello, captain.'),
    ), response_complete=True)
    replies = [row for row in store.list_messages(USER, TARGET)['messages'] if row['role'] == 'assistant']
    assert [(row['turn_id'], row['text']) for row in replies] == [
        (first['turn_id'], 'Understood, captain.'),
        (replies[1]['turn_id'], 'Hello, captain.'),
    ]


def test_incomplete_structural_boundary_preserves_stored_reply_prefix():
    first = store.record_prompt(USER, TARGET, 'u-prefix-1', 'first turn')
    store.record_primary_reply(USER, TARGET, first['turn_id'], 'The complete stored reply prefix survives.')
    store.record_prompt(USER, TARGET, 'u-prefix-2', 'next turn')

    # The later user row is structural ordering evidence, not proof that this
    # partial terminal view contains the whole earlier reply. Without an
    # explicit completion observation, a shorter overlap cannot truncate it.
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'first turn'),
        ('assistant', 'conversation', 'The complete stored reply'),
        ('user', 'conversation', 'next turn'),
    ), response_complete=False)

    replies = [row for row in store.list_messages(USER, TARGET)['messages'] if row['role'] == 'assistant']
    assert [(row['turn_id'], row['text'], row['revision']) for row in replies] == [
        (first['turn_id'], 'The complete stored reply prefix survives.', 1),
    ]


def test_structural_later_boundary_does_not_trim_a_legitimate_quoted_prompt():
    store.record_prompt(USER, TARGET, 'u-quoted-1', 'quote this')
    store.record_prompt(USER, TARGET, 'u-quoted-2', 'Hello')
    store.ingest_terminal_rows(USER, TARGET, rows(
        ('user', 'conversation', 'quote this'),
        ('assistant', 'conversation', 'The assistant may quote Hello here.'),
        ('user', 'conversation', 'Hello'),
        ('assistant', 'conversation', 'Hello, captain.'),
    ), response_complete=True)
    replies = [row['text'] for row in store.list_messages(USER, TARGET)['messages'] if row['role'] == 'assistant']
    assert replies == ['The assistant may quote Hello here.', 'Hello, captain.']


def test_real_rapid_pi_capture_keeps_two_submissions_distinct_across_reload():
    parsed = typed_rows('captured-pi-rapid-two-user-turns.ansi')
    prompts = [row['text'] for row in parsed if row['role'] == 'user' and row['kind'] == 'conversation']
    assert prompts == [
        'Reply with exactly the two words: Alpha ready.',
        'Reply with exactly the two words: Bravo ready.',
    ]
    for index, prompt in enumerate(prompts):
        store.record_prompt(USER, TARGET, f'u-rapid-{index}', prompt)
    store.ingest_terminal_rows(USER, TARGET, parsed, response_complete=True)
    first_read = store.list_messages(USER, TARGET)['messages']
    store.ingest_terminal_rows(USER, TARGET, parsed, response_complete=True)
    assert store.list_messages(USER, TARGET)['messages'] == first_read
    assert [row['text'] for row in first_read if row['role'] == 'user'] == prompts


def test_same_wording_still_uses_submission_identity_not_text_identity():
    """The raw Pi boundary path is still keyed by two client submissions."""
    fixture = typed_rows('production-pi-cross-role-boundary.ansi')
    store.record_prompt(USER, TARGET, 'u-same-1', 'Hello')
    store.record_prompt(USER, TARGET, 'u-same-2', 'Hello')
    # There is one real Hello boundary in this capture, so it may match only
    # one of the two identical canonical turns; the other remains a distinct
    # user row rather than being deduplicated or stolen by text matching.
    store.ingest_terminal_rows(USER, TARGET, fixture)
    users = [row for row in store.list_messages(USER, TARGET)['messages'] if row['role'] == 'user']
    assert [row['text'] for row in users] == ['Hello', 'Hello']
    assert [row['client_message_id'] for row in users] == ['u-same-1', 'u-same-2']


def test_production_update_invocation_is_tool_activity_not_visible_prose():
    parsed = typed_rows('production-claude-update-leak.ansi')
    assert [(row['role'], row['kind']) for row in parsed] == [
        ('user', 'conversation'), ('assistant', 'conversation'),
        ('assistant', 'tool'), ('assistant', 'conversation'),
    ]
    prompt = next(row['text'] for row in parsed if row['role'] == 'user')
    store.record_prompt(USER, TARGET, 'u-update-tool', prompt)

    store.ingest_terminal_rows(USER, TARGET, parsed)

    messages = store.list_messages(USER, TARGET)['messages']
    [reply] = [item for item in messages if item['role'] == 'assistant' and item['type'] == 'conversation']
    assert reply['text'] == (
        'Aye, captain — three clear items. Dispatching a focused pass now.\n\n'
        '1. Remove the unintended tint.\n2. Float the composer.\n3. Make the drawer header transparent.'
    )
    assert 'Update(' not in reply['text']
    assert [item['text'] for item in messages if item['type'] == 'tool'] == ['Update']
