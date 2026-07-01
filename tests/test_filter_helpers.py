"""Unit tests for the Filter's pure helpers (loaded from the single-file Function)."""

import importlib.util
import pathlib

_FILTER = pathlib.Path(__file__).resolve().parents[1] / "functions" / "honcho_memory_filter.py"
_spec = importlib.util.spec_from_file_location("honcho_memory_filter", _FILTER)
fm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fm)


def test_text_of_string_and_parts():
    assert fm._text_of("hi") == "hi"
    assert fm._text_of([{"type": "text", "text": "a"}, {"type": "image_url"}]) == "a"
    assert fm._text_of(None) == ""


def test_assistant_text_reconstructs_from_output():
    assert (
        fm._assistant_text(
            {"content": "", "output": [{"content": [{"type": "text", "text": "x"}]}]}
        )
        == "x"
    )
    assert fm._assistant_text({"content": "direct", "output": []}) == "direct"
    assert fm._assistant_text({"content": ""}) == ""


def test_prepend_system_new_and_merge():
    out = fm.Filter._prepend_system("SYS", [{"role": "user", "content": "u"}])
    assert out[0] == {"role": "system", "content": "SYS"}
    out2 = fm.Filter._prepend_system(
        "SYS", [{"role": "system", "content": "orig"}, {"role": "user", "content": "u"}]
    )
    assert out2[0]["content"].startswith("SYS") and "orig" in out2[0]["content"] and len(out2) == 2


def test_embedded_system_prompt_is_synced():
    # build_filter.py must have replaced the placeholder with the real policy.
    assert "PLACEHOLDER" not in fm.SYSTEM_PROMPT
    assert "operating instructions" in fm.SYSTEM_PROMPT
    assert "honcho_create_conclusion" in fm.SYSTEM_PROMPT
