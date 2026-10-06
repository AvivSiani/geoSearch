"""Seeding, validate() and the startup check, on MongoDB."""

from pathlib import Path

import pytest
from pydantic import BaseModel
from pymongo.database import Database

from geosearch import sources
from geosearch.registry.handlers import HandlerRegistry, ToolResult, UnknownHandler
from geosearch.registry.models import ToolDefinition
from geosearch.registry.seeding import (
    SeedFileError,
    check_startup,
    seed,
    seed_files,
    validate,
)
from geosearch.registry.store import MongoRegistry

REPO_SEEDS = Path(__file__).parent.parent / "registry" / "seeds"


class In(BaseModel):
    q: str


class Row(BaseModel):
    name: str


def _handlers(*source_ids: int) -> HandlerRegistry:
    reg = HandlerRegistry()
    for source_id in source_ids:
        reg.register(source_id=source_id, input_model=In, output_model=Row)(
            lambda args, ctx: ToolResult(summary="ok")
        )
    return reg


def _write(path: Path, *entries: tuple[object, str]) -> Path:
    lines = ["tools:"] + [f"  - source_id: {s}\n    description: {d}" for s, d in entries]
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture
def registry(mongo_db: Database) -> MongoRegistry:
    return MongoRegistry(mongo_db)


@pytest.fixture
def seeds(tmp_path: Path) -> Path:
    _write(tmp_path / "tools.yaml", (17, "Find places."), (18, "Near."))
    _write(tmp_path / "demo.yaml", (9001, "Points."))
    return tmp_path


HANDLERS = _handlers(17, 18, 9001)


def test_seed_creates_then_is_a_noop(registry: MongoRegistry, seeds: Path) -> None:
    first = seed(registry, [seeds], include_demo=False, handler_registry=HANDLERS)
    assert first.created == [17, 18]
    rev = registry.revision()

    again = seed(registry, [seeds], include_demo=False, handler_registry=HANDLERS)
    assert again.created == again.changed == again.deleted == []
    assert again.unchanged == [17, 18]
    assert registry.revision() == rev


def test_seed_reports_changes(registry: MongoRegistry, seeds: Path) -> None:
    seed(registry, [seeds], include_demo=False, handler_registry=HANDLERS)
    _write(seeds / "tools.yaml", (17, "Find places, v2."), (18, "Near."))
    report = seed(registry, [seeds], include_demo=False, handler_registry=HANDLERS)
    assert report.changed == [17]
    assert report.unchanged == [18]
    assert registry.get_tool(17).description == "Find places, v2."


def test_demo_seed_needs_include_demo(registry: MongoRegistry, seeds: Path) -> None:
    seed(registry, [seeds], include_demo=False, handler_registry=HANDLERS)
    assert 9001 not in {t.source_id for t in registry.list_tools()}
    seed(registry, [seeds], include_demo=True, handler_registry=HANDLERS)
    assert 9001 in {t.source_id for t in registry.list_tools()}


def test_explicit_demo_file_is_loaded(seeds: Path) -> None:
    assert seed_files([seeds / "demo.yaml"], include_demo=False) == [seeds / "demo.yaml"]


def test_prune_deletes_tools_in_no_seed_file(registry: MongoRegistry, seeds: Path) -> None:
    seed(registry, [seeds], include_demo=True, handler_registry=HANDLERS)
    report = seed(registry, [seeds], include_demo=False, handler_registry=HANDLERS)
    assert report.deleted == []  # no prune: extra tools stay
    report = seed(registry, [seeds], include_demo=False, prune=True, handler_registry=HANDLERS)
    assert report.deleted == [9001]
    assert [t.source_id for t in registry.list_tools()] == [17, 18]


def test_unknown_handler_fails_by_id_and_writes_nothing(
    registry: MongoRegistry, tmp_path: Path
) -> None:
    _write(tmp_path / "x.yaml", (17, "Find."), (4242, "Oops."))
    with pytest.raises(UnknownHandler, match="4242"):
        seed(registry, [tmp_path], include_demo=False, handler_registry=HANDLERS)
    assert registry.revision() == 0 and registry.list_tools() == []


@pytest.mark.parametrize(
    "content",
    [
        "tools: [",
        "tools: 3",
        "- just a list",
        "tools:\n  - source_id: demo.sample_points\n    description: x",
        "tools:\n  - source_id: '17'\n    description: x",
        "tools:\n  - source_id: 0\n    description: x",
    ],
)
def test_malformed_seed_files_fail(tmp_path: Path, registry: MongoRegistry, content: str) -> None:
    (tmp_path / "bad.yaml").write_text(content)
    with pytest.raises(SeedFileError, match="bad.yaml"):
        seed(registry, [tmp_path], include_demo=False, handler_registry=HANDLERS)


def test_duplicate_source_id_across_files_fails(tmp_path: Path, registry: MongoRegistry) -> None:
    _write(tmp_path / "a.yaml", (17, "A."))
    _write(tmp_path / "b.yaml", (17, "B."))
    with pytest.raises(SeedFileError, match="already defined"):
        seed(registry, [tmp_path], include_demo=False, handler_registry=HANDLERS)


def test_missing_seed_path_fails(tmp_path: Path) -> None:
    with pytest.raises(SeedFileError, match="not found"):
        seed_files([tmp_path / "nope"], include_demo=False)


def test_validate_reports_drift_without_writing(registry: MongoRegistry, seeds: Path) -> None:
    seed(registry, [seeds], include_demo=True, handler_registry=HANDLERS)
    assert validate(registry, [seeds], include_demo=True, handler_registry=HANDLERS).ok

    registry.upsert_tool(ToolDefinition(source_id=17, description="Edited in DB."))
    registry.upsert_tool(ToolDefinition(source_id=666, description="Not seeded."))
    registry.delete_tool(18)
    rev = registry.revision()

    report = validate(registry, [seeds], include_demo=True, handler_registry=HANDLERS)
    assert not report.ok
    diffs = {d.source_id: (d.seed, d.db) for d in report.differences}
    assert diffs == {17: ("Find places.", "Edited in DB."), 18: ("Near.", None)}
    assert report.not_in_seeds == [666]
    assert report.without_handler == [666]
    assert report.handlers_not_in_db == [18]
    assert registry.revision() == rev


def test_validate_warns_about_handlers_without_definition(
    registry: MongoRegistry, seeds: Path
) -> None:
    seed(registry, [seeds], include_demo=False, handler_registry=HANDLERS)
    report = validate(registry, [seeds], include_demo=False, handler_registry=HANDLERS)
    assert report.handlers_not_in_db == [9001]
    assert report.ok  # a warning, not a problem


def test_startup_check(registry: MongoRegistry) -> None:
    registry.upsert_tool(ToolDefinition(source_id=17, description="Find."))
    assert check_startup(registry, strict=True, handler_registry=HANDLERS) == []

    registry.upsert_tool(ToolDefinition(source_id=666, description="No handler."))
    with pytest.raises(UnknownHandler, match="666"):
        check_startup(registry, strict=True, handler_registry=HANDLERS)
    assert check_startup(registry, strict=False, handler_registry=HANDLERS) == [666]


def test_repo_seeds_match_real_handlers(registry: MongoRegistry) -> None:
    sources.load_all()
    report = seed(registry, [REPO_SEEDS], include_demo=True)
    assert 9001 in report.created
    assert validate(registry, [REPO_SEEDS], include_demo=True).ok
