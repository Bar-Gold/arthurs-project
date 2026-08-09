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
Clean, tab-based navigation. The UI should stay uncluttered and readable.

*   **Message Creation Tab**
    *   Spacious free-text input.
    *   Attach images or videos.
    *   "Save as Template" storing text + media paths for reuse.
    *   Templates are a starting point, not a lock — the user edits before sending.
*   **Group Management Tab**
    *   Add Facebook group URLs (only groups where the user can post).
    *   "Groups of Groups" — custom tags/categories for one-click batch selection.
    *   Per-group record of the last successful post time, so the UI can warn about posting to the same group too soon.
*   **Scheduling Tab**
    *   "Post Now" for immediate execution.
    *   One-time scheduled posts and recurring posts at set times.
    *   **Live queue view:** what is pending, what is running, what succeeded or failed, and the countdown to the next group.
    *   Ability to pause, skip a group, or cancel a run mid-batch.

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

DOM selectors are a separate ongoing risk: Facebook's class names are obfuscated and change frequently. Selectors must be role- and `aria-label`-based rather than class-based, and they depend on the account's UI language, which must be fixed and known before the automation layer is written.

## 9. Data Model (SQLite, initial sketch)
*   `groups` — id, name, url, last_posted_at, notes
*   `tags` — id, name
*   `group_tags` — group_id, tag_id
*   `templates` — id, name, body, media_paths, created_at
*   `tasks` — id, body, media_paths, schedule_type (now / once / recurring), run_at, cron_spec, status
*   `task_targets` — task_id, group_id, status (pending / posted / failed / skipped), attempted_at, error
*   `run_log` — append-only history for auditing what actually went out and when

## 10. Scope Control

The workload in §2 is small — a few posts a day to a handful of groups. Several features in the original spec are sized for a much larger operation and add real complexity for little benefit. **Deliberately cut from v1:**

| Cut | Why |
| --- | --- |
| Recurring / cron scheduling | Next-run computation, DST, and catch-up-on-missed-runs are a meaningful chunk of work. At 2-3 varied posts a day the user is choosing content each time anyway. "Post now" plus one-time scheduling covers the real use. |
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

Post-v1, in likely order of value: recurring schedules, video, per-group skip, tags.

## 12. Open Questions
*   ~~Facebook UI language~~ — **English** (settled). All selectors target English UI strings. This determines every selector written in Phase 4.
*   Should media be posted as a single attachment or multiple per post?
*   Preferred posting window (e.g. 09:00-22:00)?
