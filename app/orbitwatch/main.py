"""OrbitWatch: satellite telemetry and ground-station pass prediction API.

Endpoints:
  GET /healthz                      -> liveness check (is the process alive?)
  GET /readyz                       -> readiness check (is it ready for traffic?)
  GET /satellites                   -> the satellite catalog, with TLE epoch and age
  GET /telemetry/{norad_id}         -> where a satellite is right now
  GET /stations                     -> configured ground stations
  GET /passes/{norad_id}?station=   -> upcoming passes over a ground station
  GET /metrics                      -> Prometheus metrics for monitoring
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from importlib import resources

from fastapi import FastAPI, HTTPException, Query, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from .catalog import PropagationError, Satellite, load_catalog
from .passes import predict_passes
from .stations import load_stations

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("orbitwatch")

APP_VERSION = os.getenv("APP_VERSION", "dev")
DEFAULT_TLE = resources.files("orbitwatch").joinpath("data/demo.tle")
TLE_FILE = os.getenv("ORBITWATCH_TLE_FILE") or str(DEFAULT_TLE)
STATIONS_FILE = os.getenv("ORBITWATCH_STATIONS_FILE")
MAX_PASS_HOURS = 72

CATALOG: dict[str, Satellite] = load_catalog(TLE_FILE)
STATIONS = load_stations(STATIONS_FILE)
log.info("loaded %d satellites from %s and %d ground stations", len(CATALOG), TLE_FILE, len(STATIONS))

app = FastAPI(title="OrbitWatch", version=APP_VERSION)

# ---- Prometheus metrics -------------------------------------------------
REQUESTS = Counter(
    "orbitwatch_requests_total",
    "HTTP requests handled",
    ["method", "path", "status"],
)
LATENCY = Histogram(
    "orbitwatch_request_seconds",
    "HTTP request latency in seconds",
    ["path"],
)
TELEMETRY_READS = Counter(
    "orbitwatch_telemetry_reads_total",
    "Telemetry lookups per satellite",
    ["norad_id"],
)
TLE_AGE = Gauge(
    "orbitwatch_tle_age_days",
    "Age of each satellite's orbital elements; stale TLEs mean inaccurate positions",
    ["norad_id"],
)
for _sat in CATALOG.values():
    # Evaluated every time Prometheus scrapes, so the age is always current
    TLE_AGE.labels(norad_id=_sat.norad_id).set_function(_sat.tle_age_days)

PROBE_PATHS = {"/healthz", "/readyz", "/metrics"}


def _route_label(request: Request) -> str:
    """Label requests by route template (e.g. /telemetry/{norad_id}), never the raw URL.

    Unknown URLs all share one label, so a scanner hitting thousands of random
    paths can't create thousands of metric series (high cardinality).
    """
    route = request.scope.get("route")
    return getattr(route, "path", "unmatched")


@app.middleware("http")
async def record_metrics(request: Request, call_next):
    start = time.perf_counter()
    status = "500"  # if the handler crashes, still count it as a server error
    try:
        response = await call_next(request)
        status = str(response.status_code)
        return response
    finally:
        path = _route_label(request)
        if path not in PROBE_PATHS:  # keep Kubernetes probe noise out of the stats
            LATENCY.labels(path=path).observe(time.perf_counter() - start)
            REQUESTS.labels(method=request.method, path=path, status=status).inc()


def _get_sat(norad_id: str) -> Satellite:
    sat = CATALOG.get(norad_id.strip())
    if sat is None:
        raise HTTPException(status_code=404, detail=f"Unknown satellite: {norad_id}")
    return sat


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict:
    return {"status": "ready", "satellites": len(CATALOG), "version": APP_VERSION}


@app.get("/satellites")
def list_satellites() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {
            "norad_id": s.norad_id,
            "name": s.name,
            "inclination_deg": round(s.inclination_deg, 2),
            "period_min": round(s.period_min, 2),
            "tle_epoch": s.epoch.isoformat(timespec="seconds"),
            "tle_age_days": round(s.tle_age_days(now), 2),
        }
        for s in CATALOG.values()
    ]


@app.get("/telemetry/{norad_id}")
def telemetry(norad_id: str) -> dict:
    sat = _get_sat(norad_id)
    now = datetime.now(timezone.utc)
    try:
        position = sat.subpoint(now)
    except PropagationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    TELEMETRY_READS.labels(norad_id=sat.norad_id).inc()
    return {
        "norad_id": sat.norad_id,
        "name": sat.name,
        "timestamp": now.isoformat(timespec="seconds"),
        "tle_age_days": round(sat.tle_age_days(now), 2),
        **position,
    }


@app.get("/stations")
def list_stations() -> list[dict]:
    return [
        {"station_id": s.station_id, "name": s.name, "latitude": s.latitude,
         "longitude": s.longitude, "altitude_km": s.altitude_km}
        for s in STATIONS.values()
    ]


@app.get("/passes/{norad_id}")
def passes(
    norad_id: str,
    station: str = Query("colorado-springs", description="Ground station ID from /stations"),
    hours: float = Query(24.0, gt=0, le=MAX_PASS_HOURS, description="How far ahead to look"),
    min_elevation: float = Query(10.0, ge=0, lt=90, description="Antenna elevation mask, degrees"),
) -> dict:
    sat = _get_sat(norad_id)
    gs = STATIONS.get(station)
    if gs is None:
        raise HTTPException(status_code=404, detail=f"Unknown ground station: {station}")
    start = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        found = predict_passes(sat, gs.location, start, hours=hours, min_elevation_deg=min_elevation)
    except PropagationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "norad_id": sat.norad_id,
        "name": sat.name,
        "station": gs.station_id,
        "window_start": start.isoformat(timespec="seconds"),
        "window_hours": hours,
        "min_elevation_deg": min_elevation,
        "passes": found,
    }


@app.get("/metrics")
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
