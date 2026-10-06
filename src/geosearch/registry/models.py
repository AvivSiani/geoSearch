"""The stored tool definition: a `source_id` and a model-facing `description`.

Why so little: the database stores *what the model is told*, never code or
behavior. Everything executable — input model, output model, `uses_area` — is
owned by the handler registration in code (registry/handlers.py), so a database
write can change wording but can never add or reshape a running tool.
"""

import re

from pydantic import BaseModel, ConfigDict, Field

SOURCE_ID_PATTERN = r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"
MAX_DESCRIPTION_CHARS = 200
_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class ToolDefinition(BaseModel):
    """One registry entry. `source_id` is both the tool's identity and its
    handler key; its prefix is the capability (`demo.sample_points` -> `demo`)."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    source_id: str = Field(pattern=SOURCE_ID_PATTERN, max_length=64)
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_CHARS)

    @property
    def capability(self) -> str:
        return self.source_id.split(".", 1)[0]

    @property
    def model_name(self) -> str:
        """The name the model calls. Providers reject dots in tool names, so
        `.` becomes `_`; the pattern is the strictest common provider rule."""
        name = self.source_id.replace(".", "_")
        if not _MODEL_NAME_RE.match(name):  # unreachable given the source_id pattern
            raise ValueError(f"invalid model tool name: {name!r}")
        return name
