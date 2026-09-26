# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Where to look

Most of this file is a rule that already cost a live bug. They are grouped by what you are touching:

| Touching | Read |
| --- | --- |
| anything | Repository Status, Architecture, Scope Discipline |
| `worker.py`, scheduling | Rules for the Worker; Power; Repeating posts; Time |
| `automation/` | Rules for the Automation Engine; Selector Strategy; Non-Interfering Operation |
| `qtui/` | `fbposter/qtui/CLAUDE.md` — loads on its own when you open a file there, but not from `tests/`: open it before editing a `test_qtui_*` file |
| `db/`, retention | Database rules; the two retentions; Queue retention |
| `ui/` (legacy Tk) | `fbposter/ui/CLAUDE.md` — loads on its own when you open a file there |
| tests | How the tests avoid a browser and a real clock |
| `onboarding.py`, `login.py`, the wizard | Handing this to a non-technical user; Changing which Facebook account it posts as |
| `packaging/` | `packaging/CLAUDE.md` — loads on its own when you open a file there |

Text that reaches a post also passes Invisible characters and Hebrew, whatever screen it came from.

## Repository Status

**All five phases are done; v1 is feature-complete.** Chrome debug-profile launcher and CDP session (1), CustomTkinter UI (2), SQLite persistence and the safety guards (3), the automation engine in `fbposter/automation/` (4), and the scheduler/worker in `fbposter/worker.py` (5).

**The app now posts on its own.** Opening the GUI starts the worker, and any due batch will go out. `README.md` holds the full spec. Per-group text editing, the Compose preview, the Qt rewrite and **repeating posts** all shipped after v1; the content-variation warning is now actionable, so it should be rare rather than constant. Since then: a post a group holds for an admin is tracked as its own outcome and resolved by the app itself (`TARGET_AWAITING_APPROVAL` and `_follow_up_pending`, see the Worker rules), a dropped Chrome connection defers a batch instead of throwing it away, and `scripts/setup_always_on.ps1` covers the laptop that has to post with its lid shut (see Power). Most recently, two things that were silent are not: removing a group archives it rather than deleting its posting history out from under the repeat guard, and closing the window warns when doing so would strand a queued batch or an active schedule.

**It is now shipped, not just run.** The app is packaged as an installer for a non-technical client: a first-run wizard replaces the terminal commands the app used to print, and `packaging/` builds a signed-nothing-but-working `Setup.exe`. The wizard also owns the one thing a finished setup might still need changing — which Facebook account it posts as. See "Handing this to a non-technical user".

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

```powershell
# The client deliverable. Runs the suite, then PyInstaller, then Inno Setup;
# refuses to build a release from a red suite. Output: dist\FacebookAutoPoster-Setup-*.exe
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Clean
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -SkipInstaller  # .exe folder only
```

`probe` and `dry-run` are the tools for re-checking selectors whenever Facebook changes its markup. Reach for them before touching `poster.py`.

`status` exits 0 when logged in, 1 when not, 2 on error (Chrome not running, etc.).

There is no linter or formatter configured, and no pytest config file — the suite is the whole check. Baseline: **1417 tests, 75-170s** — the spread is machine load, not the suite; the Qt and Tk GUI files are ~80s of it on their own. A run of *five minutes or more* means something is reaching the network; see the `SilentNamer` note below.

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

Core: `config.py` (paths, port, Chrome flags), `chrome.py` (find/launch Chrome, probe the debug port), `session.py` (CDP attach, `c_user` cookie check), `strings.py` (every Facebook URL and UI string, in all three languages), `clock.py` (Israel-time judgement), `power.py` (`SleepBlocker`, `on_battery`), `guards.py` (the safety rules as pure functions), `recurrence.py` (repeating-schedule rules, also pure), `groups.py` (group-URL parsing), `text.py` (the invisible-character list), `single.py` (the one-app mutex), `onboarding.py` (what setup step the user is on, pure), `login.py` (putting a visible Chrome in front of them), `keepalive.py` (when to restart a Chrome that has closed, pure), `errors.py`, `worker.py` (`PostingWorker` and `LivePoster`).

Scripts: `scripts/setup_always_on.ps1` — the laptop power plan and the logon task. See the Power section.

Packaging: `packaging/` — `fbposter.spec` (PyInstaller), `installer.iss` (Inno Setup), `build.ps1` (both, plus the suite), `entry.py`, `SETUP.md` (the client's one-pager). Its rules are in `packaging/CLAUDE.md`.

Automation: `automation/poster.py` (`GroupPoster` — arrive → compose → type → attach → publish → verify, plus the read-only `probe`), `detect.py` (`classify` a page as OK / checkpoint / login / rate-limit / unavailable), `humanize.py` (`Humanizer`: keystroke timing, hovers, arrival scroll, the inter-group gap), `groupinfo.py` (read a group's display name off the `h1` **inside `[role="main"]`**; cosmetic, and must never raise into a caller).

UI (Qt, current): `qtui/app.py` (window, sidebar, connection pill, worker row, worker-event pump, background thread helper), `qtui/views/` (compose, groups, publish, queue — the nav order is the flow; plus `welcome`, the first-run wizard, which is deliberately not in the sidebar), `qtui/theme.py` (palette + one stylesheet), `qtui/widgets.py` (`card`, `row`, `clear`), `qtui/assets/`. It reuses `ui/connection.py` and every non-UI module unchanged.

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

### "Post anyway": the one way past a rule, and it is recorded

The rules used to be absolute. The user asked for an override, and there is exactly one. When a batch or a repeating post breaks a rule, Publish shows what it breaks and offers **Post anyway** in place of the post button. Nothing is posted until that second click.

- **Only these rules can be broken:** `guards.OVERRIDABLE`, meaning the cooldown, repeated text, posting hours and the daily limit. The 10-25 minute spacing is pacing, not a refusal, and still applies. Halting on a checkpoint, never retrying, and one worker at a time are not rules the user bends. Anything outside `OVERRIDABLE` (no groups, no text) is refused as it always was.
- **The choice lives on the row.** `tasks.overrides` and `schedules.overrides` (migration 009) are JSON lists of rule names. It has to be persisted: the worker re-checks every rule at the moment of posting, so without it the worker would skip the very post the user had insisted on. `_fire_schedule` copies a schedule's overrides onto every batch it fires, and `pick_body(allow_repeats=True)` keeps rotating once every wording has been used.
- **Exactly the rules shown, never "all rules".** A batch allowed past the cooldown still waits for posting hours. `PublishView.post_anyway()` re-judges rather than trusting the list: if something new is now broken, the panel comes back naming it.
- **Repeat is judged on real gaps, not averages.** `recurrence.check_schedule` is the same set of rules the worker applies when the schedule fires. 09:00 and 12:00 average twelve hours apart, yet the 12:00 run is skipped every day. `interval_violation` walks the actual occurrences, across midnight and across chosen days. Three runs a day inside a 08:00-23:00 window can never all be 8h apart, so they always need "Post anyway".
- **It is visible afterwards.** The Queue card and the repeating-post card both say which rules the batch was allowed to break, so a post at 23:40 is never a mystery.
- **A refusal states a fact, never an outcome.** The panel once listed "...so the 11:00 run would be skipped" directly above the button that posts it. Messages in `guards.py` and `recurrence.py` say what is true ("only 2h apart, less than the 8h cooldown"); what each button does is said once, by the panel (`publish.ANYWAY_EFFECT`). `tests/test_wording.py` rejects "skip", "would", "will" and "reword" in any refusal, and `test_onboarding.py` now scans every string in the package for terminal commands, after one reached the user from the scheduler.
- **The cooldown is no longer per group in the UI.** There is one rule, the default gap, and migration 009 put every group on it. A value set earlier would otherwise have gone on applying where nobody could see it. `groups.cooldown_hours` still exists and the guards still read it; nothing edits it any more. The legacy Tk window still has its old control.

### Playwright is imported lazily, and must stay that way

**Startup cost was Playwright, and it is now deferred.** This reverses an earlier decision recorded here, on a re-measurement: `playwright.sync_api` costs **~830ms**, not the ~340ms first measured, and importing `fbposter.qtui.app` went from **1670ms to 682ms** without it. The earlier note also had the mechanism wrong — neither `worker.py` nor `automation/groupinfo.py` imports Playwright; **`session.py` does**, and both pull it in through that, so one lazy import fixed both rather than needing two.

It is imported inside `session.attach()`. The objection recorded last time — that this moves the cost onto the first background name lookup or the first post — is exactly why it is safe: both of those already run **off the thread drawing the window**, and the first post has a ten-to-twenty-five-minute gap in front of it. A second on the worker thread is invisible; a second before the window appears is not. `tests/test_qtui_performance.py` pins both halves — that importing the window does not pull Playwright in, and that nothing in the package imports it at module scope, since the cost comes back silently wherever that is written.

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

### Removing a group archives it; it has never been safe to delete one

`GroupRepo.remove` sets `archived = 1`. It used to be a `DELETE`, and
`task_targets.group_id` is `ON DELETE CASCADE` — so removing a group destroyed
every post ever made to it, which is exactly the window `GroupRepo.recent_bodies`
reads for `guards.check_repeat_text`. Remove a group and add it back, which is
the obvious thing to do to correct a name, and the guard went blank: the advert
that group had already been sent could go to it again. Two clicks in the UI, no
warning, and the app's main protection against a restriction silently off.

- **`list()` already excludes archived groups**, so every screen needed no
  change; `get()` deliberately still finds them, because the Queue has to name a
  group it posted to last week.
- **Anything deciding whether to *post* must call `GroupRepo.active()`**, which
  returns `None` for an archived group — that is what `get()` returning `None`
  used to mean. `worker._attempt`, `worker._fire_schedule` and
  `worker._sweep_pending` all do. Reaching for `get()` in one of those posts to,
  or opens a page for, a group the user removed.
- **The follow-up sweep drops a removed group rather than chasing it**, since
  every check is a real page load and the answer can no longer be acted on. The
  target row stays `awaiting_approval` — which already counts towards
  `recent_bodies`, so that wording goes on being refused to that group whether
  or not anyone ever learns what the admin did with it.
- **`add_from_url` un-archives**, so pasting the link again is the undo, and it
  brings the history back with it. The Groups screen says so, because the user
  is otherwise about to wonder why the repeat guard already knows this group.
- **A schedule keeps the archived group's id** rather than losing the row to a
  cascade, so re-adding resumes it unchanged. A schedule left with *no* usable
  groups is paused and reported, like one with an unusable repeat rule —
  otherwise it comes round two or three times a day for ever, posts nothing and
  says nothing.
- A batch queued before the removal marks that target failed at posting time
  ("Group was removed.") and carries on to the rest, which is what it did when
  the row vanished.

### Database rules

- **`Database.transaction()`, never `with connection:`.** Connections use `isolation_level=None` (autocommit), so `with connection:` commits a transaction that was never begun and a later failure leaves earlier statements written. This already produced an orphan task row once; `tests/test_db.py` guards it.
- **That rule applies to `apply_migrations` too, and it was the one place still breaking it.** A migration that failed part way left its first statement written with `user_version` unmoved, so the next launch re-ran it and stopped on `duplicate column name` — the client's app simply would not open. Each migration and its version bump are now one explicit `BEGIN`/`COMMIT`/`ROLLBACK`. **Never call `executescript` inside one**: it commits whatever transaction is already open before it runs a line, so a script migration wrapped in a transaction is not atomic at all. `_run_script` splits on `sqlite3.complete_statement` and executes statement by statement, which keeps every one of them inside the `BEGIN`.
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

The Tk widget specifics — `CTkFrame`/`CTkButton` defaults, pack order, and the whole `textdir.py` bidi apparatus — now live in **`fbposter/ui/CLAUDE.md`**, which loads on its own when you open a file in `fbposter/ui/`. None of it applies to `qtui/`, whose own rules — the flow, redrawing, the Compose preview, the visual rules — are in **`fbposter/qtui/CLAUDE.md`**. What follows holds in both windows.

- **Only the main thread touches widgets.** Blocking work goes through a background thread → `queue.Queue` → a pump on the UI thread: `BackgroundRunner` and `widget.after()` in Tk, `App.run_in_background` and a `QTimer` driving `App._drain_worker_events` in Qt. The posting worker reports progress the same way and never touches a widget itself.
- **No modal dialogs for status, ever** — use `app.toast`. There are exactly three permitted, and all three share one justification: they can only appear because the user just acted, so the app already has focus and they cannot interrupt anything. One is the media file picker in Compose. The second is `qtui.app.ask_before_closing`, raised only from `closeEvent`, only when `App.unfinished_work()` finds something still due — see the close rule below. The third is the single-instance refusal in `run()`, which exists because the packaged `.exe` has no console for the `print` it used to be. Chrome's native file dialog is a different thing entirely and is never acceptable — see the Photo/video rule below.
- **Closing the window stops the posting, so it says so first.** The worker is the window's own thread; `closeEvent` stopped it silently, so a daily repeat set up and then closed away simply never ran again with nothing on screen to show it. `App.unfinished_work()` returns a phrase naming what is still due — unfinished batches, active schedules — or `None`, and only a non-`None` answer costs the user a dialog. **The confirmation is injectable (`App(confirm_close=)`) and defaults to the real one**, exactly like `check_fn` and `group_namer`: the GUI suite closes every window it builds, so a real modal would hang the run rather than fail it. It is also skipped entirely while `self.worker is None`, which is every window a test builds.
- **Compose owns per-group wording, and `body_for()` is the only way to read it.** `_base_body` is the shared text, `_bodies` holds per-group rewrites, `_editing` is the active tab. `body_for()` reads committed state only, so `capture()` must run first — it once returned the live editor contents when that group was active, which handed back the wrong text as soon as `_editing` was assigned before the read. Editing the base clears the rewrites (the user's choice) and toasts, and only when the text genuinely changed — a tab switch must never cost someone their wording.
- **Anything in a view that reaches for a browser must be injectable, and the shared test App must be given a stub.** The Groups view looks up group names on its own whenever it is shown, and `chrome.probe()` succeeds on any machine with Chrome running — so before `SilentNamer` existed, the GUI suite silently opened real Facebook pages and took nearly three minutes instead of twenty seconds. Both `App`s take `check_fn=`, `db=` and `group_namer=` for this reason.

### How the tests avoid a browser and a real clock

Nothing in the suite opens Chrome, hits Facebook, or waits out a real delay. Keep it that way — every seam already exists:

- **`tests/fake_page.py`** stands in for a Playwright `Page`, recording every call into `page.calls`. It implements only the surface `GroupPoster` actually uses, so a poster that starts calling something new fails loudly instead of quietly passing. Its knobs (`missing`, `redirect_to`, `body_text`, `wait_fails_for`, `never_detaches`) are how the halt paths, the slow-publish path and the dry-run boundary get exercised.
- **`Humanizer(rng=, sleep=)`** — pass a seeded `Random` and a no-op sleep and the human pacing is deterministic and instant.
- **`PostingWorker(poster=, now=, sleep=, blocker=, tick_seconds=)`** — the whole loop, including the inter-group gap and crash recovery, runs without a thread, a browser or the wall clock.
- **`App(check_fn=, db=, group_namer=)`** — a temporary database and `SilentNamer`. Constructing an `App` deliberately does not start the worker. Both the Tk and the Qt window take the same three seams.
- **Qt tests run offscreen.** `tests/conftest.py` has a session-scoped `qt_application` (one `QApplication`, `QT_QPA_PLATFORM=offscreen`) and a per-test `qt_app` window on a temporary database. Offscreen is not tidiness: this app's central promise is that it never takes focus, and a suite that popped real windows would break that on the developer's own machine every time it ran. Drive views through their own methods rather than synthesised clicks. Note that `deleteLater()` widgets keep painting until the event loop turns, so anything that reads pixels needs a real loop turn first — two "duplicate row" and "giant blue rectangle" scares came from screenshotting without one.

**`QPixmap` cannot be constructed before a `QApplication` exists** — Qt aborts the process (`STATUS_STACK_BUFFER_OVERRUN`), so pytest reports nothing at all rather than a failure. Any test touching `QPixmap`, `cover()` or `avatar()` must depend on the `qt_application` fixture even if it never builds a widget.

**Never call `browser.close()` on a CDP-attached browser.** That Chrome belongs to the user and holds the Facebook login. `session.attach()` is a context manager that simply drops the connection on exit; closing would take the session with it. Login is checked via the `c_user` cookie rather than the DOM — no navigation, no selectors, no language dependency.

### Handing this to a non-technical user

The app ships to a client as `dist\FacebookAutoPoster-Setup-x.y.z.exe`. How that is built, and the five things a frozen build loses without a word, are in **`packaging/CLAUDE.md`**. What stays here is the app side — and the one packaging fact that shapes app code: **the packaged app has no console, so `print()` reaches nobody.** Anything the user must see goes through the window.

**The app used to answer "what now?" with terminal commands.** `ui/connection.py` said *"Start it with 'main.py launch'"* and `automation/detect.py` said *"Run 'main.py setup' and sign in again"* — both of which reach the user through the connection pill. They are good developer instructions and useless to somebody holding an `.exe` with no console behind it. `tests/test_onboarding.py::TestNothingTellsTheUserToOpenATerminal` greps the wizard copy, the halt messages and the connection details so a third one cannot be written.

- **`onboarding.py` is pure and `login.py` drives the browser** — the same split `guards.py` has against `worker.py`. What the app decides to tell the user next is worth testing without Chrome, a profile directory or a Facebook session, and it is: 26 tests, half a second.
- **One problem at a time.** `plan()` returns a single `SetupStep`, never a list. A screen reporting four problems at once is four times as intimidating and no more useful, because they have to be fixed in order anyway — there is no point mentioning Facebook when Chrome is not installed.
- **The wizard is a view, not a dialog.** "No modal dialogs for status, ever" still holds. It is in `App.views` but deliberately **not** in `nav_buttons`: setup is something you finish, not a step you return to. The two ways back in are the first launch and the repair button under the pill.
- **`SETUP_COMPLETE_KEY` is a stored fact, not a live check.** The window has to decide where to open *before* it is on screen, and `chrome.probe()` is up to a full second — a second of grey nothing, which reads as a crash. So the wizard shows until a connection check has come back `CONNECTED` **once**, and only `CONNECTED` retires it.
- **Chrome is started after the window is up, never before it.** `App.begin_startup_checks()` is fired by a `QTimer` from `run()`, like `start_worker` and for the same reason — a window a test builds must not launch a browser. `chrome.launch` waits on the debug port for up to `LAUNCH_TIMEOUT_S` (**30 seconds**); in front of the window that is half a minute of nothing.

**The startup check gets exactly one second chance.** `STARTUP_RECHECK_MS` (15s), armed by `begin_startup_checks` and spent by the first result. The reason is the logon task: it starts the app 45 seconds after sign-in, while Windows is still bringing the network up, and the check ends in a real page load — so it can fail for a reason that fixes itself a moment later, leaving a red pill on a machine nobody is sitting at. **One retry, never a loop**: an offline machine must not reopen Facebook every fifteen seconds for ever, and nothing in this app may open Facebook on a timer. Pressing "Check connection" by hand never arms it.

**A background Chrome that closes is started again, by the window, without a loop.** Nothing used to notice Chrome going away while the app stayed open. The user closing it from the taskbar, a crash, or Windows Update left the pill on its last word, and every post waiting on a connection that was never coming back. Now `App.watch_chrome` runs every `keepalive.WATCH_EVERY_S` (20s), armed by `begin_startup_checks` and stopped in `closeEvent`. It asks the debugging port only, which is ~14ms and **never a Facebook page**. If Chrome is gone, the window restarts it off-screen and says so.

- **One connection check per restart, never per tick.** A check is a real page load. It runs after a restart, and when Chrome is seen back while the pill still says it is down. Never on the timer itself.
- **`chrome.launch` holds a lock for the whole launch.** The watcher, the startup check and the wizard's button can all ask at once. Without the lock each saw the port closed and each started a Chrome, and the second just opens another window in the first.
- **A silent port is not a dead Chrome.** Chrome holds `<profile>/lockfile` open, delete-on-close, for as long as it runs. `chrome.profile_in_use` reads that, and the watcher treats "port silent, profile held" as *busy*: it waits, and only warns after `BUSY_LOOKS_BEFORE_TELLING` looks. `chrome.launch` refuses to start a second Chrome on a held profile and gives it `BUSY_GRACE_S` to answer instead. Launching on a profile a busy Chrome still holds hands the launch to it, and Chrome may shut the busy one down to take over. The app's Chrome was once replaced outright at a moment of heavy load, and mid-post that loses the post. The lockfile behaviour was verified live on 2026-09-25.
- **A failed restart backs off, and gives up after `MAX_ATTEMPTS`.** The usual cause is a Chrome already open on the profile *without* the port, so every launch opens yet another window in it. Retrying every 20s would pile them up for ever. The keeper waits 1 minute, then 5, then stops and points the user at Start Chrome. Chrome being seen alive resets the count, whoever started it.
- **A launch that finds Chrome already up means someone else started it, off-screen.** `open_login_window` and `switch_account` check `is_running()` and then launch visibly, but the keep-alive can start Chrome between the two. `launch()` then starts nothing and returns False, and they now *move* the window on screen instead of assuming theirs is the visible one. Before this, the login form opened in an off-screen window.
- **The seams are looked up at call time** (`lambda: login.start_chrome()`), not captured in `__init__`. Tests patch `login.start_chrome` after the window exists; a captured reference ignored the patch, and on a machine without Chrome up it would have launched a real one.

**Re-login moves the window; it does not restart Chrome.** This is the part worth reading before changing it. The normal state of this app is a Chrome parked at `-32000,-32000`, so a session that expires later leaves a login form somewhere nobody can reach. Restarting Chrome is the obvious fix and the wrong one: there is no dependable way to close a window the user cannot see, and a restart mid-batch strands it. `login.open_login_window()` sends CDP `Browser.setWindowBounds` instead, and `hide_login_window()` puts it back.

**Both halves of that were verified live against Chrome 151 on 2026-08-21**, because both were assumptions and either one failing leaves the client staring at an empty browser: a page created through `context.new_page()` **survives dropping the CDP connection** (the tab is a real Chrome target and Playwright does not own it), and `Browser.setWindowBounds` moves the window on screen and back off it in both directions. The check used `example.com`, never Facebook, and closed the tab it made. **Moving a window is not `bring_to_front()`** — the banned call raises a page above whatever the user is working in at a moment they did not ask for; this runs only because they just pressed a button that says a Chrome window will open.

### Changing which Facebook account it posts as

`login.switch_account()` drops the profile's cookies and reopens Facebook. That
is the entire mechanism, and it is enough because **nothing in the database is
tied to an account** — the login cookie is only ever read as a yes/no and never
stored — so the groups, templates, repeating posts and the whole posting history
survive the switch untouched. Before this existed, the only way to do it was
renaming `ChromeProfile\` by hand.

- **Cookies, never Facebook's own Log out menu.** Driving that menu would be a
  language-dependent click on obfuscated markup, in the account menu of all
  places, where a mis-resolved selector could press something else entirely. It
  is also the only approach that leaves nothing for the next account to inherit:
  the "recently logged in" chooser is a cookie too.
- **Cleared before the navigation, never after.** Cleared afterwards, the page in
  front of the user is still the old account's feed, and the login form appears
  only if they think to reload it themselves.
- **Nothing is half-done.** A window that will not come on screen signs nobody
  out, and a sign-out that fails never navigates — landing on the old account's
  feed after being told you were signed out is how somebody posts as the wrong
  person. `_sign_out` therefore raises rather than swallowing, unlike almost
  everything else in `login.py`.
- **The pill's button offers it whenever the connection is fine**, because the
  wizard is otherwise unreachable: it retires itself the first time a check comes
  back `CONNECTED`. The button still only navigates — the wizard owns the action
  and the confirmation, which matters most for the one that destroys something.
- **Two presses, and the second is not a dialog.** The confirmation replaces the
  card rather than opening over it, so the permitted-modal list stays at three.
  The button sits beside the connection light on every screen, and one press away
  from signing out is too close to "Check connection".
- **Refused outright while the worker is mid-post.** Dropping the cookies with a
  post in the composer fails that post, and the batch then halts on a
  verification that could never have succeeded. It is a wait, not a refusal.
- **The app records the sign-out itself** (`App.note_connection`) instead of
  waiting for a check to come back and say so. That is not cosmetic: until the
  app knows, the wizard still believes it is `READY`, and the `READY` branch of
  `WelcomeView.refresh()` parks the very login window the user is about to type
  into. `_switching` exists for exactly that window of time.

## Non-Interfering Operation

The user keeps working on the machine while batches run or wait. This is a hard requirement.

It is achievable because Playwright dispatches input through the DevTools protocol, **not** through OS-level mouse/keyboard, so the automation window never needs focus. Do not break that:

- **Never call `page.bring_to_front()`.**
- Attach media with `set_input_files` on the input element. Never open the native OS file-picker — it is modal and steals focus.
- Launch the debug profile off-screen (`--window-position=-32000,-32000`) rather than minimized, and disable background throttling so an unfocused window still behaves normally: `--disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding`.
- The app's own UI must not raise itself, and opens a modal dialog only in the three cases listed under the UI rules — never to report status, which goes to the queue view or a passive toast.
- Headless is not an option — different fingerprint, defeats the real-session premise.
- Schedules are absolute timestamps recomputed on wake, never `sleep()` countdowns. Hold off system sleep during an active batch via `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`; a slot missed during suspend is reported, never fired late in a burst.

## Power: sleep, shutdown, and an unattended laptop

**`SetThreadExecutionState` suppresses the idle timer and nothing else.** Closing a lid, picking Sleep from the Start menu and shutting down all suspend the machine straight through it, mid-batch and all. Nothing in the app blocks shutdown, and nothing should. So the app's keep-awake is a defence against an *idle* machine dozing off, not against a user with a laptop — and a shut-down machine cannot be woken by Task Scheduler at all (wake timers reach a sleeping machine; only BIOS RTC or Wake-on-LAN reach a dead one). **Never build a "wake up to post" path.** The supported answer is `scripts/setup_always_on.ps1`: lid does nothing, never idles to sleep, app restarted at logon — **all of it mains-only**, because a laptop that refuses to sleep in a bag is a fire risk, and a missed slot is reported rather than fired late. It records every value it changes and `-Revert` puts them all back.

The installer calls the script with **`-AppPath`** (build the logon task around the packaged `.exe` rather than hunting for a `.venv` and `main.py`) and, when the client wanted autostart but not a machine that never sleeps, **`-SkipPower`** (register the task, touch no power setting). Keep both working when changing the script.

Three failures here were live gaps, and each one is a rule now:

- **The keep-awake is bounded by `KEEP_AWAKE_HORIZON` (30 min), not by "is a batch running".** A batch that runs out of posting window at 23:00 stays `TASK_RUNNING` with `resume_at` set to 08:00, and counting that held the machine awake for nine hours of deliberate waiting. `TaskRepo.active_batches(before)` takes the bound; 30 minutes clears the longest inter-group gap (25), so **every real gap still counts** — suspending in a gap strands the rest of the batch exactly as suspending mid-post would.
- **`ConnectionFailed` defers the batch; it does not halt it.** It is the one failure raised *before a page exists* — `session.attach()` — so nothing was typed and nothing can have been published, and the reasoning that makes every other failure terminal does not apply. Halting threw away a whole scheduled batch every time Chrome happened to be down: after a Windows Update restart, after a browser crash, in the minute between logon and Chrome finishing start-up. `TaskRepo.release_target` hands the claim back unattempted (the mirror of `claim_target`, conditional for the same reason), the batch retries every `CONNECTION_RETRY` (5 min) and gives up after `CONNECTION_GIVE_UP` (2 h). Announced **once**, not every five minutes.
- **Crash recovery distinguishes "could not look" from "looked and found nothing".** `recover()` runs seconds after a restart, which is exactly when Chrome is least likely to be up; a `ConnectionFailed` there used to mark the target failed and halt the batch on the strength of the browser being absent. It now leaves the target `running` — the cautious direction, since nothing else touches it — and `_retry_recovery` comes round again. Bounded by the same 2 hours, and for a power reason: a target left `running` keeps its task `running`, which keeps the machine awake, so waiting for ever on a browser that is never coming back would hold a laptop awake for ever too. Any *other* exception escalates immediately, unchanged — that means the check ran and came back unusable, which is the user's to look at.

`power.on_battery()` returns **three** values — True, False, and `None` for "cannot be established". A desktop, a VM and a driver that declines to answer all report 255, and telling that user to plug in a laptop they do not have is worse than saying nothing. The worker emits one `"power"` warning per batch when it is genuinely on battery, because the power plan above is mains-only by design and an unplugged laptop can be suspended with the app none the wiser. Like `check_fn` and `group_namer`, the seam is **inert by default and wired up in `App.start_worker`** — a default that read the real battery would make the suite's answer depend on whether the developer's machine was plugged in.

## Rules for the Worker (`fbposter/worker.py`)

One `PostingWorker`, one thread, started by `App.start_worker()` and by nothing else. Constructing an `App` deliberately does **not** start it, which is the only reason the GUI test suite can exist.

- **Every wait is an absolute instant, never a countdown.** The inter-group gap lives in `tasks.resume_at` and is compared against the wall clock each tick. Never replace it with `sleep()` — a countdown does not survive the app closing or the machine suspending.
- **The 10-25 minute gap is global, and `tasks.resume_at` cannot enforce that on its own.** It spaces the groups *within* one batch, and that is all it can do: `claimable()` filters a waiting task out of its result and hands back the next one, so a second batch posted straight through the first one's wait — and a batch that finished left nothing behind at all, so the one after it started the same second. Twelve groups across three batches went out in 66 minutes with two posts one minute apart, which is the exact pattern this app exists to avoid. `settings.next_post_after` is the earliest instant *any* post may go out; it is written after every post and checked at the top of `_attempt`, which is the one funnel every post passes through. The same twelve now take 3h20m with no gap under 12 minutes. **A skip is not a post and must never set it** — a batch of groups all inside their cooldown would otherwise lock the account out of posting for another twenty minutes. The ORDER BY in `claimable()` was never enough; it decides order, and cannot hold back a row the WHERE clause already removed.
- **Guards are re-checked at posting time**, not just at enqueue. The window may have closed, the cap may have filled, the cooldown may have started since. Outside the window the batch defers to `clock.next_window_open` rather than posting late.
- **That includes the wording, and for a while it did not.** `check_repeat_text` ran only at the door, and the door's answer goes stale: post now and schedule the same advert for tomorrow, and both were judged before either had gone out. The cooldown hid the near case and nothing covered the far one, so the same words reached the same group twice — the actual ban vector, not post count. `_attempt` now re-reads `GroupRepo.recent_bodies` and **skips that one group, loudly, while the batch carries on**, exactly as a cooldown does. Do not upgrade it to a halt: a wording can simply be reworded, and halting a batch over one would cost the user posts they are entitled to make.
- **Believe the `PostOutcome`.** `outcome.posted` being False (a dry run) must not be recorded as a real post — that would start a real cooldown and consume real daily cap. This was a live bug.
- **A target is claimed, not just marked.** `TaskRepo.claim_target` is a conditional `UPDATE ... WHERE state = 'pending'` and the transition itself is the lock. Reading a pending target and then marking it running as two steps left a window in which a *second copy of the app* read the same target and posted it too — racing two workers on one database produced a duplicate in **7 runs out of 40**. Never replace this with an unconditional `mark_target`.
- **Only one app may run.** `fbposter/single.py` takes a Windows named mutex in `run()`; a second launch shows a message box and exits 1. This is the second layer — `claim_target` is what makes a duplicate impossible — but it stops two copies fighting over the same Chrome session and both holding the machine awake.
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
- **Ask `GroupRepo.active()` before posting, never `get()`.** Removing a group archives it now, so `get()` still returns one the user has taken off the list. `active()` is `None` for those, which is what `get()` returning `None` used to mean. See the archiving section above.
- **Never retry.** `PostNotVerified` halts the batch; it does not re-post. A duplicate is worse than a missing post. The **one** exception is `ConnectionFailed`, and only because it is raised before a page exists — see the Power section. Do not widen it: every other failure happens with a composer open, where "it may already have posted" is live.
- **`_post` catches `BaseException`, records, then re-raises.** `KeyboardInterrupt` and `SystemExit` are not `Exception`, so they used to kill the worker thread outright — leaving the target stuck in `running` and the keep-awake request still held, so the machine could not sleep.
- **Attachments are checked on disk before the browser is touched.** A file moved since the batch was queued otherwise surfaces as a Playwright timeout inside `set_input_files`, naming nothing the user can act on.
- **Crash recovery verifies, it does not guess.** A target left `running` is checked against the group with `GroupPoster.verify`: found → done, uncheckable → escalated to the user.
- **`verify()` answering no is not proof it did not post, so recovery looks twice.** The feed is virtualised and the check is a single page load, so a slow render answers no for a post that is live; and a moderated group shows the author their own queued post, so a check landing either side of the crash can go either way. Requeueing on that alone is how a duplicate happens. `_resolve_unfound` asks `pending_verdict` — the group's own list of your posts, the one page that separates "queued" from "gone" — before anything is sent again: `pending` → `awaiting_approval` and the cooldown starts, `approved` → done (the earlier verify was a false negative), `unknown` → left `running` and retried, and **only `declined`** — not live *and* not in the queue — requeues. `_wait_or_escalate` bounds the waiting at `CONNECTION_GIVE_UP`, because a target left `running` holds the machine awake.
- **The guards skip only what the batch was allowed to break.** `_attempt` reads `task.overrides` and leaves out exactly those checks. Anything else still refuses. See "Post anyway" below.
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
- **Enforce the daily post cap and the per-group cooldown** before enqueuing, and refuse to exceed them unless the user chooses "Post anyway" for that batch. See the next section.
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

**A landmark is part of the selector, not decoration — `document.querySelector('h1')` is not the group's heading.** It returns the first `h1` in *document order*, and Facebook's chat sidebar renders its own and inserts it **ahead** of the group's about 1.75s into the load. A group called `TRY` was therefore stored as `Chats`, and it reached a client. Sampled live on 2026-09-08: at 750ms one `h1`, `'TRY'`; at 1750ms two, with `'Chats'` first. So this is **not a race that waiting fixes** — `groupinfo.py` already waited four seconds and the wrong answer is stable long before that; waiting longer makes it worse. **And it was total, not intermittent**: surveyed across 8 unrelated groups on 2026-09-08, the old selector returned `Chats` for *every one*, so a client with several groups saw the same name on all of them. The new selector agreed with the tab title 8/8, Hebrew names included — two independent parts of the page, which is what makes that evidence worth anything. Note the module's own docstring promised it turned `2509198906266893` into `bar-test`; that is one of the surveyed groups, and it was returning `Chats`. `READ_NAME` is scoped to `[role="main"] h1`, and there is deliberately **no "any h1" fallback** — the tab title is a better last resort than an arbitrary heading. `tests/test_groupinfo.py` pins the selector itself, because the bug lived in a JavaScript string no Python test can execute.

**The name is read the moment that scoped heading has text, not after a fixed pause.** The lookup used to wait a flat four seconds *after* DOMContentLoaded. Measured live on 2026-09-25, the heading is already there at DOMContentLoaded (3.47s, heading at 3.50s), so over half of every lookup was waiting for something that had already happened. `wait_for_heading` polls for `HAS_HEADING`, which is scoped exactly like `READ_NAME`. That scope is what makes reading early safe: the late, wrong heading is outside `[role="main"]`. It polls on a timer, never on animation frames, because this Chrome is off-screen and may never paint. A lookup now takes 4-5.5s end to end, and most of that is Facebook's page load.

**Fixing a name lookup does not fix the names already stored.** `GroupRepo.missing_names` only finds *empty* ones, so a wrong name is never looked at again and would sit in the Groups list for ever. Migration 008 clears `groups.name` so the Groups screen re-fetches; until it does, `display_name` falls back to the identifier, which is what a newly added group shows anyway. Safe because nothing decides anything on `name` — the repeat guard, the cooldown and the queue all key on `group_id`.

**Read every string off the live site — never translate one.** Russian's post button is `Отправить` ("send"), while the obvious translation, and what research suggested, is `Опубликовать`. Shipping the plausible word would have failed at the Post click, which is the one step that cannot safely be retried. **The pending banner caught the same trap twice more**, verified 2026-08-18: the live Hebrew is `בהמתנה` where the natural translation gives `ממתין`, and the live Russian is `подтверждения` ("confirmation") where the natural translation gives `одобрения` ("approval") — a word that appears nowhere on the page. Two of the three shipped only because the researched list happened to include the right variant alongside the wrong one. To add a language: `main.py probe` a real group, dump the composer, paste what Facebook returns.

**Verifying a pending string is cheap only while something is pending.** The banner exists solely while the group is holding a post of yours, so it cannot be read on demand: the Hebrew round needed a real post, and the Russian round was free only because that post was still sitting in the queue. If a fourth language is ever added, read both strings *while a post is pending* rather than switching twice.

**`?locale=` is ignored outright on a logged-in session — verified live, 2026-08-16.** Probing a real group with `?locale=ru_RU`, `?locale=he_IL` and `?locale=en_US` against an English account: Facebook kept the parameter in the URL and rendered English every time. `<html lang>` stayed `en` in all three, and under `he_IL` `dir` stayed **`ltr`** — Hebrew would have forced `rtl`, so this is not a redirect or a strip, it is simply ignored. There is no account-language x URL-locale matrix; there are only three states, the account's own setting.

**The account's language is the only one that matters, and the app never asks for another.** `parse_group_url` rebuilds every URL as `https://www.facebook.com/groups/<id>/` from the identifier alone, so a pasted `?locale=` is discarded and never reaches Facebook. There is no configured or expected language anywhere in the codebase: every lookup tries the *whole* candidate list across all three at once, so whichever language the page comes back in is matched. All nine account-language x pasted-locale combinations therefore work, and they work for that reason rather than by enumeration.

Two tests keep it that way, and both guard something that fails silently: `tests/test_detect.py::TestEveryLanguageIsCovered` checks all eight language-dependent tables have a candidate in each script — **including `RATE_LIMIT_MARKERS`, where losing a language means posting on through a block** — and `test_no_language_dependent_table_is_left_unguarded` fails if `strings.py` gains a table nobody added to that list.

The anomaly markers are the deliberate exception — a rate-limit warning cannot be summoned on demand, so those are researched and marked unverified, and biased toward over-matching.

Never hardcode a UI string outside `strings.py`, and never assert on a literal in a test — reference the constants.

## Known Context

Automating posts violates Facebook's Terms of Service. The measures above reduce detection risk but do not eliminate it; this tradeoff is understood and accepted by the user, and is documented in the Known Risks section of `README.md`.
