"""Deterministic synthetic data for the isolated, read-only hosted app."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

from .alert_engine import evaluate
from .collector import GPUNodeSimulator
from .models_and_db import Database

AS_OF = datetime(2026, 11, 13, 14, tzinfo=timezone.utc)
INTERVAL_SECONDS = 30

_lock = Lock()
_storage = None
_database = None


def seed_snapshot(db):
    db.seed_demo(AS_OF.date(), include_local=False)
    asset = db.get_asset("gpu-demo-01")
    simulator = GPUNodeSimulator(seed=42)
    previous = None
    for index in range(48):
        observed_at = AS_OF - timedelta(seconds=(47 - index) * INTERVAL_SECONDS)
        scenario = {16: "load_spike", 34: "thermal_spike", 35: "thermal_spike"}.get(index, "normal")
        sample = simulator.collect(asset["id"], scenario)
        sample["observed_at"] = observed_at.isoformat()
        db.save_telemetry(sample)
        evaluate(db, asset, sample, previous, now=observed_at)
        previous = sample

    hpe = db.get_asset("hpe-demo-01")
    evaluate(db, hpe, now=AS_OF - timedelta(days=4))
    for ticket in db.tickets():
        if ticket["rule"] == "gpu_pressure":
            opened_at = datetime.fromisoformat(ticket["opened_at"])
            db.transition(ticket["id"], "resolve", now=opened_at + timedelta(minutes=5))


def demo_database():
    """Create one disposable, isolated database per process on the first API request."""
    global _database, _storage
    with _lock:
        if _database is None:
            storage = TemporaryDirectory(prefix="einwatch-demo-")
            try:
                snapshot = Path(storage.name) / "snapshot.sqlite3"
                seed_snapshot(Database(snapshot))
                database = Database(snapshot, read_only=True)
            except Exception:
                storage.cleanup()
                raise
            _storage = storage
            _database = database
        return _database
