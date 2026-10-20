---
paths:
  - "fbposter/{chrome,keepalive,tabs,cdp,session,login,onboarding,connection,single,config}.py"
  - "fbposter/qtui/app.py"
  - "fbposter/qtui/views/{welcome,settings}.py"
  - "main.py"
  - "tests/test_{chrome,keepalive,tabs,session,login,onboarding,account_switch,cli}.py"
  - "tests/test_qtui_{welcome,startup,app,settings}.py"
---

# Chrome, setup and the Facebook account

Moved out of the root `CLAUDE.md`, which still applies. This file loads by itself when Claude reads or edits a file listed above.

## Handing this to a non-technical user

The app ships to a client as `dist\FacebookAutoPoster-Setup-x.y.z.exe`. How that is built, and the six things a frozen build loses without a word, are in **`packaging/CLAUDE.md`**. What stays here is the app side, and the one packaging fact that shapes app code: **the packaged app has no console, so `print()` reaches nobody.** Anything the user must see goes through the window.

**The app used to answer "what now?" with terminal commands.** `connection.py` said *"Start it with 'main.py launch'"* and `automation/detect.py` said *"Run 'main.py setup' and sign in again"*, both of which reach the user through the connection pill. They are good developer instructions and useless to somebody holding an `.exe` with no console behind it. `tests/test_onboarding.py::TestNothingTellsTheUserToOpenATerminal` greps the wizard copy, the halt messages and the connection details so a third one cannot be written.

- **`onboarding.py` is pure and `login.py` drives the browser**: the same split `stats.py` has against the Dashboard. What the app decides to tell the user next is worth testing without Chrome, a profile directory or a Facebook session, and it is: 27 tests, half a second.
- **One problem at a time.** `plan()` returns a single `SetupStep`, never a list. A screen reporting four problems at once is four times as intimidating and no more useful, because they have to be fixed in order anyway. There is no point mentioning Facebook when Chrome is not installed.
- **The wizard is a view, not a dialog.** "No modal dialogs for status, ever" still holds. It is in `App.views` but deliberately **not** in `nav_buttons`: setup is something you finish, not a step you return to. The two ways back in are the first launch and the repair button under the pill.
- **`SETUP_COMPLETE_KEY` is a stored fact, not a live check.** The window has to decide where to open *before* it is on screen, and `chrome.probe()` is up to a full second: a second of grey nothing, which reads as a crash. So the wizard shows until a connection check has come back `CONNECTED` **once**, and only `CONNECTED` retires it.
- **Chrome is started after the window is up, never before it.** `App.begin_startup_checks()` is fired by a `QTimer` from `run()`, like `start_worker` and for the same reason. A window a test builds must not launch a browser. `chrome.launch` waits on the debug port for up to `LAUNCH_TIMEOUT_S` (**30 seconds**); in front of the window that is half a minute of nothing.

**The startup check gets exactly one second chance.** `STARTUP_RECHECK_MS` (15s), armed by `begin_startup_checks` and spent by the first result. The reason is the logon task: it starts the app 45 seconds after sign-in, while Windows is still bringing the network up, and the check ends in a real page load, so it can fail for a reason that fixes itself a moment later, leaving a red pill on a machine nobody is sitting at. **One retry, never a loop**: an offline machine must not reopen Facebook every fifteen seconds for ever, and nothing in this app may open Facebook on a timer. Pressing "Check connection" by hand never arms it.

**A background Chrome that closes is started again, by the window, without a loop.** Nothing used to notice Chrome going away while the app stayed open. The user closing it from the taskbar, a crash, or Windows Update left the pill on its last word, and every post waiting on a connection that was never coming back. Now `App.watch_chrome` runs every `keepalive.WATCH_EVERY_S` (20s), armed by `begin_startup_checks` and stopped in `closeEvent`. It asks the debugging port only, which is ~14ms and **never a Facebook page**. If Chrome is gone, the window restarts it off-screen and says so.

- **One connection check per restart, never per tick.** A check is a real page load. It runs after a restart, and when Chrome is seen back while the pill still says it is down. Never on the timer itself.
- **`chrome.launch` holds a lock for the whole launch.** The watcher, the startup check and the wizard's button can all ask at once. Without the lock each saw the port closed and each started a Chrome, and the second just opens another window in the first.
- **A silent port is not a dead Chrome.** Chrome holds `<profile>/lockfile` open, delete-on-close, for as long as it runs. `chrome.profile_in_use` reads that, and the watcher treats "port silent, profile held" as *busy*: it waits, and only warns after `BUSY_LOOKS_BEFORE_TELLING` looks. `chrome.launch` refuses to start a second Chrome on a held profile and gives it `BUSY_GRACE_S` to answer instead. Launching on a profile a busy Chrome still holds hands the launch to it, and Chrome may shut the busy one down to take over. The app's Chrome was once replaced outright at a moment of heavy load, and mid-post that loses the post. The lockfile behaviour was verified live on 2026-09-25.
- **A failed restart backs off, and gives up after `MAX_ATTEMPTS`.** The usual cause is a Chrome already open on the profile *without* the port, so every launch opens yet another window in it. Retrying every 20s would pile them up for ever. The keeper waits 1 minute, then 5, then stops and points the user at Start Chrome. Chrome being seen alive resets the count, whoever started it.
- **A launch that finds Chrome already up means someone else started it, off-screen.** `open_login_window` and `switch_account` check `is_running()` and then launch visibly, but the keep-alive can start Chrome between the two. `launch()` then starts nothing and returns False, and they now *move* the window on screen instead of assuming theirs is the visible one. Before this, the login form opened in an off-screen window.
- **The seams are looked up at call time** (`lambda: login.start_chrome()`), not captured in `__init__`. Tests patch `login.start_chrome` after the window exists; a captured reference ignored the patch, and on a machine without Chrome up it would have launched a real one.

**A Chrome whose port answers can still hang every connection.** A client logged in and saw the light go green. Then the group names never loaded, and "Check connection" sat on "Checking…" until they closed the background Chrome by hand. Reproduced on 2026-09-27 against Chrome 153, on a throwaway Chrome of its own (its own profile, port 9333, no Facebook):

- **While any tab has a JavaScript dialog open, or its page is stuck in a loop, Playwright's `connect_over_cdp` hangs for ever.** Dialogs include alert, confirm and "Leave site?". Its own 30-second timeout does not fire. The check, the name lookup and every post all go through that connect, so all of them hang.
- **`/json/version` goes on answering throughout**, so the keep-alive saw a healthy Chrome.
- **`Runtime.getIsolateId` tells the two apart.** A healthy tab answers in about a millisecond; a stuck one never does. It runs nothing in the page.
- **`Target.closeTarget` on the stuck tab is enough.** A connect already hung on it finishes by itself 0.6s later.

So `tabs.clear_stuck_tabs` runs in three places:

- **In front of every connect**, through `session.before_attach`.
- **From the keep-alive**, every `TAB_SWEEP_EVERY_S` (2 min). That catches a stuck tab before a scheduled post rather than when somebody presses a button.
- **When a check passes `CHECK_DEADLINE_S` (90s)**, the one situation that also gets the restart.

Every look is a few DevTools messages to the app's own Chrome over `cdp.py`; none is a page load, and none reaches Facebook. The rules that make that safe:

- **Only the app's own Chrome, proven.** The process behind the port must have been started with `--user-data-dir=` the app's profile. The command line is read from Windows (`NtQueryInformationProcess`, then `CommandLineToArgvW`), off the PID `SystemInfo.getProcessInfo` names. Anything else answering on the port is asked which process it is and nothing more. Chrome did not write `DevToolsActivePort` into the profile here, so that file cannot be the proof.
- **Two looks, more than ten seconds of silence in all**, before a tab counts as stuck. A Facebook page busy loading on a slow laptop answers the second time.
- **Never the last tab.** Chrome quits when its last tab closes, so a blank one is opened first.
- **Nothing while the worker has a page open.** `LivePoster._page` holds `tabs.posting()`, taken only once its connect has succeeded. A worker hung *in* the connect has no page open, and must not stop the sweep that would free it.
- **The restart is last, not first.** A stalled check first sweeps. Only when there is nothing to close does it restart the app's Chrome, at most once per `STALL_RESTART_EVERY_S` (30 min). Chrome is closed with `Browser.close`, which verifiably works with stuck tabs open (profile released in 1.4s). It is started again only once the port is shut *and* the profile lock is released. Nothing is ever killed.
- **The answer to a check given up on is ignored.** Checks are numbered (`_check_serial`), because the hung one usually does return once Chrome is put right.

**Re-login moves the window; it does not restart Chrome.** This is the part worth reading before changing it. The normal state of this app is a Chrome parked at `-32000,-32000`, so a session that expires later leaves a login form somewhere nobody can reach. Restarting Chrome is the obvious fix and the wrong one: there is no dependable way to close a window the user cannot see, and a restart mid-batch strands it. `login.open_login_window()` sends CDP `Browser.setWindowBounds` instead, and `hide_login_window()` puts it back.

**Both halves of that were verified live against Chrome 151 on 2026-08-21**, because both were assumptions and either one failing leaves the client staring at an empty browser: a page created through `context.new_page()` **survives dropping the CDP connection** (the tab is a real Chrome target and Playwright does not own it), and `Browser.setWindowBounds` moves the window on screen and back off it in both directions. The check used `example.com`, never Facebook, and closed the tab it made. **Moving a window is not `bring_to_front()`**. The banned call raises a page above whatever the user is working in at a moment they did not ask for; this runs only because they just pressed a button that says a Chrome window will open.

## Changing which Facebook account it posts as

`login.switch_account()` drops the profile's cookies and reopens Facebook. That
is the whole browser side, and it is enough because **nothing in the database is
tied to an account** (the login cookie is only ever read as a yes/no and never
stored). Everything the old account had is then deleted on purpose (below).
Before this existed, the only way to do it was renaming `ChromeProfile\` by hand.

- **New account, new everything but the templates** (the user's choices,
  2026-10-19). Once the sign-out has worked, and never before,
  `repo.start_new_account` deletes in one transaction every batch (waiting or
  finished, so the whole posting history), every repeating post, every group
  set, `post_totals`, and every removed group, and clears `last_posted_at` on
  the groups still listed. The window then empties the post being written
  (`CreatePostView.clear`) and closes the Group sets editor. The Dashboard is
  fresh because there is nothing left to count. This is the one deliberate
  exception to "never delete history", and it has a known cost the user
  accepted: `sent_before` forgets the old account's posts, so in a group both
  accounts are in, an old post with the same words can be taken as proof a new
  one went out. It also closes the gap between the switch and the new groups
  being read: nothing is left queued that could post as the new account to the
  old one's groups.
- **The groups list becomes the new account's groups** (until 2026-10-19 a
  switch left the list as it was). A successful sign-out sets
  `groups_replace_pending` and `groups_import_pending`, so the read runs on the
  next `CONNECTED`, and while the replace is pending the read (automatic or the
  button) calls `GroupRepo.replace_with` instead of `import_found`. In one
  transaction: a group the new account is not in is removed as Remove does it
  (`_archive`), then deleted outright unless something was sent to it since the
  switch, so it never sits in the new account's removed groups; a removed group
  it is in comes back, overruling a removal only here; a group in both is not
  touched. No Undo.
- **Only a complete read may replace** (`FoundGroups.complete`: the heading's
  count read, and every group it counts found). A partial read would remove
  groups the account is in, so it changes nothing, says so, and leaves the
  replace pending for the next connection or the button. Unlike the first-login
  import, a failed automatic read here is announced.

- **Cookies, never Facebook's own Log out menu.** Driving that menu would be a
  language-dependent click on obfuscated markup, in the account menu of all
  places, where a mis-resolved selector could press something else entirely. It
  is also the only approach that leaves nothing for the next account to inherit:
  the "recently logged in" chooser is a cookie too.
- **Cleared before the navigation, never after.** Cleared afterwards, the page in
  front of the user is still the old account's feed, and the login form appears
  only if they think to reload it themselves.
- **Nothing is half-done.** A window that will not come on screen signs nobody
  out, and a sign-out that fails never navigates. Landing on the old account's
  feed after being told you were signed out is how somebody posts as the wrong
  person. `_sign_out` therefore raises rather than swallowing, unlike almost
  everything else in `login.py`.
- **It lives on the Settings screen.** It used to be the pill's button whenever
  the connection was fine, one press from "Check connection" on every screen; the
  user moved it. Settings owns the button and the question, and is enabled only
  while `CONNECTED`. The yes calls `WelcomeView.start_switch()` and navigates to
  the wizard, which owns the sign-out, the login that follows and parking the
  window afterwards: one implementation of each. The wizard itself no longer
  offers a switch, and the pill's button is hidden when nothing is wrong.
- **Two presses, and the second is not a dialog.** The question replaces the
  card's text rather than opening over it, so the permitted-modal list stays at
  three.
- **Refused outright while the worker is mid-post**, checked at the first press
  and again at the yes, since a post can start while they read the question.
  Dropping the cookies with a post in the composer fails that post, and the
  batch then halts on a verification that could never have succeeded. It is a
  wait, not a refusal.
- **The app records the sign-out itself** (`App.note_connection`) instead of
  waiting for a check to come back and say so. That is not cosmetic: until the
  app knows, the wizard still believes it is `READY`, and the `READY` branch of
  `WelcomeView.refresh()` parks the very login window the user is about to type
  into. `_switching` exists for exactly that window of time.

## Never call `browser.close()`

**Never call `browser.close()` on a CDP-attached browser.** That Chrome belongs to the user and holds the Facebook login. `session.attach()` is a context manager that simply drops the connection on exit; closing it as a side effect of attaching would take the browser away mid-whatever. The one deliberate close is `tabs._close_owned`, which sends `Browser.close` only to a Chrome proven to be the app's own, and two things use it, both refusing while a post is open: `tabs.restart_chrome`, the last resort for a check that has stalled (at most once in 30 minutes), and `tabs.close_chrome`, run by `closeEvent` once the worker and the Chrome watcher have stopped, because the user chose (2026-10-14) to have the background Chrome close with the app. Nothing posts while the app is shut, and the next start opens Chrome again. `App._close_chrome` is inert until `run()` wires it up, like `_restart_chrome`, or every window the suite closes would close the developer's Chrome. The login is on disk in the profile and survives it, exactly as it survives the user closing the window. `test_keepalive.py` fails on a close anywhere else. Login is checked via the `c_user` cookie rather than the DOM. No navigation, no selectors, no language dependency.
