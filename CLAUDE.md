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

## Conventions

- Python 3.12, managed with `uv`. Run `uv run pytest` and `uv run ruff check .`
  before considering any step done. The eval harness runs separately against the
  real model: `uv run python -m evals.run --suite stage2`.
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
