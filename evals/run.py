"""Eval runner. Runs each case N times on the real model and reports pass rates
plus token stats.

    uv run python -m evals.run --suite stage2 --runs 3 [--harness default|trimmed]

Deterministic checks only; small models vary between runs, so a pass *rate*
across runs is more informative than a single pass/fail.
"""

import argparse
import json
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import shapely
import yaml
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from evals.checks import TurnOutcome, TurnResult, evaluate_turn
from geosearch.agent.build import build_agent
from geosearch.agent.context import AgentContext
from geosearch.agent.ledger import LedgerRecord, TokenLedger
from geosearch.agent.model import build_chat_model
from geosearch.agent.run import build_new_turn_state, invoke_turn
from geosearch.config import GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.buffer import STRATEGIES
from geosearch.geo.ops import AreaOps
from geosearch.request.models import UserRequest
from geosearch.request.validate import validate_request

SUITE_DIR = Path(__file__).parent / "cases"
REPORT_DIR = Path(__file__).parent / "reports"

CANT_DO_NOTE = (
    "The missing-capability / area-override checks rely on a small keyword list "
    "(can't, cannot, unable, not able, don't have, not yet). This is brittle: a "
    "valid refusal phrased differently is scored as a failure."
)


@dataclass
class RunRecord:
    """One full run of a case (all its turns)."""

    turns: list[TurnOutcome]
    ledger_records: list[LedgerRecord]
    turn_count: int

    @property
    def passed(self) -> bool:
        return all(t.passed for t in self.turns)


@dataclass
class CaseReport:
    name: str
    runs: list[RunRecord] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return sum(r.passed for r in self.runs) / len(self.runs) if self.runs else 0.0


def _run_case(case: dict, cfg: GeoConfig, model: Any, default_wkt: str) -> RunRecord:
    """Execute every turn of a case in one conversation, collecting per-turn
    results and the token ledger for each turn (fresh context per turn)."""
    store = InMemoryAreaStore(max_entries=cfg.area_store.max_entries)
    ops = AreaOps(store)
    buffer_strategy = STRATEGIES[cfg.point_buffer.strategy](cfg.point_buffer)
    checkpointer = InMemorySaver()
    agent = build_agent(cfg, model, checkpointer)
    conversation_id = uuid.uuid4().hex

    wkt = case.get("wkt", default_wkt)
    turn_outcomes: list[TurnOutcome] = []
    all_records: list[LedgerRecord] = []
    area_id = area_wkt = area_summary = ""
    prev_len = 0

    for i, turn in enumerate(case["turns"], start=1):
        ledger = TokenLedger()
        context = AgentContext(area_ops=ops, cfg=cfg, ledger=ledger)
        if i == 1:
            validated = validate_request(
                UserRequest(wkt=wkt, prompt=turn["prompt"]), cfg, store, ops, buffer_strategy
            )
            area_id = validated.area_id
            area_wkt = shapely.to_wkt(store.get(area_id))
            area_summary = validated.area_summary
        state = build_new_turn_state(
            conversation_id=conversation_id,
            area_id=area_id,
            area_wkt=area_wkt,
            area_summary=area_summary,
            request_id=uuid.uuid4().hex,
            prompt=turn["prompt"],
            turn=i,
        )
        outcome = invoke_turn(agent, context, state, thread_id=conversation_id)

        new_messages = outcome.state["messages"][prev_len:]
        prev_len = len(outcome.state["messages"])
        tools_called = [
            tc["name"]
            for m in new_messages
            if isinstance(m, AIMessage)
            for tc in m.tool_calls
        ]
        tools_offered: set[str] = set()
        for record in ledger.records:
            tools_offered.update(record.tools_offered)

        result = TurnResult(
            prompt=turn["prompt"],
            answer=outcome.answer,
            tools_called=tools_called,
            tools_offered=tools_offered,
            usage=ledger.summary(),
            records=ledger.records,
            effective_input_budget=cfg.budget.effective_input_budget,
        )
        turn_outcomes.append(evaluate_turn(result, turn["checks"]))
        all_records.extend(ledger.records)

    return RunRecord(turns=turn_outcomes, ledger_records=all_records, turn_count=len(case["turns"]))


def _token_stats(reports: list[CaseReport]) -> dict[str, float]:
    records = [r for cr in reports for run in cr.runs for r in run.ledger_records]
    turns = sum(run.turn_count for cr in reports for run in cr.runs)
    if not records:
        return {}
    inputs = [r.effective_input for r in records]
    return {
        "calls": len(records),
        "mean_input_per_call": round(statistics.mean(inputs), 1),
        "peak_input_per_call": max(inputs),
        "mean_calls_per_turn": round(len(records) / turns, 2) if turns else 0.0,
        "mean_system_tokens": round(statistics.mean(r.est_system_tokens for r in records), 1),
        "mean_tool_schema_tokens": round(statistics.mean(r.est_tool_tokens for r in records), 1),
        "mean_message_tokens": round(statistics.mean(r.est_message_tokens for r in records), 1),
    }


def _write_reports(
    suite: str, harness: str, runs: int, cfg: GeoConfig, reports: list[CaseReport]
) -> Path:
    REPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = REPORT_DIR / f"{stamp}-{suite}-{harness}"
    stats = _token_stats(reports)

    payload = {
        "suite": suite,
        "harness": harness,
        "runs": runs,
        "model": cfg.llm.model,
        "provider": cfg.llm.provider,
        "effective_input_budget": cfg.budget.effective_input_budget,
        "timestamp": stamp,
        "cases": {
            cr.name: {
                "pass_rate": cr.pass_rate,
                "runs_passed": sum(r.passed for r in cr.runs),
                "runs": len(cr.runs),
                "sample_answer": cr.runs[0].turns[-1].answer if cr.runs else "",
                "turns": [
                    [
                        {"type": c.type, "passed": c.passed, "detail": c.detail}
                        for c in t.checks
                    ]
                    for t in cr.runs[0].turns
                ]
                if cr.runs
                else [],
            }
            for cr in reports
        },
        "tokens": stats,
        "notes": [CANT_DO_NOTE],
    }
    base.with_suffix(".json").write_text(json.dumps(payload, indent=2))
    base.with_suffix(".md").write_text(_markdown(payload))
    return base


def _markdown(p: dict) -> str:
    lines = [
        f"# Eval report — {p['suite']} ({p['harness']} harness)",
        "",
        f"- Model: `{p['provider']}:{p['model']}`",
        f"- Runs per case: {p['runs']}",
        f"- Effective input budget: {p['effective_input_budget']} tokens",
        f"- Generated: {p['timestamp']}",
        "",
        "## Pass rates",
        "",
        "| Case | Pass rate | Runs passed |",
        "| --- | --- | --- |",
    ]
    for name, c in p["cases"].items():
        lines.append(f"| {name} | {c['pass_rate']:.0%} | {c['runs_passed']}/{c['runs']} |")
    s = p["tokens"]
    lines += [
        "",
        "## Token cost (per model call, averaged)",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| System prompt tokens | {s.get('mean_system_tokens', 0)} |",
        f"| Tool-schema tokens | {s.get('mean_tool_schema_tokens', 0)} |",
        f"| Message tokens | {s.get('mean_message_tokens', 0)} |",
        f"| Mean input / call | {s.get('mean_input_per_call', 0)} |",
        f"| Peak input / call | {s.get('peak_input_per_call', 0)} |",
        f"| Mean calls / turn | {s.get('mean_calls_per_turn', 0)} |",
        "",
        "## Notes",
        "",
    ]
    lines += [f"- {n}" for n in p["notes"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run GeoSearch eval suite.")
    parser.add_argument("--suite", default="stage2")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--harness", choices=["default", "trimmed"], default="default")
    args = parser.parse_args()

    suite = yaml.safe_load((SUITE_DIR / f"{args.suite}.yaml").read_text())
    default_wkt = suite["wkt"]

    cfg = GeoConfig(_env_file=None)
    cfg.agent.harness = args.harness
    model = build_chat_model(cfg.llm, cfg.budget)

    reports: list[CaseReport] = []
    for case in suite["cases"]:
        case_report = CaseReport(name=case["name"])
        for _ in range(args.runs):
            case_report.runs.append(_run_case(case, cfg, model, default_wkt))
        reports.append(case_report)
        print(f"{case['name']:20s} {case_report.pass_rate:.0%}")

    base = _write_reports(args.suite, args.harness, args.runs, cfg, reports)
    print(f"\nReport: {base}.md")


if __name__ == "__main__":
    main()
