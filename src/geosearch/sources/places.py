"""Places tools (Stage 5): search (9101) and details (9102).

Both use the configured `PlacesProvider` (Google live, or replayed fixtures)
and send it the area's bbox, never the area. The resolver then does what
handlers must not have to remember: drop rows outside the polygon, turn each
row's provider `id` into a short item id, and hand the rows to the summarizer.
So a handler here only maps provider places to flat rows.

Details takes the short item id the model saw in a search summary (`i3`);
`ctx.item_ref` turns it back into the provider id, which the model never sees.
"""

from typing import Literal

from pydantic import BaseModel, Field

from geosearch.providers.places import PlaceSearch, ProviderPlace, get_places_provider
from geosearch.registry.handlers import HandlerContext, ToolResult, register_handler

PLACES_SEARCH = 9101
PLACES_DETAILS = 9102


class SearchInput(BaseModel):
    query: str = Field(
        min_length=1, max_length=200,
        description="What to look for, e.g. 'asian restaurant' or 'quiet cafe'.",
    )  # fmt: skip
    min_rating: float | None = Field(
        default=None, ge=1, le=5, description="Only places rated at least this (1-5)."
    )
    max_price: Literal["inexpensive", "moderate", "expensive", "very_expensive"] | None = Field(
        default=None, description="Only places at or below this price level."
    )
    open_now: bool = Field(default=False, description="Only places open right now.")


class DetailsInput(BaseModel):
    item_id: str = Field(description="An item id from a places search result, e.g. 'i3'.")


class PlaceRow(BaseModel):
    id: str
    name: str
    address: str | None
    lon: float
    lat: float
    rating: float | None
    rating_count: int | None
    price_level: str | None
    primary_type: str | None


class PlaceDetailsRow(BaseModel):
    id: str
    name: str
    address: str | None
    lon: float
    lat: float
    rating: float | None
    price_level: str | None
    phone: str | None
    website: str | None
    opening_hours: str | None
    editorial_summary: str | None


def _row(place: ProviderPlace, model: type[BaseModel]) -> dict:
    """Exactly the declared fields: the resolver rejects missing or extra ones."""
    return place.model_dump(include=set(model.model_fields))


@register_handler(
    source_id=PLACES_SEARCH, input_model=SearchInput, output_model=PlaceRow, uses_area=True
)
def search(args: SearchInput, ctx: HandlerContext) -> ToolResult:
    assert ctx.area_id is not None  # uses_area=True guarantees it
    provider = get_places_provider(ctx.cfg.places)
    places = provider.search(
        PlaceSearch(
            text=args.query,
            language=ctx.language,
            rect=ctx.area_ops.bbox(ctx.area_id),
            max_results=ctx.cfg.places.max_results,
            min_rating=args.min_rating,
            max_price=args.max_price,
            open_now=args.open_now,
        )
    )
    return ToolResult(
        summary=f"Found {len(places)} place(s) for '{args.query}'.",
        artifact=[_row(p, PlaceRow) for p in places],
    )


@register_handler(
    source_id=PLACES_DETAILS, input_model=DetailsInput, output_model=PlaceDetailsRow, uses_area=True
)
def details(args: DetailsInput, ctx: HandlerContext) -> ToolResult:
    ref = ctx.item_ref(args.item_id)
    if ref is None:
        return ToolResult(
            summary=f"Unknown item id {args.item_id!r}. Use an id from a places search result."
        )
    place = get_places_provider(ctx.cfg.places).details(ref, ctx.language)
    if place is None:
        return ToolResult(summary=f"No details found for {args.item_id}.")
    return ToolResult(
        summary=f"Details for {args.item_id}.", data=_row(place, PlaceDetailsRow)
    )
