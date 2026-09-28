"""Authenticated project-memory API.

The caller never supplies a tenant or owner.  Those qualifiers are derived from
the bearer principal before every store operation.
"""
from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.auth import Principal, require_scope
from app.project_memory import (
    MAX_MEMORY_SEARCH_RESULTS,
    MEMORY_KINDS,
    MEMORY_SCHEMA,
    MagiContextAssembler,
    MemoryConflict,
    MemoryNotFound,
    MemoryScope,
    ProjectMemoryStore,
)

router = APIRouter(prefix="/api/v1/magi/memory", tags=["Magi project memory"])
store = ProjectMemoryStore()
assembler = MagiContextAssembler(store)
MemoryKind = Literal[
    "goal", "architecture-decision", "user-decision", "conversation-fact",
    "repository", "prior-objective", "completed-outcome", "artifact",
    "failed-approach", "question", "preference", "fleet-outcome",
]


class MemoryWriteContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    memory_key: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
    kind: MemoryKind
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=8_000)
    importance: int = Field(default=3, ge=1, le=5)
    source_id: str = Field(default="explicit", min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


def _scope(
    principal: Principal,
    project_reference: str,
    repository_reference: str,
) -> MemoryScope:
    try:
        return MemoryScope.for_project(
            principal.user_id,
            project_reference,
            repository_reference=repository_reference,
        )
    except MemoryNotFound as exc:
        # Do not reveal whether an opaque project/repository belongs to another principal.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _scope_parameters(
    project_id: str = Query(
        os.getenv("MAGISTRATE_MAGI_PROJECT", "Magistrate"),
        min_length=1,
        max_length=160,
    ),
    repository_id: str = Query("", max_length=160),
) -> tuple[str, str]:
    # Workspace, organization, tenant, and owner are server-derived from the
    # authenticated durable project. Clients cannot forge those qualifiers.
    return project_id, repository_id


@router.put("/entries/{memory_key}")
def put_memory(
    memory_key: str,
    contract: MemoryWriteContract,
    scope_values: tuple[str, str] = Depends(_scope_parameters),
    principal: Principal = Depends(require_scope("command")),
):
    if memory_key != contract.memory_key:
        raise HTTPException(status_code=409, detail="Memory path and contract identities differ.")
    scope = _scope(principal, *scope_values)
    try:
        entry = store.put(
            principal.user_id, scope,
            memory_key=contract.memory_key, kind=contract.kind,
            title=contract.title, content=contract.content,
            importance=contract.importance, source_kind="authenticated-user",
            source_id=contract.source_id, actor_session_id=principal.session_id,
        )
    except MemoryConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"schema_version": MEMORY_SCHEMA, "scope": scope.public(), "entry": entry}


@router.delete("/entries/{entry_id}")
def delete_memory(
    entry_id: str,
    scope_values: tuple[str, str] = Depends(_scope_parameters),
    principal: Principal = Depends(require_scope("command")),
):
    scope = _scope(principal, *scope_values)
    try:
        store.delete(
            principal.user_id, scope, entry_id, actor_session_id=principal.session_id,
        )
    except MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"schema_version": MEMORY_SCHEMA, "status": "deleted", "entry_id": entry_id}


@router.get("/search")
def search_memory(
    q: str = Query(min_length=1, max_length=1_000),
    kinds: str | None = Query(None, max_length=400),
    limit: int = Query(10, ge=1, le=MAX_MEMORY_SEARCH_RESULTS),
    scope_values: tuple[str, str] = Depends(_scope_parameters),
    principal: Principal = Depends(require_scope("read")),
):
    scope = _scope(principal, *scope_values)
    selected = None
    if kinds:
        selected = {item.strip() for item in kinds.split(",") if item.strip()}
        if not selected or not selected.issubset(MEMORY_KINDS):
            raise HTTPException(status_code=422, detail="Memory search kind is unsupported.")
    try:
        results = store.search(
            principal.user_id, scope, q, kinds=selected, limit=limit,
            purpose="api-search", actor_session_id=principal.session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "schema_version": MEMORY_SCHEMA, "scope": scope.public(),
        "results": results, "count": len(results),
    }


@router.get("/context")
def preview_context(
    q: str = Query(min_length=1, max_length=1_000),
    modality: Literal["text", "voice"] = Query("text"),
    scope_values: tuple[str, str] = Depends(_scope_parameters),
    principal: Principal = Depends(require_scope("read")),
):
    scope = _scope(principal, *scope_values)
    try:
        payload = assembler.assemble_chat(
            principal.user_id, q, scope=scope, modality=modality,
            actor_session_id=principal.session_id,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    import json
    return json.loads(payload)


@router.get("/audit")
def get_memory_audit(
    limit: int = Query(100, ge=1, le=200),
    scope_values: tuple[str, str] = Depends(_scope_parameters),
    principal: Principal = Depends(require_scope("read")),
):
    scope = _scope(principal, *scope_values)
    try:
        return store.audit(principal.user_id, scope, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
