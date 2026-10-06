"""The summarizer: tool rows -> a short, cited summary for the user's question.

Why it exists (Stage 5): the main agent must never see raw rows. A places
search returns a dozen rows of names, addresses and ratings; pasting them into
the main context is exactly the cost this project is built to avoid. So every
registry result with rows is summarized first, in a separate, isolated context:
its own short message list (question, tool, rows) and no tools, no history.

It is invoked by code (`SummarizerMiddleware`), never chosen by the model — a
deepagents `task` sub-agent would let the model skip it (Stage 5, D6).

Large results are split into chunks that each fit `summarizer.chunk_tokens`,
summarized one by one (map), then merged (reduce). Every model output passes
`strip_invalid_ids` against the ids actually given, so a mangled or invented
citation never survives. Results with no rows (a small `data` reading) pass
through untouched (D7).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.types import Command

from geosearch.agent.citations import strip_invalid_ids
from geosearch.agent.ledger import LedgerRecord, TokenLedger
from geosearch.config import GeoConfig
from geosearch.registry.catalog import CatalogSnapshot

LANGUAGE_NAMES = {"en": "English", "he": "Hebrew"}
_CHARS_PER_TOKEN = 4  # the hard cap on output length, in characters per token

MAP_PROMPT = """\
You summarize one tool result for an assistant that answers the user's question.
- Use only the rows given. Keep what matters for the question; skip the rest.
- Cite every item you mention by its id in brackets, like [i3]. Only use ids from the rows.
- Never invent places, ids or facts.
- At most {lines} short lines. Write in {language}."""

MERGE_PROMPT = """\
Merge partial summaries of one tool result into one summary for the user's question.
- Keep the [id] citations exactly as written. Never invent ids or facts.
- Drop repeats; keep what matters for the question.
- At most {lines} short lines. Write in {language}."""


def render_row(row: dict[str, Any]) -> str:
    """`[i3] name=Lotus; rating=4.6` for an item, `- lon=…; lat=…` otherwise."""
    fields = "; ".join(f"{k}={v}" for k, v in row.items() if k != "id")
    return f"[{row['id']}] {fields}" if "id" in row else f"- {fields}"


def _tokens(text: str) -> int:
    return count_tokens_approximately([HumanMessage(text)])


def chunk_lines(
    lines: list[str], chunk_tokens: int, max_chunks: int
) -> tuple[list[list[int]], int]:
    """Pack line indices, in order, into chunks of at most `chunk_tokens` (a
    single oversized line gets a chunk of its own). Returns the chunks and how
    many lines were left out beyond `max_chunks`."""
    chunks: list[list[int]] = []
    size = 0
    for i, line in enumerate(lines):
        cost = _tokens(line)
        if not chunks or size + cost > chunk_tokens:
            if len(chunks) == max_chunks:
                return chunks, len(lines) - i
            chunks.append([])
            size = 0
        chunks[-1].append(i)
        size += cost
    return chunks, 0


@dataclass
class Summary:
    text: str
    calls: int
    omitted: int  # rows beyond max_chunks, left out


class ResultSummarizer:
    """Gets a BaseChatModel (the main agent's, by default); never imports a
    provider (invariant 11)."""

    def __init__(self, model: BaseChatModel, cfg: GeoConfig):
        self.model = model
        self.cfg = cfg.summarizer
        self.budget = cfg.budget.effective_input_budget
        self.max_chars = self.cfg.max_output_tokens * _CHARS_PER_TOKEN
        self.lines = max(3, self.cfg.max_output_tokens // 40)

    def _call(self, messages: list[BaseMessage], ledger: TokenLedger | None) -> str:
        start = time.perf_counter()
        reply = self.model.invoke(messages)
        if ledger is not None:
            ledger.add_summarizer(_record(messages, reply, start, self.budget, ledger))
        text = reply.text if isinstance(reply, AIMessage) else str(reply.content)
        return text.strip()[: self.max_chars]

    def summarize(
        self,
        *,
        question: str,
        language: str,
        tool: str,
        headline: str,
        rows: list[dict[str, Any]],
        ledger: TokenLedger | None = None,
    ) -> Summary:
        name = LANGUAGE_NAMES.get(language, "English")
        lines = [render_row(r) for r in rows]
        chunks, omitted = chunk_lines(lines, self.cfg.chunk_tokens, self.cfg.max_chunks)
        valid_all = {str(r["id"]) for r in rows if "id" in r}

        partials = []
        prompt = MAP_PROMPT.format(lines=self.lines, language=name)
        for chunk in chunks:
            valid = {str(rows[i]["id"]) for i in chunk if "id" in rows[i]}
            body = "\n".join(lines[i] for i in chunk)
            human = f"Question: {question}\nTool: {tool}\nResult: {headline}\nRows:\n{body}"
            text = self._call([SystemMessage(prompt), HumanMessage(human)], ledger)
            partials.append(strip_invalid_ids(text, valid))

        if len(partials) == 1:
            text = partials[0]
        else:
            prompt = MERGE_PROMPT.format(lines=self.lines, language=name)
            human = f"Question: {question}\nPartial summaries:\n\n" + "\n\n".join(partials)
            text = strip_invalid_ids(
                self._call([SystemMessage(prompt), HumanMessage(human)], ledger), valid_all
            )
        calls = len(chunks) + (1 if len(chunks) > 1 else 0)
        return Summary(text=text, calls=calls, omitted=omitted)


def _record(
    messages: list[BaseMessage], reply: Any, start: float, budget: int, ledger: TokenLedger
) -> LedgerRecord:
    est_system = count_tokens_approximately(messages[:1])
    est_message = count_tokens_approximately(messages[1:])
    usage = getattr(reply, "usage_metadata", None) or {}
    reported_in = usage.get("input_tokens")
    est_total = est_system + est_message
    return LedgerRecord(
        call_index=len(ledger.summarizer_records),
        est_system_tokens=est_system,
        est_tool_tokens=0,
        est_message_tokens=est_message,
        est_total=est_total,
        reported_input_tokens=reported_in,
        reported_output_tokens=usage.get("output_tokens"),
        tools_offered=[],
        latency_ms=(time.perf_counter() - start) * 1000.0,
        over_budget=max(est_total, reported_in or 0) > budget,
        possible_truncation=False,
        role="summarizer",
    )


# --- middleware -------------------------------------------------------------------


def _summarized(message: ToolMessage, text: str) -> ToolMessage:
    content = f"{message.content}\n{text}" if text else str(message.content)
    return ToolMessage(
        content=content,
        tool_call_id=message.tool_call_id,
        name=message.name,
        status=message.status,
        id=message.id,
    )  # no artifact: the rows end here, never checkpointed in the history


class SummarizerMiddleware(AgentMiddleware):
    """Replaces a registry tool's rows (carried on ToolMessage.artifact) with
    the summarizer's cited summary. Results without rows pass unchanged.

    If the summarizer fails, the message keeps the handler's headline plus the
    item ids and names (no other fields), so the agent can still cite them."""

    def __init__(self, summarizer: ResultSummarizer, snapshot: CatalogSnapshot):
        super().__init__()
        self.summarizer = summarizer
        self.descriptions = {e.source_id: e.description for e in snapshot.tools}

    def _rewrite(self, message: Any, request: ToolCallRequest) -> Any:
        artifact = getattr(message, "artifact", None)
        if not (isinstance(message, ToolMessage) and isinstance(artifact, dict)):
            return message
        rows = artifact.get("rows") or []
        if not rows:
            return _summarized(message, "")
        state = request.state or {}
        req = state.get("request") or {}
        try:
            summary = self.summarizer.summarize(
                question=req.get("prompt", ""),
                language=req.get("language", "en"),
                tool=self.descriptions.get(artifact.get("source_id"), message.name or ""),
                headline=str(message.content),
                rows=rows,
                ledger=getattr(request.runtime.context, "ledger", None),
            )
        except Exception:
            ids = [f"[{r['id']}] {r.get('name', '')}".strip() for r in rows if "id" in r]
            fallback = "Summary unavailable." + (" Items: " + "; ".join(ids) if ids else "")
            return _summarized(message, fallback)
        text = summary.text
        if summary.omitted:
            text += f"\n({summary.omitted} more result(s) not summarized.)"
        return _summarized(message, text)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        result = handler(request)
        if isinstance(result, Command) and isinstance(result.update, dict):
            messages = result.update.get("messages")
            if messages:
                update = {
                    **result.update,
                    "messages": [self._rewrite(m, request) for m in messages],
                }
                return Command(
                    graph=result.graph, update=update, resume=result.resume, goto=result.goto
                )
            return result
        return self._rewrite(result, request)
