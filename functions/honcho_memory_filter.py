"""
title: Honcho Memory
author: Georgel Preput
version: 0.2.0
license: MIT
description: Shared long-term memory for Open WebUI, backed by Honcho via the honcho-tools sidecar.
    inlet recalls relevant conclusions and injects the memory policy; outlet persists the turn so
    Honcho's deriver can learn. Talks only to the sidecar over HTTP — no third-party deps.
"""

# Open WebUI *Filter* Function (inlet/outlet). Upload via Admin Settings -> Functions, then
# attach to the model(s) you want memory on. It runs in-process inside Open WebUI.
#
# Design:
# - NO `requirements:` — uses `httpx` (already on the Open WebUI image). ALL Honcho/SDK logic
#   lives in the honcho-tools sidecar; this Filter just calls its /internal/{recall,save}.
# - Fails open: the ENTIRE inlet/outlet body is wrapped in try/except and returns `body`
#   unchanged on any error, because Open WebUI RE-RAISES inlet exceptions and would abort the
#   chat. A memory backend hiccup must never break or noticeably delay a chat.
# - Identity: the sidecar resolves it. We send the login email (X-Honcho-User-Email) so the
#   sidecar derives the subject peer; UserValves.peer / workspace / observation_mode override.
#
# The SYSTEM_PROMPT block is generated from prompts/system_prompt.md by scripts/build_filter.py
# — do not hand-edit between the sentinels.

from __future__ import annotations

import httpx
from pydantic import BaseModel, Field

# >>> SYSTEM_PROMPT (generated from prompts/system_prompt.md) >>>
SYSTEM_PROMPT = """\
# Honcho memory — operating instructions

You have access to a persistent, cross-application memory about the user, backed by Honcho. The
same memory is shared with the user's other assistants (e.g. Claude Code), so what you learn here
is available there and vice-versa. Treat it as a long-term record of who the user is and how they
like to work — not a scratchpad for this one conversation.

## Recall

- Relevant facts already known about the user may be injected into the conversation as a
  "Known about the user" block. Use them: respect stated preferences, don't re-ask what is
  already recorded, and tailor your answers accordingly.
- If you are unsure whether something about the user is known, prefer the `honcho_search` or
  `honcho_get_context` tools over guessing. Query when the user's history is plausibly relevant.
- Injected memory reflects what was true when it was written. If a fact looks stale or the user
  contradicts it, trust the user and update the memory.

## What is worth remembering

Save a memory (a "conclusion") only for **durable, reusable** facts:

- **Who the user is** — role, expertise, environment, tools, identities.
- **Preferences & feedback** — how they want you to work; corrections they have given. Capture the
  *why*, not just the rule.
- **Project / ongoing-work context** — goals and constraints not obvious from the immediate
  question. Convert relative dates ("yesterday", "next week") to absolute dates.
- **References** — durable pointers to resources, dashboards, endpoints, tickets.

Do **not** save: one-off task details, transient conversation state, things trivially re-derivable,
secrets/credentials, or anything the user asks you to forget.

## How to save

- Before creating a conclusion, check existing memory (`list_conclusions` / `search`) and
  **refine an existing one rather than duplicating** it.
- Write each conclusion as a single, self-contained, app-neutral statement that will still make
  sense months from now and in a different tool.
- Save proactively when you learn something durable, but do not announce it intrusively — a brief
  note is enough. When in doubt about whether something is durable, ask the user before saving.
- Memory is **shared** across the user's assistants. Write facts that are true in general, not
  only within this chat.

## Tools available

- `honcho_search` — semantic search over the user's memory/messages.
- `honcho_get_context` / `honcho_get_representation` — what is currently known about the user.
- `honcho_list_conclusions` — review saved conclusions (use before creating, to avoid duplicates).
- `honcho_create_conclusion` — save a new durable fact.
- `honcho_delete_conclusion` — remove an incorrect or obsolete fact (find its id via `honcho_list_conclusions`).
- `honcho_chat` — ask Honcho a dialectic/psychological question about the user.
"""
# <<< SYSTEM_PROMPT <<<

_TOOL_HINT = (
    "Honcho memory tools are available — call honcho_search / honcho_get_context to recall facts "
    "about the user, and honcho_chat for dialectic/psychological questions. Prefer querying over "
    "guessing when the user's history is plausibly relevant."
)


def _text_of(content) -> str:
    """Message content may be a plain string or a list of parts (multimodal). Return its text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        ]
        if not texts:
            texts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("text")]
        return "\n".join(t for t in texts if t)
    return ""


def _assistant_text(msg: dict) -> str:
    """Assistant `content` is often empty on reasoning/tool turns; reconstruct from `output`."""
    txt = _text_of(msg.get("content"))
    if txt.strip():
        return txt
    out = msg.get("output")
    if isinstance(out, str):
        return out
    if isinstance(out, list):
        chunks: list[str] = []
        for item in out:
            if not isinstance(item, dict):
                continue
            c = item.get("content")
            if isinstance(c, list):
                chunks.extend(p.get("text", "") for p in c if isinstance(p, dict) and p.get("text"))
            elif isinstance(c, str) and c:
                chunks.append(c)
            elif item.get("text"):
                chunks.append(item["text"])
        if chunks:
            return "\n".join(chunks)
    return txt  # may be empty; caller skips empty messages


class Filter:
    class Valves(BaseModel):
        enabled: bool = Field(default=True, description="Master switch for the memory filter.")
        sidecar_base_url: str = Field(default="http://honcho-tools:8000")
        sidecar_api_key: str = Field(default="", description="Bearer for the honcho-tools server.")
        default_workspace: str = Field(default="code")
        default_peer: str = Field(
            default="", description="Blank = sidecar derives from login email."
        )
        observation_mode: str = Field(default="", description="Blank = sidecar default (unified).")
        recall_enabled: bool = Field(default=True, description="Inject relevant memory in inlet.")
        save_enabled: bool = Field(default=True, description="Persist the turn in outlet.")
        inject_policy: bool = Field(default=True, description="Inject the memory policy on turn 1.")
        system_prompt: str = Field(default=SYSTEM_PROMPT, description="Editable memory policy.")
        recall_timeout: float = Field(default=3.0)
        save_timeout: float = Field(default=4.0)
        connect_timeout: float = Field(default=1.0)
        max_recall_chars: int = Field(default=4000)
        priority: int = Field(default=0, description="Filter order; lower runs earlier.")

    class UserValves(BaseModel):
        peer: str = Field(default="", description="Your Honcho peer (blank = derived from email).")
        workspace: str = Field(default="", description="Honcho workspace (blank = default).")
        enabled: bool = Field(default=True, description="Enable memory for your chats.")

    def __init__(self) -> None:
        self.valves = self.Valves()

    # -- helpers ------------------------------------------------------------
    def _headers(self, __user__: dict | None, uv) -> dict:
        h: dict[str, str] = {}
        if self.valves.sidecar_api_key:
            h["Authorization"] = f"Bearer {self.valves.sidecar_api_key}"
        email = ((__user__ or {}).get("email") or "").strip()
        if email:
            h["X-Honcho-User-Email"] = email
        peer = (getattr(uv, "peer", "") or "").strip() or self.valves.default_peer.strip()
        if peer:
            h["X-Honcho-Peer"] = peer
        workspace = (
            getattr(uv, "workspace", "") or ""
        ).strip() or self.valves.default_workspace.strip()
        if workspace:
            h["X-Honcho-Workspace"] = workspace
        if self.valves.observation_mode.strip():
            h["X-Honcho-Observation-Mode"] = self.valves.observation_mode.strip()
        return h

    @staticmethod
    def _prepend_system(text: str, messages: list) -> list:
        if messages and isinstance(messages[0], dict) and messages[0].get("role") == "system":
            merged = dict(messages[0])
            existing = _text_of(merged.get("content", ""))
            merged["content"] = f"{text}\n\n{existing}".strip()
            return [merged, *messages[1:]]
        return [{"role": "system", "content": text}, *messages]

    # -- recall (UserPromptSubmit-hook analog) ------------------------------
    async def inlet(
        self, body: dict, __user__: dict | None = None, __metadata__: dict | None = None
    ) -> dict:
        try:
            if not self.valves.enabled:
                return body
            uv = (__user__ or {}).get("valves")
            if uv is not None and not getattr(uv, "enabled", True):
                return body

            messages = body.get("messages") or []
            last_user = ""
            for m in reversed(messages):
                if isinstance(m, dict) and m.get("role") == "user":
                    last_user = _text_of(m.get("content"))
                    break

            headers = self._headers(__user__, uv)
            chat_id = (__metadata__ or {}).get("chat_id")
            if chat_id:
                headers["X-Honcho-Chat-Id"] = str(chat_id)

            block = ""
            if self.valves.recall_enabled and last_user.strip():
                try:
                    timeout = httpx.Timeout(
                        self.valves.recall_timeout, connect=self.valves.connect_timeout
                    )
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        r = await client.post(
                            f"{self.valves.sidecar_base_url}/internal/recall",
                            json={"last_user_message": last_user[:4000]},
                            headers=headers,
                        )
                    if r.status_code == 200:
                        block = (r.json().get("block") or "")[: self.valves.max_recall_chars]
                except Exception:
                    block = ""  # fail open

            first_turn = not any(
                isinstance(m, dict) and m.get("role") == "assistant" for m in messages
            )
            preamble: list[str] = []
            if first_turn and self.valves.inject_policy:
                preamble.append(self.valves.system_prompt.strip())
                preamble.append(_TOOL_HINT)
            if block:
                preamble.append(f"Known about the user (from memory):\n{block}")

            if preamble:
                body["messages"] = self._prepend_system("\n\n".join(preamble), messages)
            return body
        except Exception:
            return body  # never break the chat

    # -- save (saveMessages analog) -----------------------------------------
    async def outlet(
        self, body: dict, __user__: dict | None = None, __metadata__: dict | None = None
    ) -> dict:
        try:
            if not (self.valves.enabled and self.valves.save_enabled):
                return body
            uv = (__user__ or {}).get("valves")
            if uv is not None and not getattr(uv, "enabled", True):
                return body

            messages = body.get("messages") or []
            user_text = ""
            assistant_text = ""
            for m in reversed(messages):
                if not isinstance(m, dict):
                    continue
                role = m.get("role")
                if role == "assistant" and not assistant_text:
                    assistant_text = _assistant_text(m)
                elif role == "user" and not user_text:
                    user_text = _text_of(m.get("content"))
                if user_text and assistant_text:
                    break

            if not (user_text.strip() or assistant_text.strip()):
                return body

            headers = self._headers(__user__, uv)
            chat_id = body.get("chat_id") or (__metadata__ or {}).get("chat_id")
            if chat_id:
                headers["X-Honcho-Chat-Id"] = str(chat_id)

            try:
                timeout = httpx.Timeout(
                    self.valves.save_timeout, connect=self.valves.connect_timeout
                )
                async with httpx.AsyncClient(timeout=timeout) as client:
                    await client.post(
                        f"{self.valves.sidecar_base_url}/internal/save",
                        json={
                            "chat_id": str(chat_id) if chat_id else None,
                            "user_message": user_text or None,
                            "assistant_message": assistant_text or None,
                        },
                        headers=headers,
                    )
            except Exception:
                pass  # fail open
            return body
        except Exception:
            return body
