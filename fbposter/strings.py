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
# Hebrew verified 2026-08-18, read off the live page with the account switched
# to Hebrew: the page returned "SneakerHeads in Israel › התוכן שלך", the blurb
# "באפשרותך לנהל את הפוסטים שלך ולעיין בהם בקבוצה", and the empty Pending tab
# "אין פוסטים להצגה". Its tabs are בהמתנה / פורסמו / נדחו עם משוב / הוסרו עם
# משוב -- unused, because the app relies on Pending being the default rather
# than clicking a translated tab name.
# Russian verified 2026-08-18 in the same way: "SneakerHeads in Israel › Ваш
# контент" with the blurb "Управление публикациями в группе и их просмотр", and
# the post listed under the default На рассмотрении tab. Its empty state was
# not on screen -- there was a real post -- so "нет публикаций" is still a
# guess, and the two verified strings are what the render-proof rests on.
MY_CONTENT_PAGE_MARKERS = (
    # English (verified)
    "your content",
    "no posts to show",
    "manage and view your posts",
    # Hebrew (verified)
    "התוכן שלך",
    "אין פוסטים להצגה",
    "לנהל את הפוסטים שלך",
    # Russian (the first two verified; the empty state is researched)
    "ваш контент",
    "управление публикациями в группе",
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
# Hebrew is VERIFIED: same group on 2026-08-18 with the account switched to
# Hebrew, holding a real post. It renders "בהמתנה לאישור מנהל / פוסט אחד
# לקבלת מידע נוסף / ניהול הפוסט" -- so the live string is "בהמתנה", not the
# "ממתין" that the obvious translation gives. Both are kept: the researched
# forms cost nothing and a false hit cannot invent a pending post, because
# _is_queued asks the group's pending list before believing one.
#
# Russian is VERIFIED: same group on 2026-08-18 with the account switched to
# Russian, the same post still held. It renders "Ожидает подтверждения
# администратора / 1 публикация Подробнее / Управление публикацией" -- so the
# live word is "подтверждения" (confirmation), and the natural translation
# "одобрения" (approval) does not appear on the page at all.
#
# All three languages now match the live site. The researched forms are kept
# below them: they cost nothing, and a false hit cannot invent a pending post,
# because _is_queued asks the group's pending list before believing one.
PENDING_APPROVAL_MARKERS = (
    # English (verified)
    "pending admin approval",
    "pending approval",
    "waiting for approval",
    "your post is pending",
    "will be visible once approved",
    # Hebrew ("בהמתנה לאישור מנהל" verified; the rest researched)
    "בהמתנה לאישור",
    "ממתין לאישור מנהל",
    "ממתין לאישור",
    "הפוסט שלך ממתין",
    # Russian ("ожидает подтверждения администратора" verified; rest researched)
    "ожидает подтверждения",
    "ожидает одобрения администратора",
    "ожидает одобрения",
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
