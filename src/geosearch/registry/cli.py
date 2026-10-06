"""`geosearch-registry`: seed, check, list and delete registry definitions.

Exit codes: 0 ok, 1 validation problems (drift, bad seed file, unknown
handler, missing tool), 2 usage or connection error. Config comes from the
usual GEOSEARCH_* environment, so `GEOSEARCH_MONGO__URI` points it elsewhere.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pymongo.errors import PyMongoError

from geosearch import capabilities
from geosearch.config import GeoConfig
from geosearch.registry.handlers import UnknownHandler
from geosearch.registry.mongo import connect, database, ping
from geosearch.registry.seeding import (
    SeedFileError,
    SeedReport,
    ValidationReport,
    all_tools,
    seed,
    validate,
)
from geosearch.registry.store import MongoRegistry

OK, PROBLEMS, USAGE = 0, 1, 2
_PATHS_HELP = "seed files or directories (default: registry.seeds_dir)"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geosearch-registry", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser("seed", help="upsert definitions from YAML seed files")
    p_seed.add_argument("paths", nargs="*", type=Path, help=_PATHS_HELP)
    p_seed.add_argument("--include-demo", action="store_true", help="also load demo.yaml")
    p_seed.add_argument("--prune", action="store_true", help="delete tools in no seed file")

    p_validate = sub.add_parser("validate", help="report seed/DB drift and missing handlers")
    p_validate.add_argument("paths", nargs="*", type=Path, help=_PATHS_HELP)
    p_validate.add_argument("--include-demo", action="store_true", help="also load demo.yaml")

    sub.add_parser("list", help="list stored definitions")

    p_delete = sub.add_parser("delete", help="delete one definition")
    p_delete.add_argument("source_id")
    return parser


def _print_seed(report: SeedReport) -> None:
    for label in ("created", "changed", "unchanged", "deleted"):
        items = getattr(report, label)
        print(f"{label}: {len(items)}" + (f"  ({', '.join(items)})" if items else ""))


def _print_validation(report: ValidationReport) -> None:
    for diff in report.differences:
        db = "<missing>" if diff.db is None else repr(diff.db)
        print(f"differs: {diff.source_id}\n  seed: {diff.seed!r}\n  db:   {db}")
    for source_id in report.not_in_seeds:
        print(f"not in any seed file: {source_id}")
    for source_id in report.without_handler:
        print(f"no handler: {source_id}")
    print("ok" if report.ok else "problems found")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on usage errors, 0 on --help
        return int(exc.code or 0)

    cfg = GeoConfig()
    client = connect(cfg.mongo)
    if not ping(client):
        print("error: MongoDB is unreachable (docker compose up -d?)", file=sys.stderr)
        return USAGE
    registry = MongoRegistry(database(client, cfg.mongo))
    capabilities.load_all()
    include_demo = getattr(args, "include_demo", False) or cfg.registry.seed_demo
    paths = getattr(args, "paths", None) or [cfg.registry.seeds_dir]

    try:
        if args.command == "seed":
            _print_seed(seed(registry, paths, include_demo=include_demo, prune=args.prune))
            print(f"revision: {registry.revision()}")
            return OK
        if args.command == "validate":
            report = validate(registry, paths, include_demo=include_demo)
            _print_validation(report)
            return OK if report.ok else PROBLEMS
        if args.command == "list":
            for td in all_tools(registry):
                print(f"{td.source_id}\t{td.description}")
            print(f"revision: {registry.revision()}")
            return OK
        if args.command == "delete":
            if not registry.delete_tool(args.source_id):
                print(f"error: {args.source_id!r} is not in the registry", file=sys.stderr)
                return PROBLEMS
            print(f"deleted: {args.source_id}\nrevision: {registry.revision()}")
            return OK
    except (SeedFileError, UnknownHandler) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return PROBLEMS
    except PyMongoError as exc:
        print(f"error: MongoDB failed: {type(exc).__name__}", file=sys.stderr)
        return USAGE
    finally:
        client.close()
    return USAGE  # unreachable: argparse requires a known command


if __name__ == "__main__":
    sys.exit(main())
