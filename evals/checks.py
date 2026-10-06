"""Deterministic check functions for eval cases.

Each check takes a `TurnResult` plus its YAML params and returns (passed, detail).
Checks are intentionally simple and keyword-based; the can't-do keyword check in
particular is brittle, which the report notes. No check calls an LLM.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from geosearch.agent.ledger import LedgerRecord
from geosearch.request.models import UsageSummary

# The can't-do vocabulary (Stage 2 §14). Brittle by design; noted in the report.
CANT_DO_PHRASES = (
    "can't", "cannot", "unable", "not able", "don't have", "do not have", "not yet",
)  # fmt: skip


@dataclass
class TurnResult:
    """Everything a check may inspect about one executed turn."""

    prompt: str
    answer: str
    tools_called: list[str]  # tools the model actually invoked this turn
    tools_offered: set[str]  # tools offered to the model this turn
    usage: UsageSummary
    records: list[LedgerRecord]
    effective_input_budget: int
    # Stage 4: progressive disclosure.
    newly_loaded: list[int] = field(default_factory=list)  # registry ids loaded this turn
    tool_results: list[tuple[str, str]] = field(default_factory=list)  # (tool name, content)


def _numbers(text: str) -> list[float]:
    return [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))]


def check_tool_called(result: TurnResult, *, tool: str) -> tuple[bool, str]:
    ok = tool in result.tools_called
    return ok, f"{tool} {'called' if ok else 'NOT called'} (called: {result.tools_called})"


def check_answer_non_empty(result: TurnResult) -> tuple[bool, str]:
    ok = bool(result.answer.strip())
    return ok, f"answer len={len(result.answer.strip())}"


def check_answer_number_within(
    result: TurnResult, *, value: float, rel: float = 0.05, unit: str | None = None
) -> tuple[bool, str]:
    """Some number in the answer is within `rel` of `value`. If `unit` is given,
    the answer must also mention it."""
    if unit is not None and unit not in result.answer:
        return False, f"unit {unit!r} missing"
    hits = [n for n in _numbers(result.answer) if abs(n - value) <= rel * value]
    ok = bool(hits)
    return ok, f"numbers={_numbers(result.answer)} within {rel:.0%} of {value}: {hits}"


def check_answer_not_number(
    result: TurnResult, *, value: float, rel: float = 0.05
) -> tuple[bool, str]:
    """No number in the answer is close to `value` (e.g. must never report
    Paris's area)."""
    hits = [n for n in _numbers(result.answer) if abs(n - value) <= rel * value]
    ok = not hits
    return ok, f"forbidden≈{value}: hits={hits}"


def check_answer_contains_cant_do(result: TurnResult) -> tuple[bool, str]:
    lowered = result.answer.lower()
    hits = [p for p in CANT_DO_PHRASES if p in lowered]
    return bool(hits), f"cant-do phrases found: {hits}"


def check_no_unoffered_tool_calls(result: TurnResult) -> tuple[bool, str]:
    bad = [t for t in result.tools_called if t not in result.tools_offered]
    return not bad, f"calls to unoffered tools: {bad}"


def check_under_budget(result: TurnResult) -> tuple[bool, str]:
    peak = result.usage.peak_input_tokens
    ok = not result.usage.over_budget and peak <= result.effective_input_budget
    return ok, (
        f"peak_input={peak} budget={result.effective_input_budget} over={result.usage.over_budget}"
    )


def check_loaded_exactly(result: TurnResult, *, ids: list[int]) -> tuple[bool, str]:
    """The registry tools loaded this turn are exactly `ids` (empty: none)."""
    ok = sorted(result.newly_loaded) == sorted(ids)
    return ok, f"loaded {sorted(result.newly_loaded)}, expected {sorted(ids)}"


def check_no_reload(result: TurnResult) -> tuple[bool, str]:
    """load_tools was not called this turn (tools loaded earlier carry over)."""
    ok = "load_tools" not in result.tools_called
    return ok, f"load_tools {'NOT ' if ok else ''}called (called: {result.tools_called})"


def check_tool_result_contains(result: TurnResult, *, tool: str, text: str) -> tuple[bool, str]:
    hits = [c for name, c in result.tool_results if name == tool and text in c]
    return bool(hits), f"{tool} result containing {text!r}: {len(hits)}"


def check_any_of(result: TurnResult, *, checks: list[dict]) -> tuple[bool, str]:
    details = []
    for spec in checks:
        passed, detail = run_check(result, spec)
        details.append(f"{spec['type']}:{passed}")
        if passed:
            return True, f"any_of OK ({details})"
    return False, f"any_of all failed ({details})"


_REGISTRY: dict[str, Callable[..., tuple[bool, str]]] = {
    "tool_called": check_tool_called,
    "answer_non_empty": check_answer_non_empty,
    "answer_number_within": check_answer_number_within,
    "answer_not_number": check_answer_not_number,
    "answer_contains_cant_do": check_answer_contains_cant_do,
    "no_unoffered_tool_calls": check_no_unoffered_tool_calls,
    "under_budget": check_under_budget,
    "loaded_exactly": check_loaded_exactly,
    "no_reload": check_no_reload,
    "tool_result_contains": check_tool_result_contains,
    "any_of": check_any_of,
}


@dataclass
class CheckOutcome:
    type: str
    passed: bool
    detail: str


def run_check(result: TurnResult, spec: dict[str, Any]) -> tuple[bool, str]:
    params = {k: v for k, v in spec.items() if k != "type"}
    return _REGISTRY[spec["type"]](result, **params)


@dataclass
class TurnOutcome:
    prompt: str
    answer: str
    checks: list[CheckOutcome] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def evaluate_turn(result: TurnResult, specs: list[dict]) -> TurnOutcome:
    outcome = TurnOutcome(prompt=result.prompt, answer=result.answer)
    for spec in specs:
        passed, detail = run_check(result, spec)
        outcome.checks.append(CheckOutcome(type=spec["type"], passed=passed, detail=detail))
    return outcome
