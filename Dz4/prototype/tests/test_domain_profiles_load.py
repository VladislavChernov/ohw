"""Настоящие профили доменов обязаны грузиться тем же загрузчиком, что и стенд.

Почему этот тест существует. Уборка мёртвых ключей профиля (`61050ae`, бандл
`tidy-domain-profile-keys`) удалила содержимое секций `validation` и `canonicalization`, но
оставила сами ключи — с одним только комментарием под ними. YAML разбирает такой ключ как
`None`, а не как `{}`, и опциональная секция «должна быть маппингом» получала отказ. Профиль
переставал грузиться целиком: Config Service отвечал 422, `DomainProfileLoader` бросал
`ProfileError`, и **ни один запрос ни в одном домене не отрабатывал**.

При этом гейт был зелёным, потому что ни один тест не прогонял настоящие профили через
`load_profile_yaml`: все тесты загрузчика строили синтетический YAML в `tmp_path`. Тест на
мёртвые ключи читает профили как текст и ловит ключи, а не структуру. Теперь структура
проверяется настоящим загрузчиком, и уборка обязана заканчиваться этим прогоном.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphrag_proto.config_service.domain import (
    MANDATORY_SECTIONS,
    OPTIONAL_MAPPING_SECTIONS,
    DomainProfileError,
    load_profile_yaml,
    validate_profile,
)

PROFILES_DIR = Path(__file__).resolve().parents[1] / "domain_profiles"
DOMAIN_PROFILES = sorted(PROFILES_DIR.glob("domain_profile.*.yaml"))


def test_there_are_domain_profiles_to_check() -> None:
    """Если glob перестанет что-то находить, тест ниже станет зелёным вхолостую."""
    assert len(DOMAIN_PROFILES) == 3, [p.name for p in DOMAIN_PROFILES]


@pytest.mark.parametrize("path", DOMAIN_PROFILES, ids=lambda p: p.name)
def test_shipped_domain_profile_loads(path: Path) -> None:
    profile = load_profile_yaml(path)
    assert validate_profile(profile) == []
    for section in MANDATORY_SECTIONS:
        assert section in profile, f"{path.name}: нет обязательной секции {section!r}"
    for section in OPTIONAL_MAPPING_SECTIONS:
        if section in profile:
            assert isinstance(profile[section], dict), (
                f"{path.name}: секция {section!r} объявлена, но не маппинг"
            )


@pytest.mark.parametrize("path", DOMAIN_PROFILES, ids=lambda p: p.name)
def test_shipped_profile_has_no_empty_section_keys(path: Path) -> None:
    """Пустая секция в YAML — ловушка, а не украшение: загрузчик её нормализует.

    Отдельная проверка нужна, чтобы правка профиля не прошла молча: `_empty_optional_
    sections_to_mappings` превращает `None` в `{}`, и без этого теста пустой ключ был бы
    неотличим от настоящей пустой секции. Именно такая неотличимость стоила сломанного
    query-контура.
    """
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    empties = sorted(key for key, value in raw.items() if value is None)
    assert empties == [], (
        f"{path.name}: секции без тела {empties} — YAML даёт None, а не маппинг. "
        "Либо заполнить секцию, либо убрать ключ."
    )


def test_empty_optional_section_loads_as_empty_mapping(tmp_path: Path) -> None:
    """Пустая опциональная секция — место под будущее, а не отказ."""
    path = tmp_path / "domain_profile.test.yaml"
    path.write_text(
        "profile:\n  domain: test\nvalidation:\ncanonicalization:\n", encoding="utf-8"
    )
    profile = load_profile_yaml(path)
    assert profile["validation"] == {}
    assert profile["canonicalization"] == {}
    assert validate_profile(profile) == []


def test_empty_mandatory_section_is_still_rejected(tmp_path: Path) -> None:
    """Обязательная секция без тела — ошибка: профиль без `profile` не профиль."""
    path = tmp_path / "domain_profile.test.yaml"
    path.write_text("profile:\n", encoding="utf-8")
    with pytest.raises(DomainProfileError) as excinfo:
        load_profile_yaml(path)
    assert "profile" in str(excinfo.value)


def test_validate_profile_rejects_none_for_optional_section() -> None:
    """Нормализация живёт в загрузчике, а не здесь: чистая проверка None отвергает.

    Если бы `validate_profile` принимала пустую секцию, она перестала бы отвечать на вопрос
    «валиден ли профиль» и проверяла бы уже нормализованный вход.
    """
    data = {"profile": {"domain": "it"}, "canonicalization": None}
    assert validate_profile(data) == ["canonicalization: должен быть маппинг"]