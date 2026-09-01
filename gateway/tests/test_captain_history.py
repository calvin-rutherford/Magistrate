from unittest.mock import AsyncMock

import pytest
from app.herdr_client import HERDR_MAX_READ_LINES, HerdrClient, parse_agent_history


@pytest.mark.asyncio
async def test_history_ids_keep_identical_turns_distinct(monkeypatch):
    client = HerdrClient()
    monkeypatch.setattr(client, 'resolve_target', AsyncMock(return_value='w1:p1'))
    monkeypatch.setattr(client, 'read_agent_output', AsyncMock(return_value='› repeat\n\n• repeat\n\n• repeat\n'))
    history = await client.get_agent_history('captain')
    assert len(history['messages']) == 3
    assert len({message['id'] for message in history['messages']}) == 3


def test_history_parser_unwraps_hard_wrapped_prose_but_keeps_lists():
    output = """• Implemented and opened PR #24
  (https://github.com/example/pull/24).

  - Dark alpha: 0.98 → 0.784
  - Visually verified desktop/mobile in both
    themes
"""

    assert parse_agent_history(output) == [{
        'role': 'assistant',
        'kind': 'conversation',
        'text': 'Implemented and opened PR #24 (https://github.com/example/pull/24).\n\n'
                '- Dark alpha: 0.98 → 0.784\n- Visually verified desktop/mobile in both themes',
    }]


def test_history_parser_fails_closed_for_ambiguous_pi_plain_text():
    output = """User question from Pi

Planning next step

$ npm run test
Ran 3 commands
"""
    assert parse_agent_history(output) == []


def test_history_parser_uses_pi_ansi_boxes_and_drops_harness_artifacts():
    reset = '\x1b[0m'
    user_bg = '\x1b[48;5;59m'
    tool_bg = '\x1b[48;5;22m'
    output = '\r\n'.join([
        f'{reset}{user_bg}                                                           {reset}',
        f'{reset}{user_bg} User question from Pi                                  {reset}',
        f'{reset}{user_bg}                                                           {reset}',
        '',
        f' {reset}\x1b[1m\x1b[3m\x1b[38;5;244mPlanning private work{reset}',
        '',
        f'{reset}{tool_bg} $ npm run test                                            {reset}',
        f'{reset}{tool_bg} Took 0.4s                                                 {reset}',
        '',
        ' Agent response from Pi with',
        '   a wrapped line.',
        '',
        ' /calm animation status',
        '',
        ' {"jsonrpc":"2.0","result":{"ok":true}}',
        '',
        '───────────────────────────────────────────────────────────',
        '~/firstmate (main)',
    ])
    assert parse_agent_history(output) == [
        {'role': 'user', 'kind': 'conversation', 'text': 'User question from Pi'},
        {'role': 'assistant', 'kind': 'tool', 'text': '$ npm run test Took 0.4s'},
        {'role': 'assistant', 'kind': 'conversation', 'text': 'Agent response from Pi with a wrapped line.'},
    ]


def test_history_parser_separates_conversation_from_tool_activity():
    output = """• I’ll inspect the gateway and report back.

• Ran 3 commands · ctrl + t to view transcript

• Waited for background terminal · npm run test:chat

› Please keep tool calls out of the normal history.

• Done. The history now contains only the conversation by default.

• Working (2s • esc to interrupt)

› Ask Codex to do anything

› Skipping dev server

  gpt-5.6-sol medium · /workspace
"""

    assert parse_agent_history(output) == [
        {'role': 'assistant', 'kind': 'conversation', 'text': 'I’ll inspect the gateway and report back.'},
        {'role': 'assistant', 'kind': 'tool', 'text': 'Ran 3 commands · ctrl + t to view transcript'},
        {'role': 'assistant', 'kind': 'tool', 'text': 'Waited for background terminal · npm run test:chat'},
        {'role': 'user', 'kind': 'conversation', 'text': 'Please keep tool calls out of the normal history.'},
        {'role': 'assistant', 'kind': 'conversation', 'text': 'Done. The history now contains only the conversation by default.'},
    ]


@pytest.mark.asyncio
async def test_prompt_returns_explicit_harness_reply_but_not_rpc_ack(monkeypatch):
    process = AsyncMock()
    process.returncode = 0
    process.communicate.return_value = (b'{"jsonrpc":"2.0","id":1,"result":{"response":"Pi reply"}}', b'')
    client = HerdrClient()
    monkeypatch.setattr(client, 'resolve_target', AsyncMock(return_value='w1:p1'))
    monkeypatch.setattr('asyncio.create_subprocess_exec', AsyncMock(return_value=process))

    result = await client.prompt_agent('captain', 'hello', harness='pi', model='gpt-5.6-luna')

    assert result['response'] == 'Pi reply'

    process.communicate.return_value = (b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}', b'')
    result = await client.prompt_agent('captain', 'hello')
    assert result['response'] is None

    process.communicate.return_value = (b'Ran 2 commands\nRunning 2 commands\n', b'')
    result = await client.prompt_agent('captain', 'hello')
    assert result['response'] is None


@pytest.mark.asyncio
async def test_herdr_read_preserves_lines_and_explicitly_strips_ansi(monkeypatch):
    process = AsyncMock()
    process.returncode = 0
    process.communicate.return_value = (b"first\nsecond\n", b"")
    create_process = AsyncMock(return_value=process)
    client = HerdrClient()
    monkeypatch.setattr(client, "resolve_target", AsyncMock(return_value="w1:p1"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", create_process)

    output = await client.read_agent_output("captain")

    assert output == "first\nsecond\n"
    create_process.assert_awaited_once_with(
        "herdr", "agent", "read", "w1:p1",
        "--source", "recent-unwrapped",
        "--lines", str(HERDR_MAX_READ_LINES),
        "--format", "text",
        stdout=-1,
        stderr=-1,
    )


@pytest.mark.asyncio
async def test_herdr_read_falls_back_to_visible_output_for_working_agents(monkeypatch):
    failed_process = AsyncMock()
    failed_process.returncode = 1
    failed_process.communicate.return_value = (b'', b'agent_not_idle')
    visible_process = AsyncMock()
    visible_process.returncode = 0
    visible_process.communicate.return_value = ('› Latest question\n\n• Latest answer\n'.encode(), b'')
    create_process = AsyncMock(side_effect=[failed_process, visible_process])
    client = HerdrClient()
    monkeypatch.setattr(client, 'resolve_target', AsyncMock(return_value='w1:p1'))
    monkeypatch.setattr('asyncio.create_subprocess_exec', create_process)

    output = await client.read_agent_output('agent-1')

    assert output == '› Latest question\n\n• Latest answer\n'
    assert create_process.await_args_list[1].args[:7] == (
        'herdr', 'agent', 'read', 'w1:p1', '--source', 'visible', '--lines'
    )


def test_history_parser_types_claude_unmarked_tool_rows_as_tool_activity():
    """Claude renders tool activity as an unmarked row after a blank line."""
    output = """● Two more of the four rebases are confirmed clean — merging
  both now.

  Ran 4 shell commands

● Stop hook feedback

● Running all frontend suites (not just CI's subset) plus
  gateway:

  Searched for 1 pattern, ran 10 shell commands

● Rebasing onto the new tip:

  Running 5 shell commands…
  ⎿  $ cd /workspace && npx tsc --noEmit

✽ Combobulating… (9m 12s · ↓ 12.7k tokens)
"""

    messages = parse_agent_history(output)
    assert [(m['role'], m['kind']) for m in messages] == [
        ('assistant', 'conversation'),
        ('assistant', 'tool'),
        ('assistant', 'conversation'),
        ('assistant', 'tool'),
        ('assistant', 'conversation'),
        ('assistant', 'tool'),
    ]
    conversation = [m['text'] for m in messages if m['kind'] == 'conversation']
    assert conversation == [
        'Two more of the four rebases are confirmed clean — merging both now.',
        "Running all frontend suites (not just CI's subset) plus gateway:",
        'Rebasing onto the new tip:',
    ]
    assert all('shell command' not in text for text in conversation)
    assert 'Stop hook feedback' not in ' '.join(m['text'] for m in messages)


def test_history_parser_drops_raw_terminal_rows_mislabeled_as_conversation():
    output = """● $ cat /tmp/raw-terminal-output

● A real primary response.
"""
    assert parse_agent_history(output) == [
        {'role': 'assistant', 'kind': 'conversation', 'text': 'A real primary response.'},
    ]


def test_history_parser_drops_mid_frame_chrome_and_retypes_tool_detail_rows():
    """Herdr snapshots catch status overlays mid-frame, sometimes over a message."""
    output = """● Five of six merged now — only chat cleanup (#29) remains.
  Jump to bottom (ctrl+End) ↓

● Stop hook feed 1 new message (ctrl+End) ↓

● Hardening that, then verifying the real pattern:

  Running cd "/workspace" && npx tsc --noEmit…

  ⎿  $ cd "/workspace" && npx tsc --noEmit

✻ Cogitated for 7s · done 10:43 PM · 2 shells still running
                                          ● high · /effort
"""

    messages = parse_agent_history(output)
    assert [(m['kind'], m['text'].split('\n')[0]) for m in messages] == [
        ('conversation', 'Five of six merged now — only chat cleanup (#29) remains.'),
        ('conversation', 'Hardening that, then verifying the real pattern:'),
        ('tool', 'Running cd "/workspace" && npx tsc --noEmit…'),
    ]


# Regression for the deployed-demo report after #61/#63. These fixtures mirror
# the frontend cases in frontend/tests/chat-history.test.ts; the two parsers must
# stay in sync (see AGENTS.md).
def test_pi_tool_envelope_in_a_user_box_is_tool_activity_not_a_user_turn():
    reset = '\x1b[0m'
    box = '\x1b[48;5;59m'
    output = '\r\n'.join([
        f'{reset}{box} edit gateway/app/notifications.py                          {reset}',
        f'{reset}{box}                                                           {reset}',
        f'{reset}{box} ... 340     elif parsed.path == "/pr-detail":              {reset}',
        f'{reset}{box} 341         target_type = "pull-request"                   {reset}',
        '',
        f'{reset}{box} read frontend/src/api/client.ts:425-539                    {reset}',
        '',
        ' The deploy is healthy.',
    ])
    parsed = parse_agent_history(output)
    assert [(m['role'], m['kind']) for m in parsed] == [
        ('assistant', 'tool'), ('assistant', 'tool'), ('assistant', 'conversation'),
    ]
    assert not [m for m in parsed if m['role'] == 'user']


def test_a_captain_turn_that_merely_names_a_file_stays_a_captain_turn():
    reset = '\x1b[0m'
    box = '\x1b[48;5;59m'
    output = '\r\n'.join([
        f'{reset}{box} edit src/main.py                                          {reset}',
        '',
        f'{reset}{box} check gateway/app/db.py and tell me what it does          {reset}',
        '',
        ' Here is what it does.',
    ])
    assert parse_agent_history(output) == [
        {'role': 'user', 'kind': 'conversation', 'text': 'edit src/main.py'},
        {'role': 'user', 'kind': 'conversation', 'text': 'check gateway/app/db.py and tell me what it does'},
        {'role': 'assistant', 'kind': 'conversation', 'text': 'Here is what it does.'},
    ]


def test_spinner_metadata_and_truncated_cwd_rows_are_not_prose():
    reset = '\x1b[0m'
    output = '\r\n'.join([
        ' ⠦ Working...',
        '',
        ' model: claude-opus-5',
        '',
        ' session_id: 5f2c',
        '',
        ' ~/.treehouse/Magistrate-7ab3fc/1/Magistrate (fm/magistra...',
        '',
        f'{reset}\x1b[48;5;59m A real captain question                                {reset}',
        '',
        ' A real agent answer.',
    ])
    assert parse_agent_history(output) == [
        {'role': 'user', 'kind': 'conversation', 'text': 'A real captain question'},
        {'role': 'assistant', 'kind': 'conversation', 'text': 'A real agent answer.'},
    ]


def test_firstmate_to_worker_prompt_on_a_marked_row_is_excluded():
    output = '\n'.join([
        '› FIRSTMATE_OP: v1 launch-brief: you are a crewmate',
        '',
        '⏺ Worker acknowledgement for Firstmate.',
    ])
    assert parse_agent_history(output) == [
        {'role': 'assistant', 'kind': 'conversation', 'text': 'Worker acknowledgement for Firstmate.'},
    ]


def test_a_harness_usage_overlay_is_not_conversation():
    """The captain pane can be showing Claude's /usage panel when a snapshot is
    read. Its tab bar, block meters, and gauge rows are boxed exactly like a
    user turn and were rendered as highlighted captain messages."""
    reset = '\x1b[0m'
    box = '\x1b[48;5;59m'
    output = '\r\n'.join([
        f'{reset}{box} Settings  Status   Config   Usage   Stats                 {reset}',
        '',
        ' your usage, not a breakdown                            ↑',
        '',
        ' 74% of your usage was at >150k context',
        '',
        f'{reset}{box} ███████████████████████████▌                              {reset}',
        '',
        ' 47% used ↓',
        '',
        f'{reset}{box} redeploy the demo once the chat fix lands                 {reset}',
        '',
        ' Aye captain, queued behind the chat fix.',
    ])
    assert parse_agent_history(output) == [
        {'role': 'user', 'kind': 'conversation', 'text': 'redeploy the demo once the chat fix lands'},
        {'role': 'assistant', 'kind': 'conversation', 'text': 'Aye captain, queued behind the chat fix.'},
    ]


def test_the_framed_composer_row_is_unsubmitted_input_not_a_captain_turn():
    """Claude frames its composer with '───' rules, and a Herdr snapshot catches
    that box mid-keystroke: every keystroke otherwise minted a new user row."""
    output = '\n'.join([
        '\u203a an actually submitted captain turn',
        '',
        '\u23fa The agent reply.',
        '',
        '───────────────────────────────────────────',
        "\u276f  let's start using the pi harness and gpt 5.6 luna and",
        '  sol depending on the job. only use opus 5 for the',
        '───────────────────────────────────────────',
        '  \u23f5\u23f5 auto mode on (shift+tab to cycle)',
    ])
    assert parse_agent_history(output) == [
        {'role': 'user', 'kind': 'conversation', 'text': 'an actually submitted captain turn'},
        {'role': 'assistant', 'kind': 'conversation', 'text': 'The agent reply.'},
    ]
