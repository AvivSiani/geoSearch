import pytest
from conftest import BOWTIE, BROKEN, EMPTY, HANDOFF_POLYGON, HUGE, LINE, OUT_OF_RANGE, TLV_POINT
from fastapi.testclient import TestClient
from scripted_model import ScriptedChatModel, ai

from geosearch.api.app import create_app
from geosearch.config import GeoConfig, LimitsConfig, PointBufferConfig
from geosearch.errors import ErrorCode
from geosearch.registry.store import InMemoryRegistry


def _client(cfg: GeoConfig | None = None, responses: list | None = None) -> TestClient:
    """A TestClient with a scripted model, so the endpoint runs end-to-end
    without a real model server. Validation-error tests never reach the model."""
    cfg = cfg or GeoConfig(_env_file=None)
    model = ScriptedChatModel(responses=responses or [ai("The area is about 26.2 km².")])
    return TestClient(create_app(cfg, model=model, tool_registry=InMemoryRegistry()))


@pytest.fixture
def client() -> TestClient:
    return _client()


# --- success path (now returns an AgentResponse, not a ValidatedRequest) ---


def test_handoff_polygon_returns_agent_response(client: TestClient) -> None:
    response = client.post(
        "/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "How big is this area?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"]
    assert body["turn"] == 1
    assert body["stopped_reason"] == "finished"
    assert "km²" in body["area_summary"]
    assert body["answer"]
    assert body["usage"]["model_calls"] >= 1
    # The response carries no geometry and no server-only identifiers.
    assert "area_id" not in body
    assert "wkt" not in body


def test_buffered_point_summary_reports_circle(client: TestClient) -> None:
    response = client.post(
        "/v1/requests", json={"wkt": TLV_POINT, "prompt": "describe it"}
    )
    assert response.status_code == 200
    # The only allowed geographic transform is reported in the area summary.
    assert "circle from Point" in response.json()["area_summary"]
    assert "r=10 m" in response.json()["area_summary"]


def test_follow_up_without_wkt(client: TestClient) -> None:
    first = client.post(
        "/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "How big is this area?"}
    ).json()
    second = client.post(
        "/v1/requests",
        json={"prompt": "and again?", "conversation_id": first["conversation_id"]},
    )
    assert second.status_code == 200
    assert second.json()["turn"] == 2
    assert second.json()["conversation_id"] == first["conversation_id"]


def test_follow_up_different_area_is_area_mismatch(client: TestClient) -> None:
    first = client.post(
        "/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "q1"}
    ).json()
    response = client.post(
        "/v1/requests",
        json={
            "wkt": "POLYGON((0 0, 0.01 0, 0.01 0.01, 0 0.01, 0 0))",
            "prompt": "q2",
            "conversation_id": first["conversation_id"],
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.AREA_MISMATCH


def test_unknown_conversation_returns_404(client: TestClient) -> None:
    response = client.post(
        "/v1/requests", json={"prompt": "q", "conversation_id": "nope"}
    )
    assert response.status_code == 404
    assert response.json()["code"] == ErrorCode.CONVERSATION_NOT_FOUND


def test_client_supplied_area_id_is_ignored(client: TestClient) -> None:
    response = client.post(
        "/v1/requests",
        json={"wkt": HANDOFF_POLYGON, "prompt": "x", "area_id": "area_hacked"},
    )
    assert response.status_code == 200
    # area_id is never accepted from, nor returned to, the client.
    assert "area_id" not in response.json()


# --- validation errors (unchanged from Stage 1; never reach the model) ---


def test_missing_wkt_field(client: TestClient) -> None:
    response = client.post("/v1/requests", json={"prompt": "x"})
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.MISSING_WKT


def test_missing_prompt_field(client: TestClient) -> None:
    response = client.post("/v1/requests", json={"wkt": HANDOFF_POLYGON})
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.MISSING_PROMPT


def test_wrong_field_type_is_invalid_request(client: TestClient) -> None:
    response = client.post("/v1/requests", json={"wkt": 123, "prompt": "x"})
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.INVALID_REQUEST


def test_malformed_json_body_is_invalid_request(client: TestClient) -> None:
    response = client.post(
        "/v1/requests", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.INVALID_REQUEST


def test_prompt_too_long(client: TestClient) -> None:
    response = client.post(
        "/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "x" * 2_001}
    )
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.PROMPT_TOO_LONG


def test_wkt_too_large_by_byte_size() -> None:
    cfg = GeoConfig(_env_file=None, limits=LimitsConfig(max_wkt_bytes=50))
    client = _client(cfg)
    response = client.post("/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "x"})
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.WKT_TOO_LARGE


def test_wkt_parse_error(client: TestClient) -> None:
    response = client.post("/v1/requests", json={"wkt": BROKEN, "prompt": "x"})
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.WKT_PARSE_ERROR


def test_unsupported_geometry(client: TestClient) -> None:
    response = client.post("/v1/requests", json={"wkt": LINE, "prompt": "x"})
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.UNSUPPORTED_GEOMETRY


def test_empty_geometry_is_invalid(client: TestClient) -> None:
    response = client.post("/v1/requests", json={"wkt": EMPTY, "prompt": "x"})
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.INVALID_GEOMETRY


def test_wkt_too_large_by_vertex_count() -> None:
    cfg = GeoConfig(_env_file=None, limits=LimitsConfig(max_vertices=3))
    client = _client(cfg)
    response = client.post("/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "x"})
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.WKT_TOO_LARGE


def test_out_of_range(client: TestClient) -> None:
    response = client.post("/v1/requests", json={"wkt": OUT_OF_RANGE, "prompt": "x"})
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.OUT_OF_RANGE


def test_invalid_geometry_bowtie(client: TestClient) -> None:
    response = client.post("/v1/requests", json={"wkt": BOWTIE, "prompt": "x"})
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.INVALID_GEOMETRY


def test_buffer_not_applicable(client: TestClient) -> None:
    response = client.post(
        "/v1/requests",
        json={"wkt": HANDOFF_POLYGON, "prompt": "x", "point_buffer_m": 100.0},
    )
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.BUFFER_NOT_APPLICABLE


def test_buffer_override_not_allowed(client: TestClient) -> None:
    response = client.post(
        "/v1/requests", json={"wkt": TLV_POINT, "prompt": "x", "point_buffer_m": 100.0}
    )
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.BUFFER_OVERRIDE_NOT_ALLOWED


def test_buffer_radius_out_of_range() -> None:
    cfg = GeoConfig(
        _env_file=None,
        point_buffer=PointBufferConfig(
            allow_request_override=True, min_radius_m=1.0, max_radius_m=5_000.0
        ),
    )
    client = _client(cfg)
    response = client.post(
        "/v1/requests", json={"wkt": TLV_POINT, "prompt": "x", "point_buffer_m": 50_000.0}
    )
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.BUFFER_RADIUS_OUT_OF_RANGE


def test_antimeridian_crossing_is_out_of_range() -> None:
    cfg = GeoConfig(
        _env_file=None,
        point_buffer=PointBufferConfig(
            allow_request_override=True, min_radius_m=1.0, max_radius_m=5_000.0
        ),
    )
    client = _client(cfg)
    response = client.post(
        "/v1/requests",
        json={"wkt": "POINT(179.999 0)", "prompt": "x", "point_buffer_m": 5_000.0},
    )
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.OUT_OF_RANGE


def test_area_too_large(client: TestClient) -> None:
    response = client.post("/v1/requests", json={"wkt": HUGE, "prompt": "x"})
    assert response.status_code == 422
    assert response.json()["code"] == ErrorCode.AREA_TOO_LARGE


def test_openapi_documents_error_envelope_for_422(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    responses = schema["paths"]["/v1/requests"]["post"]["responses"]
    assert "422" in responses
    ref = responses["422"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("ErrorEnvelope")
