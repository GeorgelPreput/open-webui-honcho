#!/usr/bin/env python3
"""Sync the SYSTEM_PROMPT block of the Open WebUI Filter from prompts/system_prompt.md.

The Filter is uploaded to Open WebUI as a single file, so the memory policy must be embedded.
Rather than template the whole Filter, we keep `functions/honcho_memory_filter.py` hand-written
and regenerate only the block between the two sentinels:

    # >>> SYSTEM_PROMPT (generated from prompts/system_prompt.md) >>>
    SYSTEM_PROMPT = "..."
    # <<< SYSTEM_PROMPT <<<

Run from the repo root:  python scripts/build_filter.py [--check]
`--check` exits non-zero if the embedded prompt is stale (useful in CI / pre-commit).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT = ROOT / "prompts" / "system_prompt.md"
FILTER = ROOT / "functions" / "honcho_memory_filter.py"
START = "# >>> SYSTEM_PROMPT (generated from prompts/system_prompt.md) >>>"
END = "# <<< SYSTEM_PROMPT <<<"


def render_block(prompt_text: str) -> str:
    # Triple-quoted constant; escape backslashes and any triple-quote in the source.
    safe = prompt_text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return f'{START}\nSYSTEM_PROMPT = """\\\n{safe}"""\n{END}'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if the embedded prompt is stale")
    args = ap.parse_args()

    prompt_text = PROMPT.read_text(encoding="utf-8")
    source = FILTER.read_text(encoding="utf-8")

    if START not in source or END not in source:
        print(f"error: sentinels not found in {FILTER}", file=sys.stderr)
        return 2

    pre, rest = source.split(START, 1)
    _, post = rest.split(END, 1)
    new_source = pre + render_block(prompt_text) + post

    if args.check:
        if new_source != source:
            print(
                "SYSTEM_PROMPT block is stale; run: python scripts/build_filter.py", file=sys.stderr
            )
            return 1
        print("ok: embedded system prompt is up to date")
        return 0

    if new_source != source:
        FILTER.write_text(new_source, encoding="utf-8")
        print(f"updated {FILTER.relative_to(ROOT)} from {PROMPT.relative_to(ROOT)}")
    else:
        print("no change")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
