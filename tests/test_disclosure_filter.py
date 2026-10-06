"""Stage 4 step 4: the disclosure filter (schemas) and guard (calls)."""

from types import SimpleNamespace

from disclosure_helpers import Capture, context, model_request
from fake_registry import scale_catalog, scale_registry
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

from geosearch.agent.disclosure import DisclosureMiddleware
from geosearch.agent.tools import geo_describe_area


@tool
def read_file(path: str) -> str:
    """A stand-in for a harness built-in."""
    return path


def _offered(loaded: list[int], snapshot=None) -> list[str]:
    snapshot = snapshot or scale_catalog().snapshot
    tools = [geo_describe_area, read_file, *snapshot.resolved_tools()]
    capture = Capture()
    DisclosureMiddleware(snapshot).wrap_model_call(
        model_request(state={"loaded_tools": loaded}, tools=tools), capture
    )
    return [t.name for t in capture.request.tools]


def test_before_any_load_only_core_tools_are_offered() -> None:
    assert _offered([]) == ["geo_describe_area", "read_file"]


def test_loaded_tools_are_offered_exactly() -> None:
    assert _offered([9001, 103]) == ["geo_describe_area", "read_file", "source_103", "source_9001"]


def test_loaded_id_no_longer_in_catalog_is_ignored() -> None:
    # 9001 was loaded, then deleted from the registry: the rebuilt catalog lacks it.
    snapshot = scale_catalog(scale_registry(include=[101])).snapshot
    assert _offered([9001, 101], snapshot) == ["geo_describe_area", "read_file", "source_101"]


def test_registry_tool_names_are_noted_for_the_ledger() -> None:
    snapshot = scale_catalog().snapshot
    ctx = context()
    DisclosureMiddleware(snapshot).wrap_model_call(model_request(ctx=ctx), Capture())
    assert ctx.ledger.pending_registry_tool_names == {e.model_name for e in snapshot.tools}


def _call(name: str, loaded: list[int]) -> tuple[object, bool]:
    ran = {"handler": False}

    def handler(request: ToolCallRequest) -> ToolMessage:
        ran["handler"] = True
        return ToolMessage("ran", tool_call_id="c1")

    request = ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": "c1", "type": "tool_call"},
        tool=None,
        state={"loaded_tools": loaded},
        runtime=SimpleNamespace(),
    )
    out = DisclosureMiddleware(scale_catalog().snapshot).wrap_tool_call(request, handler)
    return out, ran["handler"]


def test_guard_blocks_unloaded_registry_tools_and_runs_nothing() -> None:
    out, ran = _call("source_103", loaded=[9001])
    assert not ran
    assert isinstance(out, ToolMessage) and out.status == "error"
    assert out.content == "source_103 is not loaded. Call load_tools([103]) first."
    assert out.tool_call_id == "c1"


def test_guard_lets_loaded_and_core_tools_run() -> None:
    assert _call("source_103", loaded=[103])[1]
    assert _call("geo_describe_area", loaded=[])[1]
    assert _call("load_tools", loaded=[])[1]
