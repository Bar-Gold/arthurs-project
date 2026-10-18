"""Tests for videos on screen: attaching one, the preview, and the wording.

The preview never opens a video: Qt's image readers cannot, QtMultimedia is
not in the packaged build, and a failed decode used to mean a "no preview"
tile, which reads as a broken file. A video gets a player-shaped tile instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbposter import media
from fbposter.qtui.views import compose as compose_module
from fbposter.qtui.views.compose import MEDIA_FILTER, media_only
from fbposter.qtui.views.publish import NOW, REPEAT

from .test_qtui_publish import views  # noqa: F401  (the shared fixture)


@pytest.fixture
def clip(tmp_path) -> Path:
    path = tmp_path / "bike.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    return path


class TestPickingAVideo:
    def test_the_picker_offers_videos_and_pictures(self):
        assert "*.mp4" in MEDIA_FILTER
        assert "*.mov" in MEDIA_FILTER
        assert "*.jpg" in MEDIA_FILTER

    def test_a_video_is_attached(self, qt_app, views, clip):  # noqa: F811
        compose, _publish = views
        assert compose.add_files([str(clip)]) == 1
        assert compose.attachments == [clip]
        assert "Attached 1 file." in qt_app.toast_label.text()

    def test_the_same_file_twice_is_attached_once(self, views, clip):  # noqa: F811
        compose, _publish = views
        compose.add_files([str(clip)])
        assert compose.add_files([str(clip)]) == 0
        assert compose.attachments == [clip]

    def test_one_over_facebook_s_limit_is_refused(self, qt_app, views, clip, monkeypatch):  # noqa: F811
        compose, _publish = views
        monkeypatch.setattr(media, "FACEBOOK_VIDEO_LIMIT_BYTES", 10)
        assert compose.add_files([str(clip)]) == 0
        assert compose.attachments == []
        assert "10 GB" in qt_app.toast_label.text()

    def test_a_large_one_comes_with_a_note(self, qt_app, views, clip, monkeypatch):  # noqa: F811
        compose, _publish = views
        monkeypatch.setattr(media, "SLOW_UPLOAD_BYTES", 10)
        compose.add_files([str(clip)])
        assert "takes a while to upload" in qt_app.toast_label.text()

    def test_the_edit_screen_judges_it_the_same_way(self, qt_app, clip, monkeypatch):
        from fbposter import recurrence
        from fbposter.db.models import utcnow

        group = qt_app.group_repo.add_from_url("https://www.facebook.com/groups/demo0")
        rule = recurrence.build(["09:00"])
        schedule = qt_app.schedule_repo.create(
            name="Bikes", bodies=["one", "two"], group_ids=[group.id],
            times=list(rule.times), next_run_at=recurrence.next_occurrence(rule, utcnow()),
        )
        editor = qt_app.views["schedule_edit"]
        assert editor.load(schedule.id)
        monkeypatch.setattr(media, "FACEBOOK_VIDEO_LIMIT_BYTES", 10)
        assert editor.add_pictures([str(clip)]) == 0
        monkeypatch.setattr(media, "FACEBOOK_VIDEO_LIMIT_BYTES", 10 ** 12)
        assert editor.add_pictures([str(clip)]) == 1
        assert editor.pictures() == [str(clip)]


class TestThePreview:
    def test_a_video_gets_a_player_tile_not_a_broken_one(self, views, clip, monkeypatch):  # noqa: F811
        compose, _publish = views
        opened = []
        monkeypatch.setattr(
            compose_module, "load_tile", lambda path, *a: opened.append(path)
        )
        compose.add_files([str(clip)])
        compose.set_mode("preview")
        (tile,) = compose.preview.tiles
        assert tile.is_video and tile.readable
        assert not tile.pixmap().isNull()
        assert opened == [], "the preview tried to decode a video"

    def test_a_video_beside_a_picture_is_still_a_video_tile(self, views, clip, tmp_path):  # noqa: F811
        compose, _publish = views
        picture = tmp_path / "bike.jpg"
        picture.write_bytes(b"not really a jpeg")
        compose.attachments = [picture, clip]
        compose.set_mode("preview")
        kinds = sorted(tile.is_video for tile in compose.preview.tiles)
        assert kinds == [False, True]


class TestTheWording:
    def test_a_post_with_only_media_is_named_for_what_it_carries(self):
        assert media_only([Path("a.jpg"), Path("b.jpg")]) == "Pictures only (2 pictures, no text)"
        assert media_only([Path("a.mp4")]) == "Video only (1 video, no text)"
        assert media_only([Path("a.jpg"), Path("b.mp4")]) == (
            "Pictures and video only (1 picture and 1 video, no text)"
        )

    def test_publish_names_a_video_only_post(self, views, clip):  # noqa: F811
        compose, publish = views
        compose._show("")
        compose.capture()
        compose.attachments = [clip]
        publish.refresh_recipients()
        assert publish.preview_note.text() == "Video only (1 video, no text)."

    def test_a_video_only_post_goes_out_with_now(self, qt_app, views, clip):  # noqa: F811
        compose, publish = views
        compose._show("")
        compose.capture()
        compose.attachments = [clip]
        publish.set_mode(NOW)
        assert publish.publish() is True
        assert qt_app.task_repo.list_recent()[0].media_paths == [str(clip)]

    def test_a_repeating_post_still_needs_words(self, qt_app, views, clip):  # noqa: F811
        """A video alone is the same post every run, like a picture alone."""
        compose, publish = views
        compose._show("")
        compose.capture()
        compose.attachments = [clip]
        publish.set_mode(REPEAT)
        assert publish.publish() is False
        assert "as well as pictures or videos" in qt_app.toast_label.text()
