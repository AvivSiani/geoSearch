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


def merge_loaded_tools(left: list[int] | None, right: list[int] | None) -> list[int]:
    """Union, keeping first-load order. A reducer rather than a plain field so
    two `load_tools` calls in the same step both land (a plain field would make
    LangGraph reject the second write), and so a turn's `[]` initial value
    never wipes what earlier turns loaded. There is no unloading (Stage 4)."""
    merged = list(left or [])
    for source_id in right or []:
        if source_id not in merged:
            merged.append(source_id)
    return merged


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


class GeoAgentState(DeepAgentState):
    """DeepAgentState (messages, files, todos, ...) plus our shared fields.

    `conversation`, `loaded_tools` and `intent` carry across turns via
    the checkpointer; `request` and `search` are overwritten each turn.
    """

    conversation: ConversationRef
    request: RequestRef
    intent: dict | None  # Stage 5 fills it; carries across turns
    loaded_tools: Annotated[list[int], merge_loaded_tools]  # registry ids; carries across turns
    search: SearchProgress
