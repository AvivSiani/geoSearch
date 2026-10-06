"""The stored tool definition: a numeric `source_id` and a model-facing `description`.

Why so little: the database stores *what the model is told*, never code or
behavior. Everything executable — input model, output model, `uses_area` — is
owned by the handler registration in code (registry/handlers.py), so a database
write can change wording but can never add or reshape a running tool.

There is no grouping and no name: the agent picks tools from their descriptions
alone, so each description must say clearly and distinctly what the tool does.
The model-facing name (`source_<id>`) is generated from the id.
"""

from pydantic import BaseModel, ConfigDict, Field

MAX_DESCRIPTION_CHARS = 200


class ToolDefinition(BaseModel):
    """One registry entry; `source_id` is both its identity and its handler key."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    source_id: int = Field(gt=0, strict=True)
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_CHARS)


def model_name_for(source_id: int) -> str:
    """The name the model calls a tool by. Generated, never stored."""
    return f"source_{source_id}"
