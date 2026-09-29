"""Антидрейф: инструкция извлечения обязана выводиться из профиля, а не обгонять его.

Проверяется то, чего не проверял ни один тест до сих пор: **реальные файлы**
`domain_profiles/*.yaml`. Тесты контракта профиля работают на словарях, собранных в самом
тесте, поэтому профиль, который лежит в репозитории, не сверялся ни с чем — и вид связи,
добавленный в `ontology.edge_types` и забытый в прозе промпта, оказывался объявленным и
никогда не извлекаемым. Выглядело это как «модель не выдаёт связи», и диагностировать
пришлось бы на стенде.

Здесь проверяется три вещи, по возрастанию строгости:

1. **Каждый объявленный тип сущности и вид связи присутствует в собранной инструкции.**
   Ловит главный дрейф.
2. **В инструкции нет служебных полей узла.** `chunk_ids`, `extractor_version`,
   `source_ids` проставляет система; если они попали в «выдавай», модель будет выдумывать
   то, о чём не знает, — и это молча портит разбор ответа.
3. **Идентичность извлечения зависит от содержимого инструкции.** Правка текста промпта
   обязана менять `extractor_version`, иначе по графу нельзя отличить два разных
   извлечения, а `EXTRACTOR_VERSION` в коде этого не умеет.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from graphrag_proto.ingestion_service.pipeline.prompt_renderer import (
    extraction_identity,
    render_instruction,
)

PROFILES_DIR = Path(__file__).resolve().parents[1] / "domain_profiles"

#: Поля, которые проставляет система. Модель не должна ни возвращать их, ни быть о них
#: спрошена: они вычисляются платформой и не восстанавливаются из текста (L1-05).
SYSTEM_ONLY_PROPERTIES = frozenset(
    {"tag_id", "node_id", "source_ids", "chunk_ids", "extractor_version", "variants"}
)


def _profile_files() -> list[Path]:
    files = sorted(PROFILES_DIR.glob("domain_profile.*.yaml"))
    assert files, f"профили домена не найдены в {PROFILES_DIR}"
    return files


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name}: профиль должен быть mapping"
    return data


@pytest.mark.parametrize("path", _profile_files(), ids=lambda p: p.name)
def test_every_declared_type_reaches_the_instruction(path: Path) -> None:
    """Объявленное в профиле обязано попасть в промпт, иначе это не извлекается."""
    profile = _load(path)
    instruction = render_instruction(profile)

    node_types = (profile.get("ontology") or {}).get("node_types") or []
    edge_types = (profile.get("ontology") or {}).get("edge_types") or []
    assert node_types, f"{path.name}: профиль без типов сущностей бессмысленен"

    for node_type in node_types:
        type_name = str(node_type["type"])
        assert type_name in instruction, f"{path.name}: тип {type_name} не попал в инструкцию"
    for edge in edge_types:
        edge_type = str(edge["type"])
        assert edge_type in instruction, f"{path.name}: вид связи {edge_type} не попал в инструкцию"
        # Направление тоже объявлено, и потерять его нельзя: тот же вид от другого
        # отправителя - это другое утверждение.
        assert f"{edge['from']} -> {edge['to']}" in instruction, (
            f"{path.name}: направление {edge['type']} ({edge['from']} -> {edge['to']}) "
            f"потеряно при сборке инструкции"
        )


@pytest.mark.parametrize("path", _profile_files(), ids=lambda p: p.name)
def test_instruction_does_not_ask_model_for_system_properties(path: Path) -> None:
    """Служебные поля не должны попадать в «выдавай»: система ставит их сама.

    Проверяется именно собранная инструкция, а не профиль: профиль может объявить
    `properties` с `chunk_ids` и `extractor_version` (он их и объявляет, они нужны узлу в
    графе), и это нормально. Запрещено, чтобы они попали в то, что просят у модели.
    """
    profile = _load(path)
    instruction = render_instruction(profile)
    lines = [line for line in instruction.splitlines() if line.startswith("- ")]
    assert lines, f"{path.name}: в инструкции нет ни одного типа"
    for line in lines:
        leaked = sorted(prop for prop in SYSTEM_ONLY_PROPERTIES if prop in line)
        assert not leaked, f"{path.name}: в строке инструкции {line!r} модель просят {leaked}"


def test_identity_changes_with_instruction_content(path: Path | None = None) -> None:
    """Правка инструкции обязана менять `extractor_version`."""
    profile = _load(path or _profile_files()[0])
    baseline = render_instruction(profile)
    identity = extraction_identity(profile, baseline)
    assert identity == extraction_identity(profile, baseline), "одна и та же инструкция — разные версии"

    changed = copy.deepcopy(profile)
    for node_type in changed["ontology"]["node_types"]:
        node_type["gloss"] = "другое пояснение"
    altered = render_instruction(changed)
    assert altered != baseline, "изменение профиля обязано менять инструкцию"
    assert extraction_identity(changed, altered) != identity, (
        "идентичность не зависит от содержимого инструкции: по графу нельзя будет "
        "отличить два разных извлечения"
    )


def test_instruction_is_pure_function_of_profile(path: Path | None = None) -> None:
    """Один и тот же профиль обязан давать побайтово одну и ту же инструкцию.

    Иначе паспорт джобы записывал бы случайную величину, а отчёт о прогоне нельзя было бы
    повторить: между двумя одинаковыми по смыслу прогонами получились бы разные версии.
    """
    profile = _load(path or _profile_files()[0])
    assert render_instruction(profile) == render_instruction(copy.deepcopy(profile))


def test_prompt_sent_to_model_carries_profile_types(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяется проводка, а не рендерер: что уходит в модель, содержит профиль.

    Тесты выше проверяют функцию сборки. Эта — то, ради чего они и нужны: если завтра
    промпт снова начнут брать из `prompt_template.user` прозой, все они останутся зелёными
    (рендерер-то на месте), а объявленные в профиле виды связей перестанут доходить до
    модели. Ловится только здесь, на границе с моделью.
    """
    from graphrag_proto.ingestion_service.pipeline.orchestrator import ExtractStage, PipelineContext

    profile = _load(PROFILES_DIR / "domain_profile.it.yaml")
    sent: list[str] = []

    class _CapturingLLM:
        def generate(self, prompt: str, system: str = "", stream: bool = True) -> list[str]:
            sent.append(prompt)
            return ['{"requirements": [], "concepts": [], "contracts": [], "relationships": []}']

    monkeypatch.setenv("EXTRACT_LLM", "true")
    ctx = PipelineContext(
        job_id="j1",
        domain="it",
        doc_type="md",
        source_url="src://d.md",
        chunks=["текст про требования и контракты"],
        profile=profile,
    )
    ExtractStage(llm=_CapturingLLM()).run(ctx)

    assert sent, "модель не была вызвана: тест проверил бы пустоту"
    prompt = sent[0]
    for edge in (profile.get("ontology") or {}).get("edge_types") or []:
        assert str(edge["type"]) in prompt, f"вид связи {edge['type']} не дошёл до модели"
    for node_type in (profile.get("ontology") or {}).get("node_types") or []:
        assert str(node_type["type"]) in prompt, f"тип {node_type['type']} не дошёл до модели"
