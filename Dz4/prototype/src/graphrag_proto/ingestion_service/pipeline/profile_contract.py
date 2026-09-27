"""Контракт профиля домена: что профиль ОБЯЗАН содержать, чтобы работать так, как объявил.

Зачем этот модуль. `config_service.domain.validate_profile` проверяет форму профиля
(секции-маппинги, `node_types` — список, структура глоссария), но не проверяет
**контракт экстракции**: `extraction.llm_enabled` и `extraction.prompt_template`. Проверять
его на стороне Config Service недостаточно по двум причинам: ingest может грузить профиль
из локального YAML в обход Config Service (`retrieval.profile.DomainProfileLoader`,
fallback-ветка читает файл без всякой проверки), а потребитель — единственный, кто знает,
какие ключи его кода читает, и только он знает, что будет при их отсутствии.

**Почему это не «ещё одна проверка», а защита от молчания.** Решение о том, пойдёт ли
документ в LLM-экстракцию, принимается в `_profile_llm_enabled` двумя шагами: сначала
явный `llm_enabled`, а если его нет — по наличию полного шаблона. Отсюда четыре исхода, и
три из них раньше были неразличимы:

| `llm_enabled` | шаблон | что происходило | что теперь |
|---|---|---|---|
| `true` | неполный | `ExtractionConfigError` → деградация с причиной | то же + ошибка валидатора |
| отсутствует | неполный | **тихо детерминированный путь, без ошибки и без флага** | **ошибка валидатора** |
| отсутствует | полный | LLM включается неявно, без следа в логах | LLM включается + предупреждение |
| `false` | любой | детерминированный путь | детерминированный путь + предупреждение, если шаблон неполный |

Вторая строка — та самая дыра, из-за которой «поле `user` есть только в одном профиле из
трёх» почти стоило вывода «на двух доменах LLM-экстракция невозможна». Вывод был неверен
не из-за грепа, а из-за того, что ветка, читающая шаблон, недостижима при явном
`llm_enabled: false`. Здесь та же ловушка закрыта явно: намерение вычисляется первым, и
неполный шаблон требуется только при намерении «включено».

**Уровни проблем.** Обязательное отсутствует — это дефект, который молча меняет поведение
документа, и он ERROR: терять данные, которые никто не считал, — худший вид поломки.
Необязательное отсутствует — не дефект (у него есть кодовый дефолт), это WARNING.
Противоречие (`llm_enabled: false` при заполненном шаблоне, или флаг не bool) — тоже
WARNING: намерение не нарушено, оно неоднозначно, и выбирать сторону должен владелец.
Ключ, который код не читает, — WARNING: профиль обещает влияние, которого нет, и это
ломает ожидания молча.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeGuard

#: Ключи, которые профиль может содержать, но ingest их НЕ читает. Проверяется на
#: текущем коде; при появлении чтения список нужно пересмотреть, иначе предупреждение
#: станет ложью, а ложное предупреждение опаснее отсутствия предупреждения.
READ_BY_INGEST: frozenset[str] = frozenset(
    {
        "profile",
        "ontology",
        "extraction",
        "chunking",
        "context_assembly",
        "retrieval",
    }
)

#: Ключи внутри `ontology`, которые читаются (в `retrievers.py` — мёртвым путём, но
#: читаются). `unique_key`, `properties`, `edge_types`, `chunk_entity_edge` не читаются:
#: `unique_key` упоминается только в `ensure_schema`, который ingest не вызывает никогда.
READ_BY_INGEST_NODE_KEYS: frozenset[str] = frozenset({"type"})

#: Ключи внутри `extraction`, которые реально читает ingest.
READ_BY_INGEST_EXTRACTION_KEYS: frozenset[str] = frozenset(
    {"llm_enabled", "prompt_template"}
)

#: Ключи внутри `extraction.prompt_template`, которые читаются.
READ_BY_INGEST_TEMPLATE_KEYS: frozenset[str] = frozenset({"system", "user", "id"})

#: Ключи внутри `chunking`, которые читаются.
READ_BY_INGEST_CHUNKING_KEYS: frozenset[str] = frozenset(
    {
        "strategy",
        "chunk_size",
        "overlap",
        "langchain_splitter",
        "llamaindex_parser",
    }
)


@dataclass(frozen=True)
class ProfileValidation:
    """Разбор профиля на нарушения контракта. ``ok`` — нет ни ошибок, ни предупреждений."""

    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors and not self.warnings

    def __bool__(self) -> bool:
        # Явное приведение к bool: dataclass без него истинна всегда, и проверка
        # `if not validation:` молча читала бы «всё хорошо» на любом профиле.
        return not self.errors and not self.warnings


def _is_mapping(value: object) -> TypeGuard[dict[str, Any]]:
    # TypeGuard, а не `bool`: после него сужение типа работает и для mypy, и для читателя.
    # Обычная функция-предикат заставила бы в каждом месте писать приведение типа, и
    # приведение — это место, где опечатка проходит незамеченной.
    return isinstance(value, dict)


def _is_nonempty_str(value: object) -> bool:
    # Непустая строка, а не просто строка: `_profile_llm_enabled` требует
    # `bool(template[key])`, и `system: ""` проходит проверку формы, но делает шаблон
    # «неполным». Валидатор обязан проверять ровно то же, иначе он разойдётся с кодом и
    # заявит, что профиль в порядке, а код пойдёт в детерминированный путь.
    return isinstance(value, str) and bool(value.strip())


def _template_gaps(template: object) -> list[str]:
    """Каких обязательных частей шаблона не хватает, поимённо."""
    if not _is_mapping(template):
        return ["extraction.prompt_template"]
    missing: list[str] = []
    if not _is_nonempty_str(template.get("system")):
        missing.append("extraction.prompt_template.system")
    if not _is_nonempty_str(template.get("user")):
        missing.append("extraction.prompt_template.user")
    return missing


def validate_ingestion_profile(profile: dict[str, Any] | None) -> ProfileValidation:
    """Проверить профиль по контракту ingest. Ничего не бросает и ничего не чинит.

    Проверка чистая: она читает профиль и возвращает список проблем. Решение (логировать,
    деградировать, падать) принимает вызывающий — иначе проверка, которой пользуются и
    Config Service, и ingest, начала бы вести себя по-разному в зависимости от того, кто
    её вызвал.
    """
    if profile is None:
        return ProfileValidation(errors=("профиль не загружен",))
    if not _is_mapping(profile):
        return ProfileValidation(errors=("профиль не является mapping",))
    if not profile:
        # Пустой профиль — «домен не настроен», а не «домен сломан». Разница принципиальная:
        # ошибка здесь означала бы, что не настроенный домен деградирует, и счётчик
        # деградации перестал бы отвечать на вопрос «что сломалось».
        return ProfileValidation()

    errors: list[str] = []
    warnings: list[str] = []

    extraction = profile.get("extraction")
    llm_on = _resolve_llm_intent(extraction, errors, warnings)
    template = extraction.get("prompt_template") if _is_mapping(extraction) else None
    gaps = _template_gaps(template)
    template_complete = not gaps

    if llm_on is True:
        # Намерение «включено» выражено явно, значит шаблон — часть контракта. Раньше эта
        # ошибка всплывала как исключение в `_extraction_template`, то есть уже на LLM-пути
        # и уже после того, как документ признан пригодным для обработки.
        for gap in gaps:
            errors.append(f"{gap} обязателен при extraction.llm_enabled: true")
    elif llm_on is None:
        # Намерение не выражено ничем: ни флагом, ни шаблоном. Раньше это приводило к
        # молчаливому детерминированному пути — документ обрабатывался, флагов не
        # ставилось, счётчики молчали. Теперь это ошибка: «выключено» и «сломано» — разные
        # утверждения о системе, и выбирать между ними должен владелец конфига.
        if "extraction" not in profile:
            # Профиль настроен частично (есть другие секции), а про экстракцию в нём не
            # сказано ничего. Это не «домен не настроен» — об этом случае выше — и не
            # «выключено намеренно»: решение не принято, оно принято по умолчанию.
            errors.append(
                "секция extraction отсутствует: не сказано, нужен ли LLM-слой, и решение "
                "будет принято по умолчанию"
            )
        else:
            for gap in gaps:
                errors.append(
                    f"{gap} отсутствует, а extraction.llm_enabled не задан: нельзя отличить "
                    "«LLM выключен намеренно» от «сломано молча»"
                )
    else:
        # llm_on is False — выключено явно. Неполный шаблон тут законен, но это мина на
        # будущее: включение флага одним символом превратит домен в деградирующий.
        if gaps and not template_complete:
            warnings.append(
                "extraction.llm_enabled: false, шаблон неполон ("
                + ", ".join(gaps)
                + ") — при включении LLM экстракция этого домена будет деградировать"
            )

    warnings.extend(_inert_extraction_keys(extraction))
    warnings.extend(_inert_ontology_keys(profile))
    warnings.extend(_inert_chunking_keys(profile))
    return ProfileValidation(errors=tuple(errors), warnings=tuple(warnings))


def _resolve_llm_intent(
    extraction: object,
    errors: list[str],
    warnings: list[str],
) -> bool | None:
    """Что профиль говорит о включении LLM: True / False / «не сказано».

    Три состояния, а не два, — потому что «не сказано» и «нет» ведут к разным выводам.
    `None` означает, что решение примет `_profile_llm_enabled` по наличию шаблона, и это
    решение не оставляет следа.
    """
    if extraction is None:
        return None
    if not _is_mapping(extraction):
        errors.append("extraction должен быть mapping")
        return None
    if "llm_enabled" not in extraction:
        return None
    flag = extraction["llm_enabled"]
    if not isinstance(flag, bool):
        # `isinstance(flag, bool)` в коде отвергнет «true» строкой и молча передаст решение
        # шаблону. Флаг, который игнорируется, хуже отсутствующего: он выглядит как
        # выключатель, а не работает как выключатель.
        errors.append(
            f"extraction.llm_enabled должен быть bool, получено {type(flag).__name__} — "
            "флаг будет проигнорирован, решение примет шаблон"
        )
        return None
    return flag


def _inert_extraction_keys(extraction: object) -> list[str]:
    if not _is_mapping(extraction):
        return []
    mapping: dict[str, Any] = extraction
    out = [
        f"extraction.{key} не читается ingest — значение в профиле не влияет ни на что"
        for key in sorted(set(mapping) - READ_BY_INGEST_EXTRACTION_KEYS)
    ]
    template = mapping.get("prompt_template")
    if not _is_mapping(template):
        return out
    template_map: dict[str, Any] = template
    return out + [
        f"extraction.prompt_template.{key} не читается ingest"
        for key in sorted(set(template_map) - READ_BY_INGEST_TEMPLATE_KEYS)
    ]


def _inert_ontology_keys(profile: dict[str, Any]) -> list[str]:
    ontology = profile.get("ontology")
    if not _is_mapping(ontology):
        return []
    ontology_map: dict[str, Any] = ontology
    out = [
        f"ontology.{key} не читается ingest" for key in sorted(set(ontology_map) - {"node_types"})
    ]
    node_types = ontology_map.get("node_types")
    if not isinstance(node_types, list):
        return out
    for index, node_type in enumerate(node_types):
        if not _is_mapping(node_type):
            out.append(
                f"ontology.node_types[{index}] не является mapping — элемент молча выброшен"
            )
            continue
        node_map: dict[str, Any] = node_type
        for key in sorted(set(node_map) - READ_BY_INGEST_NODE_KEYS):
            out.append(
                f"ontology.node_types[{index}].{key} не читается ingest "
                "(unique_key применяется только в ensure_schema, который ingest не вызывает)"
            )
    return out


def _inert_chunking_keys(profile: dict[str, Any]) -> list[str]:
    chunking = profile.get("chunking")
    if not _is_mapping(chunking):
        return []
    chunking_map: dict[str, Any] = chunking
    return [
        f"chunking.{key} не читается ingest"
        for key in sorted(set(chunking_map) - READ_BY_INGEST_CHUNKING_KEYS)
    ]


class ProfileProblemLog:
    """Пишет список проблем профиля в лог один раз на пару «домен + набор проблем».

    Профиль грузится раз на джобу (`ctx.profile_loaded`), а джоб на корпусе — сотни. Без
    дедупликации один сломанный конфиг даёт сотни одинаковых строк, и в логе он тонет
    ровно настолько же, насколько был важен. Счётчик по документам при этом остаётся: он
    в `enrichment_causes` и в отчёте, то есть источником данных служит не лог.
    """

    def __init__(self) -> None:
        self._seen: set[tuple[str, str, tuple[str, ...]]] = set()

    def due(self, domain: str, level: str, problems: tuple[str, ...]) -> bool:
        """True, если эту комбинацию ещё не писали. Заодно помечает как записанную."""
        key = (domain, level, problems)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


#: Экземпляр на процесс. Общий намеренно: дедупликация нужна между джобами, а не внутри
#: одной, и передавать логгер в контекст ради этого — лишняя связность.
PROFILE_PROBLEM_LOG = ProfileProblemLog()
