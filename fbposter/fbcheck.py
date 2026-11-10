"""Has Facebook moved anything the app uses? (`main.py check-facebook`)

The user's request, 2026-11-10: Facebook moves buttons around, and "check if
facebook moved something" has to mean one command that walks every place the
app reads or clicks on a Facebook page and says, for each, whether it still
works. These are those places, in the order the app meets them.

**It never posts unless asked.** Everything but the last step is read only or
a draft that is thrown away: the post box is opened in the test group, words
typed, a picture and a video attached, a second group ticked in Add groups,
and the draft discarded. Post is never clicked. `--post` adds one real post to
the test group, for the three things only a real post shows (Facebook's reply
to the Post click, the post's own link, and the post on the "Your content"
page); the user asked to be asked first, every time (2026-11-10).

**A checkpoint, a login page or a block stops the check at once**, as it stops
a batch. Nothing here clicks through one.

`COVERED` and `NOT_CHECKABLE` together name every Facebook string, every page
script and every step of `GroupPoster` the app has, and
`tests/test_fbcheck.py` fails when one is added that neither names. That is
what keeps "everything" true as the app grows.
"""

from __future__ import annotations

import struct
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import session, strings
from .automation import detect, groupfinder, groupinfo
from .automation.humanize import Humanizer
from .errors import AutomationHalted

# The test groups (the user's choices, 2026-11-10): TRY is posted in, and
# bar-test is the group ticked in Add groups. Both are the developer's own.
TEST_GROUP_URL = strings.group_url("1697911281266837")
TEST_GROUP_NAME = "TRY"
SECOND_GROUP_NAME = "bar-test"
# Five seconds, 647 KB: long enough that Facebook shows its upload bar.
TEST_VIDEO = Path(__file__).resolve().parent.parent / "checkup" / "five_second.mp4"
# A group number nobody has, for Facebook's "isn't available" page.
MISSING_GROUP_URL = strings.group_url("100000000000001")
# How long the upload bar is looked for after the files are set.
UPLOAD_BAR_WAIT_S = 15.0
UPLOAD_BAR_POLL_MS = 200
# How long a page's words are waited for once it has loaded.
PAGE_WORDS_WAIT_S = 10.0

OK = "ok"
FAILED = "FAILED"
SKIPPED = "skipped"


class CheckFailed(Exception):
    """What a step expected and did not find."""


@dataclass
class Options:
    group_url: str = TEST_GROUP_URL
    group_name: str = TEST_GROUP_NAME
    second_name: str = SECOND_GROUP_NAME
    video: Path | None = TEST_VIDEO
    post: bool = False


@dataclass
class Run:
    """One check-up: the page every step uses, and what the steps found."""

    context: Any
    page: Any
    options: Options
    humanizer: Humanizer | None = None
    # Shared between steps: the draft's poster and post window, the picture.
    state: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Check:
    key: str
    title: str
    run: Callable[[Run], str]
    # The Facebook strings, page scripts and poster steps this proves still work.
    covers: tuple[str, ...] = ()
    # Steps that must have passed first; otherwise this one is skipped.
    needs: tuple[str, ...] = ()
    # Makes a real post. Only with `--post`.
    real_post: bool = False


@dataclass(frozen=True)
class Result:
    key: str
    title: str
    status: str
    detail: str = ""


def _expect(condition: bool, problem: str) -> None:
    if not condition:
        raise CheckFailed(problem)


def _guard(page: Any, *, unavailable_ok: bool = False) -> detect.PageVerdict:
    """Stop the whole check on a checkpoint, login page or block."""
    try:
        text = page.inner_text("body")
    except Exception:
        text = ""
    verdict = detect.classify(page.url, text)
    if verdict.is_ok or (unavailable_ok and verdict is detect.PageVerdict.UNAVAILABLE):
        return verdict
    if verdict is detect.PageVerdict.UNAVAILABLE:
        raise CheckFailed(f"Facebook said the page is not available ({page.url}).")
    raise AutomationHalted(verdict, detect.halt_message(verdict))


def _go(page: Any, url: str) -> None:
    page.goto(url, timeout=120_000, wait_until="domcontentloaded")


def _picture() -> Path:
    """A small plain PNG, written without any imaging library."""
    width = height = 64

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + bytes((30, 60, 110)) * width for _ in range(height))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    path = Path(tempfile.gettempdir()) / "fbposter-check-picture.png"
    path.write_bytes(png)
    return path


def _poster(run: Run):
    from .automation.poster import GroupPoster

    if "poster" not in run.state:
        run.state["poster"] = GroupPoster(run.page, run.humanizer, dry_run=True)
    return run.state["poster"]


# -- the steps ---------------------------------------------------------------
def check_login_cookie(run: Run) -> str:
    user = session.find_user_id(run.context.cookies(strings.BASE_URL))
    _expect(user is not None, "No login cookie: nobody is logged in to Facebook in the app's Chrome.")
    return "The login cookie is there."


def check_home(run: Run) -> str:
    _go(run.page, strings.HOME_URL)
    _guard(run.page)
    name = session.find_account_name(run.page.content())
    _expect(bool(name), "The account's name is no longer in the page's data "
            f"({strings.ACCOUNT_DATA_MARKER}, {strings.ACCOUNT_NAME_KEY}). The "
            "connection check would say 'Logged in to Facebook' without the name.")
    return f"Logged in as {name}."


def check_your_groups(run: Run) -> str:
    result = groupfinder.read_your_groups(run.page, run.humanizer)
    _expect(result.ok, result.error)
    _expect(result.expected is not None,
            "The joined list's heading with its count in brackets was not found, so "
            "groups the account left would never be taken off the list.")
    _expect(result.complete,
            f"The page counts {result.expected} groups and {len(result.groups)} were read.")
    names = {g.name.casefold() for g in result.groups}
    for wanted in (run.options.group_name, run.options.second_name):
        _expect(wanted.casefold() in names, f"'{wanted}' is not among the groups read.")
    _expect(all(g.name for g in result.groups),
            "Some groups were read without a name: the name link is not told apart "
            "from the 'View group' button any more.")
    return f"Read all {result.expected} groups, each with its name."


def check_group_page(run: Run) -> str:
    _go(run.page, run.options.group_url)
    _guard(run.page)
    _expect(groupinfo.wait_for_heading(run.page),
            "The group's name heading inside the page's main part never appeared.")
    name = groupinfo.read_name(run.page)
    _expect(name.casefold() == run.options.group_name.casefold(),
            f"The group's name read as '{name}', not '{run.options.group_name}'.")
    members = groupinfo.read_members(run.page)
    _expect(bool(members), "The member count link was not found.")
    return f"Name '{name}', {members}."


def check_unavailable(run: Run) -> str:
    _go(run.page, MISSING_GROUP_URL)
    # The words come a moment after the page: looked for, for a while.
    for _ in range(int(PAGE_WORDS_WAIT_S * 1000 / UPLOAD_BAR_POLL_MS)):
        verdict = _guard(run.page, unavailable_ok=True)
        if verdict is detect.PageVerdict.UNAVAILABLE:
            break
        run.page.wait_for_timeout(UPLOAD_BAR_POLL_MS)
    _expect(verdict is detect.PageVerdict.UNAVAILABLE,
            "A group that does not exist was not recognised as 'not available': "
            "none of the words in UNAVAILABLE_MARKERS is on Facebook's page.")
    return "A missing group reads as not available."


def check_composer(run: Run) -> str:
    from .automation.poster import GroupPoster

    report = GroupPoster(run.page, run.humanizer).probe(run.options.group_url)
    missing = [
        label for label, found in (
            ("the page", report.page_ok),
            ("'Write something' (where a post starts)", report.composer_trigger),
            ("the post window", report.dialog),
            ("the text box", report.textbox),
            ("the Photo/video button", report.photo_button),
            ("the Post button", report.post_button),
        ) if not found
    ]
    _expect(not missing, "Not found: " + ", ".join(missing) + ". " + " ".join(report.notes))
    return "Found where a post starts, the post window, its text box, Photo/video and Post."


def check_typing(run: Run) -> str:
    poster = _poster(run)
    poster.open_group(run.options.group_url)
    dialog = poster.open_composer()
    run.state["dialog"] = dialog
    words = f"App check-up {time.strftime('%Y-%m-%d %H:%M')}, never posted"
    poster.type_body(dialog, words)
    typed = dialog.get_by_role("textbox").first.inner_text()
    _expect(words in " ".join(typed.split()), f"Typed '{words}', the box shows '{typed}'.")
    return "Typed into the post box, and the words are there."


def check_attaching(run: Run) -> str:
    poster = _poster(run)
    dialog = run.state["dialog"]
    files = [_picture()]
    if run.options.video is not None and Path(run.options.video).is_file():
        files.append(Path(run.options.video))
    dialog.locator('input[type="file"]').first.set_input_files([str(p) for p in files])
    saw_bar = False
    if len(files) > 1:
        waited = 0.0
        while waited < UPLOAD_BAR_WAIT_S and not saw_bar:
            saw_bar = poster._upload_progress(dialog) is not None
            if not saw_bar:
                run.page.wait_for_timeout(UPLOAD_BAR_POLL_MS)
                waited += UPLOAD_BAR_POLL_MS / 1000
    poster.wait_for_uploads(dialog)
    run.state["video"] = len(files) > 1
    run.state["saw_bar"] = saw_bar
    return "Attached a picture" + (" and a video" if len(files) > 1 else ", no video given") + "."


def check_upload_bar(run: Run) -> str:
    if not run.state.get("video"):
        raise CheckFailed("No video was attached, so the upload bar could not be looked for.")
    _expect(run.state.get("saw_bar", False),
            "Facebook's upload bar (role progressbar) never showed for the video. The "
            "app would not know to wait, and could click Post mid-upload.")
    return "The video's upload bar showed, and went when the upload finished."


def check_add_groups(run: Run) -> str:
    from .automation.poster import Companion

    poster = _poster(run)
    companion = Companion(group_url="(check-up) " + run.options.second_name,
                          name=run.options.second_name)
    added: list = []
    _expect(poster._find_add_groups(run.state["dialog"]) is not None,
            "No 'Add groups' button in the post window (ADD_GROUPS_BUTTONS): every "
            "post would go to one group at a time.")
    ticked = poster.add_groups(run.state["dialog"], [companion], added)
    why = poster.not_ticked.get(run.options.second_name, "")
    _expect(bool(ticked), f"'{run.options.second_name}' was not ticked: {why or 'unknown'}.")
    return f"Searched for and ticked '{run.options.second_name}', then pressed Done."


def check_post_ready(run: Run) -> str:
    poster = _poster(run)
    dialog = run.state["dialog"]
    _expect(poster._find_post_button(dialog) is not None, "The Post button is gone.")
    _expect(not poster._post_is_disabled(dialog), "The Post button stayed greyed out.")
    return "The Post button is there and could be pressed (it was not)."


def check_discard(run: Run) -> str:
    poster = _poster(run)
    poster.discard()
    run.page.wait_for_timeout(1500)
    dialog = poster._dialog()
    still = False
    try:
        still = dialog.is_visible() and dialog.get_by_role("textbox").count() > 0
    except Exception:
        still = False
    if still:
        # Leave nothing behind: try once more before reporting.
        poster.discard()
    _expect(not still, "The post window did not close, so a draft may be left in the group's "
            "post box (DISCARD_PROMPT_BUTTONS).")
    return "The draft was thrown away and the post window closed."


def check_your_content(run: Run) -> str:
    """Published, then Pending. The page's own words ("Your content") are read
    on Published: in a group that never holds posts, which the test group is,
    Pending reads "This content isn't available right now" (read live
    2026-11-10), as `check_companion` already allows."""
    _go(run.page, strings.my_posted_url(run.options.group_url))
    text = ""
    for _ in range(int(PAGE_WORDS_WAIT_S * 1000 / UPLOAD_BAR_POLL_MS)):
        _guard(run.page)
        text = run.page.inner_text("body").lower()
        if any(marker in text for marker in strings.MY_CONTENT_PAGE_MARKERS):
            break
        run.page.wait_for_timeout(UPLOAD_BAR_POLL_MS)
    _expect(any(marker in text for marker in strings.MY_CONTENT_PAGE_MARKERS),
            "The 'Your content' page loaded without any of MY_CONTENT_PAGE_MARKERS, so "
            "posts waiting for an admin could never be followed up.")
    _go(run.page, strings.my_content_url(run.options.group_url))
    _guard(run.page, unavailable_ok=True)
    return "Published and Pending both load, and the page reads as 'Your content'."


def check_real_post(run: Run) -> str:
    from .automation.poster import Companion, GroupPoster, PostRequest

    poster = GroupPoster(run.page, run.humanizer)
    body = f"App check-up {time.strftime('%Y-%m-%d %H:%M:%S')}. Testing, please ignore."
    outcome = poster.post(PostRequest(group_url=run.options.group_url, body=body))
    _expect(outcome.posted, f"Not posted: {outcome.detail}")
    _expect(bool(outcome.post_url),
            "Posted, but Facebook's reply to the Post click carried no post link "
            f"({strings.POST_CREATED_CALL}): every post would read 'might have posted'.")
    _expect(any(path in outcome.post_url for path in strings.LIVE_POST_PATHS)
            or strings.HELD_POST_PATH in outcome.post_url,
            f"The post's link has an unknown shape: {outcome.post_url}")
    _expect(poster.verify(body, run.options.group_url),
            "The post is not found by its words in the group's feed (the fallback check).")
    found = poster.check_companion(
        Companion(group_url=run.options.group_url, name=run.options.group_name), body
    )
    _expect(found.verified or found.pending,
            "The post is not on the group's 'Your content' pages, which is how the other "
            "groups in a post to several are checked.")
    return f"Posted, proved by Facebook's reply: {outcome.post_url}"


CHECKS: tuple[Check, ...] = (
    Check("login", "Logged in (the login cookie)", check_login_cookie,
          covers=("strings.LOGIN_COOKIE", "strings.BASE_URL")),
    Check("home", "Facebook's home page and the account's name", check_home,
          covers=("strings.HOME_URL", "strings.ACCOUNT_DATA_MARKER", "strings.ACCOUNT_NAME_KEY",
                  "strings.LOGIN_MARKERS"),
          needs=("login",)),
    Check("your_groups", "Reading 'Your groups' (Get my groups from Facebook)", check_your_groups,
          covers=("strings.YOUR_GROUPS_URL", "groupfinder.HAS_LIST", "groupfinder.READ"),
          needs=("home",)),
    Check("group_page", "A group's name and member count", check_group_page,
          covers=("strings.group_url", "groupinfo.HAS_HEADING", "groupinfo.READ_NAME",
                  "groupinfo.READ_MEMBERS", "groupinfo.HAS_MEMBERS"),
          needs=("home",)),
    Check("unavailable", "A group that is gone reads as 'not available'", check_unavailable,
          covers=("strings.UNAVAILABLE_MARKERS",), needs=("home",)),
    Check("composer", "The post box and its buttons", check_composer,
          covers=("strings.COMPOSER_TRIGGERS", "strings.COMPOSER_TEXTBOX",
                  "strings.PHOTO_VIDEO_BUTTONS", "strings.POST_BUTTONS", "GroupPoster.probe"),
          needs=("home",)),
    Check("typing", "Typing a post", check_typing,
          covers=("GroupPoster.open_group", "GroupPoster.open_composer", "GroupPoster.type_body",
                  "GroupPoster.guard"),
          needs=("composer",)),
    Check("attaching", "Attaching a picture and a video", check_attaching,
          covers=("GroupPoster.attach_media", "GroupPoster.wait_for_uploads"),
          needs=("typing",)),
    Check("upload_bar", "The video's upload bar", check_upload_bar, needs=("attaching",)),
    Check("add_groups", "Add groups: ticking a second group", check_add_groups,
          covers=("strings.ADD_GROUPS_BUTTONS", "strings.ADD_GROUPS_DONE", "GroupPoster.add_groups"),
          needs=("attaching",)),
    Check("post_ready", "The Post button, ready (not pressed)", check_post_ready,
          needs=("typing",)),
    Check("discard", "Throwing the draft away", check_discard,
          covers=("strings.DISCARD_PROMPT_BUTTONS", "GroupPoster.discard"),
          needs=("typing",)),
    Check("your_content", "The 'Your content' pages", check_your_content,
          covers=("strings.MY_CONTENT_PATH", "strings.MY_POSTED_PATH", "strings.my_content_url",
                  "strings.my_posted_url", "strings.MY_CONTENT_PAGE_MARKERS"),
          needs=("home",)),
    Check("real_post", "A real post to the test group", check_real_post,
          covers=("strings.GRAPHQL_PATH", "strings.POST_CREATED_CALL", "strings.LIVE_POST_PATHS",
                  "GroupPoster.post", "GroupPoster.publish", "GroupPoster.verify",
                  "GroupPoster.check_companion"),
          needs=("discard",), real_post=True),
)

# What no check can reach on demand, and why. Read these live whenever the
# chance comes (a post held for an admin, a block), never by provoking one.
NOT_CHECKABLE: dict[str, str] = {
    "strings.CHECKPOINT_MARKERS": "Facebook shows a security check only when it wants one.",
    "strings.RATE_LIMIT_MARKERS": "A block cannot be summoned, and provoking one is what the app avoids.",
    "strings.PENDING_APPROVAL_MARKERS": "Needs a post held for an admin; no test group holds posts.",
    "strings.HELD_POST_PATH": "Needs a post held for an admin; no test group holds posts.",
    "GroupPoster.awaiting_approval": "Needs a post held for an admin; no test group holds posts.",
    "GroupPoster.pending_verdict": "Needs a post held for an admin; no test group holds posts.",
    "GroupPoster.live_link": "Needs a post held for an admin, then approved.",
    "strings.CLOSE_BUTTONS": "Nothing in the app uses it.",
}


def all_covered() -> set[str]:
    return {name for check in CHECKS for name in check.covers}


def run_checks(
    context: Any,
    page: Any,
    options: Options,
    checks: tuple[Check, ...] = CHECKS,
    humanizer: Humanizer | None = None,
    report: Callable[[Result], None] = lambda _r: None,
) -> list[Result]:
    """Run every step in order. One failing does not stop the rest; a step
    whose `needs` did not pass is skipped. A checkpoint, login page or block
    stops everything, and the steps not reached say so."""
    run = Run(context=context, page=page, options=options, humanizer=humanizer)
    results: list[Result] = []
    passed: set[str] = set()
    halted = ""
    for check in checks:
        if halted:
            result = Result(check.key, check.title, SKIPPED, "Stopped: " + halted)
        elif check.real_post and not options.post:
            result = Result(check.key, check.title, SKIPPED, "Only with --post (makes a real post).")
        elif any(need not in passed for need in check.needs):
            result = Result(check.key, check.title, SKIPPED,
                            "Needs: " + ", ".join(n for n in check.needs if n not in passed))
        else:
            try:
                detail = check.run(run)
                result = Result(check.key, check.title, OK, detail)
                passed.add(check.key)
            except AutomationHalted as exc:
                halted = str(exc)
                result = Result(check.key, check.title, FAILED, halted)
            except Exception as exc:  # one step failing never stops the rest
                result = Result(check.key, check.title, FAILED, str(exc) or type(exc).__name__)
        results.append(result)
        report(result)
    if run.state.get("dialog") is not None and "discard" not in passed:
        # A step failed with the draft open: never leave it in the group.
        try:
            _poster(run).discard()
        except Exception:
            pass
    return results
