import json
from pathlib import Path

import pytest

from ai_testgen.normalizer import (
    SETTINGS_ENV,
    NormalizationError,
    NormalizerSettings,
    NormalizerSettingsError,
    decode_bytes,
    find_url,
    load_settings,
    normalize_bytes,
    normalize_text,
)

CATALOG = """# Бизнес-требования: каталог курсов

**URL:** https://otus.ru/catalog/courses?categories=programming

Каталог показывает курсы, фильтры и пагинацию.

| ID | Описание |
|----|----------|
| BR-1 | Список курсов открывается |
| BR-2 | Фильтр по категориям работает |
"""

LOGIN = """# Требования: вход в личный кабинет

Сайт: https://shop.example.com/login

## BR-1 Вход по email и паролю

Форма принимает email и пароль.

### Детали

Пароль скрыт.

## BR-2 Неверный пароль

Показывается ошибка.
"""

LIST = """# Требования к корзине

https://shop.example.com/cart

## Требования

- вход в корзину
- BR-7 сброс корзины
- выход из корзины
"""


def test_decode_plain_utf8() -> None:
    text, encoding = decode_bytes("Требования".encode())

    assert text == "Требования"
    assert encoding == "utf-8"


def test_decode_utf8_bom() -> None:
    text, encoding = decode_bytes(b"\xef\xbb\xbf" + "Сайт".encode())

    assert text == "Сайт"
    assert encoding == "utf-8"


def test_decode_cp1251() -> None:
    text, encoding = decode_bytes(
        "# Документация\nСайт: https://example.com\n".encode("cp1251")
    )

    assert encoding == "cp1251"
    assert "Документация" in text


def test_decode_cp866() -> None:
    raw = "# Normalizer - документация\r\n".encode("cp866")

    text, encoding = decode_bytes(raw)

    assert encoding == "cp866"
    assert text == "# Normalizer - документация\r\n"


def test_decode_utf16() -> None:
    text, encoding = decode_bytes("# Тест\nhttps://example.com\n".encode("utf-16"))

    assert encoding == "utf-16"
    assert text.startswith("# Тест")


def test_decode_ascii_is_utf8() -> None:
    assert decode_bytes(b"# Requirements\n") == ("# Requirements\n", "utf-8")


def test_find_url_strips_trailing_punctuation() -> None:
    assert find_url("Сайт: https://example.com/path, дальше текст.") == (
        "https://example.com/path"
    )
    assert find_url("без ссылки") is None


def test_table_requirements_become_canonical() -> None:
    result = normalize_text(CATALOG)

    assert result.strategy == "table"
    assert [item.id for item in result.requirements] == ["BR-1", "BR-2"]
    assert result.requirements[0].text == "Список курсов открывается"
    assert result.url == "https://otus.ru/catalog/courses?categories=programming"
    assert "# Бизнес-требования: каталог курсов" in result.text
    assert result.text.count("**URL:**") == 1
    assert "## Requirements" in result.text
    assert "### BR-1" in result.text


def test_br_headings_keep_nested_sections() -> None:
    result = normalize_text(LOGIN)

    assert result.strategy == "heading"
    assert [item.id for item in result.requirements] == ["BR-1", "BR-2"]
    first = result.requirements[0].text
    assert "Вход по email и паролю" in first
    assert "**Детали**" in first
    assert "Пароль скрыт." in first


def test_list_requirements_keep_explicit_ids() -> None:
    result = normalize_text(LIST)

    assert result.strategy == "list"
    assert [item.id for item in result.requirements] == ["BR-1", "BR-7", "BR-2"]
    assert result.requirements[1].text == "сброс корзины"


def test_inline_br_lines_are_recognized() -> None:
    result = normalize_text(
        "Требования\n\nBR-3: первое требование\nпродолжение\n\nBR-4: второе требование\n"
    )

    assert result.strategy == "inline"
    assert [item.id for item in result.requirements] == ["BR-3", "BR-4"]
    assert "продолжение" in result.requirements[0].text


def test_paragraph_fallback_warns() -> None:
    result = normalize_text(
        "Корзина\n\nhttps://shop.example.com/cart\n\n"
        "Пользователь добавляет товар в корзину и удаляет его.\n"
    )

    assert result.strategy == "paragraph"
    assert [item.id for item in result.requirements] == ["BR-1"]
    assert any("структура не распознана" in warning for warning in result.warnings)


def test_duplicate_ids_are_dropped_with_warning() -> None:
    result = normalize_text(
        "# Требования\n\n## BR-1 Первое\nтекст\n\n## BR-1 Дубль\nдругой\n"
    )

    assert [item.id for item in result.requirements] == ["BR-1"]
    assert any("дубликаты" in warning for warning in result.warnings)


def test_requirement_with_empty_text_is_dropped() -> None:
    result = normalize_text("# Требования\n\n## BR-1\n\n## BR-2\nтекст\n")

    assert [item.id for item in result.requirements] == ["BR-2"]
    assert any("пустым текстом" in warning for warning in result.warnings)


def test_missing_url_adds_warning_and_no_url_line() -> None:
    result = normalize_text("# Требования\n\n## BR-1\nвход по email\n")

    assert result.url is None
    assert "**URL:**" not in result.text
    assert any("URL не найден" in warning for warning in result.warnings)


def test_url_override_wins() -> None:
    result = normalize_text(CATALOG, "https://override.example.com")

    assert result.url == "https://override.example.com"
    assert "https://override.example.com" in result.text
    assert "https://otus.ru" not in result.text


def test_normalization_is_idempotent() -> None:
    for source in (CATALOG, LOGIN, LIST):
        once = normalize_text(source)
        twice = normalize_text(once.text, once.url)

        assert twice.text == once.text
        assert [(item.id, item.text) for item in twice.requirements] == [
            (item.id, item.text) for item in once.requirements
        ]


def test_normalization_is_deterministic() -> None:
    assert normalize_text(CATALOG) == normalize_text(CATALOG)
    assert normalize_bytes(CATALOG.encode("utf-8")) == normalize_text(CATALOG)


def test_change_flag_reports_canonical_input() -> None:
    canonical = normalize_text(CATALOG).text
    again = normalize_text(canonical)

    assert again.changed is False


def test_empty_document_raises() -> None:
    with pytest.raises(NormalizationError):
        normalize_text("   \n\n  ")


def test_title_falls_back_to_first_line() -> None:
    result = normalize_text(
        "Требования к корзине.\n\nhttps://shop.example.com/cart\n\n"
        "Пользователь добавляет товар и удаляет его.\n"
    )

    assert result.title == "Требования к корзине."
    assert result.text.startswith("# Требования к корзине.")
    assert "### BR-1" in result.text


NO_IDS = """# Требования к корзине

https://shop.example.com/cart

## Требования

- вход в корзину
- выход из корзины
"""


def _write_settings(tmp_path: Path, **overrides: object) -> Path:
    values: dict[str, object] = {
        "id_prefix": "BR",
        "requirements_heading": "Requirements",
        "url_label": "URL",
        "fallback_title": "Требования",
        "encodings": ["cp1251", "cp866"],
        "paragraph_min_length": 20,
    }
    values.update(overrides)
    path = tmp_path / "normalizer.toml"
    path.write_text(
        "\n".join(
            f"{key} = {json.dumps(value, ensure_ascii=False)}"
            for key, value in values.items()
        ),
        encoding="utf-8",
    )
    return path


def test_default_settings_reproduce_the_previous_hardcode() -> None:
    assert normalize_text(CATALOG) == normalize_text(CATALOG, settings=NormalizerSettings())


def test_settings_file_changes_prefix_labels_and_headings(tmp_path: Path) -> None:
    settings = load_settings(
        _write_settings(
            tmp_path,
            id_prefix="REQ",
            requirements_heading="Требования системы",
            url_label="Сайт",
            fallback_title="Без названия",
        )
    )

    result = normalize_text(NO_IDS, settings=settings)

    assert settings.id_prefix == "REQ"
    assert "### REQ-1" in result.text
    assert "### REQ-2" in result.text
    assert "## Требования системы" in result.text
    assert "**Сайт:** https://shop.example.com/cart" in result.text
    assert result.requirements[0].id == "REQ-1"


def test_existing_br_ids_are_kept_after_the_prefix_changes(tmp_path: Path) -> None:
    """Existing ids win over the configured prefix: no silent renumbering."""

    settings = load_settings(_write_settings(tmp_path, id_prefix="REQ"))

    result = normalize_text(CATALOG, settings=settings)

    assert [item.id for item in result.requirements] == ["BR-1", "BR-2"]


def test_custom_settings_stay_idempotent(tmp_path: Path) -> None:
    settings = load_settings(_write_settings(tmp_path, id_prefix="REQ"))

    once = normalize_text(LOGIN, settings=settings)
    twice = normalize_text(once.text, once.url, settings=settings)

    assert twice.text == once.text
    assert twice.changed is False


def test_paragraph_min_length_comes_from_settings(tmp_path: Path) -> None:
    text = "# Заметки\n\nКоротко\n\nТоже мало\n"
    strict = load_settings(_write_settings(tmp_path, paragraph_min_length=20))
    loose = load_settings(_write_settings(tmp_path, paragraph_min_length=5))

    with pytest.raises(NormalizationError):
        normalize_text(text, settings=strict)
    assert len(normalize_text(text, settings=loose).requirements) == 2


def test_decode_bytes_uses_configured_codecs(tmp_path: Path) -> None:
    raw = "# Привет\n".encode("cp866")
    settings = load_settings(_write_settings(tmp_path, encodings=["cp866"]))

    text, encoding = decode_bytes(raw, settings)

    assert encoding == "cp866"
    assert text == "# Привет\n"


def test_load_settings_returns_defaults_without_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(SETTINGS_ENV, raising=False)
    monkeypatch.chdir(tmp_path)

    assert load_settings(None) == NormalizerSettings()


def test_env_var_points_to_the_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_settings(tmp_path, id_prefix="ENV")
    monkeypatch.setenv(SETTINGS_ENV, str(path))

    assert load_settings(None).id_prefix == "ENV"


def test_load_settings_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(NormalizerSettingsError, match="не найден"):
        load_settings(tmp_path / "absent.toml")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id_prefix": "BR-1"}, "id_prefix"),
        ({"id_prefix": ""}, "id_prefix"),
        ({"requirements_heading": "  "}, "requirements_heading"),
        ({"encodings": []}, "encodings"),
        ({"encodings": ["no-such-codec"]}, "no-such-codec"),
        ({"encodings": "cp1251"}, "encodings"),
        ({"paragraph_min_length": 0}, "paragraph_min_length"),
        ({"paragraph_min_length": "20"}, "paragraph_min_length"),
    ],
)
def test_load_settings_rejects_invalid_values(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(NormalizerSettingsError, match=message):
        load_settings(_write_settings(tmp_path, **overrides))


def test_load_settings_warns_about_unknown_keys(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write_settings(tmp_path, mystery=1)

    load_settings(path)

    assert "mystery" in capsys.readouterr().out
