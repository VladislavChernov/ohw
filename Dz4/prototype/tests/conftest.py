from __future__ import annotations

import os
from pathlib import Path
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

# --- Корень проекта для тестов, которым нужны документы -------------------------
# Резолвер жил раньше в test_eval_dataset.py как EVAL_ROOT.parents[2], что внутри
# dev-контейнера давало `/` (корень ФС), а не `Dz4/`. Документов там нет, и все
# проверки, которым они нужны, молча выключались: один — вакуумный pass, другой —
# skip. Отсюда расхождение eval-датасетов с документами прошло незамеченным.
#
# Здесь резолвер общий: его используют и линтер датасетов, и линтер согласованности
# документации. Ищем корень по маркеру (docs/ + prototype/), а не по фиксированной
# глубине; `/repo` — та же точка монтирования, что и в compose-сервисе eval-runner,
# поэтому пути вида /repo/docs/... одинаковы в обоих окружениях.

_REPO_HINT = (
    "корень проекта не найден: ожидается каталог с docs/ и prototype/. "
    "В контейнере проект должен быть смонтирован в /repo "
    "(docker run -v <путь-к-Dz4>:/repo:ro) либо задан DZ4_REPO_ROOT."
)


def find_repo_root() -> Path | None:
    candidates = []
    env_root = os.environ.get("DZ4_REPO_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    candidates.append(Path("/repo"))
    candidates.extend(Path(__file__).resolve().parent.parent.parents)
    for candidate in candidates:
        if (candidate / "docs").is_dir() and (candidate / "prototype").is_dir():
            return candidate
    return None


def require_repo_root() -> Path:
    root = find_repo_root()
    if root is None:
        raise AssertionError(_REPO_HINT)
    return root


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return require_repo_root()
