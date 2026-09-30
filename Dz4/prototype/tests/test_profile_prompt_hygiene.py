"""Имена в примере связи должны иметь происхождение.

Модель не дообучается и копирует пример из инструкции дословно. Пример с выдуманными именами
(`CACHE_LIMIT` -> `CACHE_EVICTION`) не принадлежит ни онтологии, ни корпусу этого домена, и
прогон expC показал, что модель объявила их сущностями и использовала как концы связей:
объявлены они были, поэтому разрешались, и деградации не было, но обе сущности отсутствуют в
документе. Это тихий дефект — ровно тот класс, что прибор ловит как `PROMPT-ONLY`.

Прежняя проверка смотрела только на нумерованные префиксы (`REQ-1:`) и потому пропускала
имена без нумерации. Теперь проверяется происхождение: имя из примера обязано встречаться в
онтологии профиля или в корпусе домена.

Осознанно не проверяется разрешимость примеров через `_validate_edges`. Пример по определению
описывает форму, а не содержимое; требование, чтобы он разрешался в ответе, было бы
неразрешимым. Проверяется другое и единственно возможное: имя взято не из воздуха.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_PROFILES_DIR = Path(__file__).resolve().parents[1] / "domain_profiles"
_CORPUS_DIR = Path(__file__).resolve().parents[2] / "docs"

# Схемы нумерации, которых нет в онтологии и корпусе: REQ-1:, CON-2:, ENT-3: и т.п.
_NUMBERED_PREFIX = re.compile(r"\b[A-Z]{2,}-\d+:")

# Имена, вынимаемые из примера связи. Ключи `from`/`to`, а не любые строки в верхнем регистре:
# иначе под правило попали бы `JSON`, `Requirement`, `Concept`, `REQUIRES_CONSTRAINT` и прочие
# слова инструкции, которые не являются именами сущностей.
_RELATION_NAME = re.compile(r'"(?:from|to)"\s*:\s*"([^"]+)"')


def _user_texts() -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    for path in sorted(_PROFILES_DIR.glob("domain_profile.*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        template = (data.get("extraction") or {}).get("prompt_template") or {}
        user = template.get("user")
        if isinstance(user, str) and user.strip():
            found.append((path, user))
    return found


def test_profiles_with_user_block_exist() -> None:
    assert _user_texts(), "не найден ни один профиль с непустым extraction.prompt_template.user"


@pytest.mark.parametrize("path,user", _user_texts(), ids=lambda v: getattr(v, "name", ""))
def test_user_block_has_no_invented_numbering(path: Path, user: str) -> None:
    hits = sorted(set(_NUMBERED_PREFIX.findall(user)))
    assert not hits, (
        f"{path.name}: в тексте метода есть нумерованные префиксы {hits}, которых нет в "
        f"онтологии и корпусе. Модель скопирует их в ответ и переименует концы связей. "
        f"Либо убери пример, либо используй имена, реально встречающиеся в документе."
    )


def _profile(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _profile_labels(profile: dict[str, Any]) -> set[str]:
    """Имена, объявленные самим профилем: узлы канонизации, типы связей, глоссарий.

    Множество намеренно широкое. Проверка должна ловить выдуманные имена, а не спорить о том,
    какое именно место профиля считать онтологией; при сужении до одного раздела проверка
    начала бы требовать переносить пример в конкретное поле — то есть проверять не дефект,
    а авторское решение.
    """
    names: set[str] = set()

    name_keys = {"canonical_name", "name", "edge_types", "node_types"}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in name_keys and isinstance(value, str):
                    names.add(value.strip().casefold())
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            names.add(node.strip().casefold())

    walk(profile.get("canonicalization"))
    walk(profile.get("ontology"))
    walk(profile.get("glossary"))
    return {name for name in names if name}


#: Документы, по которым имена из примера **не** засчитываются за происхождение.
#:
#: Причина не в том, что эти тексты «не те». Причина в том, что они описывают сам дефект:
#: `CACHE_LIMIT` и `CACHE_EVICTION` встречаются в репозитории ровно в одном месте — в ADR-037 и
#: в разборе прогона expC, то есть в наших собственных записях о поломке. Если бы гард читал
#: их как корпус, он прошёл бы, объявив дефект починенным по единственной причине, что мы
#: записали о нём. Это тот же класс, что и прибор, смотрящий не на те поля: источник правды
#: заменён источником, который сам является следствием.
#:
#: Проверка на имена из этих файлов и не опирается: профиль `it` — про предметную область,
#: и происхождение имени определяется онтологией профиля и его глоссарием.
_NON_CORPUS = frozenset(
    {
        "05_adr_log.md",
        "history.md",
        "test_plan.md",
        "expert_reviews.md",
        "ingest_refactoring_solution.txt",
        "cypher_algorithm.txt",
        "tech_requirements.txt",
        "threat_model.txt",
        "prompt_and_entity_resolution.md",
        "multidomain_graph_ingest.md",
        "multidomain_graph_structure.md",
        "proposals_typed_ontology_variant_a.md",
    }
)


def _corpus_text() -> str:
    """Корпус домена: все документы, кроме наших же записей о дефекте."""
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(_CORPUS_DIR.rglob("*"))
        if path.is_file() and path.name not in _NON_CORPUS
    ).casefold()


@pytest.mark.parametrize("path,user", _user_texts(), ids=lambda v: getattr(v, "name", ""))
def test_relation_example_names_exist_in_ontology_or_corpus(path: Path, user: str) -> None:
    """Каждое имя из примера связи обязано откуда-то взяться.

    Именно этот гард должен быть красным до пересборки: сейчас пример в профиле `it` ссылается
    на `CACHE_LIMIT` и `CACHE_EVICTION`, которых нет ни в онтологии профиля, ни в корпусе.
    Красный тест здесь — не авария, а фиксация того, что дефект ещё не починен.
    """
    names = sorted(set(_RELATION_NAME.findall(user)))
    assert names, (
        f"{path.name}: в примере связи не нашлось ни одного имени. Проверка происхождения "
        f"без имён не знает ничего и молча проходит — это тот же класс, что молчащий ноль."
    )
    known = _profile_labels(_profile(path))
    corpus = _corpus_text()
    invented = [
        name for name in names if name.casefold() not in known and name.casefold() not in corpus
    ]
    assert not invented, (
        f"{path.name}: имена {invented} в примере связи не встречаются ни в онтологии профиля, "
        f"ни в корпусе домена. Модель скопирует их в ответ и объявит сущностями: они будут "
        f"разрешаться, и деградации не будет — а в графе появятся сущности, которых нет в "
        f"тексте (прогон expC). Либо возьми имена из корпуса, либо убери пример."
    )


@pytest.mark.parametrize("path,user", _user_texts(), ids=lambda v: getattr(v, "name", ""))
def test_relation_example_is_valid_json_shape(path: Path, user: str) -> None:
    """Пример обязан разбираться как JSON: иначе он учит модели невалидному формату.

    Дешёвая проверка, но она ловит другое, чем происхождение: опечатку в скобках, которая
    доживёт до стенда как «модель не выдала связи».
    """
    match = re.search(r'"relationships"\s*:\s*(\[[^\]]*\])', user)
    if not match:
        pytest.skip("в тексте метода нет примера массива relationships")
    payload = json.loads(match.group(1))
    assert payload, "пример relationships пуст: он ничему не учит, но стоит места в промте"
    for relation in payload:
        assert set(relation) >= {"from", "to", "kind"}, (
            f"{path.name}: в примере связи нет поля {sorted(set(relation))}; "
            f"kind обязателен, без него связь не считается извлечённой"
        )