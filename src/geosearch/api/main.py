"""The ASGI entry point: `uvicorn geosearch.api.main:app`.

Importing this module builds the app, which connects to MongoDB and builds the
model and agent. Everything else (tests, scripts, evals) imports `create_app`
from api/app.py instead, so importing it has no side effects.
"""

from geosearch.api.app import create_app

app = create_app()
