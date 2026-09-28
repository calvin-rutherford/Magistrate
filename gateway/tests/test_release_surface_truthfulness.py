"""Retained executable surfaces must not manufacture successful side effects."""
import pytest

from app.ar_glasses import ARGlassesConnectionManager


class Socket:
    def __init__(self):
        self.frames = []

    async def send_json(self, frame):
        self.frames.append(frame)


@pytest.mark.asyncio
async def test_ar_input_cannot_claim_a_nonexistent_firstmate_dispatch():
    socket = Socket()
    await ARGlassesConnectionManager().process_payload(
        {"type": "input", "modality": "gesture", "payload": "synthetic private input"},
        socket, can_command=True,
    )
    assert socket.frames == [{
        "status": "unavailable",
        "error": "AR execution is unavailable. Submit work through Native Magi.",
    }]
    assert "synthetic private input" not in str(socket.frames)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,authorized", [([], True), (None, True), ({"type": "input"}, True), ({"type": "input", "modality": "gesture", "payload": "x"}, False)])
async def test_invalid_or_unauthorized_ar_input_never_returns_a_receipt(payload, authorized):
    socket = Socket()
    await ARGlassesConnectionManager().process_payload(payload, socket, can_command=authorized)
    assert len(socket.frames) == 1
    assert "error" in socket.frames[0]
    assert "action" not in socket.frames[0]
    assert "status" not in socket.frames[0]
