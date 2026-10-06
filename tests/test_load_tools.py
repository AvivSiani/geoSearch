"""Stage 4 step 3: load_tools, every case in the spec's table."""

from typing import Any

from disclosure_helpers import context
from fake_registry import scale_catalog
from langchain.tools import ToolRuntime
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.types import Command

from geosearch.agent.tools.loading import make_load_tools
from geosearch.config import DisclosureConfig, GeoConfig


def _load(ids: list[Any], loaded: list[int] | None = None, cap: int | None = None) -> Command:
    cfg = GeoConfig(_env_file=None).model_copy(
        update={"disclosure": DisclosureConfig(max_loaded_tools=cap)}
    )
    runtime = ToolRuntime(
        state={"loaded_tools": loaded or []},
        context=context(cfg),
        config={},
        stream_writer=lambda *_: None,
        tool_call_id="call_7",
        store=None,
    )
    return make_load_tools(scale_catalog().snapshot).func(source_ids=ids, runtime=runtime)


def _text(cmd: Command) -> str:
    return cmd.update["messages"][0].content


def test_schema_takes_only_source_ids() -> None:
    fn = convert_to_openai_tool(make_load_tools(scale_catalog().snapshot))["function"]
    assert fn["name"] == "load_tools"
    params = fn["parameters"]
    assert set(params["properties"]) == {"source_ids"}
    assert params["properties"]["source_ids"]["items"] == {"type": "integer"}
    assert "runtime" not in str(params)


def test_valid_ids_are_loaded_with_their_returned_fields() -> None:
    cmd = _load([9001, 101])
    assert cmd.update["loaded_tools"] == [9001, 101]
    assert _text(cmd) == (
        "- source_9001 (9001): Return up to `count` random points inside the current area."
        " → returns: lon, lat\n"
        "- source_101 (101): Current weather at the area: temperature, conditions and wind."
        " → returns: temperature_c, conditions, wind_kph"
    )
    message = cmd.update["messages"][0]
    assert message.tool_call_id == "call_7" and message.name == "load_tools"


def test_already_loaded_changes_nothing() -> None:
    cmd = _load([9001], loaded=[9001])
    assert "loaded_tools" not in cmd.update
    assert _text(cmd) == "- 9001 already loaded"


def test_unknown_ids_change_nothing() -> None:
    cmd = _load([99, 100])
    assert "loaded_tools" not in cmd.update
    assert _text(cmd) == "Unknown ids: 99, 100. Use ids from the catalog."


def test_mixed_request_handles_each_id_independently() -> None:
    cmd = _load([101, 99, 9001], loaded=[9001])
    assert cmd.update["loaded_tools"] == [101]
    lines = _text(cmd).splitlines()
    assert lines[0].startswith("- source_101 (101):")
    assert lines[1] == "- 9001 already loaded"
    assert lines[2] == "Unknown ids: 99. Use ids from the catalog."


def test_cap_loads_up_to_the_limit_only() -> None:
    cmd = _load([101, 102, 103], loaded=[9001], cap=2)
    assert cmd.update["loaded_tools"] == [101]
    assert _text(cmd).splitlines()[-1] == "Not loaded (limit 2 reached): 102, 103"


def test_no_cap_by_default() -> None:
    ids = [*range(101, 111), 9001]
    assert _load(ids).update["loaded_tools"] == ids


def test_duplicates_and_empty_input() -> None:
    assert _load([101, 101]).update["loaded_tools"] == [101]
    assert _text(_load([])) == "No ids given. Use ids from the catalog."


def test_deleted_loaded_ids_do_not_count_against_the_cap() -> None:
    cmd = _load([101], loaded=[4242], cap=1)  # 4242 no longer in the catalog
    assert cmd.update["loaded_tools"] == [101]
