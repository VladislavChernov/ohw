#!/usr/bin/env python3
"""Generate graduation project reports in required formats."""

import json
import os
from datetime import datetime
from typing import Dict, Any


def generate_markdown_report(pipeline_data: Dict, output_path: str) -> str:
    """Generate Markdown report in the required graduation project format."""
    scenarios = pipeline_data.get("scenarios", [])
    metrics = pipeline_data.get("averages", {})
    
    total = len(scenarios)
    
    successful = 0
    unsuccessful = 0
    for s in scenarios:
        overall = s.get("quality_metrics", {}).get("overall", 0)
        if overall > 0.5:
            successful += 1
        else:
            unsuccessful += 1
    
    readability_avg = metrics.get("readability", 0) * 100
    clarity_avg = metrics.get("clarity_goals", 0) * 100
    coverage_avg = metrics.get("scenario_coverage", 0) * 100
    consistency_avg = metrics.get("consistency", 0) * 100
    
    md = f"""# Отчёт о тестировании сценариев

## Общая информация
- Количество сценариев: {total}
- Успешные: {successful}
- Неуспешные: {unsuccessful}

## Распределение качества
- Readability: {readability_avg:.1f}% (средняя читаемость)
- Clarity of Goals: {clarity_avg:.1f}% (чёткость целей)
- Scenario Coverage: {coverage_avg:.1f}% (покрытие сценариями)
- Consistency: {consistency_avg:.1f}% (консистентность)

## Список сценариев
| Описание | Качество | Оценка |
"""
    
    for s in scenarios:
        description = s.get("filename", "unknown")[:50]
        quality = s.get("quality_metrics", {})
        overall = quality.get("overall", 0)
        assessment = "✓ Успешный" if overall > 0.5 else "✗ Требует доработки"
        desc_short = description if len(description) <= 50 else description[:47] + "..."
        quality_str = f"readability={quality.get('readability', 0):.2f}, clarity={quality.get('clarity_goals', 0):.2f}"
        md += f"| {desc_short} | {quality_str} | {assessment} |\n"
    
    md += f"""

## Визуальный отчёт
- Boxplot распределения метрик: `charts/metrics_boxplot.png`
- Radar-chart качеств генерации: `charts/radar_chart.png`
- Оценка по сценариям: `charts/per_scenario_scores.png`
- Интерактивная визуализация: доступна в Streamlit workbench `dz2-ui-1`

## Источник данных
- Сгенерировано с использованием Ollama и шаблонов DZ7
- Нормализация требований через детерминированный нормализатор DZ2
- Метрики оценки NLP-моделью: читаемость, цели, покрытие, консистентность
- Дата прогона: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md)
    
    return output_path


def generate_json_report(pipeline_data: Dict, output_path: str) -> str:
    """Generate JSON report (source of truth)."""
    scenarios = pipeline_data.get("scenarios", [])
    
    report = {
        "project": "graduation-project",
        "generated": pipeline_data.get("generated", datetime.now().isoformat()),
        "input_dir": pipeline_data.get("input_dir", ""),
        "artifact_type": pipeline_data.get("artifact_type", "testcases"),
        "total_scenarios": len(scenarios),
        "scenarios": [],
        "averages": pipeline_data.get("averages", {}),
    }
    
    for i, s in enumerate(scenarios):
        scenario_entry = {
            "id": i + 1,
            "filename": s.get("filename", "unknown"),
            "artifact_type": s.get("artifact_type", "testcases"),
            "quality": s.get("quality_metrics", {}),
            "cases_with_requirements": s.get("cases_with_requirements", []),
            "prompt_sha256": s.get("prompt_sha256", ""),
        }
        cases = s.get("cases", [])
        if cases:
            scenario_entry["case_summaries"] = [
                {
                    "id": f"TC-{j+1}",
                    "title": "",
                    "description": "",
                    "requirement": "",
                    "type": "unknown",
                }
                for j in range(len(cases))
            ]
        report["scenarios"].append(scenario_entry)
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    return output_path


def generate_all_reports(pipeline_data: Dict, output_dir: str) -> Dict:
    """Generate all reports (JSON + Markdown + charts)."""
    from src.visualization.charts import create_all_charts
    charts_dir = os.path.join(output_dir, "charts")
    create_all_charts(pipeline_data, charts_dir)
    
    md_path = os.path.join(output_dir, "report.md")
    md_file = generate_markdown_report(pipeline_data, md_path)
    
    json_path = os.path.join(output_dir, "quality-metrics.json")
    json_file = generate_json_report(pipeline_data, json_path)
    
    return {"markdown": md_file, "json": json_file, "charts_dir": charts_dir}