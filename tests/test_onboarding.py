"""The setup wizard's judgement, tested without Chrome or a window.

`onboarding.py` is pure on purpose -- the same split `guards.py` has against
`worker.py` -- so what the app decides to tell the user next is checkable here
in milliseconds rather than through a browser.
"""

from __future__ import annotations

import pytest

from fbposter import onboarding
from fbposter.automation import detect
from fbposter.onboarding import RowState, SetupStep
from fbposter.ui.connection import ConnectionResult, ConnectionState


def result(state: ConnectionState) -> ConnectionResult:
    return ConnectionResult(state, "detail")


class TestOneProblemAtATime:
    """A checklist that reports four problems at once is four times as
    intimidating and no more useful: they have to be fixed in order anyway."""

    def test_a_missing_chrome_outranks_everything(self):
        # Even a connection result claiming success cannot matter -- without
        # Chrome there is nothing for the rest of the app to talk to.
        assert (
            onboarding.plan(False, result(ConnectionState.CONNECTED))
            is SetupStep.CHROME_MISSING
        )

    def test_nothing_checked_yet_asks_about_chrome(self):
        assert onboarding.plan(True, None) is SetupStep.CHROME_DOWN

    @pytest.mark.parametrize(
        "state, step",
        [
            (ConnectionState.CONNECTED, SetupStep.READY),
            (ConnectionState.CHROME_DOWN, SetupStep.CHROME_DOWN),
            (ConnectionState.LOGGED_OUT, SetupStep.LOGGED_OUT),
            (ConnectionState.CHECKPOINT, SetupStep.CHECKPOINT),
            (ConnectionState.ERROR, SetupStep.ERROR),
        ],
    )
    def test_every_connection_state_maps(self, state, step):
        assert onboarding.plan(True, result(state)) is step

    def test_every_state_is_covered(self):
        """A new ConnectionState with no mapping would raise KeyError inside the
        window, which is a blank screen rather than a message."""
        for state in ConnectionState:
            assert onboarding.plan(True, result(state)) in SetupStep

    def test_a_check_in_flight_does_not_claim_success(self):
        """CHECKING is not an answer. Reporting READY on it would flash "you are
        all set" every time the user pressed the button."""
        assert onboarding.plan(True, result(ConnectionState.CHECKING)) is not SetupStep.READY


class TestTheProgressList:
    def test_it_walks_forward(self):
        marks = [state for _label, state in onboarding.checklist(SetupStep.LOGGED_OUT)]
        assert marks == [RowState.DONE, RowState.DONE, RowState.CURRENT]

    def test_ready_shows_everything_done(self):
        marks = [state for _label, state in onboarding.checklist(SetupStep.READY)]
        assert marks == [RowState.DONE] * len(onboarding.CHECKLIST)

    def test_nothing_done_yet(self):
        marks = [state for _label, state in onboarding.checklist(SetupStep.CHROME_MISSING)]
        assert marks == [RowState.CURRENT, RowState.TODO, RowState.TODO]

    def test_exactly_one_row_is_current_until_the_end(self):
        for step in SetupStep:
            marks = [state for _label, state in onboarding.checklist(step)]
            current = marks.count(RowState.CURRENT)
            assert current == (0 if step is SetupStep.READY else 1), step

    def test_every_step_has_a_position(self):
        for step in SetupStep:
            assert len(onboarding.checklist(step)) == len(onboarding.CHECKLIST)


class TestNothingTellsTheUserToOpenATerminal:
    """The app is shipped to somebody who has never seen one.

    Both of these strings reached the user through the connection pill: "Start
    it with 'main.py launch'" and "Run 'main.py setup' and sign in again". They
    are perfectly good developer instructions and completely unusable by the
    person this was built for, who has an .exe and no console at all.
    """

    FORBIDDEN = ("main.py", "python ", ".venv", "command", "terminal", "cmd.exe")

    def offenders(self, text: str) -> list[str]:
        lowered = text.lower()
        return [word for word in self.FORBIDDEN if word.lower() in lowered]

    def test_the_wizard_copy_is_clean(self):
        for step, guide in onboarding.GUIDANCE.items():
            for field in (guide.headline, guide.detail, guide.action or ""):
                assert self.offenders(field) == [], f"{step}: {field!r}"

    def test_the_halt_messages_are_clean(self):
        for verdict, message in detect.HALT_MESSAGES.items():
            assert self.offenders(message) == [], f"{verdict}: {message!r}"

    def test_the_connection_messages_are_clean(self, monkeypatch):
        from fbposter import chrome
        from fbposter.ui import connection

        monkeypatch.setattr(chrome, "probe", lambda *a, **k: None)
        assert self.offenders(connection.check_connection().detail) == []

    def test_every_step_says_something(self):
        """A step with empty copy is a blank card and no way forward."""
        for step in SetupStep:
            guide = onboarding.guidance(step)
            assert guide.headline.strip()
            assert guide.detail.strip()

    def test_only_the_finished_step_reports_done(self):
        for step in SetupStep:
            assert onboarding.guidance(step).done is (step is SetupStep.READY)
            assert onboarding.needs_setup(step) is (step is not SetupStep.READY)

    def test_the_steps_the_app_cannot_fix_offer_no_button(self):
        """Installing Chrome and clearing a Facebook checkpoint are the user's
        to do. A button that cannot do anything is worse than none."""
        assert onboarding.guidance(SetupStep.CHROME_MISSING).action is None
        assert onboarding.guidance(SetupStep.READY).action is None
