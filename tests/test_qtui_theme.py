"""Guards for the visual decisions, so they cannot quietly regress.

These are not screenshot tests. Each one pins a rule that was broken at some
point and is invisible until someone hits it: focus that cannot be seen,
body text below the readable floor, a colour that only exists in one mode.

The contrast check is the one worth keeping honest — it computes real WCAG
ratios rather than trusting that the palette "looks fine", and it runs against
both light and dark.
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

    @pytest.mark.parametrize("palette_name", ["LIGHT", "DARK"])
    @pytest.mark.parametrize(
        "fg,bg",
        [
            ("TEXT", "WINDOW_BG"),
            ("TEXT", "SURFACE"),
            ("TEXT", "SIDEBAR_BG"),
            ("TEXT_MUTED", "WINDOW_BG"),
            ("TEXT_MUTED", "SURFACE"),
            ("TEXT_ON_ACCENT", "ACCENT"),
            ("SUCCESS", "SURFACE"),
            ("WARNING", "SURFACE"),
            ("DANGER", "SURFACE"),
            # The accent as text, which is a different value from the fill.
            ("ACCENT_TEXT", "SURFACE"),
            ("ACCENT_TEXT", "NAV_ACTIVE_BG"),
            ("ACCENT_TEXT", "SIDEBAR_BG"),
            # The connection pill, which was the quietest failure of the lot.
            ("NEUTRAL", "SIDEBAR_BG"),
            ("NEUTRAL", "SURFACE"),
        ],
    )
    def test_text_is_readable(self, palette_name, fg, bg):
        palette = getattr(theme, palette_name)
        ratio = contrast(palette[fg], palette[bg])
        assert ratio >= MIN_CONTRAST, (
            f"{palette_name}: {fg} on {bg} is {ratio:.2f}:1, under {MIN_CONTRAST}:1"
        )

    @pytest.mark.parametrize("palette_name", ["LIGHT", "DARK"])
    def test_white_is_readable_on_the_primary_button(self, palette_name):
        """Facebook's own #1877F2 puts white at 4.23:1, just under the floor."""
        palette = getattr(theme, palette_name)
        assert contrast(palette["ACCENT"], palette["TEXT_ON_ACCENT"]) >= MIN_CONTRAST
        assert contrast(palette["ACCENT_HOVER"], palette["TEXT_ON_ACCENT"]) >= MIN_CONTRAST

    @pytest.mark.parametrize("palette_name", ["LIGHT", "DARK"])
    def test_the_focus_ring_stands_out_from_its_surface(self, palette_name):
        """A focus ring nobody can see is the same as no focus ring."""
        palette = getattr(theme, palette_name)
        ratio = contrast(palette["ACCENT"], palette["SURFACE"])
        assert ratio >= MIN_CONTRAST_LARGE

    @pytest.mark.parametrize("palette_name", ["LIGHT", "DARK"])
    @pytest.mark.parametrize("bg", ["WINDOW_BG", "SURFACE", "SIDEBAR_BG"])
    def test_the_focus_ring_clears_the_indicator_bar(self, palette_name, bg):
        """WCAG 2.2 wants 3:1 for a focus indicator against what is next to it."""
        palette = getattr(theme, palette_name)
        assert contrast(palette["ACCENT"], palette[bg]) >= MIN_CONTRAST_LARGE

    @pytest.mark.parametrize("palette_name", ["LIGHT", "DARK"])
    def test_the_primary_focus_ring_shows_against_its_own_fill(self, palette_name):
        """The ring sits inside a coloured button, so the fill is what it
        has to stand out from -- not the page."""
        palette = getattr(theme, palette_name)
        ratio = contrast(palette["TEXT_ON_ACCENT"], palette["ACCENT"])
        assert ratio >= MIN_CONTRAST_LARGE

    @pytest.mark.parametrize("palette_name", ["LIGHT", "DARK"])
    def test_borders_are_visible_against_their_surface(self, palette_name):
        palette = getattr(theme, palette_name)
        assert contrast(palette["BORDER"], palette["SURFACE"]) > 1.1


class TestPaletteCompleteness:
    def test_both_modes_define_the_same_keys(self):
        """A colour defined for one mode only is invisible in the other."""
        assert set(theme.LIGHT) == set(theme.DARK)

    @pytest.mark.parametrize("palette_name", ["LIGHT", "DARK"])
    def test_every_value_is_a_hex_colour(self, palette_name):
        for key, value in getattr(theme, palette_name).items():
            assert value.startswith("#") and len(value) == 7, f"{key} = {value!r}"

    def test_the_stylesheet_only_uses_keys_that_exist(self):
        """Builds against both palettes; a missing key would raise KeyError."""
        for dark in (False, True):
            theme.activate(dark=dark)
            assert theme.stylesheet()
        theme.activate(dark=False)


class TestTypography:
    def test_body_text_clears_the_readable_floor(self):
        """Body was 13px and then shrunk again by the application font."""
        assert theme.SIZE_BODY >= 14

    def test_the_small_size_is_not_tiny(self):
        assert theme.SIZE_SMALL >= 12

    def test_the_scale_is_ordered(self):
        assert theme.SIZE_SMALL < theme.SIZE_BODY < theme.SIZE_HEADING < theme.SIZE_TITLE

    def test_the_application_font_does_not_override_the_stylesheet(self):
        """Two sources of truth for size, and the smaller one was winning."""
        import inspect

        from fbposter.qtui import app as app_module

        source = inspect.getsource(app_module.run)
        assert "SIZE_BODY - 2" not in source


class TestFocusAndHitTargets:
    def test_the_stylesheet_defines_focus_styling(self):
        theme.activate(dark=False)
        sheet = theme.stylesheet()
        assert "QPushButton:focus" in sheet
        assert "QLineEdit:focus" in sheet or ":focus" in sheet

    def test_focus_is_not_switched_off_anywhere(self):
        theme.activate(dark=False)
        assert "outline: none" not in theme.stylesheet()

    def test_controls_declare_a_minimum_height(self):
        theme.activate(dark=False)
        assert "min-height" in theme.stylesheet()

    def test_the_primary_action_is_the_biggest_target(self):
        assert theme.PRIMARY_HEIGHT > theme.CONTROL_HEIGHT
        assert theme.PRIMARY_HEIGHT >= 44


class TestNavigationReadsAsAFlow:
    def test_the_first_three_are_the_numbered_steps(self):
        assert FLOW_STEPS == ("compose", "groups", "publish")

    def test_queue_is_not_a_step(self):
        assert "queue" not in FLOW_STEPS

    def test_every_nav_item_has_a_plain_english_hint(self):
        for key, _label, _view in NAV_ITEMS:
            assert NAV_HINTS.get(key), f"{key} has no hint"

    def test_no_more_than_five_nav_items(self):
        """More than five and a sidebar stops being scannable."""
        assert len(NAV_ITEMS) <= 5


class TestTheRenderedSidebar:
    def test_the_steps_are_numbered_in_order(self, qt_app):
        assert qt_app.nav_buttons["compose"].text().startswith("1.")
        assert qt_app.nav_buttons["groups"].text().startswith("2.")
        assert qt_app.nav_buttons["publish"].text().startswith("3.")

    def test_queue_is_not_numbered(self, qt_app):
        assert qt_app.nav_buttons["queue"].text() == "Queue"

    def test_the_labels_still_name_their_screen(self, qt_app):
        for key, label, _view in NAV_ITEMS:
            assert label in qt_app.nav_buttons[key].text()

    def test_every_nav_button_is_reachable_without_a_mouse(self, qt_app):
        for button in qt_app.nav_buttons.values():
            assert button.focusPolicy() != 0, "nav button cannot take focus"

    def test_every_nav_button_has_an_accessible_name(self, qt_app):
        for button in qt_app.nav_buttons.values():
            assert button.accessibleName()
            assert button.toolTip()
