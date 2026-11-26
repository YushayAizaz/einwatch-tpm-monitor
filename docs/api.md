# Telemetry history API

`GET /nodes/{asset_id}/telemetry` is available in local and synthetic demonstration modes. Open `/docs` for each mode’s interactive OpenAPI schema. The [README](../README.md#api-reference) lists the original endpoints.

| Parameter | Accepted values |
| --- | --- |
| `asset_id` | 1–80 letters, digits, underscores or hyphens |
| `limit` | Integer from 1 to 100. Omit to use 25 |
| `before_id` | Exclusive sample ID. Omit for the newest page. Valid values are 1 through SQLite’s signed integer maximum |

```bash
curl 'http://127.0.0.1:8000/nodes/gpu-demo-01/telemetry?limit=12'
# Copy next_before_id from the response into the next request.
curl 'http://127.0.0.1:8000/nodes/gpu-demo-01/telemetry?limit=12&before_id=37'
```

The response has `asset_id`, `samples` and `next_before_id`. Each sample includes its SQLite `id`, the `source`, an ISO 8601 `observed_at` and source-specific fields. For example, simulated GPUs have `gpus`, `node_power_kw` and `max_temperature_c`. Local host samples have `cpu_percent`, `ram_percent` and `disk_percent`. The source of an individual record can differ from the asset’s current source after an inventory edit. Missing sensors and inapplicable fields appear as a dash in the dashboard.

Samples are ordered by descending insertion ID. Observation time is returned as data and may repeat or arrive out of order. Use `next_before_id` with the same asset to advance. A null cursor indicates the end. A known asset without samples returns 200 with an empty array. An unknown asset returns 404. Invalid parameters return FastAPI’s structured 422 response. Sample IDs belong to their current database and can change when replacing or restoring it.

SQLite uses the existing `idx_telemetry_asset(asset_id, id DESC)` index. A bounded `LIMIT limit + 1` query retrieves one extra row to decide whether another page exists. The extra row is omitted from the response. Page 1 can include newer samples added between requests, while subsequent pages anchored to earlier IDs keep their position. This approach provides neither a total count nor random access to an arbitrary page number.
