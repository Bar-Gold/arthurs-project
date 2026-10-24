"""The Posting tips screen's words, in Hebrew, English and Russian.

Advice, never a rule. The app enforces none of it: there are no posting rules
(the user's choice, 2026-10-18), and what used to be the daily limit, the
cooldown, the posting hours and the repeated-text rule is said here instead,
as recommendations from the user to their clients.

Researched on 2026-10-18. Facebook publishes no numbers, so every figure is an
estimate: Meta's spam policy (transparency.meta.com) is the only first-hand
source, and the rest comes from guides by people who post to many groups. The
policy's own words are the reason the first tip comes first: accounts may be
restricted "at lower frequencies when other indicators of Spam (e.g., posting
repetitive content)" are present.

Pure data, so it can be checked without a window: every language has the
same tips in the same order (`tests/test_tips.py`). Screen names (Publish,
Settings, Pause posting) stay in English in every language, because that is
what the window says.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The setting that remembers the language last chosen on the screen.
LANGUAGE_KEY = "tips_language"
# Most of the clients write in Hebrew (the user's choice, 2026-10-18).
DEFAULT_LANGUAGE = "he"
# In the order the switch shows them, each named in its own language.
LANGUAGES = (("he", "עברית"), ("en", "English"), ("ru", "Русский"))
RIGHT_TO_LEFT = frozenset({"he"})


@dataclass(frozen=True)
class Tip:
    title: str
    body: str


@dataclass(frozen=True)
class Page:
    title: str
    intro: str
    tips: tuple[Tip, ...]
    sources: str


PAGES = {
    "en": Page(
        title="Posting tips",
        intro=(
            "Facebook does not publish its limits, so nobody knows the exact "
            "numbers. These recommendations come from Meta's spam policy and "
            "from people who post to many groups every day. The app does not "
            "enforce any of them: it posts whatever you set up."
        ),
        tips=(
            Tip(
                "Vary the wording. This matters most.",
                "Meta's spam policy says an account can be restricted even when it "
                "posts rarely, if its posts repeat. Write every listing at least 3 "
                "different ways: a different opening line, a different order, a "
                "different closing sentence. Add them in Create a post, step 1, with "
                "Add version, and the app shares them out across the groups.",
            ),
            Tip(
                "An active account can post more",
                "Facebook gives more room to an account with a long, normal "
                "history: posting, commenting and reacting on most days. A rough "
                "guide: a new account, or one that has been quiet for a while, "
                "should start at 2-5 posts a day. After a few months of regular "
                "use, 10-20 a day. A long, active and clean history allows more. "
                "One listing to 40-80 groups is far above what the guides call "
                "safe, so do it only from an established, active account, and with "
                "several wordings.",
            ),
            Tip(
                "Build up slowly",
                "Raise the number of groups gradually, over 2-4 weeks. A sudden "
                "jump from what the account usually does is what Facebook notices "
                "most.",
            ),
            Tip(
                "Don't crowd one group",
                "Leave several hours between posts to the same group, once or "
                "twice a day at most. Read each group's rules: many allow adverts "
                "only on certain days, or once a week.",
            ),
            Tip(
                "Leave time between groups",
                "The app waits 2-4 minutes between groups by itself, on top of the "
                "time each post takes. Posting faster than that looks like a bot.",
            ),
            Tip(
                "Post while people are awake",
                "Post between about 08:00 and 23:00. In Israel, evenings "
                "(17:00-22:00) get the most attention. A post at 3 or 4 in the "
                "morning looks automated, and hardly anyone sees it.",
            ),
            Tip(
                "Links",
                "Write the full link, never a shortened one (like bit.ly). A post "
                "with pictures and no link is the safest kind.",
            ),
            Tip(
                "Members and admins count too",
                "When members or admins report a post as spam, Facebook acts much "
                "faster than it would on its own. Post only what each group "
                "accepts, in the format it asks for.",
            ),
            Tip(
                "Be a person, not just a poster",
                "Reply to comments in the first few hours, and like and comment on "
                "other people's posts in your groups. An account that only posts "
                "adverts looks like a spam account.",
            ),
            Tip(
                "If Facebook warns or blocks you",
                "Press Pause posting on the Settings screen straight away. Don't "
                "keep trying to post, and don't open a second account. Wait 24-48 "
                "hours, then start again at about a third of your usual volume and "
                "build up over 2 weeks. Blocks get longer each time, from a few "
                "hours to 3, 7 and 30 days, and repeated blocks can cost you the "
                "account. The app already stops by itself when Facebook shows a "
                "security check.",
            ),
        ),
        sources=(
            "Based on Meta's spam and account-restriction policies "
            "(transparency.meta.com) and on guides by people who post to many "
            "groups: lilachbullock.com, pilotposter.com and multiplegroupposter.com. "
            "The Israeli hours are from thetabber.com. The numbers are estimates, "
            "not Facebook's."
        ),
    ),
    "he": Page(
        title="טיפים לפרסום",
        intro=(
            "פייסבוק לא מפרסמת את המגבלות שלה, ולכן אף אחד לא יודע את המספרים "
            "המדויקים. ההמלצות כאן מבוססות על מדיניות הספאם של Meta ועל הניסיון "
            "של אנשים שמפרסמים בקבוצות רבות מדי יום. התוכנה לא אוכפת אף אחת "
            "מהן: היא מפרסמת כל מה שמגדירים בה."
        ),
        tips=(
            Tip(
                "לגוון את הנוסחים. זה הכי חשוב.",
                "לפי מדיניות הספאם של Meta, פייסבוק עלולה להגביל חשבון גם כשהוא "
                "מפרסם מעט, אם הפוסטים שלו חוזרים על עצמם. כדאי לכתוב כל מודעה "
                "ב-3 נוסחים שונים לפחות: פתיחה אחרת, סדר אחר, משפט סיום אחר. את "
                "הנוסחים מוסיפים ב-Create a post, בשלב 1, בלחיצה על Add version, "
                "והתוכנה מחלקת אותם בין הקבוצות.",
            ),
            Tip(
                "חשבון פעיל יכול לפרסם יותר",
                "פייסבוק נותנת יותר מרחב לחשבון עם היסטוריה ארוכה ורגילה: פוסטים, "
                "תגובות ולייקים ברוב הימים. הערכה גסה: חשבון חדש, או חשבון שלא "
                "היה פעיל זמן מה, כדאי שיתחיל ב-2 עד 5 פוסטים ביום. אחרי כמה "
                "חודשים של שימוש קבוע, 10 עד 20 ביום. היסטוריה ארוכה, פעילה "
                "ונקייה מאפשרת יותר. מודעה אחת ל-40 עד 80 קבוצות היא הרבה מעבר "
                "למה שהמדריכים מגדירים כבטוח, ולכן כדאי לעשות את זה רק מחשבון "
                "ותיק ופעיל, ועם כמה נוסחים.",
            ),
            Tip(
                "להגדיל בהדרגה",
                "את מספר הקבוצות כדאי להגדיל בהדרגה, לאורך 2 עד 4 שבועות. קפיצה "
                "פתאומית ממה שהחשבון עושה בדרך כלל היא מה שפייסבוק שמה לב אליו "
                "יותר מכל דבר אחר.",
            ),
            Tip(
                "לא להציף קבוצה אחת",
                "כדאי להשאיר כמה שעות בין פוסטים לאותה קבוצה, פעם או פעמיים ביום "
                "לכל היותר. חשוב לקרוא את הכללים של כל קבוצה: בהרבה קבוצות מותר "
                "לפרסם מודעות רק בימים מסוימים, או פעם בשבוע.",
            ),
            Tip(
                "זמן בין קבוצה לקבוצה",
                "התוכנה ממתינה מעצמה 2 עד 4 דקות בין קבוצה לקבוצה, בנוסף לזמן "
                "שכל פוסט לוקח. פרסום מהיר יותר נראה כמו בוט.",
            ),
            Tip(
                "לפרסם כשאנשים ערים",
                "כדאי לפרסם בערך בין 08:00 ל-23:00. בישראל, שעות הערב (17:00 עד "
                "22:00) מקבלות הכי הרבה תשומת לב. פוסט ב-3 או ב-4 לפנות בוקר "
                "נראה אוטומטי, וכמעט אף אחד לא רואה אותו.",
            ),
            Tip(
                "קישורים",
                "כדאי לכתוב את הקישור המלא, ולא קישור מקוצר (כמו bit.ly). פוסט עם "
                "תמונות ובלי קישור הוא הבטוח ביותר.",
            ),
            Tip(
                "גם חברי הקבוצה והמנהלים משפיעים",
                "כשחברי קבוצה או מנהלים מדווחים על פוסט כספאם, פייסבוק מגיבה הרבה "
                "יותר מהר מאשר כשהיא מזהה את זה לבד. כדאי לפרסם בכל קבוצה רק את "
                "מה שהיא מקבלת, ובפורמט שהיא מבקשת.",
            ),
            Tip(
                "להתנהג כמו אדם, לא כמו מכונת פרסום",
                "כדאי להגיב לתגובות בשעות הראשונות, ולעשות לייק ולהגיב גם לפוסטים "
                "של אחרים בקבוצות. חשבון שרק מפרסם מודעות נראה כמו חשבון ספאם.",
            ),
            Tip(
                "אם פייסבוק מזהירה או חוסמת",
                "יש ללחוץ מיד על Pause posting במסך Settings. חשוב לא להמשיך לנסות "
                "לפרסם ולא לפתוח חשבון שני. כדאי לחכות 24 עד 48 שעות, ואז לחזור "
                "בערך בשליש מהכמות הרגילה ולהגדיל בהדרגה במשך 2 שבועות. כל חסימה "
                "ארוכה מהקודמת, מכמה שעות ל-3, 7 ו-30 ימים, וחסימות חוזרות עלולות "
                "לגרום לאובדן החשבון. התוכנה כבר עוצרת מעצמה כשפייסבוק מציגה "
                "בדיקת אבטחה.",
            ),
        ),
        sources=(
            "מבוסס על מדיניות הספאם ומדיניות הגבלת החשבונות של Meta "
            "(transparency.meta.com), ועל מדריכים של אנשים שמפרסמים בקבוצות "
            "רבות: lilachbullock.com, pilotposter.com ו-multiplegroupposter.com. "
            "שעות הפעילות בישראל מבוססות על thetabber.com. המספרים הם הערכות, לא "
            "נתונים של פייסבוק."
        ),
    ),
    "ru": Page(
        title="Советы по публикации",
        intro=(
            "Facebook не раскрывает свои ограничения, поэтому точных цифр не знает "
            "никто. Эти рекомендации основаны на правилах Meta о спаме и на опыте "
            "людей, которые каждый день публикуют посты во многих группах. "
            "Программа их не проверяет: она публикует всё, что вы настроите."
        ),
        tips=(
            Tip(
                "Меняйте формулировки: это важнее всего",
                "Согласно правилам Meta о спаме, аккаунт могут ограничить даже при "
                "редких публикациях, если посты повторяются. Напишите каждое "
                "объявление как минимум в 3 разных вариантах: другое начало, другой "
                "порядок, другая заключительная фраза. Добавьте их как варианты "
                "текста в «Create a post», на шаге 1, кнопкой «Add version», и "
                "программа распределит их между группами.",
            ),
            Tip(
                "Активный аккаунт может публиковать больше",
                "Facebook даёт больше свободы аккаунту с долгой и обычной историей "
                "использования: публикации, комментарии и реакции почти каждый "
                "день. Примерно так: новому аккаунту или аккаунту, который долго не "
                "использовался, лучше начинать с малого, от 2 до 5 постов в день. "
                "После нескольких месяцев регулярного использования можно от 10 до "
                "20 в день. Долгая, активная и чистая история позволяет больше. "
                "Если одно объявление уходит в 40, а то и в 80 групп, риск "
                "блокировки высок, поэтому делайте это только с давнего активного "
                "аккаунта и с несколькими вариантами текста.",
            ),
            Tip(
                "Наращивайте постепенно",
                "Увеличивайте число групп постепенно: на это стоит отвести от 2 до "
                "4 недель. Facebook в первую очередь замечает резкий скачок по "
                "сравнению с обычной активностью аккаунта.",
            ),
            Tip(
                "Не заваливайте одну группу",
                "Публикуйте в одну и ту же группу не чаще одного-двух раз в день, с "
                "перерывом в несколько часов. Читайте правила каждой группы: во "
                "многих объявления разрешены только в дни, указанные в правилах, "
                "или раз в неделю.",
            ),
            Tip(
                "Паузы между группами",
                "Между группами программа сама ждёт от 2 до 4 минут, не считая "
                "времени на публикацию самого поста. Если публиковать быстрее, это "
                "выглядит как работа бота.",
            ),
            Tip(
                "Публикуйте, когда люди не спят",
                "Публикуйте примерно с 08:00 до 23:00. В Израиле больше всего "
                "внимания получают посты, опубликованные вечером, с 17:00 до 22:00. "
                "Пост в 3 или 4 часа ночи выглядит автоматическим, и его почти "
                "никто не видит.",
            ),
            Tip(
                "Ссылки",
                "Указывайте полную ссылку, а не сокращённую (например, bit.ly). "
                "Пост с фотографиями и без ссылки безопаснее всего.",
            ),
            Tip(
                "Жалобы участников ускоряют блокировку",
                "Когда участники или администраторы жалуются на пост как на спам, "
                "Facebook реагирует гораздо быстрее, чем если бы заметил сам. "
                "Публикуйте в каждой группе только то, что она принимает, и в том "
                "формате, который требуют её правила.",
            ),
            Tip(
                "Общайтесь в группах",
                "Отвечайте на комментарии в первые часы после публикации, ставьте "
                "лайки и комментируйте чужие посты в своих группах. Аккаунт, который "
                "только публикует объявления, выглядит как спам-аккаунт.",
            ),
            Tip(
                "Если Facebook предупредил или заблокировал аккаунт",
                "Сразу нажмите «Pause posting» на экране «Settings». Не пытайтесь "
                "публиковать снова и не создавайте второй аккаунт. Подождите от 24 "
                "до 48 часов, затем начните примерно с трети обычного объёма и "
                "наращивайте его в течение 2 недель. Каждая следующая блокировка "
                "длится дольше: сначала несколько часов, потом 3, 7 и 30 дней. "
                "Повторные блокировки могут стоить аккаунта. Программа сама "
                "останавливается, когда Facebook показывает проверку безопасности.",
            ),
        ),
        sources=(
            "Основано на правилах Meta о спаме и об ограничении аккаунтов "
            "(transparency.meta.com) и на руководствах людей, которые публикуют "
            "во многих группах: lilachbullock.com, pilotposter.com и "
            "multiplegroupposter.com. Данные о часах активности в Израиле взяты с "
            "сайта thetabber.com. Цифры приблизительные, это не данные Facebook."
        ),
    ),
}


def _bind_short_words(text: str) -> str:
    """A non-breaking space after a one-letter word, so «в», «с», «и» and the
    rest never end a line (ru-text R30). Done here rather than typed into the
    strings above, where the character is invisible and would trip anyone
    searching the source for a phrase."""
    return re.sub(r"(?<!\w)([вксоуиаяВКСОУИАЯ]) ", "\\1\u00a0", text)


def _typeset(page: Page) -> Page:
    return Page(
        title=_bind_short_words(page.title),
        intro=_bind_short_words(page.intro),
        tips=tuple(
            Tip(_bind_short_words(tip.title), _bind_short_words(tip.body))
            for tip in page.tips
        ),
        sources=_bind_short_words(page.sources),
    )


PAGES["ru"] = _typeset(PAGES["ru"])


def page(language: str) -> Page:
    """The page in `language`, or in the default one for anything unknown."""
    return PAGES.get(language, PAGES[DEFAULT_LANGUAGE])
