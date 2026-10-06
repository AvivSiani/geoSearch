"""Stage 4 step 1: DisclosureConfig, the loaded_tools reducer, and the scale fixture."""

import pytest
from fake_registry import FakeRegistry, scale_catalog, scale_handlers, scale_registry
from pydantic import ValidationError

from geosearch.agent.state import merge_loaded_tools
from geosearch.config import DisclosureConfig, GeoConfig
from geosearch.registry.handlers import HandlerRegistry
from geosearch.sources.fakes import FAKES, register_fakes


def test_disclosure_defaults(cfg: GeoConfig) -> None:
    assert cfg.disclosure.max_loaded_tools is None
    assert cfg.disclosure.catalog_warn_tokens == 600


def test_disclosure_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOSEARCH_DISCLOSURE__MAX_LOADED_TOOLS", "3")
    assert GeoConfig(_env_file=None).disclosure.max_loaded_tools == 3


@pytest.mark.parametrize("bad", [{"max_loaded_tools": 0}, {"catalog_warn_tokens": 0}])
def test_invalid_disclosure_config_fails_fast(bad: dict) -> None:
    with pytest.raises(ValidationError):
        DisclosureConfig(**bad)


def test_loaded_tools_reducer_is_an_ordered_union() -> None:
    assert merge_loaded_tools(None, [17]) == [17]
    assert merge_loaded_tools([17, 9001], [9001, 101]) == [17, 9001, 101]
    assert merge_loaded_tools([17], []) == [17]  # a turn's [] never wipes earlier loads
    assert merge_loaded_tools([17], None) == [17]


def test_scale_fixture_has_distinct_descriptions_and_valid_handlers() -> None:
    catalog = scale_catalog()
    ids = [e.source_id for e in catalog.tools]
    assert ids == [*range(101, 111), 9001]
    descriptions = [e.description for e in catalog.tools]
    assert len(set(descriptions)) == len(descriptions)
    for source_id in ids:
        assert catalog.output_fields(source_id)  # every fake declares its returned fields


def test_fakes_are_not_on_the_production_allowlist() -> None:
    from geosearch import sources
    from geosearch.registry.handlers import handlers

    sources.load_all()
    assert not any(source_id in handlers for source_id in FAKES)


def test_fakes_register_into_any_registry() -> None:
    reg = register_fakes(HandlerRegistry())
    assert list(reg) == list(range(101, 111))
    assert 9001 in scale_handlers()


def test_fake_registry_can_go_down() -> None:
    registry = scale_registry(include=[101])
    assert isinstance(registry, FakeRegistry) and registry.revision() == 0
    registry.down = True
    with pytest.raises(Exception, match="down"):
        registry.revision()


@pytest.mark.parametrize("source_id", sorted(FAKES))
def test_every_fake_result_matches_its_output_model(source_id: int, cfg: GeoConfig, ops) -> None:
    from geosearch.registry.handlers import HandlerContext
    from geosearch.registry.resolver import check_output

    spec = scale_handlers().get(source_id)
    ctx = HandlerContext(area_id="a", area_ops=ops, cfg=cfg, turn=1, source_id=source_id)
    assert check_output(spec, spec.func(spec.input_model(), ctx)) is None
