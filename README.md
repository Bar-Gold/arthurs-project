# Facebook Local Auto-Poster

## 1. Project Overview
A lightweight, local desktop application that automates publishing text and media posts to a set of Facebook groups. Built for a single end-user, the system prioritizes account safety, ease of daily use, and a clean, modern GUI.

The app runs entirely on the user's machine and drives a real, already-logged-in Chrome session. There are no cloud proxies, no automated logins, and no traffic from unfamiliar IP addresses.

## 2. Usage Profile (drives every design decision below)
*   **Volume:** one listing to 40-80 groups, once or twice a day. The clients are real-estate agents (2026-10-18); the original spec was 2-3 posts a day to 5-7 groups.
*   **Content:** Varies between runs. The user is not blasting one identical block of text everywhere, though templates will be reused and lightly edited.
*   **Operator present:** The user is at the machine during the day. The app does not need to survive unattended overnight runs, but it must survive a crash or restart without losing queue state.

This is a low-volume, human-paced workload. The system should be tuned for *looking normal*, not for throughput.

## 3. Technical Stack
*   **Language:** Python 3.10+
*   **UI Framework:** **Qt, through PySide6.** CustomTkinter was the original choice and was replaced: Tk 8.6 has no bidirectional text support, so any line mixing Hebrew with English or digits rendered mirrored. Qt shapes the text itself. The old Tk window has since been removed.
*   **Browser Automation:** Playwright (Python, **sync API**) connecting to a running Chrome over CDP via `connect_over_cdp`.
*   **Database:** SQLite (local `.db` file) for groups, templates, one-off and repeating posts, and the queue with its per-group history.
*   **Images:** Qt decodes attachments for the Compose preview itself. Pillow is installed only for the tests, which use it to write test images.
*   **Threading model:** Qt event loop on the main thread; one single worker thread owns Playwright and processes the queue serially. UI and worker communicate through a thread-safe queue: Playwright objects never touch the UI thread.

## 4. Chrome Session Setup (read before Phase 1)
**Chrome 136+ refuses to open `--remote-debugging-port` when running against the default user profile.** This means the app cannot attach to the user's everyday Chrome window.

The working approach is a dedicated Chrome profile, launched by the app:

```
chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\FBAutomation\ChromeProfile"
```

The user logs into Facebook **once** inside that profile. From then on it is a persistent, genuinely human-logged-in session with real cookies on the user's own IP and device. It is simply a separate profile from their daily browsing.

The app must:
*   Detect whether Chrome is already listening on the debug port before launching a second instance.
*   Expose a **"Check connection"** action that attaches over CDP and confirms an active Facebook session. It reads the login cookie as a yes/no; which account is signed in is never recorded.
*   Fail loudly and clearly if the session is logged out or a checkpoint/verification screen is showing, never attempt to click through one.

## 5. Non-Interfering Operation (requirement)
The user must be able to keep using the computer normally while a batch is posting or waiting for a scheduled time. The automation runs in the background and never takes over the machine.

This works because **Playwright drives Chrome through the DevTools protocol, not through OS-level mouse and keyboard input.** Clicks, hovers, and keystrokes are dispatched straight into the page, so the automation window never needs focus and never steals it. Headless mode is not an alternative. It changes the browser fingerprint and defeats the purpose of using a real session.

Requirements:
*   The automation Chrome window uses its own profile and stays out of the way. It is launched off-screen via `--window-position=-32000,-32000`, or parked on a second virtual desktop. Prefer off-screen to minimizing.
*   **Never call `page.bring_to_front()`** or anything else that raises the window.
*   Suppress Chrome's background throttling so an unfocused window still behaves normally:
    `--disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding`
*   Attach media with `set_input_files` on the file input element. Never trigger the native OS file-picker dialog. It is modal and does steal focus.
*   The app's own UI must not force itself to the foreground or open a dialog to report status. Status belongs on the in-app Dashboard or in a passive in-window toast. The only dialogs are ones the user's own action just raised: the image picker, the warning when closing the window would stop queued or repeating posts, and the notice that the app is already running.
*   **Sleep handling:** hold off system sleep while a batch is in flight (`SetThreadExecutionState` with `ES_CONTINUOUS | ES_SYSTEM_REQUIRED`; the display may still turn off). Schedules are stored as absolute timestamps and recomputed on wake, so suspend/resume never drifts. A slot missed while the machine was asleep is surfaced to the user, not fired late in a burst.

## 6. Core Features & UI Requirements
A left sidebar opens on **Home**, a calm page with one button: **Create a post**. That item walks the work in order as three steps of one road, with a step bar on top: **Write the post (what) → Choose the groups (where) → Choose when (when)**. An earlier step can be clicked to go back; forward is only by each step's Next button. A post that goes out or is queued clears the draft and returns to Home. Below them sit the **Dashboard** (how it is going), Group sets, Posting tips and Settings; the logo at the top of the sidebar also leads Home. Appearance follows the Windows theme. The UI stays uncluttered and readable, and never opens a modal dialog to tell the user something. Status goes to a passive in-window toast.

The **connection indicator lives in the sidebar**, not in a screen of its own: it is the "Check connection" action from §4, and nothing else in the app works without it, so it is visible from everywhere.

*   **Step 1, Write the post** (was the Compose screen)
    *   Spacious free-text input with a live character count.
    *   **Per-group wording.** Tabs above the editor ("All groups" plus one per selected group) switch the editor between the shared base text and a version written for that group. This is what makes the content-variation rule in §7 actionable rather than a warning with no remedy. Editing the base text resets the per-group versions, and says so.
    *   Attach pictures and videos, in any mix. A video is uploaded before Post is clicked, however long that takes, and the post stops (nothing posted) if the upload stops moving for 5 minutes. Facebook's own 10 GB video limit is checked when the file is attached.
    *   **Preview.** A `Write | Preview` toggle above the editor shows the post laid out the way the group will see it: avatar, name, text, and the pictures as a 1/2/3/4/+N collage running full-bleed to the card edges, with a Like/Comment/Share row. Deliberately no invented like counts. A single picture keeps its own aspect ratio; the rest crop to fill their tile, which is what stops a row of mixed shapes looking ragged. It shows the post as the group will receive it. The wording of the active tab, wrapped as it will wrap, with the attached images scaled down. Deliberately generic: no imitation of Facebook's chrome and no profile picture, because the question it answers is whether the text and the image look right. It matters most alongside per-group wording, where each group gets different words and reading one back is the only way to check it. An image that cannot be read is drawn as a named tile rather than dropped. It is still going to be uploaded.
    *   "Save as Template" storing text + media paths for reuse.
    *   Templates are a starting point, not a lock. The user edits before sending.
*   **Step 2, Choose the groups**: both the recipient picker for this post and the list itself, deliberately one screen.
    *   Tick the groups this post goes to, by clicking anywhere on a group's row; "Select all" / "Clear"; the count is always visible.
    *   Each group shows its member count as Facebook writes it ("12K members"), read off the group's page once, in the same visit as its name.
    *   The client's groups are read off Facebook by themselves after the first login, and with "Get my groups from Facebook" later; they start ticked, and a removed group is never added back.
    *   A search box narrows the list by name; "Select all" / "Clear" then act on the matches only.
    *   There is no box for adding a group by its link (removed 2026-10-14): every group comes off Facebook.
    *   A removed group can be brought back from "Show removed groups" under the list, with its posting history. It starts ticked.
    *   **Group sets** sit above the list, one box each. Picking a set adds its groups to the ticked ones; they show greyed with the set's name, and are unticked by unpicking the set, never one by one. "Clear" unpicks the sets too.
*   **Group sets**: its own item in the sidebar, below the Dashboard. Make, edit and delete sets: a name and the groups in it.
    *   A repeating post picked with a set follows it: a group added to the set later gets the next run. Now and Once take the set's groups as they are when the button is pressed.
    *   Saving a set never waits for confirmation, and judges nothing: there are no posting rules (§7).
    *   Deleting a set (with Undo) leaves its groups in the repeating posts that used it, as ordinary groups. Removing a group from the app takes it out of every set.
    *   Group addresses are reduced to a canonical group identifier, so the same group cannot be added (or posted to) twice.
    *   Per-group record of the last successful post time, so the UI can warn about posting to the same group too soon.
    *   Cooldown defaults to **8 hours** (lowered from 24, so two or three posts a day to one group is possible).
*   **Step 3, Choose when**: the one place that decides *when*. Compose owns the text and the groups and carries no timing controls at all.
    *   **Now**: straight onto the queue.
    *   **Once**: at a single chosen moment, up to a year ahead. Defaults to the current time; the field ignores the mouse wheel and starts on the minutes, so a stray scroll cannot move a post into a different year.
    *   **Repeat**: daily, or on chosen days of the week, at as many times of day as wanted (Israel local, DST-safe).
        *   Several **wordings** per schedule, shared out across the groups so one run does not send the same text to all of them, and shifted every run. Nothing refuses a repeat.
        *   Pictures or a video alone may repeat too.
        *   **Editable after it is made** (name, wordings, groups, pictures, times and days) from an Edit button on its card. Changes apply from the next run; a batch already queued goes out as it was, and saving never pauses or resumes it.
        *   The Compose text is the first wording; this screen collects alternates.
        *   Pause, resume and delete; the next run is shown before and after creating one.
    *   Nothing warns about timing, frequency or wording: there are no posting rules (§7).
*   **Dashboard** (replaced the Queue screen, 2026-10-14)
    *   **Numbers:** posted today, the last 7 days against the week before, the last 30 days, all time, the success rate, the next scheduled post, active repeating posts, and how many different wordings went out in the last 7 days. Days are Israel days.
    *   **Charts:** posts per day for the last 14 days, and how every post of the last 30 days ended.
    *   **By group:** today, 7 days and all time per group, and its last post.
    *   **Going out:** every batch still to go out, with Cancel (and Undo). Batches from a repeating post are labelled with its name.
    *   **Recent activity:** the last 15 groups the worker reached, with the result and the reason under any that did not go out.
    *   **All time never shrinks.** What the storage retention below deletes is counted first, so the totals stay.
    *   **Retention (storage).** Finished batches are deleted for good after `history_retention_days` (90 by default; 0 disables), once a day, so the database does not grow without bound. It never deletes anything unfinished, and never the newest 20 posted bodies per group. Those are what stop the same wording going to a group twice (§7), so they are kept for ever. `VACUUM` returns the freed space to disk.
*   **Posting tips**: its own item in the sidebar, below Group sets. Ten recommendations for posting safely (wording, how much an account can post, pacing, hours, links, group rules, what to do after a block), in Hebrew, English and Russian, with a language switch. It opens in Hebrew, right to left. Read only: it changes nothing the app does.

**Post-v1 (see §10):** skipping a single group mid-batch. It is listed here only so the shape of the finished app is clear. Recurring schedules, pausing, "Groups of Groups" (built as **group sets**) and video attachments were also cut from v1 and have since been built, at the user's request.

## 7. Account Protection (CRITICAL)
The user posts as an ordinary group member, not an admin, so everything happens through the normal UI. Avoiding automated-behavior signals is the top priority.

*   **Real session only.** No automated logins, ever. The app attaches to a session a human created.
*   **Strictly sequential.** One group at a time, one global worker. Batches never run in parallel and never overlap. A "Post Now" while a batch is running goes to the back of the queue.
*   **Randomized delays.** 2-4 minutes between groups, on top of the minute and a half each post takes, so one listing to 80 groups takes about six hours. It was 10-25 minutes until 2026-10-18; under 2-3 minutes reads as automation.
*   **Human-like interaction.** Randomized scrolling on arrival, hovering before clicking, per-keystroke typing delays with variance. No instant paste of a full text block.
*   **Idempotent queue.** Every group's status is committed to SQLite as it completes. After a crash or restart the batch resumes and never re-posts to a group it already hit. A duplicate post is a strong spam signal.
*   **No posting rules (2026-10-18).** There is no daily cap, no cooldown between posts to a group, no posting window and no repeated-text rule, and nothing warns about them. Whatever the user sets up goes out. The advice that used to be rules is on the Posting tips screen.
*   **Content variation is advice, not a rule.** Meta's Spam policy restricts accounts at *low* frequencies when repetitive content is present (see §8), so it is the first of the Posting tips, and a repeating post rotates its wordings. The app no longer enforces it.
*   **Human hours are advice too.** The tips say to post between about 08:00 and 23:00; the app posts when it is told to.
*   **Groups that hold posts for approval are recognised, not guessed at.** A group with post approval on accepts the post, closes the composer, and keeps it out of the feed, so verification finds nothing. Reading that as failure was wrong in both directions. It produced a confident "done" for an invisible post on one run and a halted batch on the next, purely on timing. The app reads the group's "Pending admin approval" banner, but that banner stays up while *any* of the user's posts is queued, and Facebook shows authors their own queued posts in the feed, so when the post is visible and the banner is up, the app asks the group's list of the user's pending posts which one it is. Such a post is marked awaiting approval, the batch continues to the other groups, and it still counts as sent, because it will appear the moment an admin approves, and forgetting it would invite sending the same text again.
*   **Pending posts resolve themselves.** Every few hours, inside posting hours only, the app checks the group's "Your content" page for anything it submitted to a moderated group: still listed as pending means wait, in the feed means approved, gone from both means declined, and declined means it no longer counts as sent. It takes two consecutive checks to call something declined, because a page that fails to render looks exactly like an empty pending list.
*   **Stop on anomaly.** If a checkpoint, CAPTCHA, "you're posting too fast" warning, or unexpected page appears, the worker halts the entire batch and surfaces it to the user. It never retries blindly.

## 8. Known Risks & What Meta Actually Says

Researched against Meta's own published policies. **There is no official "safe automation standard" to comply with: Meta prohibits this outright and publishes no rate limits.** Any third-party blog quoting "safe limits" (X posts/hour, Y groups/day) is inventing numbers; none come from Meta.

What the official sources do establish:

*   **Terms of Service §3.2**: accessing or collecting data from Meta products by automated means without prior written permission is prohibited, as is circumventing technological access controls. Meta may suspend or disable accounts for violations.
*   **Account Integrity policy**: prohibits "creating or using an account or other entity through automated means, such as scripting (unless the scripting activity occurs through authorized routes)." Browser automation of a member account is not an authorized route.
*   **Enforcement is discretionary**: Meta states action may be taken "in our sole discretion," is "both automated and manual," and can happen "at any time... with or without notice to you."
*   **No sanctioned alternative exists.** The Facebook Groups API (including the `publish_to_groups` permission) was deprecated in v19 (January 2024) and removed from all API versions on **22 April 2024**. There is no supported programmatic way to post to groups. This is why every remaining tool in this space is browser-based, and it means Meta closed this door deliberately.
*   **Native scheduling exists for group admins, but does not apply here.** Facebook's own composer can schedule posts in groups where you are an admin or moderator, with no automation and no risk. Confirmed that the user is a plain member of all target groups, so this shortcut is unavailable and browser automation is genuinely the only option.

**The single most important finding for this project:** Meta's Spam policy flags high-frequency posting, but adds that it "may place restrictions on accounts that are acting at **lower frequencies** when other indicators of Spam (e.g., **posting repetitive content**) or signals of inauthenticity are present."

The original 2-3 posts a day was nowhere near a frequency threshold. One listing to 40-80 groups, once or twice a day, is far above what any guide calls safe, so **repetitive content and a sudden jump in volume are the realistic ban vectors**. The app no longer enforces anything about either (the user's choice, 2026-10-18); the Posting tips screen says how to keep the risk down: several wordings, an established active account, building up slowly.

The delay ranges in §7 are a conservative engineering judgement, not a Meta-published figure. No such figure exists.

DOM selectors are a separate ongoing risk: Facebook's class names are obfuscated and change frequently. Selectors are role- and `aria-label`-based rather than class-based, and they depend on the account's UI language. **English, Hebrew and Russian are supported**, each string read off the live site rather than translated: Russian's post button turned out to be `Отправить` ("send"), not the obvious `Опубликовать`. Adding a language means probing a real group, not consulting a dictionary.

## 9. Data Model (SQLite, as built)
Stored at `C:\FBAutomation\fbposter.db`, beside the Chrome profile (or under `%LOCALAPPDATA%\FBAutomation\` on a machine where that path cannot be used). Migrations are keyed on `PRAGMA user_version`, so the schema can change without wiping stored groups.

*   `groups`: identifier (UNIQUE), url, name, **cooldown_hours**, last_posted_at, notes, **archived**, created_at. Removing a group archives it rather than deleting it, because its posting history is what the Dashboard counts and what tells the worker the same words went to a group before. `cooldown_hours` is no longer read.
*   `templates`: name (UNIQUE), body, media_paths, created_at, updated_at
*   `tasks`: body, media_paths, scheduled_for (NULL = post now), state, created_at / started_at / finished_at, error, resume_at (when a deferred batch may carry on), schedule_id (the repeating post it came from, if any)
*   `task_targets`: task_id, group_id, position, **body**, state, attempted_at, posted_at, post_url, error, resolve_misses, **`UNIQUE(task_id, group_id)`**
*   `schedules`: a repeating post's definition, never a queue entry: name, **bodies** (the wordings it rotates through), media_paths, times and days (Israel local), state, run_count, next_run_at, last_run_at. When one comes due, the worker creates an ordinary `tasks` row from it.
*   `schedule_targets`: schedule_id, group_id, position, `UNIQUE(schedule_id, group_id)`
*   `settings`: key/value: the old rule settings (daily cap and default cooldown, no longer read; the posting window, read only to keep follow-up checks of admin-held posts in the daytime), history retention, the Posting tips language (and an unused queue retention, from the old Queue screen), plus the worker's own bookkeeping (`next_post_after`, `last_prune_at`)

Two columns carry most of the safety weight. **`UNIQUE(task_id, group_id)`** means the database itself refuses to let one batch target the same group twice. A duplicate post is the worst failure mode here, so it is not left to application code. **`task_targets.body`** is per-group rather than per-batch, which is what makes content variation expressible at all.

`tags`, `group_tags` and `run_log` from the original sketch were cut (see §10). Group sets came later in their place, as three tables: `group_sets` (a unique name), `group_set_members` (its groups, in order) and `schedule_sets` (the sets a repeating post follows).

### Posting rules: none (2026-10-18)
There were four, with "Post anyway" to break any of them for one post: a daily cap of 25, an 08:00-23:00 posting window, an 8h cooldown per group, and the same text to a group at most once a day. The user removed all four, and every warning about them, for clients who post one listing to 40-80 groups. Their settings are still stored and seeded; `tasks.overrides` and `schedules.overrides` are still columns. Nothing reads them.

## 10. Scope Control

The workload in §2 is small. A few posts a day to a handful of groups. Several features in the original spec are sized for a much larger operation and add real complexity for little benefit. **Deliberately cut from v1:**

| Cut | Why |
| --- | --- |
| ~~Recurring / cron scheduling~~: **built after v1** | Cut originally because next-run computation, DST and catch-up-on-missed-runs are a meaningful chunk of work, and the user was choosing content each time anyway. Reinstated at the user's request: daily, at up to three times of day. The content problem is what made it more than a date calculation (see §7, identical text is never sent to the same group twice), so a schedule holds *several* wordings and rotates them. |
| ~~"Groups of Groups" tags~~: **built after v1, as group sets** | Cut originally because tagging pays off at 50+ groups, and with ~5-15 a checkbox list did the same job. Reinstated at the user's request (2026-10-15) once the groups were read off Facebook and the list could be long. A set is a named collection of groups, made on its own screen and picked whole. Now and Once take its groups as they are; a repeating post follows the set, so a group added to it later gets the next run. |
| `run_log` table | `task_targets` already stores per-group status, timestamp, and error. A second history table is redundant. |
| ~~Video upload~~: **built after v1** | Cut originally because uploads are slow and their end is hard to detect. Built at the user's request (2026-10-15) after watching it live: Facebook enables Post long before a video has uploaded, so the app waits for the upload's own progress bar to go, and Facebook confirms a video post with a different reply, because it publishes it only once the video is processed. |
| Pause / skip mid-batch | Cancel-the-batch is genuinely needed. Pause and per-group skip are polish. |

**Kept, because they are cheap or essential:** templates (one table, save/load), daily cap (a counter), the idempotent SQLite queue, halt-on-anomaly, and the content-variation warning. (The daily cap and the warning went on 2026-10-18 with the other posting rules.)

This makes v1 roughly: *compose → pick groups from a list → post now or at one scheduled time → serial worker with jitter → live queue view.*

## 11. Development Roadmap
1.  **Phase 1: Environment & Connection.** Dedicated Chrome profile, launcher, CDP attach, "Test Connection" verifying a live Facebook session.
2.  **Phase 2: UI Shell.** CustomTkinter app with the v1 tabs, navigation, and styling. Static, no logic yet.
3.  **Phase 3: Local Storage.** SQLite schema and binding UI inputs to Groups / Templates / Tasks.
4.  **Phase 4: Automation Engine.** Navigate to a group, locate the composer, type text, attach media, publish, verify the post actually appeared.
5.  **Phase 5: Scheduler & Safety.** Background worker, persistent queue, jitter, daily cap, anomaly detection, live queue UI.

**All five phases are complete.** The app runs a batch end to end on its own: the worker starts with the GUI, drains the queue one group at a time, waits a randomised 10-25 minutes between groups, defers past the posting window rather than posting late, holds off system sleep while a batch is in flight, and resumes rather than repeats after a crash.

**Since v1:** per-group text editing, the Compose preview, the move from Tkinter to Qt (Tk 8.6 has no bidirectional text support, so mixed Hebrew/English lines rendered mirrored), and repeating posts.

**Packaged for a client.** `packaging\build.ps1` produces `FacebookAutoPoster-Setup-x.y.z.exe`: a one-folder PyInstaller build wrapped in an Inno Setup installer, with a first-run wizard that walks a non-technical user through installing Chrome, starting it, and logging into Facebook by hand. The installer optionally registers the logon task and the always-on power plan from §5 (awake plugged in with the lid shut; on battery, awake only while the lid is open), and the uninstaller reverts both while deliberately leaving the database, the Chrome profile and therefore the Facebook login in place. The build refuses to run from a failing test suite. The Settings screen is where a client changes which Facebook account the app posts as. The wizard then ends the session and offers the login form. The switch starts the new account fresh: waiting and repeating posts, group sets, the post being written and the whole posting history are deleted, so the Dashboard starts from zero. Once the new account is logged in, the groups list is replaced with its groups. Only the templates are kept.

Post-v1, in likely order of value: a variation-suggestion helper (generate light rewordings rather than typing each by hand), per-group skip. Tags were built, as group sets, and video too.


## 12. Open Questions
*   ~~Facebook UI language~~: **English, Hebrew and Russian are all supported** (settled). Every selector string was read off the live site in each language rather than translated; see `fbposter/strings.py`.
*   ~~Media: single or multiple?~~: **multiple images per post** (settled). `set_input_files` takes a list, so it costs nothing over single.
*   ~~Posting window~~: **08:00-23:00 Israel time** (settled, then removed as a rule on 2026-10-18). It is a recommendation on Posting tips, and the hours the app follows up admin-held posts in, judged in `Asia/Jerusalem`; see `fbposter/clock.py`.
