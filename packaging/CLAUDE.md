# CLAUDE.md — packaging and the installer

The root `CLAUDE.md` applies here too. This file covers building the client's
installer: `fbposter.spec` (PyInstaller), `installer.iss` (Inno Setup), `build.ps1`
and `entry.py`. The app side of handing it over — the first-run wizard, re-login and
changing account — is app code and stays in the root file under "Handing this to a
non-technical user".

## Build through `build.ps1`, never by hand

The app is shipped to a client as `dist\FacebookAutoPoster-Setup-x.y.z.exe`, built by `packaging\build.ps1`. That script runs the suite first and **refuses to build a release from a red suite**, which is the whole reason to use it rather than calling PyInstaller by hand. It refuses on the same terms when the built bundle fails its own checks — a missing `node.exe`, `tzdata` or `check.svg`, or a non-zero `--selftest`; it used to print a warning and package it anyway, which put the discovery of a silent failure on the person least able to diagnose it. `-Force` packages it regardless, for looking at a broken build and never for a client.

**Five things fail silently in a frozen build, and `build.ps1` checks for four of them:**

| Missing | What it looks like |
| --- | --- |
| `playwright/driver/node.exe` (92MB) | The app opens and can never reach Chrome. Needs `collect_all("playwright")` — the driver is required even though Playwright never launches a browser here. |
| `tzdata` | `Asia/Jerusalem` does not resolve, `clock.posting_zone()` falls back to the machine's zone, and every window decision moves by hours. |
| `qtui/assets/check.svg` | PyInstaller does not collect `.svg` on its own. The box still fills with the accent, so only the tick is lost — on the one screen whose job is picking groups. |
| `qtui/assets/chevron-*.svg` | The arrows on every stepper, drop-down and the calendar. The buttons still work, blank. Twelve files, one per direction, mode and state; `build.ps1` looks for one and `--selftest` for all of them. |
| **`PySide6.QtSvg`** | No Python code imports it, so it looks safely excludable. Qt needs its image plugin to draw the SVG the *stylesheet* references, and the failure is the same silent missing tick. It is deliberately not in the spec's `excludes`. |

**`FacebookAutoPoster.exe --selftest` is the support call.** All five of the failures above are silent at runtime and none of them is describable by a non-technical client — "the tick is missing from a checkbox" is not a bug report. The selftest resolves `Asia/Jerusalem`, locates the Playwright driver, loads the theme and checks every SVG it points at, looks for Chrome, and writes the result to `selftest.txt` beside the database; `--quiet` skips the dialog, which is how `build.ps1` asks the bundle to check itself. It deliberately **does not open the database** — a diagnostic that creates the thing it is inspecting is not a diagnostic.

**One-folder, not one-file.** The bundle carries that 92MB `node.exe` plus Qt, and one-file re-extracts the lot to `%TEMP%` on every launch. This app sits open all day waiting for a schedule, so the unpack buys nothing.

**Two Windows traps the installer has to respect:**

- **It must not create `C:\FBAutomation\`.** The installer runs elevated; a directory it creates at the drive root may not be writable by a standard user afterwards — and `config.resolve_profile_dir()` returns an *existing* directory in preference to the `LOCALAPPDATA` fallback, so it would pick the unwritable one and stay there. Let the app create it on first run, as the user.
- **There is no console, so `print()` goes nowhere.** CPython no-ops when `sys.stdout` is `None`, so nothing crashes — it just says nothing. The single-instance refusal was a `print`, which meant double-clicking the shortcut twice made the second copy vanish without a word. It is a `QMessageBox` now, and `QApplication` is constructed *before* the lock is taken so there is something to parent it to. That dialog is the **third** permitted modal, and it earns it the same way the other two do: it can only appear because the user just double-clicked.

**Uninstalling leaves the user's data.** The database, the Chrome profile and therefore the Facebook login all live outside `{app}`, so a reinstall picks up every group, template and posting record, and nobody logs into Facebook again. The uninstaller says so, and always runs `setup_always_on.ps1 -Revert`.

`installer.iss` passes `setup_always_on.ps1` its `-AppPath` and `-SkipPower` flags; the
root file's Power section says what they do and what the script changes.
