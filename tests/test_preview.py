"""Tests for the Compose preview's image handling.

Scaling and file reading only -- the widget itself is exercised in test_ui.py
against the shared window. The rule these enforce is that a bad attachment can
never take the compose screen down with it: the preview is a convenience, and
the post it is previewing has not been sent yet.
"""

from __future__ import annotations

import sys

import pytest

from fbposter.ui import preview

IMAGE_CAP = (preview.IMAGE_MAX_WIDTH, preview.IMAGE_MAX_HEIGHT)


@pytest.fixture(autouse=True)
def empty_cache():
    preview._THUMBNAIL_CACHE.clear()
    yield
    preview._THUMBNAIL_CACHE.clear()


def write_image(path, size=(900, 600), colour=(46, 125, 74)):
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", size, colour).save(path)
    return path


class TestScaledSize:
    def test_a_large_image_is_capped(self):
        assert preview.scaled_size((900, 600), (400, 300)) == (400, 267)

    def test_a_tall_image_is_capped_by_height(self):
        assert preview.scaled_size((600, 900), (400, 300)) == (200, 300)

    def test_a_small_image_is_left_alone(self):
        """Upscaling would show the user a blurrier image than they attached."""
        assert preview.scaled_size((120, 80), (400, 300)) == (120, 80)

    def test_the_aspect_ratio_survives(self):
        width, height = preview.scaled_size((1600, 900), (400, 300))
        assert abs(width / height - 16 / 9) < 0.02

    @pytest.mark.parametrize("size", [(0, 100), (100, 0), (-5, 5)])
    def test_a_degenerate_size_does_not_divide_by_zero(self, size):
        assert preview.scaled_size(size, (400, 300)) == (1, 1)


class TestLoadThumbnail:
    def test_a_real_image_loads_and_is_capped(self, tmp_path):
        path = write_image(tmp_path / "wide.png")
        thumbnail = preview.load_thumbnail(path, IMAGE_CAP)
        assert thumbnail is not None
        assert thumbnail.cget("size") == preview.scaled_size((900, 600), IMAGE_CAP)

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        assert preview.load_thumbnail(tmp_path / "nope.png") is None

    def test_a_file_that_is_not_an_image_is_not_an_error(self, tmp_path):
        path = tmp_path / "notes.png"
        path.write_text("this is text pretending to be a png")
        assert preview.load_thumbnail(path) is None

    def test_a_directory_is_not_an_error(self, tmp_path):
        assert preview.load_thumbnail(tmp_path) is None

    def test_without_pillow_it_degrades_instead_of_raising(self, tmp_path, monkeypatch):
        """CustomTkinter treats Pillow as optional, so the app must too."""
        path = write_image(tmp_path / "green.png")
        monkeypatch.setitem(sys.modules, "PIL", None)
        assert preview.load_thumbnail(path) is None

    def test_the_same_file_is_only_decoded_once(self, tmp_path):
        path = write_image(tmp_path / "green.png")
        first = preview.load_thumbnail(path, IMAGE_CAP)
        assert preview.load_thumbnail(path, IMAGE_CAP) is first

    def test_replacing_the_file_replaces_the_thumbnail(self, tmp_path):
        """Cached on mtime, so editing an attachment is not shown as the old one."""
        import os

        path = write_image(tmp_path / "green.png")
        first = preview.load_thumbnail(path, IMAGE_CAP)

        write_image(path, size=(300, 300), colour=(200, 30, 30))
        stat = path.stat()
        os.utime(path, (stat.st_atime + 5, stat.st_mtime + 5))

        second = preview.load_thumbnail(path, IMAGE_CAP)
        assert second is not None and second is not first
        assert second.cget("size") == (300, 300)

    def test_the_cache_does_not_grow_without_bound(self, tmp_path):
        for index in range(preview._CACHE_LIMIT + 2):
            preview.load_thumbnail(write_image(tmp_path / f"{index}.png", size=(20, 20)))
        assert len(preview._THUMBNAIL_CACHE) <= preview._CACHE_LIMIT
