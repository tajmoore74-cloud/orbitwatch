"""Satellite catalog loaded from Two-Line Element (TLE) sets.

A TLE is the standard format (published by U.S. Space Force / CelesTrak) that
describes a satellite's orbit at a moment in time (its "epoch"). The SGP4
model propagates that orbit forward to any time we ask for.

TLEs go stale: accuracy degrades by roughly a kilometer or more per day, so
operators refresh them regularly. We expose each TLE's age as a metric so
monitoring can alert on stale data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sgp4.api import Satrec, jday

from .geo import ecef_to_geodetic, teme_to_ecef


class PropagationError(RuntimeError):
    """SGP4 couldn't compute a position (for example, the orbit has decayed)."""


@dataclass(frozen=True)
class Satellite:
    norad_id: str
    name: str
    satrec: Satrec

    @property
    def epoch(self) -> datetime:
        jd = self.satrec.jdsatepoch + self.satrec.jdsatepochF
        return datetime.fromtimestamp((jd - 2440587.5) * 86400.0, tz=timezone.utc)

    @property
    def inclination_deg(self) -> float:
        return math.degrees(self.satrec.inclo)

    @property
    def period_min(self) -> float:
        # no_kozai is mean motion in radians per minute
        return 2 * math.pi / self.satrec.no_kozai

    def tle_age_days(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self.epoch).total_seconds() / 86400.0

    def teme_state(self, when: datetime) -> tuple[tuple[float, ...], tuple[float, ...]]:
        """Position (km) and velocity (km/s) in TEME at a given time."""
        when = when.astimezone(timezone.utc)
        jd, fr = jday(
            when.year, when.month, when.day, when.hour, when.minute,
            when.second + when.microsecond / 1e6,
        )
        err, r, v = self.satrec.sgp4(jd, fr)
        if err != 0:
            raise PropagationError(f"SGP4 error code {err} for {self.norad_id}")
        return r, v

    def ecef_position(self, when: datetime) -> tuple[float, float, float]:
        r, _ = self.teme_state(when)
        return teme_to_ecef(r, when)

    def subpoint(self, when: datetime) -> dict:
        """The point on Earth directly below the satellite, plus altitude and speed."""
        r, v = self.teme_state(when)
        lat, lon, alt = ecef_to_geodetic(teme_to_ecef(r, when))
        return {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "altitude_km": round(alt, 2),
            "speed_km_s": round(math.sqrt(sum(c * c for c in v)), 3),
            "period_min": round(self.period_min, 2),
        }


def parse_tle_text(text: str) -> list[Satellite]:
    """Parse 3-line TLE text (name line, line 1, line 2), as served by CelesTrak."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    sats: list[Satellite] = []
    if len(lines) % 3 != 0:
        raise ValueError("TLE data must be groups of 3 lines: name, line 1, line 2")
    i = 0
    while i < len(lines):
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            raise ValueError(f"Malformed TLE near line {i + 1}: {name!r}")
        satrec = Satrec.twoline2rv(l1, l2)
        norad_id = l1[2:7].strip()
        sats.append(Satellite(norad_id=norad_id, name=name.strip(), satrec=satrec))
        i += 3
    if not sats:
        raise ValueError("No satellites found in TLE data")
    return sats


def load_catalog(path: str | Path) -> dict[str, Satellite]:
    sats = parse_tle_text(Path(path).read_text())
    return {s.norad_id: s for s in sats}
