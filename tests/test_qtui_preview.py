"""Tests for the Compose preview.

The preview answers one question — "will this read well when it lands in the
group?" — so its job is to be shaped like the destination. These tests pin the
parts of that shape that carry meaning: how many pictures are shown, at what
sizes, what happens to the ones that will not open, and that the logical text
is still recoverable however it is laid out.

They deliberately do not check pixels. Reading a conclusion off a rendered
image has already gone wrong twice in this project.
"""

from __future__ import annotations

import pytest

from fbposter.qtui import theme
from fbposter.qtui.views.compose import (
    BIG_TEXT_CHARS,
    MAX_POST_WIDTH,
    MAX_SINGLE_HEIGHT,
    MAX_TILES,
    MIN_POST_WIDTH,
    NO_TEXT,
    PostPreview,
    avatar,
    cover,
    image_size,
    load_tile,
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


def show(preview, text="", media=(), heading="Bikes Israel", sub="Just now"):
    preview.render(heading, sub, text, list(media))
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


class TestAvatar:
    def test_it_is_the_size_asked_for(self, qt_application):
        assert avatar("B", 40).size().width() == 40

    def test_an_empty_name_still_draws(self, qt_application):
        assert not avatar("").isNull()

    def test_a_hebrew_name_still_draws(self, qt_application):
        assert not avatar("יד שנייה").isNull()


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

    def test_beyond_four_the_rest_collapse_into_the_last_tile(self, preview, pics):
        show(preview, text=HEBREW,
             media=[pics[n] for n in ("wide", "square", "tall", "pano", "extra")])
        assert len(preview.tiles) == MAX_TILES

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

    def test_four_is_a_grid_of_equals(self, preview, pics):
        show(preview, text=HEBREW,
             media=[pics[n] for n in ("wide", "square", "tall", "pano")])
        sizes = {(t.width(), t.height()) for t in preview.tiles}
        assert len(sizes) == 1

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
        assert preview.tiles[0].height() <= MAX_SINGLE_HEIGHT

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
