# Development and operations runbook

## Run and verify

Install Python 3.12 and create a virtual environment. On Windows, use the virtual environment’s `Scripts/python.exe` instead of `.venv/bin/python`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q

# Read-only hosted-mode equivalent
.venv/bin/python -m uvicorn app:app --port 8000

# Local collector, writable SQLite and operator endpoints
.venv/bin/python -m src.api
```

Open `http://127.0.0.1:8000/` and `/docs`. The two commands use the same port by default, so stop one before starting the other. CI also builds the container and checks its local health and dashboard HTML. The documented Docker command binds to loopback and uses a named volume. Build it with `docker build -t einwatch-tpm-monitor .`. The default writable database is `data/einwatch.sqlite3`, or the path in `EINWATCH_DB`. Never ship that database or its WAL files to the preview. Use SQLite’s backup API or the SQLite shell’s `.backup` for consistent local backups.

## Make and test a change

1. Fetch and branch from the base you intend to change. Make one reviewable change.
2. Run the tests. Use `.venv/bin/python -m pytest tests/test_telemetry_history.py -q` to focus on the API.
3. Check `git diff --check`, review each staged file, then commit and push to your branch.
4. Open a PR against `main`. Review the CI run, OpenAPI schema and functional preview. Compare dashboard results against `/health`, `/nodes/health`, `/alerts` and `/sla/compliance`.
5. On a phone-width page, select `cisco-demo-01` and verify the empty history state, then return to `gpu-demo-01`. Verify cursor navigation and that the API returns 404 for an unknown asset and 422 for `limit=101`.

Dependencies are split across `requirements.txt` and `requirements-dev.txt`. Update pins on a branch, validate a clean install, review release notes and vulnerability advisories, and run tests and preview smoke checks. Treat bot-based updates and recurring security scans as separate approval decisions.

## Deployment and rollback

The Vercel app uses the FastAPI preset, repository root, Python 3.12 and `app.py`. The `public/` files are served by the CDN. Existing local usage still enters through `src.api`. Preview uses Vercel Authentication where configured and needs no environment variables, integrations or paid add-ons.

A production deployment requires owner approval. The repository configuration disables automatic Git deploys from `main`. The Vercel project also skips builds tagged as production, including any first Git deployment classified that way by Vercel. For review, create or verify a Preview deployment from the PR branch and check the deployment’s source SHA and target. Vercel is configured through the project settings and [Git deployment settings](https://vercel.com/docs/git). If automatic previews are unavailable for a commit author, use the authorized owner’s Vercel connection to deploy the reviewed branch or upload the reviewed source to the Preview target. Confirm Vercel attached the right source to each build.

For a broken preview, give reviewers the last verified immutable deployment URL while investigating the failed build. Create a corrected commit or revert the regression in a new branch and deploy it to Preview. For a future authorized production release, record the deployment ID and commit before promotion. If production needs a rollback, inspect the previous deployment and verify its compatibility with any data migrations. With approval, use [Vercel Instant Rollback](https://vercel.com/docs/instant-rollback) to select the prior deployment, then re-run health, API and UI smoke checks. Rolling back application code does not roll back a database migration.

## Investigate failures

- For failed browser requests, capture the path, HTTP status and time. For 422, inspect the response’s validation fields. A 404 for a write endpoint is expected on Preview.
- For a Preview 500, open the deployment’s Function Logs and identify the first relevant exception. Check the deployed SHA and verify fixture seeding with a local `app:app` run. For missing CSS or JS, check the corresponding `/einwatch/` asset and confirm the build included `public/`.
- For a local collector error, inspect stdout and `/health` collection errors. Reproduce with an isolated `EINWATCH_DB` and a known synthetic scenario. Availability of sensors depends on the host OS.
- For an alert/SLA mismatch, inspect ticket timestamps, match on ticket ID and compare `acknowledged_at` against `due_at`. The preview’s `snapshot_as_of` is the report clock.

## Hosting roadmap

Vercel’s request-driven functions fit the dashboard and API. Vercel Hobby’s legacy Cron Jobs run at most daily, so they do not support frequent monitoring. [Vercel Cron pricing and limits](https://vercel.com/docs/schedules/cron-jobs/usage-and-pricing)

Supabase Free offers persistent Postgres with a 500 MB database allowance and can pause after a week of low activity. It is a candidate for a portfolio-scale ingestion slice. Render Free can run FastAPI but idles after 15 minutes without inbound traffic and loses local SQLite files when it spins down. [Supabase plans](https://supabase.com/pricing) · [Render Free limits](https://render.com/docs/free)

Evaluate a bounded scheduled worker, such as Cloudflare Workers with Cron Triggers, for a single owner-approved HTTPS endpoint, with results posted to an authenticated API and persisted independently. Verify quotas, schedule behavior and failure handling before enabling a job. [Cloudflare Workers limits](https://developers.cloudflare.com/workers/platform/limits/) · [Cloudflare Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
