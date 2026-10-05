"""Shared fixtures: WKT samples and config, per the Stage 1 spec."""

import pytest

from geosearch.config import GeoConfig

HANDOFF_POLYGON = (
    "POLYGON((34.75 32.05, 34.80 32.05, 34.80 32.10, 34.75 32.10, 34.75 32.05))"
)
TLV_POINT = "POINT(34.78 32.08)"
POINT_Z = "POINT Z (34.78 32.08 10)"
C_SHAPE = "POLYGON((0 0, 10 0, 10 1, 1 1, 1 9, 10 9, 10 10, 0 10, 0 0))"
WITH_HOLE = "POLYGON((0 0, 4 0, 4 4, 0 4, 0 0), (1 1, 3 1, 3 3, 1 3, 1 1))"
TWO_SQUARES = "MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)), ((5 5, 6 5, 6 6, 5 6, 5 5)))"
BOWTIE = "POLYGON((0 0, 1 1, 1 0, 0 1, 0 0))"
OUT_OF_RANGE = "POLYGON((200 0, 201 0, 201 1, 200 1, 200 0))"
LINE = "LINESTRING(34.75 32.05, 34.80 32.10)"
EMPTY = "POLYGON EMPTY"
BROKEN = "POLYGON((34.75 32.05, 34.80"
HUGE = "POLYGON((0 0, 2 0, 2 2, 0 2, 0 0))"


@pytest.fixture
def cfg() -> GeoConfig:
    """Default configuration, isolated from any environment overrides."""
    return GeoConfig(_env_file=None)
