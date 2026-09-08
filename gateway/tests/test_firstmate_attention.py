import os
from types import SimpleNamespace

import pytest
from app.firstmate_client import (
    FIRSTMATE_RUNTIME_HOME_ENV,
    FIRSTMATE_TOOL_PATH_ENV,
    FirstmateClient,
)


def make_snapshot(tasks, records=None):
    return {
        'schema': 'fm-fleet-snapshot.v1',
        'tasks': tasks,
        'backlog': {'records': records or []}
    }


def write_snapshot_script(fm_home, body=None):
    script_dir = fm_home / 'bin'
    script_dir.mkdir(parents=True)
    script = script_dir / 'fm-fleet-snapshot.sh'
    script.write_text(
        body or '#!/bin/sh\nprintf \'%s\\n\' \'{"schema":"fm-fleet-snapshot.v1","tasks":[]}\'\n',
        encoding='utf-8',
    )
    script.chmod(0o700)
    return script


def test_firstmate_agent_names_prefer_matched_task_title_and_keep_unmatched_herdr_fallback():
    agents = [
        {'id': 'w1D:p2', 'pane_id': 'w1D:p2', 'name': 'raw-pane'},
        {'id': 'w2:p3', 'pane_id': 'w2:p3', 'name': 'Herdr worker'},
    ]
    snapshot = {'tasks': [{
        'id': 'chat-fix-c9',
        'endpoint': {'target': 'default:w1D:p2'},
        'backlog': {'title': 'Fix chat presentation'},
    }]}

    mapped = FirstmateClient.apply_agent_display_names(agents, snapshot)

    assert mapped[0]['name'] == 'Fix chat presentation'
    assert mapped[0]['display_name_source'] == 'firstmate'
    assert mapped[1]['name'] == 'Herdr worker'
    assert mapped[1]['display_name_source'] == 'herdr'


def test_firstmate_runtime_mapping_uses_only_observed_records_and_fails_closed():
    agents = [
        {'id': 'w1:p1', 'pane_id': 'w1:p1', 'name': 'worker', 'harness': 'herdr-harness', 'model': 'observed-model'},
        {'id': 'w1:p2', 'pane_id': 'w1:p2', 'name': 'missing-runtime'},
    ]
    snapshot = {'tasks': [
        {'endpoint': {'target': 'default:w1:p1'}, 'harness': 'spawn-harness'},
        {'endpoint': {'target': 'default:w1:p2'}},
    ]}

    mapped = FirstmateClient.apply_agent_display_names(agents, snapshot)

    assert mapped[0]['harness'] == 'spawn-harness'
    assert mapped[0]['model'] == 'observed-model'
    assert mapped[0]['runtime_sources'] == {'harness': 'firstmate', 'model': 'herdr'}
    assert mapped[1]['harness'] is None
    assert mapped[1]['model'] is None
    assert mapped[1]['runtime_sources'] == {'harness': None, 'model': None}


def test_firstmate_agent_name_mapping_never_fabricates_missing_identity():
    agents = [{'id': 'w1:p1', 'pane_id': 'w1:p1', 'name': 'w1:p1'}]
    snapshot = {'tasks': [{'endpoint': {'target': 'default:w1:p1'}, 'backlog': {}}]}
    assert FirstmateClient.apply_agent_display_names(agents, snapshot) == [
        {
            'id': 'w1:p1', 'pane_id': 'w1:p1', 'name': 'w1:p1',
            'harness': None, 'model': None,
            'runtime_sources': {'harness': None, 'model': None},
            'display_name_source': 'herdr',
        }
    ]


@pytest.mark.asyncio
async def test_needs_decision_task_surfaces_as_attention_item(monkeypatch):
    client = FirstmateClient()
    snapshot = make_snapshot(
        tasks=[{
            'id': 'demo-task-1',
            'project': '/home/user/Magistrate',
            'hints': {
                'pending_decision': True,
                'blocked_event': False,
                'open_decisions': [{'key': 'demo-task-1', 'verb': 'needs-decision', 'summary': 'Pick a rollout strategy.'}],
                'last_event_text': 'needs-decision: Pick a rollout strategy.'
            },
            'pr': None
        }],
        records=[{'id': 'demo-task-1', 'title': 'Ship the rollout', 'repo': 'Magistrate'}]
    )
    monkeypatch.setattr(client, 'get_snapshot', lambda: _future(snapshot))
    items = await client.get_attention_items()
    assert len(items) == 1
    assert items[0]['title'] == 'Ship the rollout'
    assert items[0]['subtitle'] == 'Pick a rollout strategy.'
    assert items[0]['status'] == 'needs-decision'
    assert items[0]['project'] == 'Magistrate'
    assert items[0]['deep_link'] == '/attention?item=captain-question-demo-task-1'
    assert items[0]['context']['decision_key'] == 'demo-task-1'


@pytest.mark.asyncio
async def test_blocked_task_surfaces_as_attention_item(monkeypatch):
    client = FirstmateClient()
    snapshot = make_snapshot(tasks=[{
        'id': 'demo-task-2',
        'project': '/home/user/Magistrate',
        'hints': {
            'pending_decision': False,
            'blocked_event': True,
            'open_decisions': [{'key': 'demo-task-2', 'verb': 'blocked', 'summary': 'Waiting on external API key.'}],
            'last_event_text': 'blocked: Waiting on external API key.'
        },
        'pr': None
    }])
    monkeypatch.setattr(client, 'get_snapshot', lambda: _future(snapshot))
    items = await client.get_attention_items()
    assert len(items) == 1
    assert items[0]['status'] == 'awaiting_answer'
    assert items[0]['subtitle'] == 'Waiting on external API key.'


@pytest.mark.asyncio
async def test_pr_ready_requires_both_pr_url_and_needs_decision(monkeypatch):
    client = FirstmateClient()
    snapshot = make_snapshot(tasks=[{
        'id': 'demo-task-3',
        'project': '/home/user/Magistrate',
        'hints': {
            'pending_decision': True,
            'blocked_event': False,
            'open_decisions': [{'key': 'demo-task-3', 'verb': 'needs-decision', 'summary': 'Merge decision needed.'}],
            'last_event_text': ''
        },
        'pr': {'url': 'https://github.com/example/repo/pull/9'}
    }])
    monkeypatch.setattr(client, 'get_snapshot', lambda: _future(snapshot))
    items = await client.get_attention_items()
    kinds = {item['type'] for item in items}
    assert 'captain_question' in kinds
    assert 'pr_ready' in kinds
    pr_item = next(item for item in items if item['type'] == 'pr_ready')
    assert pr_item['target_id'] == 'https://github.com/example/repo/pull/9'


@pytest.mark.asyncio
async def test_task_with_no_open_decisions_is_not_attention_worthy(monkeypatch):
    client = FirstmateClient()
    snapshot = make_snapshot(tasks=[{
        'id': 'demo-task-4',
        'project': '/home/user/Magistrate',
        'hints': {'pending_decision': False, 'blocked_event': False, 'open_decisions': [], 'last_event_text': 'working: on it'},
        'pr': None
    }])
    monkeypatch.setattr(client, 'get_snapshot', lambda: _future(snapshot))
    items = await client.get_attention_items()
    assert items == []


@pytest.mark.asyncio
async def test_recent_activity_uses_only_dated_requests_and_completions(monkeypatch):
    client = FirstmateClient()
    snapshot = make_snapshot(tasks=[], records=[
        {'id': 'new-work', 'title': 'Captain request', 'repo': 'Magistrate', 'state': 'queued', 'since': '2026-08-28'},
        {'id': 'finished', 'title': 'Finished task', 'repo': 'Magistrate', 'state': 'done', 'completion': {'verb': 'done', 'date': '2026-08-27'}},
        {'id': 'undated', 'title': 'No invented timestamp', 'state': 'queued'},
    ])
    monkeypatch.setattr(client, 'get_snapshot', lambda: _future(snapshot))
    items = await client.get_recent_activity()
    assert [item['id'] for item in items] == ['firstmate:new-work:requested', 'firstmate:finished:done']
    assert items[0]['type'] == 'task_requested'
    assert items[1]['description'] == 'Completed task'


@pytest.mark.asyncio
async def test_recent_activity_includes_and_deduplicates_landed_records(monkeypatch):
    client = FirstmateClient()
    completed = {'id': 'landed', 'title': 'Landed work', 'state': 'done', 'completion': {'verb': 'merged', 'date': '2026-08-28'}, 'pr_url': 'https://github.com/acme/ship/pull/7'}
    snapshot = make_snapshot(tasks=[], records=[completed])
    snapshot['secondmate_landed'] = {'records': [{**completed, 'home_id': 'secondmate'}]}
    monkeypatch.setattr(client, 'get_snapshot', lambda: _future(snapshot))
    items = await client.get_recent_activity()
    assert len(items) == 1
    assert items[0]['type'] == 'pull_request_merged'
    assert items[0]['url'].endswith('/pull/7')


@pytest.mark.asyncio
async def test_snapshot_subprocess_admits_validated_service_tool_directories(tmp_path, monkeypatch):
    local_bin = tmp_path / 'local-bin'
    npm_bin = tmp_path / 'npm-bin'
    local_bin.mkdir(mode=0o755)
    npm_bin.mkdir(mode=0o755)
    for path in (local_bin / 'herdr', npm_bin / 'tasks-axi', npm_bin / 'quota-axi'):
        path.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        path.chmod(0o700)

    script_dir = tmp_path / 'bin'
    script_dir.mkdir()
    script = script_dir / 'fm-fleet-snapshot.sh'
    script.write_text(
        '#!/bin/sh\n'
        'command -v herdr >/dev/null && command -v tasks-axi >/dev/null '
        '&& command -v quota-axi >/dev/null || exit 4\n'
        'printf \'%s\\n\' \'{"schema":"fm-fleet-snapshot.v1","tasks":[]}\'\n',
        encoding='utf-8',
    )
    script.chmod(0o700)
    monkeypatch.setenv(FIRSTMATE_TOOL_PATH_ENV, f'{local_bin}{os.pathsep}{npm_bin}')

    result = await FirstmateClient(str(tmp_path)).get_snapshot()

    assert result['available'] is True


def test_ambient_tool_path_excludes_empty_relative_and_world_writable_entries(tmp_path, monkeypatch):
    trusted = tmp_path / 'trusted'
    world_writable = tmp_path / 'world-writable'
    trusted.mkdir(mode=0o755)
    world_writable.mkdir(mode=0o777)
    world_writable.chmod(0o777)
    monkeypatch.delenv(FIRSTMATE_TOOL_PATH_ENV, raising=False)
    monkeypatch.setenv(
        'PATH',
        f'{os.pathsep}relative{os.pathsep}{world_writable}{os.pathsep}{trusted}{os.pathsep}',
    )

    assert FirstmateClient(str(tmp_path)).get_trusted_tool_path() == str(trusted)


def test_owner_layout_derives_runtime_home_from_directly_containing_fm_home(tmp_path, monkeypatch):
    runtime_home = tmp_path / 'owner'
    fm_home = runtime_home / 'firstmate'
    write_snapshot_script(fm_home)
    monkeypatch.delenv(FIRSTMATE_RUNTIME_HOME_ENV, raising=False)

    client = FirstmateClient(str(fm_home), tool_path=str(runtime_home))

    assert client.get_trusted_runtime_home() == str(runtime_home)


def test_runtime_home_chain_accepts_root_and_service_ownership(tmp_path, monkeypatch):
    runtime_home = tmp_path / 'root-owned-runtime'
    fm_home = runtime_home / 'firstmate'
    fm_home.mkdir(parents=True)
    real_lstat = os.lstat

    def root_owned_runtime(path):
        result = real_lstat(path)
        if os.fspath(path) == str(runtime_home):
            return SimpleNamespace(st_mode=result.st_mode, st_uid=0)
        return result

    monkeypatch.setattr(os, 'lstat', root_owned_runtime)
    assert FirstmateClient(
        str(fm_home), tool_path=str(tmp_path), runtime_home=str(runtime_home),
    ).get_trusted_runtime_home() == str(runtime_home)


def test_friend_runtime_style_layout_can_bind_nested_fm_home(tmp_path):
    runtime_home = tmp_path / 'friend-runtime'
    fm_home = runtime_home / '.local' / 'share' / 'firstmate'
    runtime_home.mkdir()
    fm_home.mkdir(parents=True)

    client = FirstmateClient(
        str(fm_home), tool_path=str(runtime_home), runtime_home=str(runtime_home),
    )

    assert client.get_trusted_runtime_home() == str(runtime_home)


@pytest.mark.asyncio
async def test_explicit_valid_runtime_home_is_passed_without_gateway_environment(tmp_path, monkeypatch):
    runtime_home = tmp_path / 'service-home'
    fm_home = runtime_home / 'firstmate'
    runtime_home.mkdir()
    write_snapshot_script(
        fm_home,
        '#!/bin/sh\n'
        '[ -z "${MAGISTRATE_SECRET_KEY+x}" ] || exit 8\n'
        'printf \'{"schema":"fm-fleet-snapshot.v1","tasks":[],"observed_home":"%s"}\\n\' "$HOME"\n',
    )
    monkeypatch.setenv('MAGISTRATE_SECRET_KEY', 'must-not-enter-child')

    result = await FirstmateClient(
        str(fm_home), tool_path=str(runtime_home), runtime_home=str(runtime_home),
    ).get_snapshot()

    assert result['available'] is True
    assert result['observed_home'] == str(runtime_home)


@pytest.mark.parametrize('configured', ['', 'relative', '/definitely/missing/magistrate-runtime-home'])
def test_invalid_or_relative_runtime_home_is_rejected(tmp_path, configured):
    runtime_home = tmp_path / 'owner'
    fm_home = runtime_home / 'firstmate'
    fm_home.mkdir(parents=True)

    assert FirstmateClient(
        str(fm_home), tool_path=str(runtime_home), runtime_home=configured,
    ).get_trusted_runtime_home() is None


def test_symlink_world_writable_and_untrusted_runtime_homes_are_rejected(tmp_path, monkeypatch):
    runtime_home = tmp_path / 'runtime'
    fm_home = runtime_home / 'firstmate'
    fm_home.mkdir(parents=True)
    linked_home = tmp_path / 'linked-runtime'
    linked_home.symlink_to(runtime_home, target_is_directory=True)
    assert FirstmateClient(
        str(fm_home), tool_path=str(runtime_home), runtime_home=str(linked_home),
    ).get_trusted_runtime_home() is None

    runtime_home.chmod(0o777)
    assert FirstmateClient(
        str(fm_home), tool_path=str(tmp_path), runtime_home=str(runtime_home),
    ).get_trusted_runtime_home() is None
    runtime_home.chmod(0o755)

    real_lstat = os.lstat

    def untrusted_owner(path):
        result = real_lstat(path)
        if os.fspath(path) == str(runtime_home):
            return SimpleNamespace(st_mode=result.st_mode, st_uid=os.geteuid() + 1)
        return result

    monkeypatch.setattr(os, 'lstat', untrusted_owner)
    assert FirstmateClient(
        str(fm_home), tool_path=str(tmp_path), runtime_home=str(runtime_home),
    ).get_trusted_runtime_home() is None


def test_runtime_home_must_own_the_selected_firstmate_runtime(tmp_path):
    selected_runtime = tmp_path / 'selected-runtime'
    other_runtime = tmp_path / 'other-runtime'
    fm_home = selected_runtime / 'firstmate'
    fm_home.mkdir(parents=True)
    other_runtime.mkdir()

    assert FirstmateClient(
        str(fm_home), tool_path=str(selected_runtime), runtime_home=str(other_runtime),
    ).get_trusted_runtime_home() is None


@pytest.mark.asyncio
async def test_invalid_explicit_runtime_home_fails_closed_without_default_fallback(tmp_path, monkeypatch):
    runtime_home = tmp_path / 'owner'
    fm_home = runtime_home / 'firstmate'
    marker = fm_home / 'executed'
    write_snapshot_script(
        fm_home,
        f'#!/bin/sh\n: > "{marker}"\nprintf \'%s\\n\' \'{{"schema":"fm-fleet-snapshot.v1","tasks":[]}}\'\n',
    )
    monkeypatch.setenv(FIRSTMATE_RUNTIME_HOME_ENV, 'relative-home')

    result = await FirstmateClient(str(fm_home), tool_path=str(runtime_home)).get_snapshot()

    assert result['available'] is False
    assert result['error'] == 'Fleet snapshot runtime home is unavailable'
    assert not marker.exists()


@pytest.mark.asyncio
async def test_runtime_home_state_avoids_no_mistakes_coarse_run_fallback(tmp_path):
    runtime_home = tmp_path / 'owner'
    fm_home = runtime_home / 'firstmate'
    tools = runtime_home / 'tools'
    run_state = runtime_home / '.no-mistakes' / 'runs'
    runtime_home.mkdir()
    tools.mkdir()
    run_state.mkdir(parents=True)
    (run_state / 'ship.run').write_text('run-authoritative-17\n', encoding='utf-8')
    fallback_marker = fm_home / 'coarse-fallback-called'
    fake_no_mistakes = tools / 'no-mistakes'
    fake_no_mistakes.write_text(
        '#!/bin/sh\n'
        'if [ "$*" = "axi status ship" ]; then\n'
        '  if [ -f "$HOME/.no-mistakes/runs/ship.run" ]; then\n'
        '    IFS= read -r run < "$HOME/.no-mistakes/runs/ship.run"\n'
        '    printf \'%s\\n\' "$run"\n'
        '  fi\n'
        'elif [ "$*" = "runs --limit 200" ]; then\n'
        '  : > "$FM_HOME/coarse-fallback-called"\n'
        '  sleep 1\n'
        '  printf \'%s\\n\' run-misattributed\n'
        'fi\n',
        encoding='utf-8',
    )
    fake_no_mistakes.chmod(0o700)
    write_snapshot_script(
        fm_home,
        '#!/bin/sh\n'
        'run="$(no-mistakes axi status ship)"\n'
        '[ -n "$run" ] || run="$(no-mistakes runs --limit 200)"\n'
        'printf \'{"schema":"fm-fleet-snapshot.v1","tasks":[{"run_id":"%s"}]}\\n\' "$run"\n',
    )

    result = await FirstmateClient(
        str(fm_home), tool_path=f'{tools}{os.pathsep}/usr/bin',
        runtime_home=str(runtime_home), snapshot_timeout=0.5,
    ).get_snapshot()

    assert result['available'] is True
    assert result['tasks'][0]['run_id'] == 'run-authoritative-17'
    assert not fallback_marker.exists()


@pytest.mark.parametrize('configured', ['', 'relative'])
@pytest.mark.asyncio
async def test_empty_or_relative_explicit_tool_path_fails_snapshot_closed(tmp_path, configured):
    script_dir = tmp_path / 'bin'
    script_dir.mkdir()
    script = script_dir / 'fm-fleet-snapshot.sh'
    script.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' \'{"schema":"fm-fleet-snapshot.v1","tasks":[]}\'\n',
        encoding='utf-8',
    )
    script.chmod(0o700)

    result = await FirstmateClient(str(tmp_path), tool_path=configured).get_snapshot()

    assert result['available'] is False
    assert result['tasks'] == []
    assert result['error'] == 'Fleet snapshot tool path is unavailable'


@pytest.mark.parametrize('malformed_suffix', ['\x00/bin', '\udcff'])
@pytest.mark.asyncio
async def test_malformed_explicit_tool_path_fails_snapshot_closed(tmp_path, malformed_suffix):
    script_dir = tmp_path / 'bin'
    script_dir.mkdir()
    script = script_dir / 'fm-fleet-snapshot.sh'
    script.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' \'{"schema":"fm-fleet-snapshot.v1","tasks":[]}\'\n',
        encoding='utf-8',
    )
    script.chmod(0o700)

    result = await FirstmateClient(
        str(tmp_path), tool_path=f'{tmp_path}{malformed_suffix}',
    ).get_snapshot()

    assert result['available'] is False
    assert result['tasks'] == []
    assert result['error'] == 'Fleet snapshot tool path is unavailable'


def test_explicit_tool_path_fails_closed_for_world_writable_or_untrusted_entry(tmp_path, monkeypatch):
    trusted = tmp_path / 'trusted'
    world_writable = tmp_path / 'world-writable'
    untrusted = tmp_path / 'untrusted'
    trusted.mkdir(mode=0o755)
    world_writable.mkdir(mode=0o777)
    world_writable.chmod(0o777)
    untrusted.mkdir(mode=0o755)

    assert FirstmateClient(
        str(tmp_path), tool_path=f'{trusted}{os.pathsep}{world_writable}',
    ).get_trusted_tool_path() is None

    real_lstat = os.lstat

    def untrusted_owner(path):
        result = real_lstat(path)
        if os.fspath(path) == str(untrusted):
            return SimpleNamespace(st_mode=result.st_mode, st_uid=os.geteuid() + 1)
        return result

    monkeypatch.setattr(os, 'lstat', untrusted_owner)
    assert FirstmateClient(str(tmp_path), tool_path=str(untrusted)).get_trusted_tool_path() is None


@pytest.mark.asyncio
async def test_snapshot_subprocess_timeout_is_bounded_and_truthful(tmp_path):
    script_dir = tmp_path / 'bin'
    script_dir.mkdir()
    script = script_dir / 'fm-fleet-snapshot.sh'
    script.write_text('#!/bin/sh\nexec sleep 5\n', encoding='utf-8')
    script.chmod(0o700)
    result = await FirstmateClient(str(tmp_path), snapshot_timeout=0.05).get_snapshot()
    assert result['tasks'] == []
    assert result['available'] is False
    assert result['error'] == 'Fleet snapshot timed out'


@pytest.mark.asyncio
async def test_snapshot_subprocess_output_is_hard_capped(tmp_path):
    script_dir = tmp_path / 'bin'
    script_dir.mkdir()
    script = script_dir / 'fm-fleet-snapshot.sh'
    script.write_text('#!/bin/sh\nhead -c 4096 /dev/zero\n', encoding='utf-8')
    script.chmod(0o700)
    result = await FirstmateClient(str(tmp_path), snapshot_max_bytes=128).get_snapshot()
    assert result['tasks'] == []
    assert result['available'] is False
    assert result['error'] == 'Fleet snapshot exceeded its bounded output size'


@pytest.mark.parametrize('body', [
    '#!/bin/sh\nexit 3\n',
    '#!/bin/sh\nprintf \'not-json\\n\'\n',
    '#!/bin/sh\nprintf \'%s\\n\' \'{"schema":"wrong","tasks":[]}\'\n',
    '#!/bin/sh\nprintf \'%s\\n\' \'{"schema":"fm-fleet-snapshot.v1","tasks":[],"error":"source failed"}\'\n',
])
@pytest.mark.asyncio
async def test_snapshot_command_and_parse_failures_are_explicitly_unavailable(tmp_path, body):
    script_dir = tmp_path / 'bin'
    script_dir.mkdir()
    script = script_dir / 'fm-fleet-snapshot.sh'
    script.write_text(body, encoding='utf-8')
    script.chmod(0o700)
    result = await FirstmateClient(str(tmp_path)).get_snapshot()
    assert result['fm_home'] == str(tmp_path)
    assert result['available'] is False
    assert result['error']


@pytest.mark.asyncio
async def test_valid_snapshot_is_explicitly_available(tmp_path):
    script_dir = tmp_path / 'bin'
    script_dir.mkdir()
    script = script_dir / 'fm-fleet-snapshot.sh'
    script.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' \'{"schema":"fm-fleet-snapshot.v1","tasks":[]}\'\n',
        encoding='utf-8',
    )
    script.chmod(0o700)
    result = await FirstmateClient(str(tmp_path)).get_snapshot()
    assert result['available'] is True
    assert result['fm_home'] == str(tmp_path)


async def _future(value):
    return value
