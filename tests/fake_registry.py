"""Agent-test doubles for the tool registry: no MongoDB, isolated handlers.

`FakeRegistry` implements Stage 3's `Registry` protocol in memory (it is the
in-memory registry plus a switch to fail like a dead MongoDB). `scale_handlers()`
is an isolated HandlerRegistry holding the ten scale fakes (101-110) and the
real demo handler (9001); `scale_registry()` holds their definitions.
"""

from pymongo.errors import ServerSelectionTimeoutError

from geosearch.registry.catalog import CatalogCache
from geosearch.registry.handlers import HandlerRegistry
from geosearch.registry.models import ToolDefinition
from geosearch.registry.store import InMemoryRegistry
from geosearch.sources.demo import DEMO_SOURCE_ID, PointRow, SamplePointsInput, sample_points
from geosearch.sources.fakes import FAKE_DEFINITIONS, register_fakes

DEMO_DEFINITION = ToolDefinition(
    source_id=DEMO_SOURCE_ID,
    description="Return up to `count` random points inside the current area.",
)


class FakeRegistry(InMemoryRegistry):
    down: bool = False

    def revision(self) -> int:
        if self.down:
            raise ServerSelectionTimeoutError("fake registry is down")
        return super().revision()


def scale_handlers() -> HandlerRegistry:
    reg = register_fakes(HandlerRegistry())
    reg.register(
        source_id=DEMO_SOURCE_ID,
        input_model=SamplePointsInput,
        output_model=PointRow,
        uses_area=True,
    )(sample_points)
    return reg


def scale_registry(*, include: list[int] | None = None) -> FakeRegistry:
    """The scale fixture plus the demo tool; `include` limits it to some ids."""
    definitions = [*FAKE_DEFINITIONS, DEMO_DEFINITION]
    if include is not None:
        definitions = [td for td in definitions if td.source_id in include]
    return FakeRegistry(definitions)


def scale_catalog(registry: FakeRegistry | None = None) -> CatalogCache:
    catalog = CatalogCache(registry or scale_registry(), handler_registry=scale_handlers())
    catalog.refresh_if_changed()
    return catalog


def places_handlers(reg: HandlerRegistry) -> HandlerRegistry:
    """Add the real places handlers (9101, 9102) to an isolated registry."""
    from geosearch.sources import places

    reg.register(
        source_id=places.PLACES_SEARCH, input_model=places.SearchInput,
        output_model=places.PlaceRow, uses_area=True,
    )(places.search)  # fmt: skip
    reg.register(
        source_id=places.PLACES_DETAILS, input_model=places.DetailsInput,
        output_model=places.PlaceDetailsRow, uses_area=True,
    )(places.details)  # fmt: skip
    return reg


def places_catalog() -> CatalogCache:
    """The scale fixture, the demo tool and the places tools, as seeded."""
    import yaml

    seed = yaml.safe_load(open("registry/seeds/places.yaml"))["tools"]
    registry = FakeRegistry([*FAKE_DEFINITIONS, DEMO_DEFINITION,
                             *(ToolDefinition(**t) for t in seed)])  # fmt: skip
    catalog = CatalogCache(registry, handler_registry=places_handlers(scale_handlers()))
    catalog.refresh_if_changed()
    return catalog
