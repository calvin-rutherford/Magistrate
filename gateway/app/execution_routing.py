"""Replaceable, process-free strategy for future execution-harness launches.

This module selects only verified inventory profiles. It does not start, stop,
or inspect a harness. The selected profile remains a launch recommendation
until Firstmate's explicit execution seam consumes it.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class ExecutionRequirements:
    reasoning: int = 1
    context_tokens: int = 0
    tools: bool = True
    multimodal: bool = False
    max_latency_ms: int | None = None
    preferred_profile_id: str | None = None


@dataclass(frozen=True)
class HarnessRoute:
    profile_id: str
    harness: str
    provider: str
    model: str
    variant: str
    estimated_cost_usd: str
    reason: str = "cheapest-reliably-capable"


@runtime_checkable
class HarnessRoutingStrategy(Protocol):
    def select(
        self,
        profiles: Sequence[dict[str, Any]],
        requirements: ExecutionRequirements,
    ) -> HarnessRoute:
        ...


class CheapestReliableHarnessStrategy:
    """Default strategy; replaceable without changing inventory or launch APIs."""

    def __init__(self, *, min_reliability: Decimal = Decimal("0.95")) -> None:
        self.min_reliability = min_reliability

    def select(
        self,
        profiles: Sequence[dict[str, Any]],
        requirements: ExecutionRequirements,
    ) -> HarnessRoute:
        eligible: list[tuple[Decimal, int, int, str, dict[str, Any]]] = []
        for profile in profiles:
            route = profile.get("routing")
            if profile.get("verified") is not True or profile.get("available") is not True:
                continue
            # Missing economics/capabilities are unknown, not free defaults.
            if not isinstance(route, dict):
                continue
            try:
                reliability = Decimal(str(route["reliability"]))
                estimated_cost = Decimal(str(route["estimated_cost_usd"]))
                reasoning = int(route["reasoning"])
                context = int(route["context_tokens"])
                latency = int(route["latency_ms"])
            except (KeyError, ValueError, TypeError, InvalidOperation):
                continue
            if reliability < self.min_reliability or estimated_cost < 0:
                continue
            if reasoning < requirements.reasoning or context < requirements.context_tokens:
                continue
            if requirements.tools and route.get("tools") is not True:
                continue
            if requirements.multimodal and route.get("multimodal") is not True:
                continue
            if requirements.max_latency_ms is not None and latency > requirements.max_latency_ms:
                continue
            preference = 0 if profile.get("id") == requirements.preferred_profile_id else 1
            eligible.append((estimated_cost, preference, latency, str(profile.get("id")), profile))
        if not eligible:
            raise ValueError("No verified execution profile truthfully satisfies the route requirements.")
        cost, _, _, _, selected = min(eligible)
        return HarnessRoute(
            profile_id=selected["id"],
            harness=selected["harness"]["id"],
            provider=selected["provider"]["id"],
            model=selected["model"]["id"],
            variant=selected["variant"],
            estimated_cost_usd=f"{cost:.6f}",
        )


def select_execution_profile(
    profiles: Sequence[dict[str, Any]],
    requirements: ExecutionRequirements,
    *,
    strategy: HarnessRoutingStrategy | None = None,
) -> HarnessRoute:
    return (strategy or CheapestReliableHarnessStrategy()).select(profiles, requirements)
