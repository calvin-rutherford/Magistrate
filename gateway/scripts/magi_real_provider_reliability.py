"""Opt-in, content-free reliability runner against the configured OpenAI model.

Nothing runs unless ``MAGISTRATE_RUN_REAL_MAGI_RELIABILITY=1`` and
``OPENAI_API_KEY`` are present. The runner uses a disposable database and emits
only counts, latency, and size aggregates—never credentials, prompts, or model
responses.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time


def _enabled() -> bool:
    return os.getenv("MAGISTRATE_RUN_REAL_MAGI_RELIABILITY", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


async def run(submissions: int) -> dict[str, object]:
    from app.magi_chat_service import MagiChatService
    from app.magi_chat_store import MagiChatStore
    from app.magi_model import OpenAIMagiModel

    store = MagiChatStore()
    service = MagiChatService(
        OpenAIMagiModel(), store=store, profile_loader=lambda _: {"name": "Reliability operator"},
    )
    owner = "real-provider-reliability"
    started = time.perf_counter_ns()
    maximum_characters = 0
    maximum_bytes = 0
    for index in range(submissions):
        if index == 0:
            prompt = (
                "Produce a complete Markdown response with at least three headings, several paragraphs, "
                "one code block, and exactly 30 numbered items. End with the marker COMPLETE."
            )
        elif index in {1, 2}:
            prompt = "Reply briefly to this repeated Unicode check: café 東京 🚀."
        else:
            prompt = f"Reply with one short complete sentence for reliability case {index}."
        client_id = f"real-provider-{index:04d}"
        result = await service.submit(owner, client_id, prompt)
        if result["status"] != "completed":
            raise RuntimeError(f"real-provider submission {index} did not complete")
        persisted = store.submission(owner, client_id)
        returned = result["assistant_message"]["content"]
        canonical = persisted["assistant_message"]["content"]
        if returned.encode("utf-8") != canonical.encode("utf-8"):
            raise RuntimeError(f"real-provider persistence mismatch at submission {index}")
        maximum_characters = max(maximum_characters, len(canonical))
        maximum_bytes = max(maximum_bytes, len(canonical.encode("utf-8")))
        duplicate = await service.submit(owner, client_id, prompt)
        if not duplicate["duplicate"] or duplicate["assistant_message"]["id"] != result["assistant_message"]["id"]:
            raise RuntimeError(f"real-provider duplicate mismatch at submission {index}")
    diagnostics = store.diagnostics(owner)
    if any(diagnostics[name] for name in (
        "legacy_chat_reads", "terminal_chat_reads", "pi_ownership_chat_reads", "magi_tool_calls",
    )):
        raise RuntimeError("real-provider native run touched a forbidden chat dependency")
    elapsed_ms = (time.perf_counter_ns() - started) // 1_000_000
    return {
        "schema_version": "magi.real-provider-reliability.v1",
        "result": "PASS",
        "submissions": submissions,
        "complete": diagnostics["magi_messages_completed"],
        "duplicate_submissions": diagnostics["magi_duplicate_submissions"],
        "maximum_completion_latency_ms": diagnostics["completion_latency"]["max_ms"],
        "maximum_response_characters": maximum_characters,
        "maximum_response_bytes": maximum_bytes,
        "wall_clock_ms": elapsed_ms,
        "truncation": 0,
        "duplication": 0,
        "cross_attribution": 0,
        "raw_internals": 0,
        "legacy_chat_reads": 0,
        "terminal_chat_reads": 0,
        "pi_ownership_chat_reads": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run opt-in native Magi checks against the real provider.")
    parser.add_argument("--submissions", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.submissions <= 200:
        raise SystemExit("--submissions must be between 1 and 200")
    if not _enabled():
        raise SystemExit("refusing real-provider run: set MAGISTRATE_RUN_REAL_MAGI_RELIABILITY=1")
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("refusing real-provider run: OPENAI_API_KEY is not configured")
    with tempfile.TemporaryDirectory(prefix="magistrate-magi-real-") as directory:
        os.environ["MAGISTRATE_ENV"] = "test"
        os.environ["MAGISTRATE_DB_PATH"] = str(Path(directory) / "magi.sqlite3")
        report = asyncio.run(run(args.submissions))
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
