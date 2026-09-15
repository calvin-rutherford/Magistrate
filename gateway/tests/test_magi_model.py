import json

import httpx
import pytest

from app.magi_chat_api import validate_magi_chat_configuration
from app.magi_model import (
    MAGI_MAX_RESPONSE_CHARACTERS,
    MagiModelError,
    MagiModelMessage,
    MagiModelToolCall,
    MagiToolDefinition,
    OpenAIMagiModel,
)


@pytest.mark.asyncio
async def test_openai_model_returns_exact_complete_text_without_logging_or_normalization():
    expected = "# Heading\n\n1. café\n2. 東京\n\n```py\nprint('🚀')\n```\n"

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == '/v1/responses'
        assert body['stream'] is False
        assert body['instructions'] == 'system'
        assert body['input'][-1] == {'role': 'user', 'content': 'hello'}
        assert 'reasoning' not in body
        assert request.headers['idempotency-key'] == 'safe-request-id'
        return httpx.Response(200, json={
            'status': 'completed',
            'output': [{'type': 'message', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': expected}]}],
        })

    model = OpenAIMagiModel(
        api_key='server-only-secret', model='test-model', base_url='https://provider.invalid/v1',
        transport=httpx.MockTransport(handler),
    )
    result = await model.complete(
        [MagiModelMessage('user', 'hello')], system_context='system', request_id='safe-request-id',
    )
    assert result.text.encode('utf-8') == expected.encode('utf-8')


@pytest.mark.asyncio
async def test_openai_model_round_trips_one_closed_tool_call_and_result_message():
    requests = []
    arguments = json.dumps({
        'objective': 'Add a health endpoint and test it.',
        'project': 'Magistrate',
        'constraints': [],
        'acceptance_criteria': ['The endpoint is covered by a passing test.'],
        'context_refs': [],
    }, separators=(',', ':'))

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append((request.headers['idempotency-key'], body))
        assert request.url.path == '/v1/responses'
        if len(requests) == 1:
            assert body['tool_choice'] == 'auto'
            assert body['parallel_tool_calls'] is False
            assert body['tools'] == [{
                'type': 'function', 'name': 'firstmate__submit_objective',
                'description': 'Submit one objective.',
                'parameters': {
                    'type': 'object', 'additionalProperties': False,
                    'properties': {'objective': {'type': 'string'}},
                    'required': ['objective'],
                },
                'strict': True,
            }]
            return httpx.Response(200, json={
                'status': 'completed',
                'output': [{
                    'type': 'function_call', 'id': 'fc_objective_1',
                    'call_id': 'call_objective_1',
                    'name': 'firstmate__submit_objective', 'arguments': arguments,
                }],
            })
        assert 'tools' not in body
        assert body['input'][-2:] == [
            {'type': 'function_call', 'call_id': 'call_objective_1',
             'name': 'firstmate__submit_objective', 'arguments': arguments},
            {'type': 'function_call_output', 'call_id': 'call_objective_1',
             'output': '{"status":"accepted"}'},
        ]
        return httpx.Response(200, json={
            'status': 'completed',
            'output': [{'type': 'message', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': 'I accepted it.'}]}],
        })

    tool = MagiToolDefinition(
        name='firstmate.submit_objective',
        description='Submit one objective.',
        parameters={
            'type': 'object', 'additionalProperties': False,
            'properties': {'objective': {'type': 'string'}},
            'required': ['objective'],
        },
    )
    model = OpenAIMagiModel(
        api_key='server-only-secret', model='test-model',
        base_url='https://provider.invalid/v1', transport=httpx.MockTransport(handler),
    )
    first = await model.complete(
        [MagiModelMessage('user', 'Please add it.')],
        system_context='system', request_id='tool-request-1', tools=[tool],
    )
    assert first.finish_reason == 'tool_calls'
    assert first.tool_calls == (
        MagiModelToolCall(
            id='call_objective_1', name='firstmate.submit_objective',
            arguments_json=arguments,
        ),
    )
    final = await model.complete(
        [
            MagiModelMessage('user', 'Please add it.'),
            MagiModelMessage('assistant', None, tool_calls=first.tool_calls),
            MagiModelMessage('tool', '{"status":"accepted"}', tool_call_id='call_objective_1'),
        ],
        system_context='system', request_id='tool-request-2',
    )
    assert final.text == 'I accepted it.'
    assert [item[0] for item in requests] == ['tool-request-1', 'tool-request-2']


def test_enabled_production_native_chat_requires_a_server_provider_key(monkeypatch):
    monkeypatch.setenv('MAGISTRATE_ENV', 'production')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    with pytest.raises(RuntimeError):
        validate_magi_chat_configuration()


def test_openai_model_rejects_an_insecure_or_credentialed_provider_url():
    with pytest.raises(RuntimeError):
        OpenAIMagiModel(api_key='secret', base_url='http://provider.invalid/v1')
    with pytest.raises(RuntimeError):
        OpenAIMagiModel(api_key='secret', base_url='https://user:password@provider.invalid/v1')


@pytest.mark.asyncio
@pytest.mark.parametrize(('payload', 'code'), [
    ({'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'},
      'output': []}, 'response_limit_reached'),
    ({'status': 'completed', 'output': [{'type': 'function_call'}]},
      'provider_tool_call_unsupported'),
    ({'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
      'content': [{'type': 'output_text', 'text': 'unsafe\u001b[31m'}]}]},
      'provider_unsafe_response'),
])
async def test_openai_model_rejects_incomplete_or_tool_results(payload, code):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    model = OpenAIMagiModel(
        api_key='server-only-secret', model='test-model',
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(MagiModelError) as raised:
        await model.complete(
            [MagiModelMessage('user', 'hello')], system_context='system', request_id='safe-request-id',
        )
    assert raised.value.code == code


@pytest.mark.asyncio
async def test_responses_provider_redacts_provider_errors_and_rejects_oversized_text():
    async def error_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/v1/responses'
        return httpx.Response(400, json={
            'error': {'message': 'secret prompt and reasoning_effort incompatibility'},
        })

    model = OpenAIMagiModel(
        api_key='server-only-secret', model='gpt-5.6-sol',
        base_url='https://provider.invalid/v1', transport=httpx.MockTransport(error_handler),
    )
    with pytest.raises(MagiModelError) as raised:
        await model.complete(
            [MagiModelMessage('user', 'hello')], system_context='system', request_id='error-id',
        )
    assert raised.value.code == 'provider_rejected_request'
    assert str(raised.value) == 'provider_rejected_request'

    oversized = 'x' * (MAGI_MAX_RESPONSE_CHARACTERS + 1)

    async def oversized_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            'status': 'completed',
            'output': [{'type': 'message', 'role': 'assistant',
                        'content': [{'type': 'output_text', 'text': oversized}]}],
        })

    bounded_model = OpenAIMagiModel(
        api_key='server-only-secret', model='gpt-5.6-sol',
        base_url='https://provider.invalid/v1', transport=httpx.MockTransport(oversized_handler),
    )
    with pytest.raises(MagiModelError) as oversized_error:
        await bounded_model.complete(
            [MagiModelMessage('user', 'hello')], system_context='system', request_id='large-id',
        )
    assert oversized_error.value.code == 'response_too_large'
