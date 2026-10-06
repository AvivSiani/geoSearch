"""Eval runner. Runs each case N times on the real model and reports pass rates
plus token stats.

    uv run python -m evals.run --suite stage2 --runs 3 [--harness default|trimmed]
    uv run python -m evals.run --suite stage4 --runs 3 --harness trimmed
    uv run python -m evals.run --suite stage5 --runs 3 --harness trimmed

A suite with `registry: scale` (stage4, stage5) needs MongoDB: it seeds a throwaway
`geosearch_eval` database with the repo seeds (demo, places) and the ten scale fakes, builds the
agent from that catalog, and drops the database afterwards. The fakes are
registered on this process's handler allowlist only.

Deterministic checks only; small models vary between runs, so a pass *rate*
across runs is more informative than a single pass/fail.
"""

import argparse
import json
import statistics
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import shapely
import yaml
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.checkpoint.memory import InMemorySaver

from evals.checks import TurnOutcome, TurnResult, evaluate_turn
from geosearch import sources
from geosearch.agent.build import build_agent
from geosearch.agent.context import AgentContext
from geosearch.agent.ledger import LedgerRecord, TokenLedger
from geosearch.agent.model import build_chat_model
from geosearch.agent.run import build_new_turn_state, invoke_turn
from geosearch.config import GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.buffer import STRATEGIES
from geosearch.geo.ops import AreaOps
from geosearch.registry.catalog import CatalogCache, CatalogSnapshot
from geosearch.registry.handlers import handlers
from geosearch.registry.mongo import connect, ping
from geosearch.registry.seeding import seed
from geosearch.registry.store import MongoRegistry
from geosearch.request.models import UserRequest
from geosearch.request.validate import validate_request
from geosearch.sources.fakes import FAKE_DEFINITIONS, FAKES, register_fakes

SUITE_DIR = Path(__file__).parent / "cases"
REPORT_DIR = Path(__file__).parent / "reports"
SEEDS_DIR = Path(__file__).parent.parent / "registry" / "seeds"
EVAL_DB = "geosearch_eval"

CANT_DO_NOTE = (
    "The missing-capability / area-override checks rely on a small keyword list "
    "(can't, cannot, unable, not able, don't have, do not have, not yet). This is brittle: a "
    "valid refusal phrased differently is scored as a failure."
)


@dataclass
class RunRecord:
    """One full run of a case (all its turns)."""

    turns: list[TurnOutcome]
    ledger_records: list[LedgerRecord]
    turn_count: int
    selections: list[tuple[list[int], list[int]]] = field(default_factory=list)  # expected, loaded
    answer_sources: list[str] = field(default_factory=list)  # per turn (Stage 5)
    summarizer_calls: list[int] = field(default_factory=list)  # per turn (Stage 5)

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


@contextmanager
def _catalog_for(suite: dict, cfg: GeoConfig) -> Iterator[CatalogSnapshot | None]:
    """The catalog a suite runs against: none for stage2, or a seeded scale
    registry in a throwaway MongoDB database for `registry: scale`."""
    if suite.get("registry") != "scale":
        yield None
        return
    client = connect(cfg.mongo)
    if not ping(client):
        raise SystemExit("MongoDB not running: docker compose up -d")
    sources.load_all()
    if not any(source_id in handlers for source_id in FAKES):
        register_fakes(handlers)  # this eval process only; never the app's allowlist
    client.drop_database(EVAL_DB)
    try:
        registry = MongoRegistry(client[EVAL_DB])
        seed(registry, [SEEDS_DIR], include_demo=True)
        for definition in FAKE_DEFINITIONS:
            registry.upsert_tool(definition)
        catalog = CatalogCache(registry)
        catalog.refresh_if_changed()
        yield catalog.snapshot
    finally:
        client.drop_database(EVAL_DB)
        client.close()


def _selection_scores(selections: list[tuple[list[int], list[int]]]) -> tuple[float, float]:
    """Mean precision and recall of what was loaded vs expected, per turn.
    An empty expectation met by loading nothing scores 1/1."""
    precisions, recalls = [], []
    for expected, loaded in selections:
        hit = len(set(expected) & set(loaded))
        precisions.append(hit / len(set(loaded)) if loaded else 1.0)
        recalls.append(hit / len(set(expected)) if expected else 1.0)
    if not precisions:
        return 1.0, 1.0
    return statistics.mean(precisions), statistics.mean(recalls)


def _run_case(
    case: dict,
    cfg: GeoConfig,
    model: Any,
    default_wkt: str,
    catalog: CatalogSnapshot | None = None,
    raw_markers: list[str] | None = None,
) -> RunRecord:
    """Execute every turn of a case in one conversation, collecting per-turn
    results and the token ledger for each turn (fresh context per turn)."""
    store = InMemoryAreaStore(max_entries=cfg.area_store.max_entries)
    ops = AreaOps(store)
    buffer_strategy = STRATEGIES[cfg.point_buffer.strategy](cfg.point_buffer)
    checkpointer = InMemorySaver()
    agent = build_agent(cfg, model, checkpointer, catalog)
    conversation_id = uuid.uuid4().hex

    wkt = case.get("wkt", default_wkt)
    turn_outcomes: list[TurnOutcome] = []
    all_records: list[LedgerRecord] = []
    area_id = area_wkt = area_summary = ""
    prev_len = 0
    prev_loaded: list[int] = []
    selections: list[tuple[list[int], list[int]]] = []
    answer_sources: list[str] = []
    summarizer_calls: list[int] = []

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
            tc["name"] for m in new_messages if isinstance(m, AIMessage) for tc in m.tool_calls
        ]
        loaded = list(outcome.state.get("loaded_tools") or [])
        newly_loaded = [i for i in loaded if i not in prev_loaded]
        prev_loaded = loaded
        if "expect_loaded" in turn:
            selections.append((list(turn["expect_loaded"]), newly_loaded))
        tool_results = [
            (m.name or "", str(m.content)) for m in new_messages if isinstance(m, ToolMessage)
        ]
        tools_offered: set[str] = set()
        for record in ledger.records:
            tools_offered.update(record.tools_offered)
        results_read = [
            str(tc["args"].get("file_path") or tc["args"].get("path") or "")
            for m in new_messages
            if isinstance(m, AIMessage)
            for tc in m.tool_calls
            if tc["name"] in ("read_file", "ls")
            and "/results" in str(tc["args"].get("file_path") or tc["args"].get("path") or "")
        ]
        summarizer_calls.append(ledger.summary().summarizer_calls)

        result = TurnResult(
            prompt=turn["prompt"],
            answer=outcome.answer,
            tools_called=tools_called,
            tools_offered=tools_offered,
            usage=ledger.summary(),
            records=ledger.records,
            effective_input_budget=cfg.budget.effective_input_budget,
            newly_loaded=newly_loaded,
            tool_results=tool_results,
            answer_source=outcome.answer_source,
            item_ids=[item.id for item in outcome.items],
            items_inside=[
                item.lon is not None
                and item.lat is not None
                and ops.contains(area_id, item.lon, item.lat)
                for item in outcome.items
            ],
            main_context="\n".join(str(m.content) for m in new_messages),
            raw_markers=list(raw_markers or []),
            results_read=results_read,
        )
        answer_sources.append(outcome.answer_source)
        turn_outcomes.append(evaluate_turn(result, turn["checks"]))
        all_records.extend(ledger.records)

    return RunRecord(
        turns=turn_outcomes,
        ledger_records=all_records,
        turn_count=len(case["turns"]),
        selections=selections,
        answer_sources=answer_sources,
        summarizer_calls=summarizer_calls,
    )


def _mean(values: list[float]) -> float:
    return round(statistics.mean(values), 1) if values else 0.0


def _disclosure_stats(reports: list[CaseReport], catalog: CatalogSnapshot) -> dict[str, float]:
    """What disclosure costs and saves. 'Before loading' = calls offering no
    registry schema; 'after' = calls offering at least one loaded tool."""
    records = [r for cr in reports for run in cr.runs for r in run.ledger_records]
    before = [r for r in records if r.est_registry_tool_tokens == 0]
    after = [r for r in records if r.est_registry_tool_tokens > 0]
    all_tools = catalog.resolved_tools()
    return {
        "catalog_tools": len(catalog.tools),
        "mean_catalog_tokens": _mean([r.est_catalog_tokens for r in records]),
        "catalog_over_warn_calls": sum(r.catalog_over_warn for r in records),
        "calls_before_loading": len(before),
        "calls_after_loading": len(after),
        "mean_tool_schema_tokens_before": _mean([r.est_tool_tokens for r in before]),
        "mean_tool_schema_tokens_after": _mean([r.est_tool_tokens for r in after]),
        "mean_registry_schema_tokens_after": _mean([r.est_registry_tool_tokens for r in after]),
        "all_registry_schemas_tokens": count_tokens_approximately([], tools=all_tools),
    }


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


def _answer_stats(reports: list[CaseReport]) -> dict[str, float]:
    """How turns finished (Stage 5): submitted vs fallback, and summarizer use."""
    sources = [s for cr in reports for run in cr.runs for s in run.answer_sources]
    calls = [c for cr in reports for run in cr.runs for c in run.summarizer_calls]
    if not sources:
        return {}
    return {
        "turns": len(sources),
        "submitted_rate": round(sources.count("submitted") / len(sources), 2),
        "fallback_rate": round(sources.count("fallback") / len(sources), 2),
        "mean_summarizer_calls_per_turn": round(statistics.mean(calls), 2) if calls else 0.0,
    }


def _run_suite(suite: dict, runs: int, harness: str) -> dict:
    """Run every case `runs` times for one harness and return its payload dict."""
    cfg = GeoConfig(_env_file=None)
    cfg.agent.harness = harness
    model = build_chat_model(cfg.llm, cfg.budget)
    default_wkt = suite["wkt"]

    reports: list[CaseReport] = []
    with _catalog_for(suite, cfg) as catalog:
        for case in suite["cases"]:
            case_report = CaseReport(name=case["name"])
            for _ in range(runs):
                case_report.runs.append(
                    _run_case(case, cfg, model, default_wkt, catalog, suite.get("raw_markers"))
                )
            reports.append(case_report)
            print(f"  {harness:8s} {case['name']:24s} {case_report.pass_rate:.0%}")
        disclosure = _disclosure_stats(reports, catalog) if catalog else None

    return {
        "suite": suite.get("name", "stage2"),
        "harness": harness,
        "runs": runs,
        "model": cfg.llm.model,
        "provider": cfg.llm.provider,
        "effective_input_budget": cfg.budget.effective_input_budget,
        "disclosure": disclosure,
        "cases": {
            cr.name: {
                "pass_rate": cr.pass_rate,
                "selection": dict(
                    zip(
                        ("precision", "recall"),
                        _selection_scores([s for run in cr.runs for s in run.selections]),
                        strict=True,
                    )
                ),
                "loaded_per_run": [[loaded for _e, loaded in run.selections] for run in cr.runs],
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
        "answers": _answer_stats(reports),
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

    lines += [
        "",
        "## How turns finished",
        "",
        "| Metric | " + " | ".join(p["harness"] for p in payloads) + " |",
        "| --- | " + " | ".join("---" for _ in payloads) + " |",
    ]
    for label, key in [
        ("Submitted (submit_answer)", "submitted_rate"),
        ("Fallback (no submit)", "fallback_rate"),
        ("Summarizer calls / turn", "mean_summarizer_calls_per_turn"),
    ]:
        cells = " | ".join(str(p.get("answers", {}).get(key, 0)) for p in payloads)
        lines.append(f"| {label} | {cells} |")

    for p in payloads:
        if p.get("disclosure"):
            lines += _disclosure_markdown(p)
    lines += ["", "## Notes", "", f"- {CANT_DO_NOTE}"]
    return "\n".join(lines) + "\n"


def _disclosure_markdown(payload: dict) -> list[str]:
    d = payload["disclosure"]
    lines = [
        "",
        f"## Tool selection ({payload['harness']})",
        "",
        "Per turn with an `expect_loaded`; averaged over runs. Precision = loaded "
        "tools that were expected; recall = expected tools that were loaded.",
        "",
        "| Case | Precision | Recall | Loaded per run (by turn) |",
        "| --- | --- | --- | --- |",
    ]
    for name, case in payload["cases"].items():
        sel = case["selection"]
        lines.append(
            f"| {name} | {sel['precision']:.2f} | {sel['recall']:.2f} | {case['loaded_per_run']} |"
        )
    lines += [
        "",
        f"## Disclosure cost ({payload['harness']})",
        "",
        "| Metric | Tokens |",
        "| --- | --- |",
        f"| Catalog block ({d['catalog_tools']} tools), mean per call | "
        f"{d['mean_catalog_tokens']} |",
        f"| Calls with catalog over warn threshold | {d['catalog_over_warn_calls']} |",
        f"| Tool schemas per call, before loading ({d['calls_before_loading']} calls) | "
        f"{d['mean_tool_schema_tokens_before']} |",
        f"| Tool schemas per call, after loading ({d['calls_after_loading']} calls) | "
        f"{d['mean_tool_schema_tokens_after']} |",
        f"| … of which loaded registry schemas | {d['mean_registry_schema_tokens_after']} |",
        f"| All registry schemas, if offered up front | {d['all_registry_schemas_tokens']} |",
    ]
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Run GeoSearch eval suite.")
    parser.add_argument("--suite", default="stage2")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--harness", choices=["default", "trimmed", "both"], default="both")
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
