"""Coordinate conversions used for satellite tracking.

SGP4 gives a satellite's position in the TEME frame (an Earth-centered frame
that does NOT rotate with the Earth). To get latitude/longitude, or to point
an antenna from a ground station, we rotate into ECEF (Earth-centered,
Earth-fixed) and then convert to geodetic coordinates on the WGS-84 ellipsoid.

Accuracy is a few hundred meters (polar motion and the equation of the
equinoxes are ignored), which is far smaller than SGP4's own error.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

# WGS-84 ellipsoid
WGS84_A_KM = 6378.137
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)


def julian_date(dt: datetime) -> float:
    """Julian date (UTC) for a timezone-aware datetime."""
    dt = dt.astimezone(timezone.utc)
    unix = dt.timestamp()
    return unix / 86400.0 + 2440587.5


def gmst_rad(dt: datetime) -> float:
    """Greenwich Mean Sidereal Time (IAU 1982), in radians.

    This is how far the Earth has rotated relative to the stars, and is the
    angle between the TEME and ECEF frames.
    """
    jd = julian_date(dt)
    t = (jd - 2451545.0) / 36525.0
    seconds = (
        67310.54841
        + (876600.0 * 3600 + 8640184.812866) * t
        + 0.093104 * t**2
        - 6.2e-6 * t**3
    )
    return math.radians((seconds % 86400.0) / 240.0) % (2 * math.pi)


def teme_to_ecef(r_teme: tuple[float, float, float], dt: datetime) -> tuple[float, float, float]:
    """Rotate a TEME position (km) into ECEF (km)."""
    theta = gmst_rad(dt)
    c, s = math.cos(theta), math.sin(theta)
    x, y, z = r_teme
    return (c * x + s * y, -s * x + c * y, z)


def ecef_to_geodetic(r_ecef: tuple[float, float, float]) -> tuple[float, float, float]:
    """ECEF (km) -> (latitude deg, longitude deg, altitude km) on WGS-84."""
    x, y, z = r_ecef
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - WGS84_E2))  # first guess
    alt = 0.0
    for _ in range(6):  # converges in a few iterations
        sin_lat = math.sin(lat)
        n = WGS84_A_KM / math.sqrt(1 - WGS84_E2 * sin_lat**2)
        alt = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1 - WGS84_E2 * n / (n + alt)))
    return math.degrees(lat), math.degrees(lon), alt


def geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_km: float) -> tuple[float, float, float]:
    """(latitude deg, longitude deg, altitude km) -> ECEF (km)."""
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    n = WGS84_A_KM / math.sqrt(1 - WGS84_E2 * math.sin(lat) ** 2)
    return (
        (n + alt_km) * math.cos(lat) * math.cos(lon),
        (n + alt_km) * math.cos(lat) * math.sin(lon),
        (n * (1 - WGS84_E2) + alt_km) * math.sin(lat),
    )


def look_angles(
    station: tuple[float, float, float], sat_ecef: tuple[float, float, float]
) -> tuple[float, float, float]:
    """Where to point an antenna: (azimuth deg, elevation deg, range km).

    Azimuth is measured clockwise from true north. Elevation is degrees above
    the horizon (negative = below the horizon, not visible).
    """
    lat_deg, lon_deg, alt_km = station
    sx, sy, sz = geodetic_to_ecef(lat_deg, lon_deg, alt_km)
    dx, dy, dz = sat_ecef[0] - sx, sat_ecef[1] - sy, sat_ecef[2] - sz

    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    # Rotate the station->satellite vector into local East/North/Up
    east = -math.sin(lon) * dx + math.cos(lon) * dy
    north = (
        -math.sin(lat) * math.cos(lon) * dx
        - math.sin(lat) * math.sin(lon) * dy
        + math.cos(lat) * dz
    )
    up = (
        math.cos(lat) * math.cos(lon) * dx
        + math.cos(lat) * math.sin(lon) * dy
        + math.sin(lat) * dz
    )
    rng = math.sqrt(dx * dx + dy * dy + dz * dz)
    az = math.degrees(math.atan2(east, north)) % 360.0
    el = math.degrees(math.asin(up / rng))
    return az, el, rng
