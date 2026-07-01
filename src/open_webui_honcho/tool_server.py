"""Honcho OpenAPI tool server for Open WebUI + internal endpoints for the Filter.

Two route groups over one shared Honcho client:
- **Model tools** (in the OpenAPI schema; register in Open WebUI Admin -> Tools): the
  ``operation_id``s are namespaced ``honcho_*`` so they never collide with other tool servers
  (e.g. mcpo). Endpoint summaries/descriptions are what the model sees.
- **Internal** (``/internal/*``, excluded from schema): recall/save for the Filter. Fail-open.

Identity (workspace / subject / observation mode) is resolved centrally from request headers
(``X-Honcho-User-Email``, ``X-Honcho-Peer``, ``X-Honcho-Workspace``,
``X-Honcho-Observation-Mode``, ``X-Honcho-Chat-Id``) with optional body overrides; see
``identity.resolve``. Open WebUI templates ``{{USER_EMAIL}}``/``{{CHAT_ID}}`` into the headers.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
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


# --- auth ------------------------------------------------------------------
def require_auth(authorization: Annotated[str | None, Header()] = None) -> None:
    """Optional bearer protection. No-op when TOOL_SERVER_API_KEY is unset."""
    expected = get_settings().tool_server_api_key
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


AuthDep = Annotated[None, Depends(require_auth)]


@lru_cache
def _client() -> HonchoClient:
    return HonchoClient(get_settings())


# --- identity from headers -------------------------------------------------
@dataclass
class ReqHeaders:
    peer: str | None
    workspace: str | None
    observation_mode: str | None
    user_email: str | None
    chat_id: str | None


def req_headers(
    x_honcho_peer: Annotated[str | None, Header()] = None,
    x_honcho_workspace: Annotated[str | None, Header()] = None,
    x_honcho_observation_mode: Annotated[str | None, Header()] = None,
    x_honcho_user_email: Annotated[str | None, Header()] = None,
    x_honcho_chat_id: Annotated[str | None, Header()] = None,
) -> ReqHeaders:
    return ReqHeaders(
        peer=x_honcho_peer,
        workspace=x_honcho_workspace,
        observation_mode=x_honcho_observation_mode,
        user_email=x_honcho_user_email,
        chat_id=x_honcho_chat_id,
    )


HeadersDep = Annotated[ReqHeaders, Depends(req_headers)]


class _Overrides(BaseModel):
    """Optional per-call identity overrides (headers are the primary source)."""

    workspace: str | None = Field(None, description="Override the Honcho workspace.")
    peer: str | None = Field(None, description="Override the subject peer.")
    observation_mode: str | None = Field(None, description="'unified' or 'directional'.")


def _identity(h: ReqHeaders, body: _Overrides | None = None) -> Identity:
    b = body or _Overrides()
    return resolve(
        workspace=b.workspace or h.workspace,
        peer=b.peer or h.peer,
        user_email=h.user_email,
        observation_mode=b.observation_mode or h.observation_mode,
    )


def _guard(fn):
    """Run a tool op, translating Honcho errors into a clean 502 for the model."""
    try:
        return fn()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"honcho error: {e}") from e


# --- request models --------------------------------------------------------
class SearchRequest(_Overrides):
    query: str = Field(..., description="What to search the user's memory for.")
    scope: str = Field("workspace", description="'workspace' (all history, default) or 'session'.")
    limit: int = Field(5, ge=1, le=50)


class ChatRequest(_Overrides):
    query: str = Field(..., description="A natural-language question about the user.")
    reasoning_level: str = Field("medium", description="minimal | low | medium | high | max")


class CreateConclusionRequest(_Overrides):
    content: str = Field(..., description="A single durable, self-contained fact about the user.")


class ListConclusionsRequest(_Overrides):
    page: int = Field(1, ge=1)
    size: int = Field(25, ge=1, le=100)


class ContextRequest(_Overrides):
    max_conclusions: int = Field(25, ge=1, le=100)


class RepresentationRequest(_Overrides):
    pass


# --- health ----------------------------------------------------------------
@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}


# --- model tools -----------------------------------------------------------
@app.post("/search", summary="Search the user's memory", operation_id="honcho_search")
def search(req: SearchRequest, _: AuthDep, h: HeadersDep) -> dict:
    """Semantic search over what is remembered about the user (messages)."""
    ident = _identity(h, req)
    return _guard(
        lambda: svc.tool_search(
            _client(), ident, req.query, scope=req.scope, limit=req.limit, chat_id=h.chat_id
        )
    )


@app.post("/chat", summary="Ask a dialectic question about the user", operation_id="honcho_chat")
def chat(req: ChatRequest, _: AuthDep, h: HeadersDep) -> dict:
    """Ask Honcho to reason about the user (dialectic). Use for 'what does the user prefer/know?'."""
    ident = _identity(h, req)
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
    ident = _identity(h, req)
    return _guard(lambda: svc.tool_create_conclusion(_client(), ident, req.content))


@app.post(
    "/conclusions/list",
    summary="List saved conclusions about the user",
    operation_id="honcho_list_conclusions",
)
def list_conclusions(req: ListConclusionsRequest, _: AuthDep, h: HeadersDep) -> dict:
    """Review saved conclusions. Use before creating one to avoid duplicates, or to find an id."""
    ident = _identity(h, req)
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
    ident = _identity(h, req)
    return _guard(
        lambda: svc.tool_get_context(_client(), ident, max_conclusions=req.max_conclusions)
    )


@app.post(
    "/representation",
    summary="Get the user's representation",
    operation_id="honcho_get_representation",
)
def get_representation(req: RepresentationRequest, _: AuthDep, h: HeadersDep) -> dict:
    """Lighter-weight than honcho_get_context: just the representation string."""
    ident = _identity(h, req)
    return _guard(lambda: svc.tool_get_representation(_client(), ident))


# --- internal (Filter) -----------------------------------------------------
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
