# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Status

**Phases 1-3 are done.** Phase 1: Chrome debug-profile launcher, CDP attach, session verification. Phase 2: CustomTkinter UI shell with a live connection pill. Phase 3: SQLite persistence — groups, templates, tasks and the safety guards, all wired into the views. Phases 4-5 (automation engine, scheduler/worker) are not started, so **nothing posts to Facebook yet**; queued batches sit at `pending`. `README.md` holds the full spec and roadmap.

## What This Is

A local, single-user Windows desktop app (Python 3.10+, CustomTkinter) that posts text and media to Facebook groups on a schedule, by driving a real logged-in Chrome session through Playwright over CDP. Everything runs on the user's machine — no server, no cloud, no automated login.

## Commands

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

.\.venv\Scripts\python.exe -m pytest tests/ -q                                 # full suite
.\.venv\Scripts\python.exe -m pytest tests/test_session.py::TestClassifyUrl -q  # one class
.\.venv\Scripts\python.exe -m pytest tests/test_session.py -q -k checkpoint     # one test

.\.venv\Scripts\python.exe main.py gui      # open the desktop app
.\.venv\Scripts\python.exe main.py setup    # Chrome on-screen, for the one-time manual login
.\.venv\Scripts\python.exe main.py launch   # Chrome off-screen, ready for automation
.\.venv\Scripts\python.exe main.py status   # attach over CDP, report the session state
```

`status` exits 0 when logged in, 1 when not, 2 on error (Chrome not running, etc.).

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

UI and worker communicate through a thread-safe queue. Schema sketch (groups, tags, group_tags, templates, tasks, task_targets, run_log) is in `README.md`.

Modules that exist so far: `fbposter/config.py` (paths, port, flags), `fbposter/chrome.py` (find/launch Chrome, probe the debug port), `fbposter/session.py` (CDP attach, session verification), `fbposter/strings.py` (all Facebook URLs and English UI strings), `fbposter/errors.py`.

UI modules: `fbposter/ui/app.py` (window, sidebar, connection pill), `views/` (compose, groups, queue), `theme.py`, `toast.py`, `background.py`, `connection.py`. Plus `fbposter/groups.py` for group-URL parsing.

Storage: `fbposter/db/` — `connection.py` (per-thread connections), `schema.py` (migrations), `models.py`, `repo.py`. Safety rules: `fbposter/guards.py`.

### Database rules

- **`Database.transaction()`, never `with connection:`.** Connections use `isolation_level=None` (autocommit), so `with connection:` commits a transaction that was never begun and a later failure leaves earlier statements written. This already produced an orphan task row once; `tests/test_db.py` guards it.
- **One connection per thread.** The UI thread and the Phase 5 worker both use the database; `Database.connection` is thread-local and WAL is on so a read never blocks on a write.
- **Safety decisions live in `guards.py` as pure functions**, never in a repository or a view. Repos supply the counts and timestamps; guards judge them. That keeps the whole safety table testable without a database or a browser.
- The `UNIQUE(task_id, group_id)` index is load-bearing — do not drop it to "fix" an insert error.
- The App takes an injectable `db=`; tests pass a temporary database and must never touch `C:\FBAutomation\fbposter.db`.

### UI rules that are easy to break

- **Only the main thread touches widgets.** Blocking work goes through `BackgroundRunner` in `fbposter/ui/background.py` — worker thread → `queue.Queue` → `widget.after()` pump. The Phase 5 posting worker reports progress the same way.
- **No modal dialogs for status, ever** — use `app.toast`. The single permitted OS dialog is the media file picker, because the user asked for it and it cannot fire during a batch.
- **Colours are `(light, dark)` tuples in `theme.py`.** Never hardcode a hex value in a widget; a colour defined for only one mode is invisible in the other.
- **`CTkFrame` defaults to 200x200.** Any frame used as a thin divider, spine or spacer must pass explicit dimensions, or it silently stretches its row (216px queue rows) or draws as a stray 200px line (an "invisible" spacer frame). Both bugs happened; `tests/test_ui.py` guards both. Use `pady` for spacing rather than an empty frame.
- **The expanding widget in a view must be packed last**, after the fixed controls are anchored with `side="bottom"`. Otherwise it claims the frame and pushes them off-window.
- GUI tests share **one** Tk interpreter for the whole session (`ui_app` in `tests/conftest.py`). Creating a second root, or recreating one after a destroy, fails intermittently on Windows with "Can't find a usable init.tcl". Never call `mainloop()` in a test — pump with `pump_until`.

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

## Rules for the Automation Engine

These are safety requirements, not preferences. The user posts as an ordinary group member and account loss is the failure mode being designed against.

- **Never automate login, and never click through a checkpoint, CAPTCHA, or verification screen.** On encountering one — or any "posting too fast" warning or unexpected page — halt the entire batch and surface it to the user. No blind retries.
- **Randomized 10-25 minute delay between groups.** At 5-7 groups a batch legitimately takes 1-3 hours. This is not a performance problem to be optimized away.
- **Human-like interaction is required**, not cosmetic: randomized scroll on arrival, hover before click, per-keystroke typing delay with variance. Never paste a full text block instantly.
- **Enforce the daily post cap and the per-group cooldown** before enqueuing, and refuse to exceed them.
- **Verify the post actually appeared** after publishing rather than trusting that the click succeeded.

- **Enforce content variation.** Per Meta's Spam policy, accounts get restricted at *low* frequencies when repetitive content is present. At this volume that is the actual ban vector — not post count. Warn before byte-identical text goes to more than two groups; make per-group editing easy.

Expected volume is low by design: 2-3 posts/day to 5-7 groups each, with content that varies between runs. Tune for looking normal, never for throughput. Groups where the user is an admin should be excluded — Facebook schedules those natively with no automation needed.

## Scope Discipline

The workload is small, and the spec was trimmed accordingly. **Not in v1:** recurring/cron scheduling, group tags ("groups of groups"), a separate `run_log` table, video upload, pause/skip mid-batch. See the Scope Control table in `README.md` for the reasoning. Do not reintroduce these without asking — each was cut on purpose. v1 is: compose → pick groups → post now or schedule once → serial worker with jitter → live queue view.

## Selector Strategy

Facebook's DOM class names are obfuscated and change between builds. Use role- and `aria-label`-based selectors (`get_by_role`, `get_by_label`) and never CSS class selectors. Selectors are also language-dependent.

**The account's Facebook UI is English.** Target English strings ("Write something...", "Post", "Photo/video"). If the account's language ever changes, every selector breaks — keep user-facing strings in one module rather than inlined across the automation code.

## Known Context

Automating posts violates Facebook's Terms of Service. The measures above reduce detection risk but do not eliminate it; this tradeoff is understood and accepted by the user, and is documented in the Known Risks section of `README.md`.
