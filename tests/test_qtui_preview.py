"""Tests for the Compose preview.

The preview answers one question ("will this read well when it lands in the
group?"), so its job is to be shaped like the destination: a Facebook post, as
a computer and as a phone show it. These tests pin the parts of that shape
that carry meaning: how many pictures are shown, in which of Facebook's
layouts, where the text folds behind "See more", what happens to pictures that
will not open, and that the logical text is still recoverable however it is
laid out.

They deliberately do not check pixels. Reading a conclusion off a rendered
image has already gone wrong twice in this project.
"""

from __future__ import annotations

import pytest

from fbposter.qtui import theme
from fbposter.qtui import icons
from fbposter.qtui.views.compose import (
    BIG_SHARE,
    BIG_TEXT_CHARS,
    MAX_POST_WIDTH,
    MAX_SINGLE_SHAPE,
    MAX_TILES,
    MIN_POST_WIDTH,
    NO_TEXT,
    PHONE_WIDTH,
    SEE_MORE,
    SEE_MORE_CHARS,
    SEE_MORE_LINES,
    TILE_GAP,
    PostPreview,
    collage_layout,
    cover,
    image_size,
    load_tile,
    see_more_cut,
)

pytest.importorskip("PIL", reason="Pillow is needed to write test images")

HEBREW = 'מוכר אופניים חשמליים Xiaomi M365 במחיר 1800 ש"ח'
LONG = HEBREW + " " + "Serious buyers only, please message me here. " * 3


@pytest.fixture
def pics(tmp_path):
    """Real image files, in the aspect ratios that break naive layouts."""
    from PIL import Image

    made = {}
    for name, size in (
        ("wide", (1600, 900)),
        ("tall", (600, 2400)),
        ("square", (800, 800)),
        ("pano", (2400, 600)),
        ("extra", (900, 700)),
        ("sixth", (1000, 1000)),
    ):
        path = tmp_path / f"{name}.jpg"
        Image.new("RGB", size, (90, 120, 170)).save(path)
        made[name] = path
    made["missing"] = tmp_path / "not-there.jpg"
    return made


@pytest.fixture
def preview(qt_app):
    """A preview widget parented to a real window, so it has a viewport."""
    return qt_app.views["compose"].preview


def show(preview, text="", media=(), heading="Bikes Israel", note="Version 1 of 2"):
    preview.render(heading, note, text, list(media))
    return preview


class TestCover:
    """QPixmap needs a QApplication to exist; without qt_application these
    aborted the whole process rather than failing."""

    def test_it_returns_exactly_the_box_asked_for(self, qt_application, pics):
        from PySide6.QtGui import QPixmap

        out = cover(QPixmap(str(pics["wide"])), 200, 200)
        assert (out.width(), out.height()) == (200, 200)

    def test_a_tall_picture_is_cropped_not_squashed(self, qt_application, pics):
        from PySide6.QtGui import QPixmap

        out = cover(QPixmap(str(pics["tall"])), 300, 100)
        assert (out.width(), out.height()) == (300, 100)

    def test_a_null_pixmap_comes_back_untouched(self, qt_application):
        from PySide6.QtGui import QPixmap

        assert cover(QPixmap(), 100, 100).isNull()

    def test_a_zero_box_does_not_raise(self, qt_application, pics):
        from PySide6.QtGui import QPixmap

        cover(QPixmap(str(pics["wide"])), 0, 0)


class TestTheGroupPicture:
    """Facebook's grey tile for a group with no cover photo: the real one is
    not known to the app, and a made-up one would say something untrue."""

    def test_it_is_drawn_at_the_size_asked_for(self, qt_application):
        picture = icons.group_picture(40)
        assert not picture.isNull()
        assert round(picture.width() / picture.devicePixelRatio()) == 40


class TestTheText:
    def test_the_logical_text_is_recoverable(self, preview):
        show(preview, text=HEBREW)
        assert preview.text_shown() == HEBREW

    def test_trailing_whitespace_is_trimmed(self, preview):
        show(preview, text="Selling a bike.\n\n  ")
        assert preview.text_shown() == "Selling a bike."

    def test_an_image_only_post_says_so(self, preview, pics):
        show(preview, text="", media=[pics["wide"]])
        assert preview.text_shown() == NO_TEXT

    def test_nothing_at_all_shows_the_empty_state(self, preview):
        show(preview, text="", media=[])
        assert preview.text_shown() == ""
        assert preview.tiles == []
        assert preview.message_label is None

    def test_short_text_without_pictures_is_set_large(self, preview):
        """What a feed does, and most of why this reads as a post."""
        show(preview, text="Selling a bike, 1800.")
        assert f"{theme.SIZE_TITLE}px" in preview.message_label.styleSheet()

    def test_long_text_is_not(self, preview):
        show(preview, text=LONG)
        assert len(LONG) > BIG_TEXT_CHARS
        assert f"{theme.SIZE_TITLE}px" not in preview.message_label.styleSheet()

    def test_short_text_with_a_picture_is_not_either(self, preview, pics):
        show(preview, text="Selling a bike.", media=[pics["wide"]])
        assert f"{theme.SIZE_TITLE}px" not in preview.message_label.styleSheet()


class TestTheCollage:
    @pytest.mark.parametrize("count", [1, 2, 3, 4])
    def test_every_picture_gets_a_tile(self, preview, pics, count):
        names = ["wide", "square", "tall", "pano"][:count]
        show(preview, text=HEBREW, media=[pics[n] for n in names])
        assert len(preview.tiles) == count

    def test_beyond_five_the_rest_collapse_into_the_last_tile(self, preview, pics):
        show(preview, text=HEBREW,
             media=[pics[n] for n in ("wide", "square", "tall", "pano", "extra", "sixth")])
        assert len(preview.tiles) == MAX_TILES == 5

    def test_a_pair_is_side_by_side_and_equal(self, preview, pics):
        show(preview, text=HEBREW, media=[pics["wide"], pics["tall"]])
        one, two = preview.tiles
        assert one.width() == two.width()
        assert one.height() == two.height()

    def test_a_pair_of_wildly_different_shapes_still_matches(self, preview, pics):
        """The reason tiles crop rather than fit."""
        show(preview, text=HEBREW, media=[pics["pano"], pics["tall"]])
        one, two = preview.tiles
        assert one.size() == two.size()

    def test_three_puts_one_wide_above_two(self, preview, pics):
        show(preview, text=HEBREW,
             media=[pics["wide"], pics["square"], pics["tall"]])
        top, left, right = preview.tiles
        assert top.width() > left.width()
        assert left.size() == right.size()

    def test_four_squares_are_a_grid_of_equals(self, preview, pics):
        show(preview, text=HEBREW,
             media=[pics[n] for n in ("square", "wide", "tall", "pano")])
        sizes = {(t.width(), t.height()) for t in preview.tiles}
        assert len(sizes) == 1

    def test_four_after_a_wide_one_are_one_above_three(self, preview, pics):
        show(preview, text=HEBREW,
             media=[pics[n] for n in ("wide", "square", "tall", "pano")])
        top, *under = preview.tiles
        assert top.width() > 2 * under[0].width()
        assert len({t.y() for t in under}) == 1

    def test_three_after_a_tall_one_stack_two_beside_it(self, preview, pics):
        show(preview, text=HEBREW,
             media=[pics[n] for n in ("tall", "square", "wide")])
        big, upper, lower = preview.tiles
        assert big.height() > upper.height()
        assert upper.x() == lower.x() > big.x()

    def test_five_are_two_above_three(self, preview, pics):
        show(preview, text=HEBREW,
             media=[pics[n] for n in ("wide", "square", "tall", "pano", "extra")])
        rows = {}
        for tile in preview.tiles:
            rows.setdefault(tile.y(), []).append(tile)
        assert sorted(len(r) for r in rows.values()) == [2, 3]

    def test_the_phone_draws_the_same_layout(self, preview, pics):
        show(preview, text=HEBREW,
             media=[pics[n] for n in ("tall", "square", "wide")])
        assert len(preview.tiles_of("phone")) == 3

    def test_tiles_never_exceed_the_post_width(self, preview, pics):
        for count in range(1, 6):
            names = ["wide", "square", "tall", "pano", "extra"][:count]
            show(preview, text=HEBREW, media=[pics[n] for n in names])
            for tile in preview.tiles:
                assert tile.width() <= preview.post_width()


class TestSinglePictureSizing:
    def test_a_wide_picture_keeps_its_shape(self, preview, pics):
        show(preview, text=HEBREW, media=[pics["wide"]])
        tile = preview.tiles[0]
        # 16:9 within a pixel or two of rounding.
        assert abs(tile.height() - tile.width() * 9 / 16) <= 2

    def test_a_very_tall_picture_is_capped(self, preview, pics):
        """Otherwise one photo pushes the whole post off the screen."""
        show(preview, text=HEBREW, media=[pics["tall"]])
        tile = preview.tiles[0]
        assert tile.height() <= MAX_SINGLE_SHAPE["computer"] * tile.width() + 1

    def test_a_panorama_is_not_stretched_to_a_minimum(self, preview, pics):
        show(preview, text=HEBREW, media=[pics["pano"]])
        tile = preview.tiles[0]
        assert tile.height() < tile.width()


class TestPicturesThatWillNotOpen:
    def test_a_missing_file_does_not_raise(self, preview, pics):
        show(preview, text=HEBREW, media=[pics["missing"]])

    def test_it_is_still_given_a_tile(self, preview, pics):
        """The file is still going to be uploaded; showing nothing would
        suggest it had been dropped."""
        show(preview, text=HEBREW, media=[pics["missing"]])
        assert len(preview.tiles) == 1
        assert preview.tiles[0].readable is False

    def test_the_tile_names_the_file(self, preview, pics):
        show(preview, text=HEBREW, media=[pics["missing"]])
        assert "not-there.jpg" in preview.tiles[0].text()

    def test_one_bad_picture_does_not_take_the_others_with_it(self, preview, pics):
        show(preview, text=HEBREW, media=[pics["wide"], pics["missing"]])
        assert [t.readable for t in preview.tiles] == [True, False]

    def test_the_layout_still_holds_its_shape(self, preview, pics):
        show(preview, text=HEBREW, media=[pics["wide"], pics["missing"]])
        one, two = preview.tiles
        assert one.size() == two.size()


class TestPostWidth:
    def test_it_stays_inside_the_feed_shaped_range(self, preview):
        assert MIN_POST_WIDTH <= preview.post_width() <= MAX_POST_WIDTH

    def test_the_card_never_forces_a_horizontal_scrollbar(self, preview, pics):
        from PySide6.QtCore import Qt

        show(preview, text=HEBREW, media=[pics["wide"]])
        assert preview.area.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff


@pytest.fixture
def shown(qt_application, qt_app):
    """The window shown (offscreen), because a hidden one is never laid out
    at its real size and the box's height is what these tests are about."""
    qt_app.show()
    yield qt_app
    qt_app.hide()


def in_preview(qt_app, media, text=HEBREW, size=theme.WINDOW_DEFAULT):
    """Compose in Preview, at a real window size."""
    from PySide6.QtWidgets import QApplication

    qt_app.show_view("compose")
    compose = qt_app.views["compose"]
    compose.editor.setPlainText(text)
    compose.attachments = list(media)
    compose.set_mode("preview")
    qt_app.resize(*size)
    # The redraw on resize builds a new post; let the scroll area see its size.
    QApplication.processEvents()
    QApplication.processEvents()
    return compose.preview


def scrolls(preview) -> bool:
    return preview.area.verticalScrollBar().maximum() > 0


class TestThePostFitsTheBox:
    """Reported 2026-10-22: with three pictures the box scrolled at the
    window's usual size, and Like/Comment/Share was out of sight."""

    NAMES = ["wide", "square", "tall", "pano", "extra"]

    @pytest.mark.parametrize("count", [1, 2, 3, 4, 5])
    def test_a_short_post_does_not_scroll(self, shown, pics, count):
        preview = in_preview(shown, [pics[n] for n in self.NAMES[:count]])
        assert not scrolls(preview)

    @pytest.mark.parametrize("count", [3, 4])
    def test_nor_in_the_smallest_window(self, shown, pics, count):
        preview = in_preview(shown, [pics[n] for n in self.NAMES[:count]],
                             size=theme.WINDOW_MIN)
        assert not scrolls(preview)

    def test_the_whole_post_shrinks_and_keeps_its_shape(self, shown, pics):
        """Narrowing the post rather than squashing its pictures, which would
        crop them to a shape the group will never see."""
        preview = in_preview(shown, [pics[n] for n in self.NAMES[:3]], size=(1120, 800))
        top = preview.tiles[0]
        assert top.width() < preview.post_width(), "the post was not narrowed"
        assert abs(top.height() - BIG_SHARE * top.width()) <= TILE_GAP

    def test_the_phone_shrinks_with_it(self, shown, pics):
        """So it stays as much narrower than the computer as it really is."""
        preview = in_preview(shown, [pics[n] for n in self.NAMES[:3]], size=(1120, 800))
        computer, phone = (preview.posts[k].width() for k in ("computer", "phone"))
        assert abs(phone - computer * PHONE_WIDTH / MAX_POST_WIDTH) <= 1

    def test_a_post_that_fits_is_not_shrunk(self, shown, pics):
        preview = in_preview(shown, [pics["square"], pics["sixth"]])
        assert preview.posts["computer"].width() == preview.post_width()

    def test_a_taller_window_gives_the_room_back(self, shown, pics):
        preview = in_preview(shown, [pics[n] for n in self.NAMES[:3]],
                             size=theme.WINDOW_MIN)
        narrow = preview.tiles[0].width()
        in_preview(shown, [pics[n] for n in self.NAMES[:3]], size=(1120, 1400))
        assert preview.tiles[0].width() > narrow

    @pytest.mark.parametrize("count", [3, 5])
    def test_a_long_post_with_pictures_does_not_scroll(self, shown, pics, count):
        """Reported 2026-10-22 on a maximised window 1366 by 697: a long post
        with pictures could not fit, was drawn full size, and scrolled."""
        lines = "\n".join(
            ["Four rooms for sale on Herzl Street, third floor of five"] * 7
        )
        preview = in_preview(shown, [pics[n] for n in self.NAMES[:count]],
                             text=lines, size=(1366, 697))
        assert not scrolls(preview)

    def test_text_too_long_to_fit_scrolls_at_full_width(self, shown, pics):
        """Opened with "See more". Shrinking it would only make it harder to
        read."""
        from PySide6.QtWidgets import QApplication

        preview = in_preview(shown, [pics[n] for n in self.NAMES[:3]],
                             text="\n".join([LONG] * 8))
        preview._see_more("computer")
        QApplication.processEvents()
        assert scrolls(preview)
        assert preview.posts["computer"].width() == preview.post_width()


class TestAComputerAndAPhone:
    """Side by side, the user's choice (2026-10-22): a post folds and fits
    differently on each."""

    def test_both_are_drawn(self, preview, pics):
        show(preview, text=HEBREW, media=[pics["wide"]])
        assert set(preview.posts) == {"computer", "phone"}

    def test_each_is_named(self, preview):
        from PySide6.QtWidgets import QLabel

        show(preview, text=HEBREW)
        captions = [l.text() for l in preview.findChildren(QLabel, "PreviewCaption")]
        assert captions == ["Computer", "Phone"]

    def test_the_phone_is_the_narrower(self, shown, pics):
        preview = in_preview(shown, [pics["wide"]], size=(1400, 900))
        assert preview.posts["phone"].width() < preview.posts["computer"].width()

    def test_the_note_is_above_the_posts_not_in_them(self, preview):
        """The posts carry only what Facebook shows: "Just now", not the app's
        own word on which version."""
        from PySide6.QtWidgets import QLabel

        show(preview, text=HEBREW, note="Version 1 of 2")
        for post in preview.posts.values():
            words = [l.text() for l in post.findChildren(QLabel)]
            assert "Just now" in words
            assert "Version 1 of 2" not in words
        notes = [l.text() for l in preview.findChildren(QLabel, "PreviewNote")]
        assert notes == ["Version 1 of 2"]

    def test_a_narrow_box_puts_the_phone_under_the_computer(self, qt_application, pics):
        """The repeating-post editor's column is too narrow for both side by
        side, and the phone post was cut off at its edge."""
        from .conftest import discard

        preview = PostPreview()
        preview.resize(470, 700)
        preview.show()
        show(preview, text=HEBREW, media=[pics["wide"], pics["square"], pics["tall"]])
        qt_application.processEvents()
        computer, phone = preview.posts["computer"], preview.posts["phone"]
        viewport = preview.area.viewport()
        assert phone.mapTo(viewport, phone.rect().topLeft()).y() > computer.mapTo(
            viewport, computer.rect().bottomLeft()).y()
        assert phone.mapTo(viewport, phone.rect().topRight()).x() <= viewport.width()
        preview.hide()
        discard(preview)

    def test_like_comment_share_never_sit_on_the_pictures(self, qt_application, pics):
        """Centred in a column, a post was measured as wide as the box and
        drawn a line short, with its last row over its pictures."""
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QLabel

        from .conftest import discard

        preview = PostPreview()
        preview.resize(470, 700)
        preview.show()
        show(preview, text=LONG, media=[pics["wide"], pics["square"], pics["tall"]])
        qt_application.processEvents()
        for kind, post in preview.posts.items():
            pictures = max(t.mapTo(post, QPoint(0, t.height())).y() for t in preview.tiles_of(kind))
            like = next(l for l in post.findChildren(QLabel, "FbAction") if l.text() == "Like")
            assert like.mapTo(post, QPoint(0, 0)).y() >= pictures, kind
        preview.hide()
        discard(preview)


class TestSeeMore:
    """Facebook folds long text behind "See more", sooner on a phone. It folds
    by lines on the screen, so the cut is near Facebook's, not exact."""

    def test_short_text_is_shown_whole(self):
        assert see_more_cut("Selling a bike.", "computer") is None
        assert see_more_cut("Selling a bike.", "phone") is None

    def test_a_phone_folds_sooner(self):
        text = "Selling a road bike, barely used, " * 5
        assert see_more_cut(text, "computer") is None
        assert see_more_cut(text, "phone") is not None

    def test_never_mid_word(self):
        text = "word " * 100
        shown = see_more_cut(text, "phone")
        assert len(shown) <= SEE_MORE_CHARS["phone"]
        assert shown.endswith("word")

    def test_many_short_lines_fold_too(self):
        lines = "\n".join(f"line {n}" for n in range(10))
        assert see_more_cut(lines, "computer").count("\n") == SEE_MORE_LINES["computer"] - 1
        assert see_more_cut(lines, "phone").count("\n") == SEE_MORE_LINES["phone"] - 1

    def test_the_folded_post_offers_see_more(self, preview):
        show(preview, text="Selling a road bike, barely used, " * 5)
        phone = preview.posts["phone"].findChild(type(preview.message_label), "FbText")
        assert SEE_MORE.replace(" ", "&nbsp;") in phone.text()
        assert SEE_MORE not in preview.message_label.text()

    def test_see_more_opens_the_whole_text(self, preview, qt_application):
        text = "Selling a road bike, barely used, " * 5
        show(preview, text=text)
        preview._see_more("phone")
        qt_application.processEvents()
        phone = preview.posts["phone"].findChild(type(preview.message_label), "FbText")
        assert phone.text() == text.rstrip()

    def test_new_text_folds_again(self, preview, qt_application):
        show(preview, text="Selling a road bike, barely used, " * 5)
        preview._see_more("phone")
        qt_application.processEvents()
        show(preview, text="Selling a mountain bike, like new, " * 5)
        phone = preview.posts["phone"].findChild(type(preview.message_label), "FbText")
        assert "See" in phone.text() and "more" in phone.text()

    def test_it_folds_by_the_lines_on_the_screen(self, preview):
        """Not the lines typed: one long line runs to many in a narrow post,
        and folded at five typed lines a long post could not fit the box."""
        text = "Selling a road bike, barely used, serviced last month. " * 8
        assert see_more_cut(text, "computer") is None, "folded by its typed lines"
        show(preview, text=text)
        for kind in ("computer", "phone"):
            message, post = preview.messages[kind], preview.posts[kind]
            assert "See" in message.text(), kind
            inside = post.width() - 2 * post.frameWidth()
            assert message.heightForWidth(inside) <= preview._text_cap(kind), kind

    def test_the_logical_text_is_still_whole(self, preview):
        text = "Selling a road bike, barely used, " * 20
        show(preview, text=text)
        assert preview.text_shown() == text.rstrip()


class TestFacebooksLayouts:
    """Where Facebook puts 2 to 5 pictures, picked by the first one's shape."""

    @pytest.mark.parametrize("count", [2, 3, 4, 5])
    @pytest.mark.parametrize("shape", ["landscape", "portrait", "square"])
    def test_every_picture_has_a_place_and_none_overlap(self, count, shape):
        tall, places = collage_layout(count, shape)
        assert len(places) == count
        area = 0.0
        for x, y, wide, high in places:
            assert 0 <= x and x + wide <= 1 + 1e-9
            assert 0 <= y and y + high <= tall + 1e-9
            area += wide * high
        # Together they fill the collage exactly, so none overlap.
        assert abs(area - tall) < 1e-9

    def test_two_wide_ones_stack(self):
        _tall, (first, second) = collage_layout(2, "landscape")
        assert first[0] == second[0] and second[1] > first[1]

    def test_two_tall_ones_sit_side_by_side(self):
        _tall, (first, second) = collage_layout(2, "portrait")
        assert first[1] == second[1] and second[0] > first[0]


class TestBackToWriteOpensNoWindow:
    """Reported 2026-10-22: switching from Preview to Write showed a small
    window for a moment. The preview was redrawn once for each writing tool
    coming back, and a post built and cleared before Qt had shown it opened
    as a window of its own."""

    def test_no_window_opens_and_the_hidden_preview_is_not_redrawn(
        self, shown, pics, monkeypatch
    ):
        from PySide6.QtCore import QEvent, QObject
        from PySide6.QtWidgets import QApplication

        preview = in_preview(shown, [pics[n] for n in ("wide", "square", "tall")])
        compose = shown.views["compose"]
        redraws = []
        redraw = preview._redraw
        monkeypatch.setattr(preview, "_redraw", lambda: (redraws.append(1), redraw()))

        class Windows(QObject):
            opened: list[str] = []

            def eventFilter(self, watched, event):  # noqa: N802 - Qt's name
                if (event.type() == QEvent.Show and watched.isWidgetType()
                        and watched.isWindow() and watched is not shown):
                    self.opened.append(type(watched).__name__)
                return False

        spy = Windows()
        QApplication.instance().installEventFilter(spy)
        try:
            compose.write_button.click()
            QApplication.processEvents()
            QApplication.processEvents()
        finally:
            QApplication.instance().removeEventFilter(spy)
        assert spy.opened == [], f"{spy.opened} opened as windows of their own"
        assert redraws == [], "the preview was redrawn on its way out"


class TestRerendering:
    def test_rendering_twice_does_not_leave_stale_tiles(self, preview, pics):
        """takeAt() does not unparent, so the old collage used to survive
        underneath the new one until the event loop caught up."""
        show(preview, text=HEBREW, media=[pics["wide"], pics["square"]])
        show(preview, text=HEBREW, media=[pics["wide"]])
        assert len(preview.tiles) == 1

    def test_going_back_to_empty_clears_everything(self, preview, pics):
        show(preview, text=HEBREW, media=[pics["wide"]])
        show(preview, text="", media=[])
        assert preview.tiles == []


@pytest.fixture
def big_photo(tmp_path):
    """A photo the size a phone takes them, stored sideways like a phone does.

    The real one that started this was 5712x4284 and 8.33MB; this is the same
    shape, small enough to keep the suite quick.
    """
    from PIL import Image

    path = tmp_path / "phone.jpg"
    image = Image.new("RGB", (2400, 1800), (120, 90, 60))
    exif = Image.Exif()
    exif[274] = 6                     # Orientation: rotate 90 clockwise
    image.save(path, exif=exif, quality=80)
    return path


class TestABigPhotoIsNotDecodedWhole:
    """`QPixmap(path)` decodes every pixel. The real 24-megapixel photo took
    ~600ms, the preview paid it once to measure the picture and again to draw
    it, and it paid the pair again on every redraw -- a resize, a switch to
    Preview, a walk to Groups and back. Loading a template and stepping between
    screens cost over a second a time.
    """

    def test_the_decode_is_scaled_to_the_tile(self, qt_application, big_photo):
        tile = load_tile(big_photo, 240, 250)
        assert not tile.isNull(), "the photo did not load at all"
        # Big enough to fill the box, nowhere near the stored 2400x1800.
        assert tile.width() >= 240 and tile.height() >= 250
        assert tile.width() * tile.height() < 2400 * 1800 / 4, (
            f"decoded {tile.width()}x{tile.height()}: close to full resolution"
        )

    def test_the_second_ask_is_not_decoded_again(self, qt_application, big_photo):
        first = load_tile(big_photo, 240, 250)
        second = load_tile(big_photo, 240, 250)
        assert first is second, "decoded the same tile twice"

    def test_a_different_box_is_decoded_separately(self, qt_application, big_photo):
        assert load_tile(big_photo, 240, 250) is not load_tile(big_photo, 480, 270)

    def test_an_edited_file_is_picked_up(self, qt_application, big_photo):
        from PIL import Image

        first = load_tile(big_photo, 240, 250)
        Image.new("RGB", (2400, 1800), (10, 200, 10)).save(big_photo, quality=80)
        assert load_tile(big_photo, 240, 250) is not first, "served a stale picture"

    def test_measuring_a_picture_does_not_decode_it(self, qt_application, big_photo):
        """The size comes off the header. This is the read that ran per render
        purely to work out how tall one picture should be."""
        assert image_size(big_photo) is not None

    def test_a_file_that_will_not_open_returns_nothing_and_does_not_raise(
        self, qt_application, tmp_path
    ):
        """A named tile is drawn from this; the file is still going to be
        uploaded, so showing nothing would suggest it had been dropped."""
        assert load_tile(tmp_path / "not-there.jpg", 240, 250).isNull()
        assert image_size(tmp_path / "not-there.jpg") is None

        broken = tmp_path / "broken.jpg"
        broken.write_bytes(b"this is not a picture")
        assert load_tile(broken, 240, 250).isNull()
        assert image_size(broken) is None


class TestTheOrientationTheCameraRecorded:
    """A phone stores a portrait photo sideways with "rotate 90" in the header.
    Facebook applies that when it renders, so a preview that ignored it was
    answering the wrong question -- and, worse, laid the picture out with its
    width and height the wrong way round.
    """

    def test_a_sideways_photo_is_measured_upright(self, qt_application, big_photo):
        size = image_size(big_photo)
        assert size.width() < size.height(), (
            f"{size.width()}x{size.height()}: still the stored landscape shape"
        )

    def test_the_tile_fills_the_box_it_was_asked_for(self, qt_application, big_photo):
        """Whatever the rotation, cover() must still get something it can crop
        to the exact slot without upscaling."""
        tile = load_tile(big_photo, 240, 250)
        assert tile.width() >= 240 and tile.height() >= 250
        shown = cover(tile, 240, 250)
        assert (shown.width(), shown.height()) == (240, 250)
