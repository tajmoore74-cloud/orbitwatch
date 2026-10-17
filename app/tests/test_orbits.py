"""Accuracy tests: our math is checked against Skyfield, a widely used,
independently written astronomy library. If our coordinate conversions or
pass prediction drift, these tests fail.
"""

from datetime import datetime, timedelta, timezone

import pytest
from skyfield.api import EarthSatellite, load, wgs84

from orbitwatch.catalog import load_catalog, parse_tle_text
from orbitwatch.geo import ecef_to_geodetic, geodetic_to_ecef
from orbitwatch.main import DEFAULT_TLE
from orbitwatch.passes import predict_passes
from orbitwatch.stations import DEFAULT_STATIONS

TS = load.timescale()
CATALOG = load_catalog(str(DEFAULT_TLE))
START = datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)
COS = DEFAULT_STATIONS[0]  # Colorado Springs


def _skyfield_sat(norad_id: str) -> EarthSatellite:
    text = DEFAULT_TLE.read_text().splitlines()
    for i, line in enumerate(text):
        if line.startswith("1 ") and line[2:7].strip() == norad_id:
            return EarthSatellite(text[i], text[i + 1], text[i - 1], TS)
    raise KeyError(norad_id)


def test_geodetic_round_trip():
    for lat, lon, alt in [(38.83, -104.82, 1.84), (-33.9, 151.2, 0.0), (89.9, 10.0, 500.0)]:
        back = ecef_to_geodetic(geodetic_to_ecef(lat, lon, alt))
        assert back == pytest.approx((lat, lon, alt), abs=1e-6)


@pytest.mark.parametrize("norad_id", sorted(CATALOG))
def test_subpoint_matches_skyfield(norad_id):
    ours = CATALOG[norad_id]
    theirs = _skyfield_sat(norad_id)
    for minutes in range(0, 24 * 60, 37):
        t = START + timedelta(minutes=minutes)
        mine = ours.subpoint(t)
        sub = wgs84.subpoint_of(theirs.at(TS.from_datetime(t)))
        height = wgs84.height_of(theirs.at(TS.from_datetime(t))).km
        assert mine["latitude"] == pytest.approx(sub.latitude.degrees, abs=0.02)
        lon_diff = (mine["longitude"] - sub.longitude.degrees + 180) % 360 - 180
        assert abs(lon_diff) < 0.02
        assert mine["altitude_km"] == pytest.approx(height, abs=0.5)


@pytest.mark.parametrize("norad_id", sorted(CATALOG))
def test_passes_match_skyfield(norad_id):
    """Every predicted AOS/LOS must be a real mask crossing according to Skyfield,
    the peak elevation must match, and no pass may be missed."""
    mask = 10.0
    mine = predict_passes(CATALOG[norad_id], COS.location, START, hours=24, min_elevation_deg=mask)
    sky = _skyfield_sat(norad_id)
    topos = wgs84.latlon(COS.latitude, COS.longitude, elevation_m=COS.altitude_km * 1000)

    def sky_elevation(t: datetime) -> float:
        return (sky - topos).at(TS.from_datetime(t)).altaz()[0].degrees

    two_s = timedelta(seconds=2)
    for p in mine:
        aos, tca, los = (datetime.fromisoformat(p[k]) for k in ("aos", "tca", "los"))
        if not p["started_before_window"]:  # rises through the mask within ~2 s of our AOS
            assert sky_elevation(aos - two_s) < mask < sky_elevation(aos + two_s)
        if not p["truncated"]:  # sets through the mask within ~2 seconds of our LOS
            assert sky_elevation(los - two_s) > mask > sky_elevation(los + two_s)
        assert p["max_elevation_deg"] == pytest.approx(sky_elevation(tca), abs=0.1)

    # Same number of passes as Skyfield's independent search (count only; its
    # event times were a few seconds less precise than its own elevation values)
    times, events = sky.find_events(
        topos, TS.from_datetime(START), TS.from_datetime(START + timedelta(hours=24)),
        altitude_degrees=mask,
    )
    assert len([p for p in mine if not p["truncated"]]) == sum(1 for e in events if e == 2)


def test_pass_already_in_progress_is_flagged():
    sat = CATALOG["99904"]
    first = predict_passes(sat, COS.location, START, hours=24)[0]
    mid_pass = datetime.fromisoformat(first["tca"])  # start the window mid-pass
    clipped = predict_passes(sat, COS.location, mid_pass, hours=1)[0]
    assert clipped["started_before_window"] is True
    assert clipped["aos"] == mid_pass.isoformat(timespec="seconds")
    assert abs((datetime.fromisoformat(clipped["los"]) -
                datetime.fromisoformat(first["los"])).total_seconds()) <= 2
    assert first["started_before_window"] is False


def test_physics_is_reasonable():
    sat = CATALOG["99901"]  # ~550 km orbit, like a Starlink shell
    assert 94 < sat.period_min < 97
    assert 7.5 < sat.subpoint(START)["speed_km_s"] < 7.7


def test_tle_parsing_rejects_garbage():
    with pytest.raises(ValueError):
        parse_tle_text("not a tle\nat all\n")
    with pytest.raises(ValueError):
        parse_tle_text("")
