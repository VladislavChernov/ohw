from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from graphrag_proto.demo_ui.client import DemoClient, Settings
from graphrag_proto.pre_ingest.generate import rule_candidates

# Имя модели LLM обязано приходить из окружения: в коде больше нет зашитого имени,
# потому что llama.cpp-сервер принимает в поле `model` любое значение и отдаёт ту
# модель, что загружена - опечатка не давала ошибки, а тихо работала. Здесь
# объявляется фиктивное значение для юнит-тестов, где реальный HTTP-вызов не
# делается; сам контраст проверяет test_llm_model_must_be_declared.
os.environ.setdefault("LLM_MODEL", "test-model-not-used-for-http")


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

# --- Кандидаты сущностей для тестов извлечения (ADR-053) ----------------------
# Извлечение без списка кандидатов не выполняется вовсе: документ без списка
# грузится только в вектор. Поэтому тест извлечения обязан построить список так же,
# как его строит стадия `pre_ingest`, - с настоящим файлом документа и настоящим
# `sha256` от его содержимого. Подделка здесь не годилась бы: проверка свежести
# существует ровно ради файла, и заглушка проверила бы не её.


def write_candidate_envelope(
    directory: Path,
    source_name: str,
    text: str,
    candidates: list[str],
) -> Path:
    """Кладёт `<имя>_candidates.json` рядом с тестовым документом, как это делает стадия."""
    envelope = {
        "source": source_name,
        "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source_bytes": len(text.encode("utf-8")),
        "count": len(candidates),
        "candidates": candidates,
    }
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{source_name}_candidates.json"
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    return path


#: Фабрика ставится фикстурой и живёт в модуле, чтобы тестам не пришлось объявлять её
#: в сигнатуре: иначе правка касалась бы сорока функций вместо одной строки на файл.
_FACTORY: Any = None
_LISTS_DIR: Path | None = None


@pytest.fixture()
def candidate_env_setup(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """Готовит кандидатов рядом с документами на время теста.

    Объявляется через `pytestmark` в файлах, где идёт извлечение. Не автоскупная: папка
    кандидатов не должна появляться у тестов, которые её не проверяют.

    С 2026-10-14 кандидаты лежат РЯДОМ с файлом документа, а не в отдельной папке, и путь
    никуда не задаётся снаружи (ADR-053). Фикстура кладёт файл туда же, куда его положил бы
    пайплайн, поэтому проверки `load_candidates` видят то же, что увидит разбор.
    """
    global _FACTORY, _LISTS_DIR
    documents = tmp_path / "documents"
    documents.mkdir(parents=True, exist_ok=True)
    _LISTS_DIR = documents

    def factory(ctx: Any, candidates: list[str] | None = None) -> Any:
        # Текст берётся из файла документа, если он уже есть. Чанками пользоваться нельзя:
        # на момент вызова фикстуры конвейер ещё не работал, `ctx.chunks` пуст, и список
        # строился бы из пустого текста - то есть ограничение проверялось бы вхолостую.
        text = ""
        if ctx.source_path:
            document = Path(ctx.source_path)
            if document.is_file():
                text = document.read_text(encoding="utf-8")
        if not text:
            text = ctx.raw_text or "\n\n".join(ctx.chunks)
        source_name = str(ctx.source_url).rsplit("/", 1)[-1]
        if not ctx.source_path:
            document = documents / source_name
            document.write_text(text, encoding="utf-8")
            ctx.source_path = str(document)
        # Кандидаты по умолчанию берутся тем же правилом, что и в стадии pre-ingest.
        # Свой замороженный список в тесте означал бы, что ограничение проверяется
        # против выдуманного набора имён, а не против того, что на самом деле нашлось
        # в тексте.
        names = sorted(rule_candidates(text)) if candidates is None else candidates
        write_candidate_envelope(document.parent, document.name, text, names)
        ctx.raw_text = text
        return ctx

    _FACTORY = factory
    # Сброс обязателен, и делается `addfinalizer`, а не `finally`: фикстура без `yield`
    # не имеет блока, который гарантированно выполнится. Иначе модуль, забывший про
    # `usefixtures`, получил бы папку ПРЕДЫДУЩЕГО теста и тихо проверил бы не то, а
    # ошибка «фикстура не объявлена» срабатывала бы только на первом тесте сессии.
    request.addfinalizer(lambda: _reset_candidate_env())


def _reset_candidate_env() -> None:
    global _FACTORY, _LISTS_DIR
    _FACTORY = None
    _LISTS_DIR = None


def candidate_env(ctx: Any, candidates: list[str] | None = None) -> Any:
    """Кладёт документ и его список кандидатов, возвращает тот же контекст.

    Вызывать без подготовки нельзя: без `candidate_env_setup` фабрика не задана, и падать
    должно рано и внятно, а не «список не найден, документ ушёл в вектор».
    """
    if _FACTORY is None:
        raise RuntimeError(
            "фикстура candidate_env_setup не объявлена: добавьте "
            "pytest.mark.usefixtures('candidate_env_setup') в модуль"
        )
    return _FACTORY(ctx, candidates)


def candidate_env_for(path: Path, candidates: list[str] | None = None) -> None:
    """Кладёт список кандидатов для УЖЕ существующего файла документа.

    Нужен тестам, которые идут через `Executor` и создают файл сами: там нет контекста,
    к которому можно было бы привязаться, но ограничение имён всё равно действует.
    """
    if _LISTS_DIR is None:
        raise RuntimeError(
            "фикстура candidate_env_setup не объявлена: добавьте "
            "pytest.mark.usefixtures('candidate_env_setup') в модуль"
        )
    text = path.read_text(encoding="utf-8")
    names = sorted(rule_candidates(text)) if candidates is None else candidates
    # Рядом с документом: путь выводится из файла, отдельной папки больше нет.
    write_candidate_envelope(path.parent, path.name, text, names)