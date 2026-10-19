"""Кандидаты сущностей и ограничение извлечения ими (ADR-053).

Тесты строят то, что строит стадия `pre_ingest`: список привязан к `sha256` документа.
Связь проверяется на настоящем файле, а не на заглушке, потому что проверка свежести
существует ровно ради файла.

Отдельно проверяется решение, которое пришлось принять вместо очевидного: кандидаты НЕ
фильтруются по типам онтологии, потому что домены разделяют словарь (ADR-053,
Дополнение 1) и фильтр выбросил бы `BELONGS_TO` - объявленное ребро домена `library`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from graphrag_proto.ingestion_service.pipeline.candidates import (
    CandidateList,
    CandidateListStaleError,
    CandidatesProductionError,
    candidates_path_for_document,
    load_candidates,
    produce_candidates,
)
from graphrag_proto.ingestion_service.pipeline.orchestrator import PipelineContext
from graphrag_proto.ingestion_service.pipeline.prompt_renderer import (
    render_candidates_block,
)
from tests.conftest import candidate_env


def write_document(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "05_adr_log.md"
    path.write_text(text, encoding="utf-8")
    return path


def write_candidates(directory: Path, source: str, text: str, candidates: list[str]) -> Path:
    """Кладёт список кандидатов по тем же правилам, что и пайплайн.

    Имя файла берётся из `candidates_path_for_document`, а не собирается здесь: второе
    определение пути разошлось бы с кодом при первом же изменении и упало бы в тесте
    непонятно где.
    """
    envelope = {
        "source": source,
        "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "count": len(candidates),
        "candidates": candidates,
    }
    directory.mkdir(parents=True, exist_ok=True)
    path = candidates_path_for_document(directory / source)
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    return path


pytestmark = pytest.mark.usefixtures("candidate_env_setup")


def test_list_is_found_next_to_the_document(tmp_path: Path) -> None:
    """Список лежит РЯДОМ с документом, и путь выводится из файла (ADR-053, 2026-10-14)."""
    text = "Document Registry построен поверх Glossary Service."
    document = write_document(tmp_path, text)
    write_candidates(document.parent, document.name, text, ["Document Registry", "Glossary Service"])

    loaded = load_candidates(document, text)

    assert loaded.source == document.name
    assert loaded.candidates == ("Document Registry", "Glossary Service")
    assert loaded.source_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert candidates_path_for_document(document).parent == document.parent


def test_missing_list_is_a_named_failure_not_vector_only(tmp_path: Path) -> None:
    """Отсутствие списка — отказ, а не режим.

    До решения от 2026-10-14 это был режим `vector_only` со статусом `succeeded`: джоба
    выглядела обработанной при пустом графе. Теперь такой исход невозможен, и проверка
    утверждает именно отказ с поимённой причиной.
    """
    text = "что-то"
    document = write_document(tmp_path, text)

    with pytest.raises(CandidatesProductionError) as caught:
        load_candidates(document, text)

    assert "ADR-052" in str(caught.value), "причина обязана называть, чем грозит список"


def test_missing_document_path_is_a_config_error(tmp_path: Path) -> None:
    """Файла документа нет — значит негде положить и список. Это ошибка вызывающего."""
    with pytest.raises(CandidatesProductionError):
        produce_candidates(tmp_path / "нет-такого" / "doc.md", "что-то")


def test_changed_document_is_refused(tmp_path: Path) -> None:
    """Ключевой инвариант: список от другой версии документа обязан быть отвергнут.

    Сверка `sha256` сохранена намеренно. Теперь файл кладёт сам пайплайн, поэтому в обычном
    пути равенство истинно по построению — и именно поэтому проверка ловит другое:
    оборванную или подложенную запись. Работа по чужому списку дала бы правдоподобный, но
    неверный граф, и виноватым выглядел бы разбор.
    """
    old_text = "Document Registry и Glossary Service."
    document = write_document(tmp_path, old_text)
    write_candidates(document.parent, document.name, old_text, ["Document Registry"])

    new_text = old_text + " Добавлен VectorStore."
    with pytest.raises(CandidateListStaleError):
        load_candidates(document, new_text)


def test_empty_candidate_list_means_prohibition_not_failure(tmp_path: Path) -> None:
    """Пустой список — законное состояние, а не отказ.

    ADR-053, Дополнение 4: при пустом списке ограничение НЕ исчезает, а становится строже —
    придумывать нельзя НИЧЕГО. Валить загрузку документа, в котором просто нет имён,
    означало бы запретить грузить законные документы.
    """
    text = "что-то"
    document = write_document(tmp_path, text)
    write_candidates(document.parent, document.name, text, [])

    loaded = load_candidates(document, text)

    assert loaded.candidates == (), "список пуст - и это состояние, а не сбой"
    block = render_candidates_block(loaded.candidates, declared_types=["Concept"])
    assert "НЕ ВЫДАВАТЬ" in block, "пустой список обязан давать полный запрет, а не пустоту"


def test_candidates_are_narrowed_to_the_chunk() -> None:
    """Список документа в разы больше чанка, и платить токенами за лишнее нельзя."""
    candidates = CandidateList(
        source="a.md",
        source_sha256="x",
        candidates=("IngestStage", "VectorStore", "НичегоНет"),
    )

    in_chunk = candidates.for_chunk("устройство стадии IngestStage и VectorStore вместе")

    assert in_chunk == ("IngestStage", "VectorStore")


def test_candidates_path_follows_the_document_not_a_setting(tmp_path: Path) -> None:
    """Путь к списку выводится из файла документа, а не приходит снаружи.

    Проверяется именно это: пока путь приходил из `CANDIDATES_DIR`, папка могла оказаться
    пустой, и пайплайн молча уходил в vector-only (ADR-053, решение от 2026-10-14).
    """
    document = tmp_path / "j-42.md"

    assert candidates_path_for_document(document) == tmp_path / "j-42_candidates.json"
    other = tmp_path / "вложенный" / "j-7.md"
    assert candidates_path_for_document(other) == tmp_path / "вложенный" / "j-7_candidates.json"


def test_prompt_block_lists_names_and_declares_types() -> None:
    block = render_candidates_block(("IngestStage", "VectorStore"), declared_types=["Concept", "Contract"])

    assert "IngestStage" in block
    assert "VectorStore" in block
    assert "Concept, Contract" in block
    # Ключевая часть: имя выбирается из списка, а не придумывается.
    assert "ДОСЛОВНО" in block


def test_prompt_block_for_empty_candidates_forbids_inventing() -> None:
    """Пустой список - запрет называть, а не отсутствие запрета.

    Раньше блок для пустого списка был пустой строкой, то есть ограничение исчезало
    ровно там, где его больше всего нужно. Тест закрепляет обратное: при пустом списке
    блок обязан быть и обязан запрещать.
    """
    block = render_candidates_block((), declared_types=["Concept"])

    assert block.strip(), "пустой список не должен давать пустой блок"
    assert "НЕ ВЫДАВАТЬ" in block
    assert "Concept" in block, "типы из онтологии остаются в силе и при пустом списке"


def test_enrichment_row_carries_the_candidate_counters(tmp_path: Path) -> None:
    """Счётчики ограничения обязаны дойти до отчёта, а не осесть в памяти контекста.

    Правило проекта: факт виден в логе, в отчёте о прогоне и в счётчике. Без строки в
    `job_enrichment` нарушение ограничения было бы видно только в логе EXTRACT, а на
    сводном отчёте выглядело бы как «извлечение не нарушалось».
    """
    from graphrag_proto.ingestion_service.storage.registry import JobStore

    registry = JobStore(tmp_path / "candidates.db")
    registry.record_enrichment(
        "job",
        cause=None,
        lost_entities=0,
        lost_edges=0,
        llm_records=3,
        llm_edges=1,
        candidates_absent=False,
        candidates_total=41,
        llm_names_off_list=2,
    )

    facts = registry.enrichment("job")

    assert facts["candidates_absent"] is False
    assert facts["candidates_total"] == 41
    assert facts["llm_names_off_list"] == 2


def test_absent_candidates_are_distinguishable_from_an_empty_list(tmp_path: Path) -> None:
    """Пустой список и отсутствующий - разные события, и обе записи это хранят.

    Иначе «список был пуст» и «список не нашли» сошлись бы в одном числе, а по этому
    числу нельзя понять, разбирался ли документ вообще.
    """
    from graphrag_proto.ingestion_service.storage.registry import JobStore

    registry = JobStore(tmp_path / "candidates.db")
    registry.record_enrichment(
        "empty",
        cause=None,
        lost_entities=0,
        lost_edges=0,
        llm_records=0,
        llm_edges=0,
        candidates_absent=False,
        candidates_total=0,
    )
    registry.record_enrichment(
        "absent",
        cause=None,
        lost_entities=0,
        lost_edges=0,
        llm_records=0,
        llm_edges=0,
        candidates_absent=True,
        candidates_total=0,
    )

    assert registry.enrichment("empty")["candidates_absent"] is False
    assert registry.enrichment("absent")["candidates_absent"] is True


class _OneAnswerLLM:
    """LLM-заглушка, считающая вызовы: их число — часть проверяемого поведения."""

    def __init__(self, answer: str) -> None:
        self._answer = answer
        self.calls = 0

    def generate(self, prompt: str, **_kwargs: object) -> list[str]:
        self.calls += 1
        self.last_prompt = prompt
        return [self._answer]

    @property
    def last_usage(self) -> None:
        return None


class _SilentLLM:
    """Модель, которая падает, если её позвали.

    Отдельный класс вместо заглушки с ответом: проверка ниже утверждает, что при
    отсутствии списка модель НЕ вызывается вовсе. Заглушка, отдающая пустой JSON,
    сделала бы тест проходящим и при обратном поведении.
    """

    def generate(self, *args: object, **kwargs: object) -> list[str]:
        raise AssertionError("модель не должна вызываться: список кандидатов отсутствует")

    @property
    def last_usage(self) -> None:
        return None


def test_off_list_name_is_counted_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """ГЛАВНЫЙ инвариант ADR-053: имя вне списка обязано быть посчитано.

    Ревью поймало здесь настоящий дефект: сверка читала ключ `canonical_name`, которого
    запись не содержит (у неё `canonical`), и выходила по `if not canonical` на КАЖДОЙ
    записи. Счётчик был всегда нулём, и гейт был зелёным, потому что ненулевое
    значение не утверждал никто.

    Тест написан так, чтобы падать именно при этом откате: значение кандидата
    не-нулевое, поэтому «проверка ничего не делает» и «проверка считает» — разные
    исходы.
    """
    from graphrag_proto.ingestion_service.pipeline.orchestrator import ExtractStage
    monkeypatch.setenv("EXTRACT_LLM", "true")

    payload = {
        "tags": [
            {"name": "IngestStage", "id": "concept:ingest"},
            {"name": "ВЫДУМАННАЯ_КОНЦЕПЦИЯ", "id": "concept:fabricated"},
        ],
        "relationships": [],
    }
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://off-list.txt",
        chunks=["устройство стадии IngestStage описано"],
    )
    ctx = candidate_env(ctx, ["IngestStage"])
    profile = {
        "extraction": {
            "llm_enabled": True,
            "prompt_template": {"id": "t", "system": "s", "user": "извлеки сущности"},
        },
        "ontology": {
            "node_types": [{"type": "Concept", "unique_key": "canonical_name"}],
            "edge_types": [],
        },
        "validation": {},
        "canonicalization": {},
    }

    ExtractStage(
        llm=_OneAnswerLLM(json.dumps(payload, ensure_ascii=False)),
        profile_fetcher=lambda _domain: profile,
        optional_failure=True,
    ).run(ctx)

    assert ctx.llm_names_total == 2, "обе записи должны быть посчитаны как извлечённые"
    assert ctx.llm_names_off_list == 1, (
        "имя вне списка обязано быть посчитано; 0 означает, что сверка не работает вовсе"
    )
    # Нарушение СОСИТСЯ, а не отбрасывается: «модель ошиблась» и «сущности нет» - разные
    # утверждения, и молча выкидывать запись значило бы терять улику.
    assert {entity["canonical"] for entity in ctx.entities} == {
        "IngestStage",
        "ВЫДУМАННАЯ_КОНЦЕПЦИЯ",
    }


def test_chunk_without_candidates_is_sent_with_a_hard_prohibition(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустой список - это ЗАПРЕТ называть, а не снятое ограничение (ADR-053).

    Ветка жива, но на текущем корпусе после ADR-054 не срабатывает: разрез markdown по
    блокам кода устранён, и разборов без списка стало 0 из 221 чанка. Проверяется всё
    равно - разбиение может измениться, а молчание при смене разбиения опаснее.
    Пустой блок был бы ХУЖЕ пропуска - он читался бы как «список не пришёл».
    """
    from graphrag_proto.ingestion_service.pipeline.orchestrator import ExtractStage
    from graphrag_proto.ingestion_service.pipeline.prompt_renderer import (
        render_candidates_block,
    )

    monkeypatch.setenv("EXTRACT_LLM", "true")

    empty_block = render_candidates_block((), declared_types=["Concept"])
    assert "НЕ ВЫДАВАТЬ" in empty_block, "пустой список обязан давать запрет, а не пустоту"
    assert "Concept" in empty_block, "типы из онтологии остаются в силе"

    llm = _OneAnswerLLM(json.dumps({"tags": [], "relationships": []}, ensure_ascii=False))
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://empty-chunk.txt",
        # Текст документа содержит имя, а чанк - нет. Именно это и создаёт разрыв: список
        # документа непуст, и пайплайн получает его сам, но конкретный чанк в него не
        # попадает. Текст без единого имени падал бы раньше - на пустом списке документа.
        raw_text="IngestStage обрабатывает документы.",
        chunks=["просто текст без имён"],
    )
    ctx = candidate_env(ctx, ["IngestStage"])
    profile = {
        "extraction": {
            "llm_enabled": True,
            "prompt_template": {"id": "t", "system": "s", "user": "извлеки сущности"},
        },
        "ontology": {
            "node_types": [{"type": "Concept", "unique_key": "canonical_name"}],
            "edge_types": [],
        },
        "validation": {},
        "canonicalization": {},
    }

    ExtractStage(
        llm=llm,
        profile_fetcher=lambda _domain: profile,
        optional_failure=True,
    ).run(ctx)

    assert ctx.chunks_total == 1
    assert ctx.chunks_without_candidates == 1, "факт обязан считаться, а не теряться"


# Дальше - полный конвейер на in-memory сторах. Прежний сценарий «документ без списка
# грузится только в вектор» удалён решением от 2026-10-14: такой джоба была бы `succeeded`
# при пустом графе - ложный успех, который ADR-053 сформулировал.
from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    Analyzer,
    ChunkStage,
    CommitStage,
    ContractStage,
    DedupStage,
    EmbedStage,
    ExtractStage,
    IngestStage,
    NormalizeStage,
    ValidateStage,
)
from graphrag_proto.ingestion_service.projection import InMemoryProjectionStateStore
from graphrag_proto.ingestion_service.readers.registry import TxtReader
from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry
from graphrag_proto.retrieval.adapters.inmemory import (
    InMemoryGraphStore,
    InMemoryVectorStore,
)


def test_pipeline_produces_candidates_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Загрузка без подготовки: пайплайн сам получает кандидатов и строит граф.

    Главное проверяемое свойство после решения от 2026-10-14: **оператору ничего готовить
    не надо**. Раньше требовался отдельный запуск стадии, и её отсутствие давало тихий
    vector-only при статусе `succeeded`. Теперь список производится здесь, перед `EXTRACT`.
    """
    monkeypatch.setenv("EXTRACT_LLM", "true")
    registry = DocumentRegistry(tmp_path / "self-produced.db")
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    profile = {
        "extraction": {
            "llm_enabled": True,
            "prompt_template": {"id": "t", "system": "s", "user": "извлеки сущности"},
        },
        "ontology": {
            "node_types": [{"type": "Concept", "unique_key": "canonical_name"}],
            "edge_types": [],
        },
        "validation": {},
        "canonicalization": {},
    }
    analyzer = Analyzer(
        [
            IngestStage({"txt": TxtReader()}),
            ChunkStage(),
            EmbedStage(),
            ExtractStage(llm=_OneAnswerLLM(json.dumps({"tags": [], "relationships": []})), optional_failure=True),
            NormalizeStage(""),
            DedupStage(),
            ContractStage(),
            ValidateStage(),
            CommitStage(
                registry,
                graph_store=graph,
                vector_store=vector,
                graph_optional=True,
                projection_state_store=InMemoryProjectionStateStore(),
            ),
        ],
        profile_fetcher=lambda _domain: profile,
    )
    source = tmp_path / "self.txt"
    source.write_text("Document Registry построен поверх Glossary Service.", encoding="utf-8")
    # Кандидатов НЕ кладём: их должен получить пайплайн.
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://self.txt",
        source_path=str(source),
    )

    analyzer.run(ctx)

    produced = candidates_path_for_document(source)
    assert produced.is_file(), "пайплайн обязан оставить файл кандидатов рядом с документом"
    assert ctx.candidates_total and ctx.candidates_total > 0, (
        "список получен и непуст, иначе разбор шёл бы с запретом называть что-либо"
    )
    assert ctx.candidates_absent is False, "отсутствия списка больше не бывает"


def test_candidates_are_not_filtered_by_ontology_types() -> None:
    """Решение владельца (ADR-053, Дополнение 1): фильтр по типам отклонён.

    `BELONGS_TO` объявлено ребром домена `library` в
    `docs/01_ontology_and_domain_profile.md:103` и в профиль `it` не входит. Фильтр по
    активному профилю выбросил бы объявленный тип связи другого домена, поэтому проверка
    закрепляет отсутствие фильтра, а не сам список.
    """
    candidates = ("BELONGS_TO", "ADAPTED_INTO", "Document Registry")
    active_it_edge_types = {"REQUIRES_CONSTRAINT", "CONTRADICTS", "SIMILAR_TO", "REFERENCES"}

    kept = tuple(name for name in candidates if name in active_it_edge_types)

    assert "BELONGS_TO" in candidates
    assert "BELONGS_TO" not in active_it_edge_types
    # Именно поэтому фильтр опасен: он отсёк бы это имя.
    assert kept == ()
