from unittest.mock import AsyncMock

import pytest
from app.herdr_client import HERDR_MAX_READ_LINES, HerdrClient, parse_agent_history


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
