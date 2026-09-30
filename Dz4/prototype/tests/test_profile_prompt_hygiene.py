"""Гигиена текста метода извлечения.

Модель не дообучается и копирует в ответ любой пример из инструкции дословно. Пример с
выдуманной схемой имён (`REQ-2: хранение контекста в памяти`) заставил её переименовать
концы связей: сущности объявлялись настоящими идентификаторами (`DEDUP_AUTO`), а `from/to`
писались по схеме из примера, и ни один конец не разрешался — документ падал с
`ExtractionModelError`. Такого формата нет ни в онтологии, ни в корпусе, поэтому его
появление в промте — всегда ошибка, независимо от намерения автора.

Проверка дешёвая и ловит именно ту ошибку, которую дороже всего обнаруживать на стенде.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_PROFILES_DIR = Path(__file__).resolve().parents[1] / "domain_profiles"

# Схемы нумерации, которых нет в онтологии и корпусе: REQ-1:, CON-2:, ENT-3: и т.п.
_NUMBERED_PREFIX = re.compile(r"\b[A-Z]{2,}-\d+:")


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