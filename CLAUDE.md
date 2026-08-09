# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Status

**Greenfield — no source code exists yet.** The repo currently contains only `README.md`, which is the design spec. Work begins at Phase 1 of the roadmap in that file. There is no build tooling, no dependency manifest, and no test suite yet; this section should be replaced with real commands once they exist.

## What This Is

A local, single-user Windows desktop app (Python 3.10+, CustomTkinter) that posts text and media to Facebook groups on a schedule, by driving a real logged-in Chrome session through Playwright over CDP. Everything runs on the user's machine — no server, no cloud, no automated login.

## Commands

None yet. When adding tooling, the two non-obvious ones to document here are the Playwright browser install step and the Chrome debug-profile launch (below).

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

**Open question blocking Phase 4: is the account's Facebook UI in English or Hebrew?** Do not write selectors until this is settled.

## Known Context

Automating posts violates Facebook's Terms of Service. The measures above reduce detection risk but do not eliminate it; this tradeoff is understood and accepted by the user, and is documented in the Known Risks section of `README.md`.
