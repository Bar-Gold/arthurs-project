"""Every Facebook-facing URL and UI string the automation depends on.

Facebook's DOM class names are obfuscated and change between builds, so
selectors are built from roles and accessible names instead. That makes them
language-dependent.

**The account's interface is Hebrew.** This was confirmed by probing a real
group: every accessible name comes back in Hebrew, and `?locale=en_US` on the
URL does not override the account setting. Hebrew names are therefore listed
first and English kept as a fallback, so the app survives the account language
changing or Facebook serving English on some surface.

Hebrew labels also come back in gendered forms for this account, which is
another reason to match a list of candidates rather than one exact string.
"""

from __future__ import annotations

BASE_URL = "https://www.facebook.com"
HOME_URL = f"{BASE_URL}/"

# Facebook sets this cookie for a logged-in account; its value is the numeric
# user id. Checking for it beats any DOM check -- no markup, no language, no
# navigation required.
LOGIN_COOKIE = "c_user"

# URL fragments Facebook redirects to when a session is not usable. Matched
# case-insensitively as substrings of the landed URL.
CHECKPOINT_MARKERS = ("/checkpoint/", "/challenge/", "/confirmemail")
LOGIN_MARKERS = ("/login", "login.php", "/recover/")

def group_url(identifier: str) -> str:
    return f"{BASE_URL}/groups/{identifier}/"


# --- Composer ---------------------------------------------------------------
# Tried in order. The group composer trigger carries no aria-label at all -- it
# is matched on its visible text.
COMPOSER_TRIGGERS = (
    "כאן כותבים",  # "write here"
    "כתבי משהו",
    "כתוב משהו",
    "Write something...",
    "Write something to the group...",
    "Write something",
    "Create post",
)

# The composer text field exposes no accessible name, only an aria-placeholder,
# so it is found as the dialog's only textbox. These are kept for the fallback
# lookup and for recognising the field when several are present.
COMPOSER_TEXTBOX = (
    "יצירת פוסט ציבורי",  # "create public post"
    "כתבי משהו",
    "Write something...",
    "Create post",
)

POST_BUTTONS = ("פרסום", "Post")  # "publish"
PHOTO_VIDEO_BUTTONS = ("תמונה או סרטון", "Photo/video", "Photo or video")
CLOSE_BUTTONS = ("סגירת תיבת הדו-שיח של המחבר", "Close")

# Closing a composer that has text in it can make Facebook ask what to do with
# the draft. Probing this account showed no prompt for a short post -- Escape
# simply closed the composer and the draft was gone on reopen -- but the prompt
# is known to appear in other situations, so the candidates stay.
#
# These specific strings are UNVERIFIED against the live site; the flow does not
# depend on them, because the composer is cleared before typing either way.
DISCARD_PROMPT_BUTTONS = (
    "מחיקה",  # "delete"
    "מחק",
    "השלכה",
    "Discard post",
    "Discard",
)

# --- Anomalies --------------------------------------------------------------
# Matched case-insensitively against the page text. Any hit halts the batch:
# the app never waits one out and never retries through it.
RATE_LIMIT_MARKERS = (
    "you're temporarily blocked",
    "you are temporarily blocked",
    "temporarily blocked",
    "posting too fast",
    "you're posting too",
    "try again later",
    "we limit how often",
    "this feature isn't available right now",
    "slow down",
)

UNAVAILABLE_MARKERS = (
    "this content isn't available",
    "content isn't available right now",
    "this group isn't available",
    "you must be a member",
    "isn't available at the moment",
)
