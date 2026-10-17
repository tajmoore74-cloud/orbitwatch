"""Ground-station pass prediction.

A "pass" is a window when a satellite is above a ground station's horizon
(above a minimum elevation mask) and the antenna can talk to it:

  AOS  - Acquisition of Signal: satellite rises above the mask
  TCA  - Time of Closest Approach: highest elevation of the pass
  LOS  - Loss of Signal: satellite drops below the mask

Approach: step through time coarsely to find when elevation crosses the
mask, then refine each crossing with bisection to about 1 second.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .catalog import Satellite
from .geo import look_angles

COARSE_STEP_S = 30
REFINE_TOLERANCE_S = 1.0


def _elevation(sat: Satellite, station: tuple[float, float, float], t: datetime) -> float:
    return look_angles(station, sat.ecef_position(t))[1]


def _refine_crossing(sat, station, mask, t_below: datetime, t_above: datetime) -> datetime:
    """Bisect between a time below the mask and a time above it."""
    lo, hi = t_below, t_above
    while abs((hi - lo).total_seconds()) > REFINE_TOLERANCE_S:
        mid = lo + (hi - lo) / 2
        if _elevation(sat, station, mid) >= mask:
            hi = mid
        else:
            lo = mid
    return hi


def _peak(sat, station, start: datetime, end: datetime) -> tuple[datetime, float]:
    """Find the highest elevation between AOS and LOS (golden-section search)."""
    ratio = (5**0.5 - 1) / 2
    a, b = start, end
    c = b - (b - a) * ratio
    d = a + (b - a) * ratio
    while (b - a).total_seconds() > REFINE_TOLERANCE_S:
        if _elevation(sat, station, c) > _elevation(sat, station, d):
            b = d
        else:
            a = c
        c = b - (b - a) * ratio
        d = a + (b - a) * ratio
    t = a + (b - a) / 2
    return t, _elevation(sat, station, t)


def predict_passes(
    sat: Satellite,
    station: tuple[float, float, float],
    start: datetime,
    hours: float = 24.0,
    min_elevation_deg: float = 10.0,
) -> list[dict]:
    """Return every pass of `sat` over `station` in the next `hours`."""
    end = start + timedelta(hours=hours)
    step = timedelta(seconds=COARSE_STEP_S)
    passes: list[dict] = []

    t_prev = start
    el_prev = _elevation(sat, station, t_prev)
    aos = start if el_prev >= min_elevation_deg else None  # already in a pass
    in_progress = aos is not None

    t = start + step
    while t <= end:
        el = _elevation(sat, station, t)
        if el_prev < min_elevation_deg <= el:  # rising through the mask
            aos = _refine_crossing(sat, station, min_elevation_deg, t_prev, t)
        elif el_prev >= min_elevation_deg > el and aos is not None:  # setting
            los = _refine_crossing(sat, station, min_elevation_deg, t, t_prev)
            passes.append(_describe(sat, station, aos, los, started_before_window=in_progress))
            aos, in_progress = None, False
        t_prev, el_prev = t, el
        t += step

    if aos is not None:  # pass still in progress at the end of the window
        passes.append(
            _describe(sat, station, aos, end, truncated=True, started_before_window=in_progress)
        )
    return passes


def _describe(
    sat, station, aos: datetime, los: datetime,
    truncated: bool = False, started_before_window: bool = False,
) -> dict:
    """truncated: still above the mask when the window ends (LOS is the window end).
    started_before_window: already above the mask when the window began (AOS is the window start).
    """
    tca, max_el = _peak(sat, station, aos, los)
    az_aos = look_angles(station, sat.ecef_position(aos))[0]
    az_los = look_angles(station, sat.ecef_position(los))[0]
    return {
        "aos": aos.isoformat(timespec="seconds"),
        "tca": tca.isoformat(timespec="seconds"),
        "los": los.isoformat(timespec="seconds"),
        "duration_s": round((los - aos).total_seconds()),
        "max_elevation_deg": round(max_el, 1),
        "aos_azimuth_deg": round(az_aos, 1),
        "los_azimuth_deg": round(az_los, 1),
        "truncated": truncated,
        "started_before_window": started_before_window,
    }
