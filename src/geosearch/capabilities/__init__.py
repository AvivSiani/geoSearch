"""Handler modules for registry tools (Stage 3+).

Each module registers its handlers on import via `register_handler`. The list
is explicit — no auto-discovery — so the handler allowlist is exactly what is
written here and reviewable in one place.
"""

import importlib

MODULES: tuple[str, ...] = ("geosearch.capabilities.demo",)


def load_all() -> None:
    """Import every handler module. Safe to call repeatedly: Python caches
    imports, so each module registers its handlers once."""
    for module in MODULES:
        importlib.import_module(module)
