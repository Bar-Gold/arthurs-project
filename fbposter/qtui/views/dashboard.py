"""Dashboard: how the posting is going, and what is still to go out.

It replaced the Queue screen (the user's choice, 2026-10-14). The numbers come
from `fbposter.stats`, which is pure; this file only draws them. Of the
Queue's tools, Cancel stays (under "Going out", with Undo). "Going out" also
lists each active repeating post's next run, with no button (the user's
choice, 2026-10-21): until its time comes a repeating post is not a batch, so
it was nowhere on this screen but the "Next scheduled" tile. The "Went live" and
"Declined" buttons for a post held by a group admin were dropped on purpose:
the worker follows those up by itself (`worker._follow_up_pending`).

Every label still answers "did it post?" in plain words, never with the
worker's own vocabulary ("halted", "skipped"): see `task_label` and
`target_label`.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fbposter import clock, stats
from fbposter.worker import PAUSED_KEY
from fbposter.db.models import (
    TARGET_AWAITING_APPROVAL,
    TARGET_DECLINED,
    TARGET_DONE,
    TARGET_FAILED,
    TARGET_PENDING,
    TARGET_RUNNING,
    TARGET_SKIPPED,
    TARGET_UNCONFIRMED,
    TASK_CANCELLED,
    TASK_DONE,
    TASK_FAILED,
    TASK_HALTED,
    TASK_MISSED,
    TASK_PENDING,
    TASK_RUNNING,
    utcnow,
)

from .. import theme
from ..widgets import badge, card, clear, row
from .compose import media_only

# Every label answers "did it post?" in plain words. The stored states are
# the worker's vocabulary -- "halted", "skipped" -- and showing them as they
# were told the user nothing, or worse: groups left behind by a stopped batch
# said "Pending" for ever, and a batch where every group was skipped said
# "Done" in green.
TASK_LABELS = {
    TASK_PENDING: ("Queued", "TEXT_MUTED"),
    TASK_RUNNING: ("In progress", "ACCENT_TEXT"),
    TASK_HALTED: ("Stopped", "DANGER"),
    TASK_FAILED: ("Stopped", "DANGER"),
    TASK_CANCELLED: ("Cancelled", "TEXT_MUTED"),
    TASK_MISSED: ("Missed", "WARNING"),
}

TARGET_LABELS = {
    TARGET_RUNNING: ("Posting now", "ACCENT_TEXT"),
    TARGET_DONE: ("Posted", "SUCCESS"),
    TARGET_FAILED: ("Not posted", "DANGER"),
    # Stopped after Post was clicked: never "not posted", it may be live. The
    # note under it says to look at the group.
    TARGET_UNCONFIRMED: ("Might have posted", "DANGER"),
    TARGET_SKIPPED: ("Not posted", "WARNING"),
    TARGET_AWAITING_APPROVAL: ("Waiting for admin approval", "WARNING"),
    TARGET_DECLINED: ("Declined by an admin", "TEXT_MUTED"),
}

# The results bar, in the order its segments and legend run: what went out,
# then what is unsure, then what did not. Each names a colour token.
OUTCOMES = (
    (TARGET_DONE, "Posted", "SUCCESS"),
    (TARGET_AWAITING_APPROVAL, "Waiting for admin approval", "INFO"),
    (TARGET_UNCONFIRMED, "Might have posted", "WARNING"),
    (TARGET_FAILED, "Not posted", "DANGER"),
    (TARGET_SKIPPED, "Skipped", "BORDER_STRONG"),
    (TARGET_DECLINED, "Declined by an admin", "TEXT_MUTED"),
)

SNIPPET_CHARS = 60
# A group name longer than this is shortened in the table, whole on hover.
NAME_CHARS = 48
RECENT_LIMIT = 15
TILE_COLUMNS = 4
# The four numbers that lead the page, big, and the two in the strip under
# them (the user's choice, 2026-10-20). Eight boxes of one size gave every
# number the same weight, so none of them led. "Repeating posts" and
# "Different wordings" were taken out of the strip the same day.
LEAD_TILES = ("today", "week", "success", "next")
STRIP_TILES = ("month", "all_time")
CHART_HEIGHT = 170
OUTCOME_BAR_HEIGHT = 14
SWATCH_SIZE = 10


def unverified(target) -> bool:
    """Pictures only, and nothing confirmed they posted.

    Facebook's reply to the Post click confirms a post and gives it a link;
    a post of pictures has no words to look for in the feed either. Without
    the reply, the post window closing is all there was to go on.
    """
    return (
        target.state == TARGET_DONE
        and not target.body.strip()
        and not target.post_url
    )


def task_label(task, targets) -> tuple[str, str]:
    """The batch's status, as (words, colour token).

    A finished batch is described by what reached the groups, not by the fact
    that the worker is done with it.
    """
    if task.state != TASK_DONE:
        return TASK_LABELS.get(task.state, (task.state.title(), "TEXT_MUTED"))
    total = len(targets)
    uploaded = sum(1 for t in targets if unverified(t))
    posted = sum(1 for t in targets if t.state == TARGET_DONE) - uploaded
    waiting = sum(1 for t in targets if t.state == TARGET_AWAITING_APPROVAL)
    # A batch carries on past a group that might have posted (2026-10-20), so
    # a finished one can hold them, and they are never "nothing posted".
    unsure = sum(1 for t in targets if t.state == TARGET_UNCONFIRMED)
    if total and posted == total:
        return "Posted", "SUCCESS"
    if total and uploaded and posted + uploaded == total:
        return "Uploaded, not verified", "WARNING"
    if total and unsure == total:
        return "Might have posted", "WARNING"
    if posted + uploaded + waiting + unsure == 0:
        return "Nothing posted", "WARNING"
    if waiting == 0 and uploaded == 0 and unsure == 0:
        return f"Posted to {posted} of {total} groups", "WARNING"
    return "Finished", "WARNING"


def target_label(target, task_state: str = TASK_PENDING) -> tuple[str, str]:
    """One group's status, as (words, colour token).

    `task_state` matters only for a group whose turn has not come: under a
    batch that has stopped, been cancelled or missed, it never will.
    """
    if unverified(target):
        return "Uploaded, not verified", "WARNING"
    if target.state == TARGET_PENDING:
        if task_state in (TASK_PENDING, TASK_RUNNING):
            return "Waiting its turn", "TEXT_MUTED"
        return "Not posted", "TEXT_MUTED"
    return TARGET_LABELS.get(target.state, (target.state.title(), "TEXT_MUTED"))


def snippet_of(body: str, media_paths) -> str:
    """The post's opening words, or what it carries when it has none."""
    flat = " ".join(body.split())
    text = flat[:SNIPPET_CHARS] + ("…" if len(flat) > SNIPPET_CHARS else "")
    if not text and media_paths:
        text = media_only(media_paths)
    return text


def upcoming(schedules) -> list:
    """The repeating posts still to come round, the soonest first.

    The next run as stored, like the "Next scheduled" tile: a fact, not a
    forecast. A paused post has none to show.
    """
    return sorted(
        (s for s in schedules if s.active and s.next_run_at is not None),
        key=lambda s: (s.next_run_at, s.id),
    )


class DayBars(QWidget):
    """Posts per day as bars, today in full colour. Drawn, not charted:
    QtCharts is left out of the packaged build on purpose."""

    def __init__(self) -> None:
        super().__init__()
        # Transparent like row(): it sits inside a card.
        self.setObjectName("Row")
        self.setMinimumHeight(CHART_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.days: list = []

    def set_days(self, days) -> None:
        days = list(days)
        if days != self.days:
            self.days = days
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        if not self.days:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        small = QFont(self.font())
        small.setPixelSize(theme.SIZE_SMALL)
        painter.setFont(small)
        line = painter.fontMetrics().height()
        top, bottom = line + 4, height - 2 * line - 6
        slot = width / len(self.days)
        bar = max(4.0, min(slot * 0.55, 36.0))
        most = max((count for _day, count in self.days), default=0) or 1
        last = len(self.days) - 1

        accent = QColor(theme.C["ACCENT"])
        # The days before today in a quiet neutral, so today stands out. The
        # accent faded was a muddy brown over navy once the accent was gold.
        faded = QColor(theme.C["BORDER_STRONG"])
        text = QColor(theme.C["TEXT"])
        muted = QColor(theme.C["TEXT_MUTED"])

        painter.setPen(QColor(theme.C["BORDER"]))
        painter.drawLine(0, int(bottom) + 1, width, int(bottom) + 1)
        for index, (day, count) in enumerate(self.days):
            centre = slot * index + slot / 2
            if count:
                tall = (bottom - top) * count / most
                rect = QRectF(centre - bar / 2, bottom - tall, bar, tall)
                painter.setPen(Qt.NoPen)
                painter.setBrush(accent if index == last else faded)
                painter.drawRoundedRect(rect, 3, 3)
                # Muted like the day under it; today's in full, as its bar is.
                painter.setPen(text if index == last else muted)
                painter.drawText(
                    QRectF(centre - slot / 2, rect.top() - line - 2, slot, line),
                    Qt.AlignCenter, str(count),
                )
            painter.setPen(text if index == last else muted)
            painter.drawText(
                QRectF(centre - slot / 2, bottom + 4, slot, line),
                Qt.AlignCenter, "Today" if index == last else day.strftime("%a"),
            )
            painter.drawText(
                QRectF(centre - slot / 2, bottom + 4 + line, slot, line),
                Qt.AlignCenter, f"{day.day} {day.strftime('%b')}" if day.day == 1 or index == 0
                else str(day.day),
            )
        painter.end()


class OutcomeBar(QWidget):
    """One bar split by how each post ended, in the order of `OUTCOMES`."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Row")
        self.setFixedHeight(OUTCOME_BAR_HEIGHT)
        self.counts: dict[str, int] = {}

    def set_counts(self, counts: dict[str, int]) -> None:
        counts = dict(counts)
        if counts != self.counts:
            self.counts = counts
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        whole = QRectF(0, 0, self.width(), self.height())
        painter.setBrush(QColor(theme.C["HOVER_BG"]))
        painter.drawRoundedRect(whole, self.height() / 2, self.height() / 2)
        total = sum(self.counts.get(state, 0) for state, _w, _c in OUTCOMES)
        if total:
            # Clipped to the rounded track, so only the two ends are rounded.
            painter.setClipRect(whole)
            x = 0.0
            for state, _words, colour in OUTCOMES:
                share = self.counts.get(state, 0) / total * self.width()
                if share:
                    painter.setBrush(QColor(theme.C[colour]))
                    painter.drawRect(QRectF(x, 0, share, self.height()))
                    x += share
            painter.setClipping(False)
        painter.end()


class Swatch(QWidget):
    """A small square of one colour, for a legend."""

    def __init__(self, colour: str) -> None:
        super().__init__()
        self.setObjectName("Row")
        self.colour = colour
        self.setFixedSize(SWATCH_SIZE, SWATCH_SIZE)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.C[self.colour]))
        painter.drawRoundedRect(QRectF(0, 0, SWATCH_SIZE, SWATCH_SIZE), 2, 2)
        painter.end()


class Tile:
    """One number on the Dashboard: a caption, the number, a line under it.

    A lead tile is a card of its own. A `small` one is a cell of the strip
    under them, a size down, and is drawn by the strip's card.
    """

    def __init__(self, caption: str, big: bool = True, small: bool = False) -> None:
        self.frame = row() if small else card()
        layout = QVBoxLayout(self.frame)
        pad = theme.PAD_S if small else theme.PAD_M
        layout.setContentsMargins(theme.PAD_M, pad, theme.PAD_M, pad)
        layout.setSpacing(2 if small else theme.PAD_XS)
        self.caption = QLabel(caption)
        self.caption.setObjectName("Muted")
        layout.addWidget(self.caption)
        self.value = QLabel("")
        # A sentence such as "Tomorrow 09:00" does not fit at the size of a
        # number in a quarter of the window.
        self.value.setObjectName("StatSmall" if small else "StatValue" if big else "StatText")
        if not small:
            # Every lead tile's value is a number's height, so the four lines
            # under them sit level. Set smaller, "Today 23:26" pulled its own
            # line up out of the row.
            number = QFont(self.value.font())
            number.setPixelSize(theme.SIZE_STAT)
            self.value.setMinimumHeight(QFontMetrics(number).height())
            self.value.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        layout.addWidget(self.value)
        self.detail = QLabel("")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        layout.addStretch(1)

    def show(self, value: str, detail: str) -> None:
        self.value.setText(value)
        self.detail.setText(detail)


class DashboardView(QWidget):
    title = "Dashboard"
    subtitle = "How your posting is going. Every time is Israel time."

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        # What each rebuilt section shows now. None means "nothing yet", so
        # the first refresh always builds.
        self._groups_shown = None
        self._going_shown = None
        self._recent_shown = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.PAD_XS)
        heading = QLabel(self.title)
        heading.setObjectName("Title")
        outer.addWidget(heading)
        sub = QLabel(self.subtitle)
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        outer.addWidget(sub)
        outer.addSpacing(theme.HEADER_GAP)

        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.holder = QWidget()
        self.column = QVBoxLayout(self.holder)
        self.column.setContentsMargins(0, 0, theme.PAD_S, 0)
        self.column.setSpacing(theme.PAD_S)
        self.area.setWidget(self.holder)
        outer.addWidget(self.area, 1)

        self._build_tiles()
        self._build_chart()
        self._build_outcomes()
        self.groups_box = self._section("By group")
        self.going_heading, self.going_box = self._section("Going out", with_heading=True)
        self.recent_box = self._section("Recent activity")
        self.column.addStretch(1)

    # -- building ----------------------------------------------------------
    def _heading(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SectionHeading")
        # Three times the room above a heading as below it, so each heading
        # reads as the start of its own section, not the foot of the last.
        # A margin of the heading's own, not a spacer: when "Going out" is
        # hidden, a spacer stayed and left a gap twice the others.
        label.setContentsMargins(0, theme.PAD_L, 0, 0)
        self.column.addWidget(label)
        return label

    def _section(self, title: str, with_heading: bool = False):
        heading = self._heading(title)
        box = QVBoxLayout()
        box.setSpacing(theme.PAD_S)
        self.column.addLayout(box)
        return (heading, box) if with_heading else box

    def _build_tiles(self) -> None:
        self.tiles = {
            "today": Tile("Posted today"),
            "week": Tile("Last 7 days"),
            "success": Tile("Success rate"),
            "next": Tile("Next scheduled", big=False),
            "month": Tile("Last 30 days", small=True),
            "all_time": Tile("All time", small=True),
        }

        grid = QGridLayout()
        grid.setSpacing(theme.PAD_S)
        for index, key in enumerate(LEAD_TILES):
            grid.addWidget(self.tiles[key].frame, 0, index)
        for column in range(TILE_COLUMNS):
            grid.setColumnStretch(column, 1)
        self.column.addLayout(grid)

        # One card, its two cells apart by a hairline. Half the gap between
        # the cards above on each side of the line puts it in the middle of
        # that gap, and each cell's words straight under a lead tile's.
        strip = card()
        cells = QHBoxLayout(strip)
        cells.setContentsMargins(0, theme.PAD_S, 0, theme.PAD_S)
        cells.setSpacing(theme.PAD_S // 2)
        for index, key in enumerate(STRIP_TILES):
            if index:
                line = QFrame()
                line.setObjectName("Divider")
                line.setFixedWidth(1)
                cells.addWidget(line)
            cells.addWidget(self.tiles[key].frame, 1)
        self.column.addWidget(strip)

    def _build_chart(self) -> None:
        self._heading(f"Posts per day, last {stats.CHART_DAYS} days")
        box = card()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_M, theme.PAD_M, theme.PAD_S)
        self.chart = DayBars()
        layout.addWidget(self.chart)
        self.chart_empty = QLabel(f"Nothing sent in the last {stats.CHART_DAYS} days.")
        self.chart_empty.setObjectName("Muted")
        layout.addWidget(self.chart_empty)
        self.column.addWidget(box)

    def _build_outcomes(self) -> None:
        self._heading(f"Results, last {stats.MONTH_DAYS} days")
        box = card()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_M, theme.PAD_M, theme.PAD_M)
        layout.setSpacing(theme.PAD_S)
        self.outcome_bar = OutcomeBar()
        layout.addWidget(self.outcome_bar)
        legend = QGridLayout()
        legend.setHorizontalSpacing(theme.PAD_L)
        legend.setVerticalSpacing(theme.PAD_XS)
        self.outcome_counts: dict[str, QLabel] = {}
        for index, (state, words, colour) in enumerate(OUTCOMES):
            line = row()
            line_layout = QHBoxLayout(line)
            line_layout.setContentsMargins(0, 0, 0, 0)
            line_layout.setSpacing(theme.PAD_S)
            line_layout.addWidget(Swatch(colour))
            # The number first, beside its own words (the user's choice,
            # 2026-10-20). At the far end of its third of the card it sat
            # nearer the next column's words than its own, and read as theirs.
            count = QLabel("0")
            count.setObjectName("Strong")
            count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            line_layout.addWidget(count)
            self.outcome_counts[state] = count
            line_layout.addWidget(QLabel(words))
            line_layout.addStretch(1)
            legend.addWidget(line, index // 3, index % 3)
        for column in range(3):
            legend.setColumnStretch(column, 1)
        layout.addLayout(legend)
        self.column.addWidget(box)

    # -- reading -----------------------------------------------------------
    def on_show(self) -> None:
        self.refresh()

    def refresh(self, force: bool = False) -> None:
        """Re-read everything and redraw what changed.

        A fresh read every time, nothing remembered between refreshes: a
        stale read would be a wrong screen. The three lists are rebuilt only
        when what they show changed, because worker events land here several
        times a minute while a batch runs.
        """
        now = utcnow()
        repo = self.app.task_repo
        records = repo.history_records()
        archived = repo.archived_totals()
        tasks = repo.unfinished()
        schedules = self.app.schedule_repo.list()
        settings = self.app.settings_repo

        summary = stats.summary(records, archived, now)
        paused = settings.get(PAUSED_KEY) == "1"
        self._show_tiles(summary, stats.next_scheduled(tasks, schedules, paused, now), now)

        days = stats.per_day(records, now)
        self.chart.set_days(days)
        anything = any(count for _day, count in days)
        self.chart.setVisible(anything)
        self.chart_empty.setVisible(not anything)

        counts = stats.outcomes(records, now)
        self.outcome_bar.set_counts(counts)
        for state, label in self.outcome_counts.items():
            label.setText(str(counts.get(state, 0)))
        # Every count as wide as the widest, so the words after them line up:
        # "12 Not posted" under "7 Posted" started a digit further on.
        widest = 0
        for label in self.outcome_counts.values():
            label.ensurePolished()
            widest = max(widest, label.fontMetrics().horizontalAdvance(label.text()))
        for label in self.outcome_counts.values():
            label.setFixedWidth(widest)

        self._show_groups(stats.by_group(records, archived, self.app.group_repo.list(), now),
                          now, force)
        self._show_going(tasks, schedules, now, force)
        self._show_recent(repo.recent_targets(RECENT_LIMIT), now, force)

    def _show_tiles(self, summary, next_up, now) -> None:
        tiles = self.tiles
        tiles["today"].show(str(summary.today), "Since midnight, Israel time")

        change = summary.last_week - summary.week_before
        if not summary.last_week and not summary.week_before:
            compared = "Nothing sent yet"
        elif change > 0:
            compared = f"{change} more than the week before"
        elif change < 0:
            compared = f"{-change} fewer than the week before"
        else:
            compared = "The same as the week before"
        tiles["week"].show(str(summary.last_week), compared)

        per_day = summary.last_month / stats.MONTH_DAYS
        tiles["month"].show(
            str(summary.last_month),
            f"About {per_day:.1f} a day" if summary.last_month else "Nothing sent yet",
        )
        tiles["all_time"].show(
            str(summary.all_time),
            f"Since {clock.format_local(summary.since, '%d %b %Y')}"
            if summary.since else "Nothing sent yet",
        )

        if summary.success_rate is None:
            tiles["success"].show("-", f"Nothing tried in the last {stats.MONTH_DAYS} days")
        else:
            tiles["success"].show(
                f"{round(summary.success_rate * 100)}%",
                f"Of {summary.tried} tried in the last {stats.MONTH_DAYS} days",
            )

        tiles["next"].show(*self._next_text(next_up, now))

    @staticmethod
    def _next_text(next_up, now) -> tuple[str, str]:
        name = f"Repeating: {next_up.name}" if next_up.name else "A one-off post"
        if next_up.kind == stats.NEXT_PAUSED:
            return "Paused", "Posting is paused in Settings"
        if next_up.kind == stats.NEXT_POSTING:
            if next_up.at is not None:
                return "In progress", f"Next group {stats.when_text(next_up.at, now)}"
            return "In progress", "A batch is going out now"
        if next_up.kind == stats.NEXT_DUE:
            return "Due now", name
        if next_up.kind == stats.NEXT_SCHEDULED:
            return stats.when_text(next_up.at, now), name
        return "Nothing scheduled", "Set one up in Create a post"

    # -- by group ----------------------------------------------------------
    def _show_groups(self, rows, now, force: bool) -> None:
        drawn = tuple(
            (
                r.group_id, r.name, r.members, r.today, r.last_week, r.all_time,
                stats.ago_text(r.last_post_at, now),
            )
            for r in rows
        )
        if not force and drawn == self._groups_shown:
            return
        self._groups_shown = drawn
        clear(self.groups_box)
        if not drawn:
            self.groups_box.addWidget(
                self._muted("No groups yet. Get them in Create a post, step 2.")
            )
            return

        box = card()
        grid = QGridLayout(box)
        grid.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_M, theme.PAD_S)
        grid.setHorizontalSpacing(theme.PAD_L)
        grid.setVerticalSpacing(theme.PAD_S)
        headers = ("Group", "Members", "Today", "7 days", "All time", "Last post")
        for column, text in enumerate(headers):
            label = self._muted(text)
            if 2 <= column <= 4:
                label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(label, 0, column)
        # A hairline under the headers, so they read as the table's and the
        # first group as a row like the rest.
        rule = QFrame()
        rule.setObjectName("Divider")
        rule.setFixedHeight(1)
        grid.addWidget(rule, 1, 0, 1, len(headers))
        for index, (_id, name, members, today, week, total, last) in enumerate(drawn, 2):
            # One line, shortened if long: a wrapped name sized to fit its
            # cell came out two lines tall in a one-line row and overlapped
            # the next. The full name is on hover.
            short = name if len(name) <= NAME_CHARS else name[:NAME_CHARS - 1] + "…"
            name_label = QLabel(short)
            if short != name:
                name_label.setToolTip(name)
            # Sized to the name and placed at the cell's start. Stretched
            # across the cell, a Hebrew name sat at its far end, beside the
            # members column, as if it belonged to that.
            grid.addWidget(name_label, index, 0, Qt.AlignLeft | Qt.AlignVCenter)
            grid.addWidget(self._muted(members), index, 1)
            for column, number in ((2, today), (3, week), (4, total)):
                label = QLabel(str(number))
                label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                grid.addWidget(label, index, column)
            grid.addWidget(self._muted(last), index, 5)
        grid.setColumnStretch(0, 1)
        self.groups_box.addWidget(box)

    # -- going out ---------------------------------------------------------
    def _show_going(self, tasks, schedules, now, force: bool) -> None:
        targets = self.app.task_repo.targets_for_tasks([t.id for t in tasks])
        names = {s.id: s.display_name for s in schedules}
        coming = upcoming(schedules)
        drawn = (
            tuple(
                (
                    task.id, task.state, task.body, task.scheduled_for, task.resume_at,
                    task.error, names.get(task.schedule_id, ""),
                    tuple((t.id, t.state) for t in targets.get(task.id, ())),
                )
                for task in tasks
            ),
            tuple(
                (
                    s.id, s.display_name, stats.when_text(s.next_run_at, now),
                    s.bodies[0] if s.bodies else "", tuple(s.media_paths),
                    len(s.recipient_ids),
                )
                for s in coming
            ),
        )
        if not force and drawn == self._going_shown:
            return
        self._going_shown = drawn
        clear(self.going_box)
        # Nothing waiting and nothing repeating: the section goes altogether.
        self.going_heading.setVisible(bool(tasks or coming))
        # The batches first: they are queued now. Then what comes round later.
        for task in tasks:
            self.going_box.addWidget(
                self._going_card(task, targets.get(task.id, ()), names, now)
            )
        for schedule in coming:
            self.going_box.addWidget(self._upcoming_card(schedule, now))

    def _going_card(self, task, targets, names, now) -> QWidget:
        box = card()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_M, theme.PAD_S)
        layout.setSpacing(theme.PAD_XS)

        header = QHBoxLayout()
        header.setSpacing(theme.PAD_S)
        title = QLabel(snippet_of(task.body, task.media_paths))
        title.setObjectName("Strong")
        title.setWordWrap(True)
        # At the card's start, above the line under it. Left to itself a
        # Hebrew post went to the far end, beside its badge. Alignment, not
        # direction code: the words still read right to left.
        title.setAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
        header.addWidget(title, 1)
        words, colour = task_label(task, targets)
        header.addWidget(badge(words, colour))
        cancel = QPushButton("Cancel")
        cancel.setObjectName("Link")
        cancel.clicked.connect(lambda _c, tid=task.id: self.cancel(tid))
        header.addWidget(cancel)
        layout.addLayout(header)

        when = (
            f"Set for {stats.when_text(task.scheduled_for, now)}"
            if task.scheduled_for else "Right away"
        )
        done = sum(1 for t in targets if t.state not in (TARGET_PENDING, TARGET_RUNNING))
        when += f" · {done} of {len(targets)} group{'s' if len(targets) != 1 else ''} done"
        # The name last, as on a repeating post's line: a Hebrew name before
        # "· 0 of 3" took the number into its own run, and the line read
        # "· 0 · <name> of 3 groups done".
        if task.schedule_id is not None:
            when += f" · {names.get(task.schedule_id) or 'repeating post'}"
        layout.addWidget(self._muted(when))

        if task.error:
            # Why it is waiting: Chrome away, say.
            note = self._muted(task.error)
            note.setWordWrap(True)
            layout.addWidget(note)
        return box

    def _upcoming_card(self, schedule, now) -> QWidget:
        """A repeating post before its time: what, when and how widely.

        No button (the user's choice, 2026-10-21): it is paused, edited or
        deleted on Repeating posts. It is not a batch yet, so there is nothing
        here to cancel.
        """
        box = card()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_M, theme.PAD_S)
        layout.setSpacing(theme.PAD_XS)

        header = QHBoxLayout()
        header.setSpacing(theme.PAD_S)
        words = snippet_of(schedule.bodies[0] if schedule.bodies else "", schedule.media_paths)
        title = QLabel(words or schedule.display_name)
        title.setObjectName("Strong")
        title.setWordWrap(True)
        # At the card's start, as on a batch's card: a Hebrew post went to
        # the far end otherwise.
        title.setAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
        header.addWidget(title, 1)
        header.addWidget(badge("Repeating", "TEXT_MUTED"))
        layout.addLayout(header)

        groups = len(schedule.recipient_ids)
        # The name last: a Hebrew name before "· 2 groups" took the number
        # into its own run, and the line read "· 2 · <name> groups".
        layout.addWidget(self._muted(
            f"Next run {stats.when_text(schedule.next_run_at, now)}"
            f" · {groups} group{'s' if groups != 1 else ''}"
            f" · {schedule.display_name}"
        ))
        return box

    def cancel(self, task_id: int) -> None:
        before = self.app.task_repo.get(task_id)
        self.app.task_repo.cancel(task_id)
        self.refresh()
        undo = None
        if before is not None:
            # One click on a link beside every batch, so a slip is one click
            # to take back rather than an "are you sure?" on every cancel.
            undo = lambda: self._uncancel(task_id, before.state)  # noqa: E731
        self.app.toast("Batch cancelled.", "info", undo=undo)

    def _uncancel(self, task_id: int, state: str) -> str | None:
        if not self.app.task_repo.uncancel(task_id, state):
            return None
        self.refresh()
        return "Batch back in the queue."

    # -- recent activity ---------------------------------------------------
    def _show_recent(self, targets, now, force: bool) -> None:
        drawn = tuple(
            (
                t.id, stats.when_text(t.posted_at or t.attempted_at, now),
                t.group_label, target_label(t, TASK_DONE), t.error,
            )
            for t in targets
        )
        if not force and drawn == self._recent_shown:
            return
        self._recent_shown = drawn
        clear(self.recent_box)
        if not drawn:
            self.recent_box.addWidget(
                self._muted("Nothing yet. Write one in Create a post.")
            )
            return
        box = card()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_M, theme.PAD_S)
        layout.setSpacing(theme.PAD_XS)
        for _id, when, group, (words, colour), error in drawn:
            line = QHBoxLayout()
            line.setSpacing(theme.PAD_S)
            stamp = self._muted(when)
            stamp.setMinimumWidth(130)
            line.addWidget(stamp)
            line.addWidget(QLabel(group))
            line.addWidget(badge(words, colour))
            line.addStretch(1)
            layout.addLayout(line)
            if error:
                # Under its own group, so it is plain which group it is about.
                note = self._muted(error)
                note.setWordWrap(True)
                note.setContentsMargins(130 + theme.PAD_S, 0, 0, 0)
                layout.addWidget(note)
        self.recent_box.addWidget(box)

    @staticmethod
    def _muted(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Muted")
        return label
