"""Read-only routes shared by the local service and the hosted demonstration."""

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .alert_engine import sla_report
from .models_and_db import Database


class TelemetryPage(BaseModel):
    asset_id: str
    samples: list[dict[str, Any]]
    next_before_id: int | None


def current_time():
    return datetime.now(timezone.utc)


def monitoring_routes(get_database, interval_seconds, clock=current_time):
    router = APIRouter()
    Store = Annotated[Database, Depends(get_database)]

    def require_asset(db, asset_id):
        asset = db.get_asset(asset_id)
        if asset is None:
            raise HTTPException(404, "Asset not found")
        return asset

    def node_health(db, asset, alerts):
        latest = db.latest(asset["id"])
        if latest and latest["source"] != asset["telemetry_source"]:
            latest = None
        age = (clock() - datetime.fromisoformat(latest["observed_at"])).total_seconds() if latest else None
        if latest is None:
            status = "unmonitored"
        elif age > max(interval_seconds * 3, 60):
            status = "stale"
        elif any(alert["severity"] == "critical" for alert in alerts):
            status = "critical"
        else:
            status = "warning" if alerts else "healthy"
        return {"asset": asset, "status": status, "sample_age_seconds": age,
                "telemetry": latest, "active_alert_count": len(alerts)}

    @router.get("/", include_in_schema=False)
    def dashboard():
        return RedirectResponse("/einwatch/index.html")

    @router.get("/assets")
    def assets(db: Store):
        return db.assets()

    @router.get("/nodes/health")
    def nodes_health(db: Store):
        alerts_by_asset = {}
        for alert in db.tickets():
            alerts_by_asset.setdefault(alert["asset_id"], []).append(alert)
        return [node_health(db, asset, alerts_by_asset.get(asset["id"], [])) for asset in db.assets()]

    @router.get("/nodes/{asset_id}/health")
    def single_node(asset_id: str, db: Store):
        asset = require_asset(db, asset_id)
        alerts = [alert for alert in db.tickets() if alert["asset_id"] == asset_id]
        return node_health(db, asset, alerts)

    @router.get("/nodes/{asset_id}/telemetry", response_model=TelemetryPage)
    def telemetry_history(
        asset_id: Annotated[str, Path(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")],
        db: Store,
        limit: Annotated[int, Query(ge=1, le=100, description="Maximum samples to return")] = 25,
        before_id: Annotated[int | None, Query(ge=1, le=2**63 - 1, description="Exclusive sample ID")] = None,
    ):
        """List recorded samples newest first by insertion ID."""
        require_asset(db, asset_id)
        samples, next_before_id = db.telemetry_page(asset_id, limit, before_id)
        return TelemetryPage(asset_id=asset_id, samples=samples, next_before_id=next_before_id)

    @router.get("/alerts")
    def alerts(db: Store, active_only: bool = True):
        return db.tickets(active_only)

    @router.get("/sla/compliance")
    def compliance(db: Store):
        return sla_report(db.tickets(active_only=False), clock())

    return router
