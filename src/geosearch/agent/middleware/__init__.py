"""Our own middleware. Ordering matters and is fixed in agent/build.py."""

from geosearch.agent.middleware.area_summary import AreaSummaryMiddleware

__all__ = ["AreaSummaryMiddleware"]
