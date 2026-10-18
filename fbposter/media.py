"""Pictures and videos: telling them apart, and what Facebook will take.

Pure, like `guards.py`: no Qt and no browser, so the composer, the preview and
the messages all ask the same question the same way.

Video was cut from v1 (uploads are slow and their end is hard to see) and was
built at the user's request on 2026-10-15. The suffixes are the ones the group
composer's own file input accepts, read off it live that day, not a list from
memory; anything the input accepts as `video/*` beyond these still uploads, it
is only shown as a picture tile in the preview.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

# The group composer's `input[type=file]` accept list, read live 2026-10-15:
# "image/*,image/heif,image/heic,video/*,video/mp4,video/x-m4v,
# video/x-matroska,.mkv,.avi,.wmv,.mov,.flv,.webm,.3gp,.3g2,.mts,.m2ts,.vob,
# .divx,.f4v,.ogv"
VIDEO_SUFFIXES = frozenset({
    ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".webm", ".3gp",
    ".3g2", ".mts", ".m2ts", ".vob", ".divx", ".f4v", ".ogv",
})
PICTURE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"})

# Facebook's own ceiling for one video. The user chose no limit of the app's
# own (2026-10-15), so this is the only one: a bigger file would upload for an
# hour and then be refused.
FACEBOOK_VIDEO_LIMIT_BYTES = 10 * 1024 ** 3

# From this size the user is told a video takes a while to upload. Watched
# live: a 58 MB video was a fifth of the way up after five minutes.
SLOW_UPLOAD_BYTES = 100 * 1024 ** 2


def is_video(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_SUFFIXES


def size_of(path: str | Path) -> int | None:
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def too_large(path: str | Path) -> bool:
    """A video over Facebook's own limit. Pictures are never refused here."""
    size = size_of(path)
    return is_video(path) and size is not None and size > FACEBOOK_VIDEO_LIMIT_BYTES


def slow_to_upload(path: str | Path) -> bool:
    size = size_of(path)
    return is_video(path) and size is not None and size >= SLOW_UPLOAD_BYTES


def describe(paths: Iterable[str | Path]) -> str:
    """ "2 pictures", "1 video", "2 pictures and 1 video" -- for a person."""
    paths = list(paths)
    videos = sum(1 for p in paths if is_video(p))
    pictures = len(paths) - videos
    parts = []
    if pictures:
        parts.append(f"{pictures} picture{'s' if pictures != 1 else ''}")
    if videos:
        parts.append(f"{videos} video{'s' if videos != 1 else ''}")
    return " and ".join(parts)


def kind(paths: Iterable[str | Path]) -> str:
    """ "Pictures", "Video" or "Pictures and video": what a post carries."""
    paths = list(paths)
    videos = any(is_video(p) for p in paths)
    pictures = any(not is_video(p) for p in paths)
    if videos and pictures:
        return "Pictures and video"
    return "Video" if videos else "Pictures"
