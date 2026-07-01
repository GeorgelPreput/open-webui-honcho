"""Thin wrapper over the honcho-ai SDK. ALL SDK calls live here.

Surface pinned against honcho-ai **2.1.2** (verified live, M1):
- ``Honcho(api_key=, base_url=, workspace_id=, timeout=, max_retries=)``
- ``honcho.peer(id)`` / ``honcho.session(id, peers=[...])`` / ``honcho.search(query, limit=)``
- ``peer.context(target=, search_query=, search_top_k=, search_max_distance=,
  include_most_frequent=, max_conclusions=) -> PeerContextResponse(.representation, .peer_card)``
- ``peer.representation(target=, ...) -> str``; ``peer.chat(query, target=, reasoning_level=) -> str|None``
- ``peer.conclusions_of(target) -> ConclusionScope`` with ``.create([{content[, session_id]}])``
  (scope injects observer==peer, observed==target), ``.list(page=, size=)``, ``.delete(id)``
- ``session.add_messages([peer.message(text)])``; ``SessionPeerConfig(observe_me=, observe_others=)``

Scope (unified vs directional) mirrors the Claude Code plugin exactly:
- unified   : observer==observed==subject; chat/context/representation on peer(subject), no target.
- directional: observer=speaker, observed=subject; reads on peer(speaker) with target=subject.
"""

from __future__ import annotations

from typing import Any

from honcho import Honcho
from honcho.api_types import SessionPeerConfig

from .config import Settings, get_settings
from .identity import Identity

_ASSISTANT_MAX_CHARS = 3000


class HonchoClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if not self._settings.honcho_api_key:
            raise RuntimeError("HONCHO_API_KEY is not set")
        self._clients: dict[str, Any] = {}  # workspace -> Honcho client (cached)

    # -- client / scope helpers ---------------------------------------------
    def _client(self, workspace: str) -> Honcho:
        if workspace not in self._clients:
            self._clients[workspace] = Honcho(
                api_key=self._settings.honcho_api_key,
                base_url=self._settings.honcho_base_url,
                workspace_id=workspace,
                timeout=self._settings.honcho_timeout_seconds,
                max_retries=2,
            )
        return self._clients[workspace]

    def _read_peer_and_target(self, ident: Identity):
        """(peer_obj, target) for chat/context/representation. unified -> no target."""
        c = self._client(ident.workspace)
        if ident.observation_mode == "directional":
            return c.peer(ident.speaker_peer), ident.subject_peer
        return c.peer(ident.subject_peer), None

    def _conclusion_scope(self, ident: Identity):
        c = self._client(ident.workspace)
        actor = c.peer(
            ident.speaker_peer if ident.observation_mode == "directional" else ident.subject_peer
        )
        return actor.conclusions_of(ident.subject_peer)

    # -- recall -------------------------------------------------------------
    def get_context(
        self,
        ident: Identity,
        *,
        search_query: str | None = None,
        search_top_k: int = 5,
        search_max_distance: float = 0.7,
        max_conclusions: int = 15,
    ) -> dict:
        peer, target = self._read_peer_and_target(ident)
        kwargs: dict[str, Any] = {
            "search_top_k": search_top_k,
            "search_max_distance": search_max_distance,
            "max_conclusions": max_conclusions,
            "include_most_frequent": True,
        }
        if target:
            kwargs["target"] = target
        if search_query:
            kwargs["search_query"] = search_query
        ctx = peer.context(**kwargs)
        return {
            "representation": getattr(ctx, "representation", None),
            "peer_card": getattr(ctx, "peer_card", None),
        }

    def get_representation(self, ident: Identity) -> str:
        peer, target = self._read_peer_and_target(ident)
        rep = peer.representation(target=target) if target else peer.representation()
        return rep or ""

    # -- dialectic ----------------------------------------------------------
    def chat(self, ident: Identity, query: str, *, reasoning_level: str = "medium") -> str:
        peer, target = self._read_peer_and_target(ident)
        kwargs: dict[str, Any] = {"reasoning_level": reasoning_level}
        if target:
            kwargs["target"] = target
        return peer.chat(query, **kwargs) or ""

    # -- search -------------------------------------------------------------
    def search(
        self,
        ident: Identity,
        query: str,
        *,
        scope: str = "workspace",
        limit: int = 5,
        session_name: str | None = None,
    ) -> list[dict]:
        c = self._client(ident.workspace)
        if scope == "session" and session_name:
            msgs = c.session(session_name).search(query, limit=limit)
        else:
            msgs = c.search(query, limit=limit)
        out = []
        for m in msgs:
            out.append(
                {
                    "content": getattr(m, "content", ""),
                    "peer_id": getattr(m, "peer_id", None) or getattr(m, "peer_name", None),
                    "created_at": str(getattr(m, "created_at", "") or ""),
                }
            )
        return out

    # -- conclusions --------------------------------------------------------
    def create_conclusion(
        self, ident: Identity, content: str, *, session_id: str | None = None
    ) -> list[dict]:
        item: dict[str, Any] = {"content": content}
        if session_id:
            item["session_id"] = session_id
        created = self._conclusion_scope(ident).create([item])
        return [{"id": c.id, "content": c.content} for c in created]

    def list_conclusions(self, ident: Identity, *, page: int = 1, size: int = 25) -> dict:
        res = self._conclusion_scope(ident).list(page=page, size=size)
        return {
            "items": [
                {
                    "id": c.id,
                    "content": c.content,
                    "created_at": str(getattr(c, "created_at", "") or ""),
                }
                for c in res.items
            ],
            "total": res.total,
            "page": res.page,
            "pages": res.pages,
        }

    def delete_conclusion(self, ident: Identity, conclusion_id: str) -> None:
        self._conclusion_scope(ident).delete(conclusion_id)

    # -- save (Filter outlet / saveMessages analog) -------------------------
    def save_messages(
        self,
        ident: Identity,
        *,
        session_name: str,
        user_message: str | None = None,
        assistant_message: str | None = None,
    ) -> None:
        """Persist a turn so Honcho's deriver forms conclusions about the subject.

        Peers are registered on the session BEFORE messages are added (mandatory: the deriver
        schedules reasoning based on session membership at message-create time). The subject is
        registered with ``observe_me=True`` so its own messages feed its unified representation.
        """
        c = self._client(ident.workspace)
        subject = c.peer(ident.subject_peer)
        if ident.observation_mode == "directional":
            speaker = c.peer(ident.speaker_peer)
            peers = [
                (subject, SessionPeerConfig(observe_me=True)),
                (speaker, SessionPeerConfig(observe_others=True)),
            ]
        else:
            speaker = c.peer(ident.speaker_peer)
            peers = [(subject, SessionPeerConfig(observe_me=True))]

        session = c.session(session_name, peers=peers)

        messages = []
        if user_message:
            messages.append(subject.message(user_message))  # feeds recall (subject's self-rep)
        if assistant_message:
            messages.append(
                speaker.message(assistant_message[:_ASSISTANT_MAX_CHARS])
            )  # provenance only
        if messages:
            session.add_messages(messages)


def get_client() -> HonchoClient:
    return HonchoClient(get_settings())
