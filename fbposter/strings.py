"""Every Facebook-facing URL and UI string the automation depends on.

Facebook's DOM class names are obfuscated and change between builds, so
selectors are built from roles and accessible names instead. That makes them
language-dependent: the target account's interface is English (README.md s.12).
If that language ever changes, this module is the only file that needs editing.
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

# --- Composer strings, unused until Phase 4 but kept here from the start ---
COMPOSER_PROMPT = "Write something..."
CREATE_POST_BUTTON = "Create post"
POST_BUTTON = "Post"
PHOTO_VIDEO_BUTTON = "Photo/video"
