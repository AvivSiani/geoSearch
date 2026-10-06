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
    assert "created: 1  (demo.sample_points)" in capsys.readouterr().out
    assert main(["seed", SEEDS, "--include-demo"]) == 0
    out = capsys.readouterr().out
    assert "unchanged: 1" in out and "revision: 1" in out


def test_demo_needs_the_flag(db: Database) -> None:
    assert main(["seed", SEEDS]) == 0
    assert MongoRegistry(db).list_capabilities() == []


def test_seed_demo_config_includes_demo(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOSEARCH_REGISTRY__SEED_DEMO", "true")
    assert main(["seed", SEEDS]) == 0
    assert MongoRegistry(db).list_capabilities() == ["demo"]


def test_prune(db: Database, capsys: pytest.CaptureFixture) -> None:
    main(["seed", SEEDS, "--include-demo"])
    assert main(["seed", SEEDS, "--prune"]) == 0
    assert "deleted: 1  (demo.sample_points)" in capsys.readouterr().out
    assert MongoRegistry(db).list_capabilities() == []


def test_validate(db: Database, capsys: pytest.CaptureFixture) -> None:
    main(["seed", SEEDS, "--include-demo"])
    capsys.readouterr()
    assert main(["validate", SEEDS, "--include-demo"]) == 0
    MongoRegistry(db).upsert_tool(
        ToolDefinition(source_id="demo.sample_points", description="Edited.")
    )
    assert main(["validate", SEEDS, "--include-demo"]) == 1
    assert "differs: demo.sample_points" in capsys.readouterr().out


def test_list_and_delete(db: Database, capsys: pytest.CaptureFixture) -> None:
    main(["seed", SEEDS, "--include-demo"])
    capsys.readouterr()
    assert main(["list"]) == 0
    assert "demo.sample_points\tReturn up to" in capsys.readouterr().out
    assert main(["delete", "demo.sample_points"]) == 0
    assert main(["delete", "demo.sample_points"]) == 1
    assert "not in the registry" in capsys.readouterr().err


def test_unknown_handler_exits_1_naming_it(
    db: Database, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    (tmp_path / "x.yaml").write_text("tools:\n  - source_id: x.typo\n    description: d\n")
    assert main(["seed", str(tmp_path)]) == 1
    assert "x.typo" in capsys.readouterr().err


def test_usage_error_exits_2() -> None:
    assert main([]) == 2
    assert main(["frobnicate"]) == 2


def test_unreachable_mongo_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("GEOSEARCH_MONGO__URI", "mongodb://localhost:1")
    monkeypatch.setenv("GEOSEARCH_MONGO__SERVER_SELECTION_TIMEOUT_MS", "100")
    assert main(["list"]) == 2
    assert "unreachable" in capsys.readouterr().err
