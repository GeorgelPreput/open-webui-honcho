"""Transport-agnostic memory operations (the MCPO-refactor seam).

Functions take an ``Identity`` + plain args and return plain dict/str — NO FastAPI types — so a
future MCP server could import this module unchanged. ``tool_server.py`` is a thin adapter.

- ``recall`` / ``save`` are fail-open (they back the Filter's inlet/outlet; a Honcho hiccup must
  never break a chat).
- ``tool_*`` functions let exceptions propagate (the model called them deliberately; the HTTP
  layer surfaces the error).
"""

from __future__ import annotations

import re

from .honcho_client import HonchoClient
from .identity import Identity

_LEADING_TAG = re.compile(r"^\s*\[.*?\]\s*")  # strip a leading "[2026-… ] " timestamp tag
_LEADING_BULLET = re.compile(r"^\s*[-*]\s*")  # strip a leading "- " bullet


def session_name(ident: Identity, chat_id: str | None) -> str:
    cid = (chat_id or "default").replace("/", "-")
    return f"owui-{ident.subject_peer}-{cid}"


def format_recall_block(
    subject: str, representation: str | None, peer_card: list[str] | None
) -> str:
    """Mirror the Claude Code plugin's UserPromptSubmit formatting so recall reads identically.

    ``[Honcho Memory for <subject>]: Relevant conclusions: a; b; c | Profile: x; y; z``
    """
    parts: list[str] = []
    if representation:
        lines = [
            ln
            for ln in representation.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        cleaned = []
        for line in lines[:5]:
            line = _LEADING_BULLET.sub("", _LEADING_TAG.sub("", line)).strip()
            if line:
                cleaned.append(line)
        if cleaned:
            parts.append("Relevant conclusions: " + "; ".join(cleaned))
    if peer_card:
        parts.append("Profile: " + "; ".join(peer_card))
    if not parts:
        return ""
    return f"[Honcho Memory for {subject}]: " + " | ".join(parts)


# -- Filter-facing (fail-open) ---------------------------------------------
def recall(client: HonchoClient, ident: Identity, last_user_message: str | None) -> str:
    try:
        ctx = client.get_context(ident, search_query=(last_user_message or None))
        return format_recall_block(
            ident.subject_peer, ctx.get("representation"), ctx.get("peer_card")
        )
    except Exception:
        return ""


def save(
    client: HonchoClient,
    ident: Identity,
    *,
    chat_id: str | None,
    user_message: str | None,
    assistant_message: str | None,
) -> bool:
    try:
        client.save_messages(
            ident,
            session_name=session_name(ident, chat_id),
            user_message=user_message,
            assistant_message=assistant_message,
        )
        return True
    except Exception:
        return False


# -- Model-facing tools (may raise; HTTP layer surfaces errors) -------------
def tool_search(
    client: HonchoClient,
    ident: Identity,
    query: str,
    *,
    scope: str,
    limit: int,
    chat_id: str | None = None,
) -> dict:
    sname = session_name(ident, chat_id) if scope == "session" else None
    return {"results": client.search(ident, query, scope=scope, limit=limit, session_name=sname)}


def tool_chat(client: HonchoClient, ident: Identity, query: str, *, reasoning_level: str) -> dict:
    return {"answer": client.chat(ident, query, reasoning_level=reasoning_level)}


def tool_create_conclusion(client: HonchoClient, ident: Identity, content: str) -> dict:
    return {"created": client.create_conclusion(ident, content)}


def tool_list_conclusions(client: HonchoClient, ident: Identity, *, page: int, size: int) -> dict:
    return client.list_conclusions(ident, page=page, size=size)


def tool_delete_conclusion(client: HonchoClient, ident: Identity, conclusion_id: str) -> dict:
    client.delete_conclusion(ident, conclusion_id)
    return {"deleted": conclusion_id}


def tool_get_context(client: HonchoClient, ident: Identity, *, max_conclusions: int) -> dict:
    return client.get_context(ident, max_conclusions=max_conclusions)


def tool_get_representation(client: HonchoClient, ident: Identity) -> dict:
    return {"representation": client.get_representation(ident)}
