"""Unit tests for recall formatting + session naming (no network)."""

from open_webui_honcho.honcho_service import format_recall_block, session_name
from open_webui_honcho.identity import Identity


def test_format_recall_block_shape():
    rep = (
        "## Explicit Observations\n\n"
        "[2026-01-02 11:47:35] alice prefers dark mode in her editor.\n"
        "[2026-01-01 23:11:10] alice deploys with Docker Compose.\n"
        "# a heading line\n"
        "- a bulleted fact"
    )
    card = ["IDENTITY: Name: alice", "ATTRIBUTE: Editor: vim"]
    block = format_recall_block("alice", rep, card)
    assert block.startswith("[Honcho Memory for alice]:")
    assert "Relevant conclusions:" in block
    assert "prefers dark mode" in block
    assert "a bulleted fact" in block  # leading "- " stripped
    assert "[2026" not in block  # timestamp tag stripped
    assert "# a heading" not in block  # heading lines skipped
    assert "Profile: IDENTITY: Name: alice; ATTRIBUTE: Editor: vim" in block


def test_format_recall_block_empty():
    assert format_recall_block("p", None, None) == ""
    assert format_recall_block("p", "", []) == ""


def test_session_name():
    ident = Identity(
        workspace="default",
        subject_peer="alice",
        speaker_peer="open_webui",
        observation_mode="unified",
    )
    assert session_name(ident, "abc-123") == "owui-alice-abc-123"
    assert session_name(ident, None) == "owui-alice-default"
    assert session_name(ident, "a/b") == "owui-alice-a-b"
