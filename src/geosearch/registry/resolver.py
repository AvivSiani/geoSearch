"""Definition -> LangChain tool, only through the handler allowlist.

The resolved tool is the one place where registry data, handler code and agent
runtime meet, so it carries the guarantees:
  - The model sees `source_<id>`, `td.description` and a compacted schema built
    from the handler's input model — nothing else. The runtime is injected via
    the `runtime` parameter name, which ToolNode fills and the schema never shows.
  - The area comes from agent state, never from arguments (invariant 2).
  - Arguments are validated here: a dict `args_schema` makes LangChain pass
    them through unchecked, which is what lets us return a short, model-readable
    error instead of an exception.
  - The handler's output is checked against its declared `output_model`, so a
    declared field cannot silently disappear and an undeclared one cannot leak.
  - Inline results stay under `max_inline_result_chars` (invariant 3); bulk
    results go to working-memory files under /turns/<turn>/results/<source_id>/.
  - Stage 5: rows outside the area are dropped for every tool whose rows carry
    lon/lat; rows with a provider `id` become conversation items with short ids
    (`i3`); bulk rows ride on the ToolMessage's `artifact` (never sent to the
    model) to the summarizer, and the message no longer names the file.
"""

import json
import logging
import threading
from collections import OrderedDict
from typing import Any

from deepagents.backends.state import create_file_data
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, ValidationError

from geosearch.geo.ops import AreaOps
from geosearch.registry.handlers import (
    HandlerContext,
    HandlerRegistry,
    RegisteredHandler,
    ToolResult,
    handlers,
)
from geosearch.registry.models import ToolDefinition

log = logging.getLogger(__name__)

TRUNCATED = "…(truncated)"
ITEM_PREFIX = "i"  # short item ids: i1, i2, …
_KEEP_KEYS = (
    "type", "description", "enum", "const", "default",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "minItems", "maxItems",
)  # fmt: skip
_MAX_ERRORS_SHOWN = 3


# --- schema -----------------------------------------------------------------------


def _compact(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Keep only what helps the model pick arguments; inline `$ref`s (an Enum
    field produces one) so the schema is self-contained and flat."""
    if "$ref" in node:  # the field's own keys (description, default) win
        node = {**defs[node["$ref"].rsplit("/", 1)[-1]], **node}
    out = {k: node[k] for k in _KEEP_KEYS if k in node}
    if "items" in node:
        out["items"] = _compact(node["items"], defs)
    if "anyOf" in node:
        out["anyOf"] = [_compact(option, defs) for option in node["anyOf"]]
    return out


def compact_schema(input_model: type[BaseModel]) -> dict[str, Any]:
    full = input_model.model_json_schema()
    defs = full.get("$defs", {})
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {name: _compact(p, defs) for name, p in full["properties"].items()},
    }
    if full.get("required"):
        schema["required"] = full["required"]
    return schema


# --- result shaping ---------------------------------------------------------------


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _validation_summary(exc: ValidationError) -> str:
    parts = [
        f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}"
        for err in exc.errors()[:_MAX_ERRORS_SHOWN]
    ]
    return "; ".join(parts)


def check_output(spec: RegisteredHandler, result: ToolResult) -> str | None:
    """None if the result matches the declared output model, else the reason.
    Rows (or `data`) must have exactly the declared fields: missing ones would
    silently disappear, extra ones would leak undeclared output to the model."""
    if not isinstance(result, ToolResult):
        return f"handler returned {type(result).__name__}, not ToolResult"
    if isinstance(result.artifact, list):
        rows = result.artifact
    else:
        rows = [result.data] if result.data else []  # empty data: nothing to check
    declared = set(spec.output_model.model_fields)
    for i, row in enumerate(rows):
        where = f"row {i}" if isinstance(result.artifact, list) else "data"
        if not isinstance(row, dict):
            return f"{where} is {type(row).__name__}, not an object"
        if extra := sorted(set(row) - declared):
            return f"{where} has undeclared fields {extra}"
        try:
            spec.output_model.model_validate(row)
        except ValidationError as exc:
            return f"{where}: {_validation_summary(exc)}"
    return None


def filter_to_area(
    result: ToolResult, area_id: str, area_ops: AreaOps
) -> tuple[ToolResult, int]:
    """Drop rows (and a single-row `data`) whose lon/lat fall outside the area.
    Returns the filtered result and how many rows were dropped. Only called for
    tools whose output model declares lon/lat, so every row has them."""

    def inside(row: dict[str, Any]) -> bool:
        return area_ops.contains(area_id, row["lon"], row["lat"])

    dropped = 0
    artifact = result.artifact
    if isinstance(artifact, list):
        kept = [row for row in artifact if inside(row)]
        dropped, artifact = len(artifact) - len(kept), kept
    data = result.data
    if data and not isinstance(result.artifact, list) and not inside(data):
        dropped, data = dropped + 1, {}
    return ToolResult(summary=result.summary, data=data, artifact=artifact), dropped


class _FileNumbers:
    """Next free `<k>` per conversation and results directory. State shows
    files from earlier steps, but parallel tool calls in one step all see the
    same state, so numbers handed out in-process are remembered too (bounded LRU)."""

    def __init__(self, max_dirs: int = 1_024):
        self._lock = threading.Lock()
        self._last: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._max_dirs = max_dirs

    def next(self, conversation_id: str, directory: str, files: dict[str, Any]) -> int:
        folder = f"{directory}/"
        used = [
            int(stem)
            for path in files
            if path.startswith(folder) and (stem := path[len(folder) : -len(".json")]).isdigit()
        ]
        with self._lock:
            key = (conversation_id, directory)
            k = max([0, *used, self._last.get(key, 0)]) + 1
            self._last[key] = k
            self._last.move_to_end(key)
            while len(self._last) > self._max_dirs:
                self._last.popitem(last=False)
        return k


_numbers = _FileNumbers()


class _ItemIds:
    """Short item ids (`i1`, `i2`, …) per conversation, one per provider id.

    Same reason as _FileNumbers: parallel tool calls in one step see the same
    state, so ids handed out in-process are remembered too, and two calls can
    never give one id to different places (or two ids to one place). State
    stays the source of truth across restarts of this map (bounded LRU)."""

    def __init__(self, max_conversations: int = 1_024):
        self._lock = threading.Lock()
        self._refs: OrderedDict[str, dict[str, str]] = OrderedDict()  # cid -> ref -> id
        self._max = max_conversations

    def assign(self, conversation_id: str, refs: list[str], items: dict[str, Any]) -> list[str]:
        with self._lock:
            known = {record["ref"]: item_id for item_id, record in items.items()}
            known |= self._refs.get(conversation_id, {})
            n = max((int(i[1:]) for i in known.values()), default=0)
            ids = []
            for ref in refs:
                if ref not in known:
                    n += 1
                    known[ref] = f"{ITEM_PREFIX}{n}"
                ids.append(known[ref])
            self._refs[conversation_id] = known
            self._refs.move_to_end(conversation_id)
            while len(self._refs) > self._max:
                self._refs.popitem(last=False)
        return ids

    def ref_of(self, conversation_id: str, item_id: str, items: dict[str, Any]) -> str | None:
        if item_id in items:
            return items[item_id]["ref"]
        with self._lock:
            for ref, known_id in self._refs.get(conversation_id, {}).items():
                if known_id == item_id:
                    return ref
        return None


_item_ids = _ItemIds()


def make_items(
    spec: RegisteredHandler, rows: list[dict[str, Any]], conversation_id: str, items: dict
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Short ids for `rows` (same order) and the item records to merge into state."""
    ids = _item_ids.assign(conversation_id, [row["id"] for row in rows], items)
    records = {
        item_id: {
            "source_id": spec.source_id,
            "ref": row["id"],
            "row": {k: v for k, v in row.items() if k != "id"},
        }
        for item_id, row in zip(ids, rows, strict=True)
    }
    return ids, records


def compact_rows(
    rows: list[Any], item_ids: list[str] | None
) -> list[dict[str, Any]]:
    """The rows as the summarizer sees them: no empty fields; for items, the
    short id replaces the provider id and coordinates are dropped (the response
    builds them from state). Rows without items keep their coordinates — for a
    point sampler they are the whole answer."""
    out = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            row = {"value": row}
        if item_ids is not None:
            row = {"id": item_ids[i]} | {
                k: v for k, v in row.items() if k not in ("id", "lon", "lat")
            }
        out.append({k: v for k, v in row.items() if v is not None})
    return out


def _shape(
    result: ToolResult,
    *,
    conversation_id: str,
    directory: str,
    files: dict[str, Any],
    limit: int,
    note: str = "",
) -> tuple[str, Any, dict[str, Any]]:
    """Build the inline message, the bulk value for the summarizer, and any
    files to write. The message stays within `limit` and never names a file
    (Stage 5, D3): bulk results are summarized, not read raw. Fallbacks:
    offload oversized data as the bulk value, then truncate the summary."""
    data, artifact = dict(result.data), result.artifact
    data_line = f"\ndata: {_json(data)}" if data else ""
    if artifact is None and data and len(result.summary) + len(data_line) + len(note) > limit:
        artifact, data, data_line = data, {}, ""  # auto-offload

    new_files: dict[str, Any] = {}

    def write(value: Any) -> None:
        k = _numbers.next(conversation_id, directory, {**files, **new_files})
        new_files[f"{directory}/{k}.json"] = create_file_data(_json(value))

    if artifact is not None:
        write(artifact)
    if data and len(result.summary) + len(data_line) + len(note) > limit:
        write(data)  # too big next to the bulk result: kept in working memory only
        data_line = ""

    summary, rest = result.summary, data_line + note
    if len(summary) + len(rest) > limit:
        summary = summary[: max(0, limit - len(rest) - len(TRUNCATED))] + TRUNCATED
    return (summary + rest)[:limit], artifact, new_files


# --- the tool ---------------------------------------------------------------------


def _error(name: str, runtime: ToolRuntime, text: str) -> ToolMessage:
    return ToolMessage(
        content=f"Error: {text}",
        tool_call_id=runtime.tool_call_id,
        name=name,
        status="error",
    )


def _area_note(dropped: int) -> str:
    return f"\n({dropped} result(s) outside the area were removed.)" if dropped else ""


def resolve(td: ToolDefinition, handler_registry: HandlerRegistry = handlers) -> BaseTool:
    """Raises UnknownHandler if `td.source_id` is not on the allowlist."""
    spec = handler_registry.get(td.source_id)
    schema = compact_schema(spec.input_model)
    name = spec.model_name

    def run(runtime: ToolRuntime, **kwargs: Any) -> Command | ToolMessage:
        try:
            args = spec.input_model.model_validate(kwargs)
        except ValidationError as exc:
            return _error(name, runtime, f"invalid arguments: {_validation_summary(exc)}")

        conversation = runtime.state.get("conversation") or {}
        bound_area = conversation.get("area_id")
        area_id = bound_area if spec.uses_area else None
        if spec.uses_area and not area_id:
            return _error(name, runtime, "no area is bound to this conversation")
        turn = int(conversation.get("turn", 0))
        conversation_id = str(conversation.get("conversation_id", ""))
        items = runtime.state.get("items") or {}
        cfg = runtime.context.cfg
        ctx = HandlerContext(
            area_id=area_id,
            area_ops=runtime.context.area_ops,
            cfg=cfg,
            turn=turn,
            source_id=td.source_id,
            language=(runtime.state.get("request") or {}).get("language", "en"),
            item_ref=lambda item_id: _item_ids.ref_of(conversation_id, item_id, items),
        )

        try:
            result = spec.func(args, ctx)
        except Exception as exc:
            log.exception("registry tool %s raised", td.source_id)
            return _error(name, runtime, f"{name} failed ({type(exc).__name__})")

        if problem := check_output(spec, result):
            log.error(
                "registry tool %s: output does not match its model: %s", td.source_id, problem
            )
            fields = ", ".join(spec.output_fields)
            return _error(name, runtime, f"{name} returned malformed output ({fields})")

        # Every tool, uses_area or not: a row outside the area is never shown.
        dropped = 0
        if spec.has_coordinates and bound_area:
            result, dropped = filter_to_area(result, bound_area, runtime.context.area_ops)

        update: dict[str, Any] = {}
        item_ids = None
        if spec.yields_items:
            # A single item in `data` (details) is an item too. Items are never
            # inline — the row holds the provider id — they go to the summarizer.
            rows = result.artifact if isinstance(result.artifact, list) else []
            rows = rows or ([result.data] if result.data else [])
            if rows:
                item_ids, update["items"] = make_items(spec, rows, conversation_id, items)
            result = ToolResult(summary=result.summary, artifact=rows)

        content, bulk, files = _shape(
            result,
            conversation_id=conversation_id,
            directory=f"/turns/{turn}/results/{td.source_id}",
            files=runtime.state.get("files") or {},
            limit=cfg.registry.max_inline_result_chars,
            note=_area_note(dropped),
        )
        bulk_rows = [] if bulk is None else bulk if isinstance(bulk, list) else [bulk]
        # LangChain never sends a ToolMessage's artifact to the model: it carries
        # the rows to the SummarizerMiddleware, which clears it.
        artifact = (
            {"source_id": td.source_id, "rows": compact_rows(bulk_rows, item_ids)}
            if bulk_rows
            else None
        )
        message = ToolMessage(
            content=content, tool_call_id=runtime.tool_call_id, name=name, artifact=artifact
        )
        update["messages"] = [message]
        if files:
            update["files"] = files
        return Command(update=update)

    return StructuredTool(
        name=name,
        description=td.description,
        args_schema=schema,
        func=run,
    )
