"""Reporting phase: JSON as the source of truth, Markdown as the presentation.

``quality-metrics.json`` is written first and contains the real generated test
cases, so every number in the Markdown report can be traced back to it. The
Markdown file is never parsed by the pipeline.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config import check_pipeline_version
from src.models import METRIC_KEYS, PipelineData, QualityMetrics, ScenarioResult

REPORT_JSON_NAME = "quality-metrics.json"
REPORT_MARKDOWN_NAME = "report.md"

METRIC_TITLES: dict[str, str] = {
    "readability": "Readability",
    "clarity_goals": "Clarity of goals",
    "scenario_coverage": "Scenario coverage",
    "consistency": "Consistency",
    "overall": "Overall",
}

METRIC_NOTES: dict[str, str] = {
    "readability": "средняя длина предложения, оптимум 17 слов",
    "clarity_goals": "5 категорий измеримых формулировок",
    "scenario_coverage": "доля BR-N из входа, упомянутых в результате",
    "consistency": "Jaccard повторных генераций",
    "overall": "взвешенное среднее известных метрик",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _fmt(value: float | None) -> str:
    """Render a metric; ``None`` is not measurable and is printed as ``n/a``."""
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def _fmt_percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1%}"


def _assessment(scenario: ScenarioResult) -> str:
    if scenario.error:
        return "ошибка"
    if scenario.quality.overall is None:
        return "n/a"
    return "успешный" if scenario.successful else "требует доработки"


def _json_path(output_dir: Path) -> Path:
    return output_dir / REPORT_JSON_NAME


def _markdown_path(output_dir: Path) -> Path:
    return output_dir / REPORT_MARKDOWN_NAME


def write_json_report(data: PipelineData, output_dir: Path) -> Path:
    """Write ``quality-metrics.json`` and return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _json_path(output_dir)
    payload = data.to_report_dict()
    payload["generated_at"] = payload.get("generated_at") or _now()
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_pipeline_data(output_dir: Path) -> PipelineData:
    """Read a previously written ``quality-metrics.json``."""
    path = _json_path(output_dir)
    if not path.is_file():
        raise FileNotFoundError(
            f"report data not found: {path}; run the generation and assessment "
            "phases first"
        )
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    check_pipeline_version(raw, path)
    return PipelineData.from_report_dict(raw)


def _metric_rows(data: PipelineData) -> list[str]:
    rows = [
        "| Метрика | Среднее | Доля | Комментарий |",
        "|---|---:|---:|---|",
    ]
    for key in (*METRIC_KEYS, "overall"):
        value = data.averages.get(key)
        rows.append(
            f"| {METRIC_TITLES[key]} | {_fmt(value)} | "
            f"{_fmt_percent(value)} | {METRIC_NOTES[key]} |"
        )
    return rows


def _scenario_rows(data: PipelineData) -> list[str]:
    rows = [
        "| № | Файл | Overall | Readability | Clarity | Coverage | "
        "Consistency | Оценка |",
        "|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for index, scenario in enumerate(data.scenarios, start=1):
        quality = scenario.quality
        rows.append(
            f"| {index} | {scenario.filename} | {_fmt(quality.overall)} | "
            f"{_fmt(quality.readability)} | {_fmt(quality.clarity_goals)} | "
            f"{_fmt(quality.scenario_coverage)} | {_fmt(quality.consistency)} | "
            f"{_assessment(scenario)} |"
        )
    return rows


def _case_rows(scenario: ScenarioResult) -> list[str]:
    if not scenario.cases:
        return []
    rows = [
        "| ID | Тип | Заголовок | Требование |",
        "|---|---|---|---|",
    ]
    for case in scenario.cases:
        requirement = case.requirement or "—"
        rows.append(
            f"| {case.id} | {case.type.value} | {case.title} | {requirement} |"
        )
    return rows


def _scenario_details(data: PipelineData) -> list[str]:
    lines: list[str] = ["## Детализация по сценариям", ""]
    if not data.scenarios:
        lines += ["Сценарии не сгенерированы.", ""]
        return lines

    for index, scenario in enumerate(data.scenarios, start=1):
        lines += [
            f"### {index}. {scenario.filename}",
            "",
            f"- Overall: **{_fmt(scenario.quality.overall)}**",
            f"- Readability: {_fmt(scenario.quality.readability)}",
            f"- Clarity of goals: {_fmt(scenario.quality.clarity_goals)}",
            f"- Scenario coverage: {_fmt(scenario.quality.scenario_coverage)}",
            f"- Consistency: {_fmt(scenario.quality.consistency)}",
            f"- Требования во входе: "
            f"{', '.join(scenario.requirement_ids) or '—'}",
            f"- Покрытые требования: "
            f"{', '.join(scenario.covered_requirements) or '—'}",
            f"- Тест-кейсов: {len(scenario.cases)}",
            f"- Попыток генерации: {scenario.attempts}",
            f"- Повторов для consistency: {scenario.runs}",
            f"- SHA-256 промпта: `{scenario.prompt_sha256 or '—'}`",
            f"- Результат: {scenario.output_file or '—'}",
            "",
        ]
        if scenario.error:
            lines += [f"> Ошибка генерации: {scenario.error}", ""]
        case_rows = _case_rows(scenario)
        if case_rows:
            lines += case_rows + [""]
    return lines


def _chart_lines(data: PipelineData) -> list[str]:
    if not data.charts:
        return ["Графики не созданы (метрики отсутствуют).", ""]
    titles = {
        "boxplot": "Boxplot распределения метрик",
        "radar": "Radar-chart средних метрик",
        "per_scenario": "Оценки по сценариям",
    }
    lines = [
        "| График | Файл |",
        "|---|---|",
    ]
    for key, path in data.charts.items():
        lines.append(f"| {titles.get(key, key)} | `{path}` |")
    lines.append("")
    for key, path in data.charts.items():
        lines += [f"![{titles.get(key, key)}]({path})", ""]
    return lines


def render_markdown(data: PipelineData) -> str:
    """Build the Markdown report from a :class:`PipelineData` instance."""
    generated_at = data.generated_at or _now()
    lines: list[str] = [
        "# Отчёт о качестве генерации тестовой документации",
        "",
        "## Общая информация",
        "",
        "| Параметр | Значение |",
        "|---|---|",
        f"| Дата прогона | {generated_at} |",
        f"| Входная папка | `{data.input_dir}` |",
        f"| Тип артефакта | `{data.artifact_type}` |",
        f"| Модель | `{data.model}` |",
        f"| Кейсов на файл | {data.count} |",
        f"| Повторов на сценарий | {data.runs} |",
        f"| Нормализация требований | {'да' if data.normalize else 'нет'} |",
        f"| Пресеты | {', '.join(data.presets) or '—'} |",
        f"| Всего сценариев | {len(data.scenarios)} |",
        f"| Успешные (overall ≥ 0.5) | {data.successful} |",
        f"| Неуспешные | {data.unsuccessful} |",
    ]
    lines += [
        "",
        "## Распределение качества",
        "",
    ]
    lines += _metric_rows(data)
    lines += [
        "",
        "> `n/a` означает, что метрику нельзя измерить (например, в требованиях "
        "нет идентификаторов `BR-N`), а не нулевое качество.",
        "",
        "## Список сценариев",
        "",
    ]
    lines += _scenario_rows(data)
    lines.append("")
    lines += _scenario_details(data)
    lines += ["## Визуальный отчёт", ""]
    lines += _chart_lines(data)
    if data.errors:
        lines += ["## Ошибки", ""]
        lines += [f"- {error}" for error in data.errors]
        lines.append("")
    lines += [
        "## Источник данных",
        "",
        f"- JSON (источник правды): `{REPORT_JSON_NAME}`",
        f"- Markdown (представление): `{REPORT_MARKDOWN_NAME}`",
        "- Сгенерированные артефакты: `scenarios/*.md`",
        "- Генерация: Ollama и вендоренные шаблоны промптов",
        "- Нормализация требований: детерминированный нормализатор проекта",
        "- Метрики: детерминированный анализ текста, без LLM-судьи",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_markdown_report(data: PipelineData, output_dir: Path) -> Path:
    """Write ``report.md`` and return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _markdown_path(output_dir)
    path.write_text(render_markdown(data), encoding="utf-8")
    return path


def generate_all_reports(data: PipelineData, output_dir: Path) -> dict[str, str]:
    """Write the JSON and Markdown reports and return their paths."""
    json_path = write_json_report(data, output_dir)
    markdown_path = write_markdown_report(data, output_dir)
    return {"json": str(json_path), "markdown": str(markdown_path)}


def empty_metrics() -> QualityMetrics:
    """Convenience for callers that need a blank metric object."""
    return QualityMetrics()
