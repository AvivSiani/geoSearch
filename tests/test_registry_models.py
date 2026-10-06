"""Stage 3 step 2: ToolDefinition validation."""

import pytest
from pydantic import ValidationError

from geosearch.registry.models import MAX_DESCRIPTION_CHARS, ToolDefinition


def _td(source_id: str = "demo.sample_points", description: str = "Does it.") -> ToolDefinition:
    return ToolDefinition(source_id=source_id, description=description)


def test_capability_and_model_name() -> None:
    td = _td("places.search_nearby")
    assert td.capability == "places"
    assert td.model_name == "places_search_nearby"


@pytest.mark.parametrize(
    "source_id",
    ["demo", "Demo.points", "demo.Points", "demo.points.extra", "1demo.points", "demo.",
     ".points", "demo-x.points", "demo.sample points", ""],
)
def test_invalid_source_ids_are_rejected(source_id: str) -> None:
    with pytest.raises(ValidationError):
        _td(source_id)


def test_source_id_allows_digits_and_underscores() -> None:
    assert _td("geo_2.points_v2").model_name == "geo_2_points_v2"


def test_model_name_length_is_bounded() -> None:
    with pytest.raises(ValidationError):
        _td("a" * 40 + "." + "b" * 40)


def test_description_bounds() -> None:
    _td(description="x" * MAX_DESCRIPTION_CHARS)
    with pytest.raises(ValidationError):
        _td(description="x" * (MAX_DESCRIPTION_CHARS + 1))
    with pytest.raises(ValidationError):
        _td(description="   ")


def test_only_source_id_and_description_are_accepted() -> None:
    with pytest.raises(ValidationError):
        ToolDefinition(source_id="demo.x", description="d", version=2)
    assert set(_td().model_dump()) == {"source_id", "description"}


def test_definitions_are_immutable() -> None:
    td = _td()
    with pytest.raises(ValidationError):
        td.description = "changed"
