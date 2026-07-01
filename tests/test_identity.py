"""Unit tests for identity resolution (no network)."""

from open_webui_honcho.config import Settings
from open_webui_honcho.identity import derive_subject, resolve, sanitize_peer


def _settings(**kw) -> Settings:
    # Explicit values so tests don't depend on the ambient environment / a .env file.
    base = dict(
        honcho_api_key="x",
        honcho_default_workspace="default",
        honcho_default_peer="user",
        honcho_speaker_peer="open_webui",
        honcho_observation_mode="unified",
        honcho_peer_derivation="localpart",
    )
    base.update(kw)
    return Settings(**base)


def test_sanitize_peer():
    assert sanitize_peer("alice") == "alice"
    assert sanitize_peer("alice.smith") == "alice-smith"
    assert sanitize_peer("  Mixed.Case+Weird  ") == "mixed-case-weird"
    assert sanitize_peer("--a__b--") == "a__b"  # underscores kept; edge hyphens stripped
    assert sanitize_peer("") == ""
    assert sanitize_peer(None) == ""


def test_derive_localpart():
    assert derive_subject(email="alice@example.com", user_id=None, mode="localpart") == "alice"
    assert (
        derive_subject(email="alice.smith@example.com", user_id=None, mode="localpart")
        == "alice-smith"
    )


def test_derive_email_mode_is_collision_safe():
    assert derive_subject(email="a.b@c.com", user_id=None, mode="email") == "a-b-c-com"


def test_derive_empty_when_no_email():
    assert derive_subject(email="", user_id=None, mode="localpart") == ""


def test_resolve_explicit_peer_wins_and_is_sanitized():
    ident = resolve(peer="Explicit.Peer", user_email="alice@example.com", settings=_settings())
    assert ident.subject_peer == "explicit-peer"


def test_resolve_email_derivation():
    ident = resolve(user_email="alice@example.com", settings=_settings())
    assert ident.subject_peer == "alice"
    assert ident.workspace == "default"
    assert ident.speaker_peer == "open_webui"
    assert ident.observation_mode == "unified"


def test_resolve_falls_back_to_default_when_no_identity():
    ident = resolve(user_email=None, settings=_settings(honcho_default_peer="fallbackpeer"))
    assert ident.subject_peer == "fallbackpeer"


def test_resolve_unknown_mode_defaults_unified():
    ident = resolve(user_email="x@y.z", observation_mode="weird", settings=_settings())
    assert ident.observation_mode == "unified"


def test_resolve_directional_mode_passthrough():
    ident = resolve(user_email="x@y.z", observation_mode="directional", settings=_settings())
    assert ident.observation_mode == "directional"
