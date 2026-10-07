"""Eval runner. Runs each case N times on the real model and reports pass rates
plus token stats.

    uv run python -m evals.run --suite stage2 --runs 3 [--harness default|trimmed]
    uv run python -m evals.run --suite stage4 --runs 3 --harness trimmed
    uv run python -m evals.run --suite stage5 --runs 3 --harness trimmed

A suite with `registry: scale` (stage4, stage5) needs MongoDB: it seeds a throwaway
`geosearch_eval` database with the repo seeds (demo, places) and the ten scale fakes, builds the
agent from that catalog, and drops the database afterwards. The fakes are
registered on this process's handler allowlist only.

Conversations persist on `--store memory` (default) or `--store mongodb`
(Stage 6: checkpoints, records and areas in the same `geosearch_eval`
database, dropped afterwards). `--restart` (mongodb only) reopens persistence
and rebuilds the agent between turns, as a restarted API process would:

    uv run python -m evals.run --suite stage5 --harness trimmed --store mongodb --restart

Deterministic checks only; small models vary between runs, so a pass *rate*
across runs is more informative than a single pass/fail.
"""

import argparse
import json
import statistics
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.graph.state import CompiledStateGraph

from evals.checks import TurnOutcome, TurnResult, evaluate_turn
from geosearch import sources
from geosearch.agent.build import build_agent
from geosearch.agent.context import AgentContext
from geosearch.agent.conversations import ConversationRegistry
from geosearch.agent.ledger import LedgerRecord, TokenLedger
from geosearch.agent.model import build_chat_model
from geosearch.agent.persistence import Persistence, open_persistence
from geosearch.agent.run import RequestRunner
from geosearch.config import GeoConfig
from geosearch.geo.buffer import STRATEGIES
from geosearch.geo.ops import AreaOps
from geosearch.registry.catalog import CatalogCache, CatalogSnapshot
from geosearch.registry.handlers import handlers
from geosearch.registry.mongo import connect, ping
from geosearch.registry.seeding import seed
from geosearch.registry.store import MongoRegistry
from geosearch.request.models import UserRequest
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


@contextmanager
def _eval_database(store: str) -> Iterator[None]:
    """With `--store mongodb`, conversations go to the throwaway eval database:
    start it empty and drop it at the end, also for suites without a registry."""
    if store != "mongodb":
        yield
        return
    client = connect(GeoConfig(_env_file=None).mongo)
    if not ping(client):
        raise SystemExit("MongoDB not running: docker compose up -d")
    client.drop_database(EVAL_DB)
    try:
        yield
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


class _FixedAgents:
    """The eval's catalog is a fixed snapshot: no registry revision to follow."""

    def __init__(self, agent: CompiledStateGraph, snapshot: CatalogSnapshot):
        self.agent, self.snapshot = agent, snapshot

    def current(self) -> CompiledStateGraph:
        return self.agent

    def current_with_catalog(self) -> tuple[CompiledStateGraph, CatalogSnapshot]:
        return self.agent, self.snapshot


@dataclass
class _EvalRunner(RequestRunner):
    """The app's RequestRunner, keeping each turn's ledger for the report."""

    ledgers: list[TokenLedger] = field(default_factory=list)

    def _context(self) -> AgentContext:
        ledger = TokenLedger()
        self.ledgers.append(ledger)
        return AgentContext(area_ops=self.ops, cfg=self.cfg, ledger=ledger)


def _open_runner(
    cfg: GeoConfig, model: BaseChatModel, catalog: CatalogSnapshot | None
) -> tuple[_EvalRunner, Persistence]:
    """Everything a process builds at startup, wired as create_app wires it."""
    persistence = open_persistence(cfg)
    store = persistence.area_store
    agent = build_agent(cfg, model, persistence.checkpointer, catalog)
    registry = ConversationRegistry(
        cfg.conversation, persistence.checkpointer, store=persistence.conversations
    )
    runner = _EvalRunner(
        cfg, _FixedAgents(agent, catalog or CatalogSnapshot()), registry, store, AreaOps(store),
        STRATEGIES[cfg.point_buffer.strategy](cfg.point_buffer),
        keep_alive=persistence.keep_alive,
    )  # fmt: skip
    return runner, persistence


def _run_case(
    case: dict,
    cfg: GeoConfig,
    model: Any,
    default_wkt: str,
    catalog: CatalogSnapshot | None = None,
    raw_markers: list[str] | None = None,
    restart: bool = False,
) -> RunRecord:
    """Execute every turn of a case in one conversation through RequestRunner,
    collecting per-turn results and the token ledger for each turn. With
    `restart`, every turn after the first runs on freshly opened persistence
    and a rebuilt agent."""
    runner, persistence = _open_runner(cfg, model, catalog)

    wkt = case.get("wkt", default_wkt)
    turn_outcomes: list[TurnOutcome] = []
    all_records: list[LedgerRecord] = []
    conversation_id = area_id = ""
    prev_len = 0
    prev_loaded: list[int] = []
    selections: list[tuple[list[int], list[int]]] = []
    answer_sources: list[str] = []
    summarizer_calls: list[int] = []

    try:
        for i, turn in enumerate(case["turns"], start=1):
            if i == 1:
                req = UserRequest(wkt=wkt, prompt=turn["prompt"])
            else:
                if restart:
                    persistence.close()
                    runner, persistence = _open_runner(cfg, model, catalog)
                req = UserRequest(prompt=turn["prompt"], conversation_id=conversation_id)
            outcome = runner.handle(req)
            ledger = runner.ledgers[-1]
            ops = runner.ops
            conversation_id = outcome.conversation_id
            area_id = outcome.state["conversation"]["area_id"]
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
    finally:
        persistence.close()

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


def _run_suite(suite: dict, runs: int, harness: str, store: str, restart: bool) -> dict:
    """Run every case `runs` times for one harness and return its payload dict."""
    cfg = GeoConfig(_env_file=None)
    cfg.agent.harness = harness
    cfg.conversation.store = store
    cfg.mongo.database = EVAL_DB  # registry and conversations: one throwaway database
    model = build_chat_model(cfg.llm, cfg.budget)
    default_wkt = suite["wkt"]

    reports: list[CaseReport] = []
    with _catalog_for(suite, cfg) as catalog:
        for case in suite["cases"]:
            case_report = CaseReport(name=case["name"])
            for _ in range(runs):
                case_report.runs.append(
                    _run_case(
                        case, cfg, model, default_wkt, catalog, suite.get("raw_markers"), restart
                    )
                )
            reports.append(case_report)
            print(f"  {harness:8s} {case['name']:24s} {case_report.pass_rate:.0%}")
        disclosure = _disclosure_stats(reports, catalog) if catalog else None

    return {
        "suite": suite.get("name", "stage2"),
        "harness": harness,
        "store": store,
        "restart": restart,
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
        f"- Conversation store: {first['store']}"
        + (" (restart between turns)" if first["restart"] else ""),
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
    parser.add_argument("--store", choices=["memory", "mongodb"], default="memory")
    parser.add_argument("--restart", action="store_true", help="restart between turns (mongodb)")
    args = parser.parse_args()
    if args.restart and args.store != "mongodb":
        raise SystemExit("--restart needs --store mongodb: memory doesn't survive a restart")

    suite = yaml.safe_load((SUITE_DIR / f"{args.suite}.yaml").read_text())
    harnesses = ["default", "trimmed"] if args.harness == "both" else [args.harness]

    REPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    with _eval_database(args.store):
        payloads = [_run_suite(suite, args.runs, h, args.store, args.restart) for h in harnesses]

    for payload in payloads:
        path = REPORT_DIR / f"{stamp}-{args.suite}-{payload['harness']}.json"
        path.write_text(json.dumps(payload, indent=2))

    md_path = REPORT_DIR / f"{stamp}-{args.suite}.md"
    md_path.write_text(_combined_markdown(args.suite, stamp, payloads))
    print(f"\nReport: {md_path}")


if __name__ == "__main__":
    main()
