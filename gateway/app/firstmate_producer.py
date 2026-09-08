"""Pinned Firstmate semantic-producer contract and bounded readiness checks."""
from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_LOCK_PATH = Path(__file__).resolve().parents[2] / 'runtime' / 'firstmate-producer.lock.json'
_LOCK_KEYS = {'schema', 'source', 'commit', 'tree', 'artifacts'}
_ARTIFACT_KEYS = {'path', 'sha256', 'bytes', 'executable'}
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_GIT_OBJECT = re.compile(r'^[0-9a-f]{40}$')
_RELATIVE_ARTIFACT = re.compile(r'^(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$')
_MAX_ARTIFACTS = 16
_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024


class ProducerContractError(RuntimeError):
    """A configured producer root does not match the reviewed immutable pin."""

    def __init__(self, code: str):
        super().__init__('The pinned Firstmate semantic producer contract is unavailable.')
        self.code = code


@dataclass(frozen=True)
class ProducerArtifact:
    path: str
    sha256: str
    bytes: int
    executable: bool


@dataclass(frozen=True)
class ProducerPin:
    source: str
    commit: str
    tree: str
    artifacts: tuple[ProducerArtifact, ...]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise RuntimeError('The Firstmate producer lock contains duplicate keys.')
        value[key] = item
    return value


def _load_pin() -> ProducerPin:
    try:
        payload = json.loads(_LOCK_PATH.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('The Firstmate producer lock is unavailable.') from exc
    if not isinstance(payload, dict) or set(payload) != _LOCK_KEYS:
        raise RuntimeError('The Firstmate producer lock has an unknown shape.')
    if payload.get('schema') != 'magistrate.firstmate-producer-lock.v1':
        raise RuntimeError('The Firstmate producer lock has an unknown schema.')
    if payload.get('source') != 'https://github.com/calvin-rutherford/firstmate.git':
        raise RuntimeError('The Firstmate producer lock has an unapproved source.')
    if not _GIT_OBJECT.fullmatch(str(payload.get('commit') or '')) or not _GIT_OBJECT.fullmatch(str(payload.get('tree') or '')):
        raise RuntimeError('The Firstmate producer lock has an invalid Git identity.')
    raw_artifacts = payload.get('artifacts')
    if not isinstance(raw_artifacts, list) or not 1 <= len(raw_artifacts) <= _MAX_ARTIFACTS:
        raise RuntimeError('The Firstmate producer lock has an invalid artifact set.')
    artifacts: list[ProducerArtifact] = []
    seen: set[str] = set()
    for raw in raw_artifacts:
        if not isinstance(raw, dict) or set(raw) != _ARTIFACT_KEYS:
            raise RuntimeError('The Firstmate producer lock has an invalid artifact.')
        path = raw.get('path')
        size = raw.get('bytes')
        digest = raw.get('sha256')
        executable = raw.get('executable')
        if (
            not isinstance(path, str) or not _RELATIVE_ARTIFACT.fullmatch(path)
            or path.startswith('.') and not path.startswith('.pi/')
            or '..' in path.split('/') or path in seen
            or not isinstance(digest, str) or not _SHA256.fullmatch(digest)
            or type(size) is not int or not 1 <= size <= _MAX_ARTIFACT_BYTES
            or type(executable) is not bool
        ):
            raise RuntimeError('The Firstmate producer lock has an invalid artifact value.')
        seen.add(path)
        artifacts.append(ProducerArtifact(path, digest, size, executable))
    required = {
        'bin/fm-captain-event.sh', 'bin/fm-fleet-snapshot.sh', 'bin/fm-spawn.sh',
        '.pi/extensions/fm-captain-event.ts', '.pi/extensions/lib/fm-captain-event.ts',
    }
    if seen != required:
        raise RuntimeError('The Firstmate producer lock omits a required call-site artifact.')
    return ProducerPin(
        source=payload['source'], commit=payload['commit'], tree=payload['tree'],
        artifacts=tuple(artifacts),
    )


FIRSTMATE_PRODUCER_PIN = _load_pin()


def _canonical_owned_directory(value: str) -> Optional[Path]:
    if not isinstance(value, str) or not value or '\x00' in value or not os.path.isabs(value):
        return None
    normalized = os.path.normpath(value)
    if normalized != value.rstrip(os.sep) or normalized == os.sep:
        return None
    current = Path(os.sep)
    allowed_owners = {0, os.geteuid()}
    try:
        for component in Path(normalized).parts[1:]:
            current /= component
            entry = os.lstat(current)
            if not stat.S_ISDIR(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or entry.st_uid not in allowed_owners:
                return None
            if entry.st_mode & stat.S_IWOTH and not (current != Path(normalized) and entry.st_mode & stat.S_ISVTX):
                return None
    except OSError:
        return None
    return Path(normalized)


def _read_owned_file(path: Path, *, maximum: int) -> bytes:
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.geteuid() or info.st_mode & stat.S_IWOTH
            or info.st_size > maximum
        ):
            raise ProducerContractError('checkout-untrusted')
        content = os.read(descriptor, maximum + 1)
        after = os.fstat(descriptor)
        if (
            len(content) != info.st_size
            or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) !=
               (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise ProducerContractError('checkout-raced')
        return content
    except ProducerContractError:
        raise
    except OSError as exc:
        raise ProducerContractError('checkout-unavailable') from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validate_checkout_identity(root: Path) -> None:
    metadata = root / '.git'
    try:
        info = os.lstat(metadata)
    except OSError as exc:
        raise ProducerContractError('checkout-unavailable') from exc
    if (
        not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid() or info.st_mode & stat.S_IWOTH
    ):
        raise ProducerContractError('checkout-untrusted')
    head = _read_owned_file(metadata / 'HEAD', maximum=128)
    if head != f'{FIRSTMATE_PRODUCER_PIN.commit}\n'.encode('ascii'):
        raise ProducerContractError('commit-mismatch')
    try:
        config_text = _read_owned_file(metadata / 'config', maximum=64 * 1024).decode('utf-8')
        config = configparser.ConfigParser(interpolation=None, strict=True)
        config.read_string(config_text)
        expected_config = {
            'core': {
                'repositoryformatversion': '0', 'filemode': 'true',
                'bare': 'false', 'logallrefupdates': 'true',
            },
            'remote "origin"': {
                'url': FIRSTMATE_PRODUCER_PIN.source,
                'fetch': '+refs/heads/*:refs/remotes/origin/*',
            },
        }
        actual_config = {
            section: dict(config.items(section, raw=True))
            for section in config.sections()
        }
        if actual_config != expected_config:
            raise ProducerContractError('source-mismatch')
    except ProducerContractError:
        raise
    except (UnicodeError, configparser.Error, ValueError) as exc:
        raise ProducerContractError('checkout-invalid') from exc


def _validate_artifact_parents(root: Path, relative: str) -> None:
    current = root
    try:
        for component in Path(relative).parts[:-1]:
            current /= component
            info = os.lstat(current)
            if (
                not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid not in {0, os.geteuid()} or info.st_mode & stat.S_IWOTH
            ):
                raise ProducerContractError('artifact-untrusted')
    except ProducerContractError:
        raise
    except OSError as exc:
        raise ProducerContractError('artifact-unavailable') from exc


def validate_producer_root(root: str, *, fm_home: Optional[str] = None) -> Path:
    """Verify the selected checkout identity and every pinned runtime call site."""
    canonical = _canonical_owned_directory(root)
    if canonical is None:
        raise ProducerContractError('root-untrusted')
    if fm_home:
        home = _canonical_owned_directory(fm_home)
        if home is None:
            raise ProducerContractError('home-untrusted')
        try:
            common = Path(os.path.commonpath((canonical, home)))
            if common in {canonical, home}:
                raise ProducerContractError('root-not-distinct')
        except (OSError, ValueError) as exc:
            raise ProducerContractError('root-unavailable') from exc
    _validate_checkout_identity(canonical)

    for artifact in FIRSTMATE_PRODUCER_PIN.artifacts:
        _validate_artifact_parents(canonical, artifact.path)
        path = canonical / artifact.path
        descriptor: Optional[int] = None
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or before.st_mode & stat.S_IWOTH
                or artifact.executable and not before.st_mode & stat.S_IXUSR
            ):
                raise ProducerContractError('artifact-untrusted')
            if before.st_size != artifact.bytes:
                raise ProducerContractError('artifact-size')
            digest = hashlib.sha256()
            remaining = artifact.bytes
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    raise ProducerContractError('artifact-size')
                digest.update(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise ProducerContractError('artifact-size')
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
            ):
                raise ProducerContractError('artifact-raced')
            if digest.hexdigest() != artifact.sha256:
                raise ProducerContractError('artifact-mismatch')
        except ProducerContractError:
            raise
        except OSError as exc:
            raise ProducerContractError('artifact-unavailable') from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
    return canonical


def activation_state(fm_home: str) -> str:
    """Classify the home-local opt-in without creating or repairing state."""
    config = Path(fm_home) / 'config'
    config_descriptor: Optional[int] = None
    descriptor: Optional[int] = None
    try:
        before = os.lstat(config)
        config_descriptor = os.open(
            config,
            os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0),
        )
        opened = os.fstat(config_descriptor)
        if (
            not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode)
            or before.st_uid != os.geteuid() or before.st_mode & stat.S_IWOTH
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            return 'invalid'
        descriptor = os.open(
            'captain-event-outbox',
            os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0),
            dir_fd=config_descriptor,
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.geteuid() or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or info.st_size > 32
        ):
            return 'invalid'
        content = os.read(descriptor, 33)
        after = os.fstat(descriptor)
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
        ):
            return 'invalid'
        return 'enabled' if content == b'enabled\n' else 'invalid'
    except FileNotFoundError:
        return 'disabled'
    except OSError:
        return 'invalid'
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if config_descriptor is not None:
            os.close(config_descriptor)


def producer_readiness(*, fm_home: str, fm_root: Optional[str], required: bool) -> dict[str, Any]:
    """Return bounded, path-free producer readiness for authenticated diagnostics."""
    base: dict[str, Any] = {
        'schema_version': 'firstmate-producer-readiness.v1',
        'required': required,
        'expected_commit': FIRSTMATE_PRODUCER_PIN.commit,
    }
    if not fm_root:
        return {**base, 'status': 'required-unavailable' if required else 'not-configured', 'activated': False}
    try:
        validate_producer_root(fm_root, fm_home=fm_home)
    except ProducerContractError as exc:
        return {**base, 'status': 'contract-invalid', 'activated': False, 'code': exc.code}
    activation = activation_state(fm_home)
    if activation == 'enabled':
        return {**base, 'status': 'ready', 'activated': True}
    if activation == 'disabled':
        status = 'required-unavailable' if required else 'installed-inactive'
        return {**base, 'status': status, 'activated': False}
    return {**base, 'status': 'activation-invalid', 'activated': False}
