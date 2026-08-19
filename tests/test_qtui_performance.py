"""The redraw guards, and the one blocking call that was on the UI thread.

Every view rebuilt all of its widgets from scratch on every visit. Opening the
Queue with 25 batches cost 110ms of widget construction to draw exactly what
was already on screen, and the worker refreshed it again on every event -- so
during a batch, which is when someone is actually watching it, it rebuilt
several times a minute. These tests pin that an unchanged screen is not
rebuilt, and that a changed one still is.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "fbposter"


def widgets_in(layout) -> list:
    return [
        layout.itemAt(index).widget()
        for index in range(layout.count())
        if layout.itemAt(index).widget() is not None
    ]


@pytest.fixture
def stocked(qt_app):
    """A window with groups and a batch, so the lists have something in them."""
    groups = qt_app.group_repo
    made = []
    for index in range(4):
        group = groups.add_from_url(f"https://www.facebook.com/groups/perf{index}")
        groups.set_name(group.id, f"Perf Group {index}")
        made.append(group)
    body = "Selling a road bike, 54cm frame."
    qt_app.task_repo.create(body, [(made[0].id, body), (made[1].id, body + "!")])
    qt_app.selected_groups = {made[0].id, made[1].id}
    return qt_app, made


class TestTheQueueIsNotRebuiltForNothing:
    def test_an_unchanged_queue_keeps_its_widgets(self, stocked):
        app, _ = stocked
        view = app.views["queue"]
        view.refresh()
        before = widgets_in(view.rows)
        assert before, "nothing was drawn to begin with"

        view.refresh()
        assert widgets_in(view.rows) == before, "rebuilt an identical queue"

    def test_a_changed_batch_does_rebuild(self, stocked):
        app, _ = stocked
        view = app.views["queue"]
        view.refresh()
        before = widgets_in(view.rows)

        task = app.task_repo.list_recent()[0]
        app.task_repo.cancel(task.id)
        view.refresh()
        assert widgets_in(view.rows) != before, "a cancelled batch was not redrawn"

    def test_a_changed_target_does_rebuild(self, stocked):
        """The card shows per-group state, so a target moving has to redraw."""
        from fbposter.db.models import TARGET_DONE

        app, _ = stocked
        view = app.views["queue"]
        view.refresh()
        before = widgets_in(view.rows)

        task = app.task_repo.list_recent()[0]
        target = app.task_repo.targets_for(task.id)[0]
        app.task_repo.mark_target(target.id, TARGET_DONE, posted=True)
        view.refresh()
        assert widgets_in(view.rows) != before

    def test_switching_scope_rebuilds(self, stocked):
        """Recent and All hold different batches, so the list is redrawn.

        This used to assert that the widgets were new objects, which held only
        because every refresh rebuilt everything. A scope switch that happens
        to show the same batches in the same order now reuses their cards --
        correctly, since a card renders one batch and renders it the same way
        in either scope. So this ages a finished batch out of Recent to make
        the two scopes genuinely differ, and checks what the user would see.
        """
        from datetime import timedelta

        from fbposter.db.models import TASK_DONE, to_iso, utcnow

        app, made = stocked
        old = app.task_repo.create("An older batch.", [(made[2].id, "An older batch.")])
        app.db.write(
            "UPDATE tasks SET state = ?, finished_at = ? WHERE id = ?",
            (TASK_DONE, to_iso(utcnow() - timedelta(hours=48)), old.id),
        )

        view = app.views["queue"]
        view.refresh()
        recent = widgets_in(view.rows)
        view.set_scope(True)
        every = widgets_in(view.rows)

        assert len(every) > len(recent), "All showed no more than Recent"
        assert "hidden" not in view.hidden_label.text(), "still claims to be hiding one"

    def test_force_rebuilds_anyway(self, stocked):
        app, _ = stocked
        view = app.views["queue"]
        view.refresh()
        before = widgets_in(view.rows)
        view.refresh(force=True)
        assert widgets_in(view.rows) != before


class TestTheGroupListIsNotRebuiltForNothing:
    def test_an_unchanged_list_keeps_its_widgets(self, stocked):
        app, _ = stocked
        view = app.views["groups"]
        view.refresh()
        before = widgets_in(view.rows)
        assert before

        view.refresh()
        assert widgets_in(view.rows) == before

    def test_a_renamed_group_rebuilds(self, stocked):
        app, made = stocked
        view = app.views["groups"]
        view.refresh()
        before = widgets_in(view.rows)

        app.group_repo.set_name(made[0].id, "Renamed Entirely")
        view.refresh()
        assert widgets_in(view.rows) != before

    def test_a_changed_cooldown_rebuilds(self, stocked):
        app, made = stocked
        view = app.views["groups"]
        view.refresh()
        before = widgets_in(view.rows)

        app.group_repo.set_cooldown(made[0].id, 12)
        view.refresh()
        assert widgets_in(view.rows) != before

    def test_a_new_group_rebuilds(self, stocked):
        app, _ = stocked
        view = app.views["groups"]
        view.refresh()
        before = widgets_in(view.rows)

        app.group_repo.add_from_url("https://www.facebook.com/groups/brandnew")
        view.refresh()
        assert widgets_in(view.rows) != before

    def test_the_checkboxes_still_resolve(self, stocked):
        """A skipped rebuild must not leave the map pointing at dead widgets."""
        app, made = stocked
        view = app.views["groups"]
        view.refresh()
        view.refresh()
        assert set(view._checkboxes) == {group.id for group in made}
        for box in view._checkboxes.values():
            box.text()  # a deleted C++ object raises here


class TestTheWordingTabsAreNotRebuiltForNothing:
    def test_unchanged_tabs_are_kept(self, stocked):
        app, _ = stocked
        view = app.views["compose"]
        view.refresh_tabs()
        before = widgets_in(view.tab_bar)
        assert before

        view.refresh_tabs()
        assert widgets_in(view.tab_bar) == before

    def test_a_new_selection_rebuilds(self, stocked):
        app, made = stocked
        view = app.views["compose"]
        view.refresh_tabs()
        before = widgets_in(view.tab_bar)

        app.set_group_selected(made[2].id, True)
        view.refresh_tabs()
        after = widgets_in(view.tab_bar)
        assert after != before
        assert len(after) == len(before) + 1

    def test_a_renamed_group_relabels_its_tab(self, stocked):
        app, made = stocked
        view = app.views["compose"]
        view.refresh_tabs()
        before = [button.text() for button in widgets_in(view.tab_bar)]

        app.group_repo.set_name(made[0].id, "Totally New Name")
        view.refresh_tabs()
        after = [button.text() for button in widgets_in(view.tab_bar)]
        assert after != before, "the strip went stale after a rename"


class TestTheActiveWordingTabStaysMarked:
    """The filled tab is the only thing on screen saying whose words are in the
    editor -- and typing into the base text clears every per-group rewrite, so
    a strip pointing at the wrong tab is how someone loses wording they had
    already written.

    None of these three change a single tab *label*, which is what the rebuild
    guard is keyed on, so none of them can be fixed by that key: the fill has
    to be restated outside it.
    """

    def marks(self, view) -> list[bool]:
        return [button.isChecked() for button in widgets_in(view.tab_bar)]

    def expected(self, view) -> list[bool]:
        return [target == view._editing for target, _label in view._tab_plan()]

    def test_switching_tabs_moves_the_highlight(self, stocked):
        """Switching tabs changes `_editing` and nothing else, so the guard
        skipped the rebuild and the highlight stayed on the tab just left --
        the editor holding one group's words under another group's name."""
        app, made = stocked
        view = app.views["compose"]
        view.refresh_tabs()
        assert self.marks(view) == self.expected(view)

        view.select_tab(made[0].id)
        assert view._editing == made[0].id
        assert self.marks(view) == self.expected(view), "the highlight went stale"

        view.select_tab(None)
        assert self.marks(view) == self.expected(view)

    def test_clicking_a_second_tab_does_not_leave_two_lit(self, stocked):
        """These buttons are checkable and in no exclusive group, so Qt checks
        the clicked one before select_tab is ever reached. With the rebuild
        skipped, the tab left behind stayed lit as well."""
        app, _ = stocked
        view = app.views["compose"]
        view.refresh_tabs()
        widgets_in(view.tab_bar)[1].click()  # the first group's tab
        assert self.marks(view) == self.expected(view)
        assert self.marks(view).count(True) == 1, "two tabs were lit at once"

    def test_clicking_the_tab_already_open_keeps_it_lit(self, stocked):
        """Clicking the tab you are already on is a normal thing to do, and Qt
        toggles a checked button *off*. Nothing about the strip's contents
        changed, so nothing put it back: no tab was lit at all."""
        app, made = stocked
        view = app.views["compose"]
        view.refresh_tabs()
        view.select_tab(made[0].id)

        widgets_in(view.tab_bar)[1].click()  # the same tab again
        assert view._editing == made[0].id
        assert self.marks(view).count(True) == 1, "the strip lost its highlight"
        assert self.marks(view) == self.expected(view)


class TestPublishRecipientsAreNotRebuiltForNothing:
    def test_unchanged_recipients_are_kept(self, stocked):
        app, _ = stocked
        view = app.views["publish"]
        view.refresh_recipients()
        before = widgets_in(view.recipient_box)
        assert before

        view.refresh_recipients()
        assert widgets_in(view.recipient_box) == before

    def test_a_changed_selection_rebuilds(self, stocked):
        app, made = stocked
        view = app.views["publish"]
        view.refresh_recipients()
        before = widgets_in(view.recipient_box)

        app.set_group_selected(made[3].id, True)
        view.refresh_recipients()
        assert widgets_in(view.recipient_box) != before

    def test_the_snippet_still_updates_when_the_rows_do_not(self, stocked):
        """The body can change without the recipient list changing at all, so
        the snippet is deliberately outside the guard."""
        app, _ = stocked
        view = app.views["publish"]
        compose = app.views["compose"]
        compose.editor.setPlainText("first wording entirely")
        compose.capture()
        view.refresh_recipients()
        first = view.preview_note.text()

        compose.editor.setPlainText("second wording entirely")
        compose.capture()
        view.refresh_recipients()
        assert view.preview_note.text() != first, "the snippet went stale"


class TestNothingSlowRunsOnTheDrawingThread:
    def test_the_debug_port_is_probed_in_the_background(self):
        """chrome.probe() is a synchronous HTTP call to the debug port: 14ms
        when Chrome answers, and up to PROBE_TIMEOUT_S of a frozen window when
        something holds the port without replying. The Groups screen ran it on
        every show."""
        from fbposter.qtui.views import groups

        source = inspect.getsource(groups.GroupsView._fetch_missing_names)
        head, marker, tail = source.partition("def work()")
        assert marker, "the background worker went away"
        assert "chrome.probe" not in head, "the probe still runs before the thread"
        assert "chrome.probe" in tail, "the probe guard was dropped entirely"


class TestOnlyTheChangedBatchIsRedrawn:
    """A worker event changes one target. The screen used to rebuild every
    card for it -- 127ms on the thread drawing the window, on every event,
    which is exactly when someone is watching the Queue.

    The snapshot is now kept per card, so a refresh replaces the batches whose
    own row moved and leaves the rest of the widgets alone.
    """

    @pytest.fixture
    def batches(self, qt_app):
        """Several batches, so "only one was redrawn" can be told apart."""
        groups = []
        for index in range(3):
            group = qt_app.group_repo.add_from_url(
                f"https://www.facebook.com/groups/many{index}"
            )
            qt_app.group_repo.set_name(group.id, f"Many Group {index}")
            groups.append(group)
        body = "Selling a road bike, 54cm frame."
        tasks = [
            qt_app.task_repo.create(body, [(group.id, body) for group in groups])
            for _ in range(5)
        ]
        view = qt_app.views["queue"]
        view.refresh(force=True)
        return qt_app, view, tasks

    def position_of(self, app, task_id: int) -> int:
        from fbposter.db.models import utcnow

        shown = app.task_repo.list_for_queue(utcnow(), view_hours(app))
        return [task.id for task in shown].index(task_id)

    def test_one_target_changing_replaces_exactly_one_card(self, batches):
        from fbposter.db.models import TARGET_DONE

        app, view, tasks = batches
        before = widgets_in(view.rows)

        changed = tasks[2]
        target = app.task_repo.targets_for(changed.id)[0]
        app.task_repo.mark_target(target.id, TARGET_DONE, posted=True)
        view.refresh()

        after = widgets_in(view.rows)
        assert len(after) == len(before), "the list changed length"
        replaced = [
            index for index, (a, b) in enumerate(zip(before, after)) if a is not b
        ]
        assert len(replaced) == 1, f"redrew {len(replaced)} cards for one target"
        assert replaced[0] == self.position_of(app, changed.id)

    def test_the_replaced_card_shows_the_new_state(self, batches):
        """Reuse is only correct if the one card that did change is right."""
        from PySide6.QtWidgets import QLabel

        from fbposter.db.models import TARGET_DONE

        app, view, tasks = batches
        changed = tasks[2]
        target = app.task_repo.targets_for(changed.id)[0]
        app.task_repo.mark_target(target.id, TARGET_DONE, posted=True)
        view.refresh()

        card = widgets_in(view.rows)[self.position_of(app, changed.id)]
        shown = [label.text() for label in card.findChildren(QLabel)]
        assert any("Done" in text for text in shown), shown
        assert any("Many Group" in text for text in shown), "lost the group names"

    def test_an_untouched_card_still_shows_its_groups(self, batches):
        """A reused widget is the old one, so it has to still be complete."""
        from PySide6.QtWidgets import QLabel

        from fbposter.db.models import TARGET_DONE

        app, view, tasks = batches
        target = app.task_repo.targets_for(tasks[2].id)[0]
        app.task_repo.mark_target(target.id, TARGET_DONE, posted=True)
        view.refresh()

        untouched = widgets_in(view.rows)[self.position_of(app, tasks[0].id)]
        shown = [label.text() for label in untouched.findChildren(QLabel)]
        assert sum("Many Group" in text for text in shown) == 3, shown

    def test_a_new_batch_rebuilds_the_list(self, batches):
        """The reuse path is only taken when the same batches are in the same
        order. A new one at the top shifts every card down."""
        app, view, _ = batches
        before = widgets_in(view.rows)

        group = app.group_repo.list()[0]
        app.task_repo.create("A brand new batch.", [(group.id, "A brand new batch.")])
        view.refresh()

        after = widgets_in(view.rows)
        assert len(after) == len(before) + 1
        assert all(widget not in before for widget in after), "reused a shifted card"


def view_hours(app) -> int:
    return app.views["queue"].retention_hours()


class TestPlaywrightIsNotImportedAtLaunch:
    """`playwright.sync_api` is the most expensive import in the app at ~830ms.

    Everything that reaches it -- the worker thread, the background group-name
    lookup -- is already off the thread drawing the window, so it is imported
    at the point of use. At module scope it was paid at startup instead, by the
    UI, before the window appeared.
    """

    def test_opening_the_app_does_not_import_playwright(self):
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import fbposter.qtui.app; "
                "print('playwright.sync_api' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False", (
            "importing the window pulled Playwright in with it, which puts "
            "~830ms back on startup"
        )

    def test_nothing_imports_playwright_at_module_scope(self):
        """A grep, because the cost comes back silently wherever it is written
        and only a stopwatch would notice."""
        offenders = []
        for path in sorted(PACKAGE.rglob("*.py")):
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if line.startswith(("import playwright", "from playwright")):
                    offenders.append(f"{path.name}:{number}")
        assert offenders == [], (
            f"{offenders} import Playwright at module scope; import it inside "
            "the function that uses it"
        )
