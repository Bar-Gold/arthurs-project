"""Pictures and videos kept in the app's own folder.

A post's attachments used to be posted from wherever the user picked them. A
client picked them from Downloads and the Desktop, and a repeating post keeps
its paths for weeks: tidying either folder would stop every later run at its
first group with "Attachment no longer on disk". So the worker stores each
attachment in `media/` beside the database, and the post points there instead
(the user's choice, 2026-10-20).

On the same drive the stored file is a **hard link**: a second name for the same
file, made instantly, costing no space, and surviving the original being moved
or deleted. Editing the original in place does change the stored one, which
the user accepted. Anywhere a link cannot be made (another drive, a file
Windows will not link) it is a **real copy**, written under a temporary name and
renamed when complete, so a copy cut short is never taken for the file.

Each file gets a folder of its own, `media/<8 hex>/<its own name>`, so the name
on screen stays the name the user picked.

Only files are ever deleted here (`prune`), never a database row.
"""

from __future__ import annotations

import os
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

MEDIA_DIRNAME = "media"
# A stored folder younger than this is never pruned. The file is stored before
# the post's row is pointed at it, and the two are not one transaction.
PRUNE_MIN_AGE = timedelta(days=1)
PARTIAL_SUFFIX = ".partial"


def media_dir(database_path: Path) -> Path:
    """Where attachments are kept: beside the database they belong to."""
    return Path(database_path).parent / MEDIA_DIRNAME


def _key(path: Path | str) -> str:
    # String work only, no disk access: this runs on every worker tick.
    return os.path.normcase(os.path.abspath(str(path)))


def is_stored(path: Path | str, root: Path) -> bool:
    """Is this attachment already in the app's folder?"""
    return _key(path).startswith(_key(root) + os.sep)


def identity(path: Path | str) -> tuple[str, int | None]:
    """What makes two attachments the same file, as far as a person can tell:
    its name and its size. A stored copy matches the original it came from,
    which its path would not."""
    file = Path(path)
    try:
        size: int | None = file.stat().st_size
    except OSError:
        size = None
    return os.path.normcase(file.name), size


def store_one(path: str, root: Path) -> str:
    """Keep this attachment in `root`, and return where it now is.

    A path already there comes back as it is. So does a file that is not on
    disk at all: the worker's own check names it to the user before anything
    is posted, which is more use than an error here.
    """
    source = Path(path)
    if is_stored(source, root) or not source.is_file():
        return path

    root.mkdir(parents=True, exist_ok=True)
    while True:
        folder = root / secrets.token_hex(4)
        try:
            folder.mkdir()
            break
        except FileExistsError:
            continue
    target = folder / source.name
    try:
        try:
            os.link(source, target)
        except OSError:
            partial = folder / (source.name + PARTIAL_SUFFIX)
            shutil.copy2(source, partial)
            os.replace(partial, target)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return str(target)


def store(paths: Sequence[str], root: Path) -> list[str]:
    """`store_one` for each, in the same order."""
    return [store_one(str(path), root) for path in paths]


def prune(root: Path, referenced: Iterable[str], now: datetime) -> int:
    """Delete stored folders nothing refers to any more. Returns how many.

    `referenced` is every attachment path still in use. A folder younger than
    `PRUNE_MIN_AGE` is kept regardless.
    """
    if not root.is_dir():
        return 0
    keep = {_key(Path(path).parent) for path in referenced if is_stored(path, root)}
    removed = 0
    for folder in root.iterdir():
        if not folder.is_dir() or _key(folder) in keep:
            continue
        made = datetime.fromtimestamp(folder.stat().st_mtime, tz=timezone.utc)
        if now - made < PRUNE_MIN_AGE:
            continue
        shutil.rmtree(folder, ignore_errors=True)
        removed += 1
    return removed
