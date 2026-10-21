"""Tests for keeping attachments in the app's own folder (fbposter/attachments.py).

A client attached pictures from Downloads and the Desktop, and a repeating post
keeps its paths for weeks. Everything here runs in a temporary folder.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fbposter import attachments


@pytest.fixture
def root(tmp_path):
    return tmp_path / "data" / attachments.MEDIA_DIRNAME


@pytest.fixture
def picture(tmp_path):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    path = folder / "דירה למכירה.png"
    path.write_bytes(b"picture bytes")
    return path


def age(folder: Path, days: float) -> None:
    then = time.time() - days * 86400
    os.utime(folder, (then, then))


class TestStoring:
    def test_it_goes_into_a_folder_of_its_own_under_its_own_name(self, root, picture):
        stored = Path(attachments.store_one(str(picture), root))
        assert stored.parent.parent == root
        assert stored.name == picture.name, "the name on screen must not change"
        assert stored.read_bytes() == b"picture bytes"

    def test_on_the_same_drive_it_is_a_link_not_a_copy(self, root, picture):
        """No extra space, and instant whatever the size."""
        stored = attachments.store_one(str(picture), root)
        assert os.path.samefile(stored, picture)

    def test_it_outlives_the_original(self, root, picture):
        stored = Path(attachments.store_one(str(picture), root))
        picture.unlink()
        assert stored.read_bytes() == b"picture bytes"

    def test_where_no_link_can_be_made_it_is_a_real_copy(self, root, picture, monkeypatch):
        def refused(*_args):
            raise OSError("not the same device")

        monkeypatch.setattr(attachments.os, "link", refused)
        stored = Path(attachments.store_one(str(picture), root))
        assert stored.read_bytes() == b"picture bytes"
        assert not os.path.samefile(stored, picture)
        assert not list(stored.parent.glob("*" + attachments.PARTIAL_SUFFIX))

    def test_a_copy_cut_short_leaves_nothing_behind(self, root, picture, monkeypatch):
        def refused(*_args):
            raise OSError("not the same device")

        def full(*_args, **_kwargs):
            raise OSError("the disk is full")

        monkeypatch.setattr(attachments.os, "link", refused)
        monkeypatch.setattr(attachments.shutil, "copy2", full)
        with pytest.raises(OSError):
            attachments.store_one(str(picture), root)
        assert list(root.iterdir()) == []

    def test_a_stored_file_comes_back_as_it_is(self, root, picture):
        stored = attachments.store_one(str(picture), root)
        assert attachments.store_one(stored, root) == stored
        assert len(list(root.iterdir())) == 1

    def test_a_missing_file_comes_back_as_it_is(self, root, tmp_path):
        """The worker's own check names it before anything is posted."""
        gone = str(tmp_path / "gone.png")
        assert attachments.store_one(gone, root) == gone
        assert not root.exists()

    def test_the_same_file_twice_gets_two_folders(self, root, picture):
        first = attachments.store_one(str(picture), root)
        second = attachments.store_one(str(picture), root)
        assert Path(first).parent != Path(second).parent

    def test_store_keeps_the_order(self, root, tmp_path, picture):
        other = tmp_path / "Downloads" / "b.png"
        other.write_bytes(b"b")
        stored = attachments.store([str(picture), str(other)], root)
        assert [Path(p).name for p in stored] == [picture.name, "b.png"]


class TestIsStored:
    def test_inside_and_outside(self, root, picture):
        assert not attachments.is_stored(picture, root)
        assert attachments.is_stored(root / "ab12cd34" / "x.png", root)

    def test_a_folder_that_only_starts_the_same_is_outside(self, root):
        assert not attachments.is_stored(str(root) + "-old/x.png", root)

    def test_case_does_not_matter_on_windows(self, root):
        if os.name != "nt":
            pytest.skip("Windows paths ignore case")
        assert attachments.is_stored(str(root / "ab" / "x.png").upper(), root)


class TestIdentity:
    def test_a_stored_file_matches_its_original(self, root, picture):
        stored = attachments.store_one(str(picture), root)
        assert attachments.identity(stored) == attachments.identity(picture)

    def test_a_missing_file_has_no_size(self, tmp_path):
        assert attachments.identity(tmp_path / "gone.png")[1] is None


class TestPrune:
    def now(self):
        return datetime.now(timezone.utc)

    def test_an_old_folder_nothing_refers_to_goes(self, root, picture):
        stored = Path(attachments.store_one(str(picture), root))
        age(stored.parent, 2)
        assert attachments.prune(root, [], self.now()) == 1
        assert not stored.parent.exists()
        assert picture.exists(), "only the app's own folder is ever touched"

    def test_a_folder_still_referred_to_stays(self, root, picture):
        stored = attachments.store_one(str(picture), root)
        age(Path(stored).parent, 30)
        assert attachments.prune(root, [stored], self.now()) == 0
        assert Path(stored).exists()

    def test_a_young_folder_stays_whatever_refers_to_it(self, root, picture):
        stored = attachments.store_one(str(picture), root)
        assert attachments.prune(root, [], self.now()) == 0
        assert Path(stored).exists()
        assert attachments.PRUNE_MIN_AGE == timedelta(days=1)

    def test_no_folder_yet_is_nothing_to_do(self, root):
        assert attachments.prune(root, [], self.now()) == 0
