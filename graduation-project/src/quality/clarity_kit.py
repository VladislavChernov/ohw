"""Tooling for a human-annotated clarity corpus.

The module does not change the production clarity formula. It exports text
fragments from generation snapshots together with the categories the current
metric sees, and summarises human annotations so a future metric change can be
justified by data rather than intuition.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.contracts import load_snapshot_document, read_json_object
from src.quality.assessor import (
    CLARITY_SIGNAL_NAMES,
    assess_clarity_goals,
    clarity_signals,
)

HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
WORD = re.compile(r"[\w\-]+", re.UNICODE)
PARAGRAPH = re.compile(r"\n\s*\n")

ANNOTATION_FIELDS = ("annotator", "measurable", "signals", "rating")
DEFAULT_TRAIN_SHARE = 70


@dataclass(frozen=True)
class Annotation:
    """One annotator's judgement for a fragment."""

    annotator: str
    measurable: bool
    signals: tuple[str, ...]
    rating: int


def fragment_id(
    *, source: str, scenario: str, run_index: int | None, text: str
) -> str:
    """Return a stable short id for an exported fragment."""
    payload = f"{source}|{scenario}|{run_index}|{text}".encode()
    return hashlib.sha1(payload).hexdigest()[:12]


def split_fragments(text: str) -> list[str]:
    """Split a rendered artifact into section-level fragments.

    Markdown headings take priority. A text without headings is split by blank
    lines, so each exported item stays close to a requirement or test case.
    """
    groups: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if HEADING.match(line) and current:
            groups.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        groups.append(current)

    fragments = ["\n".join(group).strip() for group in groups]
    fragments = [fragment for fragment in fragments if fragment]
    if len(fragments) == 1:
        paragraphs = PARAGRAPH.split(fragments[0])
        if len(paragraphs) > 1:
            fragments = [item.strip() for item in paragraphs if item.strip()]
    return fragments


def export_snapshot(
    path: Path, *, min_words: int = 8
) -> list[dict[str, Any]]:
    """Build annotation records from one ``generation.json`` snapshot."""
    document = load_snapshot_document(read_json_object(path), path.name)
    records: list[dict[str, Any]] = []
    for scenario in document.scenarios or []:
        texts: list[tuple[str, int | None]] = []
        if scenario.rendered:
            texts.append((scenario.rendered, None))
        for index, generation in enumerate(scenario.generations or [], start=1):
            texts.append((generation, index))

        for text, run_index in texts:
            for fragment in split_fragments(text):
                if len(WORD.findall(fragment)) < min_words:
                    continue
                records.append(
                    {
                        "id": fragment_id(
                            source=path.name,
                            scenario=scenario.filename,
                            run_index=run_index,
                            text=fragment,
                        ),
                        "source_file": path.name,
                        "scenario": scenario.filename,
                        "artifact_type": scenario.artifact_type,
                        "model": document.model,
                        "generated_at": document.generated_at,
                        "run_index": run_index,
                        "requirement_ids": list(scenario.requirement_ids or []),
                        "text": fragment,
                        "signals": list(clarity_signals(fragment)),
                        "clarity_score": assess_clarity_goals(fragment),
                        "annotation": None,
                    }
                )
    return records


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file, reporting the line number of malformed entries."""
    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{lineno}: некорректный JSON: {exc.msg}") from None
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{lineno}: ожидается JSON-объект")
        records.append(value)
    return records


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    """Write one JSON object per line, UTF-8 without BOM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False) + "\n" for record in records
        ),
        encoding="utf-8",
    )


def parse_annotation(raw: Any, *, source: str) -> Annotation:
    """Validate one annotation record."""
    if not isinstance(raw, dict):
        raise ValueError(f"{source}: ожидается объект аннотации")
    missing = [field for field in ANNOTATION_FIELDS if field not in raw]
    if missing:
        raise ValueError(f"{source}: нет полей: {', '.join(missing)}")
    annotator = raw["annotator"]
    measurable = raw["measurable"]
    signals = raw["signals"]
    rating = raw["rating"]
    if not isinstance(annotator, str) or not annotator.strip():
        raise ValueError(f"{source}: annotator должен быть непустой строкой")
    if not isinstance(measurable, bool):
        raise ValueError(f"{source}: measurable должен быть логическим значением")
    if not isinstance(signals, list) or any(
        not isinstance(name, str) or name not in CLARITY_SIGNAL_NAMES
        for name in signals
    ):
        raise ValueError(
            f"{source}: signals должны содержать только "
            f"{', '.join(CLARITY_SIGNAL_NAMES)}"
        )
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        raise ValueError(f"{source}: rating должен быть целым от 1 до 5")
    return Annotation(
        annotator=annotator,
        measurable=measurable,
        signals=tuple(signals),
        rating=rating,
    )


def collect_annotations(path: Path) -> dict[str, Annotation]:
    """Read annotation records keyed by fragment id."""
    result: dict[str, Annotation] = {}
    for index, raw in enumerate(read_jsonl(path), start=1):
        identifier = raw.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"{path.name}:{index}: нет поля id")
        annotation = raw["annotation"] if "annotation" in raw else raw
        result[identifier] = parse_annotation(
            annotation, source=f"{path.name}:{index} ({identifier})"
        )
    return result


def cohen_kappa(pairs: Sequence[tuple[bool, bool]]) -> float | None:
    """Inter-annotator agreement for binary judgements."""
    total = len(pairs)
    if not total:
        return None
    rate_a = sum(1 for a, _ in pairs if a) / total
    rate_b = sum(1 for _, b in pairs if b) / total
    observed = sum(1 for a, b in pairs if a == b) / total
    expected = rate_a * rate_b + (1 - rate_a) * (1 - rate_b)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1 - expected)


def weighted_kappa(
    pairs: Sequence[tuple[int, int]],
    categories: Sequence[int] = (1, 2, 3, 4, 5),
) -> float | None:
    """Quadratic-weighted Cohen's kappa for ordinal ratings.

    Unlike the binary kappa, this penalises a disagreement between 1 and 5 much
    more than one between 3 and 4. Zero expected disagreement is handled the
    same way as in ``cohen_kappa``.
    """
    if not pairs:
        return None
    levels = sorted(set(categories) | {value for pair in pairs for value in pair})
    total = len(pairs)
    rate_a = {level: 0.0 for level in levels}
    rate_b = {level: 0.0 for level in levels}
    observed = 0.0
    for a, b in pairs:
        rate_a[a] += 1.0
        rate_b[b] += 1.0
        observed += float((a - b) ** 2)
    rate_a = {level: value / total for level, value in rate_a.items()}
    rate_b = {level: value / total for level, value in rate_b.items()}
    observed /= total
    expected = sum(
        rate_a[left] * rate_b[right] * float((left - right) ** 2)
        for left in levels
        for right in levels
    )
    if expected == 0.0:
        return 1.0 if observed == 0.0 else 0.0
    return 1.0 - observed / expected


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation without third-party dependencies."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    covariance = sum(
        (x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)
    )
    variance_x = sum((x - mean_x) ** 2 for x in xs)
    variance_y = sum((y - mean_y) ** 2 for y in ys)
    if variance_x == 0 or variance_y == 0:
        return None
    return covariance / (variance_x * variance_y) ** 0.5


def is_train(identifier: str, *, train_share: int = DEFAULT_TRAIN_SHARE) -> bool:
    """Deterministic train/holdout split by fragment id."""
    if not 0 <= train_share <= 100:
        raise ValueError("train_share должен быть от 0 до 100")
    digest = hashlib.sha1(identifier.encode("utf-8")).hexdigest()
    return int(digest, 16) % 100 < train_share


def score_signals(signals: Sequence[str], weights: dict[str, float]) -> float:
    """Weight a fragment's categories, mirroring the metric's linear form."""
    return sum(weights.get(name, 0.0) for name in signals)


def derive_weights(
    items: Sequence[dict[str, Any]],
    annotation_key: str,
) -> dict[str, float]:
    """Exploratory weights from category/rating association.

    Correlations below zero are clamped to zero. If all categories are
    uninformative, equal weights are returned. This function never writes back
    to production code.
    """
    scores: dict[str, list[float]] = {name: [] for name in CLARITY_SIGNAL_NAMES}
    targets: list[float] = []
    for item in items:
        targets.append(float(item[annotation_key].rating))
        present = set(item["signals"])
        for name in CLARITY_SIGNAL_NAMES:
            scores[name].append(1.0 if name in present else 0.0)
    if len(targets) < 2:
        return {name: 0.2 for name in CLARITY_SIGNAL_NAMES}
    raw: dict[str, float] = {}
    for name, values in scores.items():
        correlation = pearson(values, targets)
        if correlation is None:
            continue
        raw[name] = max(0.0, correlation)
    total = sum(raw.values())
    if total <= 0:
        return {name: 0.2 for name in CLARITY_SIGNAL_NAMES}
    return {name: value / total for name, value in raw.items()}


def analyse(
    items: Sequence[dict[str, Any]],
    *,
    annotator_a: str,
    annotator_b: str | None = None,
) -> dict[str, Any]:
    """Compute agreement and exploratory diagnostics for annotated fragments."""
    if not items:
        raise ValueError("нет размеченных фрагментов для анализа")
    pairs = [
        (item["a"].measurable, item["b"].measurable)
        for item in items
        if "b" in item
    ]
    ratings = [float(item["a"].rating) for item in items]
    current = [float(item["clarity_score"]) for item in items]

    categories: list[dict[str, Any]] = []
    for name in CLARITY_SIGNAL_NAMES:
        present = [item for item in items if name in item["signals"]]
        absent = [item for item in items if name not in item["signals"]]
        categories.append(
            {
                "name": name,
                "present": len(present),
                "measurable_present": (
                    sum(1 for item in present if item["a"].measurable) / len(present)
                    if present
                    else None
                ),
                "measurable_absent": (
                    sum(1 for item in absent if item["a"].measurable) / len(absent)
                    if absent
                    else None
                ),
                "human_present": sum(
                    1 for item in items if name in item["a"].signals
                ),
            }
        )

    threshold_pairs = [
        (item["clarity_score"] >= 0.5, item["a"].measurable) for item in items
    ]
    predicted_yes = sum(1 for predicted, _ in threshold_pairs if predicted)
    true_positives = sum(
        1 for predicted, human in threshold_pairs if predicted and human
    )
    actual_yes = sum(1 for _, human in threshold_pairs if human)

    train = [item for item in items if is_train(item["id"])]
    holdout = [item for item in items if not is_train(item["id"])]
    derived = derive_weights(train, "a")
    default_weights = {name: 0.2 for name in CLARITY_SIGNAL_NAMES}

    def correlation(subset: Sequence[dict[str, Any]], weights: dict[str, float]):
        values = [score_signals(item["signals"], weights) for item in subset]
        targets = [float(item["a"].rating) for item in subset]
        return pearson(values, targets)

    return {
        "total": len(items),
        "annotator_a": annotator_a,
        "annotator_b": annotator_b,
        "kappa": cohen_kappa(pairs),
        "weighted_kappa": weighted_kappa(
            [(item["a"].rating, item["b"].rating) for item in items if "b" in item]
        ),
        "agreement": (
            sum(1 for a, b in pairs if a == b) / len(pairs) if pairs else None
        ),
        "categories": categories,
        "current_correlation": pearson(current, ratings),
        "precision": (
            true_positives / predicted_yes if predicted_yes else None
        ),
        "recall": (
            true_positives / actual_yes if actual_yes else None
        ),
        "train_size": len(train),
        "holdout_size": len(holdout),
        "derived_weights": derived,
        "default_train": correlation(train, default_weights),
        "derived_train": correlation(train, derived),
        "default_holdout": correlation(holdout, default_weights),
        "derived_holdout": correlation(holdout, derived),
    }


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def render_report(analysis: dict[str, Any]) -> str:
    """Render the analysis as a Russian Markdown report."""
    lines = [
        "# Предварительный анализ clarity",
        "",
        "Инструмент подготовлен до появления размеченных данных; формула "
        "`clarity` в проекте не изменена.",
        "",
        "## Сводка",
        "",
        "| Параметр | Значение |",
        "|---|---:|",
        f"| Фрагментов | {analysis['total']} |",
        f"| Разметчик A | {analysis['annotator_a']} |",
        f"| Разметчик B | {analysis['annotator_b'] or '—'} |",
        f"| Cohen's kappa (measurable) | {_fmt(analysis['kappa'])} |",
        f"| Взвешенный kappa (rating 1–5) | {_fmt(analysis['weighted_kappa'])} |",
        f"| Совпадение бинарной оценки | {_fmt(analysis['agreement'])} |",
        f"| Корреляция текущей метрики с оценкой | {_fmt(analysis['current_correlation'])} |",
        f"| Precision при пороге 0.5 | {_fmt(analysis['precision'])} |",
        f"| Recall при пороге 0.5 | {_fmt(analysis['recall'])} |",
        "",
        "Kappa ниже 0.6 означает, что определение измеримости нужно уточнить "
        "в гайде до подбора весов.",
        "",
        "## Категории",
        "",
        "| Категория | В тексте | Измеримых при наличии | Измеримых без неё | "
        "Отмечено разметчиком |",
        "|---|---:|---:|---:|---:|",
    ]
    for category in analysis["categories"]:
        lines.append(
            f"| {category['name']} | {category['present']} | "
            f"{_fmt(category['measurable_present'])} | "
            f"{_fmt(category['measurable_absent'])} | {category['human_present']} |"
        )
    lines += [
        "",
        "## Train / holdout",
        "",
        f"Разделение детерминировано по `id`: train — {analysis['train_size']}, "
        f"holdout — {analysis['holdout_size']}.",
        "",
        "| Веса | Корреляция на train | Корреляция на holdout |",
        "|---|---:|---:|",
        f"| равные 0.2 | {_fmt(analysis['default_train'])} | {_fmt(analysis['default_holdout'])} |",
        f"| выведенные на train | {_fmt(analysis['derived_train'])} | {_fmt(analysis['derived_holdout'])} |",
        "",
        "Выведенные веса: "
        + ", ".join(
            f"{name}={value:.3f}"
            for name, value in analysis["derived_weights"].items()
        ),
        "",
        "## Ограничения",
        "",
        "- Это разведочный анализ, а не основание для немедленной смены метрики.",
        "- Веса выводятся только на train; holdout используется для проверки.",
        "- Нужны как минимум два разметчика и приемлемое согласие между ними.",
        "- При нулевой дисперсии оценок или категорий корреляция печатает `n/a`.",
        "",
    ]
    return "\n".join(lines)


def join_annotations(
    records: Sequence[dict[str, Any]],
    first: dict[str, Annotation],
    second: dict[str, Annotation] | None = None,
) -> list[dict[str, Any]]:
    """Attach annotations to exported fragments by id."""
    joined: list[dict[str, Any]] = []
    for record in records:
        identifier = record["id"]
        if identifier not in first:
            continue
        item = dict(record)
        item["a"] = first[identifier]
        if second is not None:
            if identifier not in second:
                continue
            item["b"] = second[identifier]
        joined.append(item)
    return joined


WORKSHEET_HEADER = re.compile(
    r"^##\s+\d+\.\s+`(?P<id>[0-9a-f]{12})`\s+—\s+"
    r"(?P<scenario>.+?)\s+\((?:run\s+(?P<run>\d+)|(?P<rendered>rendered))\)$"
)
WORKSHEET_CHECK = re.compile(
    r"^\s*-\s+\[([^\]]*)\]\s+(measurable|signal|rating):\s*(.+)$"
)
WORKSHEET_SIGNALS = (
    "modality",
    "expectation",
    "verification",
    "prohibition",
    "result",
)


def _normalise_fragment(text: str) -> str:
    """Collapse whitespace for duplicate detection in a worksheet."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _worksheet_blocks(text: str) -> list[dict[str, Any]]:
    """Split a worksheet into blocks and collect checkbox decisions."""
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in text.splitlines():
        header = WORKSHEET_HEADER.match(line)
        if header:
            run = header.group("run")
            current = {
                "id": header.group("id"),
                "scenario": header.group("scenario").strip(),
                "run_index": int(run) if run else None,
                "text_lines": [],
                "measurable": None,
                "signals": [],
                "rating": None,
            }
            blocks.append(current)
            continue
        if current is None:
            continue
        if line.startswith("> "):
            current["text_lines"].append(line[2:])
            continue
        if line == ">":
            current["text_lines"].append("")
            continue
        checkbox = WORKSHEET_CHECK.match(line)
        if not checkbox or "x" not in checkbox.group(1).lower():
            continue
        kind, value = checkbox.group(2), checkbox.group(3).strip()
        if kind == "measurable":
            if value not in ("true", "false"):
                raise ValueError(f"{current['id']}: неверный measurable {value!r}")
            parsed = value == "true"
            if current["measurable"] is not None and current["measurable"] != parsed:
                raise ValueError(f"{current['id']}: два разных measurable")
            current["measurable"] = parsed
        elif kind == "signal":
            if value not in WORKSHEET_SIGNALS:
                raise ValueError(f"{current['id']}: неизвестный сигнал {value!r}")
            if value not in current["signals"]:
                current["signals"].append(value)
        elif kind == "rating":
            if not value.isdigit() or not 1 <= int(value) <= 5:
                raise ValueError(f"{current['id']}: неверный rating {value!r}")
            rating = int(value)
            if current["rating"] is not None and current["rating"] != rating:
                raise ValueError(f"{current['id']}: два разных rating")
            current["rating"] = rating

    for block in blocks:
        block["text"] = "\n".join(block.pop("text_lines")) + "\n"
    return blocks


def worksheet_stats(text: str) -> dict[str, int]:
    """Count blocks, completed annotations and exact text duplicates."""
    blocks = _worksheet_blocks(text)
    filled = [
        block
        for block in blocks
        if block["measurable"] is not None and block["rating"] is not None
    ]
    keys = [_normalise_fragment(block["text"]) for block in filled]
    return {
        "blocks": len(blocks),
        "filled": len(filled),
        "unfilled": len(blocks) - len(filled),
        "duplicates": len(keys) - len(set(keys)),
        "unique": len(set(keys)),
    }


def parse_worksheet(
    text: str, annotator: str, *, dedupe: bool = True
) -> list[dict[str, Any]]:
    """Read a worksheet, skipping unfilled blocks and optional duplicates."""
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in _worksheet_blocks(text):
        if block["measurable"] is None or block["rating"] is None:
            continue
        key = _normalise_fragment(block["text"])
        if dedupe:
            if key in seen:
                continue
            seen.add(key)
        record = {
            "id": block["id"],
            "scenario": block["scenario"],
            "run_index": block["run_index"],
            "text": block["text"],
            "annotation": {
                "annotator": annotator,
                "measurable": block["measurable"],
                "signals": block["signals"],
                "rating": block["rating"],
            },
        }
        parse_annotation(record["annotation"], source=f"worksheet ({record['id']})")
        records.append(record)
    if not records and not worksheet_stats(text)["blocks"]:
        raise ValueError("в рабочем листе нет фрагментов")
    return records


def render_worksheet(
    records: Sequence[dict[str, Any]], annotator: str
) -> str:
    """Render fragments as a human-readable Markdown worksheet."""
    lines = [
        "# Разметка clarity — рабочий лист",
        "",
        f"Разметчик: **{annotator}**.",
        "",
        "Для каждого фрагмента отметьте один `measurable`, нужные `signal` и "
        "один `rating`. Не меняйте ID и текст. Подробности — в гайде "
        "`docs/clarity-annotation.md`.",
    ]
    for index, record in enumerate(records, start=1):
        run = record.get("run_index")
        run_label = f"run {run}" if run is not None else "rendered"
        lines += [
            "",
            f"## {index}. `{record['id']}` — "
            f"{record.get('scenario', '')} ({run_label})",
            "",
        ]
        for text_line in record["text"].rstrip("\n").splitlines():
            lines.append(f"> {text_line}" if text_line else ">")
        lines += [
            "",
            "- [ ] measurable: true",
            "- [ ] measurable: false",
        ]
        lines += [f"- [ ] signal: {name}" for name in WORKSHEET_SIGNALS]
        lines += [f"- [ ] rating: {value}" for value in range(1, 6)]
    return "\n".join(lines) + "\n"


    for record in records:
        parse_annotation(record["annotation"], source=f"worksheet ({record['id']})")
    return records


def annotations_from_sample(
    records: Sequence[dict[str, Any]],
) -> dict[str, Annotation]:
    """Use annotations embedded in a sample file as a single annotator."""
    result: dict[str, Annotation] = {}
    for record in records:
        raw = record.get("annotation")
        if raw is None:
            continue
        result[record["id"]] = parse_annotation(
            raw, source=f"sample ({record['id']})"
        )
    return result
