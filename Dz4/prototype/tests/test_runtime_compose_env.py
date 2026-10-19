"""Окружение пайплайна запроса в runtime-compose: один контур, одна настройка.

Замер 2026-10-17 показал, что `RETRIEVAL_GRAPH_ENABLED` была объявлена только у
`query-worker`: контейнер `query-api` её не получал. Само по себе это было безобидно —
api отдаёт задание воркеру и пайплайн не исполняет. Но обход очереди ведёт к синхронному
пути, и тогда api исполнил бы пайплайн с дефолтом профиля, то есть **ось графа была бы
включена при выключенной настройке оператора**.

Дефект этого класса не виден ни в одном существующем тесте: демо-скрипты сверяются с
compose на предмет парности (`test_demo_scripts_parity.py`), а здесь речь о согласованности
двух сервисов МЕЖДУ СОБОЙ. Поэтому утверждение отдельное и узкое.

Правило проверяется как положительный инвариант, а не как список запретов: компонент,
способный исполнить пайплайн, объявляет флаг оси с тем же дефолтом. Добавление нового
сервиса, исполняющего пайплайн, потребует внести его в список — и тест об этом скажет.
"""

from __future__ import annotations

import pathlib
import re

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "prototype" / "infra" / "compose.runtime.yaml"

#: Сервисы, которые могут дойти до исполнения пайплайна запроса. Сегодня задание всегда
#: уходит в воркер, поэтому api в списке — по инварианту «может», а не по факту.
PIPELINE_SERVICES = ("query-api", "query-worker")

#: Настройки, обязательные для обоих. Ключ — имя переменной окружения, значение —
#: разбираемое объявление `${ПЕРЕМЕННАЯ:-дефолт}`; проверяется совпадение источника И дефолта.
SHARED_OVERRIDES = ("RETRIEVAL_GRAPH_ENABLED",)


def _environment(service: str) -> dict[str, object]:
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = document.get("services") or {}
    assert service in services, f"сервис {service!r} исчез из compose"
    environment = services[service].get("environment") or {}
    return {str(key): value for key, value in environment.items()}


def _declared(expression: object) -> tuple[str, str]:
    """Разобрать `${ПЕРЕМЕННАЯ:-дефолт}` в пару (источник, дефолт).

    Разбираем строго, а не «похоже на»: молчаливая подмена дефолта в одном сервисе —
    ровно тот дефект, который ловится.
    """
    assert isinstance(expression, str), f"ожидалась строка, получено {expression!r}"
    match = re.fullmatch(r"\$\{([A-Z0-9_]+):-([^{}]*)\}", expression.strip())
    assert match, f"не разобранное объявление: {expression!r}"
    return match.group(1), match.group(2)


def test_pipeline_services_declare_shared_overrides() -> None:
    """Каждый компонент, исполняющий пайплайн, объявляет общие настройки одинаково."""
    missing: list[str] = []
    mismatched: list[str] = []
    for name in SHARED_OVERRIDES:
        declared: dict[str, tuple[str, str]] = {}
        for service in PIPELINE_SERVICES:
            environment = _environment(service)
            if name not in environment:
                missing.append(f"{service}: нет {name}")
                continue
            declared[service] = _declared(environment[name])
        if len(declared) == len(PIPELINE_SERVICES) and len(set(declared.values())) > 1:
            details = "; ".join(f"{s}={src}:{dflt}" for s, (src, dflt) in sorted(declared.items()))
            mismatched.append(f"{name}: {details}")
    assert not missing, "общая настройка объявлена не у всех:\n" + "\n".join(missing)
    assert not mismatched, "общая настройка разошлась между сервисами:\n" + "\n".join(mismatched)


def test_shared_override_source_is_the_environment_variable() -> None:
    """Источник — переменная окружения, а не литерал.

    Литерал означает, что переопределение оператором невозможно: стенд соберётся с одним
    значением при любом окружении, и измерение нельзя будет повторить.
    """
    for name in SHARED_OVERRIDES:
        for service in PIPELINE_SERVICES:
            source, _default = _declared(_environment(service)[name])
            assert source == name, (
                f"{service}: {name} объявлена как {source!r}, а не как {name!r}"
            )
