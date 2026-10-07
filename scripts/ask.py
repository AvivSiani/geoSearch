"""Ask the agent from the terminal. NOT run by pytest — it needs the real model
and the MongoDB registry (`docker compose up -d`, `uv run geosearch-registry seed`).

    uv run python scripts/ask.py "Find me a good Asian restaurant that is not too expensive."
    uv run python scripts/ask.py --wkt "POINT (34.78 32.08)" --radius 500 "What's around here?"

It drives the same RequestRunner as POST /v1/requests (no HTTP server), then
reads follow-ups from stdin in the same conversation — one conversation, one
area — until an empty line or Ctrl-D. `--once` skips the follow-ups.

With the mongodb conversation store, a conversation outlives this process:
resume it later, from a new process, by its id (Stage 6):

    export GEOSEARCH_CONVERSATION__STORE=mongodb
    uv run python scripts/ask.py --once "Find sushi places"
    uv run python scripts/ask.py --conversation-id <id> "Which one is cheapest?"
"""

import argparse
import json
import sys

from geosearch.api.app import create_app
from geosearch.errors import GeoValidationError
from geosearch.request.models import UserRequest

# The eval polygon (~26 km² of Tel Aviv); the committed places fixtures cover it.
DEFAULT_WKT = "POLYGON((34.75 32.05, 34.80 32.05, 34.80 32.10, 34.75 32.10, 34.75 32.05))"
DEFAULT_PROMPT = "Find me a good Asian restaurant that is not too expensive."


def _print_outcome(outcome, verbose: bool) -> None:
    print(f"\n[turn {outcome.turn}] {outcome.area_summary}")
    print(f"conversation_id: {outcome.conversation_id}")
    print(f"\n{outcome.answer}\n")
    for item in outcome.items:
        where = f" ({item.lat:.5f}, {item.lon:.5f})" if item.lat is not None else ""
        print(f"  {item.id}  {item.name or ''}{where}  [source {item.source_id}]")
    u = outcome.usage
    print(
        f"\nstopped={outcome.stopped_reason} answer={outcome.answer_source} "
        f"calls={u.model_calls} in={u.input_tokens} out={u.output_tokens} "
        f"peak={u.peak_input_tokens} over_budget={u.over_budget} "
        f"summarizer_calls={u.summarizer_calls}"
    )
    if verbose:
        print(json.dumps([i.model_dump() for i in outcome.items], ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the GeoSearch agent from the terminal.")
    parser.add_argument("prompt", nargs="?", default=DEFAULT_PROMPT)
    parser.add_argument(
        "--wkt", default=None, help="area as WKT (Polygon, MultiPolygon, Point); default: Tel Aviv"
    )
    parser.add_argument("--radius", type=float, default=None, help="point buffer radius in metres")
    parser.add_argument("--once", action="store_true", help="no follow-up turns")
    parser.add_argument(
        "--conversation-id", help="resume a stored conversation (its area is already bound)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="print items as JSON")
    args = parser.parse_args()

    if args.conversation_id and args.wkt is not None:
        parser.error("--wkt cannot be combined with --conversation-id: the area is fixed")
    app = create_app()
    runner = app.state.runner
    if args.conversation_id:
        req = UserRequest(prompt=args.prompt, conversation_id=args.conversation_id)
    else:
        wkt = args.wkt or DEFAULT_WKT
        req = UserRequest(wkt=wkt, prompt=args.prompt, point_buffer_m=args.radius)
    print(f"> {args.prompt}")

    while True:
        try:
            outcome = runner.handle(req)
        except GeoValidationError as exc:
            print(f"error {exc.code}: {exc.message} {exc.details or ''}", file=sys.stderr)
            return 1
        _print_outcome(outcome, args.verbose)

        if args.once:
            return 0
        try:
            follow_up = input("\n> ").strip()
        except EOFError:
            return 0
        if not follow_up:
            return 0
        # Same area on a follow-up; the runner rejects a different one (AREA_MISMATCH).
        req = UserRequest(prompt=follow_up, conversation_id=outcome.conversation_id)


if __name__ == "__main__":
    sys.exit(main())
