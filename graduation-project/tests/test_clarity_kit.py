"""Tests for the clarity annotation toolkit."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.quality.assessor import assess_clarity_goals, clarity_signals
from src.quality.clarity_kit import (
    Annotation,
    analyse,
    cohen_kappa,
    collect_annotations,
    derive_weights,
    export_snapshot,
    fragment_id,
    is_train,
    parse_annotation,
    parse_worksheet,
    pearson,
    read_jsonl,
    render_report,
    render_worksheet,
    split_fragments,
    weighted_kappa,
    write_jsonl,
)

ROOT = Path(__file__).resolve().parents[1]
LIVE_RUN = ROOT / "examples" / "live-run" / "generation.json"


def test_split_by_headings() -> None:
    text = "# Первый\n\nBR-1. Система должна проверять форму.\n\n# Второй\n\nBR-2. Ожидаем результат."
    fragments = split_fragments(text)
    assert len(fragments) == 2
    assert fragments[0].startswith("# Первый")
    assert fragments[1].startswith("# Второй")


def test_split_by_paragraphs_when_no_headings() -> None:
    text = "BR-1. Система должна проверять форму.\n\nЕщё один абзац с ожидаемым результатом."
    assert len(split_fragments(text)) == 2


def test_fragment_id_is_stable() -> None:
    kwargs = {
        "source": "generation.json",
        "scenario": "req.md",
        "run_index": 1,
        "text": "BR-1. Система должна проверять форму.",
    }
    assert fragment_id(**kwargs) == fragment_id(**kwargs)


def test_export_live_run_snapshot() -> None:
    records = export_snapshot(LIVE_RUN)
    assert records
    first = records[0]
    assert first["annotation"] is None
    assert first["source_file"] == "generation.json"
    assert isinstance(first["signals"], list)
    assert set(first["signals"]) <= {
        "modality",
        "expectation",
        "verification",
        "prohibition",
        "result",
    }
    assert 0.0 <= first["clarity_score"] <= 1.0


def test_export_respects_min_words() -> None:
    assert export_snapshot(LIVE_RUN, min_words=10_000) == []


def test_signals_match_the_production_metric() -> None:
    text = "Система должна проверять форму. Ожидаем результат."
    signals = clarity_signals(text)
    assert "modality" in signals
    assert "expectation" in signals
    assert assess_clarity_goals(text) == round(0.2 * len(signals), 3)


def test_parse_annotation_rejects_bad_rating() -> None:
    with pytest.raises(ValueError, match="rating должен быть целым"):
        parse_annotation(
            {
                "annotator": "alice",
                "measurable": True,
                "signals": [],
                "rating": 6,
            },
            source="test",
        )


def test_jsonl_roundtrip_and_annotation_lookup(tmp_path: Path) -> None:
    path = tmp_path / "sample.jsonl"
    write_jsonl(
        path,
        [
            {
                "id": "abc",
                "annotation": {
                    "annotator": "alice",
                    "measurable": True,
                    "signals": ["modality"],
                    "rating": 4,
                },
            }
        ],
    )
    assert read_jsonl(path)[0]["id"] == "abc"
    assert collect_annotations(path)["abc"].annotator == "alice"


def test_cohen_kappa_known_values() -> None:
    assert cohen_kappa([(True, True), (False, False)]) == 1.0
    assert cohen_kappa([(True, False), (False, True)]) == -1.0
    assert cohen_kappa([]) is None


def test_weighted_kappa_known_values() -> None:
    assert weighted_kappa([(1, 1), (5, 5)]) == 1.0
    assert weighted_kappa([(1, 5), (5, 1)]) == -1.0
    assert weighted_kappa([]) is None
    assert weighted_kappa([(3, 3), (3, 3)]) == 1.0


def test_analyse_reports_weighted_kappa() -> None:
    items = [
        _item("a1", ["modality"], 5, True),
        _item("a2", ["modality"], 4, True),
        _item("a3", ["verification"], 3, False),
        _item("a4", [], 1, False),
    ]
    result = analyse(items, annotator_a="alice", annotator_b="bob")
    assert result["weighted_kappa"] == 1.0
    assert "Взвешенный kappa" in render_report(result)


def test_pearson_known_values() -> None:
    assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert pearson([1, 1, 1], [2, 3, 4]) is None


def _item(identifier: str, signals: list[str], rating: int, yes: bool) -> dict:
    return {
        "id": identifier,
        "signals": signals,
        "clarity_score": round(0.2 * len(signals), 3),
        "a": Annotation("alice", yes, tuple(signals), rating),
        "b": Annotation("bob", yes, tuple(signals), rating),
    }


def test_train_split_is_deterministic() -> None:
    assert is_train("abc123") is is_train("abc123")
    assert isinstance(is_train("another-id"), bool)


def test_analyse_produces_diagnostics() -> None:
    items = [
        _item("a1", ["modality", "result"], 5, True),
        _item("a2", ["modality"], 4, True),
        _item("a3", ["verification"], 3, False),
        _item("a4", [], 1, False),
        _item("a5", ["expectation"], 4, True),
        _item("a6", ["result"], 3, False),
    ]
    result = analyse(items, annotator_a="alice", annotator_b="bob")
    assert result["total"] == 6
    assert result["kappa"] == 1.0
    assert result["agreement"] == 1.0
    assert len(result["categories"]) == 5
    assert sum(result["derived_weights"].values()) == pytest.approx(1.0)
    assert result["train_size"] + result["holdout_size"] == 6
    report = render_report(result)
    assert "# Предварительный анализ clarity" in report
    assert "Cohen's kappa" in report
    assert "Train / holdout" in report


def test_derive_weights_falls_back_to_equal() -> None:
    items = [_item(str(index), [], 1, False) for index in range(3)]
    assert derive_weights(items, "a") == {
        "modality": 0.2,
        "expectation": 0.2,
        "verification": 0.2,
        "prohibition": 0.2,
        "result": 0.2,
    }


def test_export_and_analyze_scripts(tmp_path: Path) -> None:
    sample = tmp_path / "sample.jsonl"
    export = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "export_clarity_sample.py"),
            "--input",
            str(LIVE_RUN),
            "--output",
            str(sample),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert export.returncode == 0, export.stderr
    records = [json.loads(line) for line in sample.read_text(encoding="utf-8").splitlines()]
    assert records
    for record in records:
        record["annotation"] = {
            "annotator": "alice",
            "measurable": True,
            "signals": record["signals"],
            "rating": 4,
        }
    write_jsonl(sample, records)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "analyze_clarity.py"),
            "--sample",
            str(sample),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Корреляция текущей метрики" in result.stdout


def test_annotation_worksheet_roundtrip() -> None:
    records = [
        {
            "id": "a1b2c3d4e5f6",
            "text": "### TC-01\nBR-1. Система должна проверять форму.",
            "scenario": "req.md",
            "run_index": 1,
        },
        {
            "id": "f6e5d4c3b2a1",
            "text": "## Позитивные сценарии",
            "scenario": "req.md",
            "run_index": None,
        },
    ]
    worksheet = render_worksheet(records, "human-1")
    assert "Разметчик: **human-1**" in worksheet
    assert worksheet.count("- [ ] measurable: true") == 2

    first, second = worksheet.split("## 2.")
    first = (
        first.replace("- [ ] measurable: true", "- [x] measurable: true", 1)
        .replace("- [ ] signal: modality", "- [x] signal: modality", 1)
        .replace("- [ ] signal: result", "- [x] signal: result", 1)
        .replace("- [ ] rating: 4", "- [x] rating: 4", 1)
    )
    second = (
        second.replace("- [ ] measurable: false", "- [x] measurable: false", 1)
        .replace("- [ ] rating: 1", "- [x] rating: 1", 1)
    )
    parsed = parse_worksheet(first + "## 2." + second, "human-1")

    assert len(parsed) == 2
    assert parsed[0]["annotation"]["measurable"] is True
    assert parsed[0]["annotation"]["signals"] == ["modality", "result"]
    assert parsed[0]["annotation"]["rating"] == 4
    assert parsed[0]["text"] == records[0]["text"] + "\n"
    assert parsed[1]["annotation"]["measurable"] is False
    assert parsed[1]["run_index"] is None


def test_worksheet_requires_measurable_and_rating() -> None:
    worksheet = render_worksheet(
        [
            {
                "id": "a1b2c3d4e5f6",
                "text": "BR-1",
                "scenario": "req.md",
                "run_index": None,
            }
        ],
        "human-1",
    )
    with pytest.raises(ValueError, match="measurable"):
        parse_worksheet(worksheet, "human-1")
