"""Environment-driven settings and identity defaults.

Workspace, subject peer, speaker peer and observation mode are *defaults / fallbacks*. The
caller (Open WebUI, via request headers/params or the Filter's Valves) supplies the actual
identity per request; see ``identity.py``. Precedence is: request > env (here) > hardcoded.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Honcho connection ---
    # Honcho cloud by default; point at your own instance (self-hosted or private) via env.
    honcho_base_url: str = "https://api.honcho.dev"
    honcho_api_key: str = ""  # Honcho API key / workspace-scoped JWT; required at runtime

    # --- Identity defaults / fallbacks (overridable per request) ---
    # The *subject* is the human the memory is about. Its name is intentionally NOT a real
    # person here; the deploy sets it (or it is derived from the login email — see below).
    honcho_default_workspace: str = "default"
    honcho_default_peer: str = "user"
    # The *speaker* peer authors the assistant's messages (provenance only). In unified mode it
    # is NOT the conclusion observer; it only distinguishes who said what in saved sessions.
    honcho_speaker_peer: str = "open_webui"
    # "unified" (self-scoped: observer==observed==subject) or "directional" (observer=speaker).
    honcho_observation_mode: str = "unified"
    # How to derive the subject peer from the Open WebUI login: "localpart" (before @),
    # "email" (full, multi-tenant-safe), or "userid" (Open WebUI user id).
    honcho_peer_derivation: str = "localpart"

    # --- Tool-server self-protection (bearer Open WebUI sends). Empty => no auth. ---
    tool_server_api_key: str = ""
    tool_server_host: str = "0.0.0.0"
    tool_server_port: int = 8000

    # --- Fail-open timeout for Honcho calls (seconds) so memory never blocks a chat. ---
    honcho_timeout_seconds: float = 8.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
