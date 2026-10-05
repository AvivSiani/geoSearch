"""Guards invariant 5: request/ and geo/ import neither FastAPI nor any LLM library."""

import ast
from pathlib import Path

FORBIDDEN_PREFIXES = ("fastapi", "starlette", "uvicorn", "langchain", "deepagents", "openai")
SRC = Path(__file__).parent.parent / "src" / "geosearch"


def _imported_modules(py_file: Path) -> set[str]:
    tree = ast.parse(py_file.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_request_and_geo_do_not_import_fastapi_or_llm_libs() -> None:
    offenders = []
    for package in ("request", "geo"):
        for py_file in (SRC / package).rglob("*.py"):
            for module in _imported_modules(py_file):
                if module.startswith(FORBIDDEN_PREFIXES):
                    offenders.append(f"{py_file.relative_to(SRC)} imports {module}")
    assert not offenders, "\n".join(offenders)
