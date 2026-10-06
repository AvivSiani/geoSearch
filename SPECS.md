# GeoSearch Agent — Specs Overview

Short map of the whole project. Detailed specs for each stage live in `docs/specs/stage-N-<name>.md`.
Project invariants live in `CLAUDE.md` and apply to every stage.

## Goal

Build a generic, spatially grounded Deep Agent. It has five defining properties:

- **Input:** every request is an immutable WKT area plus a natural-language prompt.
- **Capabilities:** the agent discovers only the capabilities it needs (places, weather, events, …) at runtime.
- **Execution:** external tools and deterministic code do the actual work.
- **Context:** compact state and progressive retrieval keep the model's context small, so it runs reliably on small-context models.
- **Extensibility:** adding a capability never changes the core agent.

Who owns what:

> The application owns the geographic context. The agent owns intent and decisions.
> Tools own external capabilities and data. Deterministic code owns validation, geometry and numbers.

## Architecture

```text
                               CLIENT
                                 │  POST /v1/requests  {wkt, prompt}
                                 ▼
┌───────────────────────────────────────────────────────────────────┐
│ API layer · api/ · FastAPI                                    S1  │
│ thin: HTTP <-> validate_request(), typed ErrorEnvelope (422)      │
└────────────────────────────────┬──────────────────────────────────┘
                                 ▼
┌───────────────────────────────────────────────────────────────────┐
│ Request layer · request/ + geo/ · no LLM                      S1  │
│ validate WKT -> buffer Point (configurable) -> store -> area_id   │
└───────────────┬───────────────────────────────────┬───────────────┘
                │ ValidatedRequest (area_id)        │ put
                ▼                                   ▼
┌───────────────────────────────┐   ┌───────────────────────────────┐
│ Deep Agent · agent/       S2  │   │ Area store · geo/         S1  │
│ intent, plan, decide, explain │   │ in memory: area_id -> shape   │
│ sees area_id + summary and    │   │ geo ops: contains, distance,  │
│ the capability catalog only   │   │ area, representative point    │
└──┬──────────┬──────────┬──────┘   └───────────────▲───────────────┘
   │          │          │                          │
   ▼          ▼          ▼                          │ get(area_id)
┌────────┐ ┌─────────┐ ┌───────────────────┐        │
│Working │ │Tool     │ │Core tools  S2-S7  │        │
│memory  │ │filter   │ │load_capability    │────────┤
│(files) │ │S4       │ │set_intent         │        │
│S2      │ │shows    │ │rank_candidates    │        │
│        │ │only     │ │geo_describe_area  │        │
│        │ │loaded   │ │resolve_time_window│        │
│        │ │capabil- │ └───────────────────┘        │
│        │ │ity tools│                              │
└────────┘ └────┬────┘                              │
                ▼                                   │
┌───────────────────────────────┐                   │
│ Capability registry       S3  │                   │
│ registry/ · MongoDB           │                   │
│ definitions, schemas, status  │                   │
│ resolver -> handler allowlist │                   │
└───────────────┬───────────────┘                   │
                ▼                                   │
┌───────────────────────────────┐                   │
│ Capability tools      S5, S7  │                   │
│ capabilities/                 │───────────────────┘
│ places · weather · events     │   tools take area_id, never geometry
│ big results -> working memory │
└───────────────┬───────────────┘
                ▼
       External APIs / MCP servers
```

Reading the graph:

- **Two ways in:** areas enter only through the request layer, and tools reach geometry only through `area_id`.
- **Small results:** large results go to working-memory files; the model sees short summaries and the top-k rows.
- **Stage tags:** `S1`–`S8` mark which stage builds each part.

## Stage order

```text
S1 ──► S2 ──┐
  └──► S3 ──┴──► S4 ──► S5 ──► S6 ──► S7 ──► S8
```

S2 and S3 can run in parallel. Every other stage starts only after the previous gate passes.

## Stages

### Stage 1 — Foundation · done

A FastAPI endpoint over a pure-Python core with no LLM. It does the following:

- **Validation:** checks WKT in order, cheapest check first, and returns a typed error for each failure.
- **Geometry:** accepts Polygon and MultiPolygon, and turns a Point into a geodesic circle with a configurable radius.
- **Storage:** keeps areas in an in-memory LRU area store, using a content-hash `area_id`.
- **Geo operations:** contains, representative point, distance and area.

Gate: valid request + `area_id`, all tests green.

### Stage 2 — Agent core · done

`POST /v1/requests` now runs a Deep Agent on a configurable small model. The dev default is `gemma4:12b` on local Ollama. It covers:

- **Model:** a provider factory (`ollama` / `openai_compatible`), so changing model or server is config only.
- **Context budget:** every number is in config. Defaults are a 16K window and a 13,312-token input budget per call.
- **State:** a custom schema with `conversation`, `request`, `intent`, `loaded_capabilities` and `search`.
- **Conversations:** multi-turn via an optional `conversation_id`. Each conversation is bound to one area, and its state is checkpointed (in memory now, MongoDB later).
- **Harness:** the default harness is measured first, then trimmed.
- **Working memory:** a fixed file layout per conversation, with one folder per turn.
- **Token ledger:** a middleware that logs system-prompt, tool-schema and message tokens per call.
- **Evals:** eval harness v0, with single- and multi-turn cases.

The model sees `area_id` plus a compact summary, never the WKT. Area tools take no area argument; they read it from state.

Gate: token ledger live, baseline and trimmed harness cost recorded, multi-turn evals within budget.

### Stage 3 — Capability registry · next

A minimal tool registry on MongoDB, run with Docker Compose for dev and tests:

- **Definitions:** each tool is just a `source_id` (e.g. `demo.sample_points`) and a `description`. The `source_id` prefix is the capability. There are no cards, versions or status.
- **Code owns the rest:** each handler's input model, output model (its returned fields) and `uses_area` flag live in the handler's registration. The database stores definitions, never code.
- **Execution:** a resolver turns a definition into a LangChain tool through an explicit handler allowlist.
- **Area injection:** the resolver injects `area_id` from agent state; tools never take it as an argument.
- **Big results:** the resolver writes them to working-memory files; the model gets a short summary plus the path.
- **Seeding and change detection:** YAML seed files, an idempotent `geosearch-registry` CLI (`seed`, `validate`, `list`, `delete`), and a revision counter checked per request.

Gate: registry tests pass on real MongoDB; adding a tool takes a handler module and a seed entry, with no change under `agent/`.

### Stage 4 — Progressive disclosure · planned

Keeps unused tool schemas out of the model's context:

- **Catalog:** an always-on catalog in the system prompt with capability prefixes and tool descriptions only. A tool's returned fields are shown when its capability is loaded.
- **Loading:** `load_capability` adds the capability to `state.loaded_capabilities`.
- **Filtering:** a `wrap_model_call` middleware shows the model only the tools of loaded capabilities.
- **Guard:** calls to tools of unloaded capabilities are blocked.

Gate: schemas of unloaded capabilities are never sent to the model.

### Stage 5 — First capability: places · planned

One end-to-end slice:

1. Turn the prompt into an `Intent` with hard constraints and soft preferences.
2. The places tool writes its results to files.
3. `rank_candidates` (a deterministic core tool) filters to the area and the hard constraints, scores the soft preferences, and returns the top-k compact rows.
4. Fetch details for the top 3.
5. Answer, with a grounding check on every named place.

Gate: the handoff example returns 3–5 places, all inside the polygon, and the grounding check passes.

### Stage 6 — Iterative refinement · planned

When results are thin:

- **Relaxation:** a deterministic relaxation ladder drops soft preferences first, then hard semantic constraints with a visible note. The area is never relaxed.
- **Sufficiency:** a sufficiency signal says when results are good enough.
- **Stopping:** stop conditions cap iterations, tool calls and tokens.
- **Attempts log:** each try is recorded in an attempts log.

Gate: the original `area_id` is used on every tool call.

### Stage 7 — More capabilities · planned

Proves the agent is generic:

- **New capabilities:** weather and events.
- **Time:** `resolve_time_window` turns phrases like "tonight" into exact times in the area's time zone.
- **Multi-capability requests:** handled sequentially first; a capability moves to a sub-agent only if the token ledger shows it over budget.
- **Tool types:** REST and MCP handlers.

Gate: zero edits under `agent/`.

### Stage 8 — Hardening · planned

- **Reliability:** response caching, retries and rate limits.
- **Observability:** tracing.
- **Operations:** a registry admin CLI.
- **Security:** a review of handlers and MCP servers.
- **Quality:** a CI eval gate (accuracy and token growth) and a run on a second small model.
- **PostGIS:** added only if a trigger fires.

Gate: all of the above in place, and the PostGIS decision written down.
