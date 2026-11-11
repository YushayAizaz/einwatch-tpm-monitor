"""Validated asset records and transactional local SQLite persistence."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Asset(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    vendor: Literal["Dell", "HPE", "Cisco", "Supermicro"]
    model: str = Field(min_length=1, max_length=120)
    serial_number: str = Field(min_length=1, max_length=120)
    sla_tier: Literal["8x5xNBD", "24x7x4"]
    warranty_end: date
    eol_date: date
    eosl_date: date
    telemetry_source: Literal["local_psutil", "simulated", "inventory_only"] = "inventory_only"
    lifecycle_source: str = "User supplied; verify against the asset contract"

    @model_validator(mode="after")
    def validate_dates(self):
        if self.eosl_date < self.eol_date:
            raise ValueError("EOSL must be on or after EOL")
        return self


SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
 id TEXT PRIMARY KEY, vendor TEXT NOT NULL, model TEXT NOT NULL, serial_number TEXT NOT NULL,
 sla_tier TEXT NOT NULL CHECK(sla_tier IN ('8x5xNBD','24x7x4')),
 warranty_end TEXT NOT NULL, eol_date TEXT NOT NULL, eosl_date TEXT NOT NULL,
 telemetry_source TEXT NOT NULL, lifecycle_source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS telemetry (
 id INTEGER PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
 observed_at TEXT NOT NULL, source TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_telemetry_asset ON telemetry(asset_id,id DESC);
CREATE TABLE IF NOT EXISTS tickets (
 id INTEGER PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id), rule TEXT NOT NULL,
 severity TEXT NOT NULL, summary TEXT NOT NULL, evidence TEXT NOT NULL,
 opened_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, due_at TEXT NOT NULL,
 sla_tier TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
 acknowledged_at TEXT, resolved_at TEXT, occurrences INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_rule ON tickets(asset_id,rule)
 WHERE status IN ('open','acknowledged');
"""


class Database:
    def __init__(self, path="data/einwatch.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def put_asset(self, asset: Asset):
        data = asset.model_dump(mode="json")
        keys = list(data)
        with self.connect() as connection:
            connection.execute(f"INSERT INTO assets ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)}) "
                               "ON CONFLICT(id) DO UPDATE SET " + ",".join(f"{k}=excluded.{k}" for k in keys[1:]),
                               list(data.values()))
        return data

    def assets(self):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM assets ORDER BY id")]

    def seed_demo(self, today=None):
        today = today or datetime.now(timezone.utc).date()
        existing = {a["id"] for a in self.assets()}
        examples = [
            ("local-host", "Dell", "PowerEdge R750 (demo inventory label)", "24x7x4", "local_psutil", 180),
            ("gpu-demo-01", "Supermicro", "8-GPU server (synthetic)", "24x7x4", "simulated", 20),
            ("hpe-demo-01", "HPE", "ProLiant DL380 (demo)", "8x5xNBD", "inventory_only", -5),
            ("cisco-demo-01", "Cisco", "UCS C240 (demo)", "8x5xNBD", "inventory_only", 365),
        ]
        for identifier, vendor, model, tier, source, warranty_days in examples:
            if identifier not in existing:
                self.put_asset(Asset(id=identifier, vendor=vendor, model=model,
                    serial_number=f"FICTIONAL-{identifier}", sla_tier=tier,
                    warranty_end=today + timedelta(days=warranty_days),
                    eol_date=today + timedelta(days=60), eosl_date=today + timedelta(days=365),
                    telemetry_source=source,
                    lifecycle_source="Fictional demo dates relative to first startup; not OEM guidance"))

    def save_telemetry(self, payload):
        with self.connect() as connection:
            connection.execute("INSERT INTO telemetry(asset_id,observed_at,source,payload) VALUES (?,?,?,?)",
                (payload["asset_id"], payload["observed_at"], payload["source"], json.dumps(payload)))

    def latest(self, asset_id):
        with self.connect() as connection:
            row = connection.execute("SELECT payload FROM telemetry WHERE asset_id=? ORDER BY id DESC LIMIT 1",
                                     (asset_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def tickets(self, active_only=True):
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM tickets" +
                (" WHERE status IN ('open','acknowledged')" if active_only else "") + " ORDER BY id DESC")
            return [dict(row) for row in rows]

    def transition(self, ticket_id, action, now=None):
        if action not in {"acknowledge", "resolve"}:
            raise ValueError("Unknown ticket action")
        now = (now or datetime.now(timezone.utc)).isoformat()
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            if row is None:
                raise KeyError(ticket_id)
            if row["status"] == "resolved":
                raise ValueError("Ticket is already resolved")
            if action == "acknowledge":
                connection.execute("UPDATE tickets SET status='acknowledged', acknowledged_at=coalesce(acknowledged_at,?) WHERE id=?",
                                   (now, ticket_id))
            else:
                connection.execute("UPDATE tickets SET status='resolved', resolved_at=?, acknowledged_at=coalesce(acknowledged_at,?) WHERE id=?",
                                   (now, now, ticket_id))
            return dict(connection.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone())
