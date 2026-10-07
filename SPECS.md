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
                                 │  POST /v1/requests  {wkt, prompt, conversation_id?}
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
                │ ValidatedRequest                  │ put
                │ (area_id, conversation_id)        │
                ▼                                   │
┌───────────────────────────────┐                   │
│ Conversation store        S6  │                   │
│ MongoDB: checkpoints + turns  │                   │
│ load before turn, save after  │                   │
│ keyed by conversation_id      │                   │
└───────────────┬───────────────┘                   │
                ▼                                   ▼
┌───────────────────────────────┐   ┌───────────────────────────────┐
│ Deep Agent · agent/       S2  │   │ Area store · geo/     S1, S6  │
│ intent, plan, decide, explain │   │ MongoDB + LRU: area_id->shape │
│ sees area_id + summary and    │   │ geo ops: contains, distance,  │
│ the capability catalog only   │   │ area, representative point    │
└──┬──────────┬──────────┬──────┘   └───────────────▲───────────────┘
   │          │          │                          │
   ▼          ▼          ▼                          │ get(area_id)
┌────────┐ ┌─────────┐ ┌───────────────────┐        │
│Working │ │Tool     │ │Core tools  S2-S5  │        │
│memory  │ │filter   │ │load_capability    │────────┤
│(files) │ │S4       │ │set_intent         │        │
│S2      │ │shows    │ │rank_candidates    │        │
│        │ │only     │ │geo_describe_area  │        │
│        │ │loaded   │ │                   │        │
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
│ Capability tools          S5  │                   │
│ capabilities/                 │───────────────────┘
│ places (Google, demo)         │   tools take area_id, never geometry
│ big results -> working memory │
└───────────────┬───────────────┘
                ▼
       External APIs / MCP servers
```

Reading the graph:

- **Two ways in:** areas enter only through the request layer, and tools reach geometry only through `area_id`.
- **Small results:** large results go to working-memory files; the model sees short summaries and the top-k rows.
- **Durable state:** conversations, their checkpoints and their areas live in MongoDB, so a conversation survives a process restart.
- **Stage tags:** `S1`–`S7` mark which stage builds each part.

## Stage order

```text
S1 ──► S2 ──┐
  └──► S3 ──┴──► S4 ──► S5 ──► S6 ──► S7
```

S2 and S3 can run in parallel. Every other stage starts only after the previous gate passes.
Iterative refinement and more capabilities are deferred (see [Deferred](#deferred)).

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
- **Conversations:** multi-turn via an optional `conversation_id`. Each conversation is bound to one area, and its state is checkpointed (in memory here; MongoDB in Stage 6).
- **Harness:** the default harness is measured first, then trimmed.
- **Working memory:** a fixed file layout per conversation, with one folder per turn.
- **Token ledger:** a middleware that logs system-prompt, tool-schema and message tokens per call.
- **Evals:** eval harness v0, with single- and multi-turn cases.

The model sees `area_id` plus a compact summary, never the WKT. Area tools take no area argument; they read it from state.

Gate: token ledger live, baseline and trimmed harness cost recorded, multi-turn evals within budget.

### Stage 3 — Capability registry · done

A minimal tool registry on MongoDB, run with Docker Compose for dev and tests:

- **Definitions:** each tool is just a numeric `source_id` and a `description`. There are no cards, versions or status.
- **Code owns the rest:** each handler's registration declares the input model, the output model (its returned fields) and the `uses_area` flag. There is no grouping; the model sees each tool as `source_<id>`. The database stores definitions, never code.
- **Execution:** a resolver turns a definition into a LangChain tool through an explicit handler allowlist.
- **Area injection:** the resolver injects `area_id` from agent state; tools never take it as an argument.
- **Big results:** the resolver writes them to working-memory files; the model gets a short summary plus the path.
- **Seeding and change detection:** YAML seed files, an idempotent `geosearch-registry` CLI (`seed`, `validate`, `list`, `delete`), and a revision counter checked per request.

Gate: registry tests pass on real MongoDB; adding a tool takes a handler module and a seed entry, with no change under `agent/`.

### Stage 4 — Progressive disclosure · done

Keeps unused tool schemas out of the model's context:

- **Catalog:** an always-on catalog in the system prompt with `id: description` lines only. Tools are not grouped.
- **Selection:** the agent decides from the descriptions which tools the request needs, and calls `load_tools([ids])`. The result lists each loaded tool's returned fields.
- **Carry-over:** loaded tools carry across turns. There is no count cap by default (it is configurable).
- **Filtering:** a `wrap_model_call` middleware sends only the core tools plus the loaded tools.
- **Guard:** calls to unloaded tools are blocked with a "load first" message.
- **Registry changes:** every catalog tool is registered at build time, and the agent is rebuilt between requests when the registry revision changes.

Gate: schemas of unloaded tools are never sent to the model (checked from the token ledger).

### Stage 5 — First capability: places · done

One end-to-end slice, in English and Hebrew:

1. **Load:** the agent loads the places tools from the catalog: Google Places search (`9101`) and details (`9102`).
   - Google is a demo provider behind a configurable `PlacesProvider`.
   - Tests and evals replay recorded fixtures.
2. **Intent:** `set_intent` records hard constraints and soft preferences, on fields the loaded tools return.
3. **Search:** results are written to a file.
4. **Rank:** `rank_candidates` is a generic, deterministic core tool. It:
   - drops rows outside the polygon;
   - applies the hard constraints;
   - scores the soft preferences, using a review-count-adjusted rating;
   - returns the top-k compact rows plus a sufficiency signal.
5. **Details:** the model fetches details for its top picks.
6. **Answer:** `submit_answer(ids, text)` is the grounding step ("multiple choice"). Ids are checked against the ranking, and the response items are built from data. If the model never submits, it gets one reminder; after that the system falls back to the top 3.

> **As built:** candidate ranking was dropped for now (tools may return rows without scores). Instead, a sub-agent summarizes each tool's results for the user's question, and the main agent combines those summaries.

Gate: the handoff prompt returns 3–5 grounded items, all inside the polygon, in both English and Hebrew.

### Stage 6 — Conversation persistence · done

Conversations and their history are stored in MongoDB, so a conversation survives an API restart and its history can be read back:

- **Checkpointer:** the in-memory checkpointer is replaced by a MongoDB-backed one, on the same Docker Compose MongoDB as the registry. The backend is chosen in config (`memory` | `mongodb`); `memory` stays available for fast unit tests.
- **Conversation record:** a `conversations` collection with one document per conversation: `conversation_id`, bound `area_id`, timestamps, and one entry per turn (prompt, answer text, answer item ids, token totals). Checkpoints hold the full agent state for resuming; this record is the readable history.
- **Areas:** a conversation is bound to an area, so the area must outlive a restart too. The area store writes each area to an `areas` collection on `put` (idempotent, because `area_id` is a content hash). The in-memory LRU stays as a read-through cache.
- **Working memory:** working-memory files must be available when a conversation resumes. Where they live (inside agent state, so the checkpoint persists them, or outside it) is decided and recorded in the stage spec, keeping MongoDB's 16 MB document limit in mind.
- **Resume checks:** on each turn the conversation must exist and its area binding must match (the existing rule). Loaded tools that no longer exist in the registry are dropped from state, with a note to the model.
- **Retention:** conversations and their checkpoints expire after a configurable time (TTL index).
- **Read API:** `GET /v1/conversations/{conversation_id}` returns the turn history from the conversation record.
- **Errors:** an unknown or expired `conversation_id` returns a typed 404 `ErrorEnvelope`. If MongoDB is unreachable, the API returns a typed 503 and never falls back to memory silently.

Gate: a multi-turn conversation continues correctly after an API process restart (same area, same loaded tools, earlier turns used as context); all multi-turn evals pass with the `mongodb` backend; persistence tests run on real MongoDB.

> **As built:** the checkpointer is our own `MongoCheckpointSaver`, because `langgraph-checkpoint-mongodb` needs pymongo<4.18. It keeps the full checkpoint chain, since deepagents' `messages` and `files` are DeltaChannels. Turns are checkpointed once, at exit (`conversation.checkpoint_durability`), about 1–2 checkpoints per turn. Working memory stays in agent state. The default retention is 7 days. `user_id` is always `null`.
>
> **Gate:** met except for one stage2 eval case.
> - The scripted restart test (`tests/test_stage6_gate.py`) passes on real MongoDB.
> - With `--store mongodb --restart` on `gemma4:12b` (3 runs per case), stage4 and stage5 pass 100%, including their multi-turn cases `follow_up_no_reload` and `details_follow_up`, and so does stage2's `long_conversation`.
> - **Not met:** stage2's `follow_up_units` still fails, as do the single-turn `area_size` and `area_where`. The fallback now skips an empty closing message, which fixed `greeting` and `area_override`. In the remaining cases, gemma4 on Ollama 0.35.1 writes no text at all after `geo_describe_area`: tokens are generated but nothing comes back. This also happens on committed `HEAD` and with the `memory` store, so it is not caused by persistence.
>
> Details and deviations: `docs/specs/stage-6-conversation-persistence.md` §7–8.

### Stage 7 — Hardening · next

- **Reliability:** response caching, retries and rate limits.
- **Observability:** tracing.
- **Operations:** a registry admin CLI.
- **Security:** a review of handlers and MCP servers.
- **Quality:** a CI eval gate (accuracy and token growth) and a run on a second small model.
- **PostGIS:** added only if a trigger fires.

Gate: all of the above in place, and the PostGIS decision written down.

## Deferred

Not planned for now. Each item comes back only when evidence shows a need.

- **Iterative refinement** (previously Stage 6): relaxation ladder, sufficiency signal, stop conditions, attempts log.
  Most of it was built on `rank_candidates`, which was dropped.
  Revisit if evals show thin results (fewer than 3 grounded items) or runaway loops.
- **More capabilities** (previously Stage 7): weather, events, `resolve_time_window`, MCP handlers.
  Its gate, "zero edits under `agent/`", is the proof that the agent is generic.
  Revisit when a second capability is needed or that claim needs proving.
