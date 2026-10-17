"""API tests."""

from fastapi.testclient import TestClient

from orbitwatch.main import CATALOG, app

client = TestClient(app)
SAT_ID = "99901"


def test_health_and_ready():
    assert client.get("/healthz").json() == {"status": "ok"}
    body = client.get("/readyz").json()
    assert body["status"] == "ready"
    assert body["satellites"] == len(CATALOG)


def test_list_satellites_includes_tle_age():
    sats = client.get("/satellites").json()
    assert {s["norad_id"] for s in sats} == set(CATALOG)
    assert all("tle_age_days" in s and "tle_epoch" in s for s in sats)


def test_telemetry_known_satellite():
    resp = client.get(f"/telemetry/{SAT_ID}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["norad_id"] == SAT_ID
    assert -53.5 <= data["latitude"] <= 53.5  # can't exceed the 53 deg inclination
    assert -180 <= data["longitude"] <= 180
    assert 500 < data["altitude_km"] < 600


def test_telemetry_unknown_satellite_returns_404():
    assert client.get("/telemetry/12345678").status_code == 404


def test_stations_and_passes():
    stations = {s["station_id"] for s in client.get("/stations").json()}
    assert "colorado-springs" in stations
    resp = client.get(f"/passes/{SAT_ID}", params={"station": "colorado-springs", "hours": 24})
    assert resp.status_code == 200
    body = resp.json()
    assert body["station"] == "colorado-springs"
    for p in body["passes"]:
        assert p["aos"] <= p["tca"] <= p["los"]
        assert p["max_elevation_deg"] >= body["min_elevation_deg"]


def test_passes_input_validation():
    assert client.get(f"/passes/{SAT_ID}", params={"station": "nowhere"}).status_code == 404
    assert client.get(f"/passes/{SAT_ID}", params={"hours": 1000}).status_code == 422
    assert client.get(f"/passes/{SAT_ID}", params={"min_elevation": 95}).status_code == 422


def test_metrics_exposed():
    client.get(f"/telemetry/{SAT_ID}")
    text = client.get("/metrics").text
    assert "orbitwatch_requests_total" in text
    assert f'orbitwatch_telemetry_reads_total{{norad_id="{SAT_ID}"}}' in text
    assert f'orbitwatch_tle_age_days{{norad_id="{SAT_ID}"}}' in text


def test_unknown_urls_share_one_metric_label():
    for path in ("/random1", "/random2", "/.env", "/wp-admin"):
        client.get(path)
    text = client.get("/metrics").text
    assert 'path="unmatched"' in text
    assert "/random1" not in text and "/wp-admin" not in text


def test_probes_are_not_counted():
    client.get("/healthz")
    client.get("/readyz")
    text = client.get("/metrics").text
    assert 'path="/healthz"' not in text
    assert 'path="/readyz"' not in text


def test_crash_is_counted_as_500():
    @app.get("/_boom")
    def boom():
        raise RuntimeError("simulated failure")

    crash_client = TestClient(app, raise_server_exceptions=False)
    assert crash_client.get("/_boom").status_code == 500
    text = client.get("/metrics").text
    assert 'orbitwatch_requests_total{method="GET",path="/_boom",status="500"}' in text
