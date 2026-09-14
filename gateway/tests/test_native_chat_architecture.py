"""Executable contract for the sole supported human-conversation transport."""
from fastapi.routing import APIWebSocketRoute

import app.herdr_client as herdr
from app.magi_chat_api import magi_chat_readiness
from app.main import app


def route_methods() -> set[tuple[str, str]]:
    routes = list(app.routes)
    for included in app.routes:
        router = getattr(included, "original_router", None)
        if router is not None:
            routes.extend(router.routes)
    return {
        (method, route.path)
        for route in routes
        if hasattr(route, "path")
        for method in (getattr(route, "methods", None) or set())
    } | {
        ("WEBSOCKET", route.path)
        for route in routes
        if isinstance(route, APIWebSocketRoute)
    }


def test_native_magi_is_the_only_registered_human_conversation_api():
    routes = route_methods()
    assert {
        ("POST", "/api/v1/magi/messages"),
        ("GET", "/api/v1/magi/conversations/current"),
        ("GET", "/api/v1/magi/conversations/{conversation_id}"),
        ("GET", "/api/v1/magi/conversations/{conversation_id}/replay"),
        ("POST", "/api/v1/magi/messages/{client_message_id}/cancel"),
        ("WEBSOCKET", "/api/v1/events"),
    } <= routes
    retired_paths = {
        "/api/v1/captain/prompt",
        "/api/v1/captain/output",
        "/api/v1/input",
        "/api/v1/voice/moves",
        "/api/v1/agents/{agent_id}/history",
        "/api/v1/conversations/{target}/messages",
        "/api/v1/conversations/{target}/replay",
        "/api/v1/conversations/{target}/events",
        "/api/v1/conversations/{target}/reset",
    }
    assert not ({path for _, path in routes} & retired_paths)

    assert magi_chat_readiness()["enabled"] is True


def test_terminal_conversation_capabilities_are_retired():
    assert not hasattr(herdr, "parse_agent_history")
    assert not hasattr(herdr.HerdrClient, "get_agent_history")
    assert not hasattr(herdr.HerdrClient, "read_agent_output")
    assert not hasattr(herdr.HerdrClient, "prompt_agent")


def test_structured_execution_and_non_chat_agent_controls_remain_registered():
    routes = route_methods()
    assert {
        ("POST", "/api/v1/firstmate/execution-events"),
        ("POST", "/api/v1/firstmate/decision-events"),
        ("GET", "/api/v1/activity"),
        ("GET", "/api/v1/activity/snapshot"),
        ("GET", "/api/v1/activity/replay"),
        ("POST", "/api/v1/activity/catch-up"),
        ("GET", "/api/v1/attention/unified"),
        ("POST", "/api/v1/attention/actions/{action_key}/prepare"),
        ("POST", "/api/v1/attention/actions/{action_key}/execute"),
        ("GET", "/api/v1/fleet"),
        ("GET", "/api/v1/notifications/events"),
        ("POST", "/api/v1/agents/{agent_id}/send-key"),
        ("POST", "/api/v1/agents/{agent_id}/interrupt"),
        ("POST", "/api/v1/agents/{agent_id}/rename"),
        ("WEBSOCKET", "/ws/ar-interface"),
    } <= routes
