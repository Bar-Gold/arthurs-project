# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Where to look

Most of this file is a rule that already cost a live bug. They are grouped by what you are touching:

| Touching | Read |
| --- | --- |
| anything | Repository Status, Architecture, Scope Discipline |
| `worker.py`, scheduling | Rules for the Worker; Power; Repeating posts; Time |
| `automation/` | Rules for the Automation Engine; Selector Strategy; Non-Interfering Operation |
| `qtui/` | The flow; Redrawing; The Compose preview; Visual rules |
| `db/`, retention | Database rules; the two retentions; Queue retention |
| `ui/` (legacy Tk) | `fbposter/ui/CLAUDE.md` — loads on its own when you open a file there |
| tests | How the tests avoid a browser and a real clock |

Text that reaches a post also passes Invisible characters and Hebrew, whatever screen it came from.

## Repository Status

**All five phases are done; v1 is feature-complete.** Chrome debug-profile launcher and CDP session (1), CustomTkinter UI (2), SQLite persistence and the safety guards (3), the automation engine in `fbposter/automation/` (4), and the scheduler/worker in `fbposter/worker.py` (5).

**The app now posts on its own.** Opening the GUI starts the worker, and any due batch will go out. `README.md` holds the full spec. Per-group text editing, the Compose preview, the Qt rewrite and **repeating posts** all shipped after v1; the content-variation warning is now actionable, so it should be rare rather than constant. Since then: a post a group holds for an admin is tracked as its own outcome and resolved by the app itself (`TARGET_AWAITING_APPROVAL` and `_follow_up_pending`, see the Worker rules), a dropped Chrome connection defers a batch instead of throwing it away, and `scripts/setup_always_on.ps1` covers the laptop that has to post with its lid shut (see Power).

## What This Is

A local, single-user Windows desktop app (Python 3.10+, Qt/PySide6) that posts text and media to Facebook groups on a schedule, by driving a real logged-in Chrome session through Playwright over CDP. Everything runs on the user's machine — no server, no cloud, no automated login.

## Commands

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

.\.venv\Scripts\python.exe -m pytest tests/ -q                                 # full suite
.\.venv\Scripts\python.exe -m pytest tests/test_session.py::TestClassifyUrl -q  # one class
.\.venv\Scripts\python.exe -m pytest tests/test_session.py -q -k checkpoint     # one test

.\.venv\Scripts\python.exe main.py start    # everyday use: Chrome if needed, then the app
.\.venv\Scripts\python.exe main.py gui      # the app alone, no Chrome handling
.\.venv\Scripts\python.exe main.py gui --tk # the old Tkinter window (legacy)
.\.venv\Scripts\python.exe main.py setup    # Chrome on-screen, for the one-time manual login
.\.venv\Scripts\python.exe main.py launch   # Chrome off-screen, ready for automation
.\.venv\Scripts\python.exe main.py status   # attach over CDP, report the session state

# Phase 4, both safe to run against a real group:
.\.venv\Scripts\python.exe main.py probe   <group-url>              # resolve selectors; types nothing
.\.venv\Scripts\python.exe main.py dry-run <group-url> --text "..." # full rehearsal, never clicks Post
```

```powershell
# Laptop that must post with the lid shut. Mains-only, and fully reversible:
powershell -ExecutionPolicy Bypass -File scripts\setup_always_on.ps1
powershell -ExecutionPolicy Bypass -File scripts\setup_always_on.ps1 -Revert
```

`probe` and `dry-run` are the tools for re-checking selectors whenever Facebook changes its markup. Reach for them before touching `poster.py`.

`status` exits 0 when logged in, 1 when not, 2 on error (Chrome not running, etc.).

There is no linter or formatter configured, and no pytest config file — the suite is the whole check. Baseline: **1095 tests, 75-150s** — the spread is machine load, not the suite; the Qt and Tk GUI files are ~80s of it on their own. A run of *five minutes or more* means something is reaching the network; see the `SilentNamer` note below.

**Do not add `playwright install`.** It is unnecessary and was verified so against Chrome 150: the app attaches to the user's real Chrome over CDP and never launches Playwright's bundled Chromium, so the driver shipped inside the pip package is all that is required.

## Critical Constraint: Chrome Debug Profile

Chrome 136+ **refuses `--remote-debugging-port` when using the default user profile.** The app therefore cannot attach to the user's everyday Chrome window, and any code or docs assuming it can are wrong. A dedicated profile directory is required:

```
chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\FBAutomation\ChromeProfile"
```

The user logs into Facebook manually inside that profile, once. Playwright then attaches with `connect_over_cdp` — it never launches its own browser context and never logs in.

## Architecture

Three layers that must stay separate:

- **UI (main thread)** — the Qt event loop (`qtui/`; `main.py gui --tk` runs the legacy CustomTkinter one). Never touches Playwright objects or the database directly during long operations.
- **Worker (exactly one background thread)** — owns the Playwright sync API and processes the task queue strictly serially. One group at a time, globally. There is never a second worker, and batches never overlap; a "Post Now" issued mid-batch is appended to the queue rather than run concurrently.
- **SQLite** — the source of truth for queue state, not just persistence. Each group's outcome is committed as it completes so a crash or restart resumes the batch instead of re-posting. Duplicate posts are the single worst failure mode here (strong spam signal), so idempotency belongs in the schema, not in memory.

UI and worker communicate through a thread-safe queue. The tables as built are `groups`, `templates`, `tasks`, `task_targets`, `schedules`, `schedule_targets` and `settings`; `tags`, `group_tags` and `run_log` were cut (README §9 and §10) — do not write code expecting them.

Core: `config.py` (paths, port, Chrome flags), `chrome.py` (find/launch Chrome, probe the debug port), `session.py` (CDP attach, `c_user` cookie check), `strings.py` (every Facebook URL and UI string, in all three languages), `clock.py` (Israel-time judgement), `power.py` (`SleepBlocker`, `on_battery`), `guards.py` (the safety rules as pure functions), `recurrence.py` (repeating-schedule rules, also pure), `groups.py` (group-URL parsing), `errors.py`, `worker.py` (`PostingWorker` and `LivePoster`).

Scripts: `scripts/setup_always_on.ps1` — the laptop power plan and the logon task. See the Power section.

Automation: `automation/poster.py` (`GroupPoster` — arrive → compose → type → attach → publish → verify, plus the read-only `probe`), `detect.py` (`classify` a page as OK / checkpoint / login / rate-limit / unavailable), `humanize.py` (`Humanizer`: keystroke timing, hovers, arrival scroll, the inter-group gap), `groupinfo.py` (read a group's display name off its `h1`; cosmetic, and must never raise into a caller).

UI (Qt, current): `qtui/app.py` (window, sidebar, connection pill, worker row, worker-event pump, background thread helper), `qtui/views/` (compose, groups, publish, queue — the nav order is the flow), `qtui/theme.py` (palette + one stylesheet), `qtui/widgets.py` (`card`, `row`, `clear`), `qtui/assets/`. It reuses `ui/connection.py` and every non-UI module unchanged.

UI (Tk, legacy — `main.py gui --tk`): `ui/app.py`, `ui/views/`, `theme.py`, `toast.py`, `background.py`, `connection.py`, `preview.py`, `textdir.py`.

Storage: `db/` — `connection.py` (per-thread connections), `schema.py` (migrations), `models.py`, `repo.py` (`GroupRepo`, `TemplateRepo`, `TaskRepo`, `ScheduleRepo`, `SettingsRepo`).

### Invisible characters must never reach a comparison

`fbposter/text.py` owns one list of characters that print nothing — bidi marks, zero-width joiners, soft hyphen, BOM — and `strip_invisible()` removes them. It exists because two load-bearing comparisons are string equality against the user's own words:

- **`guards.normalise`** folds them, so `check_repeat_text` cannot be defeated by a paste from Word or WhatsApp. Before this, the same ad pasted twice compared as *different text* and the guard waved through the exact repeat it exists to stop. Folding in `normalise` rather than only at the input is deliberate: bodies already stored with marks in them have to compare correctly too.
- **`distinctive_snippet`** strips them, because Facebook drops them when it renders. A snippet still carrying one is searched for and never found, which reports a post that went out fine as failed and halts the batch.

Both Qt entry points clean on the way in as well — `ComposeView.get_text()` and `PublishView.alternates()`. The Tk `textdir.strip_controls()` now delegates to the same list. **Qt needing no direction marks of its own is not the same as no marks arriving**; that gap is how this shipped.

### Time: stored in UTC, judged in Israel time

Every timestamp is stored and compared in UTC, but **every human judgement is made in `Asia/Jerusalem`** — the posting window, what counts as "today" for the daily cap, and every time shown in or typed into the UI. Go through `fbposter/clock.py` (`to_local`, `local_hour`, `start_of_local_day`, `next_window_open`, `parse_local`, `format_local`); never read `.hour` or `.date()` off a stored UTC value. Doing that put the window three hours out — it allowed a 01:00 local post and refused a 09:00 one, and posting at 4am is the loudest automation signal there is.

**A posting window may cross midnight.** `clock.inside_window` owns the rule and both `guards.check_posting_window` and `clock.next_window_open` defer to it, because when they disagreed a 22:00–06:00 window excluded *every* hour: nothing could post and the batch deferred itself one day at a time, for ever, silently. `clock.sane_hour` also guards against a stored hour outside 0–23, which used to raise `hour must be in 0..23` inside `datetime.replace` — swallowed by the worker loop and retried every tick, so the app just stopped posting.

`tzdata` is therefore a hard requirement, not a nicety: Windows ships no IANA time zone database, so without it `Asia/Jerusalem` does not resolve and `clock.posting_zone()` falls back to the machine's own zone.

### The flow: what, then where, then when

Three screens, one step each, and the sidebar order *is* the sequence. The split is load-bearing, not cosmetic:

- **Compose — what.** The text, the per-group rewrites, the attachments, the templates. No timing controls and no group picker; it shows a read-only "Sending to" summary and its primary button just navigates.
- **Groups — where.** Ticking recipients and managing the list are one screen on purpose: the moment you notice a group is missing is the moment you are choosing groups, so "add a group" lives there. A group added here starts ticked, because it was added in order to be posted to.
- **Publish — when.** Now, Once, or Repeat, in one card. It calls `ComposeView.add_to_queue(when)` for the first two. **`PublishView.on_show()` calls `compose.capture()` first**, because `body_for()` reads committed state only and would otherwise publish the text from before the user's last keystroke.

**The selection lives on the window, not in a view** — `App.selected_groups` (a set of ids) with `App.selected_group_ids()` deriving the ordered list from the live group list. Three screens need to agree on it: Groups ticks them, Compose opens a wording tab per group, Publish sends to them. Deriving the order rather than storing it means a group deleted while ticked simply drops out instead of surviving as an id that no longer resolves.

**The one-off date picker is `ScheduleEntry`, not a bare `QDateTimeEdit`.** A stock one opens with the *year* section focused and accepts the mouse wheel, so one scroll over it moved a post a year out and two read as "the default is 2028" — which is exactly how it was reported. It now starts on the minutes, ignores the wheel entirely, is bounded to `[now, now + SCHEDULE_HORIZON_DAYS]`, defaults to **the current time** (an arbitrary +1h offset was reported as the next thing to undo), and `reset()`s every time Once is opened rather than once at startup — a default set at launch is a past time by the afternoon.
- **In Repeat mode the Compose text is wording #1**, and the screen collects *alternates*. That is what stops "which one is the real post?" being a question the user has to answer.
- The three mode panels live in one `QStackedWidget`, and `set_mode` marks every page but the current one `QSizePolicy.Ignored` — a stacked widget is otherwise as tall as its tallest page, which left a large void under the one-line Now panel.
- Repeat-only widgets are grouped in `self.repeat_extras` and shown or hidden as one. Hiding them individually left their spacing behind and pushed the rest of the column down the screen.
- **The sidebar is numbered.** `FLOW_STEPS` drives "1. Compose / 2. Groups / 3. Publish", with Queue below a divider because it is where you look afterwards, not a step. Four equal-looking items gave no clue they were meant to be walked in order.
- **The wording tabs live on their own row in a horizontal scroll area.** Sharing a row with the Write/Preview toggle, the layout shrank them below their own text and clipped group names mid-word once the font grew. The strip uses `setAlignment(Qt.AlignLeft)` rather than a trailing stretch, because `refresh_tabs()` clears the layout by index and counts what is in it.
- **A bare `QWidget` inside a card draws a grey band across it** — the global stylesheet gives every widget the window background. Use `widgets.row()` for any container inside a card; it is styled transparent in `theme.py`.

### Repeating posts

A `schedules` row is a **definition**, never a queue entry. When one comes due the worker materialises an ordinary `tasks` row from it and gets out of the way, so the serial queue, the 10-25 minute gap, the guards re-checked at posting time and crash recovery all apply unchanged. **Do not add a second posting path** — it would have to re-earn every one of those properties.

- **A schedule holds several wordings, and that is the whole feature.** `guards.check_repeat_text` refuses the same words to the same group *ever again* (it reads `GroupRepo.recent_bodies`, 20 deep). A schedule with one wording would fire exactly once and then be refused forever. `recurrence.pick_body(variants, offset, recent_bodies)` picks each group's text, and the offset is `run_count + position`: group 0 takes the next wording, group 1 the one after, and the whole cycle shifts every run. That is also why one run never sends identical text to several groups. Never "fix" a stalled schedule by relaxing the repeat guard — repetitive content is the actual ban vector at this volume.
- **When a group has seen every wording it is skipped, loudly, and the batch goes on without it.** The user is told to add another wording. Silently reposting would be the single worst thing this feature could do.
- **Occurrences are wall-clock times in Israel, recomputed through `clock.py`.** "09:00" means nine in the morning across a daylight-saving change, which is a different number of seconds each time; `next_occurrence` walks days in local time rather than adding 24h. `tests/test_recurrence.py` pins the 23-hour step across the 2026 spring transition.
- **A missed slot is dropped, never fired late.** Same `MISSED_GRACE` as everywhere else. Waking the machine at 19:00 must not fire the 09:00 and 14:00 slots as a burst — that is exactly the activity pattern the schedule exists to avoid.
- **A schedule never stacks a batch on top of an unfinished one of its own** (`TaskRepo.unfinished_for_schedule`); the occurrence is skipped instead.
- Resuming a paused schedule recomputes `next_run_at` rather than firing the slot that went by while it was paused.
- **The per-group cooldown defaults to 8 hours**, lowered from 24 by the user so that two or three posts a day to one group is possible at all. Migration 005 carries that onto databases seeded with the old value, and moves groups still sitting on 24 — but leaves a group the user deliberately set to something else alone. `tests/test_db.py` reads the number off `DEFAULT_SETTINGS` rather than writing it out, so changing it again is a one-line job.
- `recurrence.preview()` is what warns, before anything is written, that a chosen time sits outside the posting window, that the frequency is inside the per-group cooldown, or that there are too few wordings for the number of groups. Those are the three ways this feature quietly disappoints; none of them block.

### Redrawing: an unchanged screen is not rebuilt

Every view used to clear its layout and rebuild every widget on every visit. Measured with 12 groups and 25 batches: **Queue 110ms, Groups 44ms, Publish 34ms** per view switch — all of it widget construction, to draw exactly what was already on screen. The Queue paid it again on *every worker event*, so during a batch, which is when someone is actually watching it, it rebuilt several times a minute.

Each of those four now computes a cheap **snapshot of what it would draw** — ids, states, labels, errors, selection — compares it with what is drawn, and returns early when they match. The result: **Queue 2.1ms, Groups 0.6ms, Publish 0.8ms, Compose 1.2ms.**

- **The snapshot must contain everything the widgets show.** Anything left out goes stale on screen and no test will catch it, because the data is right and only the pixels are wrong. `queue._snapshot` covers task state, error, body, scheduled time, schedule name, and every target's state, error and group name; `groups._snapshot` covers name, cooldown and tick.
- **Re-read, but in bulk.** These snapshots re-read the same rows the rebuild would have read rather than remembering them, because a stale read is a wrong screen. That still holds — but *one query per row* does not scale, and the Queue proved it: 25 batches x 6 targets took ~175 queries per refresh, because `_group_name()` looked up each target's group to recompute `t.group_label`, a string `targets_for` had already joined onto the row. `targets_for_tasks()` and `names_by_id()` fetch the whole screen in three queries. Same rows, same moment, nothing kept between refreshes — just not a round trip each.
- **Anything outside the guard still runs every time.** Publish's snippet reads the post body, which changes without the recipient list changing at all, so it is deliberately above the early return. Groups still calls `refresh_count()`, because Compose listens to it for its wording tabs.
- **The Queue keeps a snapshot per card, not just per screen.** One screen-wide snapshot answers "has anything changed", and during a batch the answer is yes on every worker event — one target moved to `done` — so all 25 cards were rebuilt for it, 127ms on the drawing thread, several times a minute, at exactly the moment someone is watching. `_cards_shown` holds each card's own row; when the batches are the same and in the same order, only the cards whose row moved are swapped out, through `widgets.replace_at`. Measured: **127ms -> 6.5ms** for a worker event. A card renders one batch, so this is safe in both scopes — Recent and All draw the same batch identically.
- `refresh(force=True)` rebuilds regardless — for a repaint the data cannot describe. So does anything that changes the list's length or order, since a reused card would then be sitting in the wrong row.
- **Widget state the user can change by clicking is not cache-able at all, and must be restated outside the guard.** The Compose wording tabs are `setCheckable` and in no exclusive `QButtonGroup`, so Qt flips the clicked button *before* `select_tab` runs. Three ways that broke, none of which alters a single label — which is what the guard is keyed on, so none of them could be fixed by widening the key: switching tabs left the highlight on the tab just left; clicking a second tab lit **two at once**; clicking the tab you were already on toggled it **off**, lighting none. The old unconditional rebuild corrected the fill every time and hid all three. `_mark_active_tab()` now restates `setChecked` after the guard, always. This is the general trap: a snapshot describes *data*, and a widget the user can mutate directly is not described by it.

**Startup cost was Playwright, and it is now deferred.** This reverses an earlier decision recorded here, on a re-measurement: `playwright.sync_api` costs **~830ms**, not the ~340ms first measured, and importing `fbposter.qtui.app` went from **1670ms to 682ms** without it. The earlier note also had the mechanism wrong — neither `worker.py` nor `automation/groupinfo.py` imports Playwright; **`session.py` does**, and both pull it in through that, so one lazy import fixed both rather than needing two.

It is imported inside `session.attach()`. The objection recorded last time — that this moves the cost onto the first background name lookup or the first post — is exactly why it is safe: both of those already run **off the thread drawing the window**, and the first post has a ten-to-twenty-five-minute gap in front of it. A second on the worker thread is invisible; a second before the window appears is not. `tests/test_qtui_performance.py` pins both halves — that importing the window does not pull Playwright in, and that nothing in the package imports it at module scope, since the cost comes back silently wherever that is written.

**Nothing slow may run on the thread drawing the window.** `chrome.probe()` is a synchronous HTTP call to the debug port: ~14ms when Chrome answers, and up to `PROBE_TIMEOUT_S` (**one full second**) of a frozen window when something holds the port without replying. The Groups screen ran it inline on every show; it now runs inside the `run_in_background` worker, where the guard still applies. `tests/test_qtui_performance.py` pins that it stays there.

### The Compose preview is shaped like a real post

`PostPreview` in `qtui/views/compose.py` is deliberately built to look like a feed post — monogram avatar, name, meta line, text, **full-bleed** media, then a Like/Comment/Share row. That is not decoration: a preview that looks nothing like the destination cannot answer "will this read well when it lands?", which is the only question it exists to answer.

- **Pictures crop to fill their tile (`cover()`), they do not fit inside it.** Fitting leaves letterbox bars and makes a row of tiles look ragged; stretching distorts faces. The exception is a **single** picture, which keeps its natural aspect ratio — cropping the only photo in a post would misrepresent it — capped at `MAX_SINGLE_HEIGHT` so it cannot push the rest off screen.
- **Layouts are 1 / 2 / 3 / 4 / "+N"**, matching what a feed does. Past `MAX_TILES` the fourth tile carries a `+N` scrim.
- **A picture that will not open still gets a tile**, named, occupying its slot so the grid keeps its shape. The file is still going to be uploaded; showing nothing would suggest it had been dropped. Same rule as the Tk `preview.py`.
- **Short text with no picture is set large**, as a feed does. It is most of why the thing reads as a post rather than a label.
- **Preview mode gets the whole window.** The right rail (templates, "Sending to") and the attach button with its file list are all *writing* tools, and holding a third of the window plus 150px of height for them left the preview itself in half a screen, clipped, with its Like/Comment/Share row below the fold. `set_mode` stands both down, and the post is **centred** in the space the way a feed centres its column — pinned to the left edge of a wide pane it read as a stray panel. Listing the attachments under a preview that already shows them was a second telling of the same fact, and it was the height that stopped the post fitting.
- **The canvas gets the full width; the post does not.** `MAX_POST_WIDTH` stays feed-shaped (500px, about what Facebook uses). Stretching the post to fill a wide window would defeat the only question the preview exists to answer.
- **The post width is recomputed on resize** (`post_width()`, clamped to `MIN_POST_WIDTH..MAX_POST_WIDTH`) rather than fixed, so a narrow pane never produces a horizontal scrollbar. `resizeEvent` only redraws past `RESIZE_SLACK`.
- **A picture is decoded at the size it is drawn, and only once.** `QPixmap(str(path))` decodes every pixel in the file. The user's own attachment is a 5712x4284 phone photo and takes **~600ms**, and the preview paid that *twice* per render — once in `_single_height`, merely to read the aspect ratio, and once to build the tile — then paid the pair again on every redraw, which includes a resize, the switch into Preview, and walking to another screen and back. Loading a template, switching to Preview and stepping to Groups and back measured **1137ms**; it is now **77ms**. `load_tile()` hands `QImageReader` the box it is filling so the JPEG decoder scales while decoding, and caches the result against the file's mtime and size; `image_size()` reads the header rather than decoding at all. **Never call `QPixmap` on an attachment path again** — that one line is worth more than every other redraw guard in this file put together.
- **The EXIF rotation is applied, because Facebook applies it.** A phone stores a portrait photo sideways with "rotate 90" in the header, so the preview used to show it on its side — and, worse, laid it out with the width and height the wrong way round, since `QImageReader.size()` reports the *stored* shape. `_oriented_size()` swaps the axes for the quarter-turn cases, and `setScaledSize` takes the stored orientation, so the target is swapped back before it is set. A preview that shows a photo in a different orientation to the destination is not answering the question it exists to answer.
- **The footer has no counts.** Inventing "12 likes" on a post that has not been published would be showing the user something untrue.
- **`text_shown()` still returns the logical post**, whatever the labels hold.

**Remove a widget with `widgets.clear()` or `widgets.replace_at()`, never by hand.** `takeAt()` does not unparent: the widget stays a child, goes on painting, and — no longer managed by any layout — reverts to Qt's **default 640x480**, so it paints at a size it never had. `clear()` calls `setParent(None)` as well, and recurses into nested layouts; `replace_at()` does the same for one widget, and is how the Queue swaps a single card. Both live in `widgets.py` because `tests/test_qtui_polish.py` fails on a `takeAt` anywhere in the views — so a new removal goes in that module, not in the view that needs it.

This was fixed once for the preview collage and then not applied to the five other places doing the same thing, which is how the Compose screen shipped with a **blue band across it**: the stale "All groups" tab was checked, therefore filled with the accent colour, and it painted 640x480 behind the tab strip, clipped by the scroll area into a neat rectangle that looked completely deliberate. Same root cause as the "duplicate row" and "stale collage" scares. `tests/test_qtui_polish.py` greps for a stray `takeAt` in the views so the sixth one cannot be written.

**A Qt flag enum is not an `int`, and the "never raise" wrapper will hide it.** `int(reader.transformation())` raises `TypeError` on PySide6 — use `.value`. That matters here beyond the typo, because image loading is deliberately wrapped so a corrupt attachment cannot take the screen down: the exception was swallowed and *every* picture silently became a named "no preview" tile. A rule that says "must never raise" needs its failure path checked against a file that is known to be good, or a programming error inside it looks exactly like a bad file.

**`QPixmap` cannot be constructed before a `QApplication` exists** — Qt aborts the process (`STATUS_STACK_BUFFER_OVERRUN`), so pytest reports nothing at all rather than a failure. Any test touching `QPixmap`, `cover()` or `avatar()` must depend on the `qt_application` fixture even if it never builds a widget.

### Visual rules, and the tests that hold them

`tests/test_qtui_theme.py` computes real WCAG ratios against both palettes rather than trusting that colours "look fine". It found five genuine failures in the inherited Facebook palette, so the values are no longer Facebook's:

- **`ACCENT` is `#0C68DE`, not `#1877F2`.** Facebook's own blue puts white text at 4.23:1, under the 4.5:1 floor. `WARNING` and `NEUTRAL` were moved for the same reason — `NEUTRAL` was the connection pill at 3.3:1.
- **`ACCENT_TEXT` is a separate token from `ACCENT`.** In dark mode the two have opposite requirements: the fill must be dark enough to carry white text, the text must be light enough to sit on a dark surface. One value cannot do both. Use `ACCENT` for fills and borders, `ACCENT_TEXT` anywhere the accent *is* the text.
- **Focus must stay visible.** A custom stylesheet replaces the platform focus rectangle, so `:focus` rules are load-bearing rather than decoration — without them, tabbing moves an invisible cursor. Never add `outline: none`; a test greps for it.
- **Exactly one accent-filled `#Primary` button per screen: the next step in the flow.** Compose and Groups each carried three — the step button, a secondary action beside it, and "Check connection", which sits in the sidebar on *every* screen — so the fill meant nothing anywhere. "Attach images", "Add group" and "Check connection" are all plain buttons now; the accent belongs to "Next: …" and to "Post now". A checked `#Tab` is still accent, and that is fine: it marks a selection, not an action. Pinned by `tests/test_qtui_polish.py`.
- **A bare container inside a card paints the window background.** `QLabel`, `QCheckBox`, `QWidget#Row` and **`QStackedWidget`** are all listed transparent in the stylesheet; the last was missing, and the Publish mode switcher drew a grey slab across the white "When" card wherever its page did not cover it. Anything new that holds children inside a card goes on that list.
- **The checkbox indicator is styled explicitly, in both states.** Left to the platform, checked drew a bare grey tick with no box and unchecked drew an empty box — two states that did not look like one control, with *selected* the fainter of them, on the one screen whose whole job is picking groups. It is now an accent-filled rounded box with a white tick from `qtui/assets/check.svg`. The path is fed through `Path.as_posix()`: **a backslash is an escape character inside a Qt stylesheet**, so a native Windows path loads nothing and fails silently. If the SVG ever goes missing the box still fills with the accent, so the state stays readable and only the tick is lost.
- **The action sits with what it acts on.** Publish put `addStretch(1)` above its summary and Post button, anchoring them to the bottom of the window; in Now and Once modes, where the panel is one line, that left a void most of the screen tall between choosing and doing. The slack goes below everything.
- **Body text is 15px and the application font sets family only.** It used to be 13px *and* shrunk another 2pt by `QFont(FONT_FAMILY, SIZE_BODY - 2)` in `run()` — two sources of truth for size, with the smaller one winning.

**Verifying focus states is not possible through the normal render harness.** `WA_DontShowOnScreen` windows are never active, so `hasFocus()` is False and `:focus` never fires — an absent ring in such a screenshot proves nothing. Use the `offscreen` *platform plugin* instead (`QT_QPA_PLATFORM=offscreen`), where windows do activate; text renders as tofu there because the platform has no fonts, but borders draw correctly.

### Two retentions, and only one of them deletes

They are separate settings on purpose and are easy to confuse:

| Setting | Default | What it does |
| --- | --- | --- |
| `queue_retention_hours` | 24 | **Hides** finished batches on the Queue screen. Nothing is deleted; the "All" toggle shows them again. |
| `history_retention_days` | 90 | **Deletes** finished batches, permanently. 0 disables it. |

`TaskRepo.prune_history` is the destructive one, and it is deliberately conservative. It never touches:

- anything **unfinished** — a batch still due to go out is not history;
- the newest **`RECENT_BODIES_LIMIT` posted bodies per group**, however old, because that is exactly the window `GroupRepo.recent_bodies` reads for `check_repeat_text`. Deleting them would let a wording a group has already had be sent again, which is the app's main protection against a restriction. `RECENT_BODIES_LIMIT` is one constant used by both — **if they ever drift apart, pruning silently weakens the guard**, and `tests/test_history_prune.py` pins that they match.

A consequence worth knowing before "fixing" it: **with few posts to a group, nothing ages out at all**, because every post is still inside the guard's window. That is correct, and the storage involved is trivial — 20 bodies per group, for ever.

`sqlite3` reports `rowcount` as **-1** for a `DELETE` that begins with a CTE, so the prune selects the doomed ids first and deletes by id; a count that silently means "unknown" is worse than no count. `reclaim_space()` runs `VACUUM` **and then `PRAGMA wal_checkpoint(TRUNCATE)`** — under WAL the rewritten pages sit in the `-wal` file and the main database does not shrink on disk without it.

The worker calls this at most once a day (`PRUNE_EVERY`), tracked in the `last_prune_at` setting rather than in memory so that restarting the app does not re-run it and an app left open for weeks still gets round to it. A failure there is reported and swallowed: housekeeping must never be able to stall the queue.

### Queue retention is a view filter, never a purge

The queue screen shows every unfinished batch however old, plus batches finished within `queue_retention_hours` (24 by default); an "All" toggle shows the rest. `TaskRepo.list_for_queue` and `count_older_than` do the filtering.

**Do not "tidy up" by deleting old rows.** `task_targets` is what `GroupRepo.recent_bodies` reads to refuse sending the same words to a group twice, and what `posted_count_since` counts for the daily cap. Deleting history would silently switch off the app's main anti-ban protection and let a body be reposted — the screen would look tidier and the account would be at more risk. `tests/test_qtui_queue.py::TestNothingIsDeleted` guards both halves.

A batch still pending or running is never hidden, whatever its age: hiding something still due to go out is worse than a cluttered screen.

### Database rules

- **`Database.transaction()`, never `with connection:`.** Connections use `isolation_level=None` (autocommit), so `with connection:` commits a transaction that was never begun and a later failure leaves earlier statements written. This already produced an orphan task row once; `tests/test_db.py` guards it.
- **One connection per thread.** The UI thread and the Phase 5 worker both use the database; `Database.connection` is thread-local and WAL is on so a read never blocks on a write.
- **Safety decisions live in `guards.py` as pure functions**, never in a repository or a view. Repos supply the counts and timestamps; guards judge them. That keeps the whole safety table testable without a database or a browser.
- The `UNIQUE(task_id, group_id)` index is load-bearing — do not drop it to "fix" an insert error.
- **Schema changes append a migration to `MIGRATIONS` in `schema.py`; never edit an existing one.** They are keyed on `PRAGMA user_version` and the earlier ones have already run against the user's real database.
- The App takes an injectable `db=`; tests pass a temporary database and must never touch `C:\FBAutomation\fbposter.db`.

### Hebrew, and why the UI is Qt

**`fbposter/qtui/` is the UI. `fbposter/ui/` is the old Tkinter one**, kept runnable with `main.py gui --tk` and still covered by `tests/test_ui.py`. Build new UI work in `qtui/`.

The move was forced by Hebrew, which is most of what this app is used to write. **Tk 8.6 has no bidirectional text support at all**: it lays characters out in logical order, left to right, always — `Text.bbox()` proves it, and no tag, `justify`, RLM, RLE or RLI moves a single x-coordinate. What made Hebrew look right in Tk was Windows reordering each *run* it draws (one unbroken stretch of one script), so a pure-Hebrew line came out fine while any line mixing Hebrew with English or digits came out a **mirror image** of the truth. Three rounds of increasingly elaborate workarounds in Tk — per-line justify tags, an invisible U+202B embedding, `python-bidi` reordering in the preview — never got the editor right.

Qt shapes text itself and needs none of it. `qtui/views/compose.py` contains **no direction code whatsoever**, and a plain `QTextEdit` renders the mixed sentence identically to Facebook, aligning Hebrew paragraphs right on its own. Do not port `textdir.py` into `qtui/`; if Hebrew ever looks wrong there, the cause is something else.

**Verifying anything about Hebrew rendering:** never read glyph order off a screenshot — that produced two confidently wrong diagnoses in a row. Split the image into halves and identify an unambiguous anchor (a Latin word, a digit run), or compare pixels against a known-correct rendering. In the test sentence "…אני רוצה … kalofan והמחיר … 1000 שקל", correct output puts `אני` at the far right and `1000` in the left half.

### UI rules that carry over to both UIs

The Tk widget specifics — `CTkFrame`/`CTkButton` defaults, pack order, and the whole `textdir.py` bidi apparatus — now live in **`fbposter/ui/CLAUDE.md`**, which loads on its own when you open a file in `fbposter/ui/`. None of it applies to `qtui/`. What follows holds in both windows.

- **Only the main thread touches widgets.** Blocking work goes through a background thread → `queue.Queue` → a pump on the UI thread: `BackgroundRunner` and `widget.after()` in Tk, `App.run_in_background` and a `QTimer` driving `App._drain_worker_events` in Qt. The posting worker reports progress the same way and never touches a widget itself.
- **No modal dialogs for status, ever** — use `app.toast`. The single permitted OS dialog is the app's own media file picker in Compose, because the user asked for it and it can only fire while they are sitting there choosing images. Chrome's native file dialog is a different thing entirely and is never acceptable — see the Photo/video rule below.
- **Compose owns per-group wording, and `body_for()` is the only way to read it.** `_base_body` is the shared text, `_bodies` holds per-group rewrites, `_editing` is the active tab. `body_for()` reads committed state only, so `capture()` must run first — it once returned the live editor contents when that group was active, which handed back the wrong text as soon as `_editing` was assigned before the read. Editing the base clears the rewrites (the user's choice) and toasts, and only when the text genuinely changed — a tab switch must never cost someone their wording.
- **Anything in a view that reaches for a browser must be injectable, and the shared test App must be given a stub.** The Groups view looks up group names on its own whenever it is shown, and `chrome.probe()` succeeds on any machine with Chrome running — so before `SilentNamer` existed, the GUI suite silently opened real Facebook pages and took nearly three minutes instead of twenty seconds. Both `App`s take `check_fn=`, `db=` and `group_namer=` for this reason.

### How the tests avoid a browser and a real clock

Nothing in the suite opens Chrome, hits Facebook, or waits out a real delay. Keep it that way — every seam already exists:

- **`tests/fake_page.py`** stands in for a Playwright `Page`, recording every call into `page.calls`. It implements only the surface `GroupPoster` actually uses, so a poster that starts calling something new fails loudly instead of quietly passing. Its knobs (`missing`, `redirect_to`, `body_text`, `wait_fails_for`, `never_detaches`) are how the halt paths, the slow-publish path and the dry-run boundary get exercised.
- **`Humanizer(rng=, sleep=)`** — pass a seeded `Random` and a no-op sleep and the human pacing is deterministic and instant.
- **`PostingWorker(poster=, now=, sleep=, blocker=, tick_seconds=)`** — the whole loop, including the inter-group gap and crash recovery, runs without a thread, a browser or the wall clock.
- **`App(check_fn=, db=, group_namer=)`** — a temporary database and `SilentNamer`. Constructing an `App` deliberately does not start the worker. Both the Tk and the Qt window take the same three seams.
- **Qt tests run offscreen.** `tests/conftest.py` has a session-scoped `qt_application` (one `QApplication`, `QT_QPA_PLATFORM=offscreen`) and a per-test `qt_app` window on a temporary database. Offscreen is not tidiness: this app's central promise is that it never takes focus, and a suite that popped real windows would break that on the developer's own machine every time it ran. Drive views through their own methods rather than synthesised clicks. Note that `deleteLater()` widgets keep painting until the event loop turns, so anything that reads pixels needs a real loop turn first — two "duplicate row" and "giant blue rectangle" scares came from screenshotting without one.

**Never call `browser.close()` on a CDP-attached browser.** That Chrome belongs to the user and holds the Facebook login. `session.attach()` is a context manager that simply drops the connection on exit; closing would take the session with it. Login is checked via the `c_user` cookie rather than the DOM — no navigation, no selectors, no language dependency.

## Non-Interfering Operation

The user keeps working on the machine while batches run or wait. This is a hard requirement.

It is achievable because Playwright dispatches input through the DevTools protocol, **not** through OS-level mouse/keyboard, so the automation window never needs focus. Do not break that:

- **Never call `page.bring_to_front()`.**
- Attach media with `set_input_files` on the input element. Never open the native OS file-picker — it is modal and steals focus.
- Launch the debug profile off-screen (`--window-position=-32000,-32000`) rather than minimized, and disable background throttling so an unfocused window still behaves normally: `--disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding`.
- The app's own UI must not raise itself or open modal dialogs; status goes to the queue view or a passive toast.
- Headless is not an option — different fingerprint, defeats the real-session premise.
- Schedules are absolute timestamps recomputed on wake, never `sleep()` countdowns. Hold off system sleep during an active batch via `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`; a slot missed during suspend is reported, never fired late in a burst.

## Power: sleep, shutdown, and an unattended laptop

**`SetThreadExecutionState` suppresses the idle timer and nothing else.** Closing a lid, picking Sleep from the Start menu and shutting down all suspend the machine straight through it, mid-batch and all. Nothing in the app blocks shutdown, and nothing should. So the app's keep-awake is a defence against an *idle* machine dozing off, not against a user with a laptop — and a shut-down machine cannot be woken by Task Scheduler at all (wake timers reach a sleeping machine; only BIOS RTC or Wake-on-LAN reach a dead one). **Never build a "wake up to post" path.** The supported answer is `scripts/setup_always_on.ps1`: lid does nothing, never idles to sleep, app restarted at logon — **all of it mains-only**, because a laptop that refuses to sleep in a bag is a fire risk, and a missed slot is reported rather than fired late. It records every value it changes and `-Revert` puts them all back.

Three failures here were live gaps, and each one is a rule now:

- **The keep-awake is bounded by `KEEP_AWAKE_HORIZON` (30 min), not by "is a batch running".** A batch that runs out of posting window at 23:00 stays `TASK_RUNNING` with `resume_at` set to 08:00, and counting that held the machine awake for nine hours of deliberate waiting. `TaskRepo.active_batches(before)` takes the bound; 30 minutes clears the longest inter-group gap (25), so **every real gap still counts** — suspending in a gap strands the rest of the batch exactly as suspending mid-post would.
- **`ConnectionFailed` defers the batch; it does not halt it.** It is the one failure raised *before a page exists* — `session.attach()` — so nothing was typed and nothing can have been published, and the reasoning that makes every other failure terminal does not apply. Halting threw away a whole scheduled batch every time Chrome happened to be down: after a Windows Update restart, after a browser crash, in the minute between logon and Chrome finishing start-up. `TaskRepo.release_target` hands the claim back unattempted (the mirror of `claim_target`, conditional for the same reason), the batch retries every `CONNECTION_RETRY` (5 min) and gives up after `CONNECTION_GIVE_UP` (2 h). Announced **once**, not every five minutes.
- **Crash recovery distinguishes "could not look" from "looked and found nothing".** `recover()` runs seconds after a restart, which is exactly when Chrome is least likely to be up; a `ConnectionFailed` there used to mark the target failed and halt the batch on the strength of the browser being absent. It now leaves the target `running` — the cautious direction, since nothing else touches it — and `_retry_recovery` comes round again. Bounded by the same 2 hours, and for a power reason: a target left `running` keeps its task `running`, which keeps the machine awake, so waiting for ever on a browser that is never coming back would hold a laptop awake for ever too. Any *other* exception escalates immediately, unchanged — that means the check ran and came back unusable, which is the user's to look at.

`power.on_battery()` returns **three** values — True, False, and `None` for "cannot be established". A desktop, a VM and a driver that declines to answer all report 255, and telling that user to plug in a laptop they do not have is worse than saying nothing. The worker emits one `"power"` warning per batch when it is genuinely on battery, because the power plan above is mains-only by design and an unplugged laptop can be suspended with the app none the wiser. Like `check_fn` and `group_namer`, the seam is **inert by default and wired up in `App.start_worker`** — a default that read the real battery would make the suite's answer depend on whether the developer's machine was plugged in.

## Rules for the Worker (`fbposter/worker.py`)

One `PostingWorker`, one thread, started by `App.start_worker()` and by nothing else. Constructing an `App` deliberately does **not** start it, which is the only reason the GUI test suite can exist.

- **Every wait is an absolute instant, never a countdown.** The inter-group gap lives in `tasks.resume_at` and is compared against the wall clock each tick. Never replace it with `sleep()` — a countdown does not survive the app closing or the machine suspending.
- **Guards are re-checked at posting time**, not just at enqueue. The window may have closed, the cap may have filled, the cooldown may have started since. Outside the window the batch defers to `clock.next_window_open` rather than posting late.
- **Believe the `PostOutcome`.** `outcome.posted` being False (a dry run) must not be recorded as a real post — that would start a real cooldown and consume real daily cap. This was a live bug.
- **A target is claimed, not just marked.** `TaskRepo.claim_target` is a conditional `UPDATE ... WHERE state = 'pending'` and the transition itself is the lock. Reading a pending target and then marking it running as two steps left a window in which a *second copy of the app* read the same target and posted it too — racing two workers on one database produced a duplicate in **7 runs out of 40**. Never replace this with an unconditional `mark_target`.
- **Only one app may run.** `fbposter/single.py` takes a Windows named mutex in `run()`; a second launch prints a message and exits 1. This is the second layer — `claim_target` is what makes a duplicate impossible — but it stops two copies fighting over the same Chrome session and both holding the machine awake.
- **A group may hold the post for an admin, and that is a third outcome.** Groups with post approval on accept the post, close the composer, and keep it out of the feed — so `verify()` finds nothing. Guessing from that alone was wrong *both ways*: verified live against a real moderated group on 2026-08-17, the app matched something transient right after the click and recorded a confident **"done"** for a post that was not visible; a few seconds later the same check found nothing, which would have **halted** the batch. Same reality, two different wrong answers, decided by timing.
  `GroupPoster.awaiting_approval()` reads the group's "Pending admin approval" banner. No banner and no snippet still means `PostNotVerified` and a halted batch; the safe default is unchanged.
- **`verify()` succeeding does not mean the post was published.** Facebook shows the author their own *queued* post in the feed, so in a moderated group the snippet is found for a post nobody else can see — observed live in Hebrew on 2026-08-18, where the app recorded a confident "done" for a post the same page said was awaiting an admin. The cooldown and the wording happened to be right, but the target was terminal, so `_follow_up_pending` never looked at it again and an admin declining it would have locked those words to that group for ever.
  The banner cannot settle it either, in either order: it persists while *any* post of ours is queued, so checking it first mismarks a live post, and checking it only when the snippet is absent misses this case entirely. Both orderings were tried and both were wrong. So when the banner is up, `_is_queued()` asks the group's own pending list — the one page that distinguishes "your queued post, shown to you" from "published". A failed check there answers no and keeps the verdict `verify()` already reached: the post was seen, and turning that into a halted batch would be a poor trade.
  A pending target is `TARGET_AWAITING_APPROVAL`: the batch **carries on**, the cooldown starts, and the wording counts for `recent_bodies` and the daily cap. Recording it as failed would leave the repeat guard blind — and a user who re-queued the "failed" text would get two live posts the moment an admin approved both.
- **A pending post is resolved by the app, never by asking the user.** `_follow_up_pending` runs every `FOLLOW_UP_EVERY` (6h), only when something is actually awaiting, only inside the posting window — a group page opened at 4am is the same signal as a post at 4am — and leaves a post alone for `FOLLOW_UP_AFTER` (30m) first.
  `GroupPoster.pending_verdict` reads `/my_pending_content/` (verified 2026-08-17; `/pending/` is the *admin* moderation queue and is not this). Its default tab is Pending, so finding the snippet there means still waiting; otherwise `verify()` decides approved vs declined.
  **A plain decline leaves no positive trace** — "Declined with Feedback" only lists the ones an admin wrote a reason for — so declined is reached by elimination, and elimination needs care. Two things guard it: the page must contain a `MY_CONTENT_PAGE_MARKERS` string to prove it rendered at all (a half-loaded page is indistinguishable from an empty Pending tab), and it takes `MISSES_BEFORE_DECLINED` (2) *consecutive* misses, reset the moment the post turns up again. Getting this wrong releases the wording while the post is still queued, the user reposts, and both go live.
  `"unknown"` changes nothing and is retried later. A failure to check is reported and swallowed — following up must never disturb the queue, so `_follow_up_pending` is a `try/except` wrapper around `_sweep_pending` exactly like `_maybe_prune`. It runs at the top of every tick; anything it let escape would take the posting with it.
- **The sweep is bounded in three directions, and each bound is a safety rule.** It checks at most `FOLLOW_UP_PER_SWEEP` (3) groups at a time, because every check drives a real browser and a burst of page loads is the signal this whole app is built to avoid; `_follow_up_order` rotates through them on a cursor so three abandoned posts cannot starve a fourth. It stops chasing a post after `FOLLOW_UP_GIVE_UP` (30 days) — the state stays `awaiting_approval`, which is the cautious direction, and the Queue row keeps its two override buttons. And `pending_verdict` **classifies the page** before reading anything off it: a checkpoint or a rate-limit warning carries none of the page markers, so without that it would read as a polite `"unknown"`, be retried twice a day in silence, and go on opening group pages straight through a block. An `AutomationHalted` — or a browser that is not there — stops the sweep rather than moving to the next group.
- **`prune_history` never deletes a batch with a post still awaiting an admin.** The batch around it is finished, so it would otherwise age out normally — taking the Queue row, the override buttons and the follow-up's only record of it, while the post itself sat on in the group's moderation queue.
- **Never retry.** `PostNotVerified` halts the batch; it does not re-post. A duplicate is worse than a missing post. The **one** exception is `ConnectionFailed`, and only because it is raised before a page exists — see the Power section. Do not widen it: every other failure happens with a composer open, where "it may already have posted" is live.
- **`_post` catches `BaseException`, records, then re-raises.** `KeyboardInterrupt` and `SystemExit` are not `Exception`, so they used to kill the worker thread outright — leaving the target stuck in `running` and the keep-awake request still held, so the machine could not sleep.
- **Attachments are checked on disk before the browser is touched.** A file moved since the batch was queued otherwise surfaces as a Playwright timeout inside `set_input_files`, naming nothing the user can act on.
- **Crash recovery verifies, it does not guess.** A target left `running` is checked against the group with `GroupPoster.verify`: found → done, missing → requeued, uncheckable → escalated to the user.
- **A failed guard defers the batch; a cooldown skips one group.** Cap and window set `tasks.resume_at` and the whole batch waits. A group still inside its cooldown is marked `skipped` and the batch moves on, rather than stalling every remaining group behind it.
- A missed slot older than `MISSED_GRACE` (2h) is marked `missed`, never fired late in a burst. **A batch the worker deferred on purpose is exempt** — `resume_at` being set means it is waiting for the window to reopen, not that the machine was asleep, and without that exemption a 23:30 slot deferred to 08:00 came back nine hours "late" and was thrown away at the moment it was finally allowed to run.
- **Due schedules are materialised at the top of `run_once`**, before any task is claimed, and creating one counts as a step. See the Repeating posts section above.
- **`LivePoster` reattaches over CDP per group** and closes its page afterwards. Holding one connection open across a multi-hour batch would mean a Chrome restart kills the run; reattaching costs a second and survives it.
- The worker never touches a widget. It puts `WorkerEvent`s on a `queue.Queue` that the UI drains on its own thread — `App._drain_worker_events` on a `QTimer` in Qt, `App._pump_worker_events` via `after()` in Tk.

## Rules for the Automation Engine

These are safety requirements, not preferences. The user posts as an ordinary group member and account loss is the failure mode being designed against.

- **Never automate login, and never click through a checkpoint, CAPTCHA, or verification screen.** On encountering one — or any "posting too fast" warning or unexpected page — halt the entire batch and surface it to the user. No blind retries.
- **Randomized 10-25 minute delay between groups.** At 5-7 groups a batch legitimately takes 1-3 hours. This is not a performance problem to be optimized away.
- **Human-like interaction is required**, not cosmetic: randomized scroll on arrival, hover before click, per-keystroke typing delay with variance. Never paste a full text block instantly. All of it lives in `Humanizer`; `fill()` and a single `type(whole_body)` are both banned.
- **Never click the Photo/video button.** It opens the native Windows "Open" dialog, which is modal, steals focus, and was seen still sitting on screen after a run. The `input[type="file"]` is already in the composer's DOM before that button is touched, so `attach_media` writes straight to it. `PHOTO_VIDEO_BUTTONS` exists only so `probe()` can confirm the selector still resolves — do not wire it into the posting flow. That `input[type="file"]` is also the one sanctioned CSS selector in the codebase: an attribute selector on a standard element, not an obfuscated class name.
- **Enforce the daily post cap and the per-group cooldown** before enqueuing, and refuse to exceed them.
- **Verify the post actually appeared** after publishing rather than trusting that the click succeeded. Verification looks for `distinctive_snippet(body)` — the user's own words, never a Facebook string — so it does not depend on the interface language, and it reloads and scrolls before giving up because the feed is virtualised. A false negative here is expensive: it raises `PostNotVerified`, and anything that retried on that would post twice.

- **Enforce content variation.** Per Meta's Spam policy, accounts get restricted at *low* frequencies when repetitive content is present. At this volume that is the actual ban vector — not post count. Warn before byte-identical text goes to more than two groups; make per-group editing easy.

Expected volume is low by design: 2-3 posts/day to 5-7 groups each, with content that varies between runs. Tune for looking normal, never for throughput. Groups where the user is an admin should be excluded — Facebook schedules those natively with no automation needed.

## Scope Discipline

The workload is small, and the spec was trimmed accordingly. **Not in v1:** group tags ("groups of groups"), a separate `run_log` table, video upload, pause/skip mid-batch. See the Scope Control table in `README.md` for the reasoning. Do not reintroduce these without asking — each was cut on purpose. v1 is: compose → pick groups → post now or schedule once → serial worker with jitter → live queue view.

Recurring scheduling was cut from v1 on the same grounds and has since been **built at the user's request** (daily, up to three times a day; it was in their original requirements). It is the only cut that has been reversed.

## Selector Strategy

Facebook's DOM class names are obfuscated and change between builds. Use role- and `aria-label`-based selectors (`get_by_role`, `get_by_label`) and never CSS class selectors. Selectors are also language-dependent.

**English, Hebrew and Russian are all supported**, and the account has been switched between all three. Every lookup tries a list of candidates, so the order in `strings.py` implies nothing but speed. `?locale=` on the URL does **not** override the account's language setting — only the account setting matters.

Verified live in each language:

| Element | English | Hebrew | Russian |
| --- | --- | --- | --- |
| Composer trigger | `Write something...` | `כאן כותבים…` | `Напишите что-нибудь...` |
| Photo/video | `Photo/video` | `תמונה או סרטון` | `Фото/видео` |
| **Post** | `Post` | `פרסום` | **`Отправить`** |
| Pending banner | `Pending admin approval` | `בהמתנה לאישור מנהל` | **`Ожидает подтверждения администратора`** |
| "Your content" page | `Your content` | `התוכן שלך` | `Ваш контент` |

The composer trigger carries **no aria-label in any language** and is matched on visible text; the text field has no accessible name and is found as the dialog's only `textbox`.

**Read every string off the live site — never translate one.** Russian's post button is `Отправить` ("send"), while the obvious translation, and what research suggested, is `Опубликовать`. Shipping the plausible word would have failed at the Post click, which is the one step that cannot safely be retried. **The pending banner caught the same trap twice more**, verified 2026-08-18: the live Hebrew is `בהמתנה` where the natural translation gives `ממתין`, and the live Russian is `подтверждения` ("confirmation") where the natural translation gives `одобрения` ("approval") — a word that appears nowhere on the page. Two of the three shipped only because the researched list happened to include the right variant alongside the wrong one. To add a language: `main.py probe` a real group, dump the composer, paste what Facebook returns.

**Verifying a pending string is cheap only while something is pending.** The banner exists solely while the group is holding a post of yours, so it cannot be read on demand: the Hebrew round needed a real post, and the Russian round was free only because that post was still sitting in the queue. If a fourth language is ever added, read both strings *while a post is pending* rather than switching twice.

**`?locale=` is ignored outright on a logged-in session — verified live, 2026-08-16.** Probing a real group with `?locale=ru_RU`, `?locale=he_IL` and `?locale=en_US` against an English account: Facebook kept the parameter in the URL and rendered English every time. `<html lang>` stayed `en` in all three, and under `he_IL` `dir` stayed **`ltr`** — Hebrew would have forced `rtl`, so this is not a redirect or a strip, it is simply ignored. There is no account-language x URL-locale matrix; there are only three states, the account's own setting.

**The account's language is the only one that matters, and the app never asks for another.** `parse_group_url` rebuilds every URL as `https://www.facebook.com/groups/<id>/` from the identifier alone, so a pasted `?locale=` is discarded and never reaches Facebook. There is no configured or expected language anywhere in the codebase: every lookup tries the *whole* candidate list across all three at once, so whichever language the page comes back in is matched. All nine account-language x pasted-locale combinations therefore work, and they work for that reason rather than by enumeration.

Two tests keep it that way, and both guard something that fails silently: `tests/test_detect.py::TestEveryLanguageIsCovered` checks all eight language-dependent tables have a candidate in each script — **including `RATE_LIMIT_MARKERS`, where losing a language means posting on through a block** — and `test_no_language_dependent_table_is_left_unguarded` fails if `strings.py` gains a table nobody added to that list.

The anomaly markers are the deliberate exception — a rate-limit warning cannot be summoned on demand, so those are researched and marked unverified, and biased toward over-matching.

Never hardcode a UI string outside `strings.py`, and never assert on a literal in a test — reference the constants.

## Known Context

Automating posts violates Facebook's Terms of Service. The measures above reduce detection risk but do not eliminate it; this tradeoff is understood and accepted by the user, and is documented in the Known Risks section of `README.md`.
