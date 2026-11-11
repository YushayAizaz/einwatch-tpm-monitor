"""Explainable anomaly/lifecycle rules and internal maintenance ticket SLAs."""

import json
from datetime import datetime, time, timedelta, timezone


def deadline(opened_at, tier):
    """Demo response targets: four elapsed hours or next weekday 17:00 UTC."""
    if opened_at.tzinfo is None:
        raise ValueError("SLA clock requires a timezone-aware datetime")
    opened_at = opened_at.astimezone(timezone.utc)
    if tier == "24x7x4":
        return opened_at + timedelta(hours=4)
    if tier != "8x5xNBD":
        raise ValueError("Unsupported SLA tier")
    accepted = opened_at
    if accepted.weekday() >= 5 or accepted.time() >= time(17):
        accepted = datetime.combine(accepted.date() + timedelta(days=1), time(9), timezone.utc)
    while accepted.weekday() >= 5:
        accepted += timedelta(days=1)
    target = accepted.date() + timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return datetime.combine(target, time(17), timezone.utc)


def findings(asset, telemetry=None, previous=None, now=None):
    now = now or datetime.now(timezone.utc)
    result = []

    def add(rule, severity, summary, evidence):
        result.append({"rule": rule, "severity": severity, "summary": summary, "evidence": evidence})

    for field, label in [("warranty_end", "Warranty"), ("eol_date", "OEM EOL"), ("eosl_date", "OEM EOSL")]:
        days = (datetime.fromisoformat(asset[field]).date() - now.date()).days
        if days <= 30:
            add(field, "critical" if field == "eosl_date" and days < 0 else "warning",
                f"{label} {'expired' if days < 0 else 'due within 30 days'}",
                {"date": asset[field], "days_remaining": days, "lifecycle_source": asset["lifecycle_source"]})
    if not telemetry:
        return result
    source = telemetry["source"]
    temp = telemetry.get("max_temperature_c")
    if temp is not None and temp >= 85:
        add("thermal_high", "critical", "Thermal threshold exceeded", {"temperature_c": temp, "source": source})
    for field in ("cpu_percent", "ram_percent", "disk_percent"):
        if telemetry.get(field, 0) >= 90:
            add(field, "warning", f"High {field}", {"value": telemetry[field], "source": source})
    gpus = telemetry.get("gpus", [])
    if any(g["utilization_percent"] >= 95 or g["vram_used_gib"] / g["vram_total_gib"] >= 0.95 for g in gpus):
        add("gpu_pressure", "warning", "GPU utilization or VRAM pressure", {"gpus": gpus, "source": source})
    if telemetry.get("node_power_kw", 0) >= 5.5:
        add("power_high", "warning", "Node power exceeds 5.5 kW demo budget", {"kw": telemetry["node_power_kw"], "source": source})
    if previous and previous["source"] == source:
        elapsed = (datetime.fromisoformat(telemetry["observed_at"]) - datetime.fromisoformat(previous["observed_at"])).total_seconds()
        if 0 < elapsed <= 300:
            for field, threshold in [("max_temperature_c", 15), ("cpu_percent", 40)]:
                before, after = previous.get(field), telemetry.get(field)
                if before is not None and after is not None and after - before >= threshold:
                    add(f"spike_{field}", "warning", f"Rapid increase in {field}",
                        {"previous": before, "current": after, "delta": after - before, "source": source})
    return result


def evaluate(database, asset, telemetry=None, previous=None, now=None):
    now = now or datetime.now(timezone.utc)
    detected = findings(asset, telemetry, previous, now)
    with database.connect() as connection:
        for item in detected:
            evidence = json.dumps(item["evidence"])
            connection.execute("""INSERT INTO tickets
                (asset_id,rule,severity,summary,evidence,opened_at,last_seen_at,due_at,sla_tier)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(asset_id,rule) WHERE status IN ('open','acknowledged')
                DO UPDATE SET last_seen_at=excluded.last_seen_at, evidence=excluded.evidence,
                 severity=excluded.severity, summary=excluded.summary, occurrences=tickets.occurrences+1""",
                (asset["id"], item["rule"], item["severity"], item["summary"], evidence,
                 now.isoformat(), now.isoformat(), deadline(now, asset["sla_tier"]).isoformat(), asset["sla_tier"]))
    return detected


def sla_report(tickets, now=None):
    now = now or datetime.now(timezone.utc)
    rows = []
    for ticket in tickets:
        due = datetime.fromisoformat(ticket["due_at"])
        response = ticket["acknowledged_at"]
        observed = datetime.fromisoformat(response) if response else now
        state = "breached" if observed > due else "met" if response else "pending"
        rows.append({"ticket_id": ticket["id"], "asset_id": ticket["asset_id"], "sla_tier": ticket["sla_tier"],
                     "due_at": ticket["due_at"], "acknowledged_at": response, "status": state})
    assessed = [row for row in rows if row["status"] != "pending"]
    return {"as_of": now.isoformat(), "target": "response/acknowledgment", "tickets": rows,
            "pending": sum(r["status"] == "pending" for r in rows),
            "breached": sum(r["status"] == "breached" for r in rows),
            "compliance_percent": round(100 * sum(r["status"] == "met" for r in assessed) / len(assessed), 2) if assessed else None}
