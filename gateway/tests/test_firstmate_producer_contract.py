import hashlib
from pathlib import Path

import pytest

from app.firstmate_activity import FirstmateActivityAdapter
from app.firstmate_client import FirstmateClient
from app.firstmate_producer import (
    FIRSTMATE_PRODUCER_PIN,
    ProducerArtifact,
    ProducerContractError,
    ProducerPin,
    producer_readiness,
    validate_producer_root,
)


def fixture_pin(path: str, payload: bytes, *, executable: bool = True) -> ProducerPin:
    return ProducerPin(
        source=FIRSTMATE_PRODUCER_PIN.source,
        commit=FIRSTMATE_PRODUCER_PIN.commit,
        tree=FIRSTMATE_PRODUCER_PIN.tree,
        artifacts=(ProducerArtifact(
            path=path,
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes=len(payload),
            executable=executable,
        ),),
    )


def write_contract_root(root: Path, relative: str, payload: bytes, *, executable: bool = True) -> None:
    metadata = root / '.git'
    metadata.mkdir(exist_ok=True)
    (metadata / 'HEAD').write_text(f'{FIRSTMATE_PRODUCER_PIN.commit}\n', encoding='ascii')
    (metadata / 'config').write_text(
        '[core]\n'
        '\trepositoryformatversion = 0\n'
        '\tfilemode = true\n'
        '\tbare = false\n'
        '\tlogallrefupdates = true\n'
        '[remote "origin"]\n'
        f'\turl = {FIRSTMATE_PRODUCER_PIN.source}\n'
        '\tfetch = +refs/heads/*:refs/remotes/origin/*\n',
        encoding='utf-8',
    )
    artifact = root / relative
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(payload)
    artifact.chmod(0o700 if executable else 0o600)


def test_lock_names_the_reviewed_source_commit_tree_and_all_runtime_call_sites():
    assert FIRSTMATE_PRODUCER_PIN.source == 'https://github.com/calvin-rutherford/firstmate.git'
    assert FIRSTMATE_PRODUCER_PIN.commit == '2af0d17014cb2e244aa441bfe6df16c4f630475b'
    assert FIRSTMATE_PRODUCER_PIN.tree == '146c55db1fd4d9cb2edf38d384b4d2362d2041e3'
    assert {artifact.path for artifact in FIRSTMATE_PRODUCER_PIN.artifacts} == {
        'bin/fm-captain-event.sh',
        'bin/fm-fleet-snapshot.sh',
        'bin/fm-spawn.sh',
        '.pi/extensions/fm-captain-event.ts',
        '.pi/extensions/lib/fm-captain-event.ts',
    }


def test_contract_validator_accepts_only_exact_owned_distinct_artifacts(tmp_path, monkeypatch):
    payload = b'#!/bin/sh\nexit 0\n'
    root = tmp_path / 'managed-code'
    home = tmp_path / 'runtime-home'
    root.mkdir()
    home.mkdir()
    write_contract_root(root, 'bin/producer', payload)
    monkeypatch.setattr(
        'app.firstmate_producer.FIRSTMATE_PRODUCER_PIN',
        fixture_pin('bin/producer', payload),
    )

    assert validate_producer_root(str(root), fm_home=str(home)) == root

    (root / 'bin/producer').write_bytes(payload + b'# drift\n')
    with pytest.raises(ProducerContractError) as mismatch:
        validate_producer_root(str(root), fm_home=str(home))
    assert mismatch.value.code in {'artifact-size', 'artifact-mismatch'}

    with pytest.raises(ProducerContractError) as aliased:
        validate_producer_root(str(home), fm_home=str(home))
    assert aliased.value.code == 'root-not-distinct'
    with pytest.raises(ProducerContractError) as nested:
        validate_producer_root(str(root), fm_home=str(tmp_path))
    assert nested.value.code == 'root-not-distinct'


def test_contract_validator_rejects_branch_heads_and_checkout_config_injection(tmp_path, monkeypatch):
    payload = b'producer\n'
    root = tmp_path / 'managed-code'
    home = tmp_path / 'runtime-home'
    root.mkdir()
    home.mkdir()
    write_contract_root(root, 'producer', payload, executable=False)
    monkeypatch.setattr(
        'app.firstmate_producer.FIRSTMATE_PRODUCER_PIN',
        fixture_pin('producer', payload, executable=False),
    )

    (root / '.git/HEAD').write_text('ref: refs/heads/main\n', encoding='ascii')
    with pytest.raises(ProducerContractError) as branch:
        validate_producer_root(str(root), fm_home=str(home))
    assert branch.value.code == 'commit-mismatch'

    (root / '.git/HEAD').write_text(f'{FIRSTMATE_PRODUCER_PIN.commit}\n', encoding='ascii')
    with (root / '.git/config').open('a', encoding='utf-8') as stream:
        stream.write('[core]\n\tfsmonitor = /tmp/untrusted-hook\n')
    with pytest.raises(ProducerContractError) as injected:
        validate_producer_root(str(root), fm_home=str(home))
    assert injected.value.code in {'checkout-invalid', 'source-mismatch'}


def test_contract_validator_rejects_symlink_and_world_writable_roots(tmp_path, monkeypatch):
    payload = b'producer\n'
    root = tmp_path / 'managed-code'
    home = tmp_path / 'runtime-home'
    root.mkdir()
    home.mkdir()
    write_contract_root(root, 'producer', payload, executable=False)
    monkeypatch.setattr(
        'app.firstmate_producer.FIRSTMATE_PRODUCER_PIN',
        fixture_pin('producer', payload, executable=False),
    )

    linked = tmp_path / 'linked-code'
    linked.symlink_to(root, target_is_directory=True)
    with pytest.raises(ProducerContractError, match='unavailable') as symlinked:
        validate_producer_root(str(linked), fm_home=str(home))
    assert symlinked.value.code == 'root-untrusted'

    root.chmod(0o777)
    with pytest.raises(ProducerContractError) as writable:
        validate_producer_root(str(root), fm_home=str(home))
    assert writable.value.code == 'root-untrusted'


def test_readiness_is_path_free_optional_until_explicitly_required(tmp_path, monkeypatch):
    payload = b'producer\n'
    root = tmp_path / 'managed-code'
    home = tmp_path / 'runtime-home'
    root.mkdir()
    home.mkdir()
    write_contract_root(root, 'producer', payload, executable=False)
    monkeypatch.setattr(
        'app.firstmate_producer.FIRSTMATE_PRODUCER_PIN',
        fixture_pin('producer', payload, executable=False),
    )

    assert producer_readiness(fm_home=str(home), fm_root=None, required=False) == {
        'schema_version': 'firstmate-producer-readiness.v1',
        'required': False,
        'expected_commit': FIRSTMATE_PRODUCER_PIN.commit,
        'status': 'not-configured',
        'activated': False,
    }
    installed = producer_readiness(fm_home=str(home), fm_root=str(root), required=False)
    assert installed['status'] == 'installed-inactive'
    assert str(root) not in str(installed) and str(home) not in str(installed)

    config = home / 'config'
    config.mkdir()
    (config / 'captain-event-outbox').write_text('enabled\n', encoding='utf-8')
    (config / 'captain-event-outbox').chmod(0o666)
    assert producer_readiness(
        fm_home=str(home), fm_root=str(root), required=True,
    )['status'] == 'activation-invalid'
    (config / 'captain-event-outbox').chmod(0o600)
    assert producer_readiness(fm_home=str(home), fm_root=str(root), required=True)['status'] == 'ready'


def test_required_mode_without_an_explicit_root_is_truthfully_unavailable(tmp_path):
    client = FirstmateClient(str(tmp_path), captain_producer_required=True)
    readiness = client.get_producer_readiness()
    assert readiness['required'] is True
    assert readiness['status'] == 'required-unavailable'
    with pytest.raises(ProducerContractError) as missing:
        client.validate_producer_contract()
    assert missing.value.code == 'root-not-configured'


@pytest.mark.asyncio
async def test_required_mode_fails_adapter_startup_before_reconciliation(tmp_path):
    client = FirstmateClient(str(tmp_path), captain_producer_required=True)
    adapter = FirstmateActivityAdapter(client, fm_home=str(tmp_path))
    with pytest.raises(RuntimeError, match='required pinned Firstmate producer'):
        await adapter.require_captain_producer_ready()


def test_required_feature_flag_rejects_unknown_literals(tmp_path, monkeypatch):
    monkeypatch.setenv('MAGISTRATE_FIRSTMATE_CAPTAIN_REQUIRED', 'sometimes')
    with pytest.raises(ValueError, match='boolean literal'):
        FirstmateClient(str(tmp_path))
