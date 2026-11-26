# Technical handover and learning

## Walk the feature from browser to SQLite

1. In `public/einwatch/dashboard.js`, `loadOverview()` reads health, inventory, active alerts and SLA results. It matches SLA status to each alert by ticket ID. `selectAsset()` resets the cursor stack. `loadHistory()` sends `limit` and `before_id` and cancels stale requests after an asset change.
2. In `src/read_api.py`, `telemetry_history()` declares its query limits, checks asset existence with `require_asset()`, and returns a typed page envelope. FastAPI generates the required parameter and response shapes in the OpenAPI contract.
3. In `src/models_and_db.py`, `telemetry_page()` performs an asset-indexed, parameterized range query with a one-row lookahead. It decodes each sample’s JSON and returns the last visible ID as the next cursor.
4. The browser renders rows using text nodes. The chart is drawn from the same results, in oldest-to-newest order by insertion. Polling and archiving are deferred.
5. `app.py` uses the shared router with `demo_database()` from `src/demo.py`. The local service in `src/api.py` uses the writable database and runs a scheduler. Read [architecture](architecture.md) for the diagrams and [operations](operations.md) for commands, logs and rollback.
6. `tests/test_einwatch.py` documents the original contracts. `tests/test_telemetry_history.py` checks cursor stability, validation, indexing and competing ticket transitions. `tests/test_demo.py` checks the synthetic dataset and preview’s read-only behavior. Run one test at a time, then change an assertion to see its failure mode.

## Recruiter demonstration

### 30-second introduction

“EinWatch connects hardware telemetry and lifecycle records to maintenance tickets and response deadlines. In this hosted demo I can browse a synthetic GPU incident, inspect the device inventory and see how an alert maps to its SLA. The local version also collects host metrics into SQLite. The project uses FastAPI, indexed cursor pagination, and automated tests.”

### Two-minute presentation

1. Introduce the operational question: which devices need attention, and how quickly does an operator need to respond?
2. Start at the deployed dashboard. Point to the synthetic label, inventory count, active alerts and response compliance.
3. View GPU telemetry. Select Older to see the temperature spike. Show the thermal alert and its response deadline.
4. Select an inventory-only asset to show that the UI represents unavailable readings explicitly. Open API docs to show the validated query parameters.
5. Explain the architectural split between the hosted synthetic snapshot and the local collector. Describe durable uptime ingestion as a follow-up.

### Five-minute technical demo

| Time | Show |
| --- | --- |
| 0:00–0:50 | Open the dashboard, identify data provenance, and distinguish ticket SLA from service uptime. |
| 0:50–1:40 | Page through GPU samples, switch to an inventory-only device, and return to the GPU. |
| 1:40–2:35 | Use Swagger to call `/nodes/gpu-demo-01/telemetry?limit=2`, reuse the returned cursor, then use `limit=101` and inspect the 422 response. |
| 2:35–3:35 | Walk through both architecture diagrams and locate the index and SQL in the code. Explain why observation times can tie and why appends do not move an existing cursor. |
| 3:35–4:20 | Open the pagination and hosted isolation tests. Point out what the browser test adds beyond the API tests. |
| 4:20–5:00 | Discuss what persistent monitoring needs, what you personally verified, and one change you made yourself if applicable. |

## Interview practice

| Focus | Question and suggested answer | Follow-up |
| --- | --- | --- |
| Skaler · validation and provenance | How do you handle a missing reading? The API preserves the original payload and its source. The dashboard shows an empty-history state or a missing value as appropriate. It does not manufacture a measurement. | How would you validate a new category of asset data with a partially known schema? |
| Revolut · consistency | What prevents duplicate or skipped rows if telemetry arrives during pagination? An exclusive ID boundary and a matching index preserve the next page under appends. Ticket transitions use a write transaction before reading the current ticket state. | What changes with two collectors inserting replayed events into Postgres? Propose an idempotency key and unique constraint. |
| Sentry · observability | How does a thermal spike become actionable? The collector stores a sample. The rules compare values against thresholds and a recent previous reading. An upsert deduplicates repeated alerts, and each ticket carries a response deadline. | How would you correlate an API failure with a specific ingest attempt without logging a customer payload? |
| Canonical · systems and deployment | Why does the local app use one worker and the hosted app use a separate entry point? One worker owns the local scheduler and the file-backed SQLite database. The hosted function uses a reproducible synthetic snapshot with only read routes exposed. | Describe failure modes on container restart, and how you would restore a local SQLite backup consistently. |
| Cross-company · performance | Why fetch one extra row? It answers whether another page exists with bounded work and avoids counting the entire matching set. | What would the query plan look like for timestamp ordering plus an asset filter? Why would you include the ID? |
| Cross-company · deployment | How would you prove the release works? Run CI, compare the deployed commit SHA, load the dashboard in a browser, exercise navigation and paging, and compare the page to API responses. Review invalid inputs and logs. | Which signal should trigger a rollback if all tests passed and the new deployment returns 500s? |

## Practical exercises

1. **Read the API.** Traverse the GPU history using only Swagger and `next_before_id`. Verify all 48 IDs appear once.
2. **Debug a response.** Try `limit=0`, an unknown asset, and the inventory-only node. Explain the purpose of each status and body.
3. **Change the dashboard.** Add a RAM column using real local samples. Preserve the missing-data behavior in the synthetic demo.
4. **Add a regression test.** Create a sample whose observation time predates the last insertion. Show how both ordering conventions affect expected results.
5. **Analyze persistence.** Run `EXPLAIN QUERY PLAN` for the telemetry query. Compare index choices with and without the existing index using a fresh disposable database.
6. **Work through a release.** Create a branch, open a PR, reproduce a safe 422 in Preview, and locate that request in logs if available. Explain the difference between reverting source and moving a deployment alias.
7. **Design an ingestion service.** Sketch a single-service monitor with fixed target configuration, timeouts, idempotent writes, privacy-aware logs and a failure policy. Review how a caller could misuse arbitrary URL input before offering such a feature.

## Contribution record

- The baseline was uploaded to the repository in `668400e` before this milestone. Git history establishes the upload and file state. It does not establish who wrote individual lines.
- Yushay Aizaz defined the product direction, target audiences, milestone scope, and approval and learning goals.
- The monitoring-history API, dashboard, hosted demo, tests, CI, architecture choices and documentation in this milestone were AI-assisted. Attribute implementation accurately when describing the project.
- Add a dated entry with links for any subsequent owner-written changes, review decisions, debugging sessions or deployment approvals. Record independent work only once completed.
