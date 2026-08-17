"""Which way a piece of text reads, and how to align it.

Tk already renders bidirectional text correctly here: it reorders the runs,
resolves neutral characters, and picks a base direction the way the Unicode
algorithm says to. That was checked against Hebrew, English and mixed samples
before any of this was written -- prefixing the text with RLM or LRM changed
nothing on screen, because the resolution was already right. So there is no
reordering to do and no bidi library to depend on.

What Tk does *not* do is align. Every widget is left-aligned, which left a
Hebrew post hard against the left edge with its punctuation trailing off into
the middle of the box. Alignment is the whole bug and the whole fix.

The rule below is the Unicode Bidirectional Algorithm's P2/P3: the first strong
directional character decides the direction, and text with none of them reads
left-to-right. That is the same rule as HTML's dir="auto", which is what
Facebook applies to a post -- so following it keeps the Compose preview honest
about what the group is actually going to see.

The decision is made per line, not once for the whole post: a post that opens
in Hebrew and closes with an English line needs each of them against its own
edge. One shared direction dragged the English to the right and was reported
as tangled and hard to read.
"""

from __future__ import annotations

from ..text import strip_invisible

import unicodedata

LTR = "ltr"
RTL = "rtl"

# An isolate hides its contents from the surrounding paragraph's direction
# decision, so a quoted Hebrew phrase cannot flip an English post.
_ISOLATE_INITIATORS = frozenset({"LRI", "RLI", "FSI"})

# The only categories that decide anything. Digits are deliberately absent:
# "50 שקלים" is Hebrew that happens to start with a number, and reads
# right-to-left.
_STRONG = {"L": LTR, "R": RTL, "AL": RTL}


def strong_direction(text: str) -> str | None:
    """The direction of the first strong character, or None if there is none.

    The None matters: a line of "054-1234567" has no opinion of its own, and
    guessing left-to-right for it is what left a phone number stranded on the
    wrong side of an otherwise Hebrew post.
    """
    isolate_depth = 0

    for character in text:
        category = unicodedata.bidirectional(character)

        if category in _ISOLATE_INITIATORS:
            isolate_depth += 1
            continue
        if category == "PDI":
            isolate_depth = max(0, isolate_depth - 1)
            continue
        if isolate_depth:
            continue

        direction = _STRONG.get(category)
        if direction is not None:
            return direction

    return None


def base_direction(text: str) -> str:
    """The direction `text` reads in, by first-strong-character (UBA P2/P3)."""
    return strong_direction(text) or LTR


def line_directions(text: str) -> list[str]:
    """One direction per line, which is how the editor aligns them.

    Per line rather than one direction for the whole box. Sharing a single
    direction meant an English paragraph under a Hebrew one was dragged to the
    right-hand edge, which is what made a bilingual post hard to read.

    A line with no strong character of its own -- a phone number, a row of
    dashes, a blank -- inherits from the line above rather than snapping back
    to left-to-right, so it stays with the block it belongs to.
    """
    document = base_direction(text)
    directions: list[str] = []
    inherited: str | None = None

    for line in text.split("\n"):
        direction = strong_direction(line) or inherited or document
        directions.append(direction)
        inherited = direction

    return directions


def is_rtl(text: str) -> bool:
    return base_direction(text) == RTL


# U+202D LEFT-TO-RIGHT OVERRIDE ... U+202C POP DIRECTIONAL FORMATTING.
# Windows reorders each run it draws, which would undo the reordering below and
# hand back the logical order. The override switches that off, so what we
# compute is what gets drawn. Verified on screen: without it the reordered text
# comes out identical to the original.
_LRO, _PDF = "‭", "‬"


# U+202B RIGHT-TO-LEFT EMBEDDING. Placed at the start of a right-to-left line
# in the *editor*, it makes Windows draw that line with a right-to-left base
# while still resolving English and numbers inside it properly -- which is the
# one thing that makes mixed text render correctly in a widget we cannot
# reorder. Verified pixel-identical to the reordered reference rendering.
RLE_MARK = "‫"

# Everything that steers direction without printing anything. None of these may
# ever reach the post: they are display scaffolding, stripped on the way out.
BIDI_CONTROLS = "‎‏‪‫‬‭‮⁦⁧⁨⁩"
_STRIP_CONTROLS = dict.fromkeys(map(ord, BIDI_CONTROLS))


def strip_controls(text: str) -> str:
    """Remove every invisible direction character.

    The editor holds a few of these to get Windows to draw Hebrew the right way
    round. They are not part of what the user wrote, and Facebook must never
    see them, so every read of the editor goes through here.

    Delegates to the shared list, which is wider than BIDI_CONTROLS: pasted
    text also carries zero-width joiners and byte-order marks, and those cause
    the same trouble downstream.
    """
    return strip_invisible(text)


def bidi_available() -> bool:
    """Whether the reordering library is installed. Optional, like Pillow."""
    try:
        import bidi.algorithm  # noqa: F401
    except ImportError:
        return False
    return True


def to_visual(line: str) -> str:
    """One display line, reordered into the order it should appear on screen.

    Tk lays characters out in logical order and leaves reordering to Windows,
    which only does it within a single run of one script. A line mixing Hebrew
    with English or digits therefore comes out mirrored. This does the
    reordering properly and then blocks Windows from doing it again.

    For display only, and only somewhere read-only: the returned string is not
    the text of the post, and must never be sent anywhere or fed back into an
    editor.

    Falls back to the original line when the library is missing, which looks
    exactly like it did before rather than failing.
    """
    if not line:
        return line

    try:
        from bidi.algorithm import get_display
    except ImportError:
        return line

    try:
        visual = get_display(line, base_dir="R" if is_rtl(line) else "L")
    except Exception:
        return line

    return f"{_LRO}{visual}{_PDF}"


def justify_for(direction: str) -> str:
    """Tk's -justify: how the lines of a block line up with each other."""
    return "right" if direction == RTL else "left"


def anchor_for(direction: str) -> str:
    """Tk's -anchor: which edge the block sits against."""
    return "e" if direction == RTL else "w"


def justify(text: str) -> str:
    return justify_for(base_direction(text))


def anchor(text: str) -> str:
    return anchor_for(base_direction(text))
