from unittest.mock import AsyncMock

import pytest
from app.herdr_client import HERDR_MAX_READ_LINES, HerdrClient


@pytest.mark.asyncio
async def test_herdr_read_preserves_lines_and_explicitly_strips_ansi(monkeypatch):
    process = AsyncMock()
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
@pytest.mark.parametrize(
    ("key", "command"),
    [
        ("Enter", ["herdr", "agent", "send-keys", "w1:p7", "Enter"]),
        ("Up", ["herdr", "agent", "send-keys", "w1:p7", "Up"]),
        ("Down", ["herdr", "agent", "send-keys", "w1:p7", "Down"]),
        ("y", ["herdr", "agent", "prompt", "w1:p7", "y"]),
        ("n", ["herdr", "agent", "prompt", "w1:p7", "n"]),
    ],
)
async def test_terminal_controls_send_exact_commands_to_exact_pane(monkeypatch, key, command):
    process = AsyncMock()
    process.returncode = 0
    process.communicate.return_value = (b"", b"")
    create_process = AsyncMock(return_value=process)
    client = HerdrClient()
    monkeypatch.setattr(client, "resolve_target", AsyncMock(return_value="w1:p7"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", create_process)

    response = await client.send_agent_key("w1:p7", key)

    assert response["target"] == "w1:p7"
    create_process.assert_awaited_once_with(*command, stdout=-1, stderr=-1)
