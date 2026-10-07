"""The agent's state schema: the project-wide contract later stages read.

Why one shared schema rather than per-middleware state: these fields are how
later stages' tools and middleware communicate (intent in Stage 5, loaded
tools in Stage 4, search progress in Stage 6). Keeping them in one place
makes the contract explicit. A field only one middleware needs can still live
in that middleware; these are the shared ones.

Everything here is checkpointed with the conversation, so it must stay compact.
Crucially, area geometry is NOT here: the model-visible state carries only an
`area_id` and a short summary (Stage 2 invariant 1 — the model never sees WKT).
The one WKT field is server-side only and never placed in a message.
"""

from typing import Annotated, TypedDict

from deepagents import DeepAgentState

from geosearch.request.language import Language


def merge_loaded_tools(
    left: list[int] | None, right: list[int] | dict[str, list[int]] | None
) -> list[int]:
    """Union, keeping first-load order. A reducer rather than a plain field so
    two `load_tools` calls in the same step both land (a plain field would make
    LangGraph reject the second write), and so a turn's `[]` initial value
    never wipes what earlier turns loaded.

    The model never unloads (Stage 4). Only code does, with `{"drop": [ids]}`:
    a resumed conversation drops tools that left the registry (Stage 6). A plain
    dict, so it serializes in a checkpoint like any other write."""
    if isinstance(right, dict):
        dropped = set(right.get("drop", []))
        return [source_id for source_id in left or [] if source_id not in dropped]
    merged = list(left or [])
    for source_id in right or []:
        if source_id not in merged:
            merged.append(source_id)
    return merged


def merge_items(
    left: dict[str, dict] | None, right: dict[str, dict] | None
) -> dict[str, dict]:
    """Items accumulate across tools and turns. A later row for a known item
    (details after search) adds or refreshes fields but never blanks one: a
    None from the later tool keeps the earlier value. The first finder's
    `source_id` and the provider `ref` never change."""
    merged = dict(left or {})
    for item_id, record in (right or {}).items():
        old = merged.get(item_id)
        if old is None:
            merged[item_id] = record
            continue
        fresh = {k: v for k, v in record["row"].items() if v is not None}
        merged[item_id] = {**old, "row": {**old["row"], **fresh}}
    return merged


class ItemRecord(TypedDict):
    """One grounded item (Stage 5): what a tool returned for one provider id.
    Server-side: models see only the short id and the summarizer's text."""

    source_id: int  # the tool that first returned it
    ref: str  # the provider's id (e.g. a Google place_id); never shown to a model
    row: dict  # the row's declared fields, minus `id`


class ConversationRef(TypedDict):
    """Identity of the conversation and its single, fixed area."""

    conversation_id: str
    area_id: str
    area_wkt: str  # normalized WKT; server-side only, NEVER placed in a message
    area_summary: str
    turn: int  # 1-based


class RequestRef(TypedDict):
    """The current turn's request. Reset at the start of every turn."""

    request_id: str
    prompt: str
    language: Language  # detected from the prompt by code, never by the model


class SearchProgress(TypedDict):
    """Search progress. Reset each turn; "idle" until Stage 6 uses it."""

    iteration: int
    candidate_count: int
    status: str


class SubmittedAnswer(TypedDict):
    """What submit_answer stored: checked ids, and text whose citations are
    limited to them (Stage 5)."""

    text: str
    item_ids: list[str]


class GeoAgentState(DeepAgentState):
    """DeepAgentState (messages, files, todos, ...) plus our shared fields.

    `conversation`, `loaded_tools`, `items` and `intent` carry across turns via
    the checkpointer; `request`, `search`, `answer`, `reminded` and `notices`
    are overwritten each turn.
    """

    conversation: ConversationRef
    request: RequestRef
    intent: dict | None  # Stage 5 fills it; carries across turns
    loaded_tools: Annotated[list[int], merge_loaded_tools]  # registry ids; carries across turns
    items: Annotated[dict[str, ItemRecord], merge_items]  # short id -> item; carries across turns
    search: SearchProgress
    answer: SubmittedAnswer | None  # this turn's submit_answer; reset each turn
    reminded: bool  # this turn's submit reminder was sent; reset each turn
    notices: list[str]  # code-written notes for the model this turn (Stage 6); reset each turn
