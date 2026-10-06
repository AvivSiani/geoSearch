"""Stage 3 step 5: seeding, validate() and the startup check, on MongoDB."""

from pathlib import Path

import pytest
from pydantic import BaseModel
from pymongo.database import Database

from geosearch import capabilities
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


def _handlers(*source_ids: str) -> HandlerRegistry:
    reg = HandlerRegistry()
    for source_id in source_ids:
        reg.register(source_id, input_model=In, output_model=Row)(
            lambda args, ctx: ToolResult(summary="ok")
        )
    return reg


def _write(path: Path, *entries: tuple[str, str]) -> Path:
    lines = ["tools:"] + [
        f"  - source_id: {s}\n    description: {d}" for s, d in entries
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture
def registry(mongo_db: Database) -> MongoRegistry:
    return MongoRegistry(mongo_db)


@pytest.fixture
def seeds(tmp_path: Path) -> Path:
    _write(tmp_path / "places.yaml", ("places.search", "Find places."), ("places.near", "Near."))
    _write(tmp_path / "demo.yaml", ("demo.points", "Points."))
    return tmp_path


HANDLERS = _handlers("places.search", "places.near", "demo.points")


def test_seed_creates_then_is_a_noop(registry: MongoRegistry, seeds: Path) -> None:
    first = seed(registry, [seeds], include_demo=False, handler_registry=HANDLERS)
    assert sorted(first.created) == ["places.near", "places.search"]
    rev = registry.revision()

    again = seed(registry, [seeds], include_demo=False, handler_registry=HANDLERS)
    assert again.created == again.changed == again.deleted == []
    assert sorted(again.unchanged) == ["places.near", "places.search"]
    assert registry.revision() == rev


def test_seed_reports_changes(registry: MongoRegistry, seeds: Path) -> None:
    seed(registry, [seeds], include_demo=False, handler_registry=HANDLERS)
    _write(seeds / "places.yaml", ("places.search", "Find places, v2."), ("places.near", "Near."))
    report = seed(registry, [seeds], include_demo=False, handler_registry=HANDLERS)
    assert report.changed == ["places.search"]
    assert report.unchanged == ["places.near"]
    assert registry.get_tool("places.search").description == "Find places, v2."


def test_demo_seed_needs_include_demo(registry: MongoRegistry, seeds: Path) -> None:
    seed(registry, [seeds], include_demo=False, handler_registry=HANDLERS)
    assert "demo" not in registry.list_capabilities()
    seed(registry, [seeds], include_demo=True, handler_registry=HANDLERS)
    assert "demo" in registry.list_capabilities()


def test_explicit_demo_file_is_loaded(seeds: Path) -> None:
    assert seed_files([seeds / "demo.yaml"], include_demo=False) == [seeds / "demo.yaml"]


def test_prune_deletes_tools_in_no_seed_file(registry: MongoRegistry, seeds: Path) -> None:
    seed(registry, [seeds], include_demo=True, handler_registry=HANDLERS)
    report = seed(registry, [seeds], include_demo=False, handler_registry=HANDLERS)
    assert report.deleted == []  # no prune: extra tools stay
    report = seed(registry, [seeds], include_demo=False, prune=True, handler_registry=HANDLERS)
    assert report.deleted == ["demo.points"]
    assert registry.list_capabilities() == ["places"]


def test_unknown_handler_fails_by_name_and_writes_nothing(
    registry: MongoRegistry, tmp_path: Path
) -> None:
    _write(tmp_path / "x.yaml", ("places.search", "Find."), ("places.typo", "Oops."))
    with pytest.raises(UnknownHandler, match="places.typo"):
        seed(registry, [tmp_path], include_demo=False, handler_registry=HANDLERS)
    assert registry.revision() == 0 and registry.list_capabilities() == []


@pytest.mark.parametrize(
    "content",
    ["tools: [", "tools: 3", "- just a list", "tools:\n  - source_id: Bad.Id\n    description: x"],
)
def test_malformed_seed_files_fail(tmp_path: Path, registry: MongoRegistry, content: str) -> None:
    (tmp_path / "bad.yaml").write_text(content)
    with pytest.raises(SeedFileError, match="bad.yaml"):
        seed(registry, [tmp_path], include_demo=False, handler_registry=HANDLERS)


def test_duplicate_source_id_across_files_fails(tmp_path: Path, registry: MongoRegistry) -> None:
    _write(tmp_path / "a.yaml", ("places.search", "A."))
    _write(tmp_path / "b.yaml", ("places.search", "B."))
    with pytest.raises(SeedFileError, match="already defined"):
        seed(registry, [tmp_path], include_demo=False, handler_registry=HANDLERS)


def test_missing_seed_path_fails(tmp_path: Path) -> None:
    with pytest.raises(SeedFileError, match="not found"):
        seed_files([tmp_path / "nope"], include_demo=False)


def test_validate_reports_drift_without_writing(registry: MongoRegistry, seeds: Path) -> None:
    seed(registry, [seeds], include_demo=True, handler_registry=HANDLERS)
    assert validate(registry, [seeds], include_demo=True, handler_registry=HANDLERS).ok

    registry.upsert_tool(ToolDefinition(source_id="places.search", description="Edited in DB."))
    registry.upsert_tool(ToolDefinition(source_id="rogue.tool", description="Not seeded."))
    registry.delete_tool("places.near")
    rev = registry.revision()

    report = validate(registry, [seeds], include_demo=True, handler_registry=HANDLERS)
    assert not report.ok
    diffs = {d.source_id: (d.seed, d.db) for d in report.differences}
    assert diffs == {
        "places.search": ("Find places.", "Edited in DB."),
        "places.near": ("Near.", None),
    }
    assert report.not_in_seeds == ["rogue.tool"]
    assert report.without_handler == ["rogue.tool"]
    assert registry.revision() == rev


def test_startup_check(registry: MongoRegistry) -> None:
    registry.upsert_tool(ToolDefinition(source_id="places.search", description="Find."))
    assert check_startup(registry, strict=True, handler_registry=HANDLERS) == []

    registry.upsert_tool(ToolDefinition(source_id="rogue.tool", description="No handler."))
    with pytest.raises(UnknownHandler, match="rogue.tool"):
        check_startup(registry, strict=True, handler_registry=HANDLERS)
    assert check_startup(registry, strict=False, handler_registry=HANDLERS) == ["rogue.tool"]


def test_repo_seeds_match_real_handlers(registry: MongoRegistry) -> None:
    capabilities.load_all()
    report = seed(registry, [REPO_SEEDS], include_demo=True)
    assert "demo.sample_points" in report.created
    assert validate(registry, [REPO_SEEDS], include_demo=True).ok
