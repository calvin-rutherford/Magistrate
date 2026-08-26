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
