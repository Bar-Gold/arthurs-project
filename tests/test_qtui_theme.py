"""Guards for the visual decisions, so they cannot quietly regress.

These are not screenshot tests. Each one pins a rule that was broken at some
point and is invisible until someone hits it: focus that cannot be seen,
body text below the readable floor, a colour that only exists in one mode.

The contrast check is the one worth keeping honest: it computes real WCAG
ratios rather than trusting that the palette "looks fine". There is one
palette: dark mode was removed (the user's choice, 2026-10-20).
"""

from __future__ import annotations

import pytest

from fbposter.qtui import theme
from fbposter.qtui.app import FLOW_STEPS, NAV_HINTS, NAV_ITEMS

# WCAG AA for normal-size text.
MIN_CONTRAST = 4.5
# AA for large/bold text (>=18.66px bold or >=24px regular).
MIN_CONTRAST_LARGE = 3.0


def _channel(value: float) -> float:
    value /= 255
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    raw = hex_colour.lstrip("#")
    r, g, b = (int(raw[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(one: str, two: str) -> float:
    a, b = sorted((luminance(one), luminance(two)), reverse=True)
    return (a + 0.05) / (b + 0.05)


class TestContrast:
    """Priority 1: 4.5:1 for body text, in both modes."""

    @pytest.mark.parametrize("palette_name", ["LIGHT"])
    @pytest.mark.parametrize(
        "fg,bg",
        [
            ("TEXT", "WINDOW_BG"),
            ("TEXT", "SURFACE"),
            ("TEXT_MUTED", "WINDOW_BG"),
            ("TEXT_MUTED", "SURFACE"),
            # Home's headline, line, greeting and status sit on its light.
            ("TEXT", "HOME_GLOW"),
            ("TEXT_MUTED", "HOME_GLOW"),
            ("TEXT_ON_ACCENT", "ACCENT"),
            ("SUCCESS", "SURFACE"),
            ("WARNING", "SURFACE"),
            ("DANGER", "SURFACE"),
            # The accent as text, which is a different value from the fill.
            ("ACCENT_TEXT", "SURFACE"),
            ("ACCENT_TEXT", "NAV_ACTIVE_BG"),
            ("ACCENT_TEXT", "WINDOW_BG"),
            ("NEUTRAL", "SURFACE"),
            # The toast line sits on the window colour, in every level's
            # colour. DANGER failed here at 4.39:1 and nothing checked it.
            ("SUCCESS", "WINDOW_BG"),
            ("WARNING", "WINDOW_BG"),
            ("DANGER", "WINDOW_BG"),
            # Labels on a hovered or pressed button.
            ("TEXT", "HOVER_BG"),
            ("TEXT", "PRESSED_BG"),
            # A typing box or button under the pointer, and the hint in an
            # empty box, at rest and hovered.
            ("TEXT", "FIELD_HOVER"),
            # A group's row on step 2: ticked, and under the pointer, with its
            # name, its grey member count and "in <set>" (2026-10-24).
            ("TEXT", "ROW_PICKED"),
            ("TEXT_MUTED", "ROW_PICKED"),
            ("TEXT_MUTED", "FIELD_HOVER"),
            ("TEXT_MUTED", "NAV_ACTIVE_BG"),
            ("PLACEHOLDER", "SURFACE"),
            ("PLACEHOLDER", "FIELD_HOVER"),
            # "Remove" / "Delete" / "Cancel" on the tint they gain as you
            # reach for them. The first tint chosen failed at 4.32:1.
            ("DANGER", "DANGER_SOFT"),
            ("DANGER", "DANGER_PRESSED_BG"),
            # Status badges: each colour on its own tint (theme.BADGE_TINTS).
            # The first green tint tried read 4.41:1.
            ("SUCCESS", "SUCCESS_SOFT"),
            ("WARNING", "WARNING_SOFT"),
            ("ACCENT_TEXT", "NAV_ACTIVE_BG"),
            ("TEXT_MUTED", "NEUTRAL_SOFT"),
            # A joined toggle: the choices not taken, on the track, and the
            # one taken, on its raised segment.
            ("TEXT_MUTED", "SEGMENT_TRACK"),
            ("TEXT", "SEGMENT_TRACK"),
            ("TEXT", "SEGMENT_ON"),
            # The navy sidebar (2026-10-20) has colours of its own, because
            # the page's are dark. Its names, at rest, hovered,
            # pressed, and the one you are on in gold.
            ("SIDEBAR_TEXT", "SIDEBAR_BG"),
            ("SIDEBAR_TEXT", "SIDEBAR_HOVER"),
            ("SIDEBAR_TEXT", "SIDEBAR_PRESSED"),
            ("SIDEBAR_MUTED", "SIDEBAR_BG"),
            ("SIDEBAR_ACTIVE", "SIDEBAR_BG"),
            ("SIDEBAR_ACTIVE", "SIDEBAR_ACTIVE_BG"),
            # The connection's words, which sit on the sidebar itself. The
            # pill was the quietest failure of the lot.
            ("SIDEBAR_SUCCESS", "SIDEBAR_BG"),
            ("SIDEBAR_DANGER", "SIDEBAR_BG"),
            ("SIDEBAR_NEUTRAL", "SIDEBAR_BG"),
            # "Posting is paused", and the same line as you reach for it.
            # Pressed, it turns SIDEBAR_TEXT.
            ("SIDEBAR_WARNING", "SIDEBAR_BG"),
            ("SIDEBAR_WARNING", "SIDEBAR_HOVER"),
            # Preview, in Facebook's own colours (2026-10-22): a post's words
            # and its grey ones, and the note and captions on the feed's grey.
            ("FB_TEXT", "FB_CARD"),
            ("FB_MUTED", "FB_CARD"),
            ("FB_MUTED", "FB_FEED"),
        ],
    )
    def test_text_is_readable(self, palette_name, fg, bg):
        palette = getattr(theme, palette_name)
        ratio = contrast(palette[fg], palette[bg])
        assert ratio >= MIN_CONTRAST, (
            f"{palette_name}: {fg} on {bg} is {ratio:.2f}:1, under {MIN_CONTRAST}:1"
        )

    @pytest.mark.parametrize("palette_name", ["LIGHT"])
    def test_white_is_readable_on_the_primary_button(self, palette_name):
        """Facebook's own #1877F2 puts white at 4.23:1, just under the floor."""
        palette = getattr(theme, palette_name)
        assert contrast(palette["ACCENT"], palette["TEXT_ON_ACCENT"]) >= MIN_CONTRAST
        assert contrast(palette["ACCENT_HOVER"], palette["TEXT_ON_ACCENT"]) >= MIN_CONTRAST
        assert contrast(palette["ACCENT_PRESSED"], palette["TEXT_ON_ACCENT"]) >= MIN_CONTRAST

    @pytest.mark.parametrize("palette_name", ["LIGHT"])
    def test_the_focus_ring_stands_out_from_its_surface(self, palette_name):
        """A focus ring nobody can see is the same as no focus ring."""
        palette = getattr(theme, palette_name)
        ratio = contrast(palette["ACCENT"], palette["SURFACE"])
        assert ratio >= MIN_CONTRAST_LARGE

    @pytest.mark.parametrize("palette_name", ["LIGHT"])
    @pytest.mark.parametrize("bg", ["WINDOW_BG", "SURFACE"])
    def test_the_focus_ring_clears_the_indicator_bar(self, palette_name, bg):
        """WCAG 2.2 wants 3:1 for a focus indicator against what is next to it."""
        palette = getattr(theme, palette_name)
        assert contrast(palette["ACCENT"], palette[bg]) >= MIN_CONTRAST_LARGE

    @pytest.mark.parametrize("palette_name", ["LIGHT"])
    @pytest.mark.parametrize("bg", ["SIDEBAR_BG", "SIDEBAR_ACTIVE_BG"])
    def test_the_sidebar_focus_ring_is_gold(self, palette_name, bg):
        """The sidebar is navy, where a navy ring is no ring."""
        palette = getattr(theme, palette_name)
        assert contrast(palette["SIDEBAR_ACTIVE"], palette[bg]) >= MIN_CONTRAST_LARGE
        theme.activate()
        sheet = theme.stylesheet()
        nav_focus = sheet.split("QPushButton#Nav:focus")[1][:200]
        assert palette["SIDEBAR_ACTIVE"] in nav_focus

    @pytest.mark.parametrize("palette_name", ["LIGHT"])
    @pytest.mark.parametrize("fg", ["GOLD", "INFO"])
    def test_the_gold_outline_and_the_chart_colour_show(self, palette_name, fg):
        """The chosen tab's outline and the "waiting for an admin" share of
        the Dashboard's bar are drawn, not written: 3:1 is their floor."""
        palette = getattr(theme, palette_name)
        assert contrast(palette[fg], palette["SURFACE"]) >= MIN_CONTRAST_LARGE

    @pytest.mark.parametrize("palette_name", ["LIGHT"])
    def test_the_primary_focus_ring_shows_against_its_own_fill(self, palette_name):
        """The ring sits inside a coloured button, so the fill is what it
        has to stand out from -- not the page."""
        palette = getattr(theme, palette_name)
        ratio = contrast(palette["TEXT_ON_ACCENT"], palette["ACCENT"])
        assert ratio >= MIN_CONTRAST_LARGE

    @pytest.mark.parametrize("palette_name", ["LIGHT"])
    def test_borders_are_visible_against_their_surface(self, palette_name):
        palette = getattr(theme, palette_name)
        assert contrast(palette["BORDER"], palette["SURFACE"]) > 1.1


class TestPaletteCompleteness:
    def test_there_is_only_the_light_palette(self):
        """Dark mode was removed (the user's choice, 2026-10-20)."""
        for gone in ("DARK", "SIDEBAR_DARK", "CHECK_ICON_DARK", "THEME_KEY", "is_dark", "system_is_dark"):
            assert not hasattr(theme, gone), gone

    @pytest.mark.parametrize("palette_name", ["LIGHT"])
    def test_every_value_is_a_hex_colour(self, palette_name):
        for key, value in getattr(theme, palette_name).items():
            assert value.startswith("#") and len(value) == 7, f"{key} = {value!r}"

    def test_the_stylesheet_only_uses_keys_that_exist(self):
        """A missing key would raise KeyError."""
        theme.activate()
        assert theme.stylesheet()


class TestTypography:
    def test_body_text_clears_the_readable_floor(self):
        """Body was 13px and then shrunk again by the application font."""
        assert theme.SIZE_BODY >= 14

    def test_the_small_size_is_not_tiny(self):
        assert theme.SIZE_SMALL >= 12

    def test_the_scale_is_ordered(self):
        assert theme.SIZE_SMALL < theme.SIZE_BODY < theme.SIZE_HEADING < theme.SIZE_TITLE
        assert theme.SIZE_TITLE <= theme.SIZE_DISPLAY < theme.SIZE_HOME


class TestTheTitleFont:
    """Page titles are in a serif of their own (the user's choice, 2026-10-20)."""

    def test_the_font_and_its_licence_ship_with_the_app(self):
        for path in theme.FONT_FILES:
            assert path.is_file(), path
            assert (path.parent / "OFL.txt").is_file()

    def test_it_loads_and_covers_hebrew(self, qt_application):
        """The Posting tips title is Hebrew when the tips are."""
        from PySide6.QtGui import QFontDatabase

        assert theme.FONT_DISPLAY in theme.load_fonts()
        systems = QFontDatabase.writingSystems(theme.FONT_DISPLAY)
        assert QFontDatabase.WritingSystem.Hebrew in systems

    def test_only_the_titles_use_it(self):
        """Labels, buttons and numbers stay in the UI face: a display serif
        on a button is decoration in the way of the task. The tip numbers on
        Posting tips are the one figure in it (2026-10-20): they are read as
        headings of a sort, never worked with."""
        theme.activate()
        sheet = theme.stylesheet()
        theme.activate()
        rules = [block for block in sheet.split("}") if theme.FONT_DISPLAY in block]
        selectors = sorted(block.split("{")[0].strip().split("*/")[-1].strip() for block in rules)
        assert selectors == [
            "QLabel#HomeTitle", "QLabel#TipNumber", "QLabel#Title", "QPushButton#Brand"
        ]

    def test_it_is_never_made_bold(self):
        """It has one weight; Qt would fake a bold by smearing it."""
        theme.activate()
        for block in theme.stylesheet().split("}"):
            if theme.FONT_DISPLAY in block:
                assert "font-weight: 400" in block

    def test_the_app_registers_it_at_start(self):
        import inspect

        from fbposter.qtui import app as app_module

        assert "theme.load_fonts()" in inspect.getsource(app_module.run)


class TestTheTickSuitsTheFill:
    def test_the_tick_is_white_on_the_navy_box(self):
        from pathlib import Path

        path = theme.CHECK_ICON
        assert Path(path).is_file()
        assert "\\" not in path
        checked = theme.stylesheet().split("QCheckBox::indicator:checked")[1][:200]
        assert path in checked
        assert "#FFFFFF" in Path(path).read_text()

    def test_the_application_font_does_not_override_the_stylesheet(self):
        """Two sources of truth for size, and the smaller one was winning."""
        import inspect

        from fbposter.qtui import app as app_module

        source = inspect.getsource(app_module.run)
        assert "SIZE_BODY - 2" not in source


class TestFocusAndHitTargets:
    def test_the_stylesheet_defines_focus_styling(self):
        theme.activate()
        sheet = theme.stylesheet()
        assert "QPushButton:focus" in sheet
        assert "QLineEdit:focus" in sheet or ":focus" in sheet

    def test_focus_is_not_switched_off_anywhere(self):
        theme.activate()
        assert "outline: none" not in theme.stylesheet()

    def test_controls_declare_a_minimum_height(self):
        theme.activate()
        assert "min-height" in theme.stylesheet()

    def test_the_primary_action_is_the_biggest_target(self):
        assert theme.PRIMARY_HEIGHT > theme.CONTROL_HEIGHT
        assert theme.PRIMARY_HEIGHT >= 44


class TestNavigationReadsAsAFlow:
    def test_the_first_three_are_the_steps(self):
        assert FLOW_STEPS == ("compose", "groups", "publish")

    def test_the_dashboard_is_not_a_step(self):
        assert "dashboard" not in FLOW_STEPS

    def test_every_nav_item_has_a_plain_english_hint(self):
        for key, _label, _view in NAV_ITEMS:
            assert NAV_HINTS.get(key), f"{key} has no hint"

    def test_no_more_than_seven_nav_items(self):
        """Many more and a sidebar stops being scannable. It was five until
        the user chose an item of its own for group sets (2026-10-15), six
        until Posting tips (2026-10-18), and seven since Repeating posts
        (2026-10-21), so it is full: a new item means taking one away."""
        assert len(NAV_ITEMS) <= 7


class TestTheRenderedSidebar:
    def test_home_then_the_road_to_a_post_come_first(self, qt_app):
        """The three steps are one item now, in order inside it (2026-10-19)."""
        keys = list(qt_app.nav_buttons)
        assert keys[:2] == ["home", "create"]
        assert not set(FLOW_STEPS) & set(keys)

    def test_every_item_is_its_bare_name_with_an_icon(self, qt_app):
        for key, label, _view in NAV_ITEMS:
            button = qt_app.nav_buttons[key]
            assert button.text() == label
            assert not button.icon().isNull(), f"{key} has no icon"

    def test_the_icon_changes_colour_with_the_screen(self, qt_app):
        """A checked button draws its icon's On state, so the icon is lit
        with the label. Both states have to exist for that."""
        from PySide6.QtGui import QIcon

        icon = qt_app.nav_buttons["create"].icon()
        size = qt_app.nav_buttons["create"].iconSize()
        on = icon.pixmap(size, QIcon.Normal, QIcon.On).toImage()
        off = icon.pixmap(size, QIcon.Normal, QIcon.Off).toImage()
        assert on != off

    def test_every_nav_button_is_reachable_without_a_mouse(self, qt_app):
        for button in qt_app.nav_buttons.values():
            assert button.focusPolicy() != 0, "nav button cannot take focus"

    def test_every_nav_button_has_an_accessible_name(self, qt_app):
        for button in qt_app.nav_buttons.values():
            assert button.accessibleName()
            assert button.toolTip()
