import asyncio
import hashlib
import hmac
import json
import os
import socket
import stat
import struct
import time
import uuid
from pathlib import Path

import pytest

from app.pi_adapter_ipc import (
    PI_IPC_MAX_FRAME_BYTES,
    PiAdapterClient,
    PiAdapterIPCError,
    ensure_pi_ipc_key,
    _verify_peer,
)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, separators=(',', ':'), sort_keys=True,
    ).encode('utf-8')


def private_runtime(tmp_path: Path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    key = runtime / 'channel.key'
    key.write_bytes(b'K' * 48 + b'\n')
    key.chmod(0o600)
    return runtime, key


def dispatch(prompt='hello π'):
    capability = 'pic_' + 'A' * 43
    suffix = uuid.uuid4().hex
    return {
        'schema_version': 'magistrate.pi.dispatch.v1',
        'dispatch_incarnation': f'pdi_{suffix}',
        'capability': capability,
        'capability_sha256': hashlib.sha256(capability.encode()).hexdigest(),
        'tenant_id': f'tenant-{suffix}',
        'principal_id': f'tenant-{suffix}',
        'conversation_id': f'cv_{suffix}',
        'turn_id': f'ct_{suffix}',
        'assistant_message_id': f'cm_{suffix}',
        'objective_id': f'obj_{suffix}',
        'run_id': f'run_{suffix}',
        'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
        'prompt': prompt,
        'state': 'prepared',
        'expires_at': int(time.time() * 1000) + 120_000,
    }


async def start_signed_server(path, key, responder):
    observed = []

    async def handle(reader, writer):
        try:
            raw = await reader.readline()
            frame = json.loads(raw)
            expected = hmac.new(key, canonical(frame['body']), hashlib.sha256).hexdigest()
            assert hmac.compare_digest(frame['mac'], expected)
            observed.append(frame['body'])
            body, valid_mac = responder(frame['body'])
            mac = hmac.new(key, canonical(body), hashlib.sha256).hexdigest()
            if not valid_mac:
                mac = '0' * 64
            writer.write(canonical({'body': body, 'mac': mac}) + b'\n')
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_unix_server(handle, path=str(path))
    path.chmod(0o600)
    return server, observed


@pytest.mark.asyncio
async def test_authenticated_bounded_exchange_preserves_exact_prompt(tmp_path):
    runtime, key_path = private_runtime(tmp_path)
    socket_path = runtime / 'adapter.sock'
    key = key_path.read_bytes()[:-1]

    def responder(request):
        return ({
            'schema_version': 'magistrate.pi.ownership.v1',
            'event_type': 'dispatch.prepared',
            'request_nonce': request['request_nonce'],
        }, True)

    server, observed = await start_signed_server(socket_path, key, responder)
    try:
        client = PiAdapterClient(socket_path, key_path, os.geteuid())
        source = dispatch('  exact\nUnicode 🙂  ')
        response = await client.exchange(source)
    finally:
        server.close()
        await server.wait_closed()

    assert response['event_type'] == 'dispatch.prepared'
    assert observed[0]['prompt'] == source['prompt']
    assert observed[0]['prompt_sha256'] == source['prompt_sha256']
    assert observed[0]['capability'] == source['capability']
    assert observed[0]['capability_sha256'] == source['capability_sha256']
    assert observed[0]['message_type'] == 'dispatch'
    assert 'state' not in observed[0]


@pytest.mark.asyncio
async def test_status_recovery_never_retransmits_prompt(tmp_path):
    runtime, key_path = private_runtime(tmp_path)
    socket_path = runtime / 'adapter.sock'
    key = key_path.read_bytes()[:-1]

    def responder(request):
        return ({
            'schema_version': 'magistrate.pi.ownership.v1',
            'event_type': 'dispatch.bound',
            'request_nonce': request['request_nonce'],
        }, True)

    server, observed = await start_signed_server(socket_path, key, responder)
    source = dispatch('secret prompt not needed after binding')
    source['state'] = 'bound'
    try:
        await PiAdapterClient(socket_path, key_path, os.geteuid()).exchange(source)
    finally:
        server.close()
        await server.wait_closed()
    assert observed[0]['message_type'] == 'status'
    assert 'prompt' not in observed[0]


@pytest.mark.asyncio
async def test_completed_evidence_acknowledgement_is_authenticated_and_prompt_free(tmp_path):
    runtime, key_path = private_runtime(tmp_path)
    socket_path = runtime / 'adapter.sock'
    key = key_path.read_bytes()[:-1]

    def responder(request):
        return ({
            'schema_version': 'magistrate.pi.ipc.v1',
            'event_type': 'acknowledged',
            'request_nonce': request['request_nonce'],
            'dispatch_incarnation': request['dispatch_incarnation'],
            'accepted_envelope_sha256': request['accepted_envelope_sha256'],
        }, True)

    server, observed = await start_signed_server(socket_path, key, responder)
    source = dispatch('do not retransmit me')
    source['state'] = 'finalized'
    accepted_hash = 'a' * 64
    try:
        await PiAdapterClient(socket_path, key_path, os.geteuid()).acknowledge(
            source, accepted_hash,
        )
    finally:
        server.close()
        await server.wait_closed()
    assert observed[0]['message_type'] == 'ack'
    assert observed[0]['accepted_envelope_sha256'] == accepted_hash
    assert 'prompt' not in observed[0]


@pytest.mark.asyncio
async def test_response_mac_and_nonce_fail_closed(tmp_path):
    runtime, key_path = private_runtime(tmp_path)
    key = key_path.read_bytes()[:-1]

    for mismatch in ('mac', 'nonce'):
        socket_path = runtime / f'{mismatch}.sock'

        def responder(request, mismatch=mismatch):
            return ({
                'schema_version': 'magistrate.pi.ownership.v1',
                'event_type': 'dispatch.prepared',
                'request_nonce': 'different_nonce_1234' if mismatch == 'nonce' else request['request_nonce'],
            }, mismatch != 'mac')

        server, _ = await start_signed_server(socket_path, key, responder)
        try:
            with pytest.raises(PiAdapterIPCError, match=(
                'unauthenticated-ipc-response' if mismatch == 'mac' else 'ipc-response-mismatch'
            )):
                await PiAdapterClient(socket_path, key_path, os.geteuid()).exchange(dispatch())
        finally:
            server.close()
            await server.wait_closed()


@pytest.mark.asyncio
async def test_socket_owner_mode_and_adapter_absence_are_rejected(tmp_path):
    runtime, key_path = private_runtime(tmp_path)
    socket_path = runtime / 'adapter.sock'
    key = key_path.read_bytes()[:-1]

    server, _ = await start_signed_server(socket_path, key, lambda request: ({
        'schema_version': 'magistrate.pi.ownership.v1',
        'event_type': 'dispatch.prepared',
        'request_nonce': request['request_nonce'],
    }, True))
    try:
        socket_path.chmod(0o666)
        with pytest.raises(PiAdapterIPCError, match='untrusted-adapter-socket'):
            await PiAdapterClient(socket_path, key_path, os.geteuid()).exchange(dispatch())
    finally:
        server.close()
        await server.wait_closed()

    socket_path.unlink(missing_ok=True)
    with pytest.raises(PiAdapterIPCError, match='adapter-unavailable'):
        await PiAdapterClient(socket_path, key_path, os.geteuid()).exchange(dispatch())


@pytest.mark.asyncio
async def test_wrong_expected_peer_uid_is_rejected_before_payload_delivery(tmp_path):
    runtime, key_path = private_runtime(tmp_path)
    socket_path = runtime / 'adapter.sock'
    key = key_path.read_bytes()[:-1]
    server, observed = await start_signed_server(socket_path, key, lambda request: ({
        'schema_version': 'magistrate.pi.ownership.v1',
        'event_type': 'dispatch.prepared',
        'request_nonce': request['request_nonce'],
    }, True))
    try:
        with pytest.raises(PiAdapterIPCError, match='untrusted-adapter-socket'):
            await PiAdapterClient(socket_path, key_path, os.geteuid() + 1).exchange(dispatch())
        assert observed == []
    finally:
        server.close()
        await server.wait_closed()


def test_linux_peer_credentials_reject_a_different_process_owner():
    class RawSocket:
        def getsockopt(self, level, option, size):
            assert level == socket.SOL_SOCKET
            assert option == socket.SO_PEERCRED
            assert size == struct.calcsize('3i')
            return struct.pack('3i', 1234, os.geteuid() + 1, os.getegid())

    class Writer:
        def get_extra_info(self, name):
            return RawSocket() if name == 'socket' else None

    with pytest.raises(PiAdapterIPCError, match='adapter-peer-mismatch'):
        _verify_peer(Writer(), os.geteuid())


def test_key_creation_permissions_and_untrusted_paths(monkeypatch, tmp_path):
    runtime = tmp_path / 'runtime'
    monkeypatch.setenv('MAGISTRATE_PI_RUNTIME_DIR', str(runtime))
    monkeypatch.delenv('MAGISTRATE_PI_IPC_KEY_PATH', raising=False)
    path = ensure_pi_ipc_key()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    assert len(path.read_bytes().rstrip(b'\n')) >= 32

    path.chmod(0o644)
    with pytest.raises(PiAdapterIPCError, match='untrusted-ipc-key'):
        ensure_pi_ipc_key()

    path.unlink()
    target = runtime / 'target'
    target.write_bytes(b'X' * 48 + b'\n')
    target.chmod(0o600)
    path.symlink_to(target)
    with pytest.raises(PiAdapterIPCError, match='ipc-key-unavailable|untrusted-ipc-key'):
        ensure_pi_ipc_key()

    actual_runtime = tmp_path / 'actual-runtime'
    actual_runtime.mkdir(mode=0o700)
    alias_runtime = tmp_path / 'runtime-alias'
    alias_runtime.symlink_to(actual_runtime, target_is_directory=True)
    monkeypatch.setenv('MAGISTRATE_PI_RUNTIME_DIR', str(alias_runtime))
    monkeypatch.delenv('MAGISTRATE_PI_IPC_KEY_PATH', raising=False)
    with pytest.raises(PiAdapterIPCError, match='untrusted-local-runtime'):
        ensure_pi_ipc_key()


@pytest.mark.asyncio
async def test_oversized_request_is_rejected_before_delivery(tmp_path):
    runtime, key_path = private_runtime(tmp_path)
    socket_path = runtime / 'adapter.sock'
    key = key_path.read_bytes()[:-1]
    server, observed = await start_signed_server(socket_path, key, lambda request: ({
        'schema_version': 'magistrate.pi.ownership.v1',
        'event_type': 'dispatch.prepared',
        'request_nonce': request['request_nonce'],
    }, True))
    source = dispatch('x' * 100)
    # Exercise framing independently of semantic model validation; a future
    # metadata expansion remains subject to one hard transport ceiling.
    source['tenant_id'] = 'x' * PI_IPC_MAX_FRAME_BYTES
    try:
        with pytest.raises(PiAdapterIPCError, match='ipc-request-too-large'):
            await PiAdapterClient(socket_path, key_path, os.geteuid()).exchange(source)
        assert observed == []
    finally:
        server.close()
        await server.wait_closed()
