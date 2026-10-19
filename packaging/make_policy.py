"""Turn policy.txt into policy.rtf, the page the installer asks the client to accept.

Inno Setup shows a licence as plain text or as RTF. Plain text cannot say which
way a paragraph runs, so Hebrew comes out aligned left with its full stops on
the wrong side; RTF can. Run this after editing policy.txt:

    .venv\\Scripts\\python.exe packaging\\make_policy.py

`tests/test_policy.py` fails while policy.rtf is out of date, and `build.ps1`
runs the suite before it builds anything.

policy.txt: blank lines separate paragraphs, "# " starts a title, "## " a
heading, and "---" alone on a line puts space between the two languages.
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "policy.txt"
TARGET = HERE / "policy.rtf"

# RTF sizes are in half points, spacing in twentieths of a point.
BODY, HEADING, TITLE = 18, 20, 26
SPACE_AFTER = 120


def right_to_left(text: str) -> bool:
    """The first letter decides, as Unicode's own rule for a paragraph does.

    So "1.1. מסמך" is Hebrew: digits and full stops are not letters.
    """
    for char in text:
        kind = unicodedata.bidirectional(char)
        if kind in ("R", "AL"):
            return True
        if kind == "L":
            return False
    return False


def escape(text: str) -> str:
    """RTF is ASCII: every other character is written as its number."""
    out = []
    for char in text:
        code = ord(char)
        if char in "\\{}":
            out.append("\\" + char)
        elif code < 128:
            out.append(char)
        elif code > 0xFFFF:
            raise ValueError(f"{char!r} does not fit in RTF's \\u")
        else:
            # \u takes a signed 16-bit number, and "?" is what a reader too
            # old for \u shows instead.
            out.append(f"\\u{code if code < 0x8000 else code - 0x10000}?")
    return "".join(out)


def paragraphs(source: str) -> list[str]:
    found, block = [], []
    for line in source.splitlines() + [""]:
        if line.strip():
            block.append(line.strip())
        elif block:
            found.append(" ".join(block))
            block = []
    return found


def render(source: str) -> str:
    lines = [
        r"{\rtf1\ansi\ansicpg1252\deff0",
        r"{\fonttbl{\f0\fswiss\fcharset0 Segoe UI;}{\f1\fswiss\fcharset177 Segoe UI;}}",
        r"\uc1",
    ]
    for text in paragraphs(source):
        if text == "---":
            lines.append(r"\pard\sb240\sa240\par")
            continue
        size, before, bold = BODY, 0, False
        if text.startswith("# "):
            text, size, before, bold = text[2:], TITLE, 120, True
        elif text.startswith("## "):
            text, size, before, bold = text[3:], HEADING, 120, True
        if right_to_left(text):
            layout, font = r"\rtlpar\qr", r"\f1\rtlch"
        else:
            layout, font = r"\ltrpar\ql", r"\f0\ltrch"
        on, off = (r"\b", r"\b0") if bold else ("", "")
        lines.append(
            rf"\pard{layout}\sb{before}\sa{SPACE_AFTER}{font}\fs{size}{on} "
            rf"{escape(text)}{off}\par"
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    TARGET.write_text(render(SOURCE.read_text(encoding="utf-8")),
                      encoding="ascii", newline="\n")
    print(f"Wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
