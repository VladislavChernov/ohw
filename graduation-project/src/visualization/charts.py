#!/usr/bin/env python3
"""Generate matplotlib/seaborn quality assessment charts."""

import matplotlib.pyplot as plt
import seaborn as sns
import os
from typing import Dict, List, Any
import statistics

sns.set_style("whitegrid")
plt.rcParams.update({'font.size': 10})


def create_boxplot(scenarios_data: Dict, output_dir: str) -> Optional[str]:
    """Create boxplot of metrics distribution across scenarios."""
    scenarios = scenarios_data.get("scenarios", [])
    if not scenarios:
        return None
    
    readability = [s.get("quality_metrics", {}).get("readability", 0) for s in scenarios]
    clarity = [s.get("quality_metrics", {}).get("clarity_goals", 0) for s in scenarios]
    coverage = [s.get("quality_metrics", {}).get("scenario_coverage", 0) * 100 for s in scenarios]
    consistency = [s.get("quality_metrics", {}).get("consistency", 0.5) for s in scenarios]
    
    data = {"Readability": readability, "Clarity Goals": clarity, 
            "Coverage %": coverage, "Consistency": consistency}
    
    plt.figure(figsize=(10, 6))
    sns.boxplot(data=data)
    plt.title("Распределение метрик качества по сценариям")
    plt.ylabel("Score (0-1)")
    plt.xticks(rotation=45)
    plt.ylim(0, 1)
    plt.tight_layout()
    
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "metrics_boxplot.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    return path


def create_radar_chart(avg_metrics: Dict, output_dir: str) -> Optional[str]:
    """Create radar chart of average metrics."""
    categories = list(avg_metrics.keys())
    values = list(avg_metrics.values())
    values_closed = values + [values[0]]
    angles = [n * (2 * 3.14159 / len(categories)) for n in range(len(categories))]
    angles_closed = angles + [angles[0]]
    
    plt.figure(figsize=(6, 6), subplot_kw=dict(polar=True))
    plt.plot(angles_closed, values_closed, 'o-', linewidth=2)
    plt.fill(angles_closed, values_closed, alpha=0.25)
    plt.xticks(angles[:-1], categories)
    plt.ylim(0, 1)
    plt.title("Radar-chart качеств генерации")
    plt.tight_layout()
    
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "radar_chart.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    return path


def create_per_scenario_chart(scenarios_data: Dict, output_dir: str) -> Optional[str]:
    """Create bar chart showing metrics per scenario."""
    scenarios = scenarios_data.get("scenarios", [])
    if not scenarios:
        return None
    
    total = len(scenarios)
    readability = [s.get("quality_metrics", {}).get("readability", 0) for s in scenarios]
    clarity = [s.get("quality_metrics", {}).get("clarity_goals", 0) for s in scenarios]
    coverage = [s.get("quality_metrics", {}).get("scenario_coverage", 0) * 100 for s in scenarios]
    consistency = [s.get("quality_metrics", {}).get("consistency", 0.5) for s in scenarios]
    
    x = range(total)
    
    plt.figure(figsize=(12, 6))
    plt.bar(x, readability, label="Readability", alpha=0.6, color="#4c72b0")
    plt.bar(x, clarity, bottom=readability, label="Clarity Goals", alpha=0.6, color="#55a868")
    plt.bar(x, coverage, bottom=[r + c for r, c in zip(readability, clarity)], 
            label="Coverage %", alpha=0.6, color="#c44e52")
    plt.bar(x, consistency, bottom=[r + c + co for r, c, co in zip(readability, clarity, coverage)], 
            label="Consistency", alpha=0.6, color="#8172b3")
    
    plt.title("Метрики качества по каждому сценарию")
    plt.xlabel("Сценарий")
    plt.ylabel("Score")
    plt.xticks(x, [s.get("filename", f"TC-{i+1}") for i, s in enumerate(scenarios)], rotation=45)
    plt.legend()
    plt.tight_layout()
    
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "per_scenario_scores.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    return path


def create_all_charts(pipeline_data: Dict, output_dir: str) -> Dict:
    """Create all charts for the graduation project."""
    charts_dir = os.path.join(output_dir, "charts")
    result = {}
    
    boxplot_path = create_boxplot(pipeline_data, charts_dir)
    if boxplot_path:
        result["boxplot"] = boxplot_path
    
    scenarios = pipeline_data.get("scenarios", [])
    if scenarios:
        avg_metrics = {}
        for key in ["readability", "clarity_goals", "scenario_coverage", "consistency"]:
            values = [s.get("quality_metrics", {}).get(key, 0) for s in scenarios]
            avg_metrics[key] = round(statistics.mean(values), 3) if values else 0.0
        
        radar_path = create_radar_chart(avg_metrics, charts_dir)
        if radar_path:
            result["radar"] = radar_path
        
        per_scenario_path = create_per_scenario_chart(pipeline_data, charts_dir)
        if per_scenario_path:
            result["per_scenario"] = per_scenario_path
    
    return result