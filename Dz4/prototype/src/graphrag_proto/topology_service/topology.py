"""Загрузка и валидация `infra_topology.yaml` (ADR-019, add-topology-adapters).

Структура файла — docs/00 (топология): `version/environment/network/providers/
endpoints/startup`. Провайдеры обязаны быть **реализованными** id каталога фабрики
(`retrieval/adapters/factory.py::ADAPTER_CATALOG`), иначе сервис не стартует:
эффективная карта должны быть сборкой без прожект-значений.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from graphrag_proto.topology_service.catalog import SLOT_ORDER, available_providers

REQUIRED_TOP_KEYS = ("version", "environment", "network", "providers", "endpoints", "startup")


class TopologyError(ValueError):
    """Ошибка структуры/семантики файла топологии."""


def load_topology(path: str | Path) -> dict[str, Any]:
    """Парсинг + строгая валидация файла топологии инсталляции."""
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise TopologyError("файл топологии должен быть YAML-маппингом")
    missing = [key for key in REQUIRED_TOP_KEYS if key not in data]
    if missing:
        raise TopologyError(f"отсутствуют обязательные ключи: {missing}")

    providers = data.get("providers")
    if not isinstance(providers, dict):
        raise TopologyError("поле providers должно быть маппингом")

    catalog = available_providers()
    for slot in SLOT_ORDER:
        value = providers.get(slot)
        if not isinstance(value, str) or value.strip().lower() not in catalog[slot]:
            raise TopologyError(
                f"providers.{slot} должен быть реализованным провайдером "
                f"из {catalog[slot]}, отдан {value!r}"
            )
    return data


def base_providers(topology: dict[str, Any]) -> dict[str, str]:
    """Базовая карта адаптеров из providers (без override'ов)."""
    providers = topology["providers"]
    return {slot: str(providers[slot]).strip().lower() for slot in SLOT_ORDER}