"""Контракт профиля домена: обязательное отсутствует = ошибка, необязательное = предупреждение.

Проверяется чистая функция, без пайплайна. Причина в том, что ошибки валидатора должны
быть видны ДО того, как документ признан пригодным: в пайплайне негодный профиль с
`llm_enabled: true` всплывает как `ExtractionConfigError` уже на LLM-пути, а с протухшим
шаблоном и без флага — не всплывает вообще, и документ уходит в детерминированный
fallback без единой записи об этом.
"""

from __future__ import annotations

import copy
from typing import Any

from graphrag_proto.ingestion_service.pipeline.profile_contract import (
    ProfileProblemLog,
    validate_ingestion_profile,
)

BASE: dict[str, Any] = {
    "profile": {"name": "it"},
    "ontology": {"node_types": [{"type": "Concept", "unique_key": "canonical_name"}]},
    "extraction": {
        "llm_enabled": True,
        "prompt_template": {"id": "extract_v1", "system": "s", "user": "u"},
    },
    "chunking": {"strategy": "sliding_window", "chunk_size": 512, "overlap": 64},
}


def _profile(**overrides: Any) -> dict[str, Any]:
    data = copy.deepcopy(BASE)
    for dotted, value in overrides.items():
        section, _, key = dotted.partition("__")
        if key:
            data.setdefault(section, {})
            if value is None:
                data[section].pop(key, None)
            else:
                data[section][key] = value
    return data


def test_valid_profile_has_no_problems() -> None:
    verdict = validate_ingestion_profile(copy.deepcopy(BASE))

    assert verdict.errors == ()
    # `unique_key` в профиле есть, но ingest его не читает — это предупреждение, и
    # профиль без него не «идеальный», а просто не сломанный.
    assert any("unique_key" in w for w in verdict.warnings)
    assert not verdict.ok
    assert not bool(verdict) is True


def test_llm_enabled_true_with_missing_system_is_error() -> None:
    """`llm_enabled: true` + шаблон без `system` = неполный шаблон, понятная ошибка.

    Проверяется именно `system`, а не `user`: с 2026-09-29 `user` необязателен, потому
    что метод извлечения живёт в генераторе инструкции и одинаков для всех доменов.
    Прежняя проверка требовала непустой `user` и тем самым обязывала каждый профиль
    дублировать метод - то есть возвращала то дублирование, ради устранения которого
    генератор и вводился.
    """
    profile = copy.deepcopy(BASE)
    del profile["extraction"]["prompt_template"]["system"]

    verdict = validate_ingestion_profile(profile)

    assert len(verdict.errors) == 1
    assert "extraction.prompt_template.system" in verdict.errors[0]
    assert "llm_enabled: true" in verdict.errors[0]


def test_absent_user_is_legal() -> None:
    """Отсутствующий `user` - не ошибка: метод даёт генератор инструкции."""
    profile = copy.deepcopy(BASE)
    del profile["extraction"]["prompt_template"]["user"]

    verdict = validate_ingestion_profile(profile)

    assert not any("prompt_template.user" in e for e in verdict.errors)


def test_user_present_but_not_a_string_is_error() -> None:
    """Заданный, но нестроковый `user` - ошибка: молча выбросить его нельзя."""
    profile = copy.deepcopy(BASE)
    profile["extraction"]["prompt_template"]["user"] = ["не", "строка"]

    verdict = validate_ingestion_profile(profile)

    assert any("prompt_template.user" in e for e in verdict.errors)



def test_empty_template_field_is_error_not_pass() -> None:
    """Пустая строка — тоже отсутствие, и это ровно тот случай, где валидатор разошёлся бы
    с кодом: `_profile_llm_enabled` требует непустой строки, а `_extraction_template`
    проверяет только тип. Валидатор обязан проверять непустоту."""
    profile = copy.deepcopy(BASE)
    profile["extraction"]["prompt_template"]["system"] = "   "

    verdict = validate_ingestion_profile(profile)

    assert any("prompt_template.system" in e for e in verdict.errors)


def test_absent_flag_and_incomplete_template_is_error() -> None:
    """Дыра, ради которой модуль написан: молчаливый детерминированный путь.

    Раньше эта комбинация не оставляла следов: флага нет → решение принимает шаблон →
    шаблон неполон → `False` → тихий откат, без ошибки, без флага, без счётчика. Документ
    выглядит обработанным, LLM не звали, и заметить нечем.
    """
    profile = copy.deepcopy(BASE)
    profile["extraction"].pop("llm_enabled")
    del profile["extraction"]["prompt_template"]["system"]

    verdict = validate_ingestion_profile(profile)

    assert len(verdict.errors) == 1
    assert "нельзя отличить" in verdict.errors[0]


def test_absent_flag_with_complete_template_is_warning_not_error() -> None:
    """Шаблон полон, флага нет — LLM включается, это не поломка.

    Но решение принято неявно, по косвенному признаку, и этого не видно ни в логах, ни в
    отчёте: домен можно выключить, удалив одно поле шаблона, и никто об этом не узнает.
    """
    profile = copy.deepcopy(BASE)
    profile["extraction"].pop("llm_enabled")

    verdict = validate_ingestion_profile(profile)

    assert verdict.errors == ()


def test_explicit_false_with_incomplete_template_is_only_warning() -> None:
    """`llm_enabled: false` при неполном шаблоне — законно, это выключенный домен.

    Именно на этом ложном выводе стояла ошибка «на двух доменах из трёх экстракция
    невозможна»: ветка, читающая шаблон, при явном `false` недостижима. Ошибкой это быть не
    может — иначе нельзя было бы хранить выключенный домен с заготовленным шаблоном.
    """
    profile = copy.deepcopy(BASE)
    profile["extraction"]["llm_enabled"] = False
    del profile["extraction"]["prompt_template"]["system"]

    verdict = validate_ingestion_profile(profile)

    assert verdict.errors == ()
    assert any("llm_enabled: false" in w and "system" in w for w in verdict.warnings)


def test_flag_of_wrong_type_is_error() -> None:
    """`llm_enabled: "true"` строкой — флаг, который не работает как флаг.

    `isinstance(value, bool)` в коде отвергнет строку и молча передаст решение шаблону.
    Молчание здесь опаснее отсутствия ключа: автор считает, что выключил домен, а домен
    включён.
    """
    profile = copy.deepcopy(BASE)
    profile["extraction"]["llm_enabled"] = "true"

    verdict = validate_ingestion_profile(profile)

    assert any("должен быть bool" in e for e in verdict.errors)


def test_optional_missing_keys_are_warnings() -> None:
    """Отсутствие необязательного — не дефект: у него есть кодовый дефолт.

    `temperature: 0.1` в профиле не читается ingest, и это надо сказать (значение обещает
    влияние, которого нет), но ошибкой — нельзя: иначе нельзя было бы держать в профиле
    ключи для retrieval-пути, который их читает.
    """
    profile = copy.deepcopy(BASE)
    profile["extraction"]["temperature"] = 0.1
    profile["extraction"]["max_tokens"] = 4096
    profile["extraction"]["prompt_template"].pop("id")
    profile["chunking"]["chunk_size_by_type"] = {"md": 1024}

    verdict = validate_ingestion_profile(profile)

    assert verdict.errors == ()
    warnings = "\n".join(verdict.warnings)
    assert "extraction.temperature не читается" in warnings
    assert "extraction.max_tokens не читается" in warnings
    assert "chunking.chunk_size_by_type не читается" in warnings
    # Необязательный `id` молча берёт дефолт — это не проблема, а документированное
    # поведение, и выдавать его предупреждением значило бы приучить игнорировать список.
    assert "prompt_template.id" not in warnings


def test_partially_configured_profile_without_extraction_is_error() -> None:
    """Профиль настроен, а про экстракцию в нём не сказано ничего.

    Не то же самое, что пустой профиль: другие секции настроены, значит домен в работе, а
    решение про LLM-слой принято по умолчанию и нигде не зафиксировано.
    """
    profile = copy.deepcopy(BASE)
    profile.pop("extraction")

    verdict = validate_ingestion_profile(profile)

    assert any("секция extraction отсутствует" in e for e in verdict.errors)


def test_node_type_without_type_is_reported() -> None:
    """Элемент `node_types` без `type` молча выбрасывается фильтром — это надо назвать."""
    profile = copy.deepcopy(BASE)
    profile["ontology"]["node_types"].append({"label": "без типа"})

    verdict = validate_ingestion_profile(profile)

    assert any("node_types[1].label" in w for w in verdict.warnings)


def test_empty_profile_is_not_a_defect() -> None:
    """Пустой профиль — законное состояние (домен не настроен), а не поломка.

    Ошибка на пустом профиле означала бы, что не настроенный домен «деградирует», и
    счётчик деградации перестал бы отвечать на вопрос «что сломалось».
    """
    verdict = validate_ingestion_profile({})

    assert verdict.errors == ()
    assert verdict.warnings == ()
    assert verdict.ok


def test_non_mapping_profile_is_an_error() -> None:
    verdict = validate_ingestion_profile(None)  # type: ignore[arg-type]

    assert verdict.errors


def test_problem_log_writes_each_combination_once() -> None:
    """Один сломанный конфиг на корпусе в 200 документов = 200 одинаковых строк лога.

    Дедупликация обязана быть между джобами, а не внутри одной: иначе ошибка тонет ровно
    настолько же, насколько была важна. Счётчик при этом остаётся — он в отчёте.
    """
    log = ProfileProblemLog()

    assert log.due("it", "error", ("a",)) is True
    assert log.due("it", "error", ("a",)) is False
    assert log.due("it", "error", ("b",)) is True
    assert log.due("cinema", "error", ("a",)) is True
    assert log.due("it", "warning", ("a",)) is True
