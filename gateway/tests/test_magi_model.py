import json

import httpx
import pytest

from app.magi_chat_api import validate_magi_chat_configuration
from app.magi_model import MagiModelError, MagiModelMessage, OpenAIMagiModel


@pytest.mark.asyncio
async def test_openai_model_returns_exact_complete_text_without_logging_or_normalization():
    expected = "# Heading\n\n1. café\n2. 東京\n\n```py\nprint('🚀')\n```\n"

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body['stream'] is False
        assert body['messages'][-1] == {'role': 'user', 'content': 'hello'}
        assert request.headers['idempotency-key'] == 'safe-request-id'
        return httpx.Response(200, json={
            'choices': [{'finish_reason': 'stop', 'message': {'content': expected}}],
        })

    model = OpenAIMagiModel(
        api_key='server-only-secret', model='test-model', base_url='https://provider.invalid/v1',
        transport=httpx.MockTransport(handler),
    )
    result = await model.complete(
        [MagiModelMessage('user', 'hello')], system_context='system', request_id='safe-request-id',
    )
    assert result.text.encode('utf-8') == expected.encode('utf-8')


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
    ({'choices': [{'finish_reason': 'length', 'message': {'content': 'partial'}}]}, 'response_limit_reached'),
    ({'choices': [{'finish_reason': 'tool_calls', 'message': {'content': None, 'tool_calls': [{'id': 'x'}]}}]}, 'provider_tool_call_unsupported'),
    ({'choices': [{'finish_reason': 'stop', 'message': {'content': 'unsafe\u001b[31m'}}]}, 'provider_unsafe_response'),
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
