# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Where to look

Most of these rules already cost a live bug. This file holds what applies everywhere. The rest is in `.claude/rules/`, and each file there loads by itself when Claude reads or edits a file it lists at its top, tests included. When a rule names a section by its title, this table says which file holds it:

| Rule file | Sections | Loads for |
| --- | --- | --- |
| `worker.md` | Rules for the Worker; Repeating posts | `worker.py`, `recurrence.py`, Publish, the repeating-post edit screen |
| `power.md` | Power: sleep, shutdown, and an unattended laptop | `power.py`, `always_on.py`, `worker.py`, `scripts/`, Settings, `installer.iss` |
| `automation.md` | Rules for the Automation Engine; Selector Strategy; Reading the client's groups off Facebook | `automation/`, `strings.py`, `groups.py`, `media.py` |
| `chrome-and-login.md` | Handing this to a non-technical user; Changing which Facebook account it posts as; Never call `browser.close()` | `chrome.py`, `keepalive.py`, `tabs.py`, `cdp.py`, `session.py`, `login.py`, `onboarding.py`, `connection.py`, `single.py`, `config.py`, `main.py`, `qtui/app.py`, the wizard, Settings |
| `data.md` | Database rules; Two retentions; Removing a group archives it; Group sets; The Dashboard replaced the Queue screen | `db/`, `stats.py`, the Dashboard, Groups and Group sets screens |
| `ui.md` | Hebrew, and why the UI is Qt; UI rules | `qtui/` |
| `testing.md` | How the tests avoid a browser and a real clock | `tests/` |

Two folders also have their own CLAUDE.md, which loads when a file in that folder is opened but not from `tests/`: `fbposter/qtui/CLAUDE.md` (the window: flow, redrawing, look; open it before editing a `test_qtui_*` file) and `packaging/CLAUDE.md` (the installer).

Text that reaches a post also passes Invisible characters (below) and Hebrew (`ui.md`), whatever screen it came from.

## Rules that hold everywhere

Each is spelled out in its rule file. These are the ones no change may break:

- **Never post twice.** A post is never retried once a composer was opened; the one exception is `ConnectionFailed` (`worker.md`). A duplicate is worse than a missing post.
- **Never automate login, and never click through a checkpoint, CAPTCHA or warning.** Halt the batch and tell the user (`automation.md`).
- **Never call `browser.close()`** on the user's Chrome (`chrome-and-login.md`).
- **Never delete posting history to tidy up.** Removing a group archives it (`data.md`).
- **Every wait is an absolute time in the database, never a `sleep()` countdown** (`worker.md`).
- **Facebook's words live only in `strings.py`**, read off the live site and never translated, and selectors use roles and `aria-label`, never CSS classes (`automation.md`).

## Repository Status

**All five phases are done; v1 is feature-complete.** Chrome debug-profile launcher and CDP session (1), the UI (2; CustomTkinter then, Qt now), SQLite persistence and the safety guards (3), the automation engine in `fbposter/automation/` (4), and the scheduler/worker in `fbposter/worker.py` (5).

**The app now posts on its own.** Opening the GUI starts the worker, and any due batch will go out. `README.md` holds the full spec. Everything built since v1 is in `git log`; the rules each piece left behind are below and in `.claude/rules/`.

**It is now shipped, not just run.** The app is packaged as an installer for a non-technical client: a first-run wizard replaces the terminal commands the app used to print, and `packaging/` builds a signed-nothing-but-working `Setup.exe`. The wizard also owns the one thing a finished setup might still need changing, which Facebook account it posts as. See "Handing this to a non-technical user" (`chrome-and-login.md`).

## What This Is

A local, single-user Windows desktop app (Python 3.10+, Qt/PySide6) that posts text and media to Facebook groups on a schedule, by driving a real logged-in Chrome session through Playwright over CDP. Everything runs on the user's machine. No server, no cloud, no automated login.

## Commands

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

.\.venv\Scripts\python.exe -m pytest tests/ -q                                 # full suite
.\.venv\Scripts\python.exe -m pytest tests/test_session.py::TestClassifyUrl -q  # one class
.\.venv\Scripts\python.exe -m pytest tests/test_session.py -q -k checkpoint     # one test

.\.venv\Scripts\python.exe main.py start    # everyday use: Chrome if needed, then the app
.\.venv\Scripts\python.exe main.py gui      # the app alone, no Chrome handling
.\.venv\Scripts\python.exe main.py setup    # Chrome on-screen, for the one-time manual login
.\.venv\Scripts\python.exe main.py launch   # Chrome off-screen, ready for automation
.\.venv\Scripts\python.exe main.py status   # attach over CDP, report the session state

# Phase 4, both safe to run against a real group:
.\.venv\Scripts\python.exe main.py probe   <group-url>              # resolve selectors; types nothing
.\.venv\Scripts\python.exe main.py dry-run <group-url> --text "..." # full rehearsal, never clicks Post
.\.venv\Scripts\python.exe main.py dry-run <group-url> --image a.jpg --image b.jpg  # pictures; --text optional
.\.venv\Scripts\python.exe main.py dry-run <group-url> --video a.mp4  # waits for the upload, then discards
```

```powershell
# Laptop that must keep posting: awake plugged in (lid open or shut), awake on
# battery only while the lid is open. Fully reversible:
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

There is no linter or formatter configured, and no pytest config file. The suite is the whole check. Baseline: **1890 tests, 75-240s**. The spread is machine load, not the suite; the Qt GUI files are much of it. A run of *five minutes or more* means something is reaching the network; see the `SilentNamer` note in `.claude/rules/ui.md`.

**Do not add `playwright install`.** It is unnecessary and was verified so against Chrome 150: the app attaches to the user's real Chrome over CDP and never launches Playwright's bundled Chromium, so the driver shipped inside the pip package is all that is required.

## Critical Constraint: Chrome Debug Profile

Chrome 136+ **refuses `--remote-debugging-port` when using the default user profile.** The app therefore cannot attach to the user's everyday Chrome window, and any code or docs assuming it can are wrong. A dedicated profile directory is required:

```
chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\FBAutomation\ChromeProfile"
```

The user logs into Facebook manually inside that profile, once. Playwright then attaches with `connect_over_cdp`. It never launches its own browser context and never logs in.

## Architecture

Three layers that must stay separate:

- **UI (main thread)**: the Qt event loop (`qtui/`). Never touches Playwright objects or the database directly during long operations.
- **Worker (exactly one background thread)**: owns the Playwright sync API and processes the task queue strictly serially. One group at a time, globally. There is never a second worker, and batches never overlap; a "Post Now" issued mid-batch is appended to the queue rather than run concurrently.
- **SQLite**: the source of truth for queue state, not just persistence. Each group's outcome is committed as it completes so a crash or restart resumes the batch instead of re-posting. Duplicate posts are the single worst failure mode here (strong spam signal), so idempotency belongs in the schema, not in memory.

UI and worker communicate through a thread-safe queue. The tables as built are `groups`, `templates`, `tasks`, `task_targets`, `schedules`, `schedule_targets`, `settings`, `post_totals`, and for group sets `group_sets`, `group_set_members` and `schedule_sets`; `tags`, `group_tags` and `run_log` were cut (README §9 and §10). Do not write code expecting them.

Core: `config.py` (paths, port, Chrome flags), `chrome.py` (find/launch Chrome, probe the debug port), `session.py` (CDP attach, `c_user` cookie check), `connection.py` (the check behind the connection pill: `ConnectionState`, `check_connection`), `strings.py` (every Facebook URL and UI string, in all three languages), `clock.py` (Israel-time judgement), `power.py` (`SleepBlocker`, `on_battery`), `always_on.py` (the Settings screen's start-with-Windows and keep-awake switches, which run `setup_always_on.ps1`), `recurrence.py` (repeating-schedule timing, pure), `stats.py` (the Dashboard's numbers, pure too), `groups.py` (group-URL parsing), `text.py` (the invisible-character list, and `normalise`), `tips.py` (the Posting tips words in three languages, pure), `media.py` (picture or video, Facebook's 10 GB video limit, pure), `single.py` (the one-app mutex), `onboarding.py` (what setup step the user is on, pure), `login.py` (putting a visible Chrome in front of them), `keepalive.py` (when to restart a Chrome that has closed, pure), `tabs.py` (finding and closing a tab that has stopped answering, the last-resort restart, and closing Chrome when the app closes), `cdp.py` (a DevTools client on the standard library, for when Playwright is the thing hanging), `errors.py`, `worker.py` (`PostingWorker` and `LivePoster`).

Scripts: `scripts/setup_always_on.ps1`: the laptop power plan and the logon task. See `.claude/rules/power.md`.

Packaging: `packaging/`: `fbposter.spec` (PyInstaller), `installer.iss` (Inno Setup), `build.ps1` (both, plus the suite), `entry.py`, `policy.txt` (the account blocking policy the installer asks the client to accept; `make_policy.py` turns it into `policy.rtf`). Its rules are in `packaging/CLAUDE.md`.

Automation: `automation/poster.py` (`GroupPoster`: arrive → compose → type → attach → publish → verify, plus the read-only `probe`), `detect.py` (`classify` a page as OK / checkpoint / login / rate-limit / unavailable), `humanize.py` (`Humanizer`: keystroke timing, hovers, arrival scroll, the inter-group gap), `groupinfo.py` (read a group's display name off the `h1` **inside `[role="main"]`**, and its member count in the same visit; cosmetic, and must never raise into a caller), `groupfinder.py` (read every group the account is in off Facebook's "Your groups" page; see "Reading the client's groups off Facebook").

UI: `qtui/app.py` (window, sidebar, connection pill, paused line, worker-event pump, background thread helper), `qtui/views/` (`home`, where the app opens; `create`, Create a post, which holds compose, groups and publish as the three steps of one road with a step bar; dashboard; `group_sets`, where sets are made; `tips`, the Posting tips; `settings`, at the foot of the sidebar; plus `welcome`, the first-run wizard, and `schedule_edit`, which edits one repeating post from its card in step 3; neither is in the sidebar. See `fbposter/qtui/CLAUDE.md` for the flow), `qtui/theme.py` (palette + one stylesheet), `qtui/widgets.py` (`card`, `row`, `clear`, `replace_at`, `badge`, `segmented`, `AppStyle`), `qtui/icons.py` (the sidebar and status icons, as SVG strings), `qtui/assets/`.

Storage: `db/`: `connection.py` (per-thread connections), `schema.py` (migrations), `models.py`, `repo.py` (`GroupRepo`, `GroupSetRepo`, `TemplateRepo`, `TaskRepo`, `ScheduleRepo`, `SettingsRepo`).

### Invisible characters must never reach a comparison

`fbposter/text.py` owns one list of characters that print nothing (bidi marks, zero-width joiners, soft hyphen, BOM), and `strip_invisible()` removes them. It exists because two load-bearing comparisons are string equality against the user's own words:

- **`text.normalise`** folds them, so the worker knows when the same words went to a group before (`PostRequest.sent_before`), which is what stops an old post in the feed being taken as proof that a new one went out. Before this, the same ad pasted twice compared as *different text*; it once let the repeated-text rule (gone since 2026-10-18) wave a repeat through. Folding in `normalise` rather than only at the input is deliberate: bodies already stored with marks in them have to compare correctly too.
- **`distinctive_snippet`** strips them, because Facebook drops them when it renders. A snippet still carrying one is searched for and never found, which reports a post that went out fine as failed and halts the batch.

Both Qt entry points clean on the way in as well: `ComposeView.get_text()` and `PublishView.alternates()`. **Qt needing no direction marks of its own is not the same as no marks arriving**; that gap is how this shipped.

### Time: stored in UTC, judged in Israel time

Every timestamp is stored and compared in UTC, but **every human judgement is made in `Asia/Jerusalem`**: what counts as "today" on the Dashboard, the hours the follow-up sweep may open a group page in, and every time shown in or typed into the UI. Go through `fbposter/clock.py` (`to_local`, `local_hour`, `start_of_local_day`, `next_window_open`, `parse_local`, `format_local`); never read `.hour` or `.date()` off a stored UTC value. Doing that put the posting window three hours out, when there still was one: it allowed a 01:00 local post and refused a 09:00 one.

**A posting window may cross midnight.** `clock.inside_window` owns the rule and `clock.next_window_open` defers to it, because when the two judged differently a 22:00-06:00 window excluded *every* hour and a batch deferred itself one day at a time, for ever, silently. The posting hours no longer hold posts back (see "No posting rules, only tips"); they are still read, from the stored `posting_window_*` settings, by `_follow_up_pending`. `clock.sane_hour` also guards against a stored hour outside 0-23, which used to raise `hour must be in 0..23` inside `datetime.replace`; the worker loop swallowed it and retried every tick, so the app just stopped posting.

`tzdata` is therefore a hard requirement, not a nicety: Windows ships no IANA time zone database, so without it `Asia/Jerusalem` does not resolve and `clock.posting_zone()` falls back to the machine's own zone.

### No posting rules, only tips

The user's choice, 2026-10-18. The clients are real-estate agents who post one listing to 40-80 groups, once or twice a day, and the rules stopped exactly that. **The daily limit, the cooldown per group, the posting hours and the repeated-text rule are gone, and nothing warns about them either.** Whatever is queued goes out, one group at a time, with the gap between. `fbposter/guards.py` was deleted; `normalise` moved to `text.py`, and "Post anyway" went with the rules it got past.

- **What is left is not a rule the user bends.** The 2-4 minute gap between posts (lowered from 10-25 the same day, from research: closer than 2-3 minutes reads as a bot), halting on a checkpoint, never re-sending a post that may have gone out, one post at a time, and a missed slot dropped rather than fired late. The user confirmed each.
- **The advice lives on the Posting tips screen** (`fbposter/tips.py`, `qtui/views/tips.py`): ten tips in Hebrew, English and Russian, researched that day from Meta's spam policy and guides by people who post to many groups. It decides nothing: `tests/test_tips.py` fails if the worker, Compose, Publish or `recurrence.py` import it. Every language has the same tips in the same order with the same numbers, which that test pins. It opens in Hebrew, right to left through `setLayoutDirection` (not bidi marks), and remembers the language in the `tips_language` setting. Screen names (Publish, Settings, Pause posting) stay in English in every language because that is what the window says. **The Hebrew was checked with the `hebrew-content-writer` skill; the Russian had no such check**, so have a native speaker read any change to it.
- **Old data is left as it was.** `tasks.overrides` and `schedules.overrides` (migration 009) are still columns, default `[]`, and nothing reads or writes them. `daily_cap`, `default_cooldown_hours` and `groups.cooldown_hours` are still stored and seeded, unread, like `queue_retention_hours`. Never edit migration 009 to drop them.
- **Do not bring a rule back as a "small safety net".** A warning on Publish was offered and turned down too: the user wants the page of tips and nothing on the screens that post.

### Playwright is imported lazily, and must stay that way

**Startup cost was Playwright, and it is now deferred.** This reverses an earlier decision recorded here, on a re-measurement: `playwright.sync_api` costs **~830ms**, not the ~340ms first measured, and importing `fbposter.qtui.app` went from **1670ms to 682ms** without it. The earlier note also had the mechanism wrong. Neither `worker.py` nor `automation/groupinfo.py` imports Playwright; **`session.py` does**, and both pull it in through that, so one lazy import fixed both rather than needing two.

It is imported inside `session.attach()`. The objection recorded last time (that this moves the cost onto the first background name lookup or the first post) is exactly why it is safe: both of those already run **off the thread drawing the window**. A second on the worker thread is invisible; a second before the window appears is not. `tests/test_qtui_performance.py` pins both halves: that importing the window does not pull Playwright in, and that nothing in the package imports it at module scope, since the cost comes back silently wherever that is written.

## Non-Interfering Operation

The user keeps working on the machine while batches run or wait. This is a hard requirement.

It is achievable because Playwright dispatches input through the DevTools protocol, **not** through OS-level mouse/keyboard, so the automation window never needs focus. Do not break that:

- **Never call `page.bring_to_front()`.**
- Attach media with `set_input_files` on the input element. Never open the native OS file-picker. It is modal and steals focus.
- Launch the debug profile off-screen (`--window-position=-32000,-32000`) rather than minimized, and disable background throttling so an unfocused window still behaves normally: `--disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding`.
- The app's own UI must not raise itself, and opens a modal dialog only in the three cases listed under the UI rules (`ui.md`), never to report status, which goes to the Dashboard or a passive toast.
- Headless is not an option: different fingerprint, defeats the real-session premise.
- Schedules are absolute timestamps recomputed on wake, never `sleep()` countdowns. Hold off system sleep during an active batch via `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`; a slot missed during suspend is reported, never fired late in a burst.

## Scope Discipline

The workload is small, and the spec was trimmed accordingly. **Still cut:** skipping a single group mid-batch (the Dashboard's "Going out" list can only cancel a whole batch), and a separate `run_log` table. Each was cut on purpose; do not add them without asking. The Scope Control table in `README.md` has the reasoning.

Everything else cut from v1 has since been **built at the user's request**: repeating posts (daily, as many times a day as wanted since 2026-10-18; it was in their original requirements; `worker.md`), pausing (one pause for all posting, on the Settings screen, which holds a batch mid-way; `worker.md`), group tags (as group sets, 2026-10-15; `data.md`) and video upload (2026-10-15; "Videos" in `automation.md`). v1 was: compose → pick groups → post now or schedule once → serial worker with jitter → live queue view (the Dashboard since 2026-10-14).

## Writing: no long dashes

**Never write a long dash (U+2014 or U+2013), anywhere**: not in what the app says, not in a comment, a test or these docs. The user asked for every one to go (2026-10-12) because they read as machine-written. A sentence that wants one wants a full stop, a comma, a colon or brackets. A range is written with a hyphen: 10-25 minutes, 08:00-23:00. `tests/test_qtui_look.py::TestNoLongDashes` scans the app, the tests and every `.md` file.

## Known Context

Automating posts violates Facebook's Terms of Service. The measures above reduce detection risk but do not eliminate it; this tradeoff is understood and accepted by the user, and is documented in the Known Risks section of `README.md`.


## My rules
All your answers need to be short and clear.
Never assume anything, if you are not sure about something you ask me.
Explain technical stuff as simple as you can. 