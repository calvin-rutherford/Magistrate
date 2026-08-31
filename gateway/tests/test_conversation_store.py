"""The canonical conversation record replaces the terminal-derived transcript.

Every case here is a duplicate or leak class the old architecture produced by
treating a mutable Herdr snapshot as the chat database.
"""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import conversation_store as store
from app.herdr_client import classify_history_rows, parse_agent_history
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
    growth = ['The tests are running', 'The tests are running and 30 of them have',
              'The tests are running and all 42 of them pass.']
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

    assert payload['schema_version'] == store.CONVERSATION_SCHEMA
    assert [(item['role'], item['type'], item['text']) for item in payload['messages']] == [
        ('user', 'conversation', 'summarize the deploy'),
        ('assistant', 'tool', 'Running…'),
        ('assistant', 'conversation', 'The deploy is healthy.'),
    ]
    assert 'hidden-secret' not in str(payload)
    assert 'Worker-only report' not in str(payload)


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
    assert {item['turn_status'] for item in store.list_messages(USER, TARGET)['messages']} == {'cancelled'}


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
# tests/fixtures/*.ansi are live `HerdrClient.read_agent_output(..., 'ansi')`
# captures taken read-only from running panes on 2026-08-31. They carry the exact
# shapes a hand-written history array does not: Pi boxing a tool-envelope body
# like a user turn, a Claude composer framed by rules, mid-frame status overlays,
# and prose hard-wrapped at the real pane width. Recapture with the command in
# docs/chat-evidence-package.md rather than editing them by hand.
FIXTURES = Path(__file__).parent / 'fixtures'


def typed_rows(fixture: str):
    return classify_history_rows(parse_agent_history((FIXTURES / fixture).read_text()))


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
