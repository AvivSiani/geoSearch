"""The places provider behind tools 9101 (search) and 9102 (details).

Google Places API (New) is the demo live provider; `ReplayPlacesProvider` serves
recorded responses so pytest and the evals never need a key or the network
(Stage 5, D1). Both speak the same raw Google JSON, and `parse_place` maps it to
one flat `ProviderPlace`, so replayed and live results go through identical code.

The provider gets a rectangle, never the area itself: the handler passes the
area's bbox as Google's `locationRestriction`. That keeps the request small, but
a rectangle is not the polygon (and Google's restriction is not exact), which is
why the resolver filters every row against the real polygon afterwards.
"""

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel

from geosearch.config import PlacesConfig

log = logging.getLogger(__name__)

PriceLevel = Literal["inexpensive", "moderate", "expensive", "very_expensive"]
PRICE_ORDER: tuple[PriceLevel, ...] = ("inexpensive", "moderate", "expensive", "very_expensive")
_GOOGLE_PRICE = {f"PRICE_LEVEL_{p.upper()}": p for p in PRICE_ORDER} | {"PRICE_LEVEL_FREE": None}

# Ask only for the fields we map: smaller responses, and Google bills by the
# most expensive field requested.
_PLACE_FIELDS = (
    "id", "displayName", "formattedAddress", "location", "rating", "userRatingCount",
    "priceLevel", "primaryType",
)  # fmt: skip
_DETAIL_FIELDS = (
    *_PLACE_FIELDS, "nationalPhoneNumber", "websiteUri", "regularOpeningHours", "editorialSummary",
)  # fmt: skip
SEARCH_FIELD_MASK = ",".join(f"places.{f}" for f in _PLACE_FIELDS)
DETAILS_FIELD_MASK = ",".join(_DETAIL_FIELDS)


class ProviderError(Exception):
    """The provider failed (HTTP error, timeout, malformed response)."""


class PlaceSearch(BaseModel):
    text: str
    language: str
    rect: tuple[float, float, float, float]  # min_lon, min_lat, max_lon, max_lat
    max_results: int = 20
    min_rating: float | None = None
    max_price: PriceLevel | None = None
    open_now: bool = False


class ProviderPlace(BaseModel):
    """One place, flat. Search fills the first block; details fills the rest."""

    id: str
    name: str
    lon: float
    lat: float
    address: str | None = None
    rating: float | None = None
    rating_count: int | None = None
    price_level: PriceLevel | None = None
    primary_type: str | None = None
    phone: str | None = None
    website: str | None = None
    opening_hours: str | None = None
    editorial_summary: str | None = None


class PlacesProvider(Protocol):
    def search(self, q: PlaceSearch) -> list[ProviderPlace]: ...

    def details(self, place_id: str, language: str) -> ProviderPlace | None: ...


def allowed_prices(max_price: PriceLevel | None) -> list[PriceLevel]:
    """"Not more than `max_price`": every level up to and including it."""
    if max_price is None:
        return []
    return list(PRICE_ORDER[: PRICE_ORDER.index(max_price) + 1])


def parse_place(raw: dict[str, Any]) -> ProviderPlace:
    """Raw Google place JSON -> ProviderPlace. Raises ProviderError when the
    fields every place must have (id, name, location) are missing."""
    try:
        location = raw["location"]
        hours = (raw.get("regularOpeningHours") or {}).get("weekdayDescriptions") or []
        return ProviderPlace(
            id=raw["id"],
            name=(raw.get("displayName") or {})["text"],
            lon=location["longitude"],
            lat=location["latitude"],
            address=raw.get("formattedAddress"),
            rating=raw.get("rating"),
            rating_count=raw.get("userRatingCount"),
            price_level=_GOOGLE_PRICE.get(raw.get("priceLevel", "")),
            primary_type=raw.get("primaryType"),
            phone=raw.get("nationalPhoneNumber"),
            website=raw.get("websiteUri"),
            opening_hours="; ".join(hours) or None,
            editorial_summary=(raw.get("editorialSummary") or {}).get("text"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError(f"malformed place: {exc}") from exc


def search_body(q: PlaceSearch) -> dict[str, Any]:
    """The Text Search (New) request body. One page only (Stage 5)."""
    min_lon, min_lat, max_lon, max_lat = q.rect
    body: dict[str, Any] = {
        "textQuery": q.text,
        "languageCode": q.language,
        "pageSize": q.max_results,
        "locationRestriction": {
            "rectangle": {
                "low": {"latitude": min_lat, "longitude": min_lon},
                "high": {"latitude": max_lat, "longitude": max_lon},
            }
        },
    }
    if q.min_rating is not None:
        body["minRating"] = q.min_rating
    if prices := allowed_prices(q.max_price):
        body["priceLevels"] = [f"PRICE_LEVEL_{p.upper()}" for p in prices]
    if q.open_now:
        body["openNow"] = True
    return body


class GooglePlacesProvider:
    def __init__(self, cfg: PlacesConfig, transport: httpx.BaseTransport | None = None):
        if cfg.api_key is None:
            raise ProviderError("places.api_key is not set")
        self._client = httpx.Client(
            base_url=cfg.base_url,
            timeout=cfg.timeout_s,
            headers={"X-Goog-Api-Key": cfg.api_key.get_secret_value()},
            transport=transport,
        )

    def _send(self, method: str, url: str, mask: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(
                method, url, headers={"X-Goog-FieldMask": mask}, **kwargs
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Never include the request (its headers carry the key) in the error.
            raise ProviderError(f"places request failed: {type(exc).__name__}") from exc

    def raw_search(self, q: PlaceSearch) -> dict[str, Any]:
        return self._send("POST", "/places:searchText", SEARCH_FIELD_MASK, json=search_body(q))

    def raw_details(self, place_id: str, language: str) -> dict[str, Any]:
        return self._send(
            "GET", f"/places/{place_id}", DETAILS_FIELD_MASK, params={"languageCode": language}
        )

    def search(self, q: PlaceSearch) -> list[ProviderPlace]:
        return [parse_place(p) for p in self.raw_search(q).get("places", [])]

    def details(self, place_id: str, language: str) -> ProviderPlace | None:
        return parse_place(self.raw_details(place_id, language))


# --- replay -----------------------------------------------------------------------

_WORD = re.compile(r"\w+")
_PREFIX = 4  # "restaurants"~"restaurant", "מסעדות"~"מסעדה"


def words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _same_word(a: str, b: str) -> bool:
    """Equal, or sharing a 4-letter prefix: a crude, deterministic stand-in for
    stemming that covers English plurals and Hebrew inflection alike."""
    if len(a) < _PREFIX or len(b) < _PREFIX:
        return a == b
    return a[:_PREFIX] == b[:_PREFIX]


def overlap(query: set[str], candidate: set[str]) -> int:
    return sum(1 for w in query if any(_same_word(w, c) for c in candidate))


class ReplayPlacesProvider:
    """Serves recorded raw responses from `fixtures_dir`:

        index.json                      {"search": [{"language", "text", "aliases", "file"}]}
        search/<file>                   a raw Text Search response
        details/<language>/<id>.json    a raw Place Details response

    A search matches by language, then the entry whose text or aliases share
    the most words with the query (exact text wins ties); no shared word means
    no results. The real model words its queries freely, so exact matching
    alone would make the evals flaky. Server-side filters (rating, price,
    page size) are re-applied here so replay behaves like the live API; the
    rectangle is not, so fixtures can hold rows the area filter must drop.
    """

    def __init__(self, fixtures_dir: Path):
        self._dir = fixtures_dir
        index = json.loads((fixtures_dir / "index.json").read_text())
        self._search: list[dict[str, Any]] = index.get("search", [])

    def _match(self, q: PlaceSearch) -> dict[str, Any] | None:
        query = words(q.text)
        best, best_key = None, (0, False)
        for entry in self._search:
            if entry["language"] != q.language:
                continue
            texts = [entry["text"], *entry.get("aliases", [])]
            score = max(overlap(query, words(t)) for t in texts)
            key = (score, words(entry["text"]) == query)
            if score > 0 and key > best_key:
                best, best_key = entry, key
        return best

    def search(self, q: PlaceSearch) -> list[ProviderPlace]:
        entry = self._match(q)
        if entry is None:
            log.warning("places replay: no fixture for %r (%s)", q.text, q.language)
            return []
        raw = json.loads((self._dir / "search" / entry["file"]).read_text())
        places = [parse_place(p) for p in raw.get("places", [])]
        prices = allowed_prices(q.max_price)
        kept = [
            p
            for p in places
            if (q.min_rating is None or (p.rating or 0) >= q.min_rating)
            and (not prices or p.price_level in prices)
        ]
        return kept[: q.max_results]

    def details(self, place_id: str, language: str) -> ProviderPlace | None:
        if not re.fullmatch(r"[\w-]+", place_id):  # it becomes a file name
            return None
        path = self._dir / "details" / language / f"{place_id}.json"
        if not path.exists():
            log.warning("places replay: no details fixture for %s (%s)", place_id, language)
            return None
        return parse_place(json.loads(path.read_text()))


@lru_cache(maxsize=8)
def _cached(provider: str, key: str, base_url: str, timeout_s: float, fixtures: str) -> Any:
    cfg = PlacesConfig(
        provider=provider,  # type: ignore[arg-type]
        api_key=key or None,
        base_url=base_url,
        timeout_s=timeout_s,
        fixtures_dir=Path(fixtures),
    )
    if provider == "google":
        return GooglePlacesProvider(cfg)
    return ReplayPlacesProvider(cfg.fixtures_dir)


def get_places_provider(cfg: PlacesConfig) -> PlacesProvider:
    """One provider per distinct config (its HTTP client and fixture index are
    reused across calls). Keyed on plain values: the config itself isn't hashable."""
    key = cfg.api_key.get_secret_value() if cfg.api_key else ""
    return _cached(cfg.provider, key, cfg.base_url, cfg.timeout_s, str(cfg.fixtures_dir))
