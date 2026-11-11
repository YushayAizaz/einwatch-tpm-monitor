"""Loopback-first FastAPI service with a local periodic collection lifecycle."""

import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException

from .alert_engine import evaluate, sla_report
from .collector import GPUNodeSimulator, collect_local
from .models_and_db import Asset, Database

LOGGER = logging.getLogger(__name__)


def create_app(db_path=None, interval_seconds=30, background=True):
    if interval_seconds < 1:
        raise ValueError("Collection interval must be at least one second")
    db_path = db_path or os.getenv("EINWATCH_DB", "data/einwatch.sqlite3")
    lock = threading.Lock()
    simulator = GPUNodeSimulator()

    def cycle(scenario="normal"):
        with lock:
            database = app.state.database
            assets = database.assets()
            snapshots = []
            errors = []
            for asset in assets:
                snapshot = None
                previous = database.latest(asset["id"])
                try:
                    if asset["telemetry_source"] == "local_psutil":
                        snapshot = collect_local(asset["id"])
                    elif asset["telemetry_source"] == "simulated":
                        snapshot = simulator.collect(asset["id"], scenario)
                except (OSError, RuntimeError) as exc:
                    LOGGER.exception("Local collection failed for %s", asset["id"])
                    errors.append({"asset_id": asset["id"], "error": str(exc)})
                if snapshot:
                    database.save_telemetry(snapshot)
                    snapshots.append(snapshot)
                evaluate(database, asset, snapshot, previous)
            app.state.last_collection_errors = errors
            return {"snapshots": snapshots, "errors": errors}

    async def periodic():
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await asyncio.to_thread(cycle)
            except Exception:
                LOGGER.exception("Collection cycle failed; retrying at next interval")

    @asynccontextmanager
    async def lifespan(app):
        app.state.database = Database(db_path)
        app.state.database.seed_demo()
        await asyncio.to_thread(cycle)
        task = asyncio.create_task(periodic()) if background else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            # A to_thread call may still be finishing after cancellation.
            await asyncio.to_thread(lambda: lock.acquire())
            lock.release()

    app = FastAPI(title="EinWatch: Agentless Infrastructure Telemetry & TPM Lifecycle Monitor",
                  version="1.0.0", lifespan=lifespan,
                  description="Local psutil metrics, simulated GPUs, and internal TPM maintenance tickets.")

    @app.get("/health")
    def health():
        with app.state.database.connect() as connection:
            connection.execute("SELECT 1")
        return {"status": "ok", "mode": "local-only", "gpu_telemetry": "simulated",
                "last_collection_errors": app.state.last_collection_errors}

    @app.get("/assets")
    def assets():
        return app.state.database.assets()

    @app.put("/assets/{asset_id}")
    def put_asset(asset_id: str, asset: Asset):
        if asset_id != asset.id:
            raise HTTPException(400, "Path and body asset IDs must match")
        with lock:
            existing = app.state.database.assets()
            if asset.telemetry_source == "local_psutil" and any(
                a["telemetry_source"] == "local_psutil" and a["id"] != asset.id for a in existing
            ):
                raise HTTPException(409, "Only one asset may represent this local host")
            return app.state.database.put_asset(asset)

    def node_health(asset):
        latest = app.state.database.latest(asset["id"])
        if latest and latest["source"] != asset["telemetry_source"]:
            latest = None
        alerts = [a for a in app.state.database.tickets() if a["asset_id"] == asset["id"]]
        age = ((datetime.now(timezone.utc) - datetime.fromisoformat(latest["observed_at"])).total_seconds()
               if latest else None)
        stale = age is not None and age > max(interval_seconds * 3, 60)
        status = "unmonitored" if latest is None else "stale" if stale else "critical" if any(
            a["severity"] == "critical" for a in alerts) else "warning" if alerts else "healthy"
        return {"asset": asset, "status": status, "sample_age_seconds": age,
                "telemetry": latest, "active_alert_count": len(alerts)}

    @app.get("/nodes/health")
    def nodes_health():
        return [node_health(a) for a in app.state.database.assets()]

    @app.get("/nodes/{asset_id}/health")
    def single_node(asset_id: str):
        asset = next((a for a in app.state.database.assets() if a["id"] == asset_id), None)
        if not asset:
            raise HTTPException(404, "Asset not found")
        return node_health(asset)

    @app.post("/collect")
    def collect(scenario: Literal["normal", "thermal_spike", "load_spike"] = "normal"):
        return cycle(scenario)

    @app.get("/alerts")
    def alerts(active_only: bool = True):
        return app.state.database.tickets(active_only)

    @app.post("/tickets/{ticket_id}/{action}")
    def transition(ticket_id: int, action: Literal["acknowledge", "resolve"]):
        try:
            return app.state.database.transition(ticket_id, action)
        except KeyError as exc:
            raise HTTPException(404, "Ticket not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/sla/compliance")
    def compliance():
        return sla_report(app.state.database.tickets(active_only=False))

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
