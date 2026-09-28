"""Repository-controlled model routing, metering, budgets, and failover.

The router sees only bounded request metadata and canonical model messages. It
persists no prompt, response, tool arguments, or reasoning. Provider and model
selection is therefore auditable without creating a second transcript.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from enum import StrEnum
import json
import os
import re
import secrets
import sqlite3
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from app import db
from app.magi_model import (
    MagiModel,
    MagiModelError,
    MagiModelMessage,
    MagiModelResult,
    MagiModelUsage,
    MagiToolDefinition,
    OpenAIMagiModel,
)
from app.magi_providers import AnthropicMagiModel, GoogleMagiModel
from app.persistence import connect

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_ROUTE_SCHEMA = "magi.model-routing.v1"


def _default_routing_config() -> dict[str, Any]:
    """Repository-controlled defaults, validated through the public schema."""
    return {
        "schema_version": _ROUTE_SCHEMA,
        "policy": {
            "monthly_budget_usd": "100.00",
            "per_request_budget_usd": "2.00",
            "output_reservation_tokens": 4096,
            "min_reliability": "0.95",
            "max_same_model_retries": 0,
            "max_automatic_fallback_cost_usd": "0.05",
        },
        "models": [
            {
                "id": "openai:gpt-4.1-mini", "provider": "openai",
                "model": "gpt-4.1-mini", "credential_env": "OPENAI_API_KEY",
                "capability_class": "general-tools",
                "capabilities": {
                    "reasoning": 4, "context_tokens": 1_048_576,
                    "tools": True, "multimodal": True,
                },
                "reliability": "0.99", "latency_ms": 1800, "safety_tier": 4,
                "billing_mode": "metered",
                "pricing_usd_per_million_tokens": {
                    "input": "0.40", "cached_input": "0.10", "output": "1.60",
                },
            },
            {
                "id": "google:gemini-2.0-flash", "provider": "google",
                "model": "gemini-2.0-flash", "credential_env": "GOOGLE_API_KEY",
                "capability_class": "general-tools",
                "capabilities": {
                    "reasoning": 4, "context_tokens": 1_048_576,
                    "tools": True, "multimodal": True,
                },
                "reliability": "0.98", "latency_ms": 1400, "safety_tier": 4,
                "billing_mode": "metered",
                "pricing_usd_per_million_tokens": {
                    "input": "0.10", "cached_input": "0.025", "output": "0.40",
                },
            },
            {
                "id": "anthropic:claude-3-5-haiku", "provider": "anthropic",
                "model": "claude-3-5-haiku-latest", "credential_env": "ANTHROPIC_API_KEY",
                "capability_class": "general-tools",
                "capabilities": {
                    "reasoning": 4, "context_tokens": 200_000,
                    "tools": True, "multimodal": True,
                },
                "reliability": "0.99", "latency_ms": 1600, "safety_tier": 4,
                "billing_mode": "metered",
                "pricing_usd_per_million_tokens": {
                    "input": "0.80", "cached_input": "0.08", "output": "4.00",
                },
            },
        ],
    }


class RouteCategory(StrEnum):
    DIRECT_CONVERSATION = "DIRECT_CONVERSATION"
    READ_ONLY_INVESTIGATION = "READ_ONLY_INVESTIGATION"
    EXECUTION = "EXECUTION"
    DECISION_RESPONSE = "DECISION_RESPONSE"
    HIGH_IMPACT_ACTION = "HIGH_IMPACT_ACTION"


@dataclass(frozen=True)
class ModelRouteContext:
    owner_user_id: str
    category: RouteCategory
    permission_granted: bool
    explicit_confirmation: bool = False
    context_tokens: int = 0
    requires_tools: bool = False
    requires_multimodal: bool = False
    preferred_provider: str | None = None
    preferred_model: str | None = None


@dataclass(frozen=True)
class ModelCandidate:
    id: str
    provider: str
    model: str
    credential_env: str
    base_url: str | None
    capability_class: str
    reasoning: int
    context_tokens: int
    tools: bool
    multimodal: bool
    reliability: Decimal
    latency_ms: int
    input_micro_usd_per_million: int
    output_micro_usd_per_million: int
    cached_input_micro_usd_per_million: int
    billing_mode: str
    plan: str | None
    credits_micro_usd: int | None
    enabled: bool
    available: bool
    safety_tier: int
    max_output_tokens: int

    def estimated_cost(self, input_tokens: int, output_tokens: int) -> int:
        numerator = (
            max(0, input_tokens) * self.input_micro_usd_per_million
            + max(0, output_tokens) * self.output_micro_usd_per_million
        )
        return max(0, (numerator + 999_999) // 1_000_000)

    def actual_cost(self, usage: MagiModelUsage) -> int:
        paid_input = max(0, usage.input_tokens - usage.cached_input_tokens)
        numerator = (
            paid_input * self.input_micro_usd_per_million
            + usage.cached_input_tokens * self.cached_input_micro_usd_per_million
            + usage.output_tokens * self.output_micro_usd_per_million
        )
        return max(0, (numerator + 999_999) // 1_000_000)


@dataclass(frozen=True)
class RoutingPolicy:
    monthly_budget_micro_usd: int
    per_request_budget_micro_usd: int
    output_reservation_tokens: int
    min_reliability: Decimal
    max_same_model_retries: int
    max_automatic_fallback_micro_usd: int


@dataclass(frozen=True)
class RoutingCatalog:
    candidates: tuple[ModelCandidate, ...]
    policy: RoutingPolicy


_REASONING_REQUIRED = {
    RouteCategory.DIRECT_CONVERSATION: 1,
    RouteCategory.READ_ONLY_INVESTIGATION: 2,
    RouteCategory.EXECUTION: 3,
    RouteCategory.DECISION_RESPONSE: 3,
    RouteCategory.HIGH_IMPACT_ACTION: 4,
}
_HIGH_IMPACT = re.compile(
    r"\b(?:deploy(?:ment)?|production|delete|destroy|drop\s+(?:the\s+)?database|"
    r"rotate\s+(?:the\s+)?keys?|revoke|purchase|pay|publish|release\s+to\s+(?:the\s+)?app\s*store|"
    r"merge\s+(?:the\s+)?(?:pull\s+request|pr)|force[- ]push)\b",
    re.IGNORECASE,
)
_READ_ONLY = re.compile(
    r"\b(?:investigate|inspect|analy[sz]e|audit|review|research|diagnose|explain\s+why|find\s+the\s+cause)\b",
    re.IGNORECASE,
)
_EXECUTION = re.compile(
    r"\b(?:add|build|change|create|edit|fix|implement|modify|refactor|remove|rename|test|update|write)\b",
    re.IGNORECASE,
)


def requires_high_impact_confirmation(content: str) -> bool:
    """Return whether host policy requires request-bound confirmation."""
    return bool(_HIGH_IMPACT.search(content))


def classify_turn(content: str, *, command_authorized: bool, decision_bound: bool = False) -> RouteCategory:
    """Conservative host classification used for permissions and route needs.

    A free-form "yes" is never a decision response: that category is available
    only when a canonical pending decision has been bound out of band.
    """
    if decision_bound:
        return RouteCategory.DECISION_RESPONSE
    if command_authorized and requires_high_impact_confirmation(content):
        return RouteCategory.HIGH_IMPACT_ACTION
    if command_authorized and _READ_ONLY.search(content):
        return RouteCategory.READ_ONLY_INVESTIGATION
    if command_authorized and _EXECUTION.search(content):
        return RouteCategory.EXECUTION
    return RouteCategory.DIRECT_CONVERSATION


def _micro_usd(value: Any, field: str, *, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative USD amount") from exc
    if not decimal.is_finite() or decimal < 0:
        raise ValueError(f"{field} must be a non-negative USD amount")
    return int((decimal * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING))


def _safe_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{field} is invalid")
    return value


def validate_routing_config(raw: Any) -> RoutingCatalog:
    if not isinstance(raw, Mapping) or raw.get("schema_version") != _ROUTE_SCHEMA:
        raise ValueError(f"model routing schema_version must be {_ROUTE_SCHEMA}")
    policy = raw.get("policy")
    models = raw.get("models")
    if not isinstance(policy, Mapping) or not isinstance(models, list) or not models:
        raise ValueError("model routing requires policy and at least one model")
    try:
        output_tokens = int(policy["output_reservation_tokens"])
        retries = int(policy.get("max_same_model_retries", 0))
        reliability = Decimal(str(policy["min_reliability"]))
    except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
        raise ValueError("model routing policy is invalid") from exc
    if not 1 <= output_tokens <= 65_536 or not 0 <= retries <= 2 or not Decimal(0) <= reliability <= Decimal(1):
        raise ValueError("model routing policy is out of bounds")
    checked: list[ModelCandidate] = []
    seen: set[str] = set()
    for raw_model in models:
        if not isinstance(raw_model, Mapping):
            raise ValueError("each routed model must be an object")
        candidate_id = _safe_id(raw_model.get("id"), "model route id")
        if candidate_id in seen:
            raise ValueError(f"model route {candidate_id} is duplicated")
        seen.add(candidate_id)
        provider = _safe_id(raw_model.get("provider"), "provider id")
        if provider not in {"openai", "anthropic", "google"}:
            # Future providers become valid by registering a factory explicitly;
            # configuration never aliases one provider's wire format to another.
            if not provider.startswith("custom-"):
                raise ValueError(f"provider {provider} has no registered contract")
        capabilities = raw_model.get("capabilities")
        pricing = raw_model.get("pricing_usd_per_million_tokens")
        if not isinstance(capabilities, Mapping) or not isinstance(pricing, Mapping):
            raise ValueError(f"model route {candidate_id} lacks capabilities or pricing")
        credential_env = raw_model.get("credential_env")
        if not isinstance(credential_env, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", credential_env):
            raise ValueError(f"model route {candidate_id} has an invalid credential_env")
        base_url = raw_model.get("base_url")
        if base_url is not None:
            parsed_url = urlsplit(base_url) if isinstance(base_url, str) else None
            if (
                parsed_url is None or len(base_url) > 2048 or parsed_url.scheme != "https"
                or not parsed_url.hostname or parsed_url.username is not None
                or parsed_url.password is not None or parsed_url.query or parsed_url.fragment
            ):
                raise ValueError(f"model route {candidate_id} has an invalid base_url")
        try:
            reasoning = int(capabilities["reasoning"])
            context = int(capabilities["context_tokens"])
            model_reliability = Decimal(str(raw_model["reliability"]))
            latency = int(raw_model["latency_ms"])
            safety_tier = int(raw_model.get("safety_tier", 1))
        except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
            raise ValueError(f"model route {candidate_id} has invalid metrics") from exc
        flags = [capabilities.get("tools"), capabilities.get("multimodal"), raw_model.get("enabled", True), raw_model.get("available", True)]
        if not all(type(flag) is bool for flag in flags):
            raise ValueError(f"model route {candidate_id} has invalid capability flags")
        if not 1 <= reasoning <= 5 or context < 1024 or not Decimal(0) <= model_reliability <= Decimal(1) or latency < 1 or not 1 <= safety_tier <= 5:
            raise ValueError(f"model route {candidate_id} metrics are out of bounds")
        billing_mode = raw_model.get("billing_mode", "metered")
        if billing_mode not in {"metered", "subscription", "credits"}:
            raise ValueError(f"model route {candidate_id} has invalid billing_mode")
        checked.append(ModelCandidate(
            id=candidate_id,
            provider=provider,
            model=_safe_id(raw_model.get("model"), "provider model id"),
            credential_env=credential_env,
            base_url=base_url,
            capability_class=_safe_id(raw_model.get("capability_class", "general"), "capability class"),
            reasoning=reasoning,
            context_tokens=context,
            tools=flags[0],
            multimodal=flags[1],
            reliability=model_reliability,
            latency_ms=latency,
            input_micro_usd_per_million=int(_micro_usd(pricing.get("input"), "input price")),
            output_micro_usd_per_million=int(_micro_usd(pricing.get("output"), "output price")),
            cached_input_micro_usd_per_million=int(_micro_usd(pricing.get("cached_input", pricing.get("input")), "cached input price")),
            billing_mode=billing_mode,
            plan=raw_model.get("plan") if isinstance(raw_model.get("plan"), str) else None,
            credits_micro_usd=_micro_usd(raw_model.get("credits_remaining_usd"), "credits", allow_none=True),
            enabled=flags[2], available=flags[3], safety_tier=safety_tier,
            max_output_tokens=output_tokens,
        ))
    return RoutingCatalog(tuple(checked), RoutingPolicy(
        monthly_budget_micro_usd=int(_micro_usd(policy["monthly_budget_usd"], "monthly budget")),
        per_request_budget_micro_usd=int(_micro_usd(policy["per_request_budget_usd"], "per-request budget")),
        output_reservation_tokens=output_tokens,
        min_reliability=reliability,
        max_same_model_retries=retries,
        max_automatic_fallback_micro_usd=int(_micro_usd(policy["max_automatic_fallback_cost_usd"], "automatic fallback cost")),
    ))


def load_routing_catalog() -> RoutingCatalog:
    inline = os.getenv("MAGISTRATE_MODEL_ROUTING_CONFIG", "").strip()
    try:
        raw = json.loads(inline) if inline else _default_routing_config()
    except json.JSONDecodeError as exc:
        raise RuntimeError("Model routing configuration is unavailable or invalid") from exc
    try:
        return validate_routing_config(raw)
    except ValueError as exc:
        raise RuntimeError(f"Model routing configuration is invalid: {exc}") from exc


def _month_start_ms(now_ms: int) -> int:
    now = time.gmtime(now_ms / 1000)
    return int(calendar.timegm((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 0, 0)) * 1000)


class ModelRouteStore:
    """Content-free route ledger and atomic USD-micro budget reservations."""

    def _connect(self) -> sqlite3.Connection:
        db.init_db()
        connection = connect(db.DB_PATH, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def reserve(
        self,
        context: ModelRouteContext,
        request_id: str,
        candidate: ModelCandidate,
        *,
        ordinal: int,
        estimated_micro_usd: int,
        monthly_budget_micro_usd: int,
        fallback_from: str | None,
    ) -> str:
        now = int(time.time_ns() // 1_000_000)
        route_id = f"mgr_{secrets.token_urlsafe(18)}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            spent = connection.execute(
                """SELECT COALESCE(SUM(budget_charge_micro_usd), 0)
                   FROM magi_model_routes WHERE owner_user_id = ? AND created_at >= ?
                     AND state IN ('reserved','succeeded','failed')""",
                (context.owner_user_id, _month_start_ms(now)),
            ).fetchone()[0]
            if int(spent) + estimated_micro_usd > monthly_budget_micro_usd:
                raise MagiModelError("model_budget_exhausted", retryable=False)
            if candidate.credits_micro_usd is not None:
                credits_spent = connection.execute(
                    """SELECT COALESCE(SUM(budget_charge_micro_usd), 0)
                       FROM magi_model_routes WHERE provider_id = ? AND model_id = ?
                         AND state IN ('reserved','succeeded','failed')""",
                    (candidate.provider, candidate.model),
                ).fetchone()[0]
                if int(credits_spent) + estimated_micro_usd > candidate.credits_micro_usd:
                    raise MagiModelError("model_credits_exhausted", retryable=False)
            connection.execute(
                """INSERT INTO magi_model_routes
                   (id, owner_user_id, request_id, route_category, provider_id, model_id,
                    capability_class, attempt_ordinal, fallback_from, state,
                    estimated_cost_micro_usd, actual_cost_micro_usd,
                    budget_charge_micro_usd, input_tokens, output_tokens,
                    error_code, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, NULL, ?, NULL,
                           NULL, NULL, ?, ?)""",
                (
                    route_id, context.owner_user_id, request_id, context.category.value,
                    candidate.provider, candidate.model, candidate.capability_class,
                    ordinal, fallback_from, estimated_micro_usd, estimated_micro_usd,
                    now, now,
                ),
            )
        return route_id

    def settle(
        self,
        route_id: str,
        *,
        usage: MagiModelUsage | None,
        actual_micro_usd: int | None,
        error: MagiModelError | None,
        uncertain_charge: bool = False,
    ) -> None:
        now = int(time.time_ns() // 1_000_000)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT estimated_cost_micro_usd FROM magi_model_routes WHERE id = ? AND state = 'reserved'",
                (route_id,),
            ).fetchone()
            if row is None:
                return
            charge = (
                actual_micro_usd if actual_micro_usd is not None
                else int(row["estimated_cost_micro_usd"]) if uncertain_charge else 0
            )
            connection.execute(
                """UPDATE magi_model_routes
                   SET state = ?, actual_cost_micro_usd = ?, budget_charge_micro_usd = ?,
                       input_tokens = ?, output_tokens = ?, error_code = ?, updated_at = ?
                   WHERE id = ? AND state = 'reserved'""",
                (
                    "failed" if error else "succeeded", actual_micro_usd, charge,
                    usage.input_tokens if usage else None,
                    usage.output_tokens if usage else None,
                    error.code if error else None, now, route_id,
                ),
            )

    def credits_spent(self, candidate: ModelCandidate) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COALESCE(SUM(budget_charge_micro_usd), 0)
                   FROM magi_model_routes WHERE provider_id = ? AND model_id = ?
                     AND state IN ('reserved','succeeded','failed')""",
                (candidate.provider, candidate.model),
            ).fetchone()
        return int(row[0]) if row else 0

    def reliability(self, candidate: ModelCandidate) -> Decimal:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN state = 'succeeded' THEN 1 ELSE 0 END) AS successes
                   FROM magi_model_routes WHERE provider_id = ? AND model_id = ?
                     AND state IN ('succeeded','failed')""",
                (candidate.provider, candidate.model),
            ).fetchone()
        if not row or int(row["total"]) < 5:
            return candidate.reliability
        observed = Decimal(int(row["successes"] or 0)) / Decimal(int(row["total"]))
        return min(candidate.reliability, observed)

    def summary(self, owner_user_id: str, *, limit: int = 100) -> dict[str, Any]:
        now = int(time.time_ns() // 1_000_000)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT request_id, route_category, provider_id, model_id,
                          capability_class, attempt_ordinal, fallback_from, state,
                          estimated_cost_micro_usd, actual_cost_micro_usd,
                          budget_charge_micro_usd, input_tokens, output_tokens,
                          error_code, created_at
                   FROM magi_model_routes WHERE owner_user_id = ?
                   ORDER BY created_at DESC, attempt_ordinal DESC LIMIT ?""",
                (owner_user_id, max(1, min(limit, 100))),
            ).fetchall()
            totals = connection.execute(
                """SELECT COALESCE(SUM(budget_charge_micro_usd), 0),
                          COALESCE(SUM(actual_cost_micro_usd), 0), COUNT(*)
                   FROM magi_model_routes WHERE owner_user_id = ? AND created_at >= ?""",
                (owner_user_id, _month_start_ms(now)),
            ).fetchone()
        def usd(micro: int | None) -> str | None:
            return None if micro is None else f"{Decimal(micro) / Decimal(1_000_000):.6f}"
        return {
            "schema_version": _ROUTE_SCHEMA,
            "month": {
                "budget_charged_usd": usd(int(totals[0])),
                "usage_derived_actual_usd": usd(int(totals[1])),
                "calls": int(totals[2]),
            },
            "routes": [{
                "request_id": row["request_id"],
                "route_category": row["route_category"],
                "provider": row["provider_id"], "model": row["model_id"],
                "capability_class": row["capability_class"],
                "attempt": row["attempt_ordinal"], "fallback_from": row["fallback_from"],
                "state": row["state"],
                "estimated_cost_usd": usd(row["estimated_cost_micro_usd"]),
                "actual_cost_usd": usd(row["actual_cost_micro_usd"]),
                "budget_charge_usd": usd(row["budget_charge_micro_usd"]),
                "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
                "error_code": row["error_code"], "created_at": row["created_at"],
            } for row in rows],
        }


def _estimate_input_tokens(messages: Sequence[MagiModelMessage], system_context: str, tools: Sequence[MagiToolDefinition]) -> int:
    characters = len(system_context)
    for message in messages:
        characters += len(message.content or "")
        characters += sum(len(call.arguments_json) for call in message.tool_calls)
    for tool in tools:
        characters += len(tool.description) + len(json.dumps(tool.parameters, separators=(",", ":")))
    return max(1, (characters + 3) // 4)


ProviderFactory = Callable[[ModelCandidate], MagiModel]


def _openai_factory(candidate: ModelCandidate) -> MagiModel:
    kwargs: dict[str, Any] = {
        "api_key": os.getenv(candidate.credential_env, ""), "model": candidate.model,
        "max_output_tokens": candidate.max_output_tokens,
    }
    if candidate.base_url:
        kwargs["base_url"] = candidate.base_url
    return OpenAIMagiModel(**kwargs)


def _anthropic_factory(candidate: ModelCandidate) -> MagiModel:
    kwargs: dict[str, Any] = {
        "api_key": os.getenv(candidate.credential_env, ""), "model": candidate.model,
        "max_output_tokens": candidate.max_output_tokens,
    }
    if candidate.base_url:
        kwargs["base_url"] = candidate.base_url
    return AnthropicMagiModel(**kwargs)


def _google_factory(candidate: ModelCandidate) -> MagiModel:
    kwargs: dict[str, Any] = {
        "api_key": os.getenv(candidate.credential_env, ""), "model": candidate.model,
        "max_output_tokens": candidate.max_output_tokens,
    }
    if candidate.base_url:
        kwargs["base_url"] = candidate.base_url
    return GoogleMagiModel(**kwargs)


class RoutedMagiModel:
    """Cheapest-reliably-capable selector with bounded explicit failover."""

    provider_id = "routed"

    def __init__(
        self,
        catalog: RoutingCatalog | None = None,
        *,
        store: ModelRouteStore | None = None,
        provider_factories: Mapping[str, ProviderFactory] | None = None,
        providers: Mapping[str, MagiModel] | None = None,
    ) -> None:
        self.catalog = catalog or load_routing_catalog()
        self.store = store or ModelRouteStore()
        self._factories = {
            "openai": _openai_factory, "anthropic": _anthropic_factory, "google": _google_factory,
            **dict(provider_factories or {}),
        }
        self._providers = dict(providers or {})

    @property
    def configured_provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted({
            candidate.provider for candidate in self.catalog.candidates
            if candidate.enabled and candidate.available
            and (candidate.provider in self._factories or candidate.id in self._providers)
            and (bool(os.getenv(candidate.credential_env, "")) or candidate.id in self._providers)
        }))

    @property
    def configured(self) -> bool:
        return bool(self.configured_provider_ids)

    def _eligible(
        self,
        context: ModelRouteContext,
        input_tokens: int,
    ) -> list[tuple[ModelCandidate, int]]:
        required_reasoning = _REASONING_REQUIRED[context.category]
        required_safety = 4 if context.category == RouteCategory.HIGH_IMPACT_ACTION else 1
        eligible: list[tuple[ModelCandidate, int]] = []
        for candidate in self.catalog.candidates:
            credential_present = bool(os.getenv(candidate.credential_env, "")) or candidate.id in self._providers
            provider_registered = candidate.provider in self._factories or candidate.id in self._providers
            estimate = candidate.estimated_cost(input_tokens, self.catalog.policy.output_reservation_tokens)
            if not all((candidate.enabled, candidate.available, credential_present, provider_registered)):
                continue
            if candidate.reasoning < required_reasoning or candidate.safety_tier < required_safety:
                continue
            if input_tokens + self.catalog.policy.output_reservation_tokens > candidate.context_tokens:
                continue
            if context.requires_tools and not candidate.tools:
                continue
            if context.requires_multimodal and not candidate.multimodal:
                continue
            if self.store.reliability(candidate) < self.catalog.policy.min_reliability:
                continue
            if (
                candidate.credits_micro_usd is not None
                and estimate > max(0, candidate.credits_micro_usd - self.store.credits_spent(candidate))
            ):
                continue
            if estimate > self.catalog.policy.per_request_budget_micro_usd:
                continue
            eligible.append((candidate, estimate))
        def key(item: tuple[ModelCandidate, int]) -> tuple[Any, ...]:
            candidate, estimate = item
            preference = (
                0 if context.preferred_model == candidate.model else
                1 if context.preferred_provider == candidate.provider else 2
            )
            marginal = 0 if candidate.billing_mode == "subscription" else estimate
            return (marginal, preference, -self.store.reliability(candidate), candidate.latency_ms, candidate.id)
        eligible.sort(key=key)
        if eligible:
            primary_class = eligible[0][0].capability_class
            eligible = [item for item in eligible if item[0].capability_class == primary_class] + [
                item for item in eligible if item[0].capability_class != primary_class
            ]
        return eligible

    def _provider(self, candidate: ModelCandidate) -> MagiModel:
        if candidate.id in self._providers:
            return self._providers[candidate.id]
        factory = self._factories.get(candidate.provider)
        if factory is None:
            raise MagiModelError("provider_not_supported", retryable=False)
        return factory(candidate)

    async def complete_routed(
        self,
        messages: Sequence[MagiModelMessage],
        *,
        system_context: str,
        request_id: str,
        route_context: ModelRouteContext,
        tools: Sequence[MagiToolDefinition] = (),
    ) -> MagiModelResult:
        if route_context.category != RouteCategory.DIRECT_CONVERSATION and not route_context.permission_granted:
            raise MagiModelError("route_permission_required", retryable=False)
        if route_context.category == RouteCategory.HIGH_IMPACT_ACTION and not route_context.explicit_confirmation:
            raise MagiModelError("route_confirmation_required", retryable=False)
        estimated_input = max(
            route_context.context_tokens,
            _estimate_input_tokens(messages, system_context, tools),
        )
        candidates = self._eligible(route_context, estimated_input)
        if not candidates:
            raise MagiModelError("no_reliably_capable_model", retryable=False)
        ordinal = 0
        fallback_from: str | None = None
        last_error: MagiModelError | None = None
        for candidate_index, (candidate, estimate) in enumerate(candidates):
            if (
                candidate_index
                and estimate > self.catalog.policy.max_automatic_fallback_micro_usd
                and not route_context.explicit_confirmation
            ):
                raise MagiModelError("route_fallback_confirmation_required", retryable=False) from last_error
            retries = self.catalog.policy.max_same_model_retries + 1
            for retry in range(retries):
                ordinal += 1
                try:
                    route_id = self.store.reserve(
                        route_context, request_id, candidate, ordinal=ordinal,
                        estimated_micro_usd=estimate,
                        monthly_budget_micro_usd=self.catalog.policy.monthly_budget_micro_usd,
                        fallback_from=fallback_from,
                    )
                except MagiModelError as exc:
                    if exc.code != "model_credits_exhausted":
                        raise
                    last_error = exc
                    fallback_from = candidate.id
                    break
                try:
                    result = await self._provider(candidate).complete(
                        messages, system_context=system_context,
                        request_id=request_id, tools=tools,
                    )
                except MagiModelError as exc:
                    self.store.settle(
                        route_id, usage=None, actual_micro_usd=None, error=exc,
                        uncertain_charge=exc.billing_uncertain,
                    )
                    last_error = exc
                    # Never retry after observing a tool call: doing so could
                    # create a second objective selection. Unknown billing also
                    # needs a new explicit request unless the configured cost is
                    # inside the automatic materiality ceiling.
                    if not exc.retryable or exc.tool_calls or (
                        exc.billing_uncertain
                        and estimate > self.catalog.policy.max_automatic_fallback_micro_usd
                    ):
                        raise
                    if retry + 1 < retries:
                        continue
                    fallback_from = candidate.id
                    break
                except Exception as exc:
                    safe = MagiModelError("provider_failure", billing_uncertain=True)
                    self.store.settle(
                        route_id, usage=None, actual_micro_usd=None, error=safe,
                        uncertain_charge=True,
                    )
                    raise safe from exc
                else:
                    actual = candidate.actual_cost(result.usage) if result.usage else None
                    self.store.settle(
                        route_id, usage=result.usage,
                        actual_micro_usd=actual, error=None,
                        # Missing provider usage remains a conservative budget
                        # reservation, not a fabricated actual-cost claim.
                        uncertain_charge=result.usage is None,
                    )
                    return result
        raise last_error or MagiModelError("no_reliably_capable_model", retryable=False)

    async def complete(
        self,
        messages: Sequence[MagiModelMessage],
        *,
        system_context: str,
        request_id: str,
        tools: Sequence[MagiToolDefinition] = (),
    ) -> MagiModelResult:
        # Non-chat callers get a least-authority direct route. Native chat uses
        # complete_routed with authenticated principal and category metadata.
        return await self.complete_routed(
            messages, system_context=system_context, request_id=request_id, tools=tools,
            route_context=ModelRouteContext(
                owner_user_id="system", category=RouteCategory.DIRECT_CONVERSATION,
                permission_granted=True, requires_tools=bool(tools),
            ),
        )
