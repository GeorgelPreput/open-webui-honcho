"""Resolve (workspace, subject_peer, speaker_peer, observation_mode) per request.

The sidecar owns identity resolution so that both callers converge on the same scope:
- the Filter sends ``X-Honcho-User-Email`` (+ optional ``X-Honcho-Peer``/``-Workspace``/
  ``-Observation-Mode``);
- model tool calls arrive with the same headers, templated by Open WebUI
  (``X-Honcho-User-Email: {{USER_EMAIL}}``).

Precedence (highest first): explicit peer (``X-Honcho-Peer`` / body ``peer``) → derived from
the login email → env default → hardcoded ``user``. Sharing with another Honcho client works
iff the resolved subject peer equals the peer id that client already uses (e.g. ``alice``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Settings, get_settings

# Honcho peer ids: keep to a conservative, url/slug-safe charset.
_NON_PEER_CHARS = re.compile(r"[^a-z0-9_-]+")


def sanitize_peer(value: str | None) -> str:
    """Lowercase, collapse any run of disallowed chars to ``-``, strip leading/trailing ``-``.

    ``alice`` -> ``alice``; ``alice.smith`` -> ``alice-smith``; ``""`` -> ``""``.
    """
    if not value:
        return ""
    return _NON_PEER_CHARS.sub("-", value.strip().lower()).strip("-")


def derive_subject(*, email: str | None, user_id: str | None, mode: str) -> str:
    """Derive a subject peer id from the Open WebUI login, per ``mode``.

    Returns ``""`` when nothing usable is present (caller then falls back to the env default).
    """
    email = (email or "").strip()
    user_id = (user_id or "").strip()
    if mode == "email" and email:
        return sanitize_peer(email)
    if mode == "userid" and user_id:
        return sanitize_peer(user_id)
    # default: "localpart" (also the safety fallback for unknown modes)
    if email and "@" in email:
        return sanitize_peer(email.split("@", 1)[0])
    if email:
        return sanitize_peer(email)
    return ""


@dataclass(frozen=True)
class Identity:
    workspace: str
    subject_peer: str
    speaker_peer: str
    observation_mode: str  # "unified" | "directional"


def resolve(
    *,
    workspace: str | None = None,
    peer: str | None = None,
    user_email: str | None = None,
    user_id: str | None = None,
    observation_mode: str | None = None,
    speaker_peer: str | None = None,
    settings: Settings | None = None,
) -> Identity:
    s = settings or get_settings()

    mode = (observation_mode or s.honcho_observation_mode or "unified").strip().lower()
    if mode not in ("unified", "directional"):
        mode = "unified"

    # subject: explicit peer wins, else derive from the login, else env default.
    subject = sanitize_peer(peer)
    if not subject:
        subject = derive_subject(email=user_email, user_id=user_id, mode=s.honcho_peer_derivation)
    if not subject:
        # Guard (review C3): never silently serve an empty peer. Fall back to the env default.
        # For multi-tenant deployments set HONCHO_PEER_DERIVATION=email (or UserValves.peer) so
        # distinct logins never collide on this fallback.
        subject = sanitize_peer(s.honcho_default_peer) or "user"

    return Identity(
        workspace=(workspace or s.honcho_default_workspace).strip() or "code",
        subject_peer=subject,
        speaker_peer=(speaker_peer or s.honcho_speaker_peer).strip() or "open_webui",
        observation_mode=mode,
    )
