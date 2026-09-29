"""Фабрика адаптеров retrieval-контура (M2+topology, L1-02/L1-03).

Выбор транспорта — конфигурация, не код ядра. Два источника:
- env (M2): `GRAPH_STORE`, `VECTOR_STORE`, `EMBEDDER`, `RERANKER`, `LLM_ADAPTER`;
- карта адаптеров из Topology Orchestrator (M3, add-topology-adapters): явные слоты
  карты перекрывают env на уровне выбора провайдера («переключение без рестарта»).

Параметры соединений всегда из env (не зависит от карты провайдеров):
`NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD`, `LLM_BASE_URL/LLM_MODEL/LLM_TEMPERATURE/LLM_MAX_TOKENS`.
Слоты и каталог провайдеров — SSOT `infra/config/namespaces.yaml` (namespace `adapters`).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from graphrag_proto.retrieval.adapters.base import (
    Embedder,
    GraphStoreProvider,
    LLMInference,
    Reranker,
    VectorStoreProvider,
)
from graphrag_proto.retrieval.adapters.bge import BgeM3ServiceAdapter, BgeRerankerAdapter
from graphrag_proto.retrieval.adapters.deterministic import DeterministicEmbedder
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore, InMemoryVectorStore
from graphrag_proto.retrieval.adapters.llm import (
    FakeLLM,
    OpenAICompatibleAdapter,
)
from graphrag_proto.retrieval.adapters.neo4j import Neo4jGraphStore, Neo4jVectorStore
from graphrag_proto.retrieval.adapters.reranker import NoOpRerankerAdapter

SLOT_GRAPH_STORE = "graph_store"
SLOT_VECTOR_STORE = "vector_store"
SLOT_EMBEDDINGS = "embeddings"
SLOT_RERANKER = "reranker"
SLOT_LLM = "llm"

ADAPTER_CATALOG: dict[str, list[str]] = {
    SLOT_GRAPH_STORE: ["neo4j", "inmemory"],
    SLOT_VECTOR_STORE: ["neo4j", "inmemory"],
    SLOT_EMBEDDINGS: ["deterministic", "bge_m3_service"],
    SLOT_RERANKER: ["noop", "bge_reranker"],
    SLOT_LLM: ["openai", "fake"],
}

_DEFAULT_ADAPTERS: dict[str, str] = {
    SLOT_GRAPH_STORE: "inmemory",
    SLOT_VECTOR_STORE: "inmemory",
    SLOT_EMBEDDINGS: "deterministic",
    SLOT_RERANKER: "noop",
    SLOT_LLM: "openai",
}

_ENV_KEYS: dict[str, str] = {
    SLOT_GRAPH_STORE: "GRAPH_STORE",
    SLOT_VECTOR_STORE: "VECTOR_STORE",
    SLOT_EMBEDDINGS: "EMBEDDER",
    SLOT_RERANKER: "RERANKER",
    SLOT_LLM: "LLM_ADAPTER",
}


@dataclass
class Adapters:
    """Собранный набор провайдеров retrieval-контура."""

    graph_store: GraphStoreProvider
    vector_store: VectorStoreProvider
    embedder: Embedder
    reranker: Reranker
    llm: LLMInference


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _build_graph(kind: str) -> GraphStoreProvider:
    kind = kind.strip().lower()
    if kind == "inmemory":
        return InMemoryGraphStore()
    if kind == "neo4j":
        return Neo4jGraphStore(
            uri=_env("NEO4J_URI", "bolt://neo4j:7687"),
            user=_env("NEO4J_USER", "neo4j"),
            password=_env("NEO4J_PASSWORD", "graphrag"),
        )
    raise ValueError(f"graph_store={kind!r}: допустимо {', '.join(ADAPTER_CATALOG[SLOT_GRAPH_STORE])}")


def _build_vector(kind: str) -> VectorStoreProvider:
    kind = kind.strip().lower()
    if kind == "inmemory":
        return InMemoryVectorStore()
    if kind == "neo4j":
        return Neo4jVectorStore(
            uri=_env("NEO4J_URI", "bolt://neo4j:7687"),
            user=_env("NEO4J_USER", "neo4j"),
            password=_env("NEO4J_PASSWORD", "graphrag"),
        )
    raise ValueError(f"vector_store={kind!r}: допустимо {', '.join(ADAPTER_CATALOG[SLOT_VECTOR_STORE])}")


def _build_embedder(kind: str) -> Embedder:
    kind = kind.strip().lower()
    if kind == "deterministic":
        return DeterministicEmbedder()
    if kind == "bge_m3_service":
        return BgeM3ServiceAdapter.from_env()
    raise ValueError(f"embeddings={kind!r}: допустимо {', '.join(ADAPTER_CATALOG[SLOT_EMBEDDINGS])}")


def _build_reranker(kind: str) -> Reranker:
    kind = kind.strip().lower()
    if kind in ("noop", "none", "disabled", ""):
        return NoOpRerankerAdapter()
    if kind == "bge_reranker":
        return BgeRerankerAdapter.from_env()
    raise ValueError(f"reranker={kind!r}: допустимо {', '.join(ADAPTER_CATALOG[SLOT_RERANKER])}")


logger = logging.getLogger(__name__)

# Ключ в профиле `llm` -> имя переменной окружения, которой стенд может переопределить
# профиль. Порядок разрешения: env (явное намерение стенда) > профиль > ошибка.
_LLM_SETTINGS: dict[str, str] = {
    "base_url": "LLM_BASE_URL",
    "model": "LLM_MODEL",
    "temperature": "LLM_TEMPERATURE",
    "max_tokens": "LLM_MAX_TOKENS",
    "timeout_s": "LLM_TIMEOUT_S",
    # `context_window` был объявлен в профиле и не читался нигде, из-за чего у
    # `max_tokens` не было ни одной точки сверки: значение могло превышать окно модели,
    # и тогда каждый ответ обрывался по длине, а обрыв не проверялся и выглядел как
    # битый JSON, а для EXTRACT с optional_failure - как тихая деградация.
    "context_window": "LLM_CONTEXT_WINDOW",
    # Необязательный: нужен только при ненулевой температуре. При `temperature = 0`
    # жадный декодер детерминирован и seed ничего не добавляет.
    "seed": "LLM_SEED",
}

_LLM_PROFILE_PATH = "infra/config/namespaces.yaml"


def _llm_profile() -> Mapping[str, object]:
    """Блок `llm` из SSOT-профиля `infra/config/namespaces.yaml` (docs/04 §5)."""
    from graphrag_proto.config_service.namespaces import load_namespaces

    env = os.environ.get("NAMESPACES_PATH", "").strip()
    block = load_namespaces(Path(env) if env else Path(_LLM_PROFILE_PATH)).get("llm")
    return block if isinstance(block, Mapping) else {}


def _llm_setting(key: str, profile: Mapping[str, object]) -> str:
    """Настройка LLM из env или профиля. Инлайновых дефолтов нет намеренно.

    Раньше здесь стояли `http://llm:8080`, `qwen2.5-coder-7b-instruct-abliterated-
    q4_k_m`, 0.3, 2048 и таймаут, и это плохо работало. LLM-сервер принимает в поле
    `model` любое значение и отдаёт ту модель, что загружена, поэтому опечатка в имени
    не давала ошибки, а тихо работала; опечатка в адресе уводила на другой стенд или
    в никуда. Угадывать значение значит превратить ошибку конфигурации в тихую
    неверную работу, поэтому отсутствие ключа - это ошибка с логом, а не повод
    подставить что-нибудь.
    """
    env_name = _LLM_SETTINGS[key]
    raw = os.environ.get(env_name, "").strip() or str(profile.get(key, "")).strip()
    if not raw:
        source = os.environ.get("NAMESPACES_PATH", "").strip() or _LLM_PROFILE_PATH
        message = (
            f"{env_name} не задан, и ключа `{key}` нет в блоке `llm` профиля ({source}). "
            f"Настройка LLM обязана объявляться явно: env переопределяет профиль."
        )
        logger.error(message)
        raise RuntimeError(message)
    return raw


def _llm_number(
    key: str, profile: Mapping[str, object], cast: Callable[[str], float | int]
) -> float | int:
    raw = _llm_setting(key, profile)
    try:
        return cast(raw)
    except ValueError as exc:
        message = f"{_LLM_SETTINGS[key]}={raw!r} не приводится к {cast.__name__}: {exc}"
        logger.error(message)
        raise RuntimeError(message) from exc


def llm_setting(key: str) -> str:
    """Разрешённое значение настройки LLM: env стенда > профиль > ошибка.

    Публично, потому что правило разрешения должно быть одно. `run_eval.py` строит
    адаптер для судьи мимо фабрики, и своя копия этого правила разъехалась бы с
    фабрикой при первой же правке - сначала env, потом профиль или наоборот.
    """
    return _llm_setting(key, _llm_profile())


def _optional_llm_setting(key: str, profile: Mapping[str, object]) -> str:
    """Значение настройки, которой может не быть, - молча, без ошибки.

    Отдельная функция, а не `os.environ.get(...)` в одну строку, потому что рядом стоит
    `_llm_setting`, который по замыслу **обязателен**: отсутствие - ошибка конфигурации.
    Смешивать эти два поведения в одном хелпере нельзя, иначе либо сломается любой профиль
    без необязательного ключа, либо необязательный ключ станет обязательным молча.
    `context_window` именно необязательный: это метаданные для сверки, а не параметр
    адаптера, и профиль без него остаётся рабочим - просто проверить бюджет нечем.
    """
    value = os.environ.get(_LLM_SETTINGS.get(key, key), "").strip()
    return value or str(profile.get(key) or "").strip()


def _check_token_budget(profile: Mapping[str, object]) -> None:
    """`max_tokens` обязан влезать в окно модели — иначе ответ будет обрезан всегда.

    Проверка на сборке, а не на запросе: подсчитать токены промпта без токенизатора
    можно только эвристикой по символам, а ложное срабатывание на кириллице хуже, чем
    грубая, но однозначная ошибка конфигурации. Фактический обрыв ловится отдельно, по
    `finish_reason` в ответе.

    Значения берутся тем же разрешением env > профиль, что и при сборке адаптера: раньше
    в этой функции читался сырой профиль, и проверка молча проходила, когда стенд задавал
    `max_tokens` через переменную окружения, то есть ровно в том случае, ради которого
    проверка и нужна. Сама сверка необязательна: нет окна - нечего сверять.
    """
    raw_window = _optional_llm_setting("context_window", profile)
    raw_budget = _optional_llm_setting("max_tokens", profile)
    if not raw_window or not raw_budget:
        return
    try:
        window = int(raw_window)
        budget = int(raw_budget)
    except ValueError:
        return
    if window <= 0 or budget <= window:
        return
    message = (
        f"LLM_MAX_TOKENS={budget} больше окна модели ({_LLM_SETTINGS['context_window']}={window}). "
        f"Каждый ответ будет обрезан по длине."
    )
    logging.getLogger(__name__).error(message)
    raise RuntimeError(message)


def _build_llm(
    kind: str,
    *,
    temperature: float | None = None,
) -> LLMInference:
    kind = kind.strip().lower()
    if kind == "fake":
        return FakeLLM()
    if kind == "openai":
        profile = _llm_profile()
        _check_token_budget(profile)
        raw_seed = _optional_llm_setting("seed", profile)
        seed: int | None = None
        if raw_seed:
            # Передаётся, а не хранится: при `temperature = 0` жадный декодер и так
            # детерминирован, и seed не нужен. Нужен он ровно тогда, когда кто-то осознанно
            # вернул ненулевую температуру - и тогда без него воспроизводимости нет.
            try:
                seed = int(raw_seed)
            except ValueError as exc:
                message = f"{_LLM_SETTINGS['seed']}={raw_seed!r} не целое число"
                logging.getLogger(__name__).error(message)
                raise RuntimeError(message) from exc
        return OpenAICompatibleAdapter(
            base_url=_llm_setting("base_url", profile),
            model=_llm_setting("model", profile),
            temperature=float(_llm_number("temperature", profile, float))
            if temperature is None
            else temperature,
            max_tokens=int(_llm_number("max_tokens", profile, int)),
            timeout_s=float(_llm_number("timeout_s", profile, float)),
            seed=seed,
        )
    raise ValueError(f"llm={kind!r}: допустимо {', '.join(ADAPTER_CATALOG[SLOT_LLM])}")


def _resolve_slot(slot: str, adapter_map: Mapping[str, str] | None) -> str:
    """Провайдер слота: карта топологии > env > дефолт (каталог валидируется)."""
    catalog = ADAPTER_CATALOG[slot]
    default = _DEFAULT_ADAPTERS[slot]
    if adapter_map is not None and slot in adapter_map:
        provider = str(adapter_map[slot]).strip().lower()
    else:
        provider = _env(_ENV_KEYS[slot], default).strip().lower()
    if slot == SLOT_RERANKER and provider in ("none", "disabled", ""):
        provider = "noop"
    if not provider:
        provider = default
    if provider not in catalog:
        raise ValueError(f"{slot}={provider!r}: допустимо {', '.join(catalog)}")
    return provider


def build_adapters(adapter_map: Mapping[str, str] | None = None) -> Adapters:
    """Сборка всех провайдеров по карте топологии/env (M3, add-topology-adapters).

    `adapter_map` — карта слотов из Topology (операторский выбор); отсутствующие слоты
    резолвятся из env/дефолтов. `None` — поведение M2 (только env/дефолт).
    """
    kinds = {slot: _resolve_slot(slot, adapter_map) for slot in ADAPTER_CATALOG}
    return Adapters(
        graph_store=_build_graph(kinds[SLOT_GRAPH_STORE]),
        vector_store=_build_vector(kinds[SLOT_VECTOR_STORE]),
        embedder=_build_embedder(kinds[SLOT_EMBEDDINGS]),
        reranker=_build_reranker(kinds[SLOT_RERANKER]),
        llm=_build_llm(kinds[SLOT_LLM]),
    )


def build_graph_store() -> GraphStoreProvider:
    return _build_graph(_resolve_slot(SLOT_GRAPH_STORE, None))


def build_vector_store() -> VectorStoreProvider:
    return _build_vector(_resolve_slot(SLOT_VECTOR_STORE, None))


def build_embedder() -> Embedder:
    return _build_embedder(_resolve_slot(SLOT_EMBEDDINGS, None))


def build_reranker() -> Reranker:
    return _build_reranker(_resolve_slot(SLOT_RERANKER, None))


def build_llm(*, temperature: float | None = None) -> LLMInference:
    """Адаптер LLM для потребителя.

    `temperature` - точечное переопределение, а не глобальная настройка: извлечению нужна
    детерминированность, генерации ответа на вопрос - нет. Кто вызывает и с каким
    значением, видно в точке вызова, а не спрятано в профиле, где одна величина обслуживала
    оба случая.
    """
    return _build_llm(_resolve_slot(SLOT_LLM, None), temperature=temperature)