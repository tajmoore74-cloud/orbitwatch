"""Ground stations that can be used for pass prediction.

Stations load from a JSON file (ORBITWATCH_STATIONS_FILE) so operators can
change them without rebuilding the image. Coordinates below are approximate
city locations, used for demonstration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GroundStation:
    station_id: str
    name: str
    latitude: float
    longitude: float
    altitude_km: float

    @property
    def location(self) -> tuple[float, float, float]:
        return (self.latitude, self.longitude, self.altitude_km)


DEFAULT_STATIONS = (
    GroundStation("colorado-springs", "Colorado Springs, CO", 38.8339, -104.8214, 1.839),
    GroundStation("huntsville", "Huntsville, AL", 34.7304, -86.5861, 0.183),
    GroundStation("los-angeles", "Los Angeles, CA", 34.0522, -118.2437, 0.089),
)


def load_stations(path: str | Path | None) -> dict[str, GroundStation]:
    if path and Path(path).exists():
        raw = json.loads(Path(path).read_text())
        stations = [GroundStation(**item) for item in raw]
    else:
        stations = list(DEFAULT_STATIONS)
    for s in stations:
        if not (-90 <= s.latitude <= 90 and -180 <= s.longitude <= 180):
            raise ValueError(f"Invalid coordinates for station {s.station_id}")
    return {s.station_id: s for s in stations}
