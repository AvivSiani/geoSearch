"""Stage 3 step 3: the handler allowlist, its shape rules, and the demo tool.

No MongoDB here: handlers are plain code.
"""

import pytest
import shapely
from conftest import C_SHAPE, HANDOFF_POLYGON
from pydantic import BaseModel

from geosearch import capabilities
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
    reg.register("x.find", input_model=In, output_model=Row, uses_area=True)(_noop)
    spec = reg.get("x.find")
    assert spec.func is _noop
    assert spec.uses_area is True
    assert spec.output_fields == ["name", "score"]
    assert "x.find" in reg and list(reg) == ["x.find"] and len(reg) == 1


def test_unknown_source_id_names_it() -> None:
    with pytest.raises(UnknownHandler, match="x.missing"):
        HandlerRegistry().get("x.missing")


def test_registries_are_isolated() -> None:
    reg = HandlerRegistry()
    reg.register("x.find", input_model=In, output_model=Row)(_noop)
    assert "x.find" not in HandlerRegistry()


def test_duplicate_key_is_rejected() -> None:
    reg = HandlerRegistry()
    reg.register("x.find", input_model=In, output_model=Row)(_noop)
    with pytest.raises(InvalidHandler, match="duplicate"):
        reg.register("x.find", input_model=In, output_model=Row)


def test_invalid_source_id_is_rejected() -> None:
    with pytest.raises(InvalidHandler, match="invalid source_id"):
        HandlerRegistry().register("X.find", input_model=In, output_model=Row)


@pytest.mark.parametrize("field", ["area_id", "wkt", "geometry", "runtime"])
def test_reserved_input_fields_are_rejected(field: str) -> None:
    bad = type("Bad", (BaseModel,), {"__annotations__": {field: str}})
    with pytest.raises(InvalidHandler, match="reserved"):
        HandlerRegistry().register("x.find", input_model=bad, output_model=Row)


@pytest.mark.parametrize("field", ["wkt", "geometry"])
def test_output_models_never_carry_geometry(field: str) -> None:
    bad = type("Bad", (BaseModel,), {"__annotations__": {field: str}})
    with pytest.raises(InvalidHandler, match="reserved"):
        HandlerRegistry().register("x.find", input_model=In, output_model=bad)


class Nested(BaseModel):
    inner: Row


class NestedList(BaseModel):
    rows: list[Row]


class NestedOptional(BaseModel):
    row: Row | None = None


@pytest.mark.parametrize("model", [Nested, NestedList, NestedOptional])
def test_nested_models_are_rejected(model: type[BaseModel]) -> None:
    with pytest.raises(InvalidHandler, match="nested"):
        HandlerRegistry().register("x.find", input_model=model, output_model=Row)
    with pytest.raises(InvalidHandler, match="nested"):
        HandlerRegistry().register("x.find", input_model=In, output_model=model)


def test_models_must_be_pydantic() -> None:
    with pytest.raises(InvalidHandler, match="BaseModel"):
        HandlerRegistry().register("x.find", input_model=dict, output_model=Row)  # type: ignore[arg-type]


def test_flat_lists_of_scalars_are_allowed() -> None:
    class Tags(BaseModel):
        tags: list[str]

    HandlerRegistry().register("x.find", input_model=Tags, output_model=Tags)


# --- loading and the demo tool -------------------------------------------------


def test_load_all_registers_demo_and_is_repeatable() -> None:
    capabilities.load_all()
    capabilities.load_all()
    spec = handlers.get("demo.sample_points")
    assert spec.uses_area is True
    assert spec.output_fields == ["lon", "lat"]


def _ctx(ops: AreaOps, area_id: str, cfg: GeoConfig) -> HandlerContext:
    return HandlerContext(area_id=area_id, area_ops=ops, cfg=cfg, turn=1, capability="demo")


def _run_demo(ops: AreaOps, area_id: str, cfg: GeoConfig, **args: int) -> ToolResult:
    capabilities.load_all()
    spec = handlers.get("demo.sample_points")
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
        handlers.get("demo.sample_points").output_model.model_validate(row)


def test_demo_input_bounds() -> None:
    capabilities.load_all()
    model = handlers.get("demo.sample_points").input_model
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
