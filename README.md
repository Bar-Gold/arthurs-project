# Facebook Local Auto-Poster

## 1. Project Overview
A lightweight, local desktop application that automates publishing text and media posts to a set of Facebook groups. Built for a single end-user, the system prioritizes account safety, ease of daily use, and a clean, modern GUI.

The app runs entirely on the user's machine and drives a real, already-logged-in Chrome session. There are no cloud proxies, no automated logins, and no traffic from unfamiliar IP addresses.

## 2. Usage Profile (drives every design decision below)
*   **Volume:** 2-3 posts per day, each going to 5-7 groups. Roughly 15-20 published posts per day at peak.
*   **Content:** Varies between runs. The user is not blasting one identical block of text everywhere, though templates will be reused and lightly edited.
*   **Operator present:** The user is at the machine during the day. The app does not need to survive unattended overnight runs, but it must survive a crash or restart without losing queue state.

This is a low-volume, human-paced workload. The system should be tuned for *looking normal*, not for throughput.

## 3. Technical Stack
*   **Language:** Python 3.10+
*   **UI Framework:** **CustomTkinter** (decided). Gives a modern, polished look with far less code than PyQt6. Since Playwright runs on a background worker thread feeding a queue, the async integration that PyQt6 offers is not needed here.
*   **Browser Automation:** Playwright (Python, **sync API**) connecting to a running Chrome over CDP via `connect_over_cdp`.
*   **Database:** SQLite (local `.db` file) for groups, tags, templates, scheduled tasks, and queue/run history.
*   **Images:** Pillow, read only by the Compose preview to scale attachments down to thumbnails. Optional at runtime — without it the preview draws a tile per image and nothing else changes.
*   **Threading model:** Tkinter mainloop on the main thread; one single worker thread owns Playwright and processes the queue serially. UI and worker communicate through a thread-safe queue — Playwright objects never touch the UI thread.

## 4. Chrome Session Setup (read before Phase 1)
**Chrome 136+ refuses to open `--remote-debugging-port` when running against the default user profile.** This means the app cannot attach to the user's everyday Chrome window.

The working approach is a dedicated Chrome profile, launched by the app:

```
chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\FBAutomation\ChromeProfile"
```

The user logs into Facebook **once** inside that profile. From then on it is a persistent, genuinely human-logged-in session with real cookies on the user's own IP and device — it is simply a separate profile from their daily browsing.

The app must:
*   Detect whether Chrome is already listening on the debug port before launching a second instance.
*   Expose a **"Test Connection"** action that attaches over CDP, confirms an active Facebook session, and reports the logged-in account.
*   Fail loudly and clearly if the session is logged out or a checkpoint/verification screen is showing — never attempt to click through one.

## 5. Non-Interfering Operation (requirement)
The user must be able to keep using the computer normally while a batch is posting or waiting for a scheduled time. The automation runs in the background and never takes over the machine.

This works because **Playwright drives Chrome through the DevTools protocol, not through OS-level mouse and keyboard input.** Clicks, hovers, and keystrokes are dispatched straight into the page, so the automation window never needs focus and never steals it. Headless mode is not an alternative — it changes the browser fingerprint and defeats the purpose of using a real session.

Requirements:
*   The automation Chrome window uses its own profile and stays out of the way — launched off-screen via `--window-position=-32000,-32000`, or parked on a second virtual desktop. Prefer off-screen to minimizing.
*   **Never call `page.bring_to_front()`** or anything else that raises the window.
*   Suppress Chrome's background throttling so an unfocused window still behaves normally:
    `--disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding`
*   Attach media with `set_input_files` on the file input element. Never trigger the native OS file-picker dialog — it is modal and does steal focus.
*   The app's own UI must not pop modal dialogs or force itself to the foreground. Status belongs in the in-app queue view, the taskbar, or a passive Windows toast.
*   **Sleep handling:** hold off system sleep while a batch is in flight (`SetThreadExecutionState` with `ES_CONTINUOUS | ES_SYSTEM_REQUIRED`; the display may still turn off). Schedules are stored as absolute timestamps and recomputed on wake, so suspend/resume never drifts. A slot missed while the machine was asleep is surfaced to the user, not fired late in a burst.

## 6. Core Features & UI Requirements
A left sidebar navigates between four screens, in the order the work happens — **Compose (what) → Groups (where) → Publish (when) → Queue (what happened)**; appearance follows the Windows theme. The UI stays uncluttered and readable, and never opens a modal dialog to tell the user something — status goes to a passive in-window toast.

The **connection indicator lives in the sidebar**, not in a screen of its own: it is the "Test Connection" action from §4, and nothing else in the app works without it, so it is visible from everywhere.

*   **Compose**
    *   Spacious free-text input with a live character count.
    *   **Per-group wording.** Tabs above the editor — "All groups" plus one per selected group — switch the editor between the shared base text and a version written for that group. This is what makes the content-variation rule in §7 actionable rather than a warning with no remedy. Editing the base text resets the per-group versions, and says so.
    *   Attach images.
    *   **Preview.** A `Write | Preview` toggle above the editor shows the post laid out the way the group will see it — avatar, name, text, and the pictures as a 1/2/3/4/+N collage running full-bleed to the card edges, with a Like/Comment/Share row. Deliberately no invented like counts. A single picture keeps its own aspect ratio; the rest crop to fill their tile, which is what stops a row of mixed shapes looking ragged. It shows the post as the group will receive it — the wording of the active tab, wrapped as it will wrap, with the attached images scaled down. Deliberately generic: no imitation of Facebook's chrome and no profile picture, because the question it answers is whether the text and the image look right. It matters most alongside per-group wording, where each group gets different words and reading one back is the only way to check it. An image that cannot be read is drawn as a named tile rather than dropped — it is still going to be uploaded.
    *   "Save as Template" storing text + media paths for reuse.
    *   Templates are a starting point, not a lock — the user edits before sending.
*   **Groups** — both the recipient picker for this post and the list itself, deliberately one screen.
    *   Tick the groups this post goes to; "Select all" / "Clear"; the count is always visible.
    *   Add Facebook group URLs right here (only groups where the user can post). A group added this way starts ticked.
    *   URLs are reduced to a canonical group identifier, so the same group pasted in two different forms cannot be added — or posted to — twice.
    *   Per-group record of the last successful post time, so the UI can warn about posting to the same group too soon.
    *   Cooldown defaults to **8 hours** (lowered from 24, so two or three posts a day to one group is possible).
*   **Publish** — the one place that decides *when*. Compose owns the text and the groups and carries no timing controls at all.
    *   **Now** — straight onto the queue.
    *   **Once** — at a single chosen moment, up to a year ahead. Defaults to the current time; the field ignores the mouse wheel and starts on the minutes, so a stray scroll cannot move a post into a different year.
    *   **Repeat** — daily, or on chosen days of the week, at up to three times of day (Israel local, DST-safe).
        *   Several **wordings** per schedule, rotated so no group ever sees the same text twice. Without this the rule in §7 would refuse the second run, and a repeating post would work exactly once.
        *   The Compose text is the first wording; this screen collects alternates.
        *   Pause, resume and delete; the next run is shown before and after creating one.
    *   Warns before creating anything that a time falls outside the posting window, that the frequency is inside the per-group cooldown, or that there are too few wordings for the number of groups.
*   **Queue**
    *   **Live queue view:** what is pending, running, waiting, done or failed, and the countdown to the next group. Batches from a repeating post are labelled with its name.
    *   Cancel a run mid-batch.
    *   **Retention (display).** Anything unfinished is always shown; finished batches drop off the screen after `queue_retention_hours` (24 by default), with an "All" toggle for the rest. This only hides rows.
    *   **Retention (storage).** Finished batches are deleted for good after `history_retention_days` (90 by default; 0 disables), once a day, so the database does not grow without bound. It never deletes anything unfinished, and never the newest 20 posted bodies per group — those are what stop the same wording going to a group twice (§7), so they are kept for ever. `VACUUM` returns the freed space to disk.

**Post-v1 (see §10):** "Groups of Groups" tags, video attachments, and pause/skip-a-single-group mid-batch. They are listed here only so the shape of the finished app is clear; do not build them into v1. Recurring schedules were also cut from v1 and have since been built, at the user's request.

## 7. Account Protection (CRITICAL)
The user posts as an ordinary group member, not an admin, so everything happens through the normal UI. Avoiding automated-behavior signals is the top priority.

*   **Real session only.** No automated logins, ever. The app attaches to a session a human created.
*   **Strictly sequential.** One group at a time, one global worker. Batches never run in parallel and never overlap — a "Post Now" while a batch is running goes to the back of the queue.
*   **Randomized delays.** 10-25 minutes between groups. At 5-7 groups a batch takes roughly 1-3 hours; this is expected and correct, not something to optimize away.
*   **Human-like interaction.** Randomized scrolling on arrival, hovering before clicking, per-keystroke typing delays with variance. No instant paste of a full text block.
*   **Idempotent queue.** Every group's status is committed to SQLite as it completes. After a crash or restart the batch resumes and never re-posts to a group it already hit — a duplicate post is a strong spam signal.
*   **Daily cap and cooldown.** A configurable ceiling on posts per day, and a minimum gap before the same group can be posted to again. The app refuses to exceed them.
*   **Content variation is enforced, not optional.** This is the highest-value protection in the whole system, because Meta's Spam policy explicitly restricts accounts at *low* frequencies when repetitive content is present (see §8). The app must warn before sending byte-identical text to more than two groups in a batch, show a per-batch similarity indicator, and make editing per-group text easy rather than an afterthought. Identical text plus an identical link across many groups is the strongest spam signal available and must be actively discouraged in the UI.
*   **Human-hours only.** Posts are scheduled within normal waking hours; no 4 AM activity.
*   **Groups that hold posts for approval are recognised, not guessed at.** A group with post approval on accepts the post, closes the composer, and keeps it out of the feed, so verification finds nothing. Reading that as failure was wrong in both directions — it produced a confident "done" for an invisible post on one run and a halted batch on the next, purely on timing. The app now reads the group's "Pending admin approval" banner, and only after confirming the post is genuinely absent. Such a post is marked awaiting approval, the batch continues to the other groups, and the wording still starts the cooldown and counts against the repeat guard and the daily cap — because it will appear the moment an admin approves, and forgetting it would invite sending the same text again.
*   **Pending posts resolve themselves.** Every few hours, inside posting hours only, the app checks the group's "Your content" page for anything it submitted to a moderated group: still listed as pending means wait, in the feed means approved, gone from both means declined — and declined releases the wording so it can be sent again. It takes two consecutive checks to call something declined, because a page that fails to render looks exactly like an empty pending list.
*   **Stop on anomaly.** If a checkpoint, CAPTCHA, "you're posting too fast" warning, or unexpected page appears, the worker halts the entire batch and surfaces it to the user. It never retries blindly.

## 8. Known Risks & What Meta Actually Says

Researched against Meta's own published policies. **There is no official "safe automation standard" to comply with — Meta prohibits this outright and publishes no rate limits.** Any third-party blog quoting "safe limits" (X posts/hour, Y groups/day) is inventing numbers; none come from Meta.

What the official sources do establish:

*   **Terms of Service §3.2** — accessing or collecting data from Meta products by automated means without prior written permission is prohibited, as is circumventing technological access controls. Meta may suspend or disable accounts for violations.
*   **Account Integrity policy** — prohibits "creating or using an account or other entity through automated means, such as scripting (unless the scripting activity occurs through authorized routes)." Browser automation of a member account is not an authorized route.
*   **Enforcement is discretionary** — Meta states action may be taken "in our sole discretion," is "both automated and manual," and can happen "at any time... with or without notice to you."
*   **No sanctioned alternative exists.** The Facebook Groups API — including the `publish_to_groups` permission — was deprecated in v19 (January 2024) and removed from all API versions on **22 April 2024**. There is no supported programmatic way to post to groups. This is why every remaining tool in this space is browser-based, and it means Meta closed this door deliberately.
*   **Native scheduling exists for group admins — but does not apply here.** Facebook's own composer can schedule posts in groups where you are an admin or moderator, with no automation and no risk. Confirmed that the user is a plain member of all target groups, so this shortcut is unavailable and browser automation is genuinely the only option.

**The single most important finding for this project:** Meta's Spam policy flags high-frequency posting, but adds that it "may place restrictions on accounts that are acting at **lower frequencies** when other indicators of Spam (e.g., **posting repetitive content**) or signals of inauthenticity are present."

At 2-3 posts/day this project is nowhere near a frequency threshold. The realistic ban vector is **repetitive content**, not volume — which makes the varied-content usage pattern the most protective decision already made, and makes it a rule the app should enforce rather than merely allow (see §7).

The delay ranges in §7 are a conservative engineering judgement, not a Meta-published figure. No such figure exists.

DOM selectors are a separate ongoing risk: Facebook's class names are obfuscated and change frequently. Selectors are role- and `aria-label`-based rather than class-based, and they depend on the account's UI language. **English, Hebrew and Russian are supported**, each string read off the live site rather than translated — Russian's post button turned out to be `Отправить` ("send"), not the obvious `Опубликовать`. Adding a language means probing a real group, not consulting a dictionary.

## 9. Data Model (SQLite, as built)
Stored at `C:\FBAutomation\fbposter.db`, beside the Chrome profile. Migrations are keyed on `PRAGMA user_version`, so the schema can change without wiping stored groups.

*   `groups` — identifier (UNIQUE), url, name, **cooldown_hours**, last_posted_at, notes, archived, created_at
*   `templates` — name (UNIQUE), body, media_paths, created_at, updated_at
*   `tasks` — body, media_paths, scheduled_for (NULL = post now), state, created_at / started_at / finished_at, error
*   `task_targets` — task_id, group_id, position, **body**, state, attempted_at, posted_at, post_url, error, **`UNIQUE(task_id, group_id)`**
*   `settings` — key/value: daily cap, posting window, default cooldown

Two columns carry most of the safety weight. **`UNIQUE(task_id, group_id)`** means the database itself refuses to let one batch target the same group twice — a duplicate post is the worst failure mode here, so it is not left to application code. **`task_targets.body`** is per-group rather than per-batch, which is what makes content variation expressible at all.

`tags`, `group_tags` and `run_log` from the original sketch were cut (see §10).

### Safety settings (confirmed)
*   **Daily cap: 25** individual group posts — just above the realistic ceiling of 3 posts x 7 groups, so it catches a runaway rather than normal use.
*   **Posting window: 08:00–23:00.**
*   **Cooldown: per-group**, default 24h, editable on the Groups screen. Large, active groups can be lowered to a few hours. New groups start conservative because a too-tight default costs a warning while a too-loose one costs the account.
*   **Identical text is never sent to the same group twice**, regardless of cooldown. This is the rule that does the real work: posting more often to an active group is only safe while the wording changes.

## 10. Scope Control

The workload in §2 is small — a few posts a day to a handful of groups. Several features in the original spec are sized for a much larger operation and add real complexity for little benefit. **Deliberately cut from v1:**

| Cut | Why |
| --- | --- |
| ~~Recurring / cron scheduling~~ — **built after v1** | Cut originally because next-run computation, DST and catch-up-on-missed-runs are a meaningful chunk of work, and the user was choosing content each time anyway. Reinstated at the user's request: daily, at up to three times of day. The content problem is what made it more than a date calculation — see §7, identical text is never sent to the same group twice — so a schedule holds *several* wordings and rotates them. |
| "Groups of Groups" tags | Tagging pays off at 50+ groups. With ~5-15 total, a checkbox multi-select list does the same job and removes two tables plus a management screen. |
| `run_log` table | `task_targets` already stores per-group status, timestamp, and error. A second history table is redundant. |
| Video upload | Uploads are slow and progress/completion detection is flaky. Images only in v1; video is a clean addition later. |
| Pause / skip mid-batch | Cancel-the-batch is genuinely needed. Pause and per-group skip are polish. |

**Kept, because they are cheap or essential:** templates (one table, save/load), daily cap (a counter), the idempotent SQLite queue, halt-on-anomaly, and the content-variation warning.

This makes v1 roughly: *compose → pick groups from a list → post now or at one scheduled time → serial worker with jitter → live queue view.*

## 11. Development Roadmap
1.  **Phase 1 — Environment & Connection.** Dedicated Chrome profile, launcher, CDP attach, "Test Connection" verifying a live Facebook session.
2.  **Phase 2 — UI Shell.** CustomTkinter app with the v1 tabs, navigation, and styling. Static, no logic yet.
3.  **Phase 3 — Local Storage.** SQLite schema and binding UI inputs to Groups / Templates / Tasks.
4.  **Phase 4 — Automation Engine.** Navigate to a group, locate the composer, type text, attach media, publish, verify the post actually appeared.
5.  **Phase 5 — Scheduler & Safety.** Background worker, persistent queue, jitter, daily cap, anomaly detection, live queue UI.

**All five phases are complete.** The app runs a batch end to end on its own: the worker starts with the GUI, drains the queue one group at a time, waits a randomised 10–25 minutes between groups, defers past the posting window rather than posting late, holds off system sleep while a batch is in flight, and resumes rather than repeats after a crash.

**Since v1:** per-group text editing, the Compose preview, the move from Tkinter to Qt (Tk 8.6 has no bidirectional text support, so mixed Hebrew/English lines rendered mirrored), and repeating posts.

Post-v1, in likely order of value: a variation-suggestion helper (generate light rewordings rather than typing each by hand), video, per-group skip, tags.


## 12. Open Questions
*   ~~Facebook UI language~~ — **English, Hebrew and Russian are all supported** (settled). Every selector string was read off the live site in each language rather than translated; see `fbposter/strings.py`.
*   ~~Media: single or multiple?~~ — **multiple images per post** (settled). `set_input_files` takes a list, so it costs nothing over single.
*   ~~Posting window~~ — **08:00–23:00 Israel time** (settled). All window and daily-cap decisions are made in `Asia/Jerusalem`, never UTC; see `fbposter/clock.py`.
