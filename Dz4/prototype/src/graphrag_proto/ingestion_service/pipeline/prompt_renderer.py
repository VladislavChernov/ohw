"""Сборка инструкции извлечения из профиля домена.

Проблема, ради которой модуль существует. Профиль объявляет схему структурно:
`ontology.node_types` (тип, ключ, свойства) и `ontology.edge_types` (откуда, куда, вид).
Та же схема была продублирована прозой в `extraction.prompt_template.user` - перечислением
типов, полей и разрешённых видов связей. Дублирование опасно не само по себе, а тем, что
оно **не проверяется ничем**: реальные `domain_profiles/*.yaml` не читает ни один тест, и
вид, добавленный в `edge_types` и забытый в прозе, оказывался объявленным и никогда не
извлекаемым, а выглядело это как «модель не выдаёт связи».

Поэтому схема в промпте не пишется руками: она генерируется отсюда, а прозе остаётся
только **метод** - как думать и как отвечать. Разделение принципиальное: метод профилю
не принадлежит, он одинаков для всех доменов, и держать его в YAML профиля - значит
плодить копии, которые разъедутся.

Идентичность извлечения считается от **содержимо��** собранной инструкции, а не от
ручной метки `prompt_template.id`. Метка остаётся как человекочитаемая подпись в отчёте,
но идентичностью не является: правка прозы при неизменном `id` иначе осталась бы
незаметной, и по графу нельзя было бы понять, чем узел извлечён.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

#: Куда писать пояснение о типе. Поле добавлено в `ontology.node_types`; раньше эти
#: пояснения жили прозой в промпте, и это был второй источник правды.
GLOSS_KEY = "gloss"


def _section(profile: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    """Раздел профиля как отображение; отсутствующий или не-mapping - пустой.

    Профиль приходит из YAML, где любой раздел может отсутствовать или оказаться
    списком, поэтому проверка типа тут не формальность: без неё обращение к `.get`
    падает на входе, то есть ошибка в профиле выглядела бы как падение в коде.
    """
    value = profile.get(name)
    return value if isinstance(value, Mapping) else {}


def _declared_list(section: Mapping[str, Any], name: str) -> list[Any]:
    """Список внутри раздела профиля; отсутствующий или не-список - пустой список."""
    value = section.get(name)
    return value if isinstance(value, list) else []


def _node_types(profile: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items = _declared_list(_section(profile, "ontology"), "node_types")
    return [item for item in items if isinstance(item, Mapping) and item.get("type")]


def _edge_types(profile: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items = _declared_list(_section(profile, "ontology"), "edge_types")
    return [item for item in items if isinstance(item, Mapping) and item.get("type")]


def plural_entity_key(node_type: str) -> str:
    """JSON-ключ массива сущностей для типа.

    Дублирует `orchestrator._plural_entity_key` намеренно не по значению, а по
    контракту: ключ должен совпасть с тем, что пайплайн примет в ответе модели, иначе
    разъезд будет молчаливым - модель вернёт верный JSON, а код его отбросит. Единый
    источник для этого ключа - профиль, а обе стороны обязаны следовать одному правилу,
    поэтому правило вынесено сюда и проверяется тестом.
    """
    lowered = node_type.strip().lower()
    if not lowered:
        return ""
    if lowered.endswith("y"):
        return f"{lowered[:-1]}ies"
    if lowered.endswith(("s", "x", "z", "ch", "sh")):
        return f"{lowered}es"
    return f"{lowered}s"


def render_schema_block(profile: Mapping[str, Any]) -> str:
    """Схемная часть инструкции: домен, типы узлов, виды связей, скелет ответа."""
    meta = _section(profile, "profile")
    name = str(meta.get("name") or "").strip() or "домен"
    description = str(meta.get("description") or "").strip()

    lines = [f"Домен: {name}."]
    if description:
        lines.append(f"Описание домена: {description}.")
    lines.append("")

    node_types = _node_types(profile)
    if node_types:
        lines.append("Извлекай сущности только этих типов:")
        for node_type in node_types:
            type_name = str(node_type.get("type"))
            gloss = str(node_type.get(GLOSS_KEY) or "").strip()
            # Выдаёт модель, а не `properties`: половина properties - служебные поля,
            # которые проставляет система (`chunk_ids`, `extractor_version` и прочие).
            # Просить их у модели значит просить выдумать то, о чём она не знает.
            emitted = [str(p) for p in (node_type.get("emitted") or []) if isinstance(p, str)]
            detail = " — ".join(
                part for part in (gloss, f"выдавай: {', '.join(emitted)}" if emitted else "") if part
            )
            lines.append(f"- {type_name}" + (f" ({detail})" if detail else ""))
        lines.append("")

    edge_types = _edge_types(profile)
    if edge_types:
        lines.append("Связи: используй только эти виды и только в указанном направлении.")
        for edge in edge_types:
            lines.append(
                f"- {edge.get('type')}: {edge.get('from')} -> {edge.get('to')}"
            )
        lines.append("")

    chunk_edge = str(_section(profile, "ontology").get("chunk_entity_edge") or "").strip()
    if chunk_edge:
        lines.append(
            f"Связь `{chunk_edge}` (чанк -> сущность) строит система. Ты её не извлекаешь "
            f"и о ней не упоминаешь."
        )
        lines.append("")

    skeleton = ", ".join(f'"{plural_entity_key(str(nt.get("type")))}": [...]' for nt in node_types)
    lines.append("Формат ответа — строго один JSON-объект без пояснений и без markdown.")
    if skeleton:
        lines.append(f"Ключи сущностей: {skeleton}, \"relationships\": [...].")
        # Форма объекта связи задавалась здесь же: массив был назван, но содержимое - нет.
        # Из-за этого модель выдавала {from, to} БЕЗ kind, код молча превращал его в
        # RELATED, а связи между фрагментами оказывались неразрешимыми. Правило про оба
        # конца здесь, пока валидация требует именно этого; как только неизвестный конец
        # станет отложенной ссылкой, а не отказом (ADR-037), правило меняется на
        # обратное: выводи связь даже если сущности нет среди объявленных здесь.
        lines.append("")
        lines.append("Каждая связь — объект с тремя обязательными полями:")
        lines.append('- "from" и "to": canonical_name сущностей, объявленных ТОЛЬКО в этом ответе,')
        lines.append("  дословно как в поле canonical_name (регистр и пробелы как в ответе);")
        if edge_types:
            allowed = ", ".join(str(edge.get("type")) for edge in edge_types)
            lines.append(f'- "kind": ровно один вид из списка ({allowed});')
        else:
            lines.append('- "kind": вид связи;')
        lines.append('- "confidence": число от 0 до 1.')
        lines.append("")
        lines.append("Связь без обоих концов среди объявленных здесь сущностей не выводи.")
    return "\n".join(lines).strip()


#: Метод, а не схема. Одинаков для всех доменов, поэтому живёт здесь, а не в YAML профиля.
METHOD_BLOCK = """Метод:
- на каждый чанк — отдельный ответ, не объединяй разные чанки и не перескакивай между ними;
- выказывай одно утверждение об одном факте, не смешивай два источника в один факт;
- не выдумывай типы и виды связей, которых нет в списке выше;
- если не уверен в названии, вырази неопределённость в самом имени, а не отдельным полем;
- если сущностей или связей в тексте нет, верни пустой массив, а не выдуманную запись."""


def render_instruction(profile: Mapping[str, Any], method_prose: str = "") -> str:
    """Полная инструкция: сгенерированная схема + метод + проза профиля.

    `method_prose` — то, что осталось от `prompt_template.user` после вычистки схемы.
    Оно может быть пустым, и это штатно: метод уже добавлен выше. Схема в прозе не
    допускается, и это проверяется тестом, а не договорённостью.
    """
    parts = [render_schema_block(profile), METHOD_BLOCK]
    tail = method_prose.strip()
    if tail:
        parts.append(tail)
    return "\n\n".join(parts)


def instruction_fingerprint(instruction: str) -> str:
    """Короткий отпечаток содержимого инструкции."""
    digest = hashlib.sha256(instruction.encode("utf-8")).hexdigest()
    return digest[:12]


def extraction_identity(profile: Mapping[str, Any], instruction: str) -> str:
    """`extractor_version` по содержимому, а не по ручной метке.

    `deterministic:v1` в коде - константа, одинаковая при любом профиле, промпте и
    модели, то есть она не идентифицирует ничего. Здесь версия профиля входит в строку
    явно, а отпечаток покрывает инструкцию: правка текста промпта обязана изменить
    версию, иначе по графу нельзя будет отличить два разных извлечения.
    """
    meta = _section(profile, "profile")
    name = str(meta.get("name") or "?").strip() or "?"
    version = str(meta.get("version") or "0").strip() or "0"
    return f"llm:{name}@{version}:{instruction_fingerprint(instruction)}"
