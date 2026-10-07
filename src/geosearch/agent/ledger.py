"""The token ledger: one record per model call, so we can measure what the
harness actually costs on the real model.

It is a `wrap_model_call` middleware placed LAST in the stack, so it sees the
request exactly as the model receives it — after the area line is appended and
after any summarization has run.

Estimates use `count_tokens_approximately` (char-based, model-agnostic). Reported
numbers come from the response's `usage_metadata` when the provider fills it
(ChatOpenAI does). The two are kept separate on purpose: the gap between them is
itself a signal (truncation, prompt caching).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langchain_core.messages.utils import count_tokens_approximately

from geosearch.config import ContextBudgetConfig
from geosearch.request.models import UsageSummary


@dataclass
class LedgerRecord:
    """What one model call cost, estimated and (if reported) measured."""

    call_index: int
    est_system_tokens: int
    est_tool_tokens: int
    est_message_tokens: int
    est_total: int
    reported_input_tokens: int | None
    reported_output_tokens: int | None
    tools_offered: list[str]
    latency_ms: float
    over_budget: bool
    possible_truncation: bool
    est_catalog_tokens: int = 0  # the catalog block, part of est_system_tokens
    catalog_over_warn: bool = False  # catalog block > disclosure.catalog_warn_tokens
    est_registry_tool_tokens: int = 0  # loaded registry schemas, part of est_tool_tokens
    role: str = "agent"  # "agent" (main loop) or "summarizer" (Stage 5)

    @property
    def effective_input(self) -> int:
        """Prefer the provider's number; fall back to our estimate."""
        if self.reported_input_tokens is not None:
            return self.reported_input_tokens
        return self.est_total


@dataclass
class TokenLedger:
    """Fresh per turn; lives on AgentContext, never persisted or shown to the model.

    Summarizer calls (Stage 5) are kept apart from the main agent's, so the
    agent's budget numbers stay comparable with earlier stages; the summary
    reports them in their own fields."""

    records: list[LedgerRecord] = field(default_factory=list)
    summarizer_records: list[LedgerRecord] = field(default_factory=list)
    # Set by the disclosure middleware just before the call they describe.
    pending_catalog_tokens: int = 0
    pending_registry_tool_names: frozenset[str] = frozenset()

    def add(self, record: LedgerRecord) -> None:
        self.records.append(record)

    def add_summarizer(self, record: LedgerRecord) -> None:
        self.summarizer_records.append(record)

    def note_catalog(self, tokens: int) -> None:
        self.pending_catalog_tokens = tokens

    def note_registry_tools(self, names: frozenset[str]) -> None:
        self.pending_registry_tool_names = names

    def summary(self) -> UsageSummary:
        return UsageSummary(
            model_calls=len(self.records),
            input_tokens=sum(r.effective_input for r in self.records),
            output_tokens=sum(r.reported_output_tokens or 0 for r in self.records),
            peak_input_tokens=max((r.effective_input for r in self.records), default=0),
            over_budget=any(r.over_budget for r in [*self.records, *self.summarizer_records]),
            summarizer_calls=len(self.summarizer_records),
            summarizer_input_tokens=sum(r.effective_input for r in self.summarizer_records),
        )


def _reported_tokens(response: ModelResponse) -> tuple[int | None, int | None]:
    for message in response.result:
        if isinstance(message, AIMessage) and message.usage_metadata:
            usage = message.usage_metadata
            return usage.get("input_tokens"), usage.get("output_tokens")
    return None, None


class TokenLedgerMiddleware(AgentMiddleware):
    """Records one LedgerRecord per model call into runtime.context.ledger."""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        context = request.runtime.context
        ledger: TokenLedger = context.ledger
        budget: ContextBudgetConfig = context.cfg.budget

        system = [request.system_message] if request.system_message else []
        est_system = count_tokens_approximately(system) if system else 0
        est_message = count_tokens_approximately(request.messages)
        est_tools = count_tokens_approximately([], tools=request.tools) if request.tools else 0
        registry_tools = [
            t for t in request.tools if t.name in ledger.pending_registry_tool_names
        ]
        est_registry_tools = (
            count_tokens_approximately([], tools=registry_tools) if registry_tools else 0
        )
        est_total = est_system + est_message + est_tools

        call_index = len(ledger.records)
        start = time.perf_counter()
        response = handler(request)
        latency_ms = (time.perf_counter() - start) * 1000.0

        reported_input, reported_output = _reported_tokens(response)
        over_budget = max(est_total, reported_input or 0) > budget.effective_input_budget

        # Conservative truncation check (Stage 2 §11): warn-only, and only on the
        # first call of the turn, because a provider's prompt cache can report
        # fewer prompt tokens on later calls and trip a false positive.
        possible_truncation = (
            call_index == 0
            and reported_input is not None
            and reported_input < budget.truncation_warn_ratio * est_total
        )

        ledger.add(
            LedgerRecord(
                call_index=call_index,
                est_system_tokens=est_system,
                est_tool_tokens=est_tools,
                est_message_tokens=est_message,
                est_total=est_total,
                reported_input_tokens=reported_input,
                reported_output_tokens=reported_output,
                tools_offered=[tool.name for tool in request.tools],
                latency_ms=latency_ms,
                over_budget=over_budget,
                possible_truncation=possible_truncation,
                est_catalog_tokens=ledger.pending_catalog_tokens,
                catalog_over_warn=(
                    ledger.pending_catalog_tokens > context.cfg.disclosure.catalog_warn_tokens
                ),
                est_registry_tool_tokens=est_registry_tools,
            )
        )
        return response
