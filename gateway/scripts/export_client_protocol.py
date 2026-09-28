"""Export shared schemas from the actual validators without application startup.

Run with `uv run python -m scripts.export_client_protocol > protocol.json`.
The CLI isolates legacy import-time DB initialization in a disposable database;
it never accesses the configured deployment DB, providers or execution runtime.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


def client_protocol() -> dict:
    from pydantic import TypeAdapter
    from app.contracts import ActivityCatchUpContract, NativeMagiMessageContract
    from app.firstmate_decisions import FirstmateDecisionEventBatch, FirstmateAnswerDecisionToolArguments
    from app.firstmate_execution import FirstmateExecutionEventContract, FirstmateCompletionEvidence
    from app.magi_firstmate_tools import FirstmateSubmitObjectiveContract

    return {
        "schema_version": "magistrate.client-protocol.v1",
        "conversation": {
            "schema_version": "magi.native-chat.v1",
            "submit": "/api/v1/magi/messages",
            "current": "/api/v1/magi/conversations/current",
            "read": "/api/v1/magi/conversations/{conversation_id}",
            "replay": "/api/v1/magi/conversations/{conversation_id}/replay",
            "cancel": "/api/v1/magi/messages/{client_message_id}/cancel",
        },
        "events": {
            "path": "/api/v1/events",
            "schema_version": "magistrate.events.v2",
            "conversation_type": "magi_messages",
            "activity_type": "activity_records",
            "authentication": "first-frame",
        },
        "schemas": {
            "native_message": NativeMagiMessageContract.model_json_schema(),
            "activity_catch_up": ActivityCatchUpContract.model_json_schema(),
            "submit_objective": FirstmateSubmitObjectiveContract.model_json_schema(),
            "execution_event": TypeAdapter(FirstmateExecutionEventContract).json_schema(),
            "completion_evidence": FirstmateCompletionEvidence.model_json_schema(),
            "decision_events": FirstmateDecisionEventBatch.model_json_schema(),
            "answer_decision": FirstmateAnswerDecisionToolArguments.model_json_schema(),
        },
    }


if __name__ == "__main__":
    from cryptography.fernet import Fernet

    # Must happen BEFORE importing any validator that imports app.db. Existing
    # db.py initializes at import, so exporting under an operator's environment
    # would otherwise open/migrate their live database. No runtime imports above.
    with tempfile.TemporaryDirectory(prefix="magistrate-schema-") as temporary:
        for name in list(os.environ):
            if name.startswith(("MAGISTRATE_", "OPENAI_")) or name == "FM_HOME":
                del os.environ[name]
        os.environ.update({
            "MAGISTRATE_ENV": "test",
            "MAGISTRATE_DB_PATH": str(Path(temporary) / "schema.sqlite3"),
            "MAGISTRATE_SECRET_KEY": Fernet.generate_key().decode("ascii"),
        })
        print(json.dumps(client_protocol(), ensure_ascii=False, indent=2, sort_keys=True))
