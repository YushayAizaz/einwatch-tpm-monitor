import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import app
from src.demo import AS_OF, demo_database

HISTORY = "/nodes/gpu-demo-01/telemetry"


def test_preview_reads_the_demo_snapshot_and_serves_dashboard():
    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health == {"status": "ok", "mode": "synthetic-demo", "snapshot_as_of": AS_OF.isoformat()}
        nodes = client.get("/nodes/health").json()
        assert len(nodes) == 3
        assert {node["asset"]["telemetry_source"] for node in nodes} == {"simulated", "inventory_only"}
        assert client.get("/nodes/local-host/health").status_code == 404

        response = client.get(HISTORY, params={"limit": 100}).json()
        assert len(response["samples"]) == 48
        assert response["next_before_id"] is None
        assert {sample["source"] for sample in response["samples"]} == {"simulated"}
        assert response["samples"][0]["observed_at"] == AS_OF.isoformat()
        assert any(sample["scenario"] == "thermal_spike" for sample in response["samples"])
        assert any(alert["rule"] == "thermal_high" for alert in client.get("/alerts").json())
        sla = client.get("/sla/compliance").json()
        assert {ticket["status"] for ticket in sla["tickets"]} >= {"met", "breached", "pending"}

        assert client.get("/").url.path == "/einwatch/index.html"
        assert "Telemetry history" in client.get("/").text
        assert client.get("/einwatch/dashboard.js").status_code == 200


def test_preview_excludes_local_mutations():
    with TestClient(app) as client:
        assert client.post("/collect").status_code == 404
        assert client.put("/assets/local-host", json={}).status_code == 404
        assert client.post("/tickets/1/resolve").status_code == 404
        assert all(set(methods) == {"get"} for methods in client.get("/openapi.json").json()["paths"].values())
    with demo_database().connect() as connection, pytest.raises(sqlite3.OperationalError, match="readonly"):
        connection.execute("DELETE FROM telemetry")
