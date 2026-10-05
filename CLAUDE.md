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

## Conventions

- Python 3.12, managed with `uv`. Run `uv run pytest` and `uv run ruff check .`
  before considering any step done.
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
