"""Entry point for the packaged .exe.

Deliberately tiny. Almost everything it does is start the Qt window, which is
what the client's Start Menu shortcut points at -- there is no console behind
it, so anything that wanted to report a problem by printing would report it to
nobody. `fbposter.qtui.app.run()` is written for that: the single-instance
refusal is a dialog, Chrome is started on a background thread after the window
is up, and whatever is still missing is explained by the setup wizard.

The one addition is `--selftest`, which exists for the support call. Four
things can be absent from a frozen build and every one of them fails silently
at runtime rather than at startup -- see "Handing this to a non-technical user"
in CLAUDE.md. Asking a non-technical client to describe a symptom that is
"the tick is missing from a checkbox" is hopeless; asking them to run one
shortcut and send back a text file is not.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Running from source (python packaging/entry.py) needs the repo root on the
# path; running frozen does not, and this is harmless there.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def selftest(show_dialog: bool = True) -> int:
    """Check the things a broken bundle gets wrong. Touches no user data.

    Deliberately does not open the database: a diagnostic that creates what it
    is inspecting is not a diagnostic. It reports the path and whether a file
    is there.
    """
    lines: list[str] = []
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        if not ok:
            failures += 1
        mark = "ok  " if ok else "FAIL"
        lines.append(f"[{mark}] {name}" + (f"  --  {detail}" if detail else ""))

    lines.append("Facebook Auto-Poster -- self test")
    lines.append(f"frozen: {getattr(sys, 'frozen', False)}")
    lines.append(f"exe:    {sys.executable}")
    lines.append("")

    # 1. The time zone database. Without it Asia/Jerusalem does not resolve and
    #    every posting-window decision silently moves by hours.
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("Asia/Jerusalem")
        check("Time zone database (Asia/Jerusalem)", True)
    except Exception as exc:
        check("Time zone database (Asia/Jerusalem)", False, f"{type(exc).__name__}: {exc}")

    # 2. The Playwright driver. The app never launches a browser, but it cannot
    #    reach the user's Chrome without this.
    try:
        from playwright._impl._driver import compute_driver_executable

        driver = Path(compute_driver_executable()[0])
        check("Playwright driver", driver.exists(), str(driver))
    except Exception as exc:
        check("Playwright driver", False, f"{type(exc).__name__}: {exc}")

    # 3. Qt, and the SVG the stylesheet points at. A missing asset here costs
    #    the checkbox tick on the one screen whose job is picking groups.
    try:
        from fbposter.qtui import theme

        icon = Path(theme.CHECK_ICON)
        check("Qt theme loads", True)
        check("Checkbox tick (check.svg)", icon.exists(), str(icon))
    except Exception as exc:
        check("Qt theme loads", False, f"{type(exc).__name__}: {exc}")

    # 4. Chrome, and where the user's data lives.
    try:
        from fbposter import chrome, config, login

        check("Google Chrome installed", login.chrome_installed())
        db = config.database_path()
        lines.append(f"       database: {db}  ({'exists' if db.exists() else 'not created yet'})")
        version = chrome.probe()
        lines.append(
            f"       automation Chrome: "
            + (version.get("Browser", "running") if version else "not running")
        )
    except Exception as exc:
        check("Chrome check", False, f"{type(exc).__name__}: {exc}")

    lines.append("")
    lines.append("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
    report = "\n".join(lines)

    # Written beside the user's data rather than beside the .exe: Program Files
    # is not writable by the account the app runs as.
    written = ""
    try:
        from fbposter import config

        path = config.resolve_profile_dir().parent / "selftest.txt"
        path.write_text(report, encoding="utf-8")
        written = f"\n\nSaved to:\n{path}"
    except Exception:
        pass

    print(report)
    if not show_dialog:
        # --quiet: for a build script checking its own output, where a modal
        # would simply hang the run.
        return 0 if failures == 0 else 1

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv)
        box = QMessageBox(
            QMessageBox.Information if failures == 0 else QMessageBox.Warning,
            "Facebook Auto-Poster - self test",
            report + written,
        )
        box.exec()
        del app
    except Exception:
        pass

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest(show_dialog="--quiet" not in sys.argv[1:]))

    from fbposter.qtui.app import run

    sys.exit(run())
