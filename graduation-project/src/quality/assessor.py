"""NLP quality metrics for generated test documentation.

All functions are deterministic and work on plain text. A metric returns
``None`` when it cannot be measured; that value is never replaced by a fake
zero. The formulas and their limits are documented in ``docs/metrics.md``.
"""

import re
import statistics
from collections.abc import Iterable, Mapping, Sequence

from src.config import DEFAULT_QUALITY_WEIGHTS
from src.models import METRIC_KEYS, QualityMetrics

# Default overall weights. Metrics that are not measurable are dropped and the
# remaining weights are renormalised.
WEIGHTS: dict[str, float] = dict(DEFAULT_QUALITY_WEIGHTS)

OPTIMAL_WORDS_PER_SENTENCE = 17.0
READABILITY_TOLERANCE = 30.0

_BR_PATTERN = re.compile(r"\bBR-\d+\b", re.IGNORECASE)
_WORD_PATTERN = re.compile(r"[\w\-]+", re.UNICODE)
_SENTENCE_SPLIT = re.compile(r"[.!?…]+")
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_TABLE_SEPARATOR = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|[\s:|-]*$", re.MULTILINE)
_MARKDOWN_NOISE = re.compile(r"[#>*_`|]+")
_WHITESPACE = re.compile(r"\s+")

# Five independent signals of a measurable test objective. Each category adds
# 0.2, so a text that uses all five scores 1.0.
_CLARITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(должен|должна|должно|должны|must|shall|should)\b", re.IGNORECASE),
    re.compile(r"\b(ожидаем\w*|expected)\b", re.IGNORECASE),
    re.compile(r"\b(провер\w*|убеди\w*|верифиц\w*|verify)\b", re.IGNORECASE),
    re.compile(
        r"\b(не\s+(должен|должна|должно|должны)|нельзя|must\s+not)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(результат\w*|поведени\w*|сообщени\w*|result|behavior|output)\b",
        re.IGNORECASE,
    ),
)


def strip_markdown(text: str) -> str:
    """Drop code fences, table rules and markup so metrics see plain words."""
    without_code = _CODE_FENCE.sub(" ", text)
    lines = [
        line
        for line in without_code.splitlines()
        if not _TABLE_SEPARATOR.match(line)
    ]
    plain = _MARKDOWN_NOISE.sub(" ", "\n".join(lines))
    return _WHITESPACE.sub(" ", plain).strip()


def assess_readability(text: str) -> float:
    """Score average sentence length, optimal around 17 words.

    ``1.0`` means the average is exactly optimal, the score falls linearly to
    ``0.0`` when the average moves 30 words away in either direction.
    """
    plain = strip_markdown(text)
    sentences = [part.strip() for part in _SENTENCE_SPLIT.split(plain) if part.strip()]
    if not sentences:
        return 0.0
    total_words = sum(len(_WORD_PATTERN.findall(sentence)) for sentence in sentences)
    if total_words == 0:
        return 0.0
    average = total_words / len(sentences)
    deviation = abs(average - OPTIMAL_WORDS_PER_SENTENCE)
    return round(max(0.0, min(1.0, 1.0 - deviation / READABILITY_TOLERANCE)), 3)


def assess_clarity_goals(text: str) -> float:
    """Share of measurable-objective signal categories present in the text."""
    score = sum(0.2 for pattern in _CLARITY_PATTERNS if pattern.search(text))
    return round(min(1.0, score), 3)


def extract_requirement_ids(text: str) -> list[str]:
    """Return unique ``BR-N`` ids in numeric order."""
    found = {match.upper() for match in _BR_PATTERN.findall(text)}
    return sorted(found, key=lambda value: int(value.split("-")[1]))


def assess_scenario_coverage(
    generated_text: str, requirement_ids: Sequence[str]
) -> float | None:
    """Fraction of the input requirements referenced by the generated text.

    ``None`` means the question is not applicable: the input has no ``BR-N``
    ids, so coverage cannot be measured. An input with ids but no references
    in the output honestly scores ``0.0``.
    """
    expected = {value.upper() for value in requirement_ids}
    if not expected:
        return None
    found = set(extract_requirement_ids(generated_text))
    if not found:
        return 0.0
    return round(len(expected & found) / len(expected), 3)


def covered_requirement_ids(
    generated_text: str, requirement_ids: Sequence[str]
) -> list[str]:
    """Requirement ids from the input that the generated text references."""
    expected = {value.upper() for value in requirement_ids}
    return sorted(
        expected & set(extract_requirement_ids(generated_text)),
        key=lambda value: int(value.split("-")[1]),
    )


def _features(text: str) -> set[str]:
    """Lexical feature set used for Jaccard similarity between runs."""
    tokens = _WORD_PATTERN.findall(strip_markdown(text).lower())
    return {token for token in tokens if len(token) > 2}


def assess_consistency(texts: Sequence[str]) -> float | None:
    """Mean pairwise Jaccard similarity of repeated generations.

    ``None`` when fewer than two generations exist: a single sample says
    nothing about reproducibility.
    """
    if len(texts) < 2:
        return None
    features = [_features(text) for text in texts]
    similarities: list[float] = []
    for left in range(len(features)):
        for right in range(left + 1, len(features)):
            union = features[left] | features[right]
            if not union:
                similarities.append(1.0)
                continue
            similarities.append(len(features[left] & features[right]) / len(union))
    if not similarities:
        return None
    return round(statistics.mean(similarities), 3)


def assess_internal_consistency(text: str) -> float:
    """Heuristic used when only one generation is available.

    Sequential ``BR-N`` numbering without gaps scores ``0.8``; anything else
    gets the neutral ``0.5``. This is not reproducibility and the report says
    so explicitly.
    """
    ids = extract_requirement_ids(text)
    if len(ids) < 2:
        return 0.5
    numbers = sorted(int(value.split("-")[1]) for value in ids)
    if numbers == list(range(numbers[0], numbers[-1] + 1)):
        return 0.8
    return 0.5


def weighted_overall(
    values: dict[str, float | None],
    weights: Mapping[str, float] | None = None,
) -> float:
    """Weighted mean over known metrics; weights are renormalised."""
    effective = WEIGHTS if weights is None else weights
    known = {
        key: value
        for key, value in values.items()
        if key in effective and value is not None
    }
    total_weight = sum(effective[key] for key in known)
    if total_weight == 0:
        return 0.0
    score = (
        sum(effective[key] * value for key, value in known.items()) / total_weight
    )
    return round(score, 3)


def assess_quality(
    rendered: str,
    requirement_ids: Sequence[str],
    generations: Sequence[str] | None = None,
    weights: Mapping[str, float] | None = None,
) -> QualityMetrics:
    """Compute all four metrics for one generated artifact."""
    readability = assess_readability(rendered)
    clarity = assess_clarity_goals(rendered)
    coverage = assess_scenario_coverage(rendered, requirement_ids)
    if generations is not None and len(generations) > 1:
        consistency = assess_consistency(generations)
    else:
        consistency = assess_internal_consistency(rendered)
    overall = weighted_overall(
        {
            "readability": readability,
            "clarity_goals": clarity,
            "scenario_coverage": coverage,
            "consistency": consistency,
        },
        weights,
    )
    return QualityMetrics(
        readability=readability,
        clarity_goals=clarity,
        scenario_coverage=coverage,
        consistency=consistency,
        overall=overall,
    )


def aggregate_metrics(metrics: Iterable[QualityMetrics]) -> dict[str, float | None]:
    """Average each metric over scenarios, keeping ``None`` for missing data."""
    collected = list(metrics)
    result: dict[str, float | None] = {}
    for key in (*METRIC_KEYS, "overall"):
        values = [
            value
            for value in (getattr(item, key) for item in collected)
            if value is not None
        ]
        result[key] = round(statistics.mean(values), 3) if values else None
    return result
