"""The agent's state schema: the project-wide contract later stages read.

Why one shared schema rather than per-middleware state: these fields are how
later stages' tools and middleware communicate (intent in Stage 5, loaded
capabilities in Stage 4, search progress in Stage 6). Keeping them in one place
makes the contract explicit. A field only one middleware needs can still live
in that middleware; these are the shared ones.

Everything here is checkpointed with the conversation, so it must stay compact.
Crucially, area geometry is NOT here: the model-visible state carries only an
`area_id` and a short summary (Stage 2 invariant 1 — the model never sees WKT).
The one WKT field is server-side only and never placed in a message.
"""

from typing import TypedDict

from deepagents import DeepAgentState


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


class SearchProgress(TypedDict):
    """Capability-search progress. Reset each turn; "idle" until Stage 6 uses it."""

    iteration: int
    candidate_count: int
    status: str


class GeoAgentState(DeepAgentState):
    """DeepAgentState (messages, files, todos, ...) plus our shared fields.

    `conversation` and `loaded_capabilities` and `intent` carry across turns via
    the checkpointer; `request` and `search` are overwritten each turn.
    """

    conversation: ConversationRef
    request: RequestRef
    intent: dict | None  # Stage 5 fills it; carries across turns
    loaded_capabilities: list[str]  # Stage 4 fills it; carries across turns
    search: SearchProgress
