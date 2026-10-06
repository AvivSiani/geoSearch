"""Stage 4 step 2: the always-on catalog block."""

from disclosure_helpers import Capture, context, model_request
from fake_registry import FakeRegistry, scale_catalog

from geosearch.agent.disclosure import CATALOG_HEADER, NO_TOOLS, CatalogMiddleware, render_catalog


def _system_text(state: dict | None = None, system: str | None = "Base prompt.") -> str:
    capture = Capture()
    CatalogMiddleware(scale_catalog().snapshot).wrap_model_call(
        model_request(state=state, system=system), capture
    )
    assert capture.request is not None
    return capture.request.system_prompt or ""


def test_block_is_appended_after_the_base_prompt() -> None:
    text = _system_text()
    assert text.startswith("Base prompt.\n\n" + CATALOG_HEADER)


def test_entries_are_id_colon_description_sorted_by_id() -> None:
    lines = render_catalog(scale_catalog().snapshot, []).splitlines()
    assert lines[0] == CATALOG_HEADER
    ids = [int(line.split(":", 1)[0]) for line in lines[1:]]
    assert ids == sorted(ids) == [*range(101, 111), 9001]
    assert lines[-1] == "9001: Return up to `count` random points inside the current area."


def test_loaded_tools_are_marked() -> None:
    text = _system_text(state={"loaded_tools": [9001, 103]})
    assert "9001: Return up to `count` random points inside the current area. (loaded)" in text
    assert "(loaded)" in next(line for line in text.splitlines() if line.startswith("103:"))
    assert text.count("(loaded)") == 2


def test_catalog_shows_no_schemas_or_returned_fields() -> None:
    text = _system_text()
    for absent in ("properties", "temperature_c", "lon", "returns", "source_9001"):
        assert absent not in text


def test_empty_registry() -> None:
    assert render_catalog(scale_catalog(FakeRegistry()).snapshot, []) == NO_TOOLS


def test_no_base_prompt() -> None:
    assert _system_text(system=None).startswith(CATALOG_HEADER)


def test_block_is_stable_between_calls() -> None:
    snapshot = scale_catalog().snapshot
    assert render_catalog(snapshot, [101]) == render_catalog(snapshot, [101])


def test_catalog_size_is_noted_for_the_ledger() -> None:
    ctx = context()
    CatalogMiddleware(scale_catalog().snapshot).wrap_model_call(model_request(ctx=ctx), Capture())
    assert 0 < ctx.ledger.pending_catalog_tokens < ctx.cfg.disclosure.catalog_warn_tokens
