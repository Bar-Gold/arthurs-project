---
paths:
  - "fbposter/qtui/**"
  - "tests/test_qtui_*.py"
  - "tests/conftest.py"
---

# Any UI code

Moved out of the root `CLAUDE.md`, which still applies. This file loads by itself when Claude reads or edits a file listed above.

## Hebrew, and why the UI is Qt

**`fbposter/qtui/` is the only UI.** The original CustomTkinter window (`fbposter/ui/`, `main.py gui --tk`) was removed on 2026-09-30; the one piece of it the app still used, the connection check, is now `fbposter/connection.py`.

The move was forced by Hebrew, which is most of what this app is used to write. **Tk 8.6 has no bidirectional text support at all**: it lays characters out in logical order, left to right, always: `Text.bbox()` proves it, and no tag, `justify`, RLM, RLE or RLI moves a single x-coordinate. What made Hebrew look right in Tk was Windows reordering each *run* it draws (one unbroken stretch of one script), so a pure-Hebrew line came out fine while any line mixing Hebrew with English or digits came out a **mirror image** of the truth. Three rounds of increasingly elaborate workarounds in Tk (per-line justify tags, an invisible U+202B embedding, `python-bidi` reordering in the preview), never got the editor right.

Qt shapes text itself and needs none of it. `qtui/views/compose.py` contains **no direction code whatsoever**, and a plain `QTextEdit` renders the mixed sentence identically to Facebook, aligning Hebrew paragraphs right on its own. Do not add direction code (bidi marks, reordering) to `qtui/`; if Hebrew ever looks wrong there, the cause is something else.

**Verifying anything about Hebrew rendering:** never read glyph order off a screenshot. That produced two confidently wrong diagnoses in a row. Split the image into halves and identify an unambiguous anchor (a Latin word, a digit run), or compare pixels against a known-correct rendering. In the test sentence "…אני רוצה … kalofan והמחיר … 1000 שקל", correct output puts `אני` at the far right and `1000` in the left half.

## UI rules

The window's own rules (the flow, redrawing, the Compose preview, the visual rules) are in **`fbposter/qtui/CLAUDE.md`**. What follows is the part that holds for any UI code.

- **Only the main thread touches widgets.** Blocking work goes through a background thread → `queue.Queue` → a pump on the UI thread: `App.run_in_background`, and a `QTimer` driving `App._drain_worker_events`. The posting worker reports progress the same way and never touches a widget itself.
- **No modal dialogs for status, ever**. Use `app.toast`. There are exactly three permitted, and all three share one justification: they can only appear because the user just acted, so the app already has focus and they cannot interrupt anything. One is the media file picker, in Compose and on the repeating-post edit screen. The second is `qtui.app.ask_before_closing`, raised only from `closeEvent`, only when `App.unfinished_work()` finds something still due. See the close rule below. The third is the single-instance refusal in `run()`, which exists because the packaged `.exe` has no console for the `print` it used to be. Chrome's native file dialog is a different thing entirely and is never acceptable. See the Photo/video rule below. Windows' own administrator prompt (UAC) is not the app's dialog, and appears on the same terms: only straight after the user changes "Start with Windows" and Windows has refused the change without it (see Power).
- **Closing the window stops the posting, so it says so first.** The worker is the window's own thread; `closeEvent` stopped it silently, so a daily repeat set up and then closed away simply never ran again with nothing on screen to show it. `App.unfinished_work()` returns a phrase naming what is still due (unfinished batches, active schedules), or `None`, and only a non-`None` answer costs the user a dialog. **The confirmation is injectable (`App(confirm_close=)`) and defaults to the real one**, exactly like `check_fn` and `group_namer`: the GUI suite closes every window it builds, so a real modal would hang the run rather than fail it. It is also skipped entirely while `self.worker is None`, which is every window a test builds.
- **Compose owns the post's versions, and `body_for()` (or `bodies()` for every group at once) is the only way to read a group's text.** Since 2026-10-23 (the user's choice) a post is written as Version 1, 2, 3 in step 1, with no groups needed: `_versions` holds them, `_editing` is the open tab, and `_chosen` holds the version a group was given by hand on step 3. The rest are shared out by `assignment()`, through `recurrence.pick_index`, the rule repeating posts use: the group at place n starts at version n, and gets one it has not had that Israel day when there is one (`GroupRepo.sent_on_day_many`, one query for every group). It used to be a tab per group, which could only be filled in after picking the groups in step 2 and going back. Every version stands on its own: changing one never clears another. `body_for()` reads committed state only, so `capture()` must run first. It once returned the live editor contents when the tab was open, which handed back the wrong text as soon as `_editing` was assigned before the read. A tab switch must never cost someone their wording.
- **Anything in a view that reaches for a browser must be injectable, and the shared test App must be given a stub.** The Groups view looks up group names on its own whenever it is shown, and `chrome.probe()` succeeds on any machine with Chrome running, so before `SilentNamer` existed, the GUI suite silently opened real Facebook pages and took nearly three minutes instead of twenty seconds. The `App` takes `check_fn=`, `db=` and `group_namer=` for this reason.
