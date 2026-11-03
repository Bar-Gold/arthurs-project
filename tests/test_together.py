"""One post to up to 10 groups, through Facebook's own "Add groups".

Read live on 2026-11-03 (`.claude/rules/automation.md`, "Several groups in
one post"): each ticked group gets a post of its own;
Facebook's reply names the first group's post only, so the others are looked
up on their own "Your content" pages; pictures go in before the ticks.

The worker side is tested against a fake poster that ticks what it is told
to; the poster's own check of one ticked group against a fake page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbposter import pace, strings
from fbposter.automation.detect import PageVerdict
from fbposter.automation.poster import (
    MAX_COMPANIONS,
    TOGETHER_FOUND,
    TOGETHER_NOT_FOUND,
    TOGETHER_PICTURES,
    Companion,
    CompanionResult,
    GroupPoster,
    PostOutcome,
    PostRequest,
)
from fbposter.db.models import (
    TARGET_AWAITING_APPROVAL,
    TARGET_DONE,
    TARGET_PENDING,
    TARGET_UNCONFIRMED,
    TASK_HALTED,
)
from fbposter.errors import AutomationHalted, ConnectionFailed, PostNotVerified

from .test_poster import quiet_humanizer
from .test_worker import BODY, Clock, FakePoster, add_groups, db, drain, make_worker, repos  # noqa: F401


class TickingPoster(FakePoster):
    """Ticks every companion it is given, unless told which to leave out,
    and reports each one as found, or as `results` scripts it."""

    def __init__(self, *, cannot_tick=(), results=None, raises=None, halt_after="") -> None:
        super().__init__(raises=raises)
        self.cannot_tick = set(cannot_tick)
        self.results = results or {}
        self.halt_after = halt_after

    def post(self, request):
        for companion in request.companions:
            if companion.group_url not in self.cannot_tick:
                request.added.append(companion.group_url)
        outcome = super().post(request)
        return PostOutcome(
            posted=outcome.posted,
            verified=outcome.verified,
            detail=outcome.detail,
            post_url=outcome.post_url,
            companions=tuple(
                CompanionResult(
                    c.group_url,
                    None if c.group_url in self.cannot_tick
                    else self.results.get(
                        c.group_url,
                        PostOutcome(posted=True, verified=True, detail=TOGETHER_FOUND),
                    ),
                )
                for c in request.companions
            ),
            halt_after=self.halt_after,
        )


def named_groups(groups, count):
    made = add_groups(groups, count)
    for index, group in enumerate(made):
        groups.set_name(group.id, f"Group {index}")
    return [groups.get(group.id) for group in made]


def states(tasks, task):
    return [target.state for target in tasks.targets_for(task.id)]


class TestWhichGroupsGoTogether:
    def test_up_to_ten_groups_go_in_one_post(self, db, repos):
        groups, tasks, _ = repos
        made = named_groups(groups, 12)
        task = tasks.create(BODY, [(g.id, BODY) for g in made])

        poster = TickingPoster()
        make_worker(db, poster).run_once()

        (request,) = poster.requests
        assert len(request.companions) == MAX_COMPANIONS
        assert [c.name for c in request.companions] == [f"Group {i}" for i in range(1, 10)]
        assert states(tasks, task) == [TARGET_DONE] * 10 + [TARGET_PENDING] * 2

    def test_80_groups_take_8_posts(self, db, repos):
        groups, tasks, _ = repos
        made = named_groups(groups, 80)
        task = tasks.create(BODY, [(g.id, BODY) for g in made])

        poster = TickingPoster()
        ticker = Clock()
        worker = make_worker(db, poster, ticker)
        for _ in range(8):
            worker.run_once()
            ticker.advance(minutes=30)

        assert len(poster.requests) == 8
        assert set(states(tasks, task)) == {TARGET_DONE}

    def test_only_groups_with_the_same_words(self, db, repos):
        groups, tasks, _ = repos
        one, two, three, four = named_groups(groups, 4)
        tasks.create(BODY, [(one.id, "v1"), (two.id, "v2"), (three.id, "v1"), (four.id, "v2")])

        poster = TickingPoster()
        make_worker(db, poster).run_once()

        (request,) = poster.requests
        assert request.body == "v1"
        assert [c.group_url for c in request.companions] == [three.url]

    def test_a_video_goes_together_too(self, db, repos, tmp_path):
        # The user's request, 2026-11-03; read live the same day: a 5 second
        # video reached all four test groups in one post.
        groups, tasks, _ = repos
        one, two = named_groups(groups, 2)
        video = tmp_path / "tour.mp4"
        video.write_bytes(b"0")
        tasks.create(BODY, [(one.id, BODY), (two.id, BODY)], media_paths=[str(video)])

        poster = TickingPoster()
        make_worker(db, poster).run_once()

        assert [c.group_url for c in poster.requests[0].companions] == [two.url]

    def test_never_a_group_without_a_name(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups, 2)
        groups.set_name(one.id, "Named")
        tasks.create(BODY, [(one.id, BODY), (two.id, BODY)])

        poster = TickingPoster()
        make_worker(db, poster).run_once()

        assert poster.requests[0].companions == ()

    def test_two_of_the_clients_groups_with_one_name_go_alone(self, db, repos):
        groups, tasks, _ = repos
        one, two, three = named_groups(groups, 3)
        groups.set_name(two.id, "Salad Recipes")
        groups.set_name(three.id, "salad  recipes")
        tasks.create(BODY, [(g.id, BODY) for g in (one, two, three)])

        poster = TickingPoster()
        make_worker(db, poster).run_once()

        assert [c.group_url for c in poster.requests[0].companions] == [two.url]

    def test_turned_off_each_group_goes_alone(self, db, repos):
        groups, tasks, settings = repos
        settings.set(pace.TOGETHER_KEY, "0")
        made = named_groups(groups, 3)
        tasks.create(BODY, [(g.id, BODY) for g in made])

        poster = TickingPoster()
        make_worker(db, poster).run_once()

        assert poster.requests[0].companions == ()

    def test_a_removed_group_is_never_added(self, db, repos):
        groups, tasks, _ = repos
        one, two, three = named_groups(groups, 3)
        task = tasks.create(BODY, [(g.id, BODY) for g in (one, two, three)])
        groups.remove(two.id)

        poster = TickingPoster()
        make_worker(db, poster).run_once()

        assert [c.group_url for c in poster.requests[0].companions] == [three.url]


class TestWhatIsRecorded:
    def test_each_group_is_recorded_as_found(self, db, repos):
        groups, tasks, _ = repos
        made = named_groups(groups, 3)
        task = tasks.create(BODY, [(g.id, BODY) for g in made])

        make_worker(db, TickingPoster()).run_once()

        targets = tasks.targets_for(task.id)
        assert [t.state for t in targets] == [TARGET_DONE] * 3
        assert all(t.posted_at is not None for t in targets)
        assert targets[1].error == ""

    def test_a_group_holding_it_waits_for_approval(self, db, repos):
        groups, tasks, _ = repos
        one, two = named_groups(groups, 2)
        task = tasks.create(BODY, [(one.id, BODY), (two.id, BODY)])
        held = PostOutcome(posted=True, pending=True, detail="held")

        make_worker(db, TickingPoster(results={two.url: held})).run_once()

        assert states(tasks, task) == [TARGET_DONE, TARGET_AWAITING_APPROVAL]

    def test_not_found_is_might_have_posted_never_not_posted(self, db, repos):
        groups, tasks, _ = repos
        one, two = named_groups(groups, 2)
        task = tasks.create(BODY, [(one.id, BODY), (two.id, BODY)])
        missing = PostOutcome(posted=True, unconfirmed=True, detail=TOGETHER_NOT_FOUND)

        make_worker(db, TickingPoster(results={two.url: missing})).run_once()

        assert states(tasks, task) == [TARGET_DONE, TARGET_UNCONFIRMED]

    def test_a_group_that_could_not_be_ticked_goes_back_in_the_queue(self, db, repos):
        groups, tasks, _ = repos
        one, two, three = named_groups(groups, 3)
        task = tasks.create(BODY, [(g.id, BODY) for g in (one, two, three)])

        poster = TickingPoster(cannot_tick={two.url})
        ticker = Clock()
        worker = make_worker(db, poster, ticker)
        worker.run_once()

        assert states(tasks, task) == [TARGET_DONE, TARGET_PENDING, TARGET_DONE]
        assert tasks.targets_for(task.id)[1].attempted_at is None
        ticker.advance(minutes=30)
        worker.run_once()
        assert poster.requests[1].group_url == two.url

    def test_one_gap_per_post_not_per_group(self, db, repos):
        groups, tasks, settings = repos
        made = named_groups(groups, 11)
        task = tasks.create(BODY, [(g.id, BODY) for g in made])

        ticker = Clock()
        make_worker(db, TickingPoster(), ticker).run_once()

        wait = (tasks.get(task.id).resume_at - ticker.now).total_seconds()
        assert 600 <= wait <= 1500  # the test profile's one gap

    def test_the_last_post_finishes_the_batch(self, db, repos):
        groups, tasks, _ = repos
        made = named_groups(groups, 3)
        task = tasks.create(BODY, [(g.id, BODY) for g in made])

        make_worker(db, TickingPoster()).run_once()

        assert tasks.get(task.id).finished_at is not None


class TestNeverPostTwice:
    def test_before_any_page_every_group_goes_back(self, db, repos):
        groups, tasks, _ = repos
        one, two = named_groups(groups, 2)
        task = tasks.create(BODY, [(one.id, BODY), (two.id, BODY)])

        poster = FakePoster(raises={one.url: ConnectionFailed("away")})
        make_worker(db, poster).run_once()

        assert states(tasks, task) == [TARGET_PENDING, TARGET_PENDING]

    def test_a_stop_after_post_marks_every_ticked_group(self, db, repos):
        groups, tasks, _ = repos
        one, two, three = named_groups(groups, 3)
        task = tasks.create(BODY, [(g.id, BODY) for g in (one, two, three)])

        class ClickedThenStopped(TickingPoster):
            def post(self, request):
                request.added.append(request.companions[0].group_url)
                raise PostNotVerified("clicked, then lost", may_have_posted=True)

        make_worker(db, ClickedThenStopped()).run_once()

        assert states(tasks, task) == [TARGET_UNCONFIRMED, TARGET_UNCONFIRMED, TARGET_PENDING]
        assert tasks.get(task.id).state == TASK_HALTED

    def test_a_stop_before_post_marks_none(self, db, repos):
        groups, tasks, _ = repos
        one, two = named_groups(groups, 2)
        task = tasks.create(BODY, [(one.id, BODY), (two.id, BODY)])

        class TickedThenStopped(TickingPoster):
            def post(self, request):
                request.added.append(request.companions[0].group_url)
                raise PostNotVerified("never clicked", may_have_posted=False)

        make_worker(db, TickedThenStopped()).run_once()

        assert states(tasks, task)[1] == TARGET_PENDING

    def test_a_checkpoint_while_checking_halts_after_recording(self, db, repos):
        groups, tasks, _ = repos
        one, two, three = named_groups(groups, 3)
        task = tasks.create(BODY, [(g.id, BODY) for g in (one, two, three)])

        make_worker(db, TickingPoster(halt_after="Security check.")).run_once()

        assert states(tasks, task) == [TARGET_DONE] * 3
        assert tasks.get(task.id).state == TASK_HALTED
        assert "Security check." in tasks.get(task.id).error

    def test_a_crash_leaves_every_ticked_group_running_for_recovery(self, db, repos):
        groups, tasks, _ = repos
        one, two = named_groups(groups, 2)
        task = tasks.create(BODY, [(one.id, BODY), (two.id, BODY)])

        class Crash(TickingPoster):
            def post(self, request):
                raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            make_worker(db, Crash()).run_once()

        # Not ticked: back in the queue. Recorded as may have posted only when
        # it was ticked, which this one was not.
        assert states(tasks, task)[1] == TARGET_PENDING


class Page:
    """Just enough of a page for `check_companion`: what each address shows."""

    def __init__(self, shows: dict[str, str]) -> None:
        self.shows = shows
        self.url = ""
        self.visited: list[str] = []

    def goto(self, url, **_kwargs):
        self.url = url
        self.visited.append(url)

    def wait_for_timeout(self, _ms):
        pass

    def inner_text(self, _selector):
        return self.shows.get(self.url, "")


GROUP = "https://www.facebook.com/groups/2509198906266893/"
WORDS = "Bright flat for sale near the station, three rooms"


class TestCheckingATickedGroup:
    def check(self, shows, body=WORDS, sent_before=False):
        page = Page(shows)
        poster = GroupPoster(page, humanizer=quiet_humanizer())
        return poster.check_companion(Companion(GROUP, "bar-test", sent_before), body), page

    def test_found_among_your_published_posts(self):
        outcome, page = self.check({strings.my_posted_url(GROUP): f"Your content {WORDS}"})
        assert outcome.verified and outcome.posted
        assert outcome.detail == TOGETHER_FOUND
        assert page.visited == [strings.my_posted_url(GROUP)]

    def test_found_among_your_pending_posts(self):
        outcome, _page = self.check({strings.my_content_url(GROUP): WORDS})
        assert outcome.pending

    def test_found_but_the_words_went_there_before(self):
        outcome, _page = self.check({strings.my_posted_url(GROUP): WORDS}, sent_before=True)
        assert outcome.unconfirmed

    def test_not_found_is_might_have_posted(self):
        outcome, _page = self.check({})
        assert outcome.posted and outcome.unconfirmed
        assert outcome.detail == TOGETHER_NOT_FOUND

    def test_pictures_alone_are_not_looked_for(self):
        outcome, page = self.check({}, body="")
        assert outcome.posted and not outcome.verified
        assert outcome.detail == TOGETHER_PICTURES
        assert page.visited == []

    def test_a_group_that_never_holds_posts_is_not_a_halt(self):
        # Its Pending tab reads "This content isn't available right now".
        outcome, _page = self.check({
            strings.my_posted_url(GROUP): WORDS,
            strings.my_content_url(GROUP): "This content isn't available right now",
        })
        assert outcome.verified

    def test_a_video_still_processing_is_looked_at_again(self):
        posted = strings.my_posted_url(GROUP)

        class Processing(Page):
            def inner_text(self, _selector):
                shown = self.visited.count(posted) >= 3
                return WORDS if shown and self.url == posted else ""

        page = Processing({})
        poster = GroupPoster(page, humanizer=quiet_humanizer())
        request = PostRequest(
            group_url="https://www.facebook.com/groups/1/", body=WORDS,
            media_paths=(Path("tour.mp4"),), companions=(Companion(GROUP, "bar-test"),),
        )
        first = PostOutcome(posted=True, verified=True, detail="ok")
        outcome = poster._with_companions(first, request, request.companions)

        (result,) = outcome.companions
        assert result.outcome.verified
        assert page.visited.count(posted) == 3

    def test_pictures_are_not_looked_at_again(self):
        page = Page({})
        poster = GroupPoster(page, humanizer=quiet_humanizer())
        request = PostRequest(
            group_url="https://www.facebook.com/groups/1/", body=WORDS,
            media_paths=(Path("flat.jpg"),), companions=(Companion(GROUP, "bar-test"),),
        )
        first = PostOutcome(posted=True, verified=True, detail="ok")
        outcome = poster._with_companions(first, request, request.companions)

        assert outcome.companions[0].outcome.detail == TOGETHER_NOT_FOUND
        assert page.visited.count(strings.my_posted_url(GROUP)) == 1

    def test_a_checkpoint_halts(self):
        page = Page({})
        page.goto = lambda url, **_k: setattr(page, "url", "https://www.facebook.com/checkpoint/123")
        poster = GroupPoster(page, humanizer=quiet_humanizer())
        with pytest.raises(AutomationHalted) as caught:
            poster.check_companion(Companion(GROUP, "bar-test"), WORDS)
        assert caught.value.verdict is PageVerdict.CHECKPOINT
