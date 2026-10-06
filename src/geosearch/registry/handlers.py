"""The handler allowlist: the only code a registry definition can run.

A definition in MongoDB is just a `source_id` and a description. It becomes
executable only if code registered a handler under that exact `source_id`
(Stage 3 invariant 1). The registration, not the database, declares the input
model, the output model and whether the handler needs the area.

Registration enforces the shape rules up front, so a bad handler fails at
import time instead of mid-conversation:
  - input models never carry an area (`area_id`, `wkt`, `geometry`): the area
    is injected from agent state, never chosen by the model (invariant 2);
  - `runtime` is reserved too, because the resolver uses that parameter name
    for the injected ToolRuntime;
  - input and output models are flat: no nested models, so the compacted
    schema the model sees stays small and the output check stays simple.
"""

import re
import typing
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from geosearch.config import GeoConfig
from geosearch.geo.ops import AreaOps
from geosearch.registry.models import SOURCE_ID_PATTERN

RESERVED_INPUT_FIELDS = frozenset({"area_id", "wkt", "geometry", "runtime"})
RESERVED_OUTPUT_FIELDS = frozenset({"wkt", "geometry"})  # the model never sees geometry
_SOURCE_ID_RE = re.compile(SOURCE_ID_PATTERN)


class HandlerError(Exception):
    """Base class for handler registration and lookup errors."""


class UnknownHandler(HandlerError):
    """A `source_id` has no registered handler: it is not on the allowlist."""

    def __init__(self, source_id: str):
        super().__init__(f"no handler registered for source_id {source_id!r}")
        self.source_id = source_id


class InvalidHandler(HandlerError):
    """A registration breaks a shape rule (duplicate, reserved field, nesting)."""


@dataclass(frozen=True)
class HandlerContext:
    """What a handler may use besides its validated arguments. `area_id` is set
    only for handlers registered with `uses_area=True`."""

    area_id: str | None
    area_ops: AreaOps
    cfg: GeoConfig
    turn: int
    capability: str


@dataclass
class ToolResult:
    """A handler's return value. `summary` is short prose for the model; `data`
    is small structured output shown inline; `artifact` is the bulk result,
    which the resolver writes to a working-memory file instead of the message."""

    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    artifact: Any | None = None


Handler = Callable[[Any, HandlerContext], ToolResult]


@dataclass(frozen=True)
class HandlerSpec:
    """A registered handler and the contract code declares for it."""

    source_id: str
    func: Handler
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    uses_area: bool

    @property
    def output_fields(self) -> list[str]:
        return list(self.output_model.model_fields)


def _contains_model(annotation: Any) -> bool:
    """True if a BaseModel appears anywhere in the annotation (list[Row], Row | None...)."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return True
    return any(_contains_model(arg) for arg in typing.get_args(annotation))


def _check_model(source_id: str, role: str, model: Any, reserved: frozenset[str]) -> None:
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise InvalidHandler(f"{source_id}: {role} must be a pydantic BaseModel subclass")
    for name, info in model.model_fields.items():
        if name in reserved:
            raise InvalidHandler(f"{source_id}: {role} field {name!r} is reserved")
        if _contains_model(info.annotation):
            raise InvalidHandler(f"{source_id}: {role} field {name!r} is nested; models are flat")


class HandlerRegistry:
    """An allowlist of handlers keyed by `source_id`. An object rather than a
    module dict so tests can build isolated registries."""

    def __init__(self) -> None:
        self._specs: dict[str, HandlerSpec] = {}

    def register(
        self,
        source_id: str,
        *,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
        uses_area: bool = False,
    ) -> Callable[[Handler], Handler]:
        if not _SOURCE_ID_RE.match(source_id):
            raise InvalidHandler(f"invalid source_id {source_id!r}")
        if source_id in self._specs:
            raise InvalidHandler(f"duplicate handler for source_id {source_id!r}")
        _check_model(source_id, "input_model", input_model, RESERVED_INPUT_FIELDS)
        _check_model(source_id, "output_model", output_model, RESERVED_OUTPUT_FIELDS)

        def decorator(func: Handler) -> Handler:
            if source_id in self._specs:  # registered between call and decoration
                raise InvalidHandler(f"duplicate handler for source_id {source_id!r}")
            self._specs[source_id] = HandlerSpec(
                source_id, func, input_model, output_model, uses_area
            )
            return func

        return decorator

    def get(self, source_id: str) -> HandlerSpec:
        try:
            return self._specs[source_id]
        except KeyError:
            raise UnknownHandler(source_id) from None

    def __contains__(self, source_id: object) -> bool:
        return source_id in self._specs

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._specs))

    def __len__(self) -> int:
        return len(self._specs)


handlers = HandlerRegistry()
register_handler = handlers.register
