"""The handler allowlist: the only code a registry definition can run.

A definition in MongoDB is just a numeric `source_id` and a description. It
becomes executable only if code registered a handler under that exact
`source_id` (Stage 3 invariant 13): one handler per `source_id`, no grouping.
The registration, not the database, declares the input model, the output model
and whether the handler needs the area; the model-facing name is generated.

Registration enforces the shape rules up front, so a bad handler fails at
import time instead of mid-conversation:
  - input models never carry an area (`area_id`, `wkt`, `geometry`): the area
    is injected from agent state, never chosen by the model (invariant 2);
  - `runtime` is reserved too, because the resolver uses that parameter name
    for the injected ToolRuntime;
  - input and output models are flat: no nested models, so the compacted
    schema the model sees stays small and the output check stays simple.
"""

import typing
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from geosearch.config import GeoConfig
from geosearch.geo.ops import AreaOps
from geosearch.registry.models import model_name_for
from geosearch.request.language import Language

RESERVED_INPUT_FIELDS = frozenset({"area_id", "wkt", "geometry", "runtime"})
RESERVED_OUTPUT_FIELDS = frozenset({"wkt", "geometry"})  # the model never sees geometry


class HandlerError(Exception):
    """Base class for handler registration and lookup errors."""


class UnknownHandler(HandlerError):
    """A `source_id` has no registered handler: it is not on the allowlist."""

    def __init__(self, source_id: int):
        super().__init__(f"no handler registered for source_id {source_id}")
        self.source_id = source_id


class InvalidHandler(HandlerError):
    """A registration breaks a shape rule (duplicate, reserved field, nesting)."""


def _no_items(item_id: str) -> str | None:
    return None


@dataclass(frozen=True)
class HandlerContext:
    """What a handler may use besides its validated arguments. `area_id` is set
    only for handlers registered with `uses_area=True`. `language` is the turn's
    request language, detected by code (e.g. a provider's result language).
    `item_ref` turns a short item id the model passed (`i3`) back into the
    provider's id, or None if no such item exists in this conversation."""

    area_id: str | None
    area_ops: AreaOps
    cfg: GeoConfig
    turn: int
    source_id: int
    language: Language = "en"
    item_ref: Callable[[str], str | None] = _no_items


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
class RegisteredHandler:
    """A registered handler and the contract code declares for it."""

    source_id: int
    model_name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    uses_area: bool
    func: Handler

    @property
    def output_fields(self) -> list[str]:
        return list(self.output_model.model_fields)

    @property
    def has_coordinates(self) -> bool:
        """Rows carry `lon`/`lat`: the resolver drops those outside the area."""
        return {"lon", "lat"} <= set(self.output_model.model_fields)

    @property
    def yields_items(self) -> bool:
        """Rows carry the provider's `id`: the resolver turns them into items."""
        return "id" in self.output_model.model_fields


def _contains_model(annotation: Any) -> bool:
    """True if a BaseModel appears anywhere in the annotation (list[Row], Row | None...)."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return True
    return any(_contains_model(arg) for arg in typing.get_args(annotation))


def _check_model(source_id: int, role: str, model: Any, reserved: frozenset[str]) -> None:
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
        self._specs: dict[int, RegisteredHandler] = {}

    def register(
        self,
        *,
        source_id: int,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
        uses_area: bool = False,
    ) -> Callable[[Handler], Handler]:
        if type(source_id) is not int or source_id <= 0:
            raise InvalidHandler(f"source_id must be a positive int, got {source_id!r}")
        if source_id in self._specs:
            raise InvalidHandler(f"duplicate handler for source_id {source_id}")
        _check_model(source_id, "input_model", input_model, RESERVED_INPUT_FIELDS)
        _check_model(source_id, "output_model", output_model, RESERVED_OUTPUT_FIELDS)
        id_field = output_model.model_fields.get("id")
        if id_field is not None and id_field.annotation is not str:
            raise InvalidHandler(f"{source_id}: output field 'id' (a provider id) must be str")

        def decorator(func: Handler) -> Handler:
            if source_id in self._specs:  # registered between call and decoration
                raise InvalidHandler(f"duplicate handler for source_id {source_id}")
            self._specs[source_id] = RegisteredHandler(
                source_id=source_id,
                model_name=model_name_for(source_id),
                input_model=input_model,
                output_model=output_model,
                uses_area=uses_area,
                func=func,
            )
            return func

        return decorator

    def get(self, source_id: int) -> RegisteredHandler:
        try:
            return self._specs[source_id]
        except KeyError:
            raise UnknownHandler(source_id) from None

    def __contains__(self, source_id: object) -> bool:
        return source_id in self._specs

    def __iter__(self) -> Iterator[int]:
        return iter(sorted(self._specs))

    def __len__(self) -> int:
        return len(self._specs)


handlers = HandlerRegistry()
register_handler = handlers.register
