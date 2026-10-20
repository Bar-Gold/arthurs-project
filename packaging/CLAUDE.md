# CLAUDE.md: packaging and the installer

The root `CLAUDE.md` applies here too. This file covers building the client's
installer: `fbposter.spec` (PyInstaller), `installer.iss` (Inno Setup), `build.ps1`,
`entry.py`, and the policy the installer asks the client to accept (`policy.txt`,
`make_policy.py`, `policy.rtf`). The app side of handing it over (the first-run wizard, re-login and
changing account) is app code and stays in the root file under "Handing this to a
non-technical user".

## Build through `build.ps1`, never by hand

**The release number is `AppVersion` in `installer.iss`**, and it names the output file. `fbposter.__version__` (still `0.1.0`) is read by nothing and is not the release number.

The app is shipped to a client as `dist\FacebookAutoPoster-Setup-x.y.z.exe`, built by `packaging\build.ps1`. That script runs the suite first and **refuses to build a release from a red suite**, which is the whole reason to use it rather than calling PyInstaller by hand. It refuses on the same terms when the built bundle fails its own checks: a missing `node.exe`, `tzdata` or `check.svg`, or a non-zero `--selftest`; it used to print a warning and package it anyway, which put the discovery of a silent failure on the person least able to diagnose it. `-Force` packages it regardless, for looking at a broken build and never for a client.

**Six things fail silently in a frozen build, and `build.ps1` checks for five of them:**

| Missing | What it looks like |
| --- | --- |
| `playwright/driver/node.exe` (92MB) | The app opens and can never reach Chrome. Needs `collect_all("playwright")`. The driver is required even though Playwright never launches a browser here. |
| `tzdata` | `Asia/Jerusalem` does not resolve, `clock.posting_zone()` falls back to the machine's zone, and every window decision moves by hours. |
| `qtui/assets/check.svg` | PyInstaller does not collect `.svg` on its own. The box still fills with the accent, so only the tick is lost, on the one screen whose job is picking groups. White on navy. |
| `qtui/assets/fonts/Bellefair-Regular.ttf` | The page titles' serif (2026-10-20). Without it they fall back to Segoe UI: plainer, not broken. Its licence (`OFL.txt`, SIL Open Font License) sits beside it and must ship with it. |
| `qtui/assets/chevron-*.svg` | The arrows on every stepper, drop-down and the calendar. The buttons still work, blank. Six files, one per direction and state; `build.ps1` looks for one and `--selftest` for all of them. |
| **`PySide6.QtSvg`** | No Python code imports it, so it looks safely excludable. Qt needs its image plugin to draw the SVG the *stylesheet* references, and the failure is the same silent missing tick. It is deliberately not in the spec's `excludes`. |

**`FacebookAutoPoster.exe --selftest` is the support call.** All six of the failures above are silent at runtime and none of them is describable by a non-technical client: "the tick is missing from a checkbox" is not a bug report. The selftest resolves `Asia/Jerusalem`, locates the Playwright driver, loads the theme and checks every SVG it points at and the title font, looks for Chrome, and writes the result to `selftest.txt` beside the database; `--quiet` skips the dialog, which is how `build.ps1` asks the bundle to check itself. It deliberately **does not open the database**. A diagnostic that creates the thing it is inspecting is not a diagnostic.

**One-folder, not one-file.** The bundle carries that 92MB `node.exe` plus Qt, and one-file re-extracts the lot to `%TEMP%` on every launch. This app sits open all day waiting for a schedule, so the unpack buys nothing.

**Two Windows traps the installer has to respect:**

- **It must not create `C:\FBAutomation\`.** The installer runs elevated; a directory it creates at the drive root may not be writable by a standard user afterwards, and `config.resolve_profile_dir()` returns an *existing* directory in preference to the `LOCALAPPDATA` fallback, so it would pick the unwritable one and stay there. Let the app create it on first run, as the user.
- **There is no console, so `print()` goes nowhere.** CPython no-ops when `sys.stdout` is `None`, so nothing crashes. It just says nothing. The single-instance refusal was a `print`, which meant double-clicking the shortcut twice made the second copy vanish without a word. It is a `QMessageBox` now, and `QApplication` is constructed *before* the lock is taken so there is something to parent it to. That dialog is the **third** permitted modal, and it earns it the same way the other two do: it can only appear because the user just double-clicked.

**The installer will not install until the account blocking policy is accepted** (the user's request, 2026-10-19: a client whose Facebook account is blocked should have no claim and no refund). It is the licence page (`LicenseFile=policy.rtf`), renamed through `[Messages]`, in Hebrew with English below; the Hebrew wins where they differ. The seller is Bar Goldstein, and the client pays a setup fee and then monthly.

- **Edit `policy.txt`, then run `make_policy.py`**, which writes `policy.rtf`. Never edit the RTF by hand. Plain text cannot run a paragraph right to left, so Hebrew came out aligned left with its full stops on the wrong side; the RTF gives each paragraph its direction from its first letter. `tests/test_policy.py` fails while the RTF is out of date, and checks that both languages quote the accept button (`LicenseAccepted`) word for word and have the same numbered clauses.
- **It is a draft, not checked by a lawyer.** Under the standard contracts law a court can strike a clause that removes all liability, which is why it says "to the extent the law permits" and has a fallback cap (6.3: what the client paid in the last three months). Do not change what it promises without asking the user.
- **Changing the wording means a new version number and date** at the top of both halves (7.3: the binding version is the one accepted at the last install).

**No setup guide is shipped** (the user's choice, 2026-10-19). A text guide used to open in Notepad at the end of the install, which looked unprofessional, and it repeated the Welcome screen while going out of date. The installer's last page says instead that the app walks them through it (`FinishedLabel` in `[Messages]`), and `[InstallDelete]` removes the old guide and its Start Menu shortcut on an update. Do not bring back a readme: what the client needs to know belongs in the app.

**Uninstalling leaves the user's data.** The database, the Chrome profile and therefore the Facebook login all live outside `{app}`, so a reinstall picks up every group, template and posting record, and nobody logs into Facebook again. The uninstaller says so, and always runs `setup_always_on.ps1 -Revert`.

`installer.iss` passes `setup_always_on.ps1` its `-AppPath` and `-SkipPower` flags; the
root file's Power section says what they do and what the script changes. The app's
Settings screen runs the same script from `{app}\scripts\` with `-SkipTask`, `-SkipPower`
and `-Revert` in combination, and reads its exit code, so the script has to stay in
the bundle, and `-Revert` alone must still undo both halves for the uninstaller.
