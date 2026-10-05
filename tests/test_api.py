import pytest
from conftest import BOWTIE, BROKEN, EMPTY, HANDOFF_POLYGON, HUGE, LINE, OUT_OF_RANGE, TLV_POINT
from fastapi.testclient import TestClient

from geosearch.api.app import create_app
from geosearch.config import GeoConfig, LimitsConfig, PointBufferConfig
from geosearch.errors import ErrorCode


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(GeoConfig(_env_file=None)))


def test_handoff_polygon_returns_200(client: TestClient) -> None:
    response = client.post(
        "/v1/requests", json={"wkt": HANDOFF_POLYGON, "prompt": "find a good restaurant"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["area_id"].startswith("area_")
    assert body["area_km2"] == pytest.approx(26.2, rel=0.02)
    assert "area_summary" in body


def test_buffered_point_returns_200_with_buffer_info(client: TestClient) -> None:
    response = client.post(
        "/v1/requests", json={"wkt": TLV_POINT, "prompt": "find a good restaurant"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["input_geometry_type"] == "Point"
    assert body["buffer_radius_m"] == 10.0
    assert body["buffer_strategy"] == "geodesic_circle"
    assert any("buffered" in note for note in body["notes"])


# --- one 422 test per error code ---


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
    client = TestClient(create_app(cfg))
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
    client = TestClient(create_app(cfg))
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
    client = TestClient(create_app(cfg))
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
    client = TestClient(create_app(cfg))
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


def test_client_supplied_area_id_is_ignored(client: TestClient) -> None:
    response = client.post(
        "/v1/requests",
        json={"wkt": HANDOFF_POLYGON, "prompt": "x", "area_id": "area_hacked"},
    )
    assert response.status_code == 200
    assert response.json()["area_id"] != "area_hacked"


def test_openapi_documents_error_envelope_for_422(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    responses = schema["paths"]["/v1/requests"]["post"]["responses"]
    assert "422" in responses
    ref = responses["422"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("ErrorEnvelope")
