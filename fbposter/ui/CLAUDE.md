# CLAUDE.md — the legacy Tk UI

The root `CLAUDE.md` applies here too; this file adds the rules that are specific to
`fbposter/ui/`, the **old CustomTkinter window**, kept runnable with `main.py gui --tk`
and still covered by `tests/test_ui.py` (116 tests).

**Build new UI work in `fbposter/qtui/`, not here.** Nothing below applies to Qt, and
several rules are actively wrong there — `qtui/views/compose.py` contains no direction
code whatsoever and must not gain any. See "Hebrew, and why the UI is Qt" in the root
file for why the rewrite happened.

## Bidi: the mirrored-Hebrew apparatus

**Tk 8.6 has no bidirectional text support at all, and this is the single most
misdiagnosed thing in the app.** The root file's "Hebrew, and why the UI is Qt" section
owns the mechanism and the rule for verifying it — read it before believing anything you
see on screen here. What follows is only how `textdir.py` works around it.

- **The editor is fixed with one invisible character, not by reordering.** Windows honours directional formatting at draw time, so a `textdir.RLE_MARK` (U+202B) at the start of a right-to-left line gives that line a right-to-left base and it renders correctly — English and digits included, and across wrapped lines. Pixel-identical to the reordered reference; RLM, RLI and FSI all do nothing, only RLE works. Do **not** try to fix the editor by reordering its text: displaying something other than what is stored breaks the caret and selection.
- **The mark lives in the widget and must never reach the post.** `ComposeView.get_text()` calls `textdir.strip_controls()` and is the only thing that ever reads the box — keep it that way, and keep the character counter and every guard downstream of it. `tests/test_ui.py` asserts the queued `task_targets.body` and saved templates are free of every character in `textdir.BIDI_CONTROLS`.
- **The preview is reordered, because it is the only place that safely can be.** `textdir.to_visual()` reorders one display line with `python-bidi` and wraps it in LRO…PDF, which stops Windows reordering it a second time — without the override the work is silently undone. `preview.py` wraps the text by hand *in logical order first* (`wrap_to_width`) and reorders each resulting line, never the other way round. `text_shown()` returns the logical post, not what the labels hold.
- **Direction is per line, and a line with no strong character inherits from the one above.** Both halves were learned the hard way. One direction for the whole box dragged the English half of a bilingual post to the right-hand edge and was reported as tangled; without inheritance, a `054-1234567` line between two Hebrew ones flies off to the left. `line_directions()` owns both rules.
- **Retagging the textbox relayouts the whole widget (~3.6ms), so `_apply_direction` caches what it applied** and touches Tk only when a line genuinely changes direction. Doing it on every keystroke was reported as typing lag. Measured after the fix: the direction code costs ~0.1ms against Tk's own ~4ms per keystroke on a long post. `tests/test_ui.py` guards the caching.

## Widgets

- **`CTkFrame` defaults to 200x200.** Any frame used as a thin divider, spine or spacer must pass explicit dimensions, or it silently stretches its row (216px queue rows) or draws as a stray 200px line (an "invisible" spacer frame). Both bugs happened; `tests/test_ui.py` guards both. Use `pady` for spacing rather than an empty frame.
- **`CTkButton` is 140px wide unless told otherwise.** About two Compose tabs fit before the strip starts scrolling; `width=10` makes a button shrink to its text instead.
- **Colours are `(light, dark)` tuples in `theme.py`.** Never hardcode a hex value in a widget; a colour defined for only one mode is invisible in the other.
- **The expanding widget in a view must be packed last**, after the fixed controls are anchored with `side="bottom"`. Otherwise it claims the frame and pushes them off-window.
- **Rebuilding a `CTkScrollableFrame`'s children is expensive.** The Compose tab strip skips the rebuild when nothing visible changed; doing it unconditionally on every refresh was measurable across the suite.

## Compose and preview

- **The Compose preview reads committed state, never the editor.** `Write | Preview` swaps `self.editor` and `self.preview` in and out of one slot; `sync_mode()` captures first, and `refresh_preview()` renders `body_for(active tab)`. It is a no-op in Write mode, which is why `refresh_tabs()` can call it unconditionally. A preview showing another group's words would be worse than no preview. `fbposter/ui/preview.py` owns the drawing and must never raise on a bad attachment — a missing or corrupt image draws a named tile, because the file is still going to be uploaded.
- **Pillow and `python-bidi` are optional at runtime, required in `requirements.txt`.** Without Pillow every image falls back to a tile; without `python-bidi` the preview shows the same mirrored text the editor does. Neither may be allowed to raise. Nothing outside `preview.py` reads Pillow, and nothing outside `textdir.to_visual()` reads `python-bidi`.

## Tests

- GUI tests share **one** Tk interpreter for the whole session (`ui_app` in `tests/conftest.py`). Creating a second root, or recreating one after a destroy, fails intermittently on Windows with "Can't find a usable init.tcl". Never call `mainloop()` in a test — pump with `pump_until`.
- Blocking work goes through `BackgroundRunner` in `fbposter/ui/background.py` — worker thread → `queue.Queue` → `widget.after()` pump. `App._pump_worker_events` drains the posting worker's events the same way.
