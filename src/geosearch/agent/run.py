"""Running turns.

Two layers live here:
  - the single-turn mechanics (build the turn's state, invoke, read the answer);
  - the request orchestrator (RequestRunner) that turns a UserRequest into a
    turn: it decides new-conversation vs follow-up, enforces area binding, seeds
    working memory, and drives the ConversationRegistry.

Each finished turn is recorded in the conversation record (Stage 6, D4): after
the checkpoint, so the record never claims a turn the state lacks. If that write
was lost, the next follow-up backfills it from the checkpoint. `keep_alive` then
pushes out the expiry of everything the conversation needs (its checkpoints and
its area); it is a no-op on the memory backend.

The area store may be Stage 1's in-memory LRU, which can evict: a follow-up
restores the area from the checkpoint's full-precision WKT if needed (the
content-hash id is stable, so the same area_id comes back).
"""

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import shapely
from deepagents.backends.state import create_file_data
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph

from geosearch.agent.answer import REMINDER, AnswerSource, final_answer
from geosearch.agent.context import AgentContext
from geosearch.agent.conversations import ConversationRegistry
from geosearch.agent.holder import AgentHolder
from geosearch.agent.ledger import TokenLedger
from geosearch.clock import Clock, utc_now
from geosearch.config import GeoConfig
from geosearch.errors import ErrorCode, GeoValidationError
from geosearch.geo.area_store import AreaNotFound, AreaStore, area_to_wkt
from geosearch.geo.buffer import BufferStrategy
from geosearch.geo.ops import AreaOps
from geosearch.registry.catalog import CatalogSnapshot
from geosearch.registry.models import model_name_for
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
        "notices": [],
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
    """The fallback answer: this turn's last AI reply that has text.

    A small model may answer in plain text, get the submit reminder, and then
    end on an empty message (or a tool call with no text); the answer is the
    reply it gave before that. The search stops at the turn's own prompt (the
    reminder belongs to the turn), so an earlier turn's reply is never reused."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and message.content != REMINDER:
            break
        if isinstance(message, AIMessage):
            text = message.text if hasattr(message, "text") else str(message.content)
            if text.strip():
                return text
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
    # Never the default "async": each step's checkpoint write then runs on the
    # run's thread pool and waits for the previous one, and a turn with enough
    # steps can fill the pool and deadlock (langgraph 1.2.12). "exit" (the
    # default) checkpoints once when the run ends, which also keeps a MongoDB
    # thread small; "sync" writes inline after every step. Not without a thread:
    # 1.2.12's "sync" then waits on a write it never made.
    durability = context.cfg.conversation.checkpoint_durability if thread_id else None
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


def turn_entry(outcome: TurnOutcome, started_at: datetime, finished_at: datetime) -> dict:
    """The readable record of a finished turn: no state, no provider ids, no rows."""
    usage = outcome.usage
    return {
        "request_id": outcome.request_id,
        "prompt": outcome.state["request"]["prompt"],
        "answer": outcome.answer,
        "item_ids": [item.id for item in outcome.items],
        "answer_source": outcome.answer_source,
        "stopped_reason": outcome.stopped_reason,
        "tokens": {
            "model_calls": usage.model_calls,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "summarizer_calls": usage.summarizer_calls,
            "summarizer_input_tokens": usage.summarizer_input_tokens,
        },
        "started_at": started_at,
        "finished_at": finished_at,
    }


def recovered_entry(values: dict[str, Any], now: datetime) -> dict:
    """A turn whose record write was lost, rebuilt from its checkpoint. Token
    totals lived only in that request's ledger, so they are unknown."""
    messages = values.get("messages", [])
    answer, items, source = final_answer(values, _last_ai_text(messages))
    return {
        "request_id": values["request"]["request_id"],
        "prompt": values["request"]["prompt"],
        "answer": answer,
        "item_ids": [item.id for item in items],
        "answer_source": source,
        "stopped_reason": "call_limit" if _was_call_limited(messages) else "finished",
        "tokens": None,
        "started_at": None,
        "finished_at": now,
        "recovered": True,
    }


def _no_keep_alive(conversation_id: str, area_id: str) -> None:
    return None


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
    keep_alive: Callable[[str, str], None] = _no_keep_alive  # (conversation_id, area_id)
    clock: Clock = utc_now

    def _context(self) -> AgentContext:
        return AgentContext(area_ops=self.ops, cfg=self.cfg, ledger=TokenLedger())

    def handle(self, req: UserRequest) -> TurnOutcome:
        agent, catalog = self.agents.current_with_catalog()
        if req.conversation_id is None:
            return self._new_conversation(agent, req)
        return self._follow_up(agent, catalog, req)

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

    def _run_and_record(
        self,
        agent: CompiledStateGraph,
        state: dict[str, Any],
        conversation_id: str,
        turn: int,
        area_id: str,
    ) -> TurnOutcome:
        started_at = self.clock()
        outcome = invoke_turn(agent, self._context(), state, thread_id=conversation_id)
        entry = turn_entry(outcome, started_at, self.clock())
        self.registry.record_turn(conversation_id, turn, entry)
        self.keep_alive(conversation_id, area_id)
        return outcome

    def _new_conversation(self, agent: CompiledStateGraph, req: UserRequest) -> TurnOutcome:
        validated = validate_request(req, self.cfg, self.store, self.ops, self.buffer_strategy)
        area_wkt = area_to_wkt(self.store.get(validated.area_id))
        conversation_id = self.registry.create(validated.area_id, validated.area_summary)

        with self.registry.turn(conversation_id) as (_record, turn):
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
            return self._run_and_record(agent, state, conversation_id, turn, validated.area_id)

    def _reconcile(
        self, conversation_id: str, snapshot: Any, recorded_turns: int, turn: int
    ) -> int:
        """If the checkpoint holds a finished turn the record lacks (its record
        write failed), backfill it and return the next turn number. A run that
        died mid-turn has pending nodes (`next`) and is not counted."""
        state_turn = snapshot.values["conversation"]["turn"]
        if state_turn == recorded_turns + 1 and not snapshot.next:
            self.registry.record_turn(
                conversation_id, state_turn, recovered_entry(snapshot.values, self.clock())
            )
            turn = state_turn + 1
            self.registry.check_cap(conversation_id, turn)
        return turn

    def _follow_up(
        self, agent: CompiledStateGraph, catalog: CatalogSnapshot, req: UserRequest
    ) -> TurnOutcome:
        conversation_id = req.conversation_id
        assert conversation_id is not None
        if req.wkt is None and req.point_buffer_m is not None:
            raise GeoValidationError(
                ErrorCode.BUFFER_NOT_APPLICABLE,
                "point_buffer_m is not applicable to a follow-up that sends no wkt",
            )
        check_prompt_present(req.prompt)
        check_prompt_length(req.prompt, self.cfg.limits.max_prompt_chars)

        with self.registry.turn(conversation_id) as (record, turn):
            snapshot = agent.get_state({"configurable": {"thread_id": conversation_id}})
            if "conversation" not in snapshot.values:
                # A record without state: its checkpoints expired or were lost.
                self.registry.forget(conversation_id)
                raise GeoValidationError(
                    ErrorCode.CONVERSATION_NOT_FOUND,
                    "conversation state is gone",
                    {"conversation_id": conversation_id},
                )
            stored = snapshot.values["conversation"]
            area_id = stored["area_id"]
            area_wkt = stored["area_wkt"]
            area_summary = stored["area_summary"]
            if area_id != record.area_id:  # two stores disagree: a bug, never ignored
                raise RuntimeError(
                    f"conversation {conversation_id}: record area {record.area_id} "
                    f"!= state area {area_id}"
                )
            turn = self._reconcile(conversation_id, snapshot, record.turn_count, turn)

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
            # Loaded tools that left the registry since they were loaded: unload
            # them and tell the model, so it neither expects them nor is
            # surprised that they're gone.
            gone = [i for i in snapshot.values.get("loaded_tools") or [] if i not in catalog]
            if gone:
                state["loaded_tools"] = {"drop": gone}
                state["notices"] = [
                    f"Tool {model_name_for(i)} is no longer available and was unloaded."
                    for i in gone
                ]
            return self._run_and_record(agent, state, conversation_id, turn, area_id)
