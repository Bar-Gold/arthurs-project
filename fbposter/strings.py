"""Every Facebook-facing URL and UI string the automation depends on.

Facebook's DOM class names are obfuscated and change between builds, so
selectors are built from roles and accessible names instead. That makes them
language-dependent, and this account's language has already been switched
between Hebrew, English and Russian -- so every lookup tries a list of
candidates and the order implies nothing but speed.

**Every string below was read off the live site, not translated.** That matters
more than it sounds. Russian's post button is "Отправить" (send), while the
obvious translation of "Post" -- and what research suggested -- is
"Опубликовать". Hardcoding the plausible word would have failed on the first
real post. The composer trigger likewise carries no aria-label in any language
and has to be matched on its visible text.

The exception is the anomaly markers at the bottom: a rate-limit warning cannot
be summoned on demand, and deliberately provoking one is exactly what this
project avoids. Those are researched and marked as such.

To add a language: probe a real group with `main.py probe`, dump the composer,
and paste what Facebook actually returns.
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


# Where Facebook lists your own posts in a group, "Pending" first. Verified on
# 2026-08-17: /pending/ is the *admin* moderation queue and is not this.
MY_CONTENT_PATH = "my_pending_content"


def my_content_url(group_url_or_identifier: str) -> str:
    """The "Your content" page for a group, whose default tab is Pending."""
    base = group_url_or_identifier.rstrip("/")
    if not base.startswith("http"):
        base = f"{BASE_URL}/groups/{base}"
    return f"{base}/{MY_CONTENT_PATH}/"


# Proof the "Your content" page actually rendered. Without this, a page that
# failed to load reads as "nothing pending" -- and concluding "declined" from a
# blank page is how a wording gets released while the post is still queued.
#
# English verified 2026-08-17 ("Your content" / "No posts to show").
# Hebrew and Russian researched, UNVERIFIED.
MY_CONTENT_PAGE_MARKERS = (
    # English (verified)
    "your content",
    "no posts to show",
    "manage and view your posts",
    # Hebrew (unverified)
    "התוכן שלך",
    "אין פוסטים להצגה",
    # Russian (unverified)
    "ваш контент",
    "нет публикаций",
)


# --- Composer ---------------------------------------------------------------
# The group composer trigger carries no aria-label in any language, so it is
# matched on its visible text.
COMPOSER_TRIGGERS = (
    # English (verified)
    "Write something...",
    "Write something to the group...",
    "Write something",
    "Create post",
    # Hebrew (verified)
    "כאן כותבים",  # "write here"
    "כתבי משהו",
    "כתוב משהו",
    # Russian (verified)
    "Напишите что-нибудь",  # "write something"
    "Создайте публикацию",
)

# The composer text field exposes no accessible name, only an aria-placeholder,
# so it is found as the dialog's only textbox. These are kept for the fallback
# lookup and for recognising the field when several are present.
COMPOSER_TEXTBOX = (
    "Write something...",
    "Create post",
    "יצירת פוסט ציבורי",  # "create public post"
    "כתבי משהו",
    "Создайте общедоступную публикацию",  # "create a public post"
)

# Russian is "Отправить" (send), NOT the literal "Опубликовать" (publish) -- see
# the module docstring. Read off the live composer, not translated.
POST_BUTTONS = ("Post", "פרסום", "Отправить")

PHOTO_VIDEO_BUTTONS = (
    "Photo/video",
    "Photo or video",
    "תמונה או סרטון",
    "Фото/видео",
)

CLOSE_BUTTONS = (
    "Close",
    "סגירת תיבת הדו-שיח של המחבר",
    "Закрыть диалог создания публикаций",
)

# Closing a composer that has text in it can make Facebook ask what to do with
# the draft. Probing this account showed no prompt for a short post -- Escape
# simply closed the composer and the draft was gone on reopen -- but the prompt
# is known to appear in other situations, so the candidates stay.
#
# These specific strings are UNVERIFIED against the live site; the flow does not
# depend on them, because the composer is cleared before typing either way.
DISCARD_PROMPT_BUTTONS = (
    "Discard post",
    "Discard",
    "מחיקה",  # "delete"
    "מחק",
    "השלכה",
    "Удалить",  # "delete"
    "Отменить",
)

# --- Anomalies --------------------------------------------------------------
# Matched case-insensitively against the page text. Any hit halts the batch:
# the app never waits one out and never retries through it.
#
# UNVERIFIED, unlike everything above, and deliberately so: a rate-limit
# warning cannot be summoned to order, and provoking one is precisely the
# outcome this project is built to avoid. These are researched from Facebook's
# own localised wording. Erring toward over-matching is the right bias here --
# a false halt costs one delayed batch, a missed block costs the account.
RATE_LIMIT_MARKERS = (
    # English
    "you're temporarily blocked",
    "you are temporarily blocked",
    "temporarily blocked",
    "posting too fast",
    "you're posting too",
    "try again later",
    "we limit how often",
    "this feature isn't available right now",
    "slow down",
    # Hebrew
    "נחסמת באופן זמני",
    "אתה מפרסם מהר מדי",
    "נסה שוב מאוחר יותר",
    "התכונה הזו לא זמינה כרגע",
    # Russian
    "вы временно заблокированы",
    "временно заблокирован",
    "слишком часто",
    "слишком быстро",
    "попробуйте позже",
    "повторите попытку позже",
    "эта функция сейчас недоступна",
)

# A group with post approval turned on holds the post for an admin instead of
# publishing it. Facebook then shows the author a banner on the group page.
#
# English is VERIFIED: read off SneakerHeads in Israel on 2026-08-17, which
# renders "Pending admin approval / 1 post / Learn more / Manage post".
#
# Hebrew and Russian are researched and UNVERIFIED, like the anomaly markers
# below -- the account is in English and switching it to read two strings is
# not worth changing the user's Facebook settings for. Over-matching is the
# right bias: the marker is only ever consulted when the post is already
# confirmed absent from the feed, so a false hit cannot invent a pending post.
PENDING_APPROVAL_MARKERS = (
    # English (verified)
    "pending admin approval",
    "pending approval",
    "waiting for approval",
    "your post is pending",
    "will be visible once approved",
    # Hebrew (unverified)
    "ממתין לאישור מנהל",
    "ממתין לאישור",
    "בהמתנה לאישור",
    "הפוסט שלך ממתין",
    # Russian (unverified)
    "ожидает одобрения администратора",
    "ожидает одобрения",
    "ожидает подтверждения",
    "ваша публикация ожидает",
)

UNAVAILABLE_MARKERS = (
    # English
    "this content isn't available",
    "content isn't available right now",
    "this group isn't available",
    "you must be a member",
    "isn't available at the moment",
    # Hebrew
    "התוכן הזה לא זמין",
    "הקבוצה הזו לא זמינה",
    # Russian
    "материал недоступен",
    "контент недоступен",
    "эта группа недоступна",
    "вы должны быть участником",
)
