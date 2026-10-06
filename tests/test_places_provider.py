"""Stage 5 step 2: the places provider. Google is exercised through
httpx.MockTransport (no network); replay against the committed fixtures."""

import json
from pathlib import Path

import httpx
import pytest

from geosearch.config import PlacesConfig
from geosearch.providers.places import (
    DETAILS_FIELD_MASK,
    SEARCH_FIELD_MASK,
    GooglePlacesProvider,
    PlaceSearch,
    ProviderError,
    ReplayPlacesProvider,
    allowed_prices,
    get_places_provider,
    parse_place,
)

FIXTURES = Path("fixtures/places")
RECT = (34.75, 32.05, 34.80, 32.10)
RAW = {
    "id": "abc",
    "displayName": {"text": "Lotus", "languageCode": "en"},
    "formattedAddress": "Dizengoff St 1",
    "location": {"latitude": 32.07, "longitude": 34.77},
    "rating": 4.6,
    "userRatingCount": 10,
    "priceLevel": "PRICE_LEVEL_MODERATE",
    "primaryType": "asian_restaurant",
}


def _google(handler) -> tuple[GooglePlacesProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    cfg = PlacesConfig(provider="google", api_key="secret-key")
    return GooglePlacesProvider(cfg, transport=httpx.MockTransport(record)), seen


# --- parsing ----------------------------------------------------------------------


def test_parse_place_maps_fields() -> None:
    place = parse_place(RAW)
    assert (place.id, place.name, place.lon, place.lat) == ("abc", "Lotus", 34.77, 32.07)
    assert place.price_level == "moderate"
    assert place.rating_count == 10 and place.primary_type == "asian_restaurant"
    assert place.phone is None and place.opening_hours is None


def test_parse_place_details_fields() -> None:
    raw = RAW | {
        "nationalPhoneNumber": "03-1",
        "websiteUri": "https://x",
        "regularOpeningHours": {"weekdayDescriptions": ["Mon: 9-5", "Tue: 9-5"]},
        "editorialSummary": {"text": "Nice."},
        "priceLevel": "PRICE_LEVEL_FREE",
    }
    place = parse_place(raw)
    assert place.opening_hours == "Mon: 9-5; Tue: 9-5"
    assert place.editorial_summary == "Nice." and place.website == "https://x"
    assert place.price_level is None  # FREE has no place on our scale


def test_parse_place_rejects_missing_essentials() -> None:
    with pytest.raises(ProviderError):
        parse_place({"id": "x", "displayName": {"text": "n"}})  # no location


def test_allowed_prices() -> None:
    assert allowed_prices(None) == []
    assert allowed_prices("moderate") == ["inexpensive", "moderate"]


# --- google -----------------------------------------------------------------------


def test_google_search_request_shape() -> None:
    provider, seen = _google(lambda r: httpx.Response(200, json={"places": [RAW]}))
    q = PlaceSearch(
        text="asian", language="he", rect=RECT, max_results=7, min_rating=4.0,
        max_price="moderate", open_now=True,
    )  # fmt: skip
    places = provider.search(q)
    assert [p.id for p in places] == ["abc"]

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "https://places.googleapis.com/v1/places:searchText"
    assert request.headers["X-Goog-Api-Key"] == "secret-key"
    assert request.headers["X-Goog-FieldMask"] == SEARCH_FIELD_MASK
    body = json.loads(request.content)
    assert body == {
        "textQuery": "asian",
        "languageCode": "he",
        "pageSize": 7,
        "locationRestriction": {
            "rectangle": {
                "low": {"latitude": 32.05, "longitude": 34.75},
                "high": {"latitude": 32.10, "longitude": 34.80},
            }
        },
        "minRating": 4.0,
        "priceLevels": ["PRICE_LEVEL_INEXPENSIVE", "PRICE_LEVEL_MODERATE"],
        "openNow": True,
    }


def test_google_search_omits_unset_filters() -> None:
    provider, seen = _google(lambda r: httpx.Response(200, json={}))
    assert provider.search(PlaceSearch(text="x", language="en", rect=RECT)) == []
    body = json.loads(seen[0].content)
    assert not {"minRating", "priceLevels", "openNow"} & set(body)


def test_google_details_request_shape() -> None:
    provider, seen = _google(lambda r: httpx.Response(200, json=RAW))
    place = provider.details("abc", "he")
    assert place is not None and place.id == "abc"
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == "/v1/places/abc"
    assert request.url.params["languageCode"] == "he"
    assert request.headers["X-Goog-FieldMask"] == DETAILS_FIELD_MASK


def test_google_errors_become_provider_errors_without_the_key() -> None:
    provider, _ = _google(lambda r: httpx.Response(403, json={"error": "denied"}))
    with pytest.raises(ProviderError) as info:
        provider.search(PlaceSearch(text="x", language="en", rect=RECT))
    assert "secret-key" not in str(info.value)


# --- replay -----------------------------------------------------------------------


@pytest.fixture
def replay() -> ReplayPlacesProvider:
    return ReplayPlacesProvider(FIXTURES)


def _search(replay: ReplayPlacesProvider, text: str, language: str = "en", **kw) -> list:
    return replay.search(PlaceSearch(text=text, language=language, rect=RECT, **kw))


def test_replay_exact_and_fuzzy_matches(replay: ReplayPlacesProvider) -> None:
    exact = _search(replay, "asian restaurant")
    assert len(exact) == 13
    # Plurals and free wording still find the fixture.
    assert [p.id for p in _search(replay, "cheap Asian restaurants")] == [p.id for p in exact]
    assert len(_search(replay, "sushi")) == 13


def test_replay_hebrew(replay: ReplayPlacesProvider) -> None:
    places = _search(replay, "מסעדות אסייתיות זולות", "he")
    assert len(places) == 13
    assert places[0].name == "לוטוס נודל בר"
    # An English query under a Hebrew prompt hits the Hebrew fixture via aliases.
    assert _search(replay, "asian restaurant", "he")[0].name == "לוטוס נודל בר"


def test_replay_miss_returns_nothing(replay: ReplayPlacesProvider) -> None:
    assert _search(replay, "car wash") == []
    assert _search(replay, "asian restaurant", "fr") == []


def test_replay_applies_server_side_filters(replay: ReplayPlacesProvider) -> None:
    cheap = _search(replay, "asian restaurant", max_price="moderate")
    assert cheap and all(p.price_level in {"inexpensive", "moderate"} for p in cheap)
    rated = _search(replay, "asian restaurant", min_rating=4.6)
    assert rated and all((p.rating or 0) >= 4.6 for p in rated)
    assert len(_search(replay, "asian restaurant", max_results=3)) == 3


def test_replay_details(replay: ReplayPlacesProvider) -> None:
    first = _search(replay, "asian restaurant")[0]
    details = replay.details(first.id, "en")
    assert details is not None
    assert details.phone and details.website and details.opening_hours
    assert details.editorial_summary
    he = replay.details(first.id, "he")
    assert he is not None and he.name == "לוטוס נודל בר"
    assert replay.details("unknown", "en") is None
    assert replay.details("../index", "en") is None  # never a path outside the folder


def test_fixtures_are_marked_synthetic() -> None:
    index = json.loads((FIXTURES / "index.json").read_text())
    assert index["_synthetic"] is True


def test_provider_factory_caches_per_config() -> None:
    a = get_places_provider(PlacesConfig())
    assert isinstance(a, ReplayPlacesProvider)
    assert get_places_provider(PlacesConfig()) is a
    google = get_places_provider(PlacesConfig(provider="google", api_key="k"))
    assert isinstance(google, GooglePlacesProvider)
