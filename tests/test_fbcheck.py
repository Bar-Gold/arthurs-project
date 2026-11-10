"""`main.py check-facebook`: has Facebook moved anything the app uses?

The user's request, 2026-11-10: "check if facebook moved something" checks
everything in the app that is connected to a Facebook page. What keeps that
"everything" true is `TestNothingIsLeftOut`: a new Facebook string, page script
or poster step fails here until a check covers it or `NOT_CHECKABLE` says why
none can. Nothing here opens a browser.
"""

from __future__ import annotations

import inspect
import types

import pytest

import main
from fbposter import fbcheck, strings
from fbposter.automation import detect, groupfinder, groupinfo, poster
from fbposter.automation.poster import GroupPoster
from fbposter.errors import AutomationHalted
from fbposter.fbcheck import FAILED, OK, SKIPPED, Check, Options, run_checks


def facebook_strings() -> set[str]:
    """Every name strings.py defines: its URLs, words and link builders."""
    names = set()
    for name, value in vars(strings).items():
        if name.startswith("_"):
            continue
        if isinstance(value, types.FunctionType) and value.__module__ == strings.__name__:
            names.add(f"strings.{name}")
        elif name.isupper():
            names.add(f"strings.{name}")
    return names


def page_scripts() -> set[str]:
    """Every script the app runs inside a Facebook page."""
    names = set()
    for module in (groupfinder, groupinfo, poster):
        short = module.__name__.rsplit(".", 1)[-1]
        for name, value in vars(module).items():
            if name.isupper() and isinstance(value, str) and "=>" in value:
                names.add(f"{short}.{name}")
    return names


def poster_steps() -> set[str]:
    """Every step of GroupPoster the worker can call."""
    return {
        f"GroupPoster.{name}"
        for name, value in vars(GroupPoster).items()
        if not name.startswith("_") and callable(value)
    }


def resolves(name: str) -> bool:
    owner, attr = name.split(".", 1)
    modules = {"strings": strings, "groupfinder": groupfinder, "groupinfo": groupinfo,
               "GroupPoster": GroupPoster}
    return hasattr(modules[owner], attr)


class TestNothingIsLeftOut:
    def everything_known(self) -> set[str]:
        return fbcheck.all_covered() | set(fbcheck.NOT_CHECKABLE)

    @pytest.mark.parametrize("found", ["strings", "scripts", "steps"])
    def test_every_facebook_touch_is_checked_or_explained(self, found):
        wanted = {"strings": facebook_strings, "scripts": page_scripts,
                  "steps": poster_steps}[found]()
        missing = sorted(wanted - self.everything_known())
        assert not missing, (
            "Not checked by `main.py check-facebook`, and not in NOT_CHECKABLE: "
            f"{missing}. Add it to a check's `covers`, or say why it cannot be."
        )

    def test_every_name_listed_still_exists(self):
        stale = sorted(n for n in self.everything_known() if not resolves(n))
        assert not stale, f"Listed but gone from the app: {stale}"

    def test_a_name_is_checked_or_unreachable_never_both(self):
        assert not fbcheck.all_covered() & set(fbcheck.NOT_CHECKABLE)

    def test_every_check_has_a_unique_key_and_known_needs(self):
        keys = [check.key for check in fbcheck.CHECKS]
        assert len(keys) == len(set(keys))
        for index, check in enumerate(fbcheck.CHECKS):
            assert set(check.needs) <= set(keys[:index]), f"{check.key} needs a later step"


class TestItNeverPostsUnlessAsked:
    def test_only_the_real_post_step_can_click_post(self):
        for check in fbcheck.CHECKS:
            source = inspect.getsource(check.run)
            touches = ".post(" in source or ".publish(" in source
            assert touches == check.real_post, check.key

    def test_the_real_post_is_the_last_step(self):
        assert [c.real_post for c in fbcheck.CHECKS][-1] is True
        assert sum(c.real_post for c in fbcheck.CHECKS) == 1

    def test_the_draft_is_never_posted(self):
        """Every draft step goes through a poster made with dry_run."""
        run = fbcheck.Run(context=None, page=object(), options=Options())
        assert fbcheck._poster(run).dry_run is True

    def test_post_is_off_by_default(self):
        args = main.build_parser().parse_args(["check-facebook"])
        assert args.post is False and args.func is main.cmd_check_facebook
        assert Options().post is False


def step(key, outcome="ok", needs=(), real_post=False, log=None):
    def run(r):
        if log is not None:
            log.append(key)
        if isinstance(outcome, Exception):
            raise outcome
        return f"{key} fine"
    return Check(key, key.title(), run, needs=needs, real_post=real_post)


def statuses(results):
    return [(r.key, r.status) for r in results]


class TestRunningTheSteps:
    def test_one_failing_does_not_stop_the_rest(self):
        checks = (step("a", fbcheck.CheckFailed("moved")), step("b"))
        results = run_checks(None, None, Options(), checks)
        assert statuses(results) == [("a", FAILED), ("b", OK)]
        assert results[0].detail == "moved"

    def test_a_step_whose_needs_failed_is_skipped(self):
        log = []
        checks = (step("a", RuntimeError("x")), step("b", needs=("a",), log=log), step("c"))
        results = run_checks(None, None, Options(), checks)
        assert statuses(results) == [("a", FAILED), ("b", SKIPPED), ("c", OK)]
        assert log == []

    def test_a_checkpoint_stops_everything(self):
        halt = AutomationHalted(detect.PageVerdict.CHECKPOINT, "security check")
        log = []
        checks = (step("a", halt), step("b", log=log))
        results = run_checks(None, None, Options(), checks)
        assert statuses(results) == [("a", FAILED), ("b", SKIPPED)]
        assert "security check" in results[1].detail
        assert log == [], "went on past a checkpoint"

    def test_the_real_post_runs_only_when_asked(self):
        log = []
        checks = (step("p", real_post=True, log=log),)
        assert statuses(run_checks(None, None, Options(), checks)) == [("p", SKIPPED)]
        assert log == []
        assert statuses(run_checks(None, None, Options(post=True), checks)) == [("p", OK)]

    def test_each_result_is_reported_as_it_comes(self):
        seen = []
        run_checks(None, None, Options(), (step("a"), step("b")), report=seen.append)
        assert [r.key for r in seen] == ["a", "b"]

    def test_a_draft_left_open_by_a_failure_is_thrown_away(self):
        class Poster:
            discarded = 0

            def discard(self):
                Poster.discarded += 1

        def opens_a_draft(run):
            run.state["poster"] = Poster()
            run.state["dialog"] = object()
            raise fbcheck.CheckFailed("Add groups moved")

        checks = (Check("typing", "Typing", opens_a_draft),)
        run_checks(None, None, Options(), checks)
        assert Poster.discarded == 1


class TestWhatItUses:
    def test_the_test_video_is_there(self):
        assert fbcheck.TEST_VIDEO.is_file()
        assert fbcheck.TEST_VIDEO.stat().st_size < 2_000_000

    def test_the_picture_is_a_png(self):
        data = fbcheck._picture().read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n") and b"IHDR" in data and data.endswith(
            b"IEND\xaeB`\x82"
        )

    def test_the_test_groups_are_the_users_choices(self):
        assert fbcheck.TEST_GROUP_URL == strings.group_url("1697911281266837")
        assert (fbcheck.TEST_GROUP_NAME, fbcheck.SECOND_GROUP_NAME) == ("TRY", "bar-test")
