# GeoSearch Agent

A generic, spatially grounded agent. Every request is a WKT geometry (the
geographic area) plus a natural-language prompt (what the user wants).

**Stage 1** (this code) is the deterministic foundation: a FastAPI endpoint
over a pure-Python core, with no LLM involved, that turns `{wkt, prompt}`
into a validated request carrying a server-generated `area_id`. It also
provides the deterministic geo operations (`geosearch.geo.ops.AreaOps`) that
later stages will call by `area_id`. See `CLAUDE.md` for the invariants this
code is built to.

## Run

```bash
uv sync
docker compose up -d                                  # MongoDB for the tool registry
uv run geosearch-registry seed --include-demo         # optional: the demo tool
uv run uvicorn geosearch.api.main:app --reload
```

`geosearch.api.main` is the entry point: importing it builds the app and
connects to MongoDB. Code and tests import `create_app` from
`geosearch.api.app`, which has no import-time side effects.

## Test

```bash
uv run pytest
uv run ruff check .
```

## Tool registry

Tool definitions (a `source_id` and a description) live in MongoDB; handler
code lives in `src/geosearch/capabilities/`. Manage definitions with:

```bash
uv run geosearch-registry seed [PATHS] [--include-demo] [--prune]
uv run geosearch-registry validate      # exit 1 on seed/DB drift
uv run geosearch-registry list
uv run geosearch-registry delete <source_id>
```

Exit codes: 0 ok, 1 validation problems, 2 usage or connection error. See
"How to add a tool" in `CLAUDE.md`.

## Configure

Configuration is validated at startup via `pydantic-settings` and fails fast
on invalid values (e.g. `min_radius_m > default_radius_m`). Every setting can
be overridden with an environment variable, prefixed `GEOSEARCH_`, with `__`
as the nested delimiter.

| Setting | Env var | Default |
| --- | --- | --- |
| `point_buffer.strategy` | `GEOSEARCH_POINT_BUFFER__STRATEGY` | `geodesic_circle` |
| `point_buffer.default_radius_m` | `GEOSEARCH_POINT_BUFFER__DEFAULT_RADIUS_M` | `10.0` |
| `point_buffer.min_radius_m` | `GEOSEARCH_POINT_BUFFER__MIN_RADIUS_M` | `1.0` |
| `point_buffer.max_radius_m` | `GEOSEARCH_POINT_BUFFER__MAX_RADIUS_M` | `5000.0` |
| `point_buffer.quad_segs` | `GEOSEARCH_POINT_BUFFER__QUAD_SEGS` | `16` |
| `point_buffer.allow_request_override` | `GEOSEARCH_POINT_BUFFER__ALLOW_REQUEST_OVERRIDE` | `false` |
| `limits.max_wkt_bytes` | `GEOSEARCH_LIMITS__MAX_WKT_BYTES` | `100000` |
| `limits.max_vertices` | `GEOSEARCH_LIMITS__MAX_VERTICES` | `10000` |
| `limits.max_area_km2` | `GEOSEARCH_LIMITS__MAX_AREA_KM2` | `100.0` |
| `limits.max_prompt_chars` | `GEOSEARCH_LIMITS__MAX_PROMPT_CHARS` | `2000` |
| `area_store.max_entries` | `GEOSEARCH_AREA_STORE__MAX_ENTRIES` | `10000` |
| `mongo.uri` | `GEOSEARCH_MONGO__URI` | `mongodb://localhost:27017` |
| `mongo.database` | `GEOSEARCH_MONGO__DATABASE` | `geosearch` |
| `mongo.server_selection_timeout_ms` | `GEOSEARCH_MONGO__SERVER_SELECTION_TIMEOUT_MS` | `2000` |
| `registry.seeds_dir` | `GEOSEARCH_REGISTRY__SEEDS_DIR` | `registry/seeds` |
| `registry.seed_demo` | `GEOSEARCH_REGISTRY__SEED_DEMO` | `false` |
| `registry.max_inline_result_chars` | `GEOSEARCH_REGISTRY__MAX_INLINE_RESULT_CHARS` | `1500` |
| `registry.required` | `GEOSEARCH_REGISTRY__REQUIRED` | `true` |
| `registry.strict_startup` | `GEOSEARCH_REGISTRY__STRICT_STARTUP` | `true` |

For example, to buffer Points with a 1000 m radius instead of the default:

```bash
export GEOSEARCH_POINT_BUFFER__DEFAULT_RADIUS_M=1000
uv run uvicorn geosearch.api.main:app
```

## Example

A Polygon area:

```bash
curl -s http://localhost:8000/v1/requests \
  -H 'content-type: application/json' \
  -d '{
    "wkt": "POLYGON((34.75 32.05, 34.80 32.05, 34.80 32.10, 34.75 32.10, 34.75 32.05))",
    "prompt": "Find me a good Asian restaurant that is not too expensive."
  }'
```

A Point, which the server buffers into a geodesic circle (10 m by default)
and reports back in the response's `buffer_radius_m`, `buffer_strategy` and
`notes`:

```bash
curl -s http://localhost:8000/v1/requests \
  -H 'content-type: application/json' \
  -d '{
    "wkt": "POINT(34.78 32.08)",
    "prompt": "Find me a good Asian restaurant that is not too expensive."
  }'
```

A validation failure returns HTTP 422 with an `ErrorEnvelope`:

```json
{
  "code": "INVALID_GEOMETRY",
  "message": "geometry is not valid",
  "details": { "reason": "Self-intersection[0.5 0.5]" }
}
```

Interactive API docs (including every error code's `ErrorEnvelope` schema)
are served at `/docs` while the app is running.
