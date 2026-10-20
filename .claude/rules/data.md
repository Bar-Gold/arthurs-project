---
paths:
  - "fbposter/db/**"
  - "fbposter/stats.py"
  - "fbposter/qtui/views/{dashboard,groups,group_sets}.py"
  - "tests/test_{db,history_prune,group_sets,stats,account_switch}.py"
  - "tests/test_qtui_{dashboard,groups,group_sets,group_names}.py"
---

# The database, history and group sets

Moved out of the root `CLAUDE.md`, which still applies. This file loads by itself when Claude reads or edits a file listed above.

## Database rules

- **`Database.transaction()`, never `with connection:`.** Connections use `isolation_level=None` (autocommit), so `with connection:` commits a transaction that was never begun and a later failure leaves earlier statements written. This already produced an orphan task row once; `tests/test_db.py` guards it.
- **That rule applies to `apply_migrations` too, and it was the one place still breaking it.** A migration that failed part way left its first statement written with `user_version` unmoved, so the next launch re-ran it and stopped on `duplicate column name`. The client's app simply would not open. Each migration and its version bump are now one explicit `BEGIN`/`COMMIT`/`ROLLBACK`. **Never call `executescript` inside one**: it commits whatever transaction is already open before it runs a line, so a script migration wrapped in a transaction is not atomic at all. `_run_script` splits on `sqlite3.complete_statement` and executes statement by statement, which keeps every one of them inside the `BEGIN`.
- **One connection per thread.** The UI thread and the Phase 5 worker both use the database; `Database.connection` is thread-local and WAL is on so a read never blocks on a write.
- **Judgements live in pure modules** (`stats.py`, `recurrence.py`, `onboarding.py`, `keepalive.py`), never in a repository or a view. Repos supply the rows and timestamps; the pure modules work things out from them, so they are testable without a database or a browser.
- The `UNIQUE(task_id, group_id)` index is load-bearing. Do not drop it to "fix" an insert error.
- **Schema changes append a migration to `MIGRATIONS` in `schema.py`; never edit an existing one.** They are keyed on `PRAGMA user_version` and the earlier ones have already run against the user's real database.
- The App takes an injectable `db=`; tests pass a temporary database and must never touch `C:\FBAutomation\fbposter.db`.

## Two retentions, and only one of them deletes

They are separate settings on purpose and are easy to confuse:

| Setting | Default | What it does |
| --- | --- | --- |
| `queue_retention_hours` | 24 | **No longer read.** It hid finished batches on the Queue screen, which the Dashboard replaced. It stays in `DEFAULT_SETTINGS` because the migrations seed from it. |
| `history_retention_days` | 90 | **Deletes** finished batches, permanently. 0 disables it. |

`TaskRepo.prune_history` is the destructive one, and it is deliberately conservative. **It counts what it deletes into `post_totals` first** (migration 013), in the same transaction, so the Dashboard's "all time" never shrinks (see "The Dashboard"). It never touches:

- anything **unfinished**: a batch still due to go out is not history;
- the newest **`RECENT_BODIES_LIMIT` posted bodies per group**, however old, because that is exactly the window `GroupRepo.recent_bodies` reads. That history is what tells the worker a text was sent to a group before (`PostRequest.sent_before`). Every check that finds a post by its words must not be fooled by an older post with the same words. `RECENT_BODIES_LIMIT` is one constant used by both, and `tests/test_history_prune.py` pins that they match.

A consequence worth knowing before "fixing" it: **with few posts to a group, nothing ages out at all**, because every post is still inside the guard's window. That is correct, and the storage involved is trivial: 20 bodies per group, for ever.

`sqlite3` reports `rowcount` as **-1** for a `DELETE` that begins with a CTE, so the prune selects the doomed ids first and deletes by id; a count that silently means "unknown" is worse than no count. `reclaim_space()` runs `VACUUM` **and then `PRAGMA wal_checkpoint(TRUNCATE)`**. Under WAL the rewritten pages sit in the `-wal` file and the main database does not shrink on disk without it.

The worker calls this at most once a day (`PRUNE_EVERY`), tracked in the `last_prune_at` setting rather than in memory so that restarting the app does not re-run it and an app left open for weeks still gets round to it. A failure there is reported and swallowed: housekeeping must never be able to stall the queue.

## Removing a group archives it; it has never been safe to delete one

`GroupRepo.remove` sets `archived = 1`. It used to be a `DELETE`, and
`task_targets.group_id` is `ON DELETE CASCADE`, so removing a group destroyed
every post ever made to it. That history was what the repeated-text rule read
(gone since 2026-10-18), and it is still what `sent_before` reads and the
Dashboard counts. Two clicks in the UI, no warning, and it was silently gone.
The one place a group row is deleted is an account switch (`replace_with`), and
only a group with no history left to lose.

- **`list()` already excludes archived groups**, so every screen needed no
  change; `get()` deliberately still finds them, because the Dashboard has to
  name a group it posted to last week.
- **Anything deciding whether to *post* must call `GroupRepo.active()`**, which
  returns `None` for an archived group. That is what `get()` returning `None`
  used to mean. `worker._attempt`, `worker._fire_schedule` and
  `worker._sweep_pending` all do. Reaching for `get()` in one of those posts to,
  or opens a page for, a group the user removed.
- **The follow-up sweep drops a removed group rather than chasing it**, since
  every check is a real page load and the answer can no longer be acted on. The
  target row stays `awaiting_approval`, which counts as sent (on the Dashboard
  and for `sent_before`) whether or not anyone ever learns what the admin did
  with it.
- **A removed group comes back from the list under the Groups list**
  ("Show removed groups", then "Bring back": `GroupRepo.removed` and
  `bring_back`). Pasting its link again used to be the way, until the paste box
  went (2026-10-14). It brings the history back with it, but not its place in
  any repeating or queued post, and it starts ticked. The toast says so when
  there is history, because the user is otherwise about to wonder why the
  Dashboard already counts posts to this group. `add_from_url` still un-archives too.
- **Removing a group takes it out of every repeating post and every queued
  post, and re-adding it puts it back in neither**. The user's choice
  (2026-09-30 for repeating, 2026-10-01 for queued), reversing the earlier
  "re-adding resumes it". All of it happens in the one transaction that
  archives the group, and `GroupRepo.remove` returns a `Removal` (how many of
  each) so the Groups screen can say what it was taken out of. The `Removal`
  also records exactly what changed, so the banner's **Undo** straight after
  the click (`GroupRepo.restore`) puts the group back into all of it. That is
  taking back a slip, not re-adding: "Bring back" later still restores
  neither, and Undo puts back only what is still as the removal left it.
  - Repeating: its `schedule_targets` rows are deleted. Migration 010 did the
    same for groups removed before. To post there again, the user ticks it on
    the post's Edit screen. A schedule left with *no* groups is paused and
    reported when it next comes due, like one with an unusable repeat rule.
    Otherwise it comes round two or three times a day for ever, posts nothing
    and says nothing.
  - Queued: every `pending` turn in an unfinished batch is marked `skipped`
    with `GROUP_REMOVED` ("Group was removed."), not deleted, so the Dashboard
    says what became of it. A `running` turn is mid-post and is left alone. The
    `WHERE state = 'pending'` is what stops this racing `claim_target`. A batch
    left with nothing to do is finished on the spot: `cancelled` ("Every group
    in it was removed.") if it never started, `done` if it had, as the worker
    would have recorded. Finished batches are history and are not touched.
  - The worker's `active()` checks in `_attempt` and `_fire_schedule` stay as a
    second line; `_attempt` records the same `skipped` / `GROUP_REMOVED`.

## Group sets: one name for several groups, and a live link for repeating posts

The user asked for "Group of Groups" (2026-10-15); it is called **Group sets** on screen. It was cut from v1 as "group tags" and is built now because the Groups list is read off Facebook and can be long. The user's choices, and what follows from them:

- **Made in one place, picked whole elsewhere.** The Group sets screen (its own sidebar item, below the Dashboard) makes, edits and deletes sets. Groups and a repeating post's edit screen pick them, each as one checkbox. A group a picked set brings shows ticked, greyed, with "in <set>" beside it, and **cannot be unticked on its own**: unpick the set. Picking a set adds to the groups already ticked. Clear unpicks the sets too.
- **A set is not a posting path.** Now and Once copy the set's groups into an ordinary batch when the button is pressed (`App.selected_group_ids()` includes them). A repeating post stores the set itself in `schedule_sets`, and `ScheduleRepo` works out `Schedule.recipient_ids` afresh on every read: the post's own groups in their order, then each set's groups in the set's order, each group once, removed groups left out. `_fire_schedule` reads `recipient_ids`, never `group_ids`, which is now only the groups ticked by hand. So **a group added to a set gets the next run** of every post that follows it, and one taken out does not. The worker and the queue never see a set.
- **The order is fixed because it rotates the wordings** (`run_count + position`). `GroupSetRepo.update` keeps the groups already in a set where they were and puts new ones after them, as the edit screen does for a post's own groups.
- **Saving a set is never held up, and judges nothing.** It used to name, in the banner, any rule a following post newly broke; with no rules (2026-10-18) it just says what was saved.
- **Deleting a set keeps posting going.** In one transaction, every post that followed it gets the set's groups as ordinary groups, then the set goes (`SetDeletion` records exactly that). Undo (`GroupSetRepo.restore`) puts the set, its groups and its links back, and takes away the groups the delete added.
- **Removing a group takes it out of every set**, in the same transaction that archives it, exactly as it leaves repeating posts. `Removal.set_rows` lets Undo put it back in its place; "Bring back" later does not. A post whose sets have no groups left is paused when it next comes due, with the usual "no groups left" message.
- **A set needs a name and at least one group.** Names are unique ignoring case (`COLLATE NOCASE`) and are stored with invisible marks stripped, because they are compared.
- Tests: `tests/test_group_sets.py` (storage, removal, deletion, the worker following a set) and `tests/test_qtui_group_sets.py` (the screens).

## The Dashboard replaced the Queue screen

The user asked for it (2026-10-14): a screen of numbers about the posting, in the Queue's place in the sidebar. `fbposter/stats.py` works every number out as pure functions; `qtui/views/dashboard.py` only draws them. What the user chose, and what follows from it:

- **"Sent" is `repo.SENT_STATES`**: posted, waiting for an admin, or might have posted, everywhere the Dashboard counts posts. The "Posted today" tile says "Since midnight, Israel time"; it said "N of 25 allowed today" until the daily limit went (2026-10-18), and the per-group "Can post" column went with the cooldown. Success rate is what went out of what was tried; a skip (a group removed mid-batch) is the app working, not failing, and is left out.
- **Every day is an Israel calendar date** through `clock`, never "now minus 24 hours": "the last 7 days" is seven dates, so a daylight-saving change cannot move a post into another day.
- **"All time" never shrinks, except at an account switch.** The history prune deletes finished batches after 90 days, so before it deletes it adds their counts to `post_totals`, per group and per state, in the same transaction (`TaskRepo.archived_totals`). All time is the rows still stored plus those counts. No foreign key on that table: a cascade would be a way to lose them. Switching Facebook account is the one deliberate reset: it deletes the history and `post_totals`, so the Dashboard starts from zero; see "Changing which Facebook account it posts as".
- **Cancel stayed, the approval buttons did not.** "Going out" lists every unfinished batch with Cancel (and Undo). The "Went live" / "Declined" buttons for a post held by an admin were dropped: the worker follows those up by itself, and one it gives up on stays `awaiting_approval`, which counts as sent for that day only. `TaskRepo.resolve_pending` stays, because the worker uses it.
- **Recent activity replaced the batch history.** The last 15 groups the worker reached, each with its result and the reason under one that did not go out. The Queue's Recent/All toggle, `list_for_queue` and `count_older_than` went with it.
- **The charts are drawn with `QPainter`** (`DayBars`, `OutcomeBar`, `Meter`). `PySide6.QtCharts` is excluded from the packaged build on purpose (`fbposter.spec`), so do not reach for it.
- **Worker events refresh it**, several times a minute while a batch runs, so the tiles and charts update in place and the three lists (by group, going out, recent) are rebuilt only when their own snapshot changed. `tests/test_qtui_performance.py::TestTheDashboardIsNotRebuiltForNothing` pins that.

**Do not "tidy up" by deleting old rows** (an account switch is the one deliberate exception). `task_targets` is what `sent_before` reads and what the Dashboard counts. Deleting history would let an old post in the feed be taken as proof that a new one with the same words went out.
