"""Record places fixtures from the live Google Places API (New). NOT run by
pytest — it needs a key and the network, and it costs API calls. Run by hand:

    GEOSEARCH_PLACES__API_KEY=... uv run python scripts/record_places.py \
        --language en --text "asian restaurant" --alias "asian food" --alias sushi

It runs one Text Search over the rectangle (default: the handoff polygon's
bbox), fetches details for every place returned, and writes:

    fixtures/places/search/<language>-<slug>.json
    fixtures/places/details/<language>/<place_id>.json
    fixtures/places/index.json      (entry for this text/language added or replaced)

Recorded files replace the synthetic ones (Stage 5, D1): the search file and
index entry for the same language and text are overwritten. Synthetic details
files are left in place; delete the folder first for a clean re-record.
"""

import argparse
import json
import re
from pathlib import Path

from geosearch.config import GeoConfig, PlacesConfig
from geosearch.providers.places import GooglePlacesProvider, PlaceSearch

HANDOFF_RECT = (34.75, 32.05, 34.80, 32.10)


def _dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _slug(text: str) -> str:
    return re.sub(r"\W+", "-", text.lower()).strip("-") or "query"


def main() -> None:
    parser = argparse.ArgumentParser(description="Record Google Places fixtures.")
    parser.add_argument("--text", required=True, help="The Text Search query.")
    parser.add_argument("--language", required=True, choices=["en", "he"])
    parser.add_argument("--alias", action="append", default=[], help="Extra match text.")
    parser.add_argument("--rect", type=float, nargs=4, default=HANDOFF_RECT,
                        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"))
    args = parser.parse_args()

    cfg = GeoConfig()
    live = PlacesConfig(**{**cfg.places.model_dump(), "provider": "google"})
    provider = GooglePlacesProvider(live)
    out = live.fixtures_dir

    raw = provider.raw_search(
        PlaceSearch(text=args.text, language=args.language, rect=tuple(args.rect),
                    max_results=live.max_results)
    )
    file = f"{args.language}-{_slug(args.text)}.json"
    _dump(out / "search" / file, raw)
    places = raw.get("places", [])
    for place in places:
        details = provider.raw_details(place["id"], args.language)
        _dump(out / "details" / args.language / f"{place['id']}.json", details)

    index_path = out / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {"search": []}
    entries = [
        e for e in index.get("search", [])
        if not (e["language"] == args.language and e["text"] == args.text)
    ]
    entries.append(
        {"language": args.language, "text": args.text, "aliases": args.alias, "file": file}
    )
    index["search"] = entries
    _dump(index_path, index)
    print(f"recorded {len(places)} place(s) -> {out / 'search' / file}")


if __name__ == "__main__":
    main()
