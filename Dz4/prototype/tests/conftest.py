from __future__ import annotations

import os
from typing import Any

import pytest

from graphrag_proto.demo_ui.client import DemoClient, Settings


def pytest_addoption(parser: Any) -> None:
    parser.addoption("--e2e-ingest", action="store", default=None, help="Ingestion API base URL (e2e)")
    parser.addoption("--e2e-query", action="store", default=None, help="Query API base URL (e2e)")
    parser.addoption("--e2e-key", action="store", default=None, help="X-API-Key (e2e)")


@pytest.fixture()
def e2e_client(request: Any) -> DemoClient:
    ingest = request.config.getoption("--e2e-ingest") or os.environ.get("E2E_INGEST")
    query = request.config.getoption("--e2e-query") or os.environ.get("E2E_QUERY")
    key = (
        request.config.getoption("--e2e-key")
        or os.environ.get("E2E_KEY")
        or os.environ.get("GRAPH_AUTH_API_KEY", "changeme")
    )
    if not ingest or not query:
        pytest.skip("e2e-адреса не заданы: --e2e-ingest/--e2e-query или E2E_INGEST/E2E_QUERY")
    return DemoClient(Settings(ingestion_url=ingest, query_url=query, api_key=key))