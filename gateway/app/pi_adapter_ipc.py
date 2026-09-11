"""Authenticated local IPC client for the Magistrate-owned Pi adapter.

The transport carries opaque ownership metadata beside the prompt and does not
inspect any presentation-layer or agent-session storage.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import socket
import stat
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Optional

PI_IPC_SCHEMA = 'magistrate.pi.ipc.v1'
PI_OWNERSHIP_SCHEMA = 'magistrate.pi.ownership.v1'
PI_IPC_MAX_FRAME_BYTES = 1_250_000
PI_IPC_KEY_MIN_BYTES = 32
PI_LOCAL_PATH_MAX_BYTES = 4096
PI_UNIX_SOCKET_PATH_MAX_BYTES = 107
PI_CONNECT_TIMEOUT_MIN_SECONDS = 0.1
PI_CONNECT_TIMEOUT_MAX_SECONDS = 10.0
PI_RESPONSE_TIMEOUT_MIN_SECONDS = 1.0
PI_RESPONSE_TIMEOUT_MAX_SECONDS = 120.0


class PiAdapterIPCError(RuntimeError):
    """A bounded local IPC failure with no prompt/capability in its message."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(',', ':'), sort_keys=True,
    ).encode('utf-8')


def _environment_value(name: str) -> Optional[str]:
    value = os.getenv(name)
    return value.strip() if value is not None else None


def _absolute_local_path(value: str | Path, *, socket_path: bool = False) -> Path:
    try:
        text = os.fspath(value)
        encoded = text.encode('utf-8')
    except (TypeError, UnicodeError) as exc:
        raise PiAdapterIPCError('invalid-local-path') from exc
    maximum = PI_UNIX_SOCKET_PATH_MAX_BYTES if socket_path else PI_LOCAL_PATH_MAX_BYTES
    if not text or '\x00' in text or len(encoded) > maximum:
        raise PiAdapterIPCError('invalid-local-path')
    if not os.path.isabs(text) or text.startswith('//') or os.path.abspath(text) != text:
        raise PiAdapterIPCError('invalid-local-path')
    return Path(text)


def _default_runtime_dir() -> Path:
    configured = _environment_value('MAGISTRATE_PI_RUNTIME_DIR')
    if configured is not None:
        return _absolute_local_path(configured)
    xdg_runtime = _environment_value('XDG_RUNTIME_DIR')
    if xdg_runtime:
        return _absolute_local_path(Path(xdg_runtime) / 'magistrate')
    # The ownership channel is Linux local IPC. Falling back to a name directly
    # below /tmp would make a world-writable directory its trust boundary.
    return Path('/run/user') / str(os.geteuid()) / 'magistrate'


def _configured_path(name: str, default_name: str, *, socket_path: bool = False) -> Path:
    configured = _environment_value(name)
    value = configured if configured is not None else _default_runtime_dir() / default_name
    return _absolute_local_path(value, socket_path=socket_path)


def _validate_directory_chain(path: Path) -> None:
    """Reject replaceable/symlinked components up to one private boundary.

    A sticky system temporary directory may contain a service-owned private
    boundary (as in hermetic tests), but a non-sticky world-writable component
    can replace descendants and is never trusted.
    """
    allowed_owners = {0, os.geteuid()}
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            info = current.lstat()
        except OSError as exc:
            raise PiAdapterIPCError('local-runtime-unavailable') from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid not in allowed_owners
        ):
            raise PiAdapterIPCError('untrusted-local-runtime')
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            if current == path or not info.st_mode & stat.S_ISVTX:
                raise PiAdapterIPCError('untrusted-local-runtime')


def _ensure_private_directory(path: Path) -> None:
    path = _absolute_local_path(path)
    try:
        if not path.exists():
            # Create only the final boundary. Recursively creating through an
            # untrusted or misspelled parent would make startup appear safe.
            _validate_directory_chain(path.parent)
            path.mkdir(mode=0o700, parents=False, exist_ok=False)
        _validate_directory_chain(path)
        info = path.lstat()
        if path.resolve(strict=True) != path:
            raise PiAdapterIPCError('untrusted-local-runtime')
    except PiAdapterIPCError:
        raise
    except (OSError, RuntimeError) as exc:
        raise PiAdapterIPCError('local-runtime-unavailable') from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise PiAdapterIPCError('untrusted-local-runtime')


def _read_private_key(path: Path, *, create: bool) -> bytes:
    _ensure_private_directory(path.parent)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        if not create:
            raise PiAdapterIPCError('ipc-key-unavailable') from None
        material = secrets.token_urlsafe(48).encode('ascii')
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0),
                0o600,
            )
            try:
                pending = memoryview(material + b'\n')
                while pending:
                    written = os.write(descriptor, pending)
                    if written <= 0:
                        raise OSError('short IPC key write')
                    pending = pending[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            parent = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        except FileExistsError:
            pass
        except OSError as exc:
            raise PiAdapterIPCError('ipc-key-unavailable') from exc
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise PiAdapterIPCError('ipc-key-unavailable') from exc
    except OSError as exc:
        raise PiAdapterIPCError('ipc-key-unavailable') from exc

    try:
        opened = os.fstat(descriptor)
        linked = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(linked.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
            or (opened.st_dev, opened.st_ino) != (linked.st_dev, linked.st_ino)
        ):
            raise PiAdapterIPCError('untrusted-ipc-key')
        chunks = []
        remaining = 1025
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        material = b''.join(chunks)
    except PiAdapterIPCError:
        raise
    except OSError as exc:
        raise PiAdapterIPCError('untrusted-ipc-key') from exc
    finally:
        os.close(descriptor)
    if len(material) > 1024 or not material.endswith(b'\n'):
        raise PiAdapterIPCError('invalid-ipc-key')
    key = material[:-1]
    if len(key) < PI_IPC_KEY_MIN_BYTES or any(byte < 33 or byte > 126 for byte in key):
        raise PiAdapterIPCError('invalid-ipc-key')
    return key


def ensure_pi_ipc_key() -> Path:
    """Validate the full local state and create only a safely missing key."""
    client = PiAdapterClient.from_environment()
    client.ensure_key()
    return client.key_path


def _expected_adapter_uid() -> int:
    raw = _environment_value('MAGISTRATE_PI_ADAPTER_UID')
    if raw is None:
        return os.geteuid()
    if not re.fullmatch(r'[0-9]+', raw):
        raise PiAdapterIPCError('invalid-adapter-uid')
    value = int(raw)
    if value != os.geteuid():
        raise PiAdapterIPCError('invalid-adapter-uid')
    return value


def _validate_endpoint_boundaries(
    runtime_dir: Path, key_path: Path, socket_path: Path, journal_path: Path,
) -> None:
    runtime_dir = _absolute_local_path(runtime_dir)
    paths = (key_path, socket_path, journal_path)
    if any(path.parent != runtime_dir for path in paths) or len(set(paths)) != len(paths):
        raise PiAdapterIPCError('invalid-local-boundary')


def _validate_socket_path(path: Path, expected_uid: int, *, allow_missing: bool = False) -> bool:
    _ensure_private_directory(path.parent)
    try:
        info = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return False
        raise PiAdapterIPCError('adapter-unavailable') from None
    except OSError as exc:
        raise PiAdapterIPCError('adapter-unavailable') from exc
    if (
        not stat.S_ISSOCK(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != expected_uid
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise PiAdapterIPCError('untrusted-adapter-socket')
    return True


def _validate_optional_journal(path: Path, expected_uid: int) -> bool:
    _ensure_private_directory(path.parent)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PiAdapterIPCError('untrusted-adapter-journal') from exc
    try:
        opened = os.fstat(descriptor)
        linked = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(linked.st_mode)
            or opened.st_uid != expected_uid
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
            or (opened.st_dev, opened.st_ino) != (linked.st_dev, linked.st_ino)
        ):
            raise PiAdapterIPCError('untrusted-adapter-journal')
    except PiAdapterIPCError:
        raise
    except OSError as exc:
        raise PiAdapterIPCError('untrusted-adapter-journal') from exc
    finally:
        os.close(descriptor)
    return True


def _timeout(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = _environment_value(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError as exc:
        raise PiAdapterIPCError('invalid-ipc-timeout') from exc
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise PiAdapterIPCError('invalid-ipc-timeout')
    return value


def _verify_peer(writer: asyncio.StreamWriter, expected_uid: int) -> int:
    raw_socket = writer.get_extra_info('socket')
    peer_option = getattr(socket, 'SO_PEERCRED', None)
    if raw_socket is None or peer_option is None:
        raise PiAdapterIPCError('peer-identity-unavailable')
    try:
        credentials = raw_socket.getsockopt(socket.SOL_SOCKET, peer_option, struct.calcsize('3i'))
        _pid, uid, _gid = struct.unpack('3i', credentials)
    except (OSError, struct.error) as exc:
        raise PiAdapterIPCError('peer-identity-unavailable') from exc
    if uid != expected_uid:
        raise PiAdapterIPCError('adapter-peer-mismatch')
    return _pid


def _signed_frame(body: Mapping[str, Any], key: bytes) -> bytes:
    encoded_body = _canonical_json(dict(body))
    mac = hmac.new(key, encoded_body, hashlib.sha256).hexdigest()
    frame = _canonical_json({'body': dict(body), 'mac': mac}) + b'\n'
    if len(frame) > PI_IPC_MAX_FRAME_BYTES:
        raise PiAdapterIPCError('ipc-request-too-large')
    return frame


def _verified_response(raw: bytes, key: bytes, request_nonce: str) -> dict[str, Any]:
    if not raw or len(raw) > PI_IPC_MAX_FRAME_BYTES or not raw.endswith(b'\n'):
        raise PiAdapterIPCError('invalid-ipc-response')
    try:
        frame = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PiAdapterIPCError('invalid-ipc-response') from exc
    if not isinstance(frame, dict) or set(frame) != {'body', 'mac'}:
        raise PiAdapterIPCError('invalid-ipc-response')
    try:
        if _canonical_json(frame) + b'\n' != raw:
            raise PiAdapterIPCError('noncanonical-ipc-response')
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise PiAdapterIPCError('invalid-ipc-response') from exc
    body, supplied_mac = frame['body'], frame['mac']
    if not isinstance(body, dict) or not isinstance(supplied_mac, str):
        raise PiAdapterIPCError('invalid-ipc-response')
    expected_mac = hmac.new(key, _canonical_json(body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_mac, supplied_mac):
        raise PiAdapterIPCError('unauthenticated-ipc-response')
    if body.get('request_nonce') != request_nonce:
        raise PiAdapterIPCError('ipc-response-mismatch')
    if body.get('schema_version') == PI_IPC_SCHEMA:
        if body.get('event_type') == 'error':
            if set(body) != {'schema_version', 'event_type', 'request_nonce', 'error_code'}:
                raise PiAdapterIPCError('unsupported-adapter-response')
            code = body.get('error_code')
            raise PiAdapterIPCError(
                code
                if isinstance(code, str)
                and re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', code)
                else 'adapter-rejected'
            )
        if body.get('event_type') == 'ready':
            if set(body) != {'schema_version', 'event_type', 'request_nonce'}:
                raise PiAdapterIPCError('unsupported-adapter-response')
            return body
        if body.get('event_type') == 'acknowledged':
            if set(body) != {
                'schema_version', 'event_type', 'request_nonce',
                'dispatch_incarnation', 'accepted_envelope_sha256',
            }:
                raise PiAdapterIPCError('unsupported-adapter-response')
            return body
        raise PiAdapterIPCError('unsupported-adapter-response')
    if body.get('schema_version') != PI_OWNERSHIP_SCHEMA:
        raise PiAdapterIPCError('unsupported-adapter-response')
    return body


@dataclass(frozen=True)
class PiAdapterClient:
    socket_path: Path
    key_path: Path
    expected_uid: int
    connect_timeout: float = 1.0
    response_timeout: float = 20.0
    runtime_dir: Optional[Path] = None
    journal_path: Optional[Path] = None

    @classmethod
    def from_environment(cls) -> 'PiAdapterClient':
        runtime_dir = _default_runtime_dir()
        key_path = _configured_path('MAGISTRATE_PI_IPC_KEY_PATH', 'pi-ownership.key')
        socket_path = _configured_path(
            'MAGISTRATE_PI_ADAPTER_SOCKET', 'pi-ownership.sock', socket_path=True,
        )
        journal_path = _configured_path(
            'MAGISTRATE_PI_ADAPTER_JOURNAL', 'pi-ownership.journal',
        )
        _validate_endpoint_boundaries(runtime_dir, key_path, socket_path, journal_path)
        return cls(
            socket_path=socket_path,
            key_path=key_path,
            expected_uid=_expected_adapter_uid(),
            connect_timeout=_timeout(
                'MAGISTRATE_PI_CONNECT_TIMEOUT_SECONDS', 1.0,
                PI_CONNECT_TIMEOUT_MIN_SECONDS, PI_CONNECT_TIMEOUT_MAX_SECONDS,
            ),
            response_timeout=_timeout(
                'MAGISTRATE_PI_RESPONSE_TIMEOUT_SECONDS', 20.0,
                PI_RESPONSE_TIMEOUT_MIN_SECONDS, PI_RESPONSE_TIMEOUT_MAX_SECONDS,
            ),
            runtime_dir=runtime_dir,
            journal_path=journal_path,
        )

    def ensure_key(self) -> None:
        """Validate startup configuration, creating only a missing shared key.

        A missing adapter socket is a readiness condition. Any socket object or
        journal that is already present must satisfy its complete local trust
        boundary before Gateway starts serving requests.
        """
        runtime_dir = self.runtime_dir or self.key_path.parent
        _ensure_private_directory(runtime_dir)
        if self.runtime_dir is not None and self.journal_path is not None:
            _validate_endpoint_boundaries(
                self.runtime_dir, self.key_path, self.socket_path, self.journal_path,
            )
        socket_present = _validate_socket_path(
            self.socket_path, self.expected_uid, allow_missing=True,
        )
        journal_present = False
        if self.journal_path is not None:
            journal_present = _validate_optional_journal(
                self.journal_path, self.expected_uid,
            )
        try:
            _read_private_key(self.key_path, create=False)
        except PiAdapterIPCError as exc:
            if exc.code != 'ipc-key-unavailable' or socket_present or journal_present:
                raise
            _read_private_key(self.key_path, create=True)

    async def probe(self) -> int:
        """Complete a nonce-bound handshake and return the verified peer PID."""
        key = _read_private_key(self.key_path, create=False)
        _validate_socket_path(self.socket_path, self.expected_uid)
        if self.journal_path is not None:
            _validate_optional_journal(self.journal_path, self.expected_uid)
        request_nonce = secrets.token_urlsafe(24)
        frame = _signed_frame({
            'schema_version': PI_IPC_SCHEMA,
            'message_type': 'probe',
            'request_nonce': request_nonce,
            'issued_at': int(time.time() * 1000),
        }, key)
        writer: Optional[asyncio.StreamWriter] = None
        connected = False
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(
                    str(self.socket_path), limit=PI_IPC_MAX_FRAME_BYTES + 1,
                ),
                timeout=self.connect_timeout,
            )
            connected = True
            peer_pid = _verify_peer(writer, self.expected_uid)
            writer.write(frame)
            await asyncio.wait_for(writer.drain(), timeout=self.connect_timeout)
            raw = await asyncio.wait_for(reader.readline(), timeout=self.response_timeout)
            if len(raw) > PI_IPC_MAX_FRAME_BYTES:
                raise PiAdapterIPCError('ipc-response-too-large')
            response = _verified_response(raw, key, request_nonce)
            if response.get('event_type') != 'ready':
                raise PiAdapterIPCError('unsupported-adapter-response')
            return peer_pid
        except PiAdapterIPCError:
            raise
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
            code = 'adapter-authentication-unavailable' if connected else 'adapter-unavailable'
            raise PiAdapterIPCError(code) from exc
        except (ValueError, asyncio.LimitOverrunError) as exc:
            raise PiAdapterIPCError('invalid-ipc-response') from exc
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

    async def is_ready(self) -> bool:
        """Whether the adapter answers an authenticated readiness probe."""
        try:
            await self.probe()
            return True
        except PiAdapterIPCError:
            return False

    async def exchange(
        self,
        dispatch: Mapping[str, Any],
        *,
        action: Optional[Literal['dispatch', 'status', 'ack']] = None,
        accepted_envelope_sha256: Optional[str] = None,
    ) -> dict[str, Any]:
        """Dispatch/recover one exact incarnation and return signed evidence."""
        key = _read_private_key(self.key_path, create=False)
        _validate_socket_path(self.socket_path, self.expected_uid)
        if self.journal_path is not None:
            _validate_optional_journal(self.journal_path, self.expected_uid)
        request_nonce = secrets.token_urlsafe(24)
        selected_action = action or (
            'status' if dispatch.get('state') in {'bound', 'finalized', 'failed'} else 'dispatch'
        )
        required = (
            'dispatch_incarnation', 'capability', 'capability_sha256',
            'tenant_id', 'principal_id',
            'conversation_id', 'turn_id', 'assistant_message_id', 'objective_id',
            'run_id', 'prompt_sha256', 'expires_at',
        )
        if any(key_name not in dispatch for key_name in required):
            raise PiAdapterIPCError('invalid-dispatch-state')
        body: dict[str, Any] = {
            'schema_version': PI_IPC_SCHEMA,
            'message_type': selected_action,
            'request_nonce': request_nonce,
            'issued_at': int(time.time() * 1000),
            **{name: dispatch[name] for name in required},
        }
        if selected_action == 'dispatch':
            prompt = dispatch.get('prompt')
            if not isinstance(prompt, str):
                raise PiAdapterIPCError('invalid-dispatch-state')
            body['prompt'] = prompt
        elif selected_action == 'ack':
            if (
                not isinstance(accepted_envelope_sha256, str)
                or not re.fullmatch(r'[a-f0-9]{64}', accepted_envelope_sha256)
            ):
                raise PiAdapterIPCError('invalid-dispatch-state')
            body['accepted_envelope_sha256'] = accepted_envelope_sha256
        frame = _signed_frame(body, key)
        writer: Optional[asyncio.StreamWriter] = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(
                    str(self.socket_path), limit=PI_IPC_MAX_FRAME_BYTES + 1,
                ),
                timeout=self.connect_timeout,
            )
            _verify_peer(writer, self.expected_uid)
            writer.write(frame)
            await asyncio.wait_for(writer.drain(), timeout=self.connect_timeout)
            raw = await asyncio.wait_for(reader.readline(), timeout=self.response_timeout)
            if len(raw) > PI_IPC_MAX_FRAME_BYTES:
                raise PiAdapterIPCError('ipc-response-too-large')
            return _verified_response(raw, key, request_nonce)
        except PiAdapterIPCError:
            raise
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
            raise PiAdapterIPCError('adapter-unavailable') from exc
        except (ValueError, asyncio.LimitOverrunError) as exc:
            raise PiAdapterIPCError('invalid-ipc-response') from exc
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

    async def acknowledge(
        self,
        dispatch: Mapping[str, Any],
        accepted_envelope_sha256: str,
    ) -> None:
        response = await self.exchange(
            dispatch,
            action='ack',
            accepted_envelope_sha256=accepted_envelope_sha256,
        )
        if (
            response.get('schema_version') != PI_IPC_SCHEMA
            or response.get('event_type') != 'acknowledged'
            or response.get('dispatch_incarnation') != dispatch.get('dispatch_incarnation')
            or response.get('accepted_envelope_sha256') != accepted_envelope_sha256
        ):
            raise PiAdapterIPCError('ipc-acknowledgement-mismatch')
