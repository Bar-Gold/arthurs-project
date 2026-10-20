"""Tests for posting videos: the upload wait, the connection, and the rules.

Video was cut from v1 and built on 2026-10-15. Two things were learned live on
the TRY group that day, and both are pinned here because both fail silently:

* **Facebook enables Post long before a video has uploaded**, so the poster
  waits for the upload progress bar to go, never for the button. A 58 MB video
  sat at 1% with Post clickable.
* **Playwright refuses a file over 50 MB to a browser it thinks is remote**,
  and an attached browser counts as remote unless `is_local` is passed.

The upload wait has no overall limit (the user's choice): only five minutes
without movement stops it, and that is before Post, so nothing was posted.
"""

from __future__ import annotations

import base64
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from fbposter import media, session, strings
from fbposter.automation.poster import (
    UPLOAD_GUARD_EVERY_MS,
    UPLOAD_POLL_MS,
    UPLOAD_STALL_MS,
    created_post_url,
)
from fbposter.errors import AutomationHalted, UploadStalled

from .fake_page import FakePage
from .test_poster import POST_BUTTON, make_poster, request

from .conftest import fresh_database

VIDEO = Path("C:/clips/bike.mp4")
PICTURE = Path("C:/pictures/bike.jpg")
STALL_LOOKS = UPLOAD_STALL_MS // UPLOAD_POLL_MS


def looks(page) -> list[tuple]:
    """What each look at the upload bars found."""
    return [call[1] for call in page.calls if call[0] == "progress"]


def post_clicked_at(page) -> int:
    return next(
        index for index, call in enumerate(page.calls)
        if call[0] == "click" and call[1].endswith(POST_BUTTON)
    )


class TestWhatCountsAsAVideo:
    def test_by_suffix_ignoring_case(self):
        assert media.is_video("a.MP4")
        assert media.is_video(Path("b.mov"))
        assert not media.is_video("c.jpg")

    def test_the_composer_s_own_list_is_covered(self):
        """Read off the live file input, 2026-10-15."""
        for suffix in (".mp4", ".m4v", ".mkv", ".avi", ".wmv", ".mov", ".webm", ".3gp"):
            assert suffix in media.VIDEO_SUFFIXES

    def test_how_a_post_is_described(self):
        assert media.describe([PICTURE, PICTURE]) == "2 pictures"
        assert media.describe([VIDEO]) == "1 video"
        assert media.describe([PICTURE, VIDEO]) == "1 picture and 1 video"
        assert media.kind([PICTURE]) == "Pictures"
        assert media.kind([VIDEO]) == "Video"
        assert media.kind([PICTURE, VIDEO]) == "Pictures and video"

    def test_only_a_video_over_facebook_s_limit_is_too_large(self, tmp_path, monkeypatch):
        monkeypatch.setattr(media, "FACEBOOK_VIDEO_LIMIT_BYTES", 10)
        big_video = tmp_path / "big.mp4"
        big_video.write_bytes(b"x" * 11)
        big_picture = tmp_path / "big.jpg"
        big_picture.write_bytes(b"x" * 11)
        assert media.too_large(big_video)
        assert not media.too_large(big_picture)
        assert not media.too_large(tmp_path / "missing.mp4")


class TestTheConnection:
    def test_it_tells_playwright_the_chrome_is_on_this_pc(self, monkeypatch):
        """Without is_local, a video over 50 MB is refused before it uploads."""
        import playwright.sync_api

        seen = {}

        class Chromium:
            def connect_over_cdp(self, endpoint, **kwargs):
                seen.update(kwargs, endpoint=endpoint)
                return SimpleNamespace(contexts=["context"])

        @contextmanager
        def fake_sync_playwright():
            yield SimpleNamespace(chromium=Chromium())

        monkeypatch.setattr(playwright.sync_api, "sync_playwright", fake_sync_playwright)
        monkeypatch.setattr(session, "before_attach", None)
        with session.attach("http://127.0.0.1:9222") as context:
            assert context == "context"
        assert seen["is_local"] is True


class TestWaitingForTheUpload:
    def test_post_waits_until_the_upload_bar_has_gone(self):
        page = FakePage(uploads=[[0.0], [20.0], [55.0], [90.0]])
        outcome = make_poster(page).post(request(media_paths=(VIDEO,)))
        assert outcome.posted
        seen = looks(page)
        assert seen[:4] == [(0.0,), (20.0,), (55.0,), (90.0,)]
        last_look = max(i for i, call in enumerate(page.calls) if call[0] == "progress")
        assert last_look < post_clicked_at(page)

    def test_a_clickable_post_button_is_not_taken_as_done(self):
        """The live case: Post enabled at once, the video still going up."""
        page = FakePage(uploads=[[1.0], [2.0], [3.0]])
        make_poster(page).post(request(media_paths=(VIDEO,)))
        bars_seen = [found for found in looks(page) if found]
        assert len(bars_seen) == 3

    def test_a_disabled_post_button_is_waited_out_too(self):
        """Live, Post is disabled for a moment before the bar appears."""
        page = FakePage(post_disabled_polls=3)
        make_poster(page).post(request(media_paths=(VIDEO,)))
        assert len(looks(page)) >= 3 + 2

    def test_pictures_with_no_bar_go_straight_on(self):
        page = FakePage()
        make_poster(page).post(request(media_paths=(PICTURE,)))
        assert len(looks(page)) == 2

    def test_no_files_no_wait(self):
        page = FakePage()
        make_poster(page).post(request())
        assert looks(page) == []

    def test_a_stalled_upload_stops_before_post(self):
        page = FakePage(upload_stuck_at=12.0)
        poster = make_poster(page)
        with pytest.raises(UploadStalled, match="nothing was posted"):
            poster.post(request(media_paths=(VIDEO,)))
        assert not poster.clicked_post
        assert not page.clicked(POST_BUTTON)
        assert ("press", "Escape") in page.calls, "the draft was left behind"

    def test_it_is_movement_that_counts_not_time(self):
        """Slow is fine for as long as it keeps moving: far past five minutes."""
        crawl = [[i * 0.1] for i in range(STALL_LOOKS * 2)]
        page = FakePage(uploads=crawl)
        assert make_poster(page).post(request(media_paths=(VIDEO,))).posted

    def test_one_video_finishing_is_not_read_as_a_stall(self):
        """The total drops when a bar goes while the next carries on."""
        crawl = [[90.0, 5.0]] + [[5.0 + i * 0.1] for i in range(1, STALL_LOOKS + 10)]
        page = FakePage(uploads=crawl)
        assert make_poster(page).post(request(media_paths=(VIDEO, VIDEO))).posted

    def test_a_checkpoint_during_a_long_upload_halts(self):
        page = FakePage(upload_stuck_at=40.0)
        poster = make_poster(page)
        dialog = page.get_by_role("dialog").first
        page.body_text = strings.RATE_LIMIT_MARKERS[0]
        with pytest.raises(AutomationHalted):
            poster.wait_for_uploads(dialog)
        assert len(looks(page)) <= UPLOAD_GUARD_EVERY_MS // UPLOAD_POLL_MS + 1

    def test_a_dry_run_waits_for_the_upload_too(self):
        """So a rehearsal shows whether the video gets up at all."""
        page = FakePage(uploads=[[10.0], [70.0]])
        outcome = make_poster(page, dry_run=True).post(request(media_paths=(VIDEO,)))
        assert outcome.dry_run
        assert looks(page)[:2] == [(10.0,), (70.0,)]
        assert not page.clicked(POST_BUTTON)


def video_reply(story_id: str) -> str:
    """Facebook's reply to Post for a video, in the shape read live on
    2026-10-15 (the numbers here are made up)."""
    return json.dumps({
        "data": {"story_create": {
            "story_id": story_id, "post_id": None, "publishing_flow": "FALLBACK",
            "story": None, "group_feed_story_edge": None,
        }},
        "extensions": {"is_final": True},
    })


def story_id(text: str) -> str:
    return base64.b64encode(text.encode()).decode().rstrip("=")


GROUP = "https://www.facebook.com/groups/testgroup/"


class TestFacebooksReplyForAVideo:
    def test_the_post_number_is_read_from_the_story_id(self):
        reply = video_reply(story_id("S:_I1234:VK:987654321"))
        assert created_post_url(reply, GROUP) == f"{GROUP}permalink/987654321/"

    def test_padding_is_not_needed(self):
        """Facebook sends it unpadded; base64 needs the padding back."""
        for number in ("9", "98", "987"):
            reply = video_reply(story_id(f"S:_I1234:VK:{number}"))
            assert created_post_url(reply, GROUP) == f"{GROUP}permalink/{number}/"

    def test_anything_else_in_the_story_id_is_not_trusted(self):
        for junk in ("", "not base64!", story_id("hello"), story_id("S:_I1:VK:abc")):
            assert created_post_url(video_reply(junk), GROUP) == ""

    def test_without_the_group_there_is_no_link_to_build(self):
        assert created_post_url(video_reply(story_id("S:_I1:VK:42"))) == ""

    def test_a_video_post_is_confirmed_by_it_and_says_it_may_take_a_while(self):
        page = FakePage(created_reply=video_reply(story_id("S:_I1:VK:42")))
        outcome = make_poster(page).post(request(media_paths=(VIDEO,)))
        assert outcome.verified
        assert outcome.post_url.endswith("/permalink/42/")
        assert "few minutes" in outcome.detail

    def test_a_picture_post_does_not_say_so(self):
        page = FakePage(created_reply=video_reply(story_id("S:_I1:VK:42")))
        outcome = make_poster(page).post(request(media_paths=(PICTURE,)))
        assert "few minutes" not in outcome.detail


class TestCrashRecoveryGivesAVideoTimeToAppear:
    """A video post shows in neither the feed nor the pending list until
    Facebook has processed it. Recovery looking in that minute reads it as
    never posted and requeues it: a duplicate."""

    @pytest.fixture
    def interrupted(self, tmp_path):
        from fbposter.db.models import TARGET_RUNNING, utcnow
        from fbposter.db.repo import GroupRepo, TaskRepo

        from .test_worker import BODY, Clock, FakePoster, add_groups, make_worker

        db = fresh_database(tmp_path / "video.db")
        groups, tasks = GroupRepo(db), TaskRepo(db)
        one, _ = add_groups(groups)
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"video")
        task = tasks.create(BODY, [(one.id, BODY)], media_paths=[str(clip)])
        target = tasks.targets_for(task.id)[0]
        tasks.mark_target(target.id, TARGET_RUNNING, attempted=True)
        clock = Clock(start=utcnow())
        poster = FakePoster(verify_result=False)
        poster.verdict = "declined"
        yield tasks, task, poster, make_worker(db, poster, clock_fn=clock), clock
        db.close()

    def test_it_does_not_look_in_the_first_minutes(self, interrupted):
        from fbposter.db.models import TARGET_RUNNING

        tasks, task, poster, worker, clock = interrupted
        clock.advance(minutes=2)
        worker.recover()
        assert poster.verify_calls == [] and poster.verdict_calls == []
        assert tasks.targets_for(task.id)[0].state == TARGET_RUNNING

    def test_it_looks_once_the_video_has_had_time(self, interrupted):
        tasks, task, poster, worker, clock = interrupted
        clock.advance(minutes=11)
        worker.recover()
        assert poster.verify_calls and poster.verdict_calls


class TestTheCommandLine:
    def test_dry_run_takes_videos(self):
        import main as cli

        args = cli.build_parser().parse_args(
            ["dry-run", "https://www.facebook.com/groups/1/", "--video", "a.mp4",
             "--image", "b.jpg"]
        )
        assert args.video == ["a.mp4"]
        assert args.image == ["b.jpg"]
