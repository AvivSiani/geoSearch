"""Running turns.

Two layers live here:
  - the single-turn mechanics (build the turn's state, invoke, read the answer);
  - the request orchestrator (RequestRunner) that turns a UserRequest into a
    turn: it decides new-conversation vs follow-up, enforces area binding, seeds
    working memory, and drives the ConversationRegistry.

The area store is Stage 1's in-memory LRU. Because it can evict, a follow-up
re-puts the conversation's stored WKT before the turn: the content-hash id is
stable, so the same area_id comes back (Stage 2 §10).
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import shapely
from deepagents.backends.state import create_file_data
from langchain_core.messages import AIMessage
from langgraph.graph.state import CompiledStateGraph

from geosearch.agent.answer import AnswerSource, final_answer
from geosearch.agent.context import AgentContext
from geosearch.agent.conversations import ConversationRegistry
from geosearch.agent.holder import AgentHolder
from geosearch.agent.ledger import TokenLedger
from geosearch.config import GeoConfig
from geosearch.errors import ErrorCode, GeoValidationError
from geosearch.geo.area_store import AreaNotFound, AreaStore, area_to_wkt
from geosearch.geo.buffer import BufferStrategy
from geosearch.geo.ops import AreaOps
from geosearch.request.checks import check_prompt_length, check_prompt_present
from geosearch.request.language import detect_language
from geosearch.request.models import ResponseItem, UsageSummary, UserRequest
from geosearch.request.validate import validate_request

StoppedReason = Literal["finished", "call_limit"]

# The ModelCallLimitMiddleware (exit_behavior="end") ends a run by appending an
# AIMessage with this prefix. Pinned to langchain==1.4.3; re-verify on upgrade.
_CALL_LIMIT_PREFIX = "Model call limits exceeded"


@dataclass
class TurnOutcome:
    conversation_id: str
    turn: int
    request_id: str
    area_summary: str
    answer: str
    stopped_reason: StoppedReason
    usage: UsageSummary
    state: dict[str, Any]  # final graph state, for conversations/debugging
    items: list[ResponseItem] = field(default_factory=list)
    answer_source: AnswerSource = "fallback"


def build_new_turn_state(
    *,
    conversation_id: str,
    area_id: str,
    area_wkt: str,
    area_summary: str,
    request_id: str,
    prompt: str,
    turn: int,
) -> dict[str, Any]:
    """The state update for a turn: the user's prompt plus fresh `request` and
    `search`. `conversation` carries the fixed area; `intent` and
    `loaded_tools` are left for the checkpointer to persist across turns. The
    language is per turn: a follow-up may switch between English and Hebrew."""
    return {
        "messages": [{"role": "user", "content": prompt}],
        "conversation": {
            "conversation_id": conversation_id,
            "area_id": area_id,
            "area_wkt": area_wkt,
            "area_summary": area_summary,
            "turn": turn,
        },
        "request": {
            "request_id": request_id,
            "prompt": prompt,
            "language": detect_language(prompt),
        },
        "search": {"iteration": 0, "candidate_count": 0, "status": "idle"},
        "answer": None,
        "reminded": False,
        "files": _seed_files(area_summary, turn, request_id, prompt),
    }


def _seed_files(area_summary: str, turn: int, request_id: str, prompt: str) -> dict[str, Any]:
    """Working-memory seeds (Stage 2 §10). File entries merge across turns, so
    re-writing /conversation.json each turn just refreshes the turn count."""
    return {
        "/conversation.json": create_file_data(
            json.dumps({"area_summary": area_summary, "turn": turn})
        ),
        f"/turns/{turn}/request.json": create_file_data(
            json.dumps({"request_id": request_id, "prompt": prompt})
        ),
    }


def _last_ai_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message.text if hasattr(message, "text") else str(message.content)
    return ""


def _was_call_limited(messages: list[Any]) -> bool:
    if not messages:
        return False
    last = messages[-1]
    return isinstance(last, AIMessage) and str(last.content).startswith(_CALL_LIMIT_PREFIX)


def invoke_turn(
    agent: CompiledStateGraph,
    context: AgentContext,
    state_update: dict[str, Any],
    *,
    thread_id: str | None = None,
) -> TurnOutcome:
    """Invoke the agent for one turn and package the result.

    A fresh AgentContext is passed per turn (never persisted, never shown to the
    model); its ledger is summarized into the outcome. thread_id routes
    checkpointing for conversations.
    """
    config = {"configurable": {"thread_id": thread_id}} if thread_id else {}
    # durability="sync" when checkpointing: with the default "async", each step's
    # checkpoint write runs on the run's thread pool and waits for the previous
    # write; a turn with enough steps (each model call passes several middleware
    # nodes) can fill the pool with waiting writes and deadlock (seen with
    # langgraph 1.2.12). Writing inline costs ~nothing for the in-memory saver.
    # Not without a thread: 1.2.12's "sync" then waits on a write it never made.
    durability = "sync" if thread_id else None
    result = agent.invoke(state_update, context=context, config=config, durability=durability)

    messages = result.get("messages", [])
    conversation = result["conversation"]
    request = result["request"]
    answer, items, source = final_answer(result, _last_ai_text(messages))
    return TurnOutcome(
        conversation_id=conversation["conversation_id"],
        turn=conversation["turn"],
        request_id=request["request_id"],
        area_summary=conversation["area_summary"],
        answer=answer,
        stopped_reason="call_limit" if _was_call_limited(messages) else "finished",
        usage=context.ledger.summary(),
        state=result,
        items=items,
        answer_source=source,
    )


@dataclass
class RequestRunner:
    """Turns a UserRequest into one agent turn. Built once (app startup) and
    reused; holds the agent holder, the conversation registry and the Stage 1
    geo deps. Each request takes the current agent once (which may rebuild it
    after a registry change) and keeps it for the whole turn."""

    cfg: GeoConfig
    agents: AgentHolder
    registry: ConversationRegistry
    store: AreaStore
    ops: AreaOps
    buffer_strategy: BufferStrategy

    def _context(self) -> AgentContext:
        return AgentContext(area_ops=self.ops, cfg=self.cfg, ledger=TokenLedger())

    def handle(self, req: UserRequest) -> TurnOutcome:
        agent = self.agents.current()
        if req.conversation_id is None:
            return self._new_conversation(agent, req)
        return self._follow_up(agent, req)

    def _restore_area(self, area_id: str, area_wkt: str) -> None:
        """Make sure the conversation's area is in the store. A persistent store
        reads it back; an in-memory one may have evicted it, so it is re-put from
        the state's full-precision WKT, which hashes to the same area_id. A
        different id would mean the area changed, which must never pass silently."""
        try:
            self.store.get(area_id)
            return
        except AreaNotFound:
            pass
        restored = self.store.put(shapely.from_wkt(area_wkt))
        if restored != area_id:
            raise AreaNotFound(f"{area_id} restored as {restored}")

    def _new_conversation(self, agent: CompiledStateGraph, req: UserRequest) -> TurnOutcome:
        validated = validate_request(req, self.cfg, self.store, self.ops, self.buffer_strategy)
        area_wkt = area_to_wkt(self.store.get(validated.area_id))
        conversation_id = self.registry.create(validated.area_id)

        with self.registry.turn(conversation_id) as (_entry, turn):
            state = build_new_turn_state(
                conversation_id=conversation_id,
                area_id=validated.area_id,
                area_wkt=area_wkt,
                area_summary=validated.area_summary,
                request_id=validated.request_id,
                prompt=validated.prompt,
                turn=turn,
            )
            # The only turn that seeds the carry-across fields; later turns inherit
            # them from the checkpointer.
            state["intent"] = None
            state["loaded_tools"] = []
            state["items"] = {}
            return invoke_turn(agent, self._context(), state, thread_id=conversation_id)

    def _follow_up(self, agent: CompiledStateGraph, req: UserRequest) -> TurnOutcome:
        conversation_id = req.conversation_id
        assert conversation_id is not None
        if req.wkt is None and req.point_buffer_m is not None:
            raise GeoValidationError(
                ErrorCode.BUFFER_NOT_APPLICABLE,
                "point_buffer_m is not applicable to a follow-up that sends no wkt",
            )
        check_prompt_present(req.prompt)
        check_prompt_length(req.prompt, self.cfg.limits.max_prompt_chars)

        with self.registry.turn(conversation_id) as (entry, turn):
            stored = agent.get_state(
                {"configurable": {"thread_id": conversation_id}}
            ).values["conversation"]
            area_id = stored["area_id"]
            area_wkt = stored["area_wkt"]
            area_summary = stored["area_summary"]

            # Area binding: a re-sent WKT must resolve to the same area_id.
            if req.wkt is not None:
                probe = validate_request(
                    UserRequest(
                        wkt=req.wkt, prompt=req.prompt, point_buffer_m=req.point_buffer_m
                    ),
                    self.cfg,
                    self.store,
                    self.ops,
                    self.buffer_strategy,
                )
                if probe.area_id != area_id:
                    raise GeoValidationError(
                        ErrorCode.AREA_MISMATCH,
                        "a follow-up cannot change the conversation's area",
                        {"expected_area_id": area_id, "got_area_id": probe.area_id},
                    )

            self._restore_area(area_id, area_wkt)

            state = build_new_turn_state(
                conversation_id=conversation_id,
                area_id=area_id,
                area_wkt=area_wkt,
                area_summary=area_summary,
                request_id=uuid.uuid4().hex,
                prompt=req.prompt.strip(),
                turn=turn,
            )
            return invoke_turn(agent, self._context(), state, thread_id=conversation_id)
