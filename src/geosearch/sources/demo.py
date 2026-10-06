"""Demo tool (source_id 9001): proves the registry end to end with no external service.

`demo.sample_points` is deterministic (seeded RNG over the area's bbox, kept
only where `contains` accepts), so tests and evals can assert exact output.
Its seed entry lives in registry/seeds/demo.yaml and is loaded only on request.
"""

import random

from pydantic import BaseModel, Field

from geosearch.registry.handlers import HandlerContext, ToolResult, register_handler

# Bounds the rejection sampling: a thin or holey area can reject most bbox
# samples, and the tool must still return promptly with whatever it found.
_ATTEMPTS_PER_POINT = 200


class SamplePointsInput(BaseModel):
    count: int = Field(ge=1, le=50, description="How many points to return.")
    seed: int = Field(default=0, description="Random seed; the same seed gives the same points.")


class PointRow(BaseModel):
    lon: float
    lat: float


DEMO_SOURCE_ID = 9001


@register_handler(
    source_id=DEMO_SOURCE_ID, input_model=SamplePointsInput, output_model=PointRow, uses_area=True
)
def sample_points(args: SamplePointsInput, ctx: HandlerContext) -> ToolResult:
    assert ctx.area_id is not None  # uses_area=True guarantees it
    min_lon, min_lat, max_lon, max_lat = ctx.area_ops.bbox(ctx.area_id)
    rng = random.Random(args.seed)

    points: list[dict[str, float]] = []
    for _ in range(args.count * _ATTEMPTS_PER_POINT):
        if len(points) == args.count:
            break
        lon = round(rng.uniform(min_lon, max_lon), 6)
        lat = round(rng.uniform(min_lat, max_lat), 6)
        if ctx.area_ops.contains(ctx.area_id, lon, lat):
            points.append({"lon": lon, "lat": lat})

    return ToolResult(
        summary=f"Sampled {len(points)} random point(s) inside the area (seed {args.seed}).",
        data={"count": len(points)},
        artifact=points,
    )
