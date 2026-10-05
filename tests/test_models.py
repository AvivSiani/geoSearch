import pytest
from pydantic import ValidationError

from geosearch.errors import ErrorCode, GeoValidationError
from geosearch.request.models import ErrorEnvelope, UserRequest, ValidatedRequest


def test_user_request_defaults() -> None:
    req = UserRequest(wkt="POINT(1 1)", prompt="find food")
    assert req.point_buffer_m is None


def test_user_request_requires_prompt() -> None:
    # prompt is always required...
    with pytest.raises(ValidationError):
        UserRequest(wkt="POINT(1 1)")


def test_user_request_wkt_optional_for_followups() -> None:
    # ...but wkt is optional (a follow-up omits it), as is conversation_id.
    req = UserRequest(prompt="find food")
    assert req.wkt is None
    assert req.conversation_id is None
    follow_up = UserRequest(prompt="and again?", conversation_id="abc123")
    assert follow_up.conversation_id == "abc123"


def test_validated_request_round_trip() -> None:
    vr = ValidatedRequest(
        request_id="a" * 32,
        area_id="area_abc123def456",
        prompt="find food",
        input_geometry_type="Point",
        area_geometry_type="Polygon",
        buffer_radius_m=10.0,
        buffer_strategy="geodesic_circle",
        bbox=(34.77, 32.07, 34.79, 32.09),
        area_km2=0.0003,
        representative_point=(34.78, 32.08),
        area_summary="area_abc123def456 · circle from Point · r=10 m",
        notes=["Point buffered to a 10 m geodesic circle"],
    )
    assert vr.input_geometry_type == "Point"
    assert vr.bbox == (34.77, 32.07, 34.79, 32.09)


def test_validated_request_rejects_bad_geometry_type() -> None:
    with pytest.raises(ValidationError):
        ValidatedRequest(
            request_id="a" * 32,
            area_id="area_abc123def456",
            prompt="find food",
            input_geometry_type="LineString",
            area_geometry_type="Polygon",
            buffer_radius_m=None,
            buffer_strategy=None,
            bbox=(0.0, 0.0, 1.0, 1.0),
            area_km2=1.0,
            representative_point=(0.5, 0.5),
            area_summary="x",
            notes=[],
        )


def test_error_envelope_serializes_code_as_string() -> None:
    env = ErrorEnvelope(code=ErrorCode.MISSING_WKT, message="wkt is required")
    dumped = env.model_dump()
    assert dumped["code"] == "MISSING_WKT"
    assert dumped["details"] == {}


def test_geo_validation_error_defaults_details_to_empty_dict() -> None:
    err = GeoValidationError(ErrorCode.INVALID_GEOMETRY, "bad geometry")
    assert err.details == {}
    assert err.code == ErrorCode.INVALID_GEOMETRY
    assert str(err) == "bad geometry"


def test_geo_validation_error_details_not_shared_between_instances() -> None:
    a = GeoValidationError(ErrorCode.INVALID_GEOMETRY, "a")
    b = GeoValidationError(ErrorCode.INVALID_GEOMETRY, "b")
    a.details["x"] = 1
    assert b.details == {}
