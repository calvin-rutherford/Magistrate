import asyncio
import json
import os

import httpx
import pytest

from app import db
from app.magi_chat_service import MagiChatService
from app.magi_chat_store import MagiChatStore
from app.magi_firstmate_tools import (
    FIRSTMATE_SUBMIT_OBJECTIVE,
    FirstmateObjectiveTools,
    ObjectiveDispatchError,
    ObjectiveDispatchReceipt,
    ObjectiveSubmissionStore,
    TasksAxiObjectiveDispatcher,
    _task_body,
    _task_title,
    _validate_tasks_axi_receipt,
    parse_submit_objective_arguments,
)
from app.magi_model import MagiModelResult, MagiModelToolCall, OpenAIMagiModel
from app.magi_tool_protocol import MagiToolContext, MagiToolError


def objective_arguments(**updates):
    value = {
        'objective': 'Add a health endpoint to Magistrate and test it.',
        'project': 'Magistrate',
        'constraints': ['Preserve the existing Native Chat transport.'],
        'acceptance_criteria': [
            'An authenticated health endpoint returns the documented healthy response.',
            'Focused endpoint tests pass.',
        ],
        'context_refs': ['message:mgm_request_1234'],
    }
    value.update(updates)
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def tool_context(owner='operator-a', *, authorized=True):
    return MagiToolContext(
        owner_user_id=owner,
        conversation_id='mgc_conversation_1234',
        turn_id='mgt_turn_1234',
        user_message_id='mgm_user_1234',
        assistant_message_id='mgm_assistant_1234',
        command_authorized=authorized,
    )


class FakeDispatcher:
    def __init__(self, *, fail_count=0):
        self.fail_count = fail_count
        self.calls = []

    async def submit(self, **request):
        self.calls.append(request)
        if self.fail_count:
            self.fail_count -= 1
            raise ObjectiveDispatchError('synthetic-refusal')
        return ObjectiveDispatchReceipt(already_present=len(self.calls) > 1)


@pytest.mark.parametrize('arguments', [
    '{}',
    objective_arguments(project='Magistrate workspace'),
    objective_arguments(acceptance_criteria=[]),
    objective_arguments(extra='not allowed'),
    objective_arguments(constraints=['same', 'same']),
    objective_arguments(context_refs=['../../secret']),
    objective_arguments(objective='x' * 4_001),
    objective_arguments(objective='unsafe\u001bvalue'),
    '{"objective":"one","objective":"two","project":"Magistrate",'
    '"constraints":[],"acceptance_criteria":["verified"],"context_refs":[]}',
])
def test_submit_objective_arguments_are_strict_bounded_and_closed(arguments):
    with pytest.raises(MagiToolError) as raised:
        parse_submit_objective_arguments(arguments)
    assert raised.value.code == 'objective_arguments_invalid'


def test_submit_objective_contract_keeps_all_required_typed_fields():
    parsed = parse_submit_objective_arguments(objective_arguments())
    assert parsed.model_dump() == {
        'objective': 'Add a health endpoint to Magistrate and test it.',
        'project': 'Magistrate',
        'constraints': ['Preserve the existing Native Chat transport.'],
        'acceptance_criteria': [
            'An authenticated health endpoint returns the documented healthy response.',
            'Focused endpoint tests pass.',
        ],
        'context_refs': ['message:mgm_request_1234'],
    }


@pytest.mark.asyncio
async def test_objective_executor_is_principal_scoped_and_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'objectives.sqlite3'))
    dispatcher = FakeDispatcher()
    store = ObjectiveSubmissionStore()
    tools = FirstmateObjectiveTools(store=store, dispatcher=dispatcher)
    call = MagiModelToolCall(
        id='call_objective_1', name=FIRSTMATE_SUBMIT_OBJECTIVE,
        arguments_json=objective_arguments(),
    )

    first = await tools.execute(call, context=tool_context(), invocation_key='a' * 64)
    replay = await tools.execute(call, context=tool_context(), invocation_key='a' * 64)
    assert first.model_content() == replay.model_content()
    payload = json.loads(first.model_content())
    assert payload == {
        'schema_version': 'firstmate.submit-objective-result.v1',
        'status': 'accepted',
        'objective_id': payload['objective_id'],
        'task_id': payload['task_id'],
    }
    assert payload['objective_id'].startswith('mgo_')
    assert payload['task_id'].startswith('magi-')
    assert len(dispatcher.calls) == 1
    assert dispatcher.calls[0]['task_id'] == payload['task_id']
    assert dispatcher.calls[0]['project'] == 'Magistrate'
    assert 'firstmate.objective.v1' in dispatcher.calls[0]['body']
    assert 'Add a health endpoint to Magistrate and test it.' in dispatcher.calls[0]['body']

    record = store.get('operator-a', payload['objective_id'])
    assert record is not None
    assert record['status'] == 'accepted'
    assert record['task_id'] == payload['task_id']
    assert record['contract']['acceptance_criteria'][-1] == 'Focused endpoint tests pass.'
    assert store.get('operator-b', payload['objective_id']) is None

    other = await tools.execute(call, context=tool_context('operator-b'), invocation_key='a' * 64)
    assert other.payload['objective_id'] != payload['objective_id']
    assert other.payload['task_id'] != payload['task_id']
    assert len(dispatcher.calls) == 2


@pytest.mark.asyncio
async def test_objective_executor_rejects_missing_command_authority_and_argument_drift(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'objective-auth.sqlite3'))
    dispatcher = FakeDispatcher()
    tools = FirstmateObjectiveTools(
        store=ObjectiveSubmissionStore(), dispatcher=dispatcher,
    )
    call = MagiModelToolCall('call_objective_1', FIRSTMATE_SUBMIT_OBJECTIVE, objective_arguments())
    with pytest.raises(MagiToolError) as unauthorized:
        await tools.execute(call, context=tool_context(authorized=False), invocation_key='b' * 64)
    assert unauthorized.value.code == 'objective_not_authorized'
    assert dispatcher.calls == []

    await tools.execute(call, context=tool_context(), invocation_key='b' * 64)
    changed = MagiModelToolCall(
        'call_objective_2', FIRSTMATE_SUBMIT_OBJECTIVE,
        objective_arguments(objective='Build a different endpoint.'),
    )
    with pytest.raises(MagiToolError) as conflict:
        await tools.execute(changed, context=tool_context(), invocation_key='b' * 64)
    assert conflict.value.code == 'objective_idempotency_conflict'
    assert len(dispatcher.calls) == 1


@pytest.mark.asyncio
async def test_tasks_axi_dispatch_is_argument_safe_minimal_and_receipt_bound(monkeypatch, tmp_path):
    fm_home = tmp_path / 'firstmate'
    fm_home.mkdir()
    captured = {}

    class Stream:
        def __init__(self, content):
            self.content = content

        async def read(self, size):
            chunk, self.content = self.content[:size], self.content[size:]
            return chunk

    class Process:
        pid = 12345
        returncode = 0

        def __init__(self, stdout):
            self.stdout = Stream(stdout)
            self.stderr = Stream(b'')

        async def wait(self):
            return 0

    class TrustedFirstmate:
        def __init__(self):
            self.fm_home = str(fm_home)

        @staticmethod
        def get_trusted_tool_path():
            return '/usr/bin:/bin'

        @staticmethod
        def get_trusted_runtime_home():
            return str(tmp_path)

    async def fake_create_subprocess_exec(*arguments, **options):
        captured['arguments'] = arguments
        captured['options'] = options
        body_path = arguments[arguments.index('--body-file') + 1]
        with open(body_path, encoding='utf-8') as stream:
            body = stream.read()
        captured['body_path'] = body_path
        task = {
            'id': arguments[2],
            'title': arguments[3],
            'state': 'queued',
            'kind': None,
            'repo': arguments[arguments.index('--repo') + 1],
            'priority': None,
            'created': '2026-09-13',
            'closed': None,
            'deps': [],
            'hold': None,
            'links': [],
            'body': body,
            'blocked': False,
            'blocked_by': [],
            'held': False,
        }
        stdout = json.dumps({'ok': True, 'action': 'add', 'task': task}).encode()
        return Process(stdout)

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_create_subprocess_exec)
    dispatcher = TasksAxiObjectiveDispatcher(TrustedFirstmate())
    receipt = await dispatcher.submit(
        task_id='magi-' + '3' * 32,
        title='Magi: Add endpoint; $(touch owned) (Magi objective)',
        project='Magistrate',
        body='bounded objective body',
    )
    assert receipt.already_present is False
    arguments = captured['arguments']
    assert arguments == (
        'tasks-axi', 'add', 'magi-' + '3' * 32,
        'Magi: Add endpoint; $(touch owned) (Magi objective)',
        '--repo', 'Magistrate', '--body-file', captured['body_path'], '--queue', '--json',
    )
    assert '--kind' not in arguments
    assert not os.path.exists(captured['body_path'])
    assert set(captured['options']['env']) == {
        'FM_HOME', 'PATH', 'HOME', 'LANG', 'LC_ALL', 'NO_COLOR',
    }
    assert captured['options']['cwd'] == str(fm_home)


def test_tasks_axi_receipt_must_confirm_the_exact_deterministic_task():
    contract = parse_submit_objective_arguments(objective_arguments())
    title = _task_title(contract)
    body = _task_body('mgo_' + '1' * 32, json.dumps(
        contract.model_dump(), ensure_ascii=False, separators=(',', ':'), sort_keys=True,
    ))
    task = {
        'id': 'magi-' + '2' * 32,
        'title': title,
        'state': 'queued',
        'kind': None,
        'repo': 'Magistrate',
        'priority': None,
        'created': '2026-09-13',
        'closed': None,
        'deps': [],
        'hold': None,
        'links': [],
        'body': body,
        'blocked': False,
        'blocked_by': [],
        'held': False,
    }
    receipt = _validate_tasks_axi_receipt(
        json.dumps({'ok': True, 'action': 'add', 'task': task}).encode(),
        task_id=task['id'], title=title, project='Magistrate', body=body,
    )
    assert receipt.already_present is False
    duplicate = _validate_tasks_axi_receipt(
        json.dumps({'ok': True, 'action': 'add', 'already': True, 'task': task}).encode(),
        task_id=task['id'], title=title, project='Magistrate', body=body,
    )
    assert duplicate.already_present is True

    wrong = {**task, 'body': 'unrelated objective'}
    with pytest.raises(ObjectiveDispatchError):
        _validate_tasks_axi_receipt(
            json.dumps({'ok': True, 'action': 'add', 'already': True, 'task': wrong}).encode(),
            task_id=task['id'], title=title, project='Magistrate', body=body,
        )


class IntentRoutingModel:
    def __init__(
        self,
        *,
        unsafe_ack=False,
        force_tool=False,
        acknowledgement="I’ve accepted that objective. I’ll keep you updated here.",
    ):
        self.unsafe_ack = unsafe_ack
        self.force_tool = force_tool
        self.acknowledgement = acknowledgement
        self.calls = []

    async def complete(self, messages, *, system_context, request_id, tools=()):
        self.calls.append({
            'messages': list(messages), 'system_context': system_context,
            'request_id': request_id, 'tools': tuple(tools),
        })
        if messages[-1].role == 'tool':
            if self.unsafe_ack:
                result = json.loads(messages[-1].content)
                return MagiModelResult(
                    f"Firstmate task_id {result['task_id']} is completed.",
                )
            return MagiModelResult(self.acknowledgement)
        prompt = messages[-1].content
        if self.force_tool or (tools and prompt.startswith('Add a health endpoint')):
            return MagiModelResult(
                None,
                finish_reason='tool_calls',
                tool_calls=(MagiModelToolCall(
                    id='call_submit_health',
                    name=FIRSTMATE_SUBMIT_OBJECTIVE,
                    arguments_json=objective_arguments(),
                ),),
            )
        return MagiModelResult('Here is a direct conversational answer.')


@pytest.mark.asyncio
async def test_responses_provider_service_keeps_ordinary_chat_and_objective_delegation(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'responses-service.sqlite3'))
    dispatcher = FakeDispatcher()
    objective_store = ObjectiveSubmissionStore()
    tools = FirstmateObjectiveTools(store=objective_store, dispatcher=dispatcher)
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == '/v1/responses'
        assert body['instructions']
        if body.get('tools'):
            assert body['reasoning'] == {'effort': 'high'}
            assert 'messages' not in body
            assert body['tools'][0]['name'] == 'firstmate__submit_objective'
            assert set(body['tools'][0]) == {
                'type', 'name', 'description', 'parameters', 'strict',
            }
        if len(requests) == 1:
            return httpx.Response(200, json={
                'status': 'completed',
                'output': [{'type': 'message', 'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': 'Direct answer.'}]}],
            })
        if len(requests) == 2:
            return httpx.Response(200, json={
                'status': 'completed',
                'output': [{'type': 'function_call', 'call_id': 'call_objective_service',
                            'name': 'firstmate__submit_objective',
                            'arguments': objective_arguments()}],
            })
        assert body['input'][-1]['type'] == 'function_call_output'
        return httpx.Response(200, json={
            'status': 'completed',
            'output': [{'type': 'message', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': 'Accepted.'}]}],
        })

    model = OpenAIMagiModel(
        api_key='server-only-secret', model='gpt-5.6-sol',
        base_url='https://provider.invalid/v1', reasoning_effort='high',
        transport=httpx.MockTransport(handler),
    )
    service = MagiChatService(
        model, store=MagiChatStore(), profile_loader=lambda _: {}, tool_executor=tools,
    )
    ordinary = await service.submit(
        'operator-a', 'responses-question-0001', 'What does a health endpoint report?',
        allow_tools=True,
    )
    assert ordinary['status'] == 'completed'
    assert ordinary['assistant_message']['content'] == 'Direct answer.'
    assert dispatcher.calls == []
    assert requests[0]['reasoning'] == {'effort': 'high'}
    offered = requests[0]['tools'][0]
    assert offered['type'] == 'function'
    assert offered['name'] == 'firstmate__submit_objective'
    assert offered['strict'] is True
    assert 'function' not in offered

    objective = await service.submit(
        'operator-a', 'responses-objective-0001',
        'Add a health endpoint to Magistrate and test it.', allow_tools=True,
    )
    assert objective['status'] == 'completed'
    assert objective['assistant_message']['content'] == 'Accepted.'
    assert len(dispatcher.calls) == 1
    assert len(requests) == 3
    assert requests[1]['reasoning'] == {'effort': 'high'}


@pytest.mark.asyncio
async def test_native_chat_routes_actionable_intent_once_but_answers_questions_directly(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'native-objective.sqlite3'))
    dispatcher = FakeDispatcher()
    objective_store = ObjectiveSubmissionStore()
    tools = FirstmateObjectiveTools(store=objective_store, dispatcher=dispatcher)
    model = IntentRoutingModel()
    service = MagiChatService(
        model,
        store=MagiChatStore(),
        profile_loader=lambda _: {'name': 'Tester'},
        tool_executor=tools,
    )

    actionable = await service.submit(
        'operator-a', 'objective-client-0001',
        'Add a health endpoint to Magistrate and test it.',
        allow_tools=True,
    )
    assert actionable['status'] == 'completed'
    assert actionable['assistant_message']['content'] == (
        'I’ve accepted that objective. I’ll keep you updated here.'
    )
    assert len(dispatcher.calls) == 1
    assert len(model.calls) == 2
    assert [definition.name for definition in model.calls[0]['tools']] == [
        FIRSTMATE_SUBMIT_OBJECTIVE,
    ]
    assert model.calls[1]['tools'] == ()
    assert [message.role for message in model.calls[1]['messages'][-2:]] == ['assistant', 'tool']
    tool_result = json.loads(model.calls[1]['messages'][-1].content)
    objective = objective_store.get('operator-a', tool_result['objective_id'])
    assert objective is not None and objective['status'] == 'accepted'
    assert objective['user_message_id'] == actionable['user_message']['id']

    replay = await service.submit(
        'operator-a', 'objective-client-0001',
        'Add a health endpoint to Magistrate and test it.',
        allow_tools=True,
    )
    assert replay['duplicate'] is True
    assert replay['messages'] == actionable['messages']
    assert len(dispatcher.calls) == 1
    assert len(model.calls) == 2

    question = await service.submit(
        'operator-a', 'question-client-0001',
        'What does a health endpoint normally report?',
        allow_tools=True,
    )
    assert question['assistant_message']['content'] == 'Here is a direct conversational answer.'
    assert len(dispatcher.calls) == 1
    assert len(model.calls) == 3
    diagnostics = await service.diagnostics('operator-a')
    assert diagnostics['magi_tool_calls'] == 1


@pytest.mark.asyncio
async def test_chat_never_offers_tool_without_command_authority_or_executes_unsolicited_call(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'native-objective-auth.sqlite3'))
    dispatcher = FakeDispatcher()
    model = IntentRoutingModel(force_tool=True)
    service = MagiChatService(
        model,
        store=MagiChatStore(),
        profile_loader=lambda _: {},
        tool_executor=FirstmateObjectiveTools(
            store=ObjectiveSubmissionStore(), dispatcher=dispatcher,
        ),
    )
    result = await service.submit(
        'voice-only', 'objective-auth-0001',
        'Add a health endpoint to Magistrate and test it.',
        allow_tools=False,
    )
    assert result['status'] == 'failed'
    assert model.calls[0]['tools'] == ()
    assert dispatcher.calls == []
    diagnostics = await service.diagnostics('voice-only')
    assert diagnostics['magi_tool_calls'] == 1


@pytest.mark.asyncio
async def test_native_chat_preserves_the_models_immediate_user_language_acknowledgement(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'native-objective-language.sqlite3'))
    dispatcher = FakeDispatcher()
    acknowledgement = 'He aceptado ese objetivo. Te mantendré al tanto aquí.'
    service = MagiChatService(
        IntentRoutingModel(force_tool=True, acknowledgement=acknowledgement),
        store=MagiChatStore(),
        profile_loader=lambda _: {},
        tool_executor=FirstmateObjectiveTools(
            store=ObjectiveSubmissionStore(), dispatcher=dispatcher,
        ),
    )
    result = await service.submit(
        'operator-a', 'objective-language-0001',
        'Añade un endpoint de salud a Magistrate y pruébalo.',
        allow_tools=True,
    )
    assert result['status'] == 'completed'
    assert result['assistant_message']['content'] == acknowledgement
    assert len(dispatcher.calls) == 1


@pytest.mark.asyncio
async def test_accepted_objective_uses_truthful_ack_fallback_instead_of_internal_or_done_claim(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'native-objective-ack.sqlite3'))
    dispatcher = FakeDispatcher()
    service = MagiChatService(
        IntentRoutingModel(unsafe_ack=True),
        store=MagiChatStore(),
        profile_loader=lambda _: {},
        tool_executor=FirstmateObjectiveTools(
            store=ObjectiveSubmissionStore(), dispatcher=dispatcher,
        ),
    )
    result = await service.submit(
        'operator-a', 'objective-ack-0001',
        'Add a health endpoint to Magistrate and test it.',
        allow_tools=True,
    )
    text = result['assistant_message']['content']
    assert text == "I've accepted that objective. I'll keep you updated here as the work progresses."
    assert 'Firstmate' not in text
    assert 'task_id' not in text
    assert 'completed' not in text
    assert len(dispatcher.calls) == 1


@pytest.mark.asyncio
async def test_failed_queue_acceptance_retries_same_objective_and_task_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'native-objective-retry.sqlite3'))
    dispatcher = FakeDispatcher(fail_count=1)
    model = IntentRoutingModel()
    objective_store = ObjectiveSubmissionStore()
    service = MagiChatService(
        model,
        store=MagiChatStore(),
        profile_loader=lambda _: {},
        tool_executor=FirstmateObjectiveTools(
            store=objective_store, dispatcher=dispatcher,
        ),
    )
    failed = await service.submit(
        'operator-a', 'objective-retry-0001',
        'Add a health endpoint to Magistrate and test it.',
        allow_tools=True,
    )
    assert failed['status'] == 'failed'
    retried = await service.submit(
        'operator-a', 'objective-retry-0001',
        'Add a health endpoint to Magistrate and test it.',
        retry_failed=True,
        allow_tools=True,
    )
    assert retried['status'] == 'completed'
    assert retried['user_message']['id'] == failed['user_message']['id']
    assert retried['assistant_message']['id'] == failed['assistant_message']['id']
    assert len(dispatcher.calls) == 2
    assert dispatcher.calls[0]['task_id'] == dispatcher.calls[1]['task_id']
    assert dispatcher.calls[0]['body'] == dispatcher.calls[1]['body']
    assert len({call['request_id'] for call in model.calls}) == len(model.calls)
