"""ToolDefinition validation: a positive int source_id and a bounded description."""

import pytest
from pydantic import ValidationError

from geosearch.registry.models import MAX_DESCRIPTION_CHARS, ToolDefinition, model_name_for


def _td(source_id: object = 9001, description: str = "Does it.") -> ToolDefinition:
    return ToolDefinition(source_id=source_id, description=description)


def test_model_name_is_generated_from_the_id() -> None:
    assert model_name_for(17) == "source_17"


@pytest.mark.parametrize("source_id", [0, -1, "17", 1.5, True, None, "demo.sample_points"])
def test_invalid_source_ids_are_rejected(source_id: object) -> None:
    with pytest.raises(ValidationError):
        _td(source_id)


def test_description_bounds() -> None:
    _td(description="x" * MAX_DESCRIPTION_CHARS)
    with pytest.raises(ValidationError):
        _td(description="x" * (MAX_DESCRIPTION_CHARS + 1))
    with pytest.raises(ValidationError):
        _td(description="   ")


def test_only_source_id_and_description_are_accepted() -> None:
    with pytest.raises(ValidationError):
        ToolDefinition(source_id=1, description="d", name="x")
    assert _td().model_dump() == {"source_id": 9001, "description": "Does it."}


def test_definitions_are_immutable() -> None:
    td = _td()
    with pytest.raises(ValidationError):
        td.description = "changed"
