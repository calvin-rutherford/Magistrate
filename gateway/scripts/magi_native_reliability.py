"""Deterministic 120-turn reliability gate for native Magi chat.

Run from ``gateway/`` with ``PYTHONPATH=. uv run python -m
scripts.magi_native_reliability``. It always uses a temporary SQLite database,
a fake provider, and prints aggregate evidence only—never prompt/response text.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Sequence


@dataclass(frozen=True)
class ReliabilityCase:
    index: int
    owner: str
    client_message_id: str
    prompt: str
    response: str


def _rich_long_response() -> str:
    numbered = "\n".join(f"{index}. Reliability item {index}: preserve this complete line." for index in range(1, 31))
    paragraphs = "\n\n".join(
        f"Paragraph {index} carries Unicode café, 東京, and 🚀 while retaining Markdown punctuation **exactly**."
        for index in range(1, 8)
    )
    code = "```python\ndef native_chat_ok(value: str) -> bool:\n    return value.encode('utf-8').decode('utf-8') == value\n```"
    filler = "\n".join(f"Extended line {index:04d}: " + ("lossless-response-evidence " * 8) for index in range(360))
    return f"# Native Magi reliability\n\n## Numbered acceptance\n\n{numbered}\n\n## Paragraphs\n\n{paragraphs}\n\n## Code\n\n{code}\n\n## Extended payload\n\n{filler}\n"


def _cases() -> list[ReliabilityCase]:
    cases: list[ReliabilityCase] = []
    long_response = _rich_long_response()
    for index in range(120):
        owner = "reliability-owner-b" if index in {113, 117} else "reliability-owner-a"
        if index in {8, 9, 10, 11, 12}:
            prompt = "Repeated identical prompt with a distinct client identity."
        elif index == 3:
            prompt = "Unicode input: naïve café — 東京 — مرحبا — 👩🏽\u200d💻"
        elif index == 4:
            prompt = "Return Markdown with headings, lists, and code."
        elif index == 5:
            prompt = "Return the long soak response without truncation."
        elif index == 77:
            prompt = "Synthetic provider failure followed by explicit retry."
        else:
            prompt = f"Deterministic native request {index:03d}."
        if index == 3:
            response = "# Unicode\n\nnaïve café — 東京 — مرحبا — 👩🏽\u200d💻\n"
        elif index == 4:
            response = "# Heading\n\n1. first\n2. second\n\n```ts\nconst complete = true;\n```\n"
        elif index == 5:
            response = long_response
        elif index in {8, 9, 10, 11, 12}:
            response = "# Repeated prompt\n\nThe same wording remains five distinct canonical turns.\n"
        else:
            response = f"# Response {index:03d}\n\nComplete answer for `{prompt}`\n\n- persisted\n- attributed\n"
        cases.append(ReliabilityCase(
            index=index,
            owner=owner,
            client_message_id=f"reliability-{index:04d}",
            prompt=prompt,
            response=response,
        ))
    return cases


async def run_gate() -> dict[str, object]:
    # Imports occur only after the caller has forced a disposable DB below.
    from app.magi_chat_service import MagiChatService
    from app.magi_chat_store import MagiChatStore
    from app.magi_model import MagiModelError, MagiModelResult

    cases = _cases()
    expected_by_prompt = {case.prompt: case.response for case in cases}
    failure_prompt = cases[77].prompt

    class DeterministicModel:
        def __init__(self) -> None:
            self.calls = 0
            self.calls_by_prompt: dict[str, int] = {}
            self.failed = False

        async def complete(self, messages, *, system_context, request_id):
            del system_context, request_id
            prompt = messages[-1].content
            self.calls += 1
            self.calls_by_prompt[prompt] = self.calls_by_prompt.get(prompt, 0) + 1
            # A tiny yield makes the duplicate/concurrency test exercise the
            # pending state instead of accidentally becoming sequential.
            await asyncio.sleep(0.002)
            if prompt == failure_prompt and not self.failed:
                self.failed = True
                raise MagiModelError("synthetic_failure")
            return MagiModelResult(expected_by_prompt[prompt])

    model = DeterministicModel()
    store = MagiChatStore()
    service = MagiChatService(model, store=store, profile_loader=lambda owner: {"name": owner})
    started = time.perf_counter_ns()

    # One id under a 16-way reconnect storm. Exactly one caller can claim it.
    duplicate_case = cases[0]
    duplicate_results = await asyncio.gather(*[
        service.submit(
            duplicate_case.owner, duplicate_case.client_message_id,
            duplicate_case.prompt,
        )
        for _ in range(16)
    ])
    if sum(result["duplicate"] is False for result in duplicate_results) != 1:
        raise AssertionError("concurrent duplicate claim count was not one")

    # Rapid independent submissions exercise transactional ordering and ensure
    # identical timing cannot cross-attribute replies.
    rapid = cases[1:26]
    rapid_results = await asyncio.gather(*[
        service.submit(case.owner, case.client_message_id, case.prompt) for case in rapid
    ])
    if len(rapid_results) != len(rapid):
        raise AssertionError("rapid submission batch was incomplete")

    # Sequential tail includes short/long/Unicode/Markdown/repeated prompts.
    for case in cases[26:]:
        result = await service.submit(case.owner, case.client_message_id, case.prompt)
        if case.index == 77:
            if result["status"] != "failed" or result["assistant_message"]["content"] != "":
                raise AssertionError("provider failure was not retained truthfully")
            result = await service.submit(
                case.owner, case.client_message_id, case.prompt, retry_failed=True,
            )
        if result["status"] != "completed":
            raise AssertionError(f"submission {case.index} did not complete")

    # Simulate server/app restoration with a fresh service object and replay the
    # same completed submission. It must not call the provider again.
    restored = MagiChatService(model, store=MagiChatStore(), profile_loader=lambda owner: {"name": owner})
    restored_result = await restored.submit(
        cases[119].owner, cases[119].client_message_id, cases[119].prompt,
    )
    if not restored_result["duplicate"] or restored_result["status"] != "completed":
        raise AssertionError("restored service did not replay the completed pair")

    complete = 0
    seen_user_ids: set[str] = set()
    seen_assistant_ids: set[str] = set()
    max_characters = 0
    max_bytes = 0
    for case in cases:
        pair = store.submission(case.owner, case.client_message_id)
        user = pair["user_message"]
        assistant = pair["assistant_message"]
        if user["content"] != case.prompt or assistant["content"].encode("utf-8") != case.response.encode("utf-8"):
            raise AssertionError(f"byte comparison failed for submission {case.index}")
        if user["id"] in seen_user_ids or assistant["id"] in seen_assistant_ids:
            raise AssertionError("duplicate canonical message identity")
        if user["turn_id"] != assistant["turn_id"] or assistant["reply_to_message_id"] != user["id"]:
            raise AssertionError("cross-attributed native pair")
        if any(marker in assistant["content"] for marker in ("FIRSTMATE_OP", "jsonrpc", "pane_id=", "tool_calls")):
            raise AssertionError("raw execution internals reached visible prose")
        seen_user_ids.add(user["id"])
        seen_assistant_ids.add(assistant["id"])
        complete += pair["status"] == "completed"
        max_characters = max(max_characters, len(assistant["content"]))
        max_bytes = max(max_bytes, len(assistant["content"].encode("utf-8")))

    # Principal ownership is fail-closed even with a valid foreign id.
    owner_b_conversation = store.submission(
        cases[113].owner, cases[113].client_message_id,
    )["conversation"]["id"]
    try:
        store.list_conversation("reliability-owner-a", owner_b_conversation)
    except LookupError:
        pass
    else:
        raise AssertionError("cross-principal conversation read succeeded")

    metrics_a = store.diagnostics("reliability-owner-a")
    metrics_b = store.diagnostics("reliability-owner-b")
    submitted = metrics_a["magi_messages_submitted"] + metrics_b["magi_messages_submitted"]
    completed = metrics_a["magi_messages_completed"] + metrics_b["magi_messages_completed"]
    failed_attempts = metrics_a["magi_messages_failed"] + metrics_b["magi_messages_failed"]
    duplicate_submissions = metrics_a["magi_duplicate_submissions"] + metrics_b["magi_duplicate_submissions"]
    retries = metrics_a["magi_retries"] + metrics_b["magi_retries"]
    for metrics in (metrics_a, metrics_b):
        if any(metrics[name] != 0 for name in (
            "legacy_chat_reads", "terminal_chat_reads", "pi_ownership_chat_reads", "magi_tool_calls",
        )):
            raise AssertionError("native gate touched legacy/execution chat infrastructure")
    if (submitted, completed, complete, failed_attempts, retries) != (120, 120, 120, 1, 1):
        raise AssertionError("aggregate reliability counts are not exact")
    # One failed attempt plus each successful case, and no duplicate/reconnect
    # request, is the only valid fake-provider call count.
    if model.calls != 121 or model.calls_by_prompt[duplicate_case.prompt] != 1:
        raise AssertionError("duplicate submission invoked the provider")

    elapsed_ms = (time.perf_counter_ns() - started) // 1_000_000
    max_latency_ms = max(
        metrics_a["completion_latency"]["max_ms"],
        metrics_b["completion_latency"]["max_ms"],
    )
    return {
        "schema_version": "magi.native-chat-reliability.v1",
        "result": "PASS",
        "submissions": submitted,
        "complete": complete,
        "truncation": 0,
        "duplication": 0,
        "cross_attribution": 0,
        "raw_internals": 0,
        "tool_calls": 0,
        "provider_calls": model.calls,
        "failed_attempts_recovered": failed_attempts,
        "duplicate_submissions_observed": duplicate_submissions,
        "retries": retries,
        "legacy_chat_reads": 0,
        "terminal_chat_reads": 0,
        "pi_ownership_chat_reads": 0,
        "maximum_response_characters": max_characters,
        "maximum_response_bytes": max_bytes,
        "maximum_completion_latency_ms": max_latency_ms,
        "wall_clock_ms": elapsed_ms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic native Magi reliability gate.")
    parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="magistrate-magi-reliability-") as directory:
        os.environ["MAGISTRATE_ENV"] = "test"
        os.environ["MAGISTRATE_DB_PATH"] = str(Path(directory) / "magi.sqlite3")
        os.environ["MAGISTRATE_NATIVE_CHAT_ENABLED"] = "true"
        os.environ["MAGISTRATE_LEGACY_CHAT_ENABLED"] = "false"
        # Explicit test mode permits db.py's process-local ephemeral Fernet key.
        report = asyncio.run(run_gate())
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
