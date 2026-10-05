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


def _run_suite(suite: dict, runs: int, harness: str) -> dict:
    """Run every case `runs` times for one harness and return its payload dict."""
    cfg = GeoConfig(_env_file=None)
    cfg.agent.harness = harness
    model = build_chat_model(cfg.llm, cfg.budget)
    default_wkt = suite["wkt"]

    reports: list[CaseReport] = []
    for case in suite["cases"]:
        case_report = CaseReport(name=case["name"])
        for _ in range(runs):
            case_report.runs.append(_run_case(case, cfg, model, default_wkt))
        reports.append(case_report)
        print(f"  {harness:8s} {case['name']:20s} {case_report.pass_rate:.0%}")

    return {
        "suite": suite.get("name", "stage2"),
        "harness": harness,
        "runs": runs,
        "model": cfg.llm.model,
        "provider": cfg.llm.provider,
        "effective_input_budget": cfg.budget.effective_input_budget,
        "cases": {
            cr.name: {
                "pass_rate": cr.pass_rate,
                "runs_passed": sum(r.passed for r in cr.runs),
                "runs": len(cr.runs),
                "sample_answer": cr.runs[0].turns[-1].answer if cr.runs else "",
                "turns": [
                    [{"type": c.type, "passed": c.passed, "detail": c.detail} for c in t.checks]
                    for t in cr.runs[0].turns
                ]
                if cr.runs
                else [],
            }
            for cr in reports
        },
        "tokens": _token_stats(reports),
    }


def _combined_markdown(suite: str, stamp: str, payloads: list[dict]) -> str:
    """Pass-rate and token-cost comparison across the harnesses that were run."""
    first = payloads[0]
    lines = [
        f"# Eval report — {suite}",
        "",
        f"- Model: `{first['provider']}:{first['model']}`",
        f"- Runs per case: {first['runs']}",
        f"- Effective input budget: {first['effective_input_budget']} tokens",
        f"- Harnesses: {', '.join(p['harness'] for p in payloads)}",
        f"- Generated: {stamp}",
        "",
        "## Pass rate by case",
        "",
        "| Case | " + " | ".join(p["harness"] for p in payloads) + " |",
        "| --- | " + " | ".join("---" for _ in payloads) + " |",
    ]
    for name in first["cases"]:
        cells = " | ".join(f"{p['cases'][name]['pass_rate']:.0%}" for p in payloads)
        lines.append(f"| {name} | {cells} |")

    lines += [
        "",
        "## Token cost per model call (averaged)",
        "",
        "| Metric | " + " | ".join(p["harness"] for p in payloads) + " |",
        "| --- | " + " | ".join("---" for _ in payloads) + " |",
    ]
    for label, key in [
        ("System prompt tokens", "mean_system_tokens"),
        ("Tool-schema tokens", "mean_tool_schema_tokens"),
        ("Message tokens", "mean_message_tokens"),
        ("Mean input / call", "mean_input_per_call"),
        ("Peak input / call", "peak_input_per_call"),
        ("Mean calls / turn", "mean_calls_per_turn"),
    ]:
        cells = " | ".join(str(p["tokens"].get(key, 0)) for p in payloads)
        lines.append(f"| {label} | {cells} |")

    lines += ["", "## Notes", "", f"- {CANT_DO_NOTE}"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run GeoSearch eval suite.")
    parser.add_argument("--suite", default="stage2")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--harness", choices=["default", "trimmed", "both"], default="both"
    )
    args = parser.parse_args()

    suite = yaml.safe_load((SUITE_DIR / f"{args.suite}.yaml").read_text())
    harnesses = ["default", "trimmed"] if args.harness == "both" else [args.harness]

    REPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payloads = [_run_suite(suite, args.runs, h) for h in harnesses]

    for payload in payloads:
        path = REPORT_DIR / f"{stamp}-{args.suite}-{payload['harness']}.json"
        path.write_text(json.dumps(payload, indent=2))

    md_path = REPORT_DIR / f"{stamp}-{args.suite}.md"
    md_path.write_text(_combined_markdown(args.suite, stamp, payloads))
    print(f"\nReport: {md_path}")


if __name__ == "__main__":
    main()
