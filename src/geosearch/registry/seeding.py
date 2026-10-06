"""YAML seeds -> registry, the read-only drift check, and the startup check.

Seed files are the reviewed source of truth for definitions; the database is
what the running app reads. `seed` makes the database match the files
(idempotently — re-seeding unchanged files writes nothing and leaves the
revision alone), `validate` reports where they differ without writing, and
`check_startup` makes sure nothing stored is outside the handler allowlist.

Seed file format:

    tools:
      - source_id: demo.sample_points
        description: Return up to `count` random points inside the current area.

The demo seed (`demo.yaml`) is skipped when expanding a directory unless
demo tools are asked for; a file path given explicitly is always loaded.
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from geosearch.registry.handlers import HandlerRegistry, UnknownHandler, handlers
from geosearch.registry.models import ToolDefinition
from geosearch.registry.store import Registry

log = logging.getLogger(__name__)

DEMO_SEED = "demo.yaml"


class SeedFileError(Exception):
    """A seed file is unreadable, malformed, or conflicts with another one."""


@dataclass
class SeedReport:
    created: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)


@dataclass
class DescriptionDiff:
    source_id: str
    seed: str
    db: str | None  # None: in a seed file but not in the database


@dataclass
class ValidationReport:
    differences: list[DescriptionDiff] = field(default_factory=list)
    not_in_seeds: list[str] = field(default_factory=list)  # DB tools in no seed file
    without_handler: list[str] = field(default_factory=list)  # seed or DB source_ids

    @property
    def ok(self) -> bool:
        return not (self.differences or self.not_in_seeds or self.without_handler)


def seed_files(paths: Iterable[Path], include_demo: bool) -> list[Path]:
    """Expand directories to their *.yaml/*.yml files (sorted, demo filtered)."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            found = sorted([*path.glob("*.yaml"), *path.glob("*.yml")])
            files.extend(f for f in found if include_demo or f.name != DEMO_SEED)
        elif path.is_file():
            files.append(path)
        else:
            raise SeedFileError(f"seed path not found: {path}")
    return files


def load_definitions(files: Sequence[Path]) -> list[ToolDefinition]:
    """Parse and validate every definition; a source_id may appear only once."""
    seen: dict[str, Path] = {}
    definitions: list[ToolDefinition] = []
    for path in files:
        try:
            doc = yaml.safe_load(path.read_text()) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise SeedFileError(f"{path}: cannot read YAML: {exc}") from exc
        entries = doc.get("tools", []) if isinstance(doc, dict) else None
        if not isinstance(entries, list):
            raise SeedFileError(f"{path}: expected a top-level 'tools:' list")
        for i, entry in enumerate(entries):
            try:
                td = ToolDefinition.model_validate(entry)
            except ValidationError as exc:
                raise SeedFileError(f"{path}: tools[{i}]: {exc}") from exc
            if td.source_id in seen:
                raise SeedFileError(
                    f"{path}: {td.source_id!r} is already defined in {seen[td.source_id]}"
                )
            seen[td.source_id] = path
            definitions.append(td)
    return definitions


def all_tools(registry: Registry) -> list[ToolDefinition]:
    return [td for cap in registry.list_capabilities() for td in registry.get_tools(cap)]


def _require_handlers(source_ids: Iterable[str], handler_registry: HandlerRegistry) -> None:
    for source_id in sorted(source_ids):
        if source_id not in handler_registry:
            raise UnknownHandler(source_id)


def seed(
    registry: Registry,
    paths: Iterable[Path],
    *,
    include_demo: bool,
    prune: bool = False,
    handler_registry: HandlerRegistry = handlers,
) -> SeedReport:
    """Make the registry match the seed files. Nothing is written unless every
    seeded source_id has a handler, so a typo cannot half-apply a seed."""
    definitions = load_definitions(seed_files(paths, include_demo))
    _require_handlers((td.source_id for td in definitions), handler_registry)

    report = SeedReport()
    existing = {td.source_id for td in all_tools(registry)}
    for td in definitions:
        if not registry.upsert_tool(td):
            report.unchanged.append(td.source_id)
        elif td.source_id in existing:
            report.changed.append(td.source_id)
        else:
            report.created.append(td.source_id)

    if prune:
        seeded = {td.source_id for td in definitions}
        for source_id in sorted(existing - seeded):
            if registry.delete_tool(source_id):
                report.deleted.append(source_id)
    return report


def validate(
    registry: Registry,
    paths: Iterable[Path],
    *,
    include_demo: bool,
    handler_registry: HandlerRegistry = handlers,
) -> ValidationReport:
    """Read-only: how the database differs from the seed files and the allowlist."""
    definitions = load_definitions(seed_files(paths, include_demo))
    stored = {td.source_id: td.description for td in all_tools(registry)}
    seeded = {td.source_id: td.description for td in definitions}

    report = ValidationReport()
    for source_id, description in seeded.items():
        in_db = stored.get(source_id)
        if in_db != description:
            report.differences.append(DescriptionDiff(source_id, description, in_db))
    report.not_in_seeds = sorted(set(stored) - set(seeded))
    report.without_handler = sorted(
        s for s in set(stored) | set(seeded) if s not in handler_registry
    )
    return report


def check_startup(
    registry: Registry, *, strict: bool, handler_registry: HandlerRegistry = handlers
) -> list[str]:
    """Every stored source_id must be on the allowlist. Strict: raise for the
    first offender. Lenient: log each one and return them, so the catalog can
    skip them while the app still starts."""
    missing = sorted(
        td.source_id for td in all_tools(registry) if td.source_id not in handler_registry
    )
    if missing and strict:
        raise UnknownHandler(missing[0])
    for source_id in missing:
        log.warning("registry: skipping %r: no handler registered", source_id)
    return missing
