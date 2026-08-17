"""Invisible characters, and getting rid of them.

Text pasted out of Word, WhatsApp, a browser or anything else bidi-aware
routinely carries characters that print nothing: direction marks, zero-width
joiners, a stray byte-order mark. The user cannot see them and did not type
them, but they are in the string.

That matters here more than it would in most apps, because two of the app's
load-bearing comparisons are string equality against the user's own words:

* `guards.check_repeat_text` refuses wording a group has already been sent.
  An invisible character makes two identical posts compare as different, so
  the guard waves through the exact repeat it exists to stop -- and repetitive
  content is what gets accounts restricted.
* `automation.poster.distinctive_snippet` searches the group's feed for the
  post to confirm it appeared. Facebook strips these characters when it
  renders, so searching for a snippet that still contains one finds nothing
  and reports a successful post as failed.

The Tk UI stripped bidi marks on the way out of its editor because it had put
them there itself. The Qt UI needs no marks of its own and so had no such
step -- which is how pasted ones started reaching the database.

Kept deliberately narrow: only characters with no visible width. Anything that
occupies space, including the non-breaking space, is left alone, because
removing it would change what the reader sees.
"""

from __future__ import annotations

# Bidirectional formatting: marks, embeddings, overrides and isolates.
BIDI_MARKS = (
    "‎"  # LEFT-TO-RIGHT MARK
    "‏"  # RIGHT-TO-LEFT MARK
    "‪"  # LEFT-TO-RIGHT EMBEDDING
    "‫"  # RIGHT-TO-LEFT EMBEDDING
    "‬"  # POP DIRECTIONAL FORMATTING
    "‭"  # LEFT-TO-RIGHT OVERRIDE
    "‮"  # RIGHT-TO-LEFT OVERRIDE
    "⁦"  # LEFT-TO-RIGHT ISOLATE
    "⁧"  # RIGHT-TO-LEFT ISOLATE
    "⁨"  # FIRST STRONG ISOLATE
    "⁩"  # POP DIRECTIONAL ISOLATE
)

# Zero-width and formatting characters that are not about direction. These
# arrive from web pages and word processors just as often as the bidi ones.
ZERO_WIDTH = (
    "­"  # SOFT HYPHEN
    "​"  # ZERO WIDTH SPACE
    "‌"  # ZERO WIDTH NON-JOINER
    "‍"  # ZERO WIDTH JOINER
    "⁠"  # WORD JOINER
    "﻿"  # ZERO WIDTH NO-BREAK SPACE / BOM
)

INVISIBLE = BIDI_MARKS + ZERO_WIDTH

_TABLE = dict.fromkeys(map(ord, INVISIBLE))


def strip_invisible(text: str) -> str:
    """Remove every character that takes up no space and prints nothing.

    Safe on any input, including text that has none: it is a translate table,
    so the common case costs one pass and allocates nothing extra.
    """
    if not text:
        return text
    return text.translate(_TABLE)


def has_invisible(text: str) -> bool:
    """Whether stripping would change anything. For messages and tests."""
    return any(ch in _TABLE for ch in map(ord, text or ""))
