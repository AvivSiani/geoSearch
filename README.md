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
docker compose up -d                                  # MongoDB: tool registry, conversations
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

## Places (Stage 5)

Tools 9101 (search) and 9102 (details) answer place questions in English and
Hebrew. Seed them with `uv run geosearch-registry seed`. By default they replay
the synthetic fixtures in `fixtures/places/` (no key, no network); set
`GEOSEARCH_PLACES__PROVIDER=google` and `GEOSEARCH_PLACES__API_KEY` for the live
Google Places API (New), and record real fixtures with
`scripts/record_places.py`.

Every response carries `items` — grounded places, each inside the area, built
from tool data (`id`, `source_id`, `name`, `lon`, `lat`, `data`) — and
`answer_source` (`submitted` when the agent finished with `submit_answer`,
`fallback` otherwise). Detailed design: `docs/specs/stage-5-places.md`.

## Conversations (Stage 6)

A response's `conversation_id` continues the conversation: send it with the next
`prompt` and no `wkt` (the area is bound for the whole conversation). Where
conversations live is `GEOSEARCH_CONVERSATION__STORE`:

- `memory` (default): in-process, for tests and quick dev runs.
- `mongodb`: checkpoints, turn records and areas in `mongo.database`, so a
  conversation survives an API restart. MongoDB must be up at startup; the app
  never falls back to memory.

A conversation expires `conversation.idle_ttl_minutes` (7 days) after its last
turn. Its recorded turns are at `GET /v1/conversations/{conversation_id}` (404
when unknown or expired; 503 `STORE_UNAVAILABLE` when MongoDB is down).
`user_id` there is always `null`: a placeholder for future per-user ownership.
From the terminal, `scripts/ask.py` runs a turn and `--conversation-id`
resumes one. Detailed design: `docs/specs/stage-6-conversation-persistence.md`.

## Tool registry

Tool definitions (an integer `source_id` and a description) live in MongoDB; handler
code lives in `src/geosearch/sources/` (one handler per numeric `source_id`). Manage definitions with:

```bash
uv run geosearch-registry seed [PATHS] [--include-demo] [--prune]
uv run geosearch-registry validate      # exit 1 on seed/DB drift
uv run geosearch-registry list
uv run geosearch-registry delete <source_id>   # an integer id
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
| `conversation.store` | `GEOSEARCH_CONVERSATION__STORE` | `memory` (or `mongodb`) |
| `conversation.max_turns` | `GEOSEARCH_CONVERSATION__MAX_TURNS` | `20` |
| `conversation.idle_ttl_minutes` | `GEOSEARCH_CONVERSATION__IDLE_TTL_MINUTES` | `10080` (7 days) |
| `conversation.checkpoint_durability` | `GEOSEARCH_CONVERSATION__CHECKPOINT_DURABILITY` | `exit` (or `sync`) |
| `conversation.checkpoint_warn_bytes` | `GEOSEARCH_CONVERSATION__CHECKPOINT_WARN_BYTES` | `4000000` |
| `conversation.checkpoints_collection` | `GEOSEARCH_CONVERSATION__CHECKPOINTS_COLLECTION` | `checkpoints` |
| `conversation.checkpoint_writes_collection` | `GEOSEARCH_CONVERSATION__CHECKPOINT_WRITES_COLLECTION` | `checkpoint_writes` |
| `conversation.conversations_collection` | `GEOSEARCH_CONVERSATION__CONVERSATIONS_COLLECTION` | `conversations` |
| `conversation.areas_collection` | `GEOSEARCH_CONVERSATION__AREAS_COLLECTION` | `areas` |
| `mongo.uri` | `GEOSEARCH_MONGO__URI` | `mongodb://localhost:27017` |
| `mongo.database` | `GEOSEARCH_MONGO__DATABASE` | `geosearch` |
| `mongo.server_selection_timeout_ms` | `GEOSEARCH_MONGO__SERVER_SELECTION_TIMEOUT_MS` | `2000` |
| `registry.seeds_dir` | `GEOSEARCH_REGISTRY__SEEDS_DIR` | `registry/seeds` |
| `registry.seed_demo` | `GEOSEARCH_REGISTRY__SEED_DEMO` | `false` |
| `registry.max_inline_result_chars` | `GEOSEARCH_REGISTRY__MAX_INLINE_RESULT_CHARS` | `1500` |
| `registry.required` | `GEOSEARCH_REGISTRY__REQUIRED` | `true` |
| `registry.strict_startup` | `GEOSEARCH_REGISTRY__STRICT_STARTUP` | `true` |
| `places.provider` | `GEOSEARCH_PLACES__PROVIDER` | `replay` (or `google`) |
| `places.api_key` | `GEOSEARCH_PLACES__API_KEY` | unset (required for `google`) |
| `places.max_results` | `GEOSEARCH_PLACES__MAX_RESULTS` | `20` |
| `places.fixtures_dir` | `GEOSEARCH_PLACES__FIXTURES_DIR` | `fixtures/places` |
| `places.timeout_s` | `GEOSEARCH_PLACES__TIMEOUT_S` | `10.0` |
| `summarizer.chunk_tokens` | `GEOSEARCH_SUMMARIZER__CHUNK_TOKENS` | `2500` |
| `summarizer.max_chunks` | `GEOSEARCH_SUMMARIZER__MAX_CHUNKS` | `4` |
| `summarizer.max_output_tokens` | `GEOSEARCH_SUMMARIZER__MAX_OUTPUT_TOKENS` | `400` |

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
