import json
from decimal import Decimal
import sqlite3

import httpx
import pytest

from app import db
from app.execution_routing import (
    CheapestReliableHarnessStrategy,
    ExecutionRequirements,
    HarnessRoute,
    select_execution_profile,
)
from app.magi_chat_service import MagiChatService
from app.magi_chat_store import MagiChatStore
from app.magi_model import (
    MagiModelError,
    MagiModelMessage,
    MagiModelResult,
    MagiModelToolCall,
    MagiModelUsage,
    MagiToolDefinition,
)
from app.magi_providers import AnthropicMagiModel, GoogleMagiModel
from app.magi_routing import (
    ModelRouteContext,
    ModelRouteStore,
    RouteCategory,
    RoutedMagiModel,
    classify_turn,
    validate_routing_config,
)


def route_config(models, **policy):
    return {
        'schema_version': 'magi.model-routing.v1',
        'policy': {
            'monthly_budget_usd': policy.get('monthly_budget_usd', '10'),
            'per_request_budget_usd': policy.get('per_request_budget_usd', '2'),
            'output_reservation_tokens': policy.get('output_reservation_tokens', 100),
            'min_reliability': policy.get('min_reliability', '0.90'),
            'max_same_model_retries': policy.get('max_same_model_retries', 0),
            'max_automatic_fallback_cost_usd': policy.get('max_automatic_fallback_cost_usd', '1'),
        },
        'models': models,
    }


def candidate(identifier, provider, *, input_price='1', output_price='1', reasoning=5,
              tools=True, multimodal=True, reliability='0.99', context=100000,
              safety_tier=5, billing_mode='metered'):
    return {
        'id': identifier, 'provider': provider, 'model': identifier.split(':')[-1],
        'credential_env': f'{provider.upper().replace("-", "_")}_TEST_KEY',
        'capability_class': 'general',
        'capabilities': {
            'reasoning': reasoning, 'context_tokens': context,
            'tools': tools, 'multimodal': multimodal,
        },
        'reliability': reliability, 'latency_ms': 100,
        'safety_tier': safety_tier, 'billing_mode': billing_mode,
        'pricing_usd_per_million_tokens': {
            'input': input_price, 'cached_input': input_price, 'output': output_price,
        },
    }


class FakeProvider:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def complete(self, messages, *, system_context, request_id, tools=()):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_router_selects_cheapest_reliably_capable_and_records_actual_cost(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'routing.sqlite3'))
    catalog = validate_routing_config(route_config([
        candidate('openai:expensive', 'openai', input_price='10', output_price='10'),
        candidate('google:cheap', 'google', input_price='1', output_price='2'),
    ]))
    cheap = FakeProvider([MagiModelResult('ok', usage=MagiModelUsage(1000, 200, 100))])
    expensive = FakeProvider([MagiModelResult('wrong')])
    router = RoutedMagiModel(
        catalog, store=ModelRouteStore(),
        providers={'google:cheap': cheap, 'openai:expensive': expensive},
    )
    result = await router.complete_routed(
        [MagiModelMessage('user', 'hello')], system_context='system', request_id='route-1',
        route_context=ModelRouteContext(
            owner_user_id='owner', category=RouteCategory.EXECUTION,
            permission_granted=True, requires_tools=True,
        ),
    )
    assert result.text == 'ok'
    assert cheap.calls == 1 and expensive.calls == 0
    [record] = router.store.summary('owner')['routes']
    assert record['provider'] == 'google'
    assert record['model'] == 'cheap'
    assert record['route_category'] == 'EXECUTION'
    assert record['input_tokens'] == 1000
    # Cached input uses the configured $1/M rate: 1000 input + 200 output.
    assert record['actual_cost_usd'] == '0.001400'


@pytest.mark.asyncio
async def test_route_respects_credit_allocation_and_subscription_plan(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'credits.sqlite3'))
    exhausted = candidate('openai:credits', 'openai', input_price='0.1', output_price='0.1')
    exhausted['billing_mode'] = 'credits'
    exhausted['credits_remaining_usd'] = '0'
    subscription = candidate(
        'anthropic:plan', 'anthropic', input_price='10', output_price='10',
        billing_mode='subscription',
    )
    catalog = validate_routing_config(route_config([exhausted, subscription]))
    credit_provider = FakeProvider([MagiModelResult('must not run')])
    plan_provider = FakeProvider([MagiModelResult('plan', usage=MagiModelUsage(1, 1))])
    router = RoutedMagiModel(catalog, providers={
        'openai:credits': credit_provider, 'anthropic:plan': plan_provider,
    })
    result = await router.complete_routed(
        [MagiModelMessage('user', 'hello')], system_context='system', request_id='route-plan',
        route_context=ModelRouteContext(
            owner_user_id='owner', category=RouteCategory.DIRECT_CONVERSATION,
            permission_granted=True,
        ),
    )
    assert result.text == 'plan'
    assert credit_provider.calls == 0 and plan_provider.calls == 1


@pytest.mark.asyncio
async def test_failover_is_same_class_then_alternate_provider_and_is_audited(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'fallback.sqlite3'))
    catalog = validate_routing_config(route_config([
        candidate('openai:primary', 'openai', input_price='0.1', output_price='0.1'),
        candidate('anthropic:fallback', 'anthropic', input_price='0.2', output_price='0.2'),
    ]))
    primary = FakeProvider([MagiModelError('provider_unavailable')])
    fallback = FakeProvider([MagiModelResult('fallback', usage=MagiModelUsage(10, 5))])
    router = RoutedMagiModel(catalog, providers={
        'openai:primary': primary, 'anthropic:fallback': fallback,
    })
    result = await router.complete_routed(
        [MagiModelMessage('user', 'fix it')], system_context='system', request_id='route-2',
        route_context=ModelRouteContext(
            owner_user_id='owner', category=RouteCategory.EXECUTION,
            permission_granted=True, requires_tools=True,
        ),
    )
    assert result.text == 'fallback'
    records = list(reversed(router.store.summary('owner')['routes']))
    assert [(item['provider'], item['state']) for item in records] == [
        ('openai', 'failed'), ('anthropic', 'succeeded'),
    ]
    assert records[1]['fallback_from'] == 'openai:primary'


@pytest.mark.asyncio
async def test_router_never_fails_over_after_observing_tool_selection(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'tool-stop.sqlite3'))
    catalog = validate_routing_config(route_config([
        candidate('openai:primary', 'openai', input_price='0.5', output_price='0.5'),
        candidate('google:fallback', 'google'),
    ]))
    primary = FakeProvider([MagiModelError(
        'provider_invalid_tool_call', retryable=True, tool_calls=1,
    )])
    fallback = FakeProvider([MagiModelResult('must not run')])
    router = RoutedMagiModel(catalog, providers={
        'openai:primary': primary, 'google:fallback': fallback,
    })
    with pytest.raises(MagiModelError, match='provider_invalid_tool_call'):
        await router.complete_routed(
            [MagiModelMessage('user', 'do it')], system_context='system', request_id='route-3',
            tools=[MagiToolDefinition('host.tool', 'One tool.', {
                'type': 'object', 'additionalProperties': False, 'properties': {}, 'required': [],
            })],
            route_context=ModelRouteContext(
                owner_user_id='owner', category=RouteCategory.EXECUTION,
                permission_granted=True, requires_tools=True,
            ),
        )
    assert primary.calls == 1 and fallback.calls == 0


@pytest.mark.asyncio
async def test_material_fallback_pauses_until_explicit_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'material.sqlite3'))
    catalog = validate_routing_config(route_config([
        candidate('openai:primary', 'openai', input_price='0.01', output_price='0.01'),
        candidate('anthropic:costly', 'anthropic', input_price='1000', output_price='1000'),
    ], max_automatic_fallback_cost_usd='0.01'))
    router = RoutedMagiModel(catalog, providers={
        'openai:primary': FakeProvider([MagiModelError('provider_unavailable')]),
        'anthropic:costly': FakeProvider([MagiModelResult('costly')]),
    })
    with pytest.raises(MagiModelError, match='route_fallback_confirmation_required'):
        await router.complete_routed(
            [MagiModelMessage('user', 'do it')], system_context='system', request_id='route-4',
            route_context=ModelRouteContext(
                owner_user_id='owner', category=RouteCategory.EXECUTION,
                permission_granted=True, requires_tools=True,
            ),
        )


@pytest.mark.asyncio
async def test_budget_reservation_blocks_paid_call_before_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'budget.sqlite3'))
    catalog = validate_routing_config(route_config([
        candidate('openai:model', 'openai', input_price='1000', output_price='1000'),
    ], monthly_budget_usd='0.001', per_request_budget_usd='2'))
    provider = FakeProvider([MagiModelResult('must not run')])
    router = RoutedMagiModel(catalog, providers={'openai:model': provider})
    with pytest.raises(MagiModelError, match='model_budget_exhausted'):
        await router.complete_routed(
            [MagiModelMessage('user', 'hello')], system_context='system', request_id='route-5',
            route_context=ModelRouteContext(
                owner_user_id='owner', category=RouteCategory.DIRECT_CONVERSATION,
                permission_granted=True,
            ),
        )
    assert provider.calls == 0


def test_capability_and_configuration_validation_fail_closed():
    config = route_config([candidate(
        'openai:small', 'openai', reasoning=1, tools=False, multimodal=False,
    )])
    catalog = validate_routing_config(config)
    router = RoutedMagiModel(catalog, providers={'openai:small': FakeProvider([])})
    assert router._eligible(ModelRouteContext(
        owner_user_id='owner', category=RouteCategory.EXECUTION,
        permission_granted=True, requires_tools=True,
    ), 10) == []
    config['models'][0]['pricing_usd_per_million_tokens']['input'] = '-1'
    with pytest.raises(ValueError, match='non-negative'):
        validate_routing_config(config)


def test_attachment_route_excludes_models_without_multimodal_capability():
    catalog = validate_routing_config(route_config([
        candidate('openai:text-only', 'openai', input_price='0.01', multimodal=False),
        candidate('google:file-capable', 'google', input_price='1', multimodal=True),
    ]))
    providers = {
        'openai:text-only': FakeProvider([]),
        'google:file-capable': FakeProvider([]),
    }
    router = RoutedMagiModel(catalog, providers=providers)
    eligible = router._eligible(ModelRouteContext(
        owner_user_id='owner', category=RouteCategory.DIRECT_CONVERSATION,
        permission_granted=True, requires_multimodal=True,
    ), 10)
    assert [item[0].id for item in eligible] == ['google:file-capable']


def test_turn_categories_require_bound_decisions_and_mark_high_impact():
    assert classify_turn('Hello there', command_authorized=True) == RouteCategory.DIRECT_CONVERSATION
    assert classify_turn('Investigate the flaky test', command_authorized=True) == RouteCategory.READ_ONLY_INVESTIGATION
    assert classify_turn('Fix the flaky test', command_authorized=True) == RouteCategory.EXECUTION
    assert classify_turn('Deploy this to production', command_authorized=True) == RouteCategory.HIGH_IMPACT_ACTION
    assert classify_turn('yes', command_authorized=True) == RouteCategory.DIRECT_CONVERSATION
    assert classify_turn('yes', command_authorized=True, decision_bound=True) == RouteCategory.DECISION_RESPONSE


@pytest.mark.asyncio
async def test_unconfirmed_high_impact_turn_makes_no_model_or_objective_call(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'high-impact.sqlite3'))

    class ForbiddenModel:
        async def complete(self, *args, **kwargs):
            raise AssertionError('unconfirmed high-impact request reached provider')

    service = MagiChatService(
        ForbiddenModel(), store=MagiChatStore(), profile_loader=lambda _: {},
        tool_executor=None,
    )
    result = await service.submit(
        'owner', 'high-impact-client-1', 'Deploy this to production now.',
        allow_tools=True,
    )
    assert result['status'] == 'completed'
    assert 'explicit confirmation' in result['assistant_message']['content']
    with sqlite3.connect(db.DB_PATH) as connection:
        assert connection.execute('SELECT COUNT(*) FROM magi_objective_submissions').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM magi_model_routes').fetchone()[0] == 0
        assert connection.execute(
            'SELECT route_category, outcome FROM magi_turn_routes'
        ).fetchone() == ('HIGH_IMPACT_ACTION', 'confirmation-required')


@pytest.mark.asyncio
async def test_future_provider_factory_replaces_provider_without_router_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'replacement.sqlite3'))
    monkeypatch.setenv('CUSTOM_LOCAL_TEST_KEY', 'configured')
    catalog = validate_routing_config(route_config([
        candidate('custom-local:v1', 'custom-local'),
    ]))
    created = []

    def factory(selected):
        created.append(selected.id)
        return FakeProvider([MagiModelResult('replacement', usage=MagiModelUsage(1, 1))])

    router = RoutedMagiModel(catalog, provider_factories={'custom-local': factory})
    result = await router.complete_routed(
        [MagiModelMessage('user', 'hello')], system_context='system', request_id='route-6',
        route_context=ModelRouteContext(
            owner_user_id='owner', category=RouteCategory.DIRECT_CONVERSATION,
            permission_granted=True,
        ),
    )
    assert result.text == 'replacement'
    assert created == ['custom-local:v1']


@pytest.mark.asyncio
async def test_anthropic_adapter_translates_closed_tool_contract_and_usage():
    async def handler(request):
        body = json.loads(request.content)
        assert request.url.path == '/v1/messages'
        assert body['tool_choice'] == {'type': 'any', 'disable_parallel_tool_use': True}
        assert body['tools'][0]['name'] == 'host__submit'
        return httpx.Response(200, json={
            'stop_reason': 'tool_use',
            'content': [{'type': 'thinking', 'thinking': 'private'}, {
                'type': 'tool_use', 'id': 'call_1', 'name': 'host__submit', 'input': {'x': 1},
            }],
            'usage': {'input_tokens': 12, 'output_tokens': 3},
        })

    model = AnthropicMagiModel(
        api_key='secret', model='claude-test', base_url='https://provider.invalid/v1',
        transport=httpx.MockTransport(handler),
    )
    result = await model.complete(
        [MagiModelMessage('user', 'do it')], system_context='system', request_id='anthropic-1',
        tools=[MagiToolDefinition('host.submit', 'Submit.', {
            'type': 'object', 'properties': {'x': {'type': 'integer'}},
            'required': ['x'], 'additionalProperties': False,
        })],
    )
    assert result.tool_calls == (MagiModelToolCall('call_1', 'host.submit', '{"x":1}'),)
    assert result.usage == MagiModelUsage(12, 3)


@pytest.mark.asyncio
async def test_google_adapter_keeps_api_key_out_of_url_and_discards_private_blocks():
    async def handler(request):
        assert 'secret' not in str(request.url)
        assert request.headers['x-goog-api-key'] == 'secret'
        body = json.loads(request.content)
        assert body['systemInstruction']['parts'][0]['text'] == 'system'
        return httpx.Response(200, json={
            'candidates': [{
                'finishReason': 'STOP',
                'content': {'parts': [
                    {'thoughtSignature': 'private-provider-data'}, {'text': 'Visible answer.'},
                ]},
            }],
            'usageMetadata': {'promptTokenCount': 8, 'candidatesTokenCount': 2},
        })

    model = GoogleMagiModel(
        api_key='secret', model='gemini-test', base_url='https://provider.invalid/v1',
        transport=httpx.MockTransport(handler),
    )
    result = await model.complete(
        [MagiModelMessage('user', 'hello')], system_context='system', request_id='google-1',
    )
    assert result.text == 'Visible answer.'
    assert result.usage == MagiModelUsage(8, 2)


def test_harness_strategy_is_capability_cost_aware_and_replaceable():
    profiles = [{
        'id': 'pi:cheap', 'verified': True, 'available': True,
        'harness': {'id': 'pi'}, 'provider': {'id': 'google'},
        'model': {'id': 'gemini'}, 'variant': 'default',
        'routing': {
            'reasoning': 4, 'context_tokens': 100000, 'tools': True,
            'multimodal': False, 'reliability': '0.99', 'latency_ms': 100,
            'estimated_cost_usd': '0.010000',
        },
    }, {
        'id': 'codex:costly', 'verified': True, 'available': True,
        'harness': {'id': 'codex'}, 'provider': {'id': 'openai'},
        'model': {'id': 'gpt'}, 'variant': 'default',
        'routing': {
            'reasoning': 5, 'context_tokens': 100000, 'tools': True,
            'multimodal': True, 'reliability': '0.99', 'latency_ms': 120,
            'estimated_cost_usd': '0.100000',
        },
    }]
    selected = select_execution_profile(profiles, ExecutionRequirements(reasoning=3))
    assert selected.profile_id == 'pi:cheap'
    multimodal = CheapestReliableHarnessStrategy().select(
        profiles, ExecutionRequirements(reasoning=3, multimodal=True),
    )
    assert multimodal.profile_id == 'codex:costly'

    class ReplacementStrategy:
        def select(self, profiles, requirements):
            return HarnessRoute('external:v1', 'external', 'future', 'm', 'v', '0.000000', 'replacement')

    replaced = select_execution_profile(profiles, ExecutionRequirements(), strategy=ReplacementStrategy())
    assert replaced.reason == 'replacement'
