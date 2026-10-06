"""Finishing a turn (Stage 5): end on submit, remind once, else fall back.

  - Once `submit_answer` stored an answer, the turn ends before the next model
    call (a before_model jump), so the model can't keep talking past it.
  - If the model replies without tool calls and nothing was submitted, it gets
    one reminder and one more call (an after_model jump back to the model).
  - If it still doesn't submit (or the call limit stops the run), the system
    falls back: the last reply, with citations limited to known items.

Either way the response's items are built here from `state.items` — data the
tools returned — never from model text. The provider ref stays server-side.
"""

from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage

from geosearch.agent.citations import cited_ids, strip_invalid_ids
from geosearch.request.models import ResponseItem

AnswerSource = Literal["submitted", "fallback"]

REMINDER = (
    "Finish now by calling submit_answer with text=... and item_ids=[...] "
    "(ids from tool results, or [] if none)."
)


class SubmitAnswerMiddleware(AgentMiddleware):
    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if state.get("answer"):
            return {"jump_to": "end"}
        return None

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None
        if state.get("answer") or state.get("reminded"):
            return None
        return {"messages": [HumanMessage(REMINDER)], "reminded": True, "jump_to": "model"}


def build_items(item_ids: list[str], items: dict[str, Any]) -> list[ResponseItem]:
    """Response items, in the answer's order, from stored data only."""
    out = []
    for item_id in item_ids:
        record = items.get(item_id)
        if record is None:
            continue
        row = dict(record["row"])
        out.append(
            ResponseItem(
                id=item_id,
                source_id=record["source_id"],
                name=row.pop("name", None),
                lon=row.pop("lon", None),
                lat=row.pop("lat", None),
                data={k: v for k, v in row.items() if v is not None},
            )
        )
    return out


def final_answer(
    state: dict[str, Any], last_text: str
) -> tuple[str, list[ResponseItem], AnswerSource]:
    """The turn's answer text, items and where they came from."""
    items = state.get("items") or {}
    submitted = state.get("answer")
    if submitted:
        return submitted["text"], build_items(submitted["item_ids"], items), "submitted"
    text = strip_invalid_ids(last_text, set(items))
    return text, build_items(cited_ids(text), items), "fallback"
