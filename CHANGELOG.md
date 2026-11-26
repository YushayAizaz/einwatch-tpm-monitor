# Changelog

## 1.1.0 (proposed)

- Add cursor-paginated telemetry history, backed by the existing SQLite asset and sample ID index.
- Add a dashboard for inventory, telemetry, alert details, and ticket response SLA.
- Add an isolated, read-only Vercel demonstration using a deterministic synthetic SQLite snapshot.
- Serialize competing local ticket transitions to preserve resolved state and first acknowledgment.
- Pin Python dependencies, separate test dependencies, and add pull-request CI.
- Document deployment, rollback, architectural decisions and learning exercises.

## 1.0.0 (original baseline)

- Local psutil collection, simulated GPU telemetry and SQLite-backed TPM lifecycle inventory, alert tickets, and acknowledgment deadlines.
