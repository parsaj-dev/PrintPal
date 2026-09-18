"""Tests for best-effort carrier / tracking recognition."""
from __future__ import annotations

from printpal.carrier import (
    parse_tracking, UPS, USPS, FEDEX, UNKNOWN,
)


def test_ups():
    s = parse_tracking(["1Z999AA10123456784"])
    assert s.carrier == UPS and s.tracking == "1Z999AA10123456784"


def test_usps_impb():
    s = parse_tracking(["9400111899223818110794"])
    assert s.carrier == USPS and s.tracking == "9400111899223818110794"


def test_usps_s10():
    s = parse_tracking(["EA123456789US"])
    assert s.carrier == USPS and s.tracking == "EA123456789US"


def test_fedex_12_digit():
    s = parse_tracking(["794658123456"])
    assert s.carrier == FEDEX and s.tracking == "794658123456"


def test_ups_wins_over_noise():
    s = parse_tracking(["some prefix 1Z12345E0305271640 trailing"])
    assert s.carrier == UPS and s.tracking == "1Z12345E0305271640"


def test_unknown_keeps_raw():
    s = parse_tracking(["ABC-XYZ-ORDER"])
    assert s.carrier == UNKNOWN and s.tracking == "ABC-XYZ-ORDER"


def test_empty():
    s = parse_tracking([])
    assert s.carrier == UNKNOWN and s.tracking is None
