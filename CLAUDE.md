# GeoSearch Agent

A generic, spatially grounded agent. Every request is a WKT geometry (the
geographic area) plus a natural-language prompt (what the user wants).

Responsibility split, which guides every design choice:

> The application owns the geographic context. The agent owns intent and
> decisions. Deterministic code owns validation, geometry and numbers.

## Invariants (do not violate these)

1. **WKT is immutable geographic context.** Never silently change the user's
   area: no repairing invalid geometry, no expanding, no simplifying.
2. **The only allowed geographic transform is explicit and app-level:**
   buffering a Point into a circle with a configured radius, reported back in
   the response.
3. **After validation, areas are referenced only by `area_id`.** Geo
   operations take an `area_id`, never raw geometry.
4. **`area_id` is created by the server only.** No endpoint or model accepts
   an `area_id` from a client.
5. **`request/` and `geo/` import neither FastAPI nor any LLM library.**
   `api/` is a thin translation layer.
6. **Stage 1 has no LLM, agent, MongoDB or network calls.**

### Stage 2 invariants (agent)

7. **The model never sees WKT.** Not in the system prompt, not in messages, not
   in tool results. `GeoAgentState.conversation.area_wkt` is server-side only.
8. **The model never chooses the area.** Area tools take **no** area argument;
   they read `area_id` from state (`geo_describe_area`).
9. **One conversation, one area.** A follow-up cannot change the area; a re-sent
   WKT that resolves to a different `area_id` is `AREA_MISMATCH`.
10. **All model and budget numbers live in config.** Changing model, provider or
    window is a config change (`GEOSEARCH_LLM__*`, `GEOSEARCH_BUDGET__*`), never
    a code change.
11. **`agent/model.py` is the only module that imports a provider class.**
    Everything else receives a `BaseChatModel`.
12. **Tests never need a GPU or a running model** — except `scripts/smoke_model.py`
    and the eval harness. Everything in `pytest` uses `tests/scripted_model.py`.

### Stage 3 invariants (registry)

13. **Allowlist.** A registry definition (`source_id: int` + `description`, nothing
    else) runs only if its `source_id` has a handler registered in code
    (`registry/handlers.py`), one handler per `source_id`. Every stored
    `source_id` must have a handler: seeding refuses unknown ones, and startup
    fails on them (`registry.strict_startup`, else they are logged and skipped).
14. **No area inputs.** Handler input models never contain `area_id`, `wkt` or
    `geometry` (nor `runtime`, the injection name); registration rejects them.
    The resolver injects `area_id` from agent state for `uses_area=True` handlers.
15. **Result size.** A registry tool's inline result is at most
    `registry.max_inline_result_chars`; artifacts and oversized `data` go to
    working-memory files under `/turns/<turn>/results/<source_id>/<k>.json`.
16. **No MongoDB in old tests.** Stage 1–2 tests never need MongoDB: they pass
    an `InMemoryRegistry` to `create_app(..., tool_registry=...)`. MongoDB
    tests use the `mongo_db` fixture and skip when the server is down.
17. **No grouping.** Tools have no names, prefixes or capabilities: a numeric
    `source_id` and a description. The model-facing name is generated
    (`source_<id>`). The agent selects tools from their descriptions alone, so
    every description must be clear and distinct from the others.

### Stage 4 invariants (progressive disclosure)

18. **Schemas only while loaded.** A registry tool's schema is sent to the model
    only while its id is in `loaded_tools` (DisclosureMiddleware filters
    `request.tools`; the ledger's `est_registry_tool_tokens` proves it).
19. **Runs only while loaded.** A call to a registry tool that isn't loaded
    returns "source_<id> is not loaded. Call load_tools([<id>]) first." and runs
    nothing (DisclosureMiddleware.wrap_tool_call).
20. **Catalog shows `id: description` only.** Returned fields appear only in the
    `load_tools` result, never in the always-on catalog.
21. **Adding a tool never touches `agent/`.** A handler plus a seed entry; the
    running app rebuilds its agent on the next request after the registry
    revision changes (AgentHolder), never mid-request.

## Verified library APIs (Stage 2, pinned versions)

Pinned: `deepagents==0.7.21`, `langchain==1.4.3`, `langgraph==1.2.12`,
`langchain-ollama==1.1.0`, `langchain-openai==1.6.7`. Facts confirmed against
these; re-verify on upgrade:

- **ChatOllama** thinking mode is the `reasoning` param (not `thinking`/`think`);
  it fills `usage_metadata`. `num_ctx` carries our window (confirmed via `/api/ps`
  `context_length`).
- **ToolRuntime** imports from `langchain.tools` as `ToolRuntime[Context, State]`;
  a `runtime`-only tool exposes an empty `properties` schema.
- **Middleware**: append to the system prompt with
  `request.override(system_message=SystemMessage(...))` in `wrap_model_call`;
  user middleware whose `.name` matches a base-stack one *replaces it in place*
  (so we swap `FilesystemMiddleware`/`SummarizationMiddleware`). Call-limit
  middleware is `ModelCallLimitMiddleware(run_limit=..., exit_behavior="end")`; on
  the cap it appends an AIMessage starting `"Model call limits exceeded"` — the
  only signal of a call-limit stop (`run_model_call_count` is not surfaced).
- **deepagents default tools**: `ls, read_file, write_file, edit_file, glob, grep,
  delete, task`. There is **no `write_todos`** and **no `execute`** (execute needs
  a sandbox backend). Harness trimming via `HarnessProfile.excluded_tools` is keyed
  by `provider:model`, so to stay model-agnostic we use a `FilesystemMiddleware`
  allowlist plus our own `ToolAllowlistMiddleware`.
- **StateBackend files** live under the `"files"` state key as a path→`FileData`
  dict; build entries with `deepagents.backends.state.create_file_data`. File
  updates merge across turns.
- **Prompt-cache caveat (§11): did NOT reproduce** on Ollama 0.35.1 — a repeated
  prompt reported identical `input_tokens`. The ledger's truncation check is still
  conservative (warn-only, first call of the turn) in case other servers differ.

## Verified library APIs (Stage 3)

Pinned: `pymongo==4.18.2`. Confirmed against it and the Stage 2 pins:

- **Runtime-built tools**: `StructuredTool(name=..., description=..., args_schema=<dict>,
  func=...)` exposes the dict schema unchanged (`convert_to_openai_tool`), but
  passes arguments through **unvalidated** — the resolver validates them with the
  handler's input model itself.
- **ToolRuntime injection** in a runtime-built tool: `ToolNode` reads the
  function's signature, so a parameter named `runtime` is injected even with a
  dict `args_schema`, and never appears in the schema.
- **Command + ToolMessage**: a tool may return
  `Command(update={"messages": [ToolMessage(..., tool_call_id=runtime.tool_call_id)],
  "files": {...}})`; the call id comes from `runtime.tool_call_id`.
- **PyMongo**: `find_one_and_update(filter, update, upsert=True,
  return_document=ReturnDocument.AFTER)`. Idempotent upsert = `update_one({_id, description: {$ne: d}}, ..., upsert=True)`;
  a `DuplicateKeyError` there means "unchanged".

## Verified library APIs (Stage 4)

Confirmed against the Stage 2 pins:

- **wrap_model_call**: `request.override(tools=[...])` replaces the tools sent;
  `request.override(system_message=SystemMessage(...))` appends to the prompt.
- **wrap_tool_call(request: ToolCallRequest, handler)**: `ToolCallRequest`
  (`langchain.agents.middleware.types`) has `tool_call`, `tool`, `state`,
  `runtime`. Returning a `ToolMessage` without calling `handler` skips the tool.
- **Custom state from a tool**: return `Command(update={"loaded_tools": [...],
  "messages": [ToolMessage(..., tool_call_id=runtime.tool_call_id)]})`. The field
  needs a reducer (`Annotated[list[int], merge_loaded_tools]`) or two parallel
  writes in one step raise `InvalidUpdateError`.
- **Middleware order in `create_deep_agent`**: deepagents' base stack
  `[Filesystem, SubAgent, Summarization, PatchToolCalls]`, then ours in list
  order, then its tail `[AnthropicPromptCaching, UnsupportedContent]`. Same-named
  middleware replace base ones *in place*, so our summarization stays outside
  the disclosure middleware (harmless: it acts on messages, not the tool list).
- **Ollama 0.35.1 + gemma4, thinking off**: a positional hint like
  `load_tools([ids])` in the prompt makes the model emit multi-id calls that
  Ollama's tool-call parser silently drops (empty reply, no tool calls). Prompts
  show named-argument syntax (`source_ids=[...]`) instead.

## How to add a tool

No change under `agent/`. Two pieces:

1. **Handler** in a module under `src/geosearch/sources/`, listed in `MODULES`
   in `sources/__init__.py` (explicit list, no auto-discovery). One handler per
   `source_id`; pick an unused positive int:

   ```python
   class FindInput(BaseModel):          # flat; no area_id/wkt/geometry/runtime
       query: str = Field(description="What to look for.")

   class PlaceRow(BaseModel):           # flat; one result row
       name: str
       lon: float
       lat: float

   @register_handler(source_id=17, input_model=FindInput,
                     output_model=PlaceRow, uses_area=True)
   def search(args: FindInput, ctx: HandlerContext) -> ToolResult:
       rows = ...                       # ctx.area_id, ctx.area_ops, ctx.cfg, ctx.turn
       return ToolResult(summary=f"Found {len(rows)} places.",
                         data={"count": len(rows)}, artifact=rows)
   ```

   If `artifact` is a list, every row must match `output_model` exactly;
   otherwise non-empty `data` must. A mismatch is an error to the model.
2. **Seed entry** in a YAML file under `registry/seeds/` (`source_id: 17`, and a
   description ≤ 200 chars that says clearly what the tool does and how it differs
   from the others — the agent chooses by description alone), then
   `uv run geosearch-registry seed`. The running app picks it up on the next
   request (revision check).

Check drift with `uv run geosearch-registry validate`; `seed --prune` removes
tools that are in no seed file; `delete <source_id>` removes one.

## Conventions

- Python 3.12, managed with `uv`. Run `uv run pytest` and `uv run ruff check .`
  before considering any step done. The eval harness runs separately against the
  real model: `uv run python -m evals.run --suite stage2` and
  `uv run python -m evals.run --suite stage4 --harness trimmed` (needs MongoDB;
  seeds and drops a `geosearch_eval` database).
- MongoDB (tool registry, Stage 3) runs via `docker compose up -d`; registry
  tests skip without it. The app is started with
  `uv run uvicorn geosearch.api.main:app`. Only `api/main.py` builds an app at
  import time; everything else imports `create_app` from `api/app.py`.
- `src/` layout. Type hints everywhere.
- Config via `pydantic-settings`, env prefix `GEOSEARCH_`, nested delimiter
  `__` (e.g. `GEOSEARCH_POINT_BUFFER__DEFAULT_RADIUS_M=1000`). Config is
  validated at startup and fails fast on invalid values.
- Default point-buffer radius is 10 m (`point_buffer.default_radius_m`),
  bounded by `min_radius_m=1.0` / `max_radius_m=5000.0`. Fully configurable
  per deployment; not a hardcoded value.
- Keep code simple, modular and readable — plain functions and small classes.
  Docstrings explain *why*, not just *what*.
- Each validation check in `request/checks.py` is its own small, independently
  testable function; `request/validate.py` only orchestrates them in order.
- Work proceeds in small, reviewed steps (see the stage spec). Do not commit
  without explicit approval for that step.
