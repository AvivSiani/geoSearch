"""Stage 3 step 3: the handler allowlist, its shape rules, and the demo tool.

No MongoDB here: handlers are plain code.
"""

import pytest
import shapely
from conftest import C_SHAPE, HANDOFF_POLYGON
from pydantic import BaseModel

from geosearch import sources
from geosearch.config import GeoConfig
from geosearch.geo.area_store import InMemoryAreaStore
from geosearch.geo.ops import AreaOps
from geosearch.registry.handlers import (
    HandlerContext,
    HandlerRegistry,
    InvalidHandler,
    ToolResult,
    UnknownHandler,
    handlers,
)


class In(BaseModel):
    q: str


class Row(BaseModel):
    name: str
    score: float | None = None


def _noop(args: BaseModel, ctx: HandlerContext) -> ToolResult:
    return ToolResult(summary="ok")


def test_register_and_get() -> None:
    reg = HandlerRegistry()
    reg.register(source_id=17, input_model=In, output_model=Row, uses_area=True)(_noop)
    spec = reg.get(17)
    assert spec.func is _noop
    assert spec.source_id == 17 and spec.model_name == "source_17"
    assert spec.input_model is In and spec.output_model is Row
    assert spec.uses_area is True
    assert spec.output_fields == ["name", "score"]
    assert 17 in reg and list(reg) == [17] and len(reg) == 1


def test_unknown_source_id_names_it() -> None:
    with pytest.raises(UnknownHandler, match="99"):
        HandlerRegistry().get(99)


def test_registries_are_isolated() -> None:
    reg = HandlerRegistry()
    reg.register(source_id=17, input_model=In, output_model=Row)(_noop)
    assert 17 not in HandlerRegistry()


def test_duplicate_key_is_rejected() -> None:
    reg = HandlerRegistry()
    reg.register(source_id=17, input_model=In, output_model=Row)(_noop)
    with pytest.raises(InvalidHandler, match="duplicate"):
        reg.register(source_id=17, input_model=In, output_model=Row)


@pytest.mark.parametrize("source_id", [0, -3, "17", True, 1.0])
def test_invalid_source_id_is_rejected(source_id: object) -> None:
    with pytest.raises(InvalidHandler, match="positive int"):
        HandlerRegistry().register(source_id=source_id, input_model=In, output_model=Row)


@pytest.mark.parametrize("field", ["area_id", "wkt", "geometry", "runtime"])
def test_reserved_input_fields_are_rejected(field: str) -> None:
    bad = type("Bad", (BaseModel,), {"__annotations__": {field: str}})
    with pytest.raises(InvalidHandler, match="reserved"):
        HandlerRegistry().register(source_id=17, input_model=bad, output_model=Row)


@pytest.mark.parametrize("field", ["wkt", "geometry"])
def test_output_models_never_carry_geometry(field: str) -> None:
    bad = type("Bad", (BaseModel,), {"__annotations__": {field: str}})
    with pytest.raises(InvalidHandler, match="reserved"):
        HandlerRegistry().register(source_id=17, input_model=In, output_model=bad)


class Nested(BaseModel):
    inner: Row


class NestedList(BaseModel):
    rows: list[Row]


class NestedOptional(BaseModel):
    row: Row | None = None


@pytest.mark.parametrize("model", [Nested, NestedList, NestedOptional])
def test_nested_models_are_rejected(model: type[BaseModel]) -> None:
    with pytest.raises(InvalidHandler, match="nested"):
        HandlerRegistry().register(source_id=17, input_model=model, output_model=Row)
    with pytest.raises(InvalidHandler, match="nested"):
        HandlerRegistry().register(source_id=17, input_model=In, output_model=model)


def test_models_must_be_pydantic() -> None:
    with pytest.raises(InvalidHandler, match="BaseModel"):
        HandlerRegistry().register(source_id=17, input_model=dict, output_model=Row)  # type: ignore[arg-type]


def test_flat_lists_of_scalars_are_allowed() -> None:
    class Tags(BaseModel):
        tags: list[str]

    HandlerRegistry().register(source_id=17, input_model=Tags, output_model=Tags)


# --- loading and the demo tool (9001) -------------------------------------------------


def test_load_all_registers_demo_and_is_repeatable() -> None:
    sources.load_all()
    sources.load_all()
    spec = handlers.get(9001)
    assert spec.model_name == "source_9001"
    assert spec.uses_area is True
    assert spec.output_fields == ["lon", "lat"]


def _ctx(ops: AreaOps, area_id: str, cfg: GeoConfig) -> HandlerContext:
    return HandlerContext(area_id=area_id, area_ops=ops, cfg=cfg, turn=1, source_id=9001)


def _run_demo(ops: AreaOps, area_id: str, cfg: GeoConfig, **args: int) -> ToolResult:
    sources.load_all()
    spec = handlers.get(9001)
    return spec.func(spec.input_model(**args), _ctx(ops, area_id, cfg))


def test_demo_points_are_inside_and_deterministic(
    store: InMemoryAreaStore, ops: AreaOps, cfg: GeoConfig
) -> None:
    area_id = store.put(shapely.from_wkt(C_SHAPE))
    first = _run_demo(ops, area_id, cfg, count=20, seed=7)
    again = _run_demo(ops, area_id, cfg, count=20, seed=7)
    other = _run_demo(ops, area_id, cfg, count=20, seed=8)

    assert first.artifact == again.artifact != other.artifact
    assert first.data == {"count": 20}
    assert all(ops.contains(area_id, p["lon"], p["lat"]) for p in first.artifact)
    for row in first.artifact:
        handlers.get(9001).output_model.model_validate(row)


def test_demo_input_bounds() -> None:
    sources.load_all()
    model = handlers.get(9001).input_model
    for bad in (0, 51):
        with pytest.raises(ValueError):
            model(count=bad)
    assert model(count=3).seed == 0


def test_demo_summary_mentions_count(
    store: InMemoryAreaStore, ops: AreaOps, cfg: GeoConfig
) -> None:
    area_id = store.put(shapely.from_wkt(HANDOFF_POLYGON))
    result = _run_demo(ops, area_id, cfg, count=3)
    assert "3" in result.summary and len(result.artifact) == 3
