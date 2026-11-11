import copy
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src import collector
from src.alert_engine import deadline, evaluate, findings, sla_report
from src.api import create_app
from src.collector import GPUNodeSimulator, collect_local
from src.models_and_db import Asset, Database

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)  # Friday


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.seed_demo(NOW.date())
    return db


def gpu_asset(database):
    return next(a for a in database.assets() if a["id"] == "gpu-demo-01")


def test_real_local_collection(tmp_path):
    data = collect_local(disk_path=tmp_path, interval=0.01)
    assert data["source"] == "local_psutil"
    for field in ("cpu_percent", "ram_percent", "disk_percent"):
        assert 0 <= data[field] <= 100
    assert data["ram_total_gib"] > 0
    assert data["disk_total_gib"] > 0
    assert datetime.fromisoformat(data["observed_at"]).tzinfo is not None


def test_thermal_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(collector.psutil, "sensors_temperatures", lambda: {}, raising=False)
    data = collect_local(disk_path=tmp_path, interval=0.01)
    assert data["max_temperature_c"] is None
    assert not data["thermal_available"]


def test_thermal_values_and_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(collector.psutil, "sensors_temperatures", lambda: {
        "cpu": [SimpleNamespace(label="package", current=91),
                SimpleNamespace(label="bad", current=float("nan"))]}, raising=False)
    assert collect_local(disk_path=tmp_path, interval=0.01)["max_temperature_c"] == 91

    def unavailable():
        raise OSError("Unsupported sensor")

    monkeypatch.setattr(collector.psutil, "sensors_temperatures", unavailable)
    assert collect_local(disk_path=tmp_path, interval=0.01)["temperatures"] == []


@pytest.mark.parametrize("scenario", ["normal", "thermal_spike", "load_spike"])
def test_eight_gpu_simulation(scenario):
    data = GPUNodeSimulator(42).collect(scenario=scenario)
    assert data["source"] == "simulated"
    assert data["gpu_count"] == len(data["gpus"]) == 8
    assert data["gpu_power_kw"] == pytest.approx(sum(g["power_kw"] for g in data["gpus"]))
    assert data["node_power_kw"] == pytest.approx(data["gpu_power_kw"] + 0.45)
    assert data["rack_power_kw"] == data["node_power_kw"]
    for gpu in data["gpus"]:
        assert 0 <= gpu["vram_used_gib"] <= gpu["vram_total_gib"]
        assert 0 <= gpu["utilization_percent"] <= 100
    assert data["gpus"] == GPUNodeSimulator(42).collect(scenario=scenario)["gpus"]


def test_reject_invalid_collection_settings():
    with pytest.raises(ValueError):
        GPUNodeSimulator().collect(scenario="remote")
    with pytest.raises(ValueError):
        collect_local(interval=0)


def test_seed_assets_and_validation(database):
    assert {a["vendor"] for a in database.assets()} == {"Dell", "HPE", "Cisco", "Supermicro"}
    database.seed_demo(NOW.date())
    assert len(database.assets()) == 4
    asset = gpu_asset(database)
    asset["eosl_date"] = "2000-01-01"
    with pytest.raises(ValidationError):
        Asset(**asset)


@pytest.mark.parametrize("opened,expected", [
    ("2026-11-13T12:00:00+00:00", "2026-11-16T17:00:00+00:00"),
    ("2026-11-13T18:00:00+00:00", "2026-11-17T17:00:00+00:00"),
    ("2026-11-14T12:00:00+00:00", "2026-11-17T17:00:00+00:00"),
    ("2026-11-16T07:00:00+00:00", "2026-11-17T17:00:00+00:00"),
])
def test_business_day_deadlines(opened, expected):
    assert deadline(datetime.fromisoformat(opened), "8x5xNBD").isoformat() == expected


def test_four_hour_deadline():
    assert deadline(NOW, "24x7x4") == NOW + timedelta(hours=4)
    with pytest.raises(ValueError):
        deadline(NOW.replace(tzinfo=None), "24x7x4")


def test_anomaly_ticket_dedup_and_acknowledgment(database):
    asset = gpu_asset(database)
    snapshot = GPUNodeSimulator(42).collect(scenario="thermal_spike")
    evaluate(database, asset, snapshot, now=NOW)
    initial = len(database.tickets())
    evaluate(database, asset, snapshot, now=NOW + timedelta(minutes=1))
    assert len(database.tickets()) == initial
    ticket = next(t for t in database.tickets() if t["rule"] == "thermal_high")
    assert ticket["severity"] == "critical"
    assert ticket["occurrences"] == 2
    assert ticket["due_at"] == (NOW + timedelta(hours=4)).isoformat()
    database.transition(ticket["id"], "acknowledge", NOW + timedelta(hours=1))
    report = sla_report(database.tickets(), now=NOW + timedelta(days=1))
    assert next(r for r in report["tickets"] if r["ticket_id"] == ticket["id"])["status"] == "met"
    database.transition(ticket["id"], "resolve", NOW + timedelta(hours=2))
    assert ticket["id"] not in {t["id"] for t in database.tickets()}
    evaluate(database, asset, snapshot, now=NOW + timedelta(hours=3))
    assert any(t["rule"] == "thermal_high" for t in database.tickets())


def test_sla_breach_and_late_acknowledgment(database):
    evaluate(database, gpu_asset(database), now=NOW)
    report = sla_report(database.tickets(), NOW + timedelta(hours=5))
    assert report["breached"] == 1
    assert report["compliance_percent"] == 0
    ticket = database.tickets()[0]
    database.transition(ticket["id"], "acknowledge", NOW + timedelta(hours=5))
    assert sla_report(database.tickets(), NOW + timedelta(hours=6))["breached"] == 1
    assert sla_report([])["compliance_percent"] is None


def test_load_pressure_and_recent_spikes(database):
    asset = gpu_asset(database)
    pressure = GPUNodeSimulator(3).collect(scenario="load_spike")
    assert {"gpu_pressure", "power_high"} <= {r["rule"] for r in findings(asset, pressure, now=NOW)}
    prior = {"source": "local_psutil", "observed_at": NOW.isoformat(),
             "cpu_percent": 20, "max_temperature_c": 40}
    current = {**prior, "observed_at": (NOW + timedelta(seconds=30)).isoformat(),
               "cpu_percent": 92, "ram_percent": 95, "disk_percent": 93, "max_temperature_c": 60}
    rules = {r["rule"] for r in findings(asset, current, prior, NOW)}
    assert {"cpu_percent", "ram_percent", "disk_percent", "spike_cpu_percent", "spike_max_temperature_c"} <= rules
    prior["observed_at"] = (NOW - timedelta(hours=1)).isoformat()
    assert not any(r["rule"].startswith("spike_") for r in findings(asset, current, prior, NOW))


def test_lifecycle_rules(database):
    asset = gpu_asset(database)
    for field in ("warranty_end", "eol_date", "eosl_date"):
        asset[field] = (NOW.date() - timedelta(days=1)).isoformat()
    result = findings(asset, now=NOW)
    assert {r["rule"] for r in result} == {"warranty_end", "eol_date", "eosl_date"}
    assert next(r for r in result if r["rule"] == "eosl_date")["severity"] == "critical"


def test_api_end_to_end(tmp_path):
    app = create_app(tmp_path / "api.sqlite3", background=False)
    with TestClient(app) as client:
        assert client.get("/health").json()["mode"] == "local-only"
        assert len(client.get("/assets").json()) == 4
        assert len(client.get("/nodes/health").json()) == 4
        assert client.get("/nodes/hpe-demo-01/health").json()["status"] == "unmonitored"
        assert client.get("/nodes/missing/health").status_code == 404
        response = client.post("/collect?scenario=thermal_spike")
        assert response.status_code == 200
        assert len(response.json()["snapshots"]) == 2
        assert client.get("/nodes/gpu-demo-01/health").json()["status"] == "critical"
        ticket = next(t for t in client.get("/alerts").json() if t["rule"] == "thermal_high")
        assert client.post(f"/tickets/{ticket['id']}/acknowledge").status_code == 200
        assert client.post(f"/tickets/{ticket['id']}/resolve").json()["status"] == "resolved"
        assert client.post(f"/tickets/{ticket['id']}/resolve").status_code == 409
        assert client.post("/tickets/99999/acknowledge").status_code == 404
        assert client.get("/sla/compliance").status_code == 200
        assert client.post("/collect?scenario=invalid").status_code == 422
        asset = client.get("/assets").json()[0]
        asset["sla_tier"] = "24x7x4"
        assert client.put(f"/assets/{asset['id']}", json=asset).status_code == 200
        assert client.put("/assets/wrong", json=asset).status_code == 400
        asset["sla_tier"] = "invalid"
        assert client.put(f"/assets/{asset['id']}", json=asset).status_code == 422


def test_api_stale_data_and_local_host_uniqueness(tmp_path):
    app = create_app(tmp_path / "api.sqlite3", background=False)
    with TestClient(app) as client:
        snapshot = app.state.database.latest("local-host")
        snapshot["observed_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        app.state.database.save_telemetry(snapshot)
        assert client.get("/nodes/local-host/health").json()["status"] == "stale"
        asset = copy.deepcopy(client.get("/nodes/local-host/health").json()["asset"])
        asset["id"] = "duplicate-host"
        assert client.put("/assets/duplicate-host", json=asset).status_code == 409


def test_database_survives_reopen(database):
    snapshot = GPUNodeSimulator(5).collect()
    database.save_telemetry(snapshot)
    reopened = Database(database.path)
    assert reopened.latest("gpu-demo-01") == snapshot


def test_periodic_collection_lifecycle(tmp_path):
    app = create_app(tmp_path / "scheduled.sqlite3", interval_seconds=1)
    with TestClient(app):
        initial = app.state.database.latest("gpu-demo-01")["observed_at"]
        timeout = time.monotonic() + 5
        while time.monotonic() < timeout:
            if app.state.database.latest("gpu-demo-01")["observed_at"] != initial:
                break
            time.sleep(0.1)
        assert app.state.database.latest("gpu-demo-01")["observed_at"] != initial
