# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Status

**All five phases are done; v1 is feature-complete.** Chrome debug-profile launcher and CDP session (1), CustomTkinter UI (2), SQLite persistence and the safety guards (3), the automation engine in `fbposter/automation/` (4), and the scheduler/worker in `fbposter/worker.py` (5).

**The app now posts on its own.** Opening the GUI starts the worker, and any due batch will go out. `README.md` holds the full spec. Per-group text editing, the Compose preview, the Qt rewrite and **repeating posts** all shipped after v1; the content-variation warning is now actionable, so it should be rare rather than constant.

## What This Is

A local, single-user Windows desktop app (Python 3.10+, CustomTkinter) that posts text and media to Facebook groups on a schedule, by driving a real logged-in Chrome session through Playwright over CDP. Everything runs on the user's machine — no server, no cloud, no automated login.

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

`probe` and `dry-run` are the tools for re-checking selectors whenever Facebook changes its markup. Reach for them before touching `poster.py`.

`status` exits 0 when logged in, 1 when not, 2 on error (Chrome not running, etc.).

There is no linter or formatter configured, and no pytest config file — the suite is the whole check. Baseline: **685 tests, ~54s**. A run that suddenly takes minutes means something is reaching the network; see the `SilentNamer` note below.

**Do not add `playwright install`.** It is unnecessary and was verified so against Chrome 150: the app attaches to the user's real Chrome over CDP and never launches Playwright's bundled Chromium, so the driver shipped inside the pip package is all that is required.

## Critical Constraint: Chrome Debug Profile

Chrome 136+ **refuses `--remote-debugging-port` when using the default user profile.** The app therefore cannot attach to the user's everyday Chrome window, and any code or docs assuming it can are wrong. A dedicated profile directory is required:

```
chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\FBAutomation\ChromeProfile"
```

The user logs into Facebook manually inside that profile, once. Playwright then attaches with `connect_over_cdp` — it never launches its own browser context and never logs in.

## Architecture

Three layers that must stay separate:

- **UI (main thread)** — CustomTkinter mainloop. Never touches Playwright objects or the database directly during long operations.
- **Worker (exactly one background thread)** — owns the Playwright sync API and processes the task queue strictly serially. One group at a time, globally. There is never a second worker, and batches never overlap; a "Post Now" issued mid-batch is appended to the queue rather than run concurrently.
- **SQLite** — the source of truth for queue state, not just persistence. Each group's outcome is committed as it completes so a crash or restart resumes the batch instead of re-posting. Duplicate posts are the single worst failure mode here (strong spam signal), so idempotency belongs in the schema, not in memory.

UI and worker communicate through a thread-safe queue. The tables as built are `groups`, `templates`, `tasks`, `task_targets`, `schedules`, `schedule_targets` and `settings`; `tags`, `group_tags` and `run_log` were cut (README §9 and §10) — do not write code expecting them.

Core: `config.py` (paths, port, Chrome flags), `chrome.py` (find/launch Chrome, probe the debug port), `session.py` (CDP attach, `c_user` cookie check), `strings.py` (every Facebook URL and UI string, in all three languages), `clock.py` (Israel-time judgement), `power.py` (`SleepBlocker`), `guards.py` (the safety rules as pure functions), `recurrence.py` (repeating-schedule rules, also pure), `groups.py` (group-URL parsing), `errors.py`, `worker.py` (`PostingWorker` and `LivePoster`).

Automation: `automation/poster.py` (`GroupPoster` — arrive → compose → type → attach → publish → verify, plus the read-only `probe`), `detect.py` (`classify` a page as OK / checkpoint / login / rate-limit / unavailable), `humanize.py` (`Humanizer`: keystroke timing, hovers, arrival scroll, the inter-group gap), `groupinfo.py` (read a group's display name off its `h1`; cosmetic, and must never raise into a caller).

UI (Qt, current): `qtui/app.py` (window, sidebar, connection pill, worker row, worker-event pump, background thread helper), `qtui/views/` (compose, groups, publish, queue — the nav order is the flow), `qtui/theme.py` (palette + one stylesheet), `qtui/widgets.py`. It reuses `ui/connection.py` and every non-UI module unchanged.

UI (Tk, legacy — `main.py gui --tk`): `ui/app.py`, `ui/views/`, `theme.py`, `toast.py`, `background.py`, `connection.py`, `preview.py`, `textdir.py`.

Storage: `db/` — `connection.py` (per-thread connections), `schema.py` (migrations), `models.py`, `repo.py` (`GroupRepo`, `TemplateRepo`, `TaskRepo`, `ScheduleRepo`, `SettingsRepo`).

### Time: stored in UTC, judged in Israel time

Every timestamp is stored and compared in UTC, but **every human judgement is made in `Asia/Jerusalem`** — the posting window, what counts as "today" for the daily cap, and every time shown in or typed into the UI. Go through `fbposter/clock.py` (`to_local`, `local_hour`, `start_of_local_day`, `next_window_open`, `parse_local`, `format_local`); never read `.hour` or `.date()` off a stored UTC value. Doing that put the window three hours out — it allowed a 01:00 local post and refused a 09:00 one, and posting at 4am is the loudest automation signal there is.

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

### UI rules that are easy to break

These describe the **legacy Tk UI**. The ideas — one thread touching widgets, no modal status dialogs, committed-state reads — carry over to Qt; the widget specifics do not.

- **Only the main thread touches widgets.** Blocking work goes through `BackgroundRunner` in `fbposter/ui/background.py` — worker thread → `queue.Queue` → `widget.after()` pump. The Phase 5 posting worker reports progress the same way.
- **No modal dialogs for status, ever** — use `app.toast`. The single permitted OS dialog is the app's own media file picker in Compose, because the user asked for it and it can only fire while they are sitting there choosing images. Chrome's native file dialog is a different thing entirely and is never acceptable — see the Photo/video rule below.
- **Compose owns per-group wording, and `body_for()` is the only way to read it.** `_base_body` is the shared text, `_bodies` holds per-group rewrites, `_editing` is the active tab. `body_for()` reads committed state only, so `capture()` must run first — it once returned the live editor contents when that group was active, which handed back the wrong text as soon as `_editing` was assigned before the read. Editing the base clears the rewrites (the user's choice) and toasts, and only when the text genuinely changed — a tab switch must never cost someone their wording.
- **Rebuilding a `CTkScrollableFrame`'s children is expensive.** The Compose tab strip skips the rebuild when nothing visible changed; doing it unconditionally on every refresh was measurable across the suite.
- **The Compose preview reads committed state, never the editor.** `Write | Preview` swaps `self.editor` and `self.preview` in and out of one slot; `sync_mode()` captures first, and `refresh_preview()` renders `body_for(active tab)`. It is a no-op in Write mode, which is why `refresh_tabs()` can call it unconditionally. A preview showing another group's words would be worse than no preview. `fbposter/ui/preview.py` owns the drawing and must never raise on a bad attachment — a missing or corrupt image draws a named tile, because the file is still going to be uploaded.
- **Tk 8.6 has no bidi support at all, and this is the single most misdiagnosed thing in the app.** It lays characters out in logical order, left to right, always; `Text.bbox()` proves it, and no tag, `justify`, RLM, RLE or RLI changes a single x-coordinate. What makes Hebrew look right is Windows reordering each *run* it draws — one unbroken stretch of one script. So a pure-Hebrew line (one run) renders correctly, while any line mixing Hebrew with English or digits comes back as a **mirror image** of the truth: in "…אופניים של kalofan והמחיר… 1000" the app puts `1000` on the right where Facebook puts it on the left. Measure with `bbox()` and split screenshots into halves before believing anything here; reading glyph order off a screenshot got this wrong twice.
- **The editor is fixed with one invisible character, not by reordering.** Windows honours directional formatting at draw time, so a `textdir.RLE_MARK` (U+202B) at the start of a right-to-left line gives that line a right-to-left base and it renders correctly — English and digits included, and across wrapped lines. Pixel-identical to the reordered reference; RLM, RLI and FSI all do nothing, only RLE works. Do **not** try to fix the editor by reordering its text: displaying something other than what is stored breaks the caret and selection.
- **The mark lives in the widget and must never reach the post.** `ComposeView.get_text()` calls `textdir.strip_controls()` and is the only thing that ever reads the box — keep it that way, and keep the character counter and every guard downstream of it. `tests/test_ui.py` asserts the queued `task_targets.body` and saved templates are free of every character in `textdir.BIDI_CONTROLS`.
- **The preview is reordered, because it is the only place that safely can be.** `textdir.to_visual()` reorders one display line with `python-bidi` and wraps it in LRO…PDF, which stops Windows reordering it a second time — without the override the work is silently undone. `preview.py` wraps the text by hand *in logical order first* (`wrap_to_width`) and reorders each resulting line, never the other way round. `text_shown()` returns the logical post, not what the labels hold.
- **Direction is per line, and a line with no strong character inherits from the one above.** Both halves were learned the hard way. One direction for the whole box dragged the English half of a bilingual post to the right-hand edge and was reported as tangled; without inheritance, a `054-1234567` line between two Hebrew ones flies off to the left. `line_directions()` owns both rules.
- **Retagging the textbox relayouts the whole widget (~3.6ms), so `_apply_direction` caches what it applied** and touches Tk only when a line genuinely changes direction. Doing it on every keystroke was reported as typing lag. Measured after the fix: the direction code costs ~0.1ms against Tk's own ~4ms per keystroke on a long post. `tests/test_ui.py` guards the caching.
- **Pillow and `python-bidi` are optional at runtime, required in `requirements.txt`.** Without Pillow every image falls back to a tile; without `python-bidi` the preview shows the same mirrored text the editor does. Neither may be allowed to raise. Nothing outside `preview.py` reads Pillow, and nothing outside `textdir.to_visual()` reads `python-bidi`.
- **`CTkButton` is 140px wide unless told otherwise.** About two Compose tabs fit before the strip starts scrolling; `width=10` makes a button shrink to its text instead.
- **Colours are `(light, dark)` tuples in `theme.py`.** Never hardcode a hex value in a widget; a colour defined for only one mode is invisible in the other.
- **`CTkFrame` defaults to 200x200.** Any frame used as a thin divider, spine or spacer must pass explicit dimensions, or it silently stretches its row (216px queue rows) or draws as a stray 200px line (an "invisible" spacer frame). Both bugs happened; `tests/test_ui.py` guards both. Use `pady` for spacing rather than an empty frame.
- **The expanding widget in a view must be packed last**, after the fixed controls are anchored with `side="bottom"`. Otherwise it claims the frame and pushes them off-window.
- **Anything in a view that reaches for a browser must be injectable, and the shared test App must be given a stub.** The Groups view looks up group names on its own whenever it is shown, and `chrome.probe()` succeeds on any machine with Chrome running — so before `SilentNamer` existed, the GUI suite silently opened real Facebook pages and took nearly three minutes instead of twenty seconds. `App` takes `check_fn=`, `db=` and `group_namer=` for this reason.
- GUI tests share **one** Tk interpreter for the whole session (`ui_app` in `tests/conftest.py`). Creating a second root, or recreating one after a destroy, fails intermittently on Windows with "Can't find a usable init.tcl". Never call `mainloop()` in a test — pump with `pump_until`.

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

## Rules for the Worker (`fbposter/worker.py`)

One `PostingWorker`, one thread, started by `App.start_worker()` and by nothing else. Constructing an `App` deliberately does **not** start it, which is the only reason the GUI test suite can exist.

- **Every wait is an absolute instant, never a countdown.** The inter-group gap lives in `tasks.resume_at` and is compared against the wall clock each tick. Never replace it with `sleep()` — a countdown does not survive the app closing or the machine suspending.
- **Guards are re-checked at posting time**, not just at enqueue. The window may have closed, the cap may have filled, the cooldown may have started since. Outside the window the batch defers to `clock.next_window_open` rather than posting late.
- **Believe the `PostOutcome`.** `outcome.posted` being False (a dry run) must not be recorded as a real post — that would start a real cooldown and consume real daily cap. This was a live bug.
- **Never retry.** `PostNotVerified` halts the batch; it does not re-post. A duplicate is worse than a missing post.
- **Crash recovery verifies, it does not guess.** A target left `running` is checked against the group with `GroupPoster.verify`: found → done, missing → requeued, uncheckable → escalated to the user.
- **A failed guard defers the batch; a cooldown skips one group.** Cap and window set `tasks.resume_at` and the whole batch waits. A group still inside its cooldown is marked `skipped` and the batch moves on, rather than stalling every remaining group behind it.
- A missed slot older than `MISSED_GRACE` (2h) is marked `missed`, never fired late in a burst. **A batch the worker deferred on purpose is exempt** — `resume_at` being set means it is waiting for the window to reopen, not that the machine was asleep, and without that exemption a 23:30 slot deferred to 08:00 came back nine hours "late" and was thrown away at the moment it was finally allowed to run.
- **Due schedules are materialised at the top of `run_once`**, before any task is claimed, and creating one counts as a step. See the Repeating posts section above.
- **`LivePoster` reattaches over CDP per group** and closes its page afterwards. Holding one connection open across a multi-hour batch would mean a Chrome restart kills the run; reattaching costs a second and survives it.
- The worker never touches a widget. It puts `WorkerEvent`s on a `queue.Queue` that `App._pump_worker_events` drains via `after()`.

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

The composer trigger carries **no aria-label in any language** and is matched on visible text; the text field has no accessible name and is found as the dialog's only `textbox`.

**Read every string off the live site — never translate one.** Russian's post button is `Отправить` ("send"), while the obvious translation, and what research suggested, is `Опубликовать`. Shipping the plausible word would have failed at the Post click, which is the one step that cannot safely be retried. To add a language: `main.py probe` a real group, dump the composer, paste what Facebook returns.

The anomaly markers are the deliberate exception — a rate-limit warning cannot be summoned on demand, so those are researched and marked unverified, and biased toward over-matching.

Never hardcode a UI string outside `strings.py`, and never assert on a literal in a test — reference the constants.

## Known Context

Automating posts violates Facebook's Terms of Service. The measures above reduce detection risk but do not eliminate it; this tradeoff is understood and accepted by the user, and is documented in the Known Risks section of `README.md`.
