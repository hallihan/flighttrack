import pygame
import pytest

from flighttrack.aircraft_types import LABELS, Kind, classify
from flighttrack.ui import icons, theme


@pytest.mark.parametrize(
    ("type_code", "kind"),
    [
        ("B77W", Kind.WIDEBODY),
        ("A320", Kind.NARROWBODY),
        ("b738", Kind.NARROWBODY),
        ("CRJ9", Kind.REGIONAL_JET),
        ("E75L", Kind.REGIONAL_JET),
        ("C56X", Kind.BUSINESS_JET),
        ("DH8D", Kind.TURBOPROP),
        ("BE58", Kind.PISTON_TWIN),
        ("C172", Kind.HIGH_WING),
        ("P28A", Kind.LOW_WING),
        ("SR22", Kind.COMPOSITE),
        ("R44", Kind.HELICOPTER),
        ("H60", Kind.HEAVY_HELICOPTER),
    ],
)
def test_common_types_are_classified(type_code, kind):
    assert classify(type_code) is kind


def test_unlisted_types_fall_back_to_the_emitter_category_then_unknown():
    assert classify("ZZZZ", "A7") is Kind.HELICOPTER
    assert classify(None, "A5") is Kind.WIDEBODY
    assert classify(None, "a1") is Kind.HIGH_WING
    assert classify("ZZZZ", "C1") is Kind.UNKNOWN
    assert classify(None, None) is Kind.UNKNOWN
    assert classify("C172", "A5") is Kind.HIGH_WING, "a known type beats the category"


def test_every_kind_has_a_shape_and_all_but_unknown_a_label():
    assert set(icons.SHAPES) == set(Kind)
    assert set(LABELS) == set(Kind) - {Kind.UNKNOWN}


def test_heading_buckets_wrap():
    assert icons.heading_bucket(0) == 0
    assert icons.heading_bucket(87.4) == 85
    assert icons.heading_bucket(87.6) == 90
    assert icons.heading_bucket(358.9) == 0


@pytest.mark.parametrize("kind", list(Kind))
def test_sprites_are_centered_colored_and_cached(kind):
    pygame.display.init()
    try:
        image = icons.sprite(kind, 0, theme.AIRCRAFT_MID)
        width, height = image.get_size()
        assert width == height and width % 2 == 1
        colored = pygame.mask.from_threshold(image, theme.AIRCRAFT_MID, (40, 40, 40, 255))
        assert colored.count() > 20
        # Symmetric about the vertical axis when pointing north.
        (bounds,) = [pygame.mask.from_surface(image).get_bounding_rects()[0]]
        assert abs(bounds.centerx - width // 2) <= 1
        assert icons.sprite(kind, 0, theme.AIRCRAFT_MID) is image
        assert icons.sprite(kind, 90, theme.AIRCRAFT_MID) is not image
        small = icons.sprite(kind, 0, theme.AIRCRAFT_MID, 0.45)
        assert small.get_width() < width
    finally:
        pygame.quit()
