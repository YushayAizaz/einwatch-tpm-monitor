"""Vercel entry point. Only synthetic read endpoints are registered here."""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.demo import AS_OF, INTERVAL_SECONDS, demo_database
from src.read_api import monitoring_routes
from src.version import VERSION

app = FastAPI(
    title="EinWatch Synthetic Demonstration",
    version=VERSION,
    description="Read-only synthetic hardware, telemetry, alerts and SLA results.",
)


@app.get("/health")
def health():
    with demo_database().connect() as connection:
        connection.execute("SELECT 1").fetchone()
    return {"status": "ok", "mode": "synthetic-demo", "snapshot_as_of": AS_OF.isoformat()}


app.include_router(monitoring_routes(demo_database, INTERVAL_SECONDS, lambda: AS_OF))

# Vercel serves public assets from its CDN. This mount supports local preview runs.
if not os.getenv("VERCEL"):
    public = Path(__file__).resolve().parent / "public" / "einwatch"
    app.mount("/einwatch", StaticFiles(directory=public), name="dashboard-assets")
