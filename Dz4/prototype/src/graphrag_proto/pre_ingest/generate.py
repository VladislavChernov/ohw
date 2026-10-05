"""Генерация кандидатов сущностей из документов (стадия pre-ingest, ADR-053).

Контракт запуска: на вход папка с документами, на выход папка с результатами. Для каждого
`<имя>.md` пишется `<имя>_candidates.json`.

Зачем стадия нужна: без списка кандидатов модель выдумывает половину имён (ADR-052, измерено
50.0% на `docs/05_adr_log.md`), а выдуманное имя разрешается в графе так же, как настоящее.
Список ограничивает модель выбором из найденного, а не порождением.

Что НЕ делает этот модуль: не назначает типы и не строит связи. Типы NER здесь новостные
(`ORG`/`PER`/`LOC`) и для доменной онтологии бесполезны, поэтому в файл попадают только
тексты кандидатов.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Поверхностные правила. Они дают плотность: медиана 41 кандидат на чанк против 2 у NER.
CANDIDATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # идентификаторы: `_identity_key`, `ingestion-api`, `app.py`, `docs/06`
    re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[._\-][A-Za-z0-9]+)+\b"),
    # ВЕРХНИЙ РЕГИСТР: `ADR`, `LLM`, `API`
    re.compile(r"\b[A-Z]{2,}[A-Z0-9]*\b"),
    # смешанный регистр: `IngestStage`, `VectorStore`
    re.compile(r"\b[A-Za-z]+[A-Z][A-Za-z0-9]*\b"),
    # буквы+цифры: `ADR-049`, `M0`
    re.compile(r"\b[A-Z]+-\d+\b"),
    # код в обратных кавычках
    re.compile(r"`([^`\n]{2,60})`"),
)

#: Слова, которые формально подходят под правила, но сущностями не являются. Список короткий
#: и намеренно неполный: он чинит самые частые ложные срабатывания, а не подгоняет результат.
STOPWORDS: frozenset[str] = frozenset(
    {
        "TODO", "NOTE", "WARNING", "ERROR", "INFO", "OK", "HTTP", "JSON", "YAML",
        "SQL", "CPU", "RAM", "VRAM", "URL", "URI", "PDF", "UTF", "ASCII",
        "TRUE", "FALSE", "NULL", "NONE", "AND", "NOT", "FOR", "THE",
    }
)

#: Символы, которых нет ни в одном настоящем кандидате: скобки, кавычки, знаки препинания,
#: операторы. Кандидат, содержащий такой символ, - это обрывок кода или прозы, а не имя.
#: Проверка задана положительным признаком «структурных символов нет», потому что перечень
#: разрешённых символов запретил бы законную сущность при первой же правке онтологии.
STRUCTURAL = frozenset('"\'`(){}[]<>|=^~,;:?!*+#\\')

MIN_LENGTH = 3


def is_well_formed(candidate: str) -> bool:
    """Кандидат пригоден для ограничения модели.

    Проверка именно такая, а не «похоже на имя»: список нужен, чтобы сузить выбор модели,
    и обрывок вроде `'(domain, canonical_name)'` или `'"atomic"'` это свойство не сужает,
    а только разбавляет.
    """
    if len(candidate) < MIN_LENGTH:
        return False
    if any(char in STRUCTURAL for char in candidate):
        return False
    # Начинаться и заканчиваться пробелой или точкой имя не может: это обрывок предложения.
    if not (candidate[0].isalnum() and candidate[-1].isalnum()):
        return False
    # Буква обязательна. Проверка формы сама по себе пропускала голые числа и даты:
    # `404`, `0.0`, `2026-09-17` проходили как «не структурные символы, оба края
    # alnum», и попадали в список, который предназначен для ограничения модели.
    return any(char.isalpha() for char in candidate)


@dataclass(frozen=True)
class CandidateSet:
    """Кандидаты одного документа вместе с тем, из какой версии источника они получены."""

    source_name: str
    source_sha256: str
    source_bytes: int
    candidates: list[str]
    by_rule: int
    by_ner: int


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rule_candidates(text: str) -> set[str]:
    """Кандидаты по поверхностным правилам."""
    found: set[str] = set()
    for rule in CANDIDATE_PATTERNS:
        for match in rule.finditer(text):
            value = (match.group(1) if match.groups() else match.group(0)).strip()
            if value in STOPWORDS or not is_well_formed(value):
                continue
            found.add(value)
    return found


def ner_candidates(text: str, ner: Any, segmenter: Any) -> set[str]:
    """Кандидаты по NER. Типы отбрасываются: они новостные, а не доменные.

    Фильтры применяются те же, что и к правилам. Раньше NER шёл в объединение мимо них,
    и в списке оказывались `RAM`, `AI`, `LLM` из стоп-слов - то есть два источника
    кандидатов фильтровались по разным правилам, и проверка на стоп-слова врала.
    """
    from natasha import Doc

    doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_ner(ner)
    return {
        span.text.strip()
        for span in doc.spans
        if span.text.strip() not in STOPWORDS and is_well_formed(span.text.strip())
    }


def collapse_case_variants(candidates: set[str], text: str) -> set[str]:
    """Схлопывает варианты, отличающиеся только регистром: `LLM`/`llm`, `LangChain`/`langchain`.

    Оставляется тот вариант, который в документе встречается чаще. Правило измеримо, а не
    вкусовое: иначе модель получит одно и то же имя дважды и не сможет выбрать, а в графе
    появятся две версии одной сущности. Порядок при равенстве задан явно, иначе файл
    менялся бы от запуска к запуску.
    """
    folded: dict[str, list[str]] = {}
    for candidate in candidates:
        folded.setdefault(candidate.casefold(), []).append(candidate)

    chosen: set[str] = set()
    for variants in folded.values():
        if len(variants) == 1:
            chosen.add(variants[0])
            continue
        chosen.add(
            max(
                variants,
                key=lambda item: (
                    text.casefold().count(item.casefold()),
                    # При равной частоте предпочитаем смешанный регистр: `LangChain`
                    # читается как имя, `langchain` - как слово из lowercase-прозаики.
                    not item.islower() and not item.isupper(),
                    item,
                ),
            )
        )
    return chosen


def build_candidate_set(path: Path, text: str, ner: Any, segmenter: Any) -> CandidateSet:
    CANDIDATE_PATTERNS = rule_candidates(text)
    ner_found = ner_candidates(text, ner, segmenter)
    # Объединение считается ДО схлопывания регистра: иначе потерялось бы разделение на
    # «нашло правило» и «нашёл только NER», а это единственный способ увидеть, какой
    # источник что-то дал.
    merged = collapse_case_variants(CANDIDATE_PATTERNS | ner_found, text)
    return CandidateSet(
        source_name=path.name,
        source_sha256=sha256_of(text),
        source_bytes=len(text.encode("utf-8")),
        # Сортировка нужна не для красоты: файл сравнивают при проверке свежести, и
        # недетерминированный порядок давал бы ложное расхождение.
        candidates=sorted(merged, key=lambda item: (item.casefold(), item)),
        by_rule=len(CANDIDATE_PATTERNS),
        by_ner=len(ner_found - CANDIDATE_PATTERNS),
    )


def to_envelope(candidate_set: CandidateSet, extractor: str) -> dict[str, Any]:
    return {
        "source": candidate_set.source_name,
        "source_sha256": candidate_set.source_sha256,
        "source_bytes": candidate_set.source_bytes,
        "generated_by": extractor,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "count": len(candidate_set.candidates),
        "counts": {"by_rule": candidate_set.by_rule, "by_ner_only": candidate_set.by_ner},
        "candidates": candidate_set.candidates,
    }


def target_name(document_name: str) -> str:
    """`<имя документа>_candidates.json` - имя документа в начале, как решил владелец."""
    return f"{document_name}_candidates.json"


def iter_documents(source: Path) -> list[Path]:
    return sorted(path for path in source.glob("*.md") if path.is_file())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-ingest: кандидаты сущностей из документов")
    parser.add_argument("--src", required=True, type=Path, help="папка с документами")
    parser.add_argument("--dst", required=True, type=Path, help="папка для результатов")
    args = parser.parse_args(argv)

    if not args.src.is_dir():
        print(f"папка с документами не найдена: {args.src}", file=sys.stderr)
        return 2
    args.dst.mkdir(parents=True, exist_ok=True)

    documents = iter_documents(args.src)
    if not documents:
        print(f"в папке нет .md: {args.src}", file=sys.stderr)
        return 2

    from natasha import NewsEmbedding, Segmenter
    from natasha.ner import NewsNERTagger

    segmenter = Segmenter()
    ner = NewsNERTagger(NewsEmbedding())

    for path in documents:
        text = path.read_text(encoding="utf-8")
        candidate_set = build_candidate_set(path, text, ner, segmenter)
        envelope = to_envelope(candidate_set, "natasha+CANDIDATE_PATTERNS")
        out = args.dst / target_name(path.name)
        out.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        print(
            f"{path.name} -> {out.name}: {envelope['count']} кандидатов "
            f"(правила {candidate_set.by_rule}, только NER {candidate_set.by_ner})"
        )
    print(f"готово: {len(documents)} документов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())