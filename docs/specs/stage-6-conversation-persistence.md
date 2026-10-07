# Stage 6 — Conversation persistence

Conversations, their checkpoints and their areas live in MongoDB, so a
conversation survives an API restart and its history can be read back. The
overview is in `SPECS.md`, and the project invariants in `CLAUDE.md` still apply.
This file is the detailed plan: what the code does today, the open decisions,
and the order of the steps.

Gate:

- a multi-turn conversation continues after a simulated restart, with the same
  area, the same loaded tools, and earlier turns used as context;
- new conversation documents always contain `user_id: null`;
- all multi-turn evals pass with the `mongodb` backend;
- the full test suite is green.

## 1. What the code does today

| Part | Where | Today |
| --- | --- | --- |
| Checkpointer | `agent/conversations.py: make_checkpointer` | `InMemorySaver` when `conversation.store == "memory"`; `"mongodb"` is accepted by config and raises `NotImplementedError`. One saver is shared by every agent the `AgentHolder` builds, so a rebuild keeps conversations. |
| Conversation metadata | `ConversationRegistry` (same module) | In-process dict: `area_id`, `created_at`, `last_used`, completed `turn` count, a `threading.Lock`. Enforces `CONVERSATION_BUSY` (non-blocking lock), `CONVERSATION_LIMIT` (`max_turns`) and idle expiry (`idle_ttl_minutes`, checked lazily on access). A failed turn advances neither the count nor `last_used`. |
| Turn flow | `agent/run.py: RequestRunner` | New: `validate_request` → `store.put` → `registry.create` → `invoke_turn`. Follow-up: `registry.turn` → `agent.get_state(thread)` reads `conversation` (`area_id`, `area_wkt`, `area_summary`) → optional `AREA_MISMATCH` probe → `store.put(from_wkt(area_wkt))` (re-put in case the LRU evicted it) → `invoke_turn`. `invoke_turn` uses `durability="sync"`. |
| Area store | `geo/area_store.py` | `InMemoryAreaStore`: bounded LRU of normalized, prepared shapes, keyed by the content-hash `area_id`. `AreaStore` is already a protocol (`put`, `get`). |
| Working memory | deepagents `StateBackend` | Files are the `files` channel of the agent state, so they are checkpointed with it. Layout: `/conversation.json`, `/turns/<t>/request.json`, `/turns/<t>/results/<source_id>/<k>.json`. |
| Registry MongoDB | `registry/mongo.py`, `registry/startup.py` | `connect(cfg.mongo)` (selection timeout from config), `ping`, database `mongo.database` (default `geosearch`). Collections `tools`, `registry_meta`. Unreachable at startup fails fast when `registry.required`. |
| Docker Compose | `docker-compose.yml` | One `mongo:8.0` with a named volume. Nothing to add for Stage 6. |
| Evals | `evals/run.py: _run_case` | Builds its own `InMemorySaver` and agent and calls `invoke_turn` directly; it does not go through `RequestRunner`. |

### 1.1 Measured (real model, handoff prompt, then one follow-up)

| | after turn 1 | after turn 2 |
| --- | --- | --- |
| checkpoints written (cumulative) | 33 | 90 |
| serialized state (msgpack) | 10.3 KB | 22.0 KB |
| of which `messages` / `files` / `items` | 5.4 / 2.3 / 1.6 KB | 13.2 / 5.3 / 3.0 KB |
| files | 3 | 8 |

About 30–60 checkpoints per turn (every middleware hook is a graph step), and
the state grows by roughly 10 KB per turn.

### 1.2 Bug found: the area round-trip through WKT is lossy

`run.py` stores `area_wkt = shapely.to_wkt(geom)`, which rounds to **6** decimals
by default, while `make_area_id` hashes at **7**. A follow-up then re-puts
`from_wkt(area_wkt)`. For an area with a 7th-decimal coordinate this gives a
**different** `area_id` (checked: `area_b3d8b51aac08` ≠ `area_8d78cb30dde6`).
Today it rarely matters, because the original shape is usually still in the LRU.
After a restart the LRU is empty, so it would break invariant 1. It is fixed in
step 1 (§4).

## 2. Decisions

### D1 — Checkpointer: `langgraph-checkpoint-mongodb` vs. our own

Compatibility, checked against PyPI and the package source:

- The latest version is `langgraph-checkpoint-mongodb==0.5.0` (2026-09-04). It requires
  `langgraph-checkpoint>=3.0.0`; we have 4.2.0, so that part is fine.
- It requires **`pymongo>=4.9,<4.18`**. We pin `pymongo==4.18.2`, so
  `uv add` fails as unsatisfiable. It resolves only after lowering pymongo to
  4.17.0.
- It also brings in `langchain-mongodb` (vector-search tooling), which pulls
  in `langchain-classic`, `langchain-text-splitters`, `numpy`, `lark` and
  `pymongo-search-utils`. We use none of them.
- `MongoDBSaver.put` writes a new document on every step and never deletes old
  ones. That is about 90 documents after two turns (§1.1).
- TTL is `created_at` + `expireAfterSeconds` on each document. Its index helper
  compares a list with a tuple, so it calls `create_index` on every start; once
  the TTL value changes, that call should raise `IndexOptionsConflict`. This is from
  reading the code and was not confirmed, since we didn't take this option.

**Found in step 3: delta channels.** deepagents 0.7.21 declares `messages` and
`files` as LangGraph `DeltaChannel`s (`snapshot_frequency=50`). A checkpoint
stores only a marker for them, and their values are rebuilt by walking the
**parent chain** and its pending writes back to the last snapshot. So:

- A "latest checkpoint only" saver (the original design of C) would silently
  rebuild messages and files as **empty**. Any saver must keep the chain.
- Storage is linear, not quadratic. Measured with a full-history saver on the two
  real turns of §1.1, it is about 230 KB per turn (33 + 57 checkpoints of about
  5–6 KB, plus about 10 KB of writes).
- With `durability="exit"`, LangGraph writes 1–2 checkpoints per turn instead of
  about 45. On the same two turns, messages (25), files (8), items and loaded tools
  all reload correctly. That is about 10 KB per turn. It also means a turn that dies
  mid-way leaves no half-written steps behind.

| Option | Pros | Cons |
| --- | --- | --- |
| A. `MongoDBSaver` as is | Maintained upstream; the full `BaseCheckpointSaver` contract. | pymongo pin lowered; ~7 unused transitive dependencies incl. numpy; TTL counts from each doc's creation, so a live conversation's old ancestors expire first and cut the delta chain; possible index conflict when retention changes. |
| B. `MongoDBSaver` + prune after each turn | Upstream contract. | Same pin and dependency cost; a delta-safe prune must force a snapshot through a private type (`_DeltaSnapshot`); same TTL caveats. |
| C. Our own `MongoCheckpointSaver`, full history | No dependency or pin change; `expires_at` TTL like our other collections, refreshed for the whole thread every turn, so the chain never breaks in a live conversation; about 150 lines. | We own the `BaseCheckpointSaver` contract and must re-verify it on LangGraph upgrades. |

**Decision (revised in step 3): C with full history, plus `durability="exit"`.**

- Durability is set in config: `conversation.checkpoint_durability`, default
  `exit`, or `sync` as before. Only `invoke_turn` reads it. `exit` also avoids
  the Stage 5 thread-pool deadlock, because there are no per-step writes to chain.
- The parity suite runs the same scripted multi-turn conversations on
  `InMemorySaver` and on ours, and they must give equal `get_state()`. It
  covers tool calls, parallel `load_tools`, the submit reminder, a failing turn,
  and more than 50 steps so a delta snapshot is crossed.

Design of C:

- `checkpoints`: one document per checkpoint, unique on (thread_id,
  checkpoint_ns, checkpoint_id). It holds `parent_checkpoint_id`, the serialized
  checkpoint (`serde.dumps_typed`, BSON binary), the metadata and `expires_at`.
- `checkpoint_writes`: one document per (thread, ns, checkpoint_id, task_id,
  idx). It uses `$set` vs `$setOnInsert` with `WRITES_IDX_MAP`, as upstream does.
- `touch(thread_id, expires_at)` refreshes `expires_at` on every document of a
  thread. It is called once per finished turn.
- `get_next_version` is the same as `InMemorySaver`'s. `list` supports
  `filter`/`before`/`limit`. `get_delta_channel_history` is the base class's
  (a parent walk through `get_tuple`).

### D2 — Working memory

The files live **in agent state** (`files` channel, `StateBackend`), so the
checkpoint already persists them, and a resumed conversation sees every earlier
turn's files with no extra work.

| Option | Pros | Cons |
| --- | --- | --- |
| A. Keep files in state (checkpointed) | No code change; atomic with the rest of the state; a resume is exact. | Files count toward the 16 MB checkpoint document. |
| B. Move files to a store (deepagents `StoreBackend` or our own collection) | No size pressure on the checkpoint. | Two writes per step that must stay consistent; a new backend in `agent/` (against "no agent changes"); nothing needs it at today's sizes. |

16 MB check: the limit applies to each document. A checkpoint holds the
non-delta channels (about 5–6 KB measured), plus the full messages and files
every 50 steps when a delta snapshot lands, which is at most the whole state:
about 10 KB per turn (§1.1). Even a full `max_results=20` Google page per turn,
about 5× the fixture rows, gives roughly 1 MB at the `max_turns=20` cap. That is
more than 10× below the limit.

**Recommendation: A.** Add a cheap guard: the saver checks each document it
writes and logs a warning above `conversation.checkpoint_warn_bytes`
(default 4 MB). It only warns and never truncates, which would change the
conversation.

### D3 — Collections, indexes, database, retention

All four collections go in the **same database as the registry** (`mongo.database`),
with names in config:

| Collection | `_id` | Other fields | Indexes |
| --- | --- | --- | --- |
| `checkpoints` | `{thread_id, checkpoint_ns}` | checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata, expires_at | TTL `expires_at` |
| `checkpoint_writes` | ObjectId | thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, channel, type, value, expires_at | unique (thread_id, checkpoint_ns, checkpoint_id, task_id, idx); TTL `expires_at` |
| `conversations` | conversation_id | user_id, area_id, area_summary, created_at, updated_at, expires_at, turn_count, turns[] | TTL `expires_at` |
| `areas` | area_id | wkb, geometry_type, created_at, expires_at | TTL `expires_at` |

- **Database:** reusing `mongo.database` means one client and one Compose service.
  Tests already get a throwaway database per test, and evals already use
  `geosearch_eval`, so dropping it cleans up everything. A separate database would
  only add a setting.
- **TTL style:** every TTL index is `expires_at` with `expireAfterSeconds=0`, and
  code sets `expires_at = now + retention` on each write. Changing the retention
  then never requires an index rebuild (see D1's `IndexOptionsConflict`), and
  the conversation, its checkpoint and its area all expire on the same clock.
- **Areas are shared** (content hash), so an area must outlive the conversations
  using it. Each turn bumps it with `$max: {expires_at}`. The TTL monitor
  runs about every 60 s, so code also treats `expires_at < now` as expired on read.
  Expiry is then exact and testable with an injected clock.
- **Area format:** store WKB of the normalized shape, which is exact, never WKT
  (§1.2). The WKT is never returned by any endpoint.

Retention:

| Option | Pros | Cons |
| --- | --- | --- |
| A. Reuse `conversation.idle_ttl_minutes` for both backends, keep 60 min | One knob, one meaning. | An hour is too short for a durable store; "survives a restart" would barely hold. |
| B. Same knob, default raised to 7 days (10,080 min) | One knob and one meaning ("expires this long after its last turn"); a useful durable default. | The in-memory backend also keeps entries longer (it is for tests and dev only, and expiry is checked on access there anyway). |
| C. Separate `retention_days` for mongodb | Each backend gets its own default. | Two knobs with the same meaning. |

**Recommendation: B.**

`user_id` index:

| Option | Pros | Cons |
| --- | --- | --- |
| Index now | Ready for per-user queries. | An index nothing reads costs a write on every turn and suggests a feature that doesn't exist. |
| Index when something queries by user | No dead index; it will be a one-line `create_index` (partial, `user_id` not null) in the index setup. | Must be remembered when ownership lands (noted in the code and here). |

**Recommendation: wait.** `user_id` is always written as `null`, is never accepted from
the API, and has no logic around it.

### D4 — Turn record: when written, and keeping it consistent with the checkpoint

The checkpoint is written during `invoke` (many steps). The turn record is the readable
history. These are two separate writes, and MongoDB transactions need a replica set
(Compose runs a standalone server).

| Option | Pros | Cons |
| --- | --- | --- |
| A. Checkpoint first, then append the record; reconcile on the next resume | Simple; the record never claims a turn the state lacks; a lost record write is repaired from the checkpoint. | A short window where the history lags the state. |
| B. Write a `running` entry before invoke, complete it after | History shows in-flight turns. | Crash leftovers need a lease/sweeper; a failed turn would need cleanup to keep "failed turns don't count". |
| C. Transaction | Atomic. | Needs a replica set; the checkpoint writes happen inside LangGraph, not in our session. |

**Recommendation: A.**

- After `invoke_turn` returns, `$push` the entry and `$set` `turn_count`,
  `updated_at` and `expires_at`, with the filter `{_id, turn_count: n-1}`.
- A failed turn writes no entry. That matches today's rule that a failed turn doesn't count.
- If the record write fails, the request returns 503 `STORE_UNAVAILABLE`. On
  the next resume, if the checkpoint shows a **finished** run for turn
  `turn_count+1` (`get_state().next == ()` and `conversation.turn ==
  turn_count+1`), the entry is backfilled from the state:
  - prompt from `request`;
  - answer and item ids from `answer`, or from the fallback;
  - `tokens: null`;
  - `recovered: true`.

  A run that died mid-turn has a non-empty `next` and is not counted, as today.

Turn entry: `{turn, request_id, prompt, answer, item_ids, answer_source,
stopped_reason, tokens: {model_calls, input_tokens, output_tokens,
summarizer_calls, summarizer_input_tokens}, started_at, finished_at}`. That is about 4 KB
per entry, so `max_turns=20` is far from 16 MB.

Concurrency: the busy lock stays in-process, and the deployment model is a single API
process. The conditional `turn_count` filter detects a second writer, but
multi-process locking is out of scope (Stage 7).

### D5 — Restart test

| Option | Pros | Cons |
| --- | --- | --- |
| A. New `create_app` on the same MongoDB database, in-process | Fast; deterministic with the scripted model; every in-memory piece (LRU, lock table, agent, checkpointer instance) is rebuilt from scratch. | Not a real OS process. |
| B. Real subprocess (uvicorn), killed and restarted | Closest to production. | Slow and flaky in pytest, and it can't inject the scripted model without a test hook in `api/main.py`. |

**Recommendation: A for pytest, plus a real restart in the evals.**

- **pytest:** app 1 runs turn 1 (loads tools, finds items) and is closed, including
  its Mongo clients. App 2 is created with a fresh scripted model and an empty LRU,
  and runs turn 2. Assertions:
  - same `area_id`, read through from `areas`;
  - `loaded_tools` carried over;
  - app 2's first model call contains turn 1's messages;
  - `GET` lists both turns with `user_id: null`.

  A variant removes a tool from the registry between the two apps (§3.5).
- **evals:** a `--restart` flag rebuilds everything between turns, from the
  persistence factory down to the agent, on the real model.
- **by hand:** `scripts/ask.py --conversation-id <id>` resumes a conversation from a
  new process.

## 3. Components

### 3.1 Config (`ConversationConfig`)

- `store: "memory" | "mongodb"` already exists; the default stays `memory`.
- `idle_ttl_minutes` gets a default of 10,080 (D3).
- New collection names: `checkpoints_collection`, `checkpoint_writes_collection`,
  `conversations_collection`, `areas_collection`.
- New `checkpoint_warn_bytes` and `checkpoint_durability` (`exit` | `sync`, default `exit`; D1).
- The stale docstrings ("a mongodb store is not planned") are fixed.
- Mongo connection settings stay in `MongoConfig`.

### 3.2 Persistence factory (`agent/persistence.py`)

`open_persistence(cfg) -> Persistence(checkpointer, conversations, area_store, close)`
is the only place that picks a backend. `create_app` calls it instead of
`make_checkpointer` + `InMemoryAreaStore`.

With `mongodb`:

- connect with `registry/mongo.connect`, then ping;
- if MongoDB is unreachable, raise at startup (`PersistenceUnavailable`), with no
  memory fallback;
- otherwise ensure the indexes and build the Mongo implementations.

### 3.3 Area store (`geo/area_store.py`)

`MongoAreaStore(collection, cache: InMemoryAreaStore, clock, retention)`:

- **`put`** hashes the area. If the id is cached, it returns. Otherwise it upserts
  `$setOnInsert {wkb, geometry_type, created_at}` with `$max {expires_at}`, then caches.
- **`get`** reads the LRU first, then MongoDB (WKB → normalized, prepared). It
  raises `AreaNotFound` when the area is missing or expired.
- **`touch(area_id)`** bumps `expires_at`; it is called once per turn.

`geo/` keeps its rule of no FastAPI and no LLM; pymongo is allowed. `AreaOps` is
unchanged.

### 3.4 Conversation store and registry (`agent/conversations.py`)

`ConversationStore` protocol: `create(area_id, area_summary) -> id`, `get(id)`,
`append_turn(id, entry, expected_count)`, `backfill(...)`, `delete(id)`.

- `InMemoryConversationStore` keeps today's behavior and gains turn entries, so GET
  works on both backends.
- `MongoConversationStore` writes `user_id: None` on create, always.
- `ConversationRegistry` keeps the in-process lock table and delegates metadata to the
  store. `CONVERSATION_NOT_FOUND` covers unknown, expired (`expires_at < now`) and a
  record whose checkpoint is gone. The last case deletes the record.

### 3.5 Resume checks (`agent/run.py`)

On every follow-up:

- the record exists and has not expired;
- the area binding matches (today's `AREA_MISMATCH` probe);
- the area is read through `store.get(area_id)`, with no WKT round-trip (§1.2);
- `loaded_tools` is checked against the current catalog snapshot.

Ids that are no longer in the registry are dropped:

- the reducer accepts a `{"drop": [ids]}` update (plain dict, msgpack-safe);
- a per-turn `notices: list[str]` field, reset each turn, carries
  `"Tool source_9001 is no longer available and was unloaded."`;
- `CatalogMiddleware` appends notices after the catalog block, only when they are
  non-empty, so the normal prompt stays byte-identical.

This is the one small change under `agent/` that touches what the model sees. It is
required by the stage, and it changes nothing when no tool was removed.

### 3.6 API

- `GET /v1/conversations/{conversation_id}` returns `ConversationHistory {conversation_id,
  user_id, area_id, area_summary, created_at, updated_at, expires_at, turns[]}`.
  It never includes WKT, provider ids or raw rows.
- `ErrorCode.STORE_UNAVAILABLE` returns 503.
- The route maps `pymongo.errors.PyMongoError` to `STORE_UNAVAILABLE`, separately
  from `MODEL_UNAVAILABLE` (pymongo's `ConnectionFailure` is not a builtin
  `ConnectionError`, which step 2 checks).
- An unknown or expired id returns 404 `CONVERSATION_NOT_FOUND`, as today.

### 3.7 Evals and the terminal script

- `_run_case` takes its checkpointer and area store from `open_persistence(cfg)`.
- `--store mongodb` uses the `geosearch_eval` database, which is dropped afterwards.
- `--restart` reopens the persistence and rebuilds the agent between turns.
- `scripts/ask.py --conversation-id`.

## 4. Steps

Each step is one reviewed commit (`stage6: step N — …`), with `uv run pytest` and
`uv run ruff check .` green.

| Step | Content | Tests |
| --- | --- | --- |
| 0 | This spec. | — |
| 1 | Fix §1.2: full-precision WKT in state (`rounding_precision=-1`), follow-up reads the area through the store instead of re-putting WKT. | a 7-decimal polygon keeps its `area_id` across a follow-up after the LRU is cleared |
| 2 | Config (§3.1); `open_persistence` with the `memory` backend only; `STORE_UNAVAILABLE` + route mapping. | config defaults/validation; 503 envelope on a pymongo error; memory backend unchanged |
| 3 | `MongoCheckpointSaver` (full history) + indexes + `touch` + `checkpoint_warn_bytes`; `conversation.checkpoint_durability` (default `exit`). | parity suite vs `InMemorySaver` (incl. a delta snapshot crossing); `list` filters; `touch`; `delete_thread`; `expires_at` set; size warning |
| 4 | `MongoAreaStore` (write-through, read-through, WKB, `touch`). | idempotent put; read-through after a fresh cache; exact geometry; expired → `AreaNotFound` |
| 5 | `ConversationStore` (memory + Mongo), registry delegation, turn records, reconcile, expiry; `mongodb` wired in `open_persistence` (fails fast when down). | `user_id: null`; entries after success only; backfill after a failed record write; expired → 404; startup fails when Mongo is down |
| 6 | Resume checks (§3.5) + `GET /v1/conversations/{id}`. | dropped tool + notice; prompt unchanged without notices; GET shape on both backends; 404 |
| 7 | Restart gate test (D5); eval `--store mongodb --restart`; `ask.py --conversation-id`; run the stage2/4/5 evals on `mongodb` and write the report. | §5 |
| 8 | Docs: Stage 6 invariants and verified APIs in `CLAUDE.md`, README config table, `SPECS.md` Stage 6 done + deviations. | — |

## 5. Gate checks

- **pytest (scripted model, real MongoDB):** the D5 restart test, which checks the
  same area, the same loaded tools, earlier turns in context, and GET with
  `user_id: null`. Also the dropped-tool variant, and the 404 and 503 cases.
- **Evals (gemma4:12b, replay fixtures):** `stage2`, `stage4` and `stage5`, each with
  `--store mongodb --restart`. The multi-turn cases pass at the same rate as on
  `memory` (the baseline is run in the same session).
- **Full suite:** `uv run pytest` and `uv run ruff check .`.

## 6. Out of scope

Per-user ownership and queries (only the `user_id: null` placeholder), multi-process
turn locking, checkpoint history and time travel, moving working memory out of state,
and compressing or summarizing stored history.

## 7. As built: deviations from this plan

1. **D1 was revised in step 3** (see D1, "Found in step 3"). The saver keeps the
   full checkpoint chain, not just the latest checkpoint, because `messages` and
   `files` are DeltaChannels. Turns now default to `durability="exit"`
   (`conversation.checkpoint_durability`), which is also the new default for the
   `memory` backend. Within a turn the agent behaves exactly as before; only how
   often state is checkpointed changes.
2. **`keep_alive`** (from `open_persistence`) refreshes the expiry of the
   conversation's checkpoints and area after each recorded turn. `RequestRunner`
   gains `keep_alive` and `clock`, both with defaults, so existing callers are
   unchanged.
3. **Area restore (step 1):** a follow-up reads the area through the store, and
   re-puts it from the full-precision WKT only if the store lost it (the
   in-memory LRU). It raises if the restored shape hashes to a different
   `area_id`.
4. **`AgentHolder.current_with_catalog()`** returns the agent together with the
   catalog it was built from. The resume check needs the two to match.
5. **Under `agent/`** besides persistence: `CatalogMiddleware` appends the
   turn's `notices` (none in the normal case, so the prompt is byte-identical),
   `GeoAgentState` gains `notices`, and the `loaded_tools` reducer accepts
   `{"drop": [...]}`.
6. **A conflicting record write** (another writer recorded the turn first) is
   409 `CONVERSATION_BUSY`, the same code as a concurrent turn.
7. **The eval harness now runs turns through `RequestRunner`**, as the app does,
   instead of calling `invoke_turn` itself. That is what makes `--store mongodb`
   exercise records, areas and resume checks. `--restart` rebuilds everything
   between turns.
8. **`scripts/ask.py`** prints the `conversation_id` and resumes with
   `--conversation-id`.

## 8. Gate results

- **pytest:** 489 passed, all on real MongoDB where needed (`docker compose up -d`):
  - the D5 restart test, in two variants (`tests/test_stage6_gate.py`);
  - the saver parity suite, in both durability modes, including a delta-snapshot
    crossing;
  - the record, expiry, backfill, fail-fast, history and resume tests.
- **Evals** (`gemma4:12b`, replay fixtures, trimmed harness,
  `--store mongodb --restart`, 3 runs per case):

  | Suite | Case | Turns | Pass |
  | --- | --- | --- | --- |
  | stage4 | random_points, points_and_weather, area_size_loads_nothing | 1 | 100% |
  | stage4 | follow_up_no_reload | 2 | 100% |
  | stage5 | handoff_en, handoff_he | 1 | 100% |
  | stage5 | details_follow_up | 2 | 100% |
  | stage2 | missing_capability | 1 | 100% |
  | stage2 | long_conversation | many | 100% |
  | stage2 | area_size, area_where, area_override, greeting | 1 | 0% |
  | stage2 | follow_up_units | 2 | 0% |

  The stage2 failures are all `answer_non_empty`. The model answers in plain
  text, gets the one `submit_answer` reminder, then ends on an empty message, and
  the Stage 5 fallback takes the *last* AI text, which is empty. The same prompt
  gives the same result on committed `HEAD` (before Stage 6), and the
  `memory`-store baseline (1 run per case) shows the same pattern case for case.
  The stage2 suite had not been re-run since Stage 5. Fixing it means changing
  the fallback (for example, the last *non-empty* AI text), which is agent
  behavior and out of scope here. It is left as a follow-up.
- Reports: `evals/reports/20261007T134958Z-stage2.md`,
  `20261007T135611Z-stage4.md`, `20261007T140339Z-stage5.md` (mongodb, restart),
  and `20261007T141349Z-stage2.md` (memory baseline).

### 8.1 Follow-up: the empty fallback

Two separate causes produced the empty stage2 answers:

1. **Fixed, in code.** The model answered in plain text, got the reminder, and
   ended on an empty message, and the fallback took that empty last message.
   `_last_ai_text` now returns the turn's last AI reply that *has text*. It stops
   at the turn's own prompt, so an earlier turn's reply is never reused. This
   fixed `greeting` and `area_override`.
2. **Not fixed: the model and server return nothing.** In `area_size`,
   `area_where` and `follow_up_units`, gemma4:12b writes no text at all after
   `geo_describe_area`. Ollama 0.35.1 reports 55–100 generated tokens, but the
   message has no content, no tool calls and no thinking, so there is nothing to
   fall back on.
   - Replaying the captured request without tools and with `think=true` gives a
     normal thinking block plus an answer.
   - With `think=false`, the same request comes back empty. This points to a
     reasoning block that Ollama strips, after which the model stops without
     answering.
   - It is not the tool-call parser: `submit_answer` calls with `°`, `²` and
     `[i1]` in the text parse fine.
   - `llm.thinking=true` (config only) fixed `area_where` but not `area_size`.

   That leaves a decision about the model, server or prompt, outside Stage 6.

stage2 on `--store mongodb --restart` after fix 1 (1 run per case):
missing_capability, area_override, greeting and long_conversation pass;
area_size, area_where and follow_up_units fail
(`evals/reports/20261007T145739Z-stage2.md`).
