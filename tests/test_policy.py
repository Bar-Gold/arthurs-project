"""The account blocking policy the installer asks the client to accept.

The user's request (2026-10-19): a client whose Facebook account is blocked
should have no claim and no refund. The installer shows the policy and will
not go on until it is accepted. It is written once, in `packaging/policy.txt`,
and `packaging/make_policy.py` turns it into the RTF the installer shows,
because plain text cannot run Hebrew right to left.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"

_spec = importlib.util.spec_from_file_location("make_policy", PACKAGING / "make_policy.py")
make_policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_policy)


def installer() -> str:
    return (PACKAGING / "installer.iss").read_text(encoding="utf-8")


def source() -> str:
    return make_policy.SOURCE.read_text(encoding="utf-8")


def halves() -> tuple[str, str]:
    hebrew, english = source().split("\n---\n")
    return hebrew, english


class TestTheInstallerShowsIt:
    def test_it_is_the_licence_page(self):
        """The licence page is the one Inno Setup will not pass without "I
        accept"."""
        assert re.search(r"^LicenseFile=policy\.rtf$", installer(), re.M)

    def test_the_page_is_up_to_date(self):
        """Editing policy.txt changes nothing until make_policy.py is run, and
        the client would accept the old words."""
        built = make_policy.render(source())
        shipped = make_policy.TARGET.read_text(encoding="ascii").replace("\r\n", "\n")
        assert shipped == built, r"run .venv\Scripts\python.exe packaging\make_policy.py"

    def test_the_button_it_names_is_the_real_one(self):
        """Both languages quote the button that accepts it, word for word."""
        found = re.search(r"^LicenseAccepted=(.+)$", installer(), re.M)
        label = found.group(1).replace("&", "")
        for half in halves():
            assert f'"{label}"' in half


class TestWhatItSays:
    def test_both_languages_name_the_seller(self):
        hebrew, english = halves()
        assert "בר גולדשטיין" in hebrew
        assert "Bar Goldstein" in english

    def test_both_languages_cover_the_block_the_refund_and_the_claim(self):
        hebrew, english = halves()
        for word in ("Meta", "חסימה", "החזר", "מוותר", "דמי ההקמה", "דמי מנוי"):
            assert word in hebrew, word
        for word in ("Meta", "block", "refund", "waives", "setup fee", "subscription"):
            assert word in english, word

    def test_the_hebrew_wins(self):
        hebrew, english = halves()
        assert "הנוסח העברי יגבר" in hebrew
        assert "the Hebrew version prevails" in english

    def test_both_languages_have_the_same_clauses(self):
        """A clause in one language and not the other is a gap someone can
        argue through."""
        def clauses(text):
            return re.findall(r"^(\d+\.\d+)\. ", text, re.M)

        hebrew, english = halves()
        # 1.3 is the Hebrew note on masculine wording; English needs none.
        assert [c for c in clauses(hebrew) if c != "1.3"] == clauses(english)


class TestTheLayout:
    def test_hebrew_runs_right_to_left_and_english_left_to_right(self):
        rtf = make_policy.render("# כותרת\n\nשלום world\n\nHello עולם\n")
        rows = [line for line in rtf.splitlines() if line.startswith(r"\pard")]
        assert r"\rtlpar\qr" in rows[0]
        assert r"\rtlpar\qr" in rows[1]
        assert r"\ltrpar\ql" in rows[2]

    def test_a_numbered_hebrew_clause_is_hebrew(self):
        """Digits and full stops are not letters, so they do not decide."""
        assert make_policy.right_to_left("1.1. מסמך זה")
        assert not make_policy.right_to_left("1.1. This policy")

    def test_every_paragraph_of_the_policy_has_a_direction(self):
        rows = [line for line in make_policy.render(source()).splitlines()
                if line.startswith(r"\pard") and "\\par" in line and "\\sb240" not in line]
        assert rows
        assert all(r"\rtlpar" in row or r"\ltrpar" in row for row in rows)

    def test_it_is_plain_ascii(self):
        assert make_policy.render(source()).isascii()

    def test_rtf_characters_are_escaped(self):
        assert make_policy.escape("a{b}c\\") == r"a\{b\}c\\"
        assert make_policy.escape("ש") == r"\u1513?"
