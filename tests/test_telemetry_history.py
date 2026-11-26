from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from src.api import create_app
from src.models_and_db import Database

GPU_HISTORY = "/nodes/gpu-demo-01/telemetry"
SAME_TIME = datetime(2026, 11, 13, 12, tzinfo=timezone.utc).isoformat()


@pytest.fixture
def monitor(tmp_path):
    app = create_app(tmp_path / "history.sqlite3", background=False)
    with TestClient(app) as client:
        yield client, app.state.database


def save(database, asset_id="gpu-demo-01"):
    database.save_telemetry({
        "asset_id": asset_id, "source": "simulated", "observed_at": SAME_TIME,
    })


def test_history_pages_are_stable_when_a_sample_is_inserted(monitor):
    client, database = monitor
    for _ in range(3):
        save(database)
        save(database, "local-host")

    first = client.get(GPU_HISTORY, params={"limit": 2}).json()
    assert first["asset_id"] == "gpu-demo-01"
    assert len(first["samples"]) == 2
    assert first["next_before_id"] == first["samples"][-1]["id"]
    assert [item["observed_at"] for item in first["samples"]] == [SAME_TIME, SAME_TIME]

    save(database)
    second = client.get(GPU_HISTORY, params={"limit": 2, "before_id": first["next_before_id"]}).json()
    assert len(second["samples"]) == 2
    assert second["next_before_id"] is None
    combined = first["samples"] + second["samples"]
    ids = [item["id"] for item in combined]
    assert ids == sorted(set(ids), reverse=True)
    assert all(sample["asset_id"] == "gpu-demo-01" for sample in combined)
    assert client.get(GPU_HISTORY).json()["samples"][0]["id"] > ids[0]


def test_empty_and_unknown_history(monitor):
    client, _ = monitor
    assert client.get("/nodes/cisco-demo-01/telemetry").json() == {
        "asset_id": "cisco-demo-01", "samples": [], "next_before_id": None,
    }
    assert client.get(GPU_HISTORY, params={"before_id": 1}).json()["samples"] == []
    assert client.get("/nodes/unknown/telemetry").status_code == 404
    assert client.get("/nodes/bad.asset/telemetry").status_code == 422


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"limit": "many"},
    {"before_id": 0}, {"before_id": 2**63}, {"before_id": "older"},
])
def test_invalid_pagination(monitor, params):
    client, _ = monitor
    assert client.get(GPU_HISTORY, params=params).status_code == 422


def test_existing_database_uses_index_for_history(monitor):
    _, database = monitor
    reopened = Database(database.path)
    with reopened.connect() as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT id, payload FROM telemetry "
            "WHERE asset_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
            ("gpu-demo-01", 100, 13),
        ).fetchall()
    assert any("idx_telemetry_asset" in row["detail"] for row in plan)
    assert reopened.telemetry_page("gpu-demo-01", limit=12)[0][0]["source"] == "simulated"


def test_simultaneous_resolution_records_one_transition(monitor):
    client, database = monitor
    ticket_id = client.get("/alerts").json()[0]["id"]
    ready = Barrier(2)

    def resolve():
        ready.wait(timeout=5)
        try:
            return database.transition(ticket_id, "resolve")["status"]
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: resolve(), range(2)))
    assert sorted(outcomes) == ["Ticket is already resolved", "resolved"]
