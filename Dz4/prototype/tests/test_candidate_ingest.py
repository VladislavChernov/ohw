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
    CANDIDATES_DIR_ENV,
    CandidateList,
    CandidateListConfigError,
    CandidateListStaleError,
    candidates_dir_from_env,
    load_candidates,
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
    envelope = {
        "source": source,
        "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "count": len(candidates),
        "candidates": candidates,
    }
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{source}_candidates.json"
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    return path


pytestmark = pytest.mark.usefixtures("candidate_env_setup")


def test_list_is_found_and_carries_sha256(tmp_path: Path) -> None:
    text = "Document Registry построен поверх Glossary Service."
    document = write_document(tmp_path, text)
    directory = tmp_path / "candidates"
    write_candidates(directory, document.name, text, ["Document Registry", "Glossary Service"])

    loaded = load_candidates(directory, document.name, text)

    assert loaded is not None
    assert loaded.source == document.name
    assert loaded.candidates == ("Document Registry", "Glossary Service")
    assert loaded.source_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_missing_list_is_vector_only_mode_not_failure(tmp_path: Path) -> None:
    text = "что-то"
    document = write_document(tmp_path, text)

    assert load_candidates(tmp_path / "empty", document.name, text) is None


def test_unset_directory_is_a_config_error_not_a_silent_vector(tmp_path: Path) -> None:
    """Ненастроенная папка - ошибка, а не «списка нет».

    Иначе стенд без `CANDIDATES_DIR` отработал бы целиком в векторе и выглядел бы
    исправным: отчёт показал бы успех, графа не было бы ни одного. Различие принципиально:
    «у документа нет своего списка» - решённый режим, «стадия не запускалась» - ошибка.
    """
    text = "что-то"
    document = write_document(tmp_path, text)

    with pytest.raises(CandidateListConfigError):
        load_candidates(None, document.name, text)


def test_missing_list_can_be_made_an_error(tmp_path: Path) -> None:
    text = "что-то"
    document = write_document(tmp_path, text)

    with pytest.raises(FileNotFoundError):
        load_candidates(tmp_path / "empty", document.name, text, on_missing="error")


def test_changed_document_is_refused(tmp_path: Path) -> None:
    """Ключевой инвариант: список от другой версии документа обязан быть отвергнут.

    Без этой проверки разбор молча идёт по чужому списку: граф получается правдоподобным,
    и виноватым выглядит разбор, а не устаревший список.
    """
    old_text = "Document Registry и Glossary Service."
    document = write_document(tmp_path, old_text)
    directory = tmp_path / "candidates"
    write_candidates(directory, document.name, old_text, ["Document Registry"])

    new_text = old_text + " Добавлен VectorStore."
    with pytest.raises(CandidateListStaleError):
        load_candidates(directory, document.name, new_text)


def test_candidates_are_narrowed_to_the_chunk() -> None:
    """Список документа в разы больше чанка, и платить токенами за лишнее нельзя."""
    candidates = CandidateList(
        source="a.md",
        source_sha256="x",
        candidates=("IngestStage", "VectorStore", "НичегоНет"),
    )

    in_chunk = candidates.for_chunk("устройство стадии IngestStage и VectorStore вместе")

    assert in_chunk == ("IngestStage", "VectorStore")


def test_env_directory_absent_means_no_candidates() -> None:
    assert candidates_dir_from_env({}) is None
    assert candidates_dir_from_env({CANDIDATES_DIR_ENV: "  "}) is None
    assert candidates_dir_from_env({CANDIDATES_DIR_ENV: "/data/pre-ingest"}) == Path(
        "/data/pre-ingest"
    )


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

    Решение «пропускать такой вызов» остаётся открытым: таких чанков 10 из 343,
    то есть 2.9% корпуса. Пока владелец не выбрал, проверяется другое: вызов уходит,
    но блок запрещает придумывать что-либо. Пустой блок был бы ХУЖЕ пропуска -
    он читался бы как «список не пришёл, значит можно всё».
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
"""Решение владельца: нет списка - грузим только вектор (ADR-053).

Проверяются ОБЕ оси, а не одна: главный риск здесь в том, что при отсутствии
списка всё выглядит исправно - вектора-то записались, - но графа при этом не
существует. Поэтому тест требует отсутствия нод в графе И наличия векторов.
"""
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


def test_document_without_candidates_is_written_as_vectors_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    registry = DocumentRegistry(tmp_path / "vector-only.db")
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
            ExtractStage(llm=_SilentLLM(), optional_failure=True),
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
    source = tmp_path / "no-list.txt"
    source.write_text("требование индексировать документы", encoding="utf-8")
    # Папка кандидатов настроена фикстурой модуля, но своего списка у документа нет:
    # это решённый режим, а не сбой.
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://no-list.txt",
        source_path=str(source),
    )

    analyzer.run(ctx)

    assert ctx.candidates_absent is True
    assert vector._vectors, "вектора обязаны быть записаны: ради них документ и грузится"
    assert not graph.list_chunk_ids_of_source("src://no-list.txt", "it"), (
        "без списка кандидатов графовая ось не строится: иначе модель выдаст выдумку"
    )


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
