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
