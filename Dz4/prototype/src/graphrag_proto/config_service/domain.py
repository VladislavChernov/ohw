from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

MANDATORY_SECTIONS = ("profile",)
OPTIONAL_MAPPING_SECTIONS = (
    "ontology",
    "extraction",
    "validation",
    "canonicalization",
    "chunking",
    "context_assembly",
    "glossary",
)


class DomainProfileError(ValueError):
    """Ошибка формата/содержимого Domain Profile."""


def load_profile_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise DomainProfileError(f"{path.name}: профиль должен быть YAML-маппингом")
    data = _empty_optional_sections_to_mappings(data)
    errors = validate_profile(data)
    if errors:
        raise DomainProfileError(f"{path.name}: {'; '.join(errors)}")
    return data


def _empty_optional_sections_to_mappings(data: dict[str, Any]) -> dict[str, Any]:
    """Пустая секция YAML — это `None`, а «должен быть маппинг» на неё ругается.

    Ключ с одним только комментарием под ним разбирается как `None`, и опциональная секция,
    объявленная пустой, отклонялась целиком: профиль не грузился, а вместе с ним и
    query-контур, которому профиль домена нужен для каждого запроса. Пустая секция — это
    место под будущие правила, а не ошибка формата, поэтому она нормализуется в `{}`.

    Нормализация живёт здесь, а не в `validate_profile`: такая функция честно отвечает на
    вопрос «валиден ли этот профиль» и не имеет права молча переписывать проверяемое.
    Обязательные секции (`profile`) не нормализуются — пустая обязательная секция остаётся
    ошибкой, потому что профиль без неё не профиль.
    """
    return {
        key: ({} if value is None and key in OPTIONAL_MAPPING_SECTIONS else value)
        for key, value in data.items()
    }


def validate_profile(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for section in MANDATORY_SECTIONS:
        if section not in data:
            errors.append(f"отсутствует секция '{section}'")
        elif not isinstance(data[section], dict):
            # Проверки на тип не было: обязательная секция, объявленная с пустым телом,
            # проходила как есть, хотя профиль без содержимого `profile` не профиль.
            errors.append(f"{section}: должен быть маппинг")
    for section in OPTIONAL_MAPPING_SECTIONS:
        if section in data and not isinstance(data[section], dict):
            errors.append(f"{section}: должен быть маппинг")
    ontology = data.get("ontology")
    if isinstance(ontology, dict):
        for key in ("node_types", "edge_types"):
            if key in ontology and not isinstance(ontology[key], list):
                errors.append(f"ontology.{key}: должен быть список")
    glossary = data.get("glossary")
    if glossary is not None:
        errors.extend(_validate_glossary(glossary))
    return errors


def _validate_glossary(glossary: Any) -> list[str]:
    """Расширенная glossary-валидация (M1): уникальность canonical_name и алиасов.

    Правила (docs/04 §4, M1-запрос из M0):
    - секции terms/data_types/complexity_aliases — список словарей {canonical_name, aliases};
    - один тег (алиас) не ведёт к двум каноническим терминам.
    """
    errors: list[str] = []
    if not isinstance(glossary, dict):
        return ["glossary: должен быть маппинг"]
    alias_owner: dict[str, str] = {}
    for section in ("terms", "data_types", "complexity_aliases"):
        entries = glossary.get(section)
        if entries is None:
            continue
        if not isinstance(entries, list):
            errors.append(f"glossary.{section}: должен быть список")
            continue
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                errors.append(f"glossary.{section}[{i}]: должен быть словарь")
                continue
            name = entry.get("canonical_name")
            if not isinstance(name, str) or not name:
                errors.append(f"glossary.{section}[{i}]: поле canonical_name обязано быть непустой строкой")
                continue
            aliases = entry.get("aliases")
            if aliases is not None and not isinstance(aliases, list):
                errors.append(f"glossary.{section}[{i}].aliases: должен быть список")
                continue
            for alias in aliases or []:
                if not isinstance(alias, str) or not alias:
                    continue
                key = alias.casefold()
                prev = alias_owner.get(key)
                if prev is not None and prev != name:
                    errors.append(
                        f"glossary.{section}[{i}]: тег '{alias}' уже ведёт к "
                        f"canonical_name '{prev}' (не может быть двух канонизаций)"
                    )
                else:
                    alias_owner[key] = name
    return errors


def domain_from_profile(profile: dict[str, Any], default: str) -> str:
    name = profile.get("profile", {}).get("name")
    return name if isinstance(name, str) and name else default


def domain_from_filename(path: Path) -> str | None:
    stem = path.stem
    if stem.startswith("domain_profile."):
        return stem.removeprefix("domain_profile.")
    return None