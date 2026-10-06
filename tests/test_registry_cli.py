"""Stage 3 step 7: the geosearch-registry CLI, against a throwaway MongoDB."""

from pathlib import Path

import pytest
from pymongo.database import Database

from geosearch.registry.cli import main
from geosearch.registry.models import ToolDefinition
from geosearch.registry.store import MongoRegistry

SEEDS = str(Path(__file__).parent.parent / "registry" / "seeds")


@pytest.fixture
def db(mongo_db: Database, monkeypatch: pytest.MonkeyPatch) -> Database:
    monkeypatch.setenv("GEOSEARCH_MONGO__DATABASE", mongo_db.name)
    return mongo_db


def test_seed_is_idempotent(db: Database, capsys: pytest.CaptureFixture) -> None:
    assert main(["seed", SEEDS, "--include-demo"]) == 0
    assert "created: 3  (9001, 9101, 9102)" in capsys.readouterr().out
    assert main(["seed", SEEDS, "--include-demo"]) == 0
    out = capsys.readouterr().out
    assert "unchanged: 3" in out and "revision: 3" in out


def test_demo_needs_the_flag(db: Database) -> None:
    assert main(["seed", SEEDS]) == 0
    assert [t.source_id for t in MongoRegistry(db).list_tools()] == [9101, 9102]  # no 9001


def test_seed_demo_config_includes_demo(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOSEARCH_REGISTRY__SEED_DEMO", "true")
    assert main(["seed", SEEDS]) == 0
    assert [t.source_id for t in MongoRegistry(db).list_tools()] == [9001, 9101, 9102]


def test_prune(db: Database, capsys: pytest.CaptureFixture) -> None:
    main(["seed", SEEDS, "--include-demo"])
    assert main(["seed", SEEDS, "--prune"]) == 0
    assert "deleted: 1  (9001)" in capsys.readouterr().out
    assert [t.source_id for t in MongoRegistry(db).list_tools()] == [9101, 9102]


def test_validate(db: Database, capsys: pytest.CaptureFixture) -> None:
    main(["seed", SEEDS, "--include-demo"])
    capsys.readouterr()
    assert main(["validate", SEEDS, "--include-demo"]) == 0
    MongoRegistry(db).upsert_tool(ToolDefinition(source_id=9001, description="Edited."))
    assert main(["validate", SEEDS, "--include-demo"]) == 1
    assert "differs: 9001" in capsys.readouterr().out


def test_list_and_delete(db: Database, capsys: pytest.CaptureFixture) -> None:
    main(["seed", SEEDS, "--include-demo"])
    capsys.readouterr()
    assert main(["list"]) == 0
    assert "9001\tReturn up to" in capsys.readouterr().out
    assert main(["delete", "9001"]) == 0
    assert main(["delete", "9001"]) == 1
    assert "not in the registry" in capsys.readouterr().err


def test_unknown_handler_exits_1_naming_it(
    db: Database, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    (tmp_path / "x.yaml").write_text("tools:\n  - source_id: 4242\n    description: d\n")
    assert main(["seed", str(tmp_path)]) == 1
    assert "4242" in capsys.readouterr().err


def test_usage_error_exits_2() -> None:
    assert main([]) == 2
    assert main(["frobnicate"]) == 2
    assert main(["delete", "demo.sample_points"]) == 2  # ids are ints


def test_unreachable_mongo_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("GEOSEARCH_MONGO__URI", "mongodb://localhost:1")
    monkeypatch.setenv("GEOSEARCH_MONGO__SERVER_SELECTION_TIMEOUT_MS", "100")
    assert main(["list"]) == 2
    assert "unreachable" in capsys.readouterr().err


def test_validate_warns_about_handler_without_definition(
    db: Database, capsys: pytest.CaptureFixture
) -> None:
    main(["seed", SEEDS])
    assert main(["validate", SEEDS]) == 0
    assert "warning: handler without a DB definition: 9001" in capsys.readouterr().out


def test_legacy_string_id_document_is_reported(
    db: Database, capsys: pytest.CaptureFixture
) -> None:
    db.tools.insert_one({"_id": "demo.sample_points", "description": "old"})
    assert main(["list"]) == 1
    assert "invalid stored definition" in capsys.readouterr().err
