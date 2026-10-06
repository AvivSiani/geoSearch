"""Stage 3 step 7: registry wiring in create_app — startup policies and the
per-request catalog refresh."""

from pathlib import Path

import pytest
from conftest import HANDOFF_POLYGON
from fastapi.testclient import TestClient
from pymongo.database import Database
from scripted_model import ScriptedChatModel, ai

from geosearch.api.app import create_app
from geosearch.config import GeoConfig, MongoConfig, RegistryConfig
from geosearch.registry.cli import main
from geosearch.registry.handlers import UnknownHandler
from geosearch.registry.models import ToolDefinition
from geosearch.registry.startup import RegistryUnavailable
from geosearch.registry.store import InMemoryRegistry

SEEDS = str(Path(__file__).parent.parent / "registry" / "seeds")
DEAD_MONGO = MongoConfig(uri="mongodb://localhost:1", server_selection_timeout_ms=100)


def _model() -> ScriptedChatModel:
    return ScriptedChatModel(responses=[ai("ok")])


def _cfg(**registry: object) -> GeoConfig:
    cfg = GeoConfig(_env_file=None)
    return cfg.model_copy(update={"registry": RegistryConfig(**registry)})


def test_injected_registry_sets_the_catalog() -> None:
    registry = InMemoryRegistry(
        [ToolDefinition(source_id=9001, description="Points.")]
    )
    app = create_app(GeoConfig(_env_file=None), model=_model(), tool_registry=registry)
    assert [e.source_id for e in app.state.catalog.tools] == [9001]


def test_unreachable_and_required_fails_startup() -> None:
    cfg = GeoConfig(_env_file=None).model_copy(update={"mongo": DEAD_MONGO})
    with pytest.raises(RegistryUnavailable, match="docker compose up -d"):
        create_app(cfg, model=_model())


def test_unreachable_and_optional_starts_empty(caplog: pytest.LogCaptureFixture) -> None:
    cfg = _cfg(required=False).model_copy(update={"mongo": DEAD_MONGO})
    app = create_app(cfg, model=_model())
    assert app.state.catalog.tools == ()
    assert "MongoDB unreachable" in caplog.text


def test_unknown_stored_source_id_fails_startup_by_name() -> None:
    registry = InMemoryRegistry([ToolDefinition(source_id=666, description="d")])
    with pytest.raises(UnknownHandler, match="666"):
        create_app(GeoConfig(_env_file=None), model=_model(), tool_registry=registry)


def test_lenient_startup_skips_unknown_source_ids() -> None:
    registry = InMemoryRegistry(
        [
            ToolDefinition(source_id=666, description="d"),
            ToolDefinition(source_id=9001, description="Points."),
        ]
    )
    app = create_app(_cfg(strict_startup=False), model=_model(), tool_registry=registry)
    assert [e.source_id for e in app.state.catalog.tools] == [9001]


def test_cli_change_is_picked_up_on_next_request(
    mongo_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEOSEARCH_MONGO__DATABASE", mongo_db.name)
    app = create_app(GeoConfig(_env_file=None), model=_model())
    client = TestClient(app)
    body = {"wkt": HANDOFF_POLYGON, "prompt": "q"}
    assert client.post("/v1/requests", json=body).status_code == 200
    assert app.state.catalog.tools == ()

    assert main(["seed", SEEDS, "--include-demo"]) == 0
    assert app.state.catalog.tools == ()  # nothing changes between requests
    assert client.post("/v1/requests", json=body).status_code == 200
    assert [e.source_id for e in app.state.catalog.tools] == [9001]
    assert app.state.catalog.tool(9001).name == "source_9001"

    assert main(["delete", "9001"]) == 0
    client.post("/v1/requests", json=body)
    assert app.state.catalog.tools == ()
