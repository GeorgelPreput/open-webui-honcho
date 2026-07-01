"""Honcho OpenAPI tool server for Open WebUI + internal endpoints for the Filter.

Two route groups over one shared Honcho client:
- **Model tools** (in the OpenAPI schema; register in Open WebUI Admin -> Tools): the
  ``operation_id``s are namespaced ``honcho_*`` so they never collide with other tool servers
  (e.g. mcpo). Endpoint summaries/descriptions are what the model sees.
- **Internal** (``/internal/*``, excluded from schema): recall/save for the Filter. Fail-open.

Identity & auth are transport-injected, NOT model arguments: the ``Authorization`` bearer and the
``X-Honcho-*`` headers are read from the raw request and are deliberately kept OUT of the OpenAPI
schema (no header ``parameters``, no identity fields in the request bodies). This keeps each tool's
model-facing signature clean (only the meaningful args) — Open WebUI's OpenAPI->tool parser skips
tools whose params it can't map, and nullable header params are exactly the kind it trips on.

Open WebUI supplies identity via the tool-server connection: Bearer = ``TOOL_SERVER_API_KEY`` and
Custom Headers ``X-Honcho-User-Email={{USER_EMAIL}}`` / ``X-Honcho-Chat-Id={{CHAT_ID}}``. The
Filter additionally sends ``X-Honcho-Peer`` / ``-Workspace`` / ``-Observation-Mode`` when a valve
overrides them. See ``identity.resolve``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from . import honcho_service as svc
from .config import get_settings
from .honcho_client import HonchoClient
from .identity import Identity, resolve

app = FastAPI(
    title="Honcho Memory",
    version="0.1.0",
    description="Shared long-term memory about the user, backed by Honcho.",
)


# --- auth (read from the raw header; never a documented tool parameter) ------
def require_auth(request: Request) -> None:
    """Optional bearer protection. No-op when TOOL_SERVER_API_KEY is unset."""
    expected = get_settings().tool_server_api_key
    if not expected:
        return
    if request.headers.get("authorization") != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


AuthDep = Annotated[None, Depends(require_auth)]


@lru_cache
def _client() -> HonchoClient:
    return HonchoClient(get_settings())


# --- identity from request headers (kept out of the OpenAPI schema) ----------
@dataclass
class ReqHeaders:
    peer: str | None
    workspace: str | None
    observation_mode: str | None
    user_email: str | None
    chat_id: str | None


def req_headers(request: Request) -> ReqHeaders:
    h = request.headers
    return ReqHeaders(
        peer=h.get("x-honcho-peer"),
        workspace=h.get("x-honcho-workspace"),
        observation_mode=h.get("x-honcho-observation-mode"),
        user_email=h.get("x-honcho-user-email"),
        chat_id=h.get("x-honcho-chat-id"),
    )


HeadersDep = Annotated[ReqHeaders, Depends(req_headers)]


def _identity(h: ReqHeaders) -> Identity:
    return resolve(
        workspace=h.workspace,
        peer=h.peer,
        user_email=h.user_email,
        observation_mode=h.observation_mode,
    )


def _guard(fn):
    """Run a tool op, translating Honcho errors into a clean 502 for the model."""
    try:
        return fn()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"honcho error: {e}") from e


# --- request models (model-facing args only; identity comes from headers) ----
class SearchRequest(BaseModel):
    query: str = Field(..., description="What to search the user's memory for.")
    scope: str = Field("workspace", description="'workspace' (all history, default) or 'session'.")
    limit: int = Field(5, ge=1, le=50)


class ChatRequest(BaseModel):
    query: str = Field(..., description="A natural-language question about the user.")
    reasoning_level: str = Field("medium", description="minimal | low | medium | high | max")


class CreateConclusionRequest(BaseModel):
    content: str = Field(..., description="A single durable, self-contained fact about the user.")


class ListConclusionsRequest(BaseModel):
    page: int = Field(1, ge=1)
    size: int = Field(25, ge=1, le=100)


class ContextRequest(BaseModel):
    max_conclusions: int = Field(25, ge=1, le=100)


# --- health ------------------------------------------------------------------
@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}


# --- model tools -------------------------------------------------------------
@app.post("/search", summary="Search the user's memory", operation_id="honcho_search")
def search(req: SearchRequest, _: AuthDep, h: HeadersDep) -> dict:
    """Semantic search over what is remembered about the user (messages)."""
    ident = _identity(h)
    return _guard(
        lambda: svc.tool_search(
            _client(), ident, req.query, scope=req.scope, limit=req.limit, chat_id=h.chat_id
        )
    )


@app.post("/chat", summary="Ask a dialectic question about the user", operation_id="honcho_chat")
def chat(req: ChatRequest, _: AuthDep, h: HeadersDep) -> dict:
    """Ask Honcho to reason about the user (dialectic). Use for 'what does the user prefer/know?'."""
    ident = _identity(h)
    return _guard(
        lambda: svc.tool_chat(_client(), ident, req.query, reasoning_level=req.reasoning_level)
    )


@app.post(
    "/conclusions/create",
    summary="Save a durable fact about the user",
    operation_id="honcho_create_conclusion",
)
def create_conclusion(req: CreateConclusionRequest, _: AuthDep, h: HeadersDep) -> dict:
    """Save one durable, self-contained fact about the user. Check existing memory first."""
    ident = _identity(h)
    return _guard(lambda: svc.tool_create_conclusion(_client(), ident, req.content))


@app.post(
    "/conclusions/list",
    summary="List saved conclusions about the user",
    operation_id="honcho_list_conclusions",
)
def list_conclusions(req: ListConclusionsRequest, _: AuthDep, h: HeadersDep) -> dict:
    """Review saved conclusions. Use before creating one to avoid duplicates, or to find an id."""
    ident = _identity(h)
    return _guard(lambda: svc.tool_list_conclusions(_client(), ident, page=req.page, size=req.size))


@app.delete(
    "/conclusions/{conclusion_id}",
    summary="Delete a conclusion by id",
    operation_id="honcho_delete_conclusion",
)
def delete_conclusion(conclusion_id: str, _: AuthDep, h: HeadersDep) -> dict:
    """Delete an incorrect or obsolete conclusion (find its id via honcho_list_conclusions)."""
    ident = _identity(h)
    return _guard(lambda: svc.tool_delete_conclusion(_client(), ident, conclusion_id))


@app.post(
    "/context", summary="Get current context about the user", operation_id="honcho_get_context"
)
def get_context(req: ContextRequest, _: AuthDep, h: HeadersDep) -> dict:
    """Get the user's representation + profile card (what is currently known about them)."""
    ident = _identity(h)
    return _guard(
        lambda: svc.tool_get_context(_client(), ident, max_conclusions=req.max_conclusions)
    )


@app.post(
    "/representation",
    summary="Get the user's representation",
    operation_id="honcho_get_representation",
)
def get_representation(_: AuthDep, h: HeadersDep) -> dict:
    """Lighter-weight than honcho_get_context: just the representation string. Takes no arguments."""
    ident = _identity(h)
    return _guard(lambda: svc.tool_get_representation(_client(), ident))


# --- internal (Filter) -------------------------------------------------------
class RecallRequest(BaseModel):
    last_user_message: str | None = None


class SaveRequest(BaseModel):
    chat_id: str | None = None
    user_message: str | None = None
    assistant_message: str | None = None


@app.post("/internal/recall", include_in_schema=False)
def internal_recall(req: RecallRequest, _: AuthDep, h: HeadersDep) -> dict:
    ident = _identity(h)
    return {"block": svc.recall(_client(), ident, req.last_user_message)}  # fail-open in svc


@app.post("/internal/save", include_in_schema=False)
def internal_save(req: SaveRequest, _: AuthDep, h: HeadersDep) -> dict:
    ident = _identity(h)
    chat_id = req.chat_id or h.chat_id
    saved = svc.save(
        _client(),
        ident,
        chat_id=chat_id,
        user_message=req.user_message,
        assistant_message=req.assistant_message,
    )
    return {"saved": saved}


def main() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run(app, host=s.tool_server_host, port=s.tool_server_port)


if __name__ == "__main__":
    main()
