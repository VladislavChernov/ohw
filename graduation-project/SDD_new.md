# Software Design Document (SDD)
# Graduation Project: Automated Test Documentation Generation with LLM

## Document Information

| Field | Value |
|-------|-------|
| **Document Title** | Software Design Document |
| **Project** | ai-testgen-graduation-project |
| **Version** | 0.1.0 |
| **Author** | Vladislav Chernov |
| **Date** | 09.11.2026 |
| **Status** | Draft |
| **Related Homeworks** | DZ2, DZ7, DZ8 |

## 1. Overview

### 1.1 Purpose
This document describes the software design for the graduation project that automates generation and evaluation of test documentation using LLM (Ollama). The project integrates existing homeworks (DZ2 - test case generator, DZ7 - prompt generator, DZ8 - quality lab) into a comprehensive system with full reporting pipeline.

### 1.2 Scope
- Generate 5 types of test artifacts from requirement files via Ollama LLM
- Assess quality using 4 NLP metrics: readability, clarity of goals, scenario coverage, consistency
- Produce JSON + Markdown reports in specified format
- Provide matplotlib/seaborn visualization charts
- Operate within existing Ohw infrastructure (no new containers)

### 1.3 Definitions and Abbreviations
- **BR-N** - Canonical requirement number format from DZ2 normalizer
- **TC-N** - Test case number format
- **LLM** - Large Language Model (via Ollama)
- **NLP** - Natural Language Processing metrics
- **EARS** - Requirements notation format (Used in OpenSpec)
- **SHALL** - Mandatory requirement in EARS
- **WHEN/THEN** - Scenario steps in EARS

### 1.4 References
- DZ2 Repository: `d:/Otus/ohw/dz2/`
- DZ7 Repository: `d:/Otus/ohw/dz7/`
- DZ8 Repository: `d:/Otus/ohw/dz8/`
- OpenSpec Conventions: `d:/Otus/ohw/openspec/`
- Ollama Documentation: https://github.com/ollama/ollama
- Ohw Infrastructure: `d:/Otus/ohw/infra/`

---

## 2. Architecture

### 2.1 High-Level Architecture

```mermaid
graph TD
    A[CLI Input] --> B[Workflow Engine]
    B --> C[Generator Module]
    B --> D[Quality Assessment Module]
    B --> E[Visualization Module]
    B --> F[Report Generation Module]
    
    C --> G[Ollama LLM]
    D --> G
    D --> H[DZ2 Normalizer]
    D --> I[NLP Metrics Engine]
    
    E --> J[Matplotlib Charts]
    E --> K[Seaborn Charts]
    
    F --> L[JSON Report (Source of Truth)]
    F --> M[Markdown Report (Formatted)]
    F --> N[Charts PNG Files]
    
    style A fill:#e1f5fe,stroke:#01579b
    style B fill:#e8f5e9,stroke:#2e7d32
    style C fill:#fff3e0,stroke:#ef6c00
    style D fill:#f3e5f5,stroke:#7b1fa2
    style E fill:#e0f7fa,stroke:#006064
    style F fill:#fce4ec,stroke:#c2185b
```

### 2.2 Component Overview

| Component | Responsibility | Dependencies |
|-----------|---------------|------------|
| **Workflow Engine** | Orchestrates generation → assessment → visualization → reporting | CLI args, config |
| **Generator Module** | Generates 5 artifact types from requirement files via Ollama | DZ2 service, DZ7 templates, Ollama |
| **Quality Assessment** | Calculates 4 NLP metrics on generated artifacts | Generated text, requirement BR-N numbers |
| **Visualization** | Creates matplotlib/seaborn charts from metrics | Metrics data |
| **Report Generation** | Produces JSON + Markdown reports in required format | All previous modules, OpenSpec docs |

### 2.3 Data Flow

```
1. CLI Input → Workflow Engine
2. Workflow → Generator: input-dir, --type, --count, --url, --normalize
3. Generator → Ollama LLM: built prompt from external template
4. Ollama → Generator: raw response JSON/text
5. Generator → Validation: ast.parse, schema validation
6. Generator → Quality Assessment: extracted text + BR-N numbers
7. Quality Assessment → NLP Metrics: readability, clarity, coverage, consistency
8. Quality Assessment → Visualization: metrics data
9. Visualization → Charts: matplotlib/seaborn PNG files
10. All modules → Report Generation: aggregated data
11. Report Generation → JSON Report: structured data (source of truth)
12. Report Generation → Markdown Report: formatted text
13. Report Generation → Charts: embedded/referenced PNG files
```

---

## 3. Module Specifications

### 3.1 Workflow Engine (`src/workflow/`)

#### 3.1.1 Main Entry Point
**File**: `src/workflow/__init__.py`, `src/workflow/main.py`

```python
#!/usr/bin/env python3
"""Main workflow engine for graduation project."""

import argparse
import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(
        description="Graduation project: Test documentation generation and evaluation"
    )
    parser.add_argument(
        "--generate", action="store_true",
        help="Generate test artifacts from requirement files"
    )
    parser.add_argument(
        "--assess", action="store_true",
        help="Assess quality of generated artifacts"
    )
    parser.add_argument(
        "--visualize", action="store_true",
        help="Create charts from quality metrics"
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Generate JSON + Markdown reports"
    )
    parser.add_argument(
        "--input-dir", default="../dz2/docs",
        help="Input directory with requirement files (default: ../dz2/docs)"
    )
    parser.add_argument(
        "--type", default="testcases",
        choices=["testcases", "checklist", "testplan", "qareport", "coveragematrix"],
        help="Artifact type to generate (default: testcases)"
    )
    parser.add_argument(
        "--count", type=int, default=10,
        help="Number of artifacts per file (default: 10)"
    )
    parser.add_argument(
        "--url", default=None,
        help="URL of test site (overrides file content)"
    )
    parser.add_argument(
        "--normalize", action="store_true",
        help="Normalize requirements using DZ2 normalizer"
    )
    parser.add_argument(
        "--preset", action="append", default=[],
        help="Preset ID from presets directory (can be specified multiple times)"
    )
    parser.add_argument(
        "--output-dir", default="output",
        help="Base output directory for all artifacts"
    )
    
    args = parser.parse_args()
    
    # Initialize data structure for pipeline
    pipeline_data = {
        "scenarios": [],
        "metrics": {},
        "generated": None,
        "input_dir": args.input_dir,
        "artifact_type": args.type,
        "count": args.count,
    }
    
    # Phase 1: Generation
    if args.generate or (not any([args.generate, args.assess, args.visualize, args.report])):
        from src.generator.service import generate_all_artifacts
        generate_all_artifacts(pipeline_data, args)
    
    # Phase 2: Quality Assessment
    if args.assess or (not any([args.generate, args.assess, args.visualize, args.report])):
        from src.quality.assessor.assess_scenarios
        assess_scenarios(pipeline_data)
    
    # Phase 3: Visualization
    if args.visualize or (not any([args.generate, args.assess, args.visualize, args.report])):
        from src.visualization.charts import create_all_charts
        create_all_charts(pipeline_data, args.output_dir)
    
    # Phase 4: Report Generation
    if args.report or (not any([args.generate, args.assess, args.visualize, args.report])):
        from src.reporting.report_generator import generate_all_reports
        generate_all_reports(pipeline_data, args.output_dir)
    
    print("Workflow complete.")

if __name__ == "__main__":
    main()
```

#### 3.1.2 Argument Parsing
- All CLI arguments match DZ2 existing CLI where possible
- New arguments: `--assess`, `--visualize`, `--report` (graduation project specific)
- Defaults consistent with DZ2/DZ7 configuration

---

### 3.2 Generator Module (`src/generator/`)

#### 3.2.1 Service Wrapper
**File**: `src/generator/service.py`

Extends DZ2 `ai_testgen.service.generate_artifact` to include quality metadata.

```python
from dataclasses import dataclass
from typing import Optional, List
from ai_testgen.service import GeneratedArtifact, GenerationResult, ArtifactType
from ai_testgen.ollama_client import OllamaClient
from ai_testgen.handlers import ArtifactHandler
from ai_testgen.input_doc import RequirementDoc

@dataclass(frozen=True)
class QualityMetrics:
    """NLP metrics for generated test documentation."""
    readability: float = 0.0  # 0.0-1.0, higher = more readable
    clarity_goals: float = 0.0  # 0.0-1.0, higher = clearer objectives
    scenario_coverage: float = 0.0  # 0.0-1.0, portion of BR-N covered
    consistency: float = 0.0  # 0.0-1.0, Jaccard index across generations
    overall: float = 0.0  # weighted average of above

@dataclass(frozen=True)
class EnhancedGeneratedArtifact:
    """Extended artifact with quality metrics."""
    artifact_type: ArtifactType
    result: GenerationResult
    user_prompt: str
    attempts: int
    quality_metrics: QualityMetrics = QualityMetrics()
    cases_with_requirements: List[str] = None  # Which BR-N each case covers

def generate_artifact_with_metrics(
    client: OllamaClient,
    handler: ArtifactHandler,
    doc: RequirementDoc,
    options: GenerationOptions,
    *,
    on_retry: Callable[[RetryNotice], None] = None,
    user_prompt: str | None = None,
) -> EnhancedGeneratedArtifact:
    """Generate artifact and assess its quality."""
    
    # Call original generation
    base = generate_artifact(
        client=client,
        handler=handler,
        doc=doc,
        options=options,
        on_retry=on_retry,
        user_prompt=user_prompt,
    )
    
    # Extract generated text for NLP analysis
    generated_text = ""
    if base.result.document is not None:
        generated_text = base.result.document
    elif base.result.cases is not None:
        # Build text from cases
        case_texts = [f"{c.id}: {c.title} - {c.description}" for c in base.result.cases]
        generated_text = " ".join(case_texts)
    
    # Assess quality metrics
    metrics = assess_quality_text(generated_text, doc)
    
    # Find which BR-N requirements are covered
    cases_with_reqs = []
    for case in base.result.cases or []:
        req_id = case.requirement.split('-')[-1] if case.requirement and '-' in case.requirement else None
        cases_with_reqs.append(req_id) if req_id else cases_with_reqs.append(None)
    
    return EnhancedGeneratedArtifact(
        artifact_type=base.artifact_type,
        result=base.result,
        user_prompt=base.user_prompt,
        attempts=base.attempts,
        quality_metrics=metrics,
        cases_with_requirements=cases_with_reqs,
    )

def assess_quality_text(generated_text: str, doc: RequirementDoc) -> QualityMetrics:
    """Assess quality of generated text using NLP metrics."""
    
    from .quality.assessor import assess_readability, assess_clarity_goals
    
    # Readability
    readability = assess_readability(generated_text)
    
    # Clarity of goals
    clarity_goals = assess_clarity_goals(generated_text)
    
    # Scenario coverage (portion of BR-N requirements covered)
    scenario_coverage = assess_scenario_coverage(
        generated_text, 
        doc.requirements if hasattr(doc, 'requirements') else []
    )
    
    # Consistency (would need multiple generations; use single-gen placeholder)
    consistency = 0.5  # Neutral score for single generation
    
    # Overall weighted average
    overall = (readability * 0.25 + clarity_goals * 0.25 + 
               scenario_coverage * 0.30 + consistency * 0.20)
    
    return QualityMetrics(
        readability=round(readability, 3),
        clarity_goals=round(clarity_goals, 3),
        scenario_coverage=round(scenario_coverage, 3),
        consistency=round(consistency, 3),
        overall=round(overall, 3),
    )

def generate_all_artifacts(pipeline_data: dict, args: argparse.Namespace) -> None:
    """Generate test artifacts for all files in input directory."""
    
    from ai_testgen.service import generate_artifact, GenerationOptions
    from ai_testgen.handlers import ArtifactHandler
    from ai_testgen.input_doc import RequirementDoc
    from ai_testgen.ollama_client import OllamaClient
    import os
    
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir) / "scenarios"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    artifact_type = args.type
    count = args.count
    url = args.url or ""
    normalize = args.normalize
    
    handler = ArtifactHandler.get_handler(artifact_type)
    
    # Process each file
    results = []
    for filepath in sorted(input_path.glob("*.md")) + sorted(input_path.glob("*.txt")):
        try:
            doc = RequirementDoc.from_file(filepath)
            
            if normalize:
                # Use DZ2 normalizer
                from ai_testgen.normalizer import normalize_doc
                doc = normalize_doc(doc)
            
            # Generate artifact
            client = OllamaClient(
                base_url=args.ollama_base_url or "http://localhost:11434",
                model=args.ollama_model or "qwen2.5:7b-instruct",
            )
            
            result = generate_artifact_with_metrics(
                client=client,
                handler=handler,
                doc=doc,
                options=GenerationOptions(count=count, temperature=0.7, max_retries=3),
            )
            
            # Save markdown output
            output_path = output_dir / f"{doc.stem}.md"
            render_func = handler.render if hasattr(handler, 'render') else None
            if render_func:
                markdown = render_func(
                    url=url if url else doc.url,
                    source_name=doc.stem,
                )
                output_path.write_text(markdown, encoding="utf-8")
            
            results.append({
                "filename": filepath.name,
                "artifact_type": artifact_type,
                "cases": result.result.cases,
                "quality_metrics": result.quality_metrics,
                "cases_with_requirements": result.cases_with_requirements,
                "prompt_sha256": hashlib.sha256(result.user_prompt.encode()).hexdigest(),
            })
            
        except Exception as e:
            print(f"Error processing {filepath.name}: {e}")
            results.append({
                "filename": filepath.name,
                "artifact_type": artifact_type,
                "error": str(e),
            })
    
    # Store in pipeline data
    pipeline_data["scenarios"] = results
    pipeline_data["generated"] = datetime.now().isoformat()
    
    print(f"Generated {len(results)} artifacts from {input_dir}")
```

---

### 3.3 Quality Assessment Module (`src/quality/`)

#### 3.3.1 Assessor
**File**: `src/quality/__init__.py`, `src/quality/assessor.py`

```python
#!/usr/bin/env python3
"""NLP quality assessment for generated test documentation."""

import re
import statistics
from typing import List, Optional
from datetime import datetime

def assess_readability(text: str) -> float:
    """
    Calculate readability score (0-1, higher=more readable).
    
    Uses average sentence length formula.
    Optimal ~15-20 words per sentence for technical text.
    """
    import nltk
    from nltk.tokenize import sent_tokenize, word_tokenize
    
    try:
        nltk.data.find('tokenizers/punkt')
    except LookupError:
        nltk.download('punkt', quiet=True)
    
    sentences = sent_tokenize(text)
    if not sentences or len(sentences) == 0:
        return 0.0
    
    # Calculate average words per sentence
    avg_words = statistics.mean([len(word_tokenize(s)) for s in sentences])
    
    # Score: 1.0 at optimal 17 words, decreases away from optimal
    optimal = 17.0
    deviation = abs(avg_words - optimal)
    score = max(0.0, min(1.0, 1.0 - deviation / 30.0))
    
    return round(score, 3)

def assess_clarity_goals(text: str) -> float:
    """
    Assess clarity of test goals (0-1, higher=clearer).
    
    Checks for measurable objective patterns:
    - "should error", "should success", "expected result"
    - "must not", "must be performed"
    - "verify that", "verify if"
    """
    patterns = [
        r'should .+ (error|success|condition|result|behavior)',
        r'expected .+ (result|output|value|behavior)',
        r'must .+ (not|be|performed)',
        r'verify .+ (that|if|the)',
        r'test .+ (pass|fail|valid|invalid)',
    ]
    
    score = 0.0
    max_patterns = len(patterns)
    
    for pattern in patterns:
        matches = len(re.findall(pattern, text, re.IGNORECASE))
        # Each pattern contributes max 0.2 to score
        score += min(0.2, matches * 0.2)
    
    # Normalize to 0-1 (divide by max possible)
    score = score / max_patterns if max_patterns > 0 else 0.0
    
    return round(min(1.0, score), 3)

def assess_scenario_coverage(
    generated_text: str, 
    requirement_ids: List[str]
) -> float:
    """
    Calculate percentage of requirements covered by generated test scenarios.
    
    Maps BR-N numbers from requirements to test case references.
    """
    if not requirement_ids:
        return 0.5  # Neutral score when no requirements
    
    # Extract BR-N IDs from generated text
    br_ids_found = set()
    br_pattern = r'BR-\d+'
    matches = re.findall(br_pattern, generated_text)
    for m in matches:
        br_ids_found.add(m)
    
    # Count how many requirements are referenced
    if not br_ids_found:
        return 0.0
    
    covered = len(br_ids_found.intersection(set(requirement_ids)))
    coverage = covered / len(requirement_ids) if requirement_ids else 0.0
    
    return round(min(1.0, coverage), 3)

def assess_consistency(
    generation_texts: List[str]
) -> float:
    """
    Assess consistency across multiple generations (0-1, higher=more consistent).
    
    Uses Jaccard similarity based on case titles/descriptions.
    """
    if len(generation_texts) < 2:
        return 1.0  # Single generation - perfectly consistent
    
    # Extract "key features" from each generation
    # Simple approach: use set of unique case descriptions
    featuresets = []
    for text in generation_texts:
        # Extract case IDs and titles pattern
        case_ids = re.findall(r'TC-\d+', text)
        case_titles = re.findall(r'-\s+\w+[\w\s]+\.\w+', text)  # simplified
        featuresets.append(set(case_ids + case_titles))
    
    # Calculate pairwise Jaccard similarities
    total_similarity = 0.0
    pair_count = 0
    
    for i in range(len(featuresets)):
        for j in range(i + 1, len(featuresets)):
            intersection = len(featuresets[i].intersection(featuresets[j]))
            union = len(featuresets[i].union(featuresets[j]))
            if union > 0:
                similarity = intersection / union
                total_similarity += similarity
                pair_count += 1
    
    if pair_count == 0:
        return 0.5  # Neutral
    
    avg_similarity = total_similarity / pair_count
    # Map similarity (0-1) to consistency (0-1)
    # Higher similarity = higher consistency
    consistency = round(min(1.0, avg_similarity), 3)
    
    return consistency

def assess_single_generation_consistency(
    generated_text: str,
    baseline_texts: List[str] = None
) -> float:
    """
    Assess consistency of single generation against baseline.
    
    If baseline provided, compare against baselines.
    If not, use internal consistency heuristics.
    """
    if baseline_texts:
        # Compare against baselines using Jaccard
        featureset = set(re.findall(r'TC-\d+|BR-\d+', generated_text))
        total_sim = 0.0
        for bl in baseline_texts:
            bl_features = set(re.findall(r'TC-\d+|BR-\d+', bl))
            if bl_features:
                intersection = len(featureset.intersection(bl_features))
                union = len(featureset.union(bl_features))
                if union > 0:
                    total_sim += intersection / union
        
        avg_sim = total_sim / len(baseline_texts) if baseline_texts else 0
        return round(min(1.0, avg_sim), 3)
    else:
        # Use internal consistency: check for repeated patterns,
        # stable terminology, etc.
        # Simple heuristic: look for consistent BR-N numbering
        br_numbers = re.findall(r'BR-\d+', generated_text)
        if len(br_numbers) >= 2:
            # Check if numbering is consistent (sequential, no gaps expected)
            nums = [int(m.split('-')[1]) for m in br_numbers]
            expected_range = range(min(nums), max(nums) + 1)
            gaps = set(expected_range) - set(nums)
            if not gaps:
                return 0.8  # Good consistency - sequential numbering
            else:
                return 0.5  # Partial consistency
        return 0.5  # Neutral

def assess_scenarios(scenarios_data: dict) -> dict:
    """
    Assess quality of all scenarios in pipeline data.
    
    Args:
        scenarios_data: Dict with scenarios from generator module
        
    Returns:
        Dict with aggregated metrics
    """
    scenarios = scenarios_data.get("scenarios", [])
    if not scenarios:
        return {"overall": 0.0, "detailed": []}
    
    detailed = []
    metrics_keys = ["readability", "clarity_goals", "scenario_coverage", "consistency", "overall"]
    
    for scenario in scenarios:
        quality = scenario.get("quality_metrics", {})
        detailed.append({
            "filename": scenario.get("filename", "unknown"),
            "artifact_type": scenario.get("artifact_type", "unknown"),
            "readability": quality.get("readability", 0.0),
            "clarity_goals": quality.get("clarity_goals", 0.0),
            "scenario_coverage": quality.get("scenario_coverage", 0.0),
            "consistency": quality.get("consistency", 0.5),
            "overall": quality.get("overall", 0.0),
        })
    
    # Calculate aggregated metrics
    if detailed:
        avg_metrics = {
            key: round(statistics.mean([d[key] for d in detailed]), 3) if key != "consistency" 
            else round(statistics.mean([d["consistency"] for d in detailed]), 3)
        for key in metrics_keys}
    else:
        avg_metrics = {key: 0.0 for key in metrics_keys}
    
    return {
        "overall": round(statistics.mean([d["overall"] for d in detailed]), 3) if detailed else 0.0,
        "detailed": detailed,
        "averages": avg_metrics,
        "count": len(detailed),
    }
```

---

### 3.4 Visualization Module (`src/visualization/`)

#### 3.5.1 Charts Generator
**File**: `src/visualization/charts.py`

```python
#!/usr/bin/env python3
"""Generate matplotlib/seaborn quality assessment charts."""

import matplotlib.pyplot as plt
import seaborn as sns
import os
from typing import Dict, List, Any, Optional
import statistics

# Set style
sns.set_style("whitegrid")
plt.rcParams.update({
    'font.size': 10,
    'axes.titlesize': 12,
    'axes.labelsize': 10,
    'figure.titlesize': 14,
})

def create_metrics_boxplot(
    scenarios_data: dict,
    output_dir: str
) -> Optional[str]:
    """
    Create boxplot of metrics distribution across scenarios.
    
    Args:
        scenarios_data: Data from pipeline with scenarios
        output_dir: Directory to save chart
        
    Returns:
        Path to saved file or None
    """
    scenarios = scenarios_data.get("scenarios", [])
    if not scenarios:
        return None
    
    # Extract metrics
    readability = [s.get("quality_metrics", {}).get("readability", 0) for s in scenarios]
    clarity = [s.get("quality_metrics", {}).get("clarity_goals", 0) for s in scenarios]
    coverage = [s.get("quality_metrics", {}).get("scenario_coverage", 0) * 100 for s in scenarios]  # %
    consistency = [s.get("quality_metrics", {}).get("consistency", 0.5) for s in scenarios]
    
    data = {
        "Readability": readability,
        "Clarity Goals": clarity,
        "Coverage %": coverage,
        "Consistency": consistency,
    }
    
    plt.figure(figsize=(10, 6))
    sns.boxplot(data=data)
    plt.title("Распределение метрик качества по сценариям")
    plt.ylabel("Score (0-1)")
    plt.xticks(rotation=45)
    plt.ylim(0, 1)
    plt.tight_layout()
    
    # Save
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "metrics_boxplot.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    
    return path

def create_radar_chart(
    avg_metrics: dict,
    output_dir: str
) -> Optional[str]:
    """
    Create radar/radar chart of average metrics.
    
    Args:
        avg_metrics: Dictionary of average metric values
        output_dir: Directory to save chart
        
    Returns:
        Path to saved file or None
    """
    categories = list(avg_metrics.keys())
    values = list(avg_metrics.values())
    
    # Close the loop for radar chart
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

def create_per_scenario_chart(
    scenarios_data: dict,
    output_dir: str
) -> Optional[str]:
    """
    Create bar chart showing metrics per scenario.
    
    Args:
        scenarios_data: Data from pipeline
        output_dir: Directory to save chart
        
    Returns:
        Path to saved file or None
    """
    scenarios = scenarios_data.get("scenarios", [])
    if not scenarios:
        return None
    
    total = len(scenarios)
    
    # Extract metrics
    readability = [s.get("quality_metrics", {}).get("readability", 0) for s in scenarios]
    clarity = [s.get("quality_metrics", {}).get("clarity_goals", 0) for s in scenarios]
    coverage = [s.get("quality_metrics", {}).get("scenario_coverage", 0) * 100 for s in scenarios]  # %
    consistency = [s.get("quality_metrics", {}).get("consistency", 0.5) for s in scenarios]
    
    x = range(total)
    
    plt.figure(figsize=(12, 6))
    
    # Readability baseline
    plt.bar(x, readability, label="Readability", alpha=0.6, color="#4c72b0")
    
    # Clarity on top
    plt.bar(x, clarity, bottom=readability, label="Clarity Goals", alpha=0.6, color="#55a868")
    
    # Coverage on top
    plt.bar(x, coverage, bottom=[r + c for r, c in zip(readability, clarity)], 
            label="Coverage %", alpha=0.6, color="#c44e52")
    
    # Consistency on top
    plt.bar(x, consistency, bottom=[r + c + co for r, c, co in zip(readability, clarity, coverage)], 
            label="Consistency", alpha=0.6, color="#8172b3")
    
    plt.title("Метрики качества по каждому сценарию")
    plt.xlabel("Сценарий")
    plt.ylabel("Score")
    plt.xticks(x, [s.get("filename", f"TC-{i+1}") for i, s in enumerate(scenarios)], rotation=45)
    plt.legend()
    plt.tight_layout()
    
    # Save
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "per_scenario_scores.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    
    return path

def create_all_charts(
    pipeline_data: dict,
    output_dir: str
) -> dict:
    """
    Create all charts for the graduation project.
    
    Args:
        pipeline_data: Data from all pipeline phases
        output_dir: Base output directory
        
    Returns:
        Dict of chart file paths
    """
    charts_dir = os.path.join(output_dir, "charts")
    
    result = {}
    
    # 1. Boxplot of metrics distribution
    boxplot_path = create_metrics_boxplot(pipeline_data, charts_dir)
    if boxplot_path:
        result["boxplot"] = boxplot_path
    
    # 2. Radar chart of averages
    scenarios = pipeline_data.get("scenarios", [])
    if scenarios:
        # Calculate averages
        avg_metrics = {}
        metrics_keys = ["readability", "clarity_goals", "scenario_coverage", "consistency"]
        for key in metrics_keys:
            values = [
                s.get("quality_metrics", {}).get(key, 0) for s in scenarios
            ]
            avg_metrics[key] = round(statistics.mean(values), 3) if values else 0.0
        
        radar_path = create_radar_chart(avg_metrics, charts_dir)
        if radar_path:
            result["radar"] = radar_path
        
        # 3. Per-scenario chart
        per_scenario_path = create_per_scenario_chart(pipeline_data, charts_dir)
        if per_scenario_path:
            result["per_scenario"] = per_scenario_path
    
    return result
```

---

### 3.5 Report Generation Module (`src/reporting/`)

#### 3.5.1 Report Generator
**File**: `src/reporting/report_generator.py`

```python
#!/usr/bin/env python3
"""Generate graduation project reports in required formats."""

import json
import os
from datetime import datetime
from typing import Dict, Any, Optional
import statistics

def generate_markdown_report(
    pipeline_data: dict,
    output_path: str
) -> str:
    """
    Generate Markdown report in the required graduation project format.
    
    Format specified in the assignment:
    # Отчёт о тестировании сценариев
    ## Общая информация
    - Количество сценариев: 10
    - Успешные: 8
    - Неуспешные: 2
    ## Распределение качества
    [visualization link]
    ## Список сценариев
    | Описание | Качество | Оценка |
    """
    
    scenarios = pipeline_data.get("scenarios", [])
    metrics = pipeline_data.get("averages", {})
    
    total = len(scenarios)
    
    # Calculate success counts
    successful = 0
    unsuccessful = 0
    for s in scenarios:
        overall = s.get("quality_metrics", {}).get("overall", 0)
        if overall > 0.5:
            successful += 1
        else:
            unsuccessful += 1
    
    # Quality distribution description
    readability_avg = metrics.get("readability", 0) * 100  # Convert to %
    clarity_avg = metrics.get("clarity_goals", 0) * 100
    coverage_avg = metrics.get("scenario_coverage", 0) * 100  # %
    consistency_avg = metrics.get("consistency", 0) * 100  # %
    
    # Generate markdown
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
        description = s.get("filename", "unknown")[:50]  # Truncate
        quality = s.get("quality_metrics", {})
        overall = quality.get("overall", 0)
        assessment = "✓ Успешный" if overall > 0.5 else "✗ Требует доработки"
        
        # Truncate description and format quality
        desc_short = description if len(description) <= 50 else description[:47] + "..."
        quality_str = f"readability={quality.get('readability', 0):.2f}, clarity={quality.get('clarity_goals', 0):.2f}"
        
        md += f"| {desc_short} | {quality_str} | {assessment} |\n"
    
    # Add visualization references
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
    
    # Save
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md)
    
    return output_path

def generate_json_report(
    pipeline_data: dict,
    output_path: str
) -> str:
    """
    Generate JSON report (source of truth).
    
    Contains all structured data for the project.
    """
    scenarios = pipeline_data.get("scenarios", [])
    
    # Build structured report
    report = {
        "project": "graduation-project",
        "generated": pipeline_data.get("generated", datetime.now().isoformat()),
        "input_dir": pipeline_data.get("input_dir", ""),
        "artifact_type": pipeline_data.get("artifact_type", "testcases"),
        "total_scenarios": len(scenarios),
        "scenarios": [],
        "averages": pipeline_data.get("averages",
    }
    
    # Add each scenario
    for i, s in enumerate(scenarios):
        scenario_entry = {
            "id": i + 1,
            "filename": s.get("filename", "unknown"),
            "artifact_type": s.get("artifact_type", "testcases"),
            "quality": s.get("quality_metrics", {}),
            "cases_with_requirements": s.get("cases_with_requirements", []),
            "prompt_sha256": s.get("prompt_sha256", ""),
        }
        # Add case summaries
        cases = s.get("cases", [])
        if cases:
            scenario_entry["case_summaries"] = [
                {
                    "id": c.id if hasattr(c, 'id') else f"TC-{j+1}",
                    "title": c.title if hasattr(c, 'title') else "",
                    "description": c.description[:80] if hasattr(c, 'description') and c.description else "",
                    "requirement": c.requirement if hasattr(c, 'requirement') else "",
                    "type": c.type.value if hasattr(c, 'type') else "unknown",
                }
                for j, c in enumerate(cases)
            ]
        report["scenarios"].append(scenario_entry)
    
    # Save
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    return output_path

def generate_all_reports(
    pipeline_data: dict,
    output_dir: str
) -> dict:
    """
    Generate all reports (JSON + Markdown + charts).
    
    Args:
        pipeline_data: Data from all pipeline phases
        output_dir: Base output directory
        
    Returns:
        Dict of generated file paths
    """
    charts_dir = os.path.join(output_dir, "charts")
    
    # 1. Generate charts first (if not already done)
    from src.visualization.charts import create_all_charts
    create_all_charts(pipeline_data, charts_dir)
    
    # 2. Generate Markdown report
    md_path = os.path.join(output_dir, "report.md")
    md_file = generate_markdown_report(pipeline_data, md_path)
    
    # 3. Generate JSON report
    json_path = os.path.join(output_dir, "quality-metrics.json")
    json_file = generate_json_report(pipeline_data, json_path)
    
    return {
        "markdown": md_file,
        "json": json_file,
        "charts_dir": charts_dir,
    }
```

---

### 3.6 OpenSpec Integration

#### 3.6.1 Project Specification
**File**: `openspec/project.md` - Already created (see previous response)

#### 3.6.2 Changes and Tasks
**Files**: 
- `openspec/changes/proposal.md` - Scope proposal
- `openspec/changes/design.md` - Architecture design  
- `openspec/changes/tasks.md` - Implementation tasks

#### 3.6.3 Specifications
**Files**:
- `openspec/specs/generation/spec.md` - Generation specification
- `openspec/specs/quality/spec.md` - Quality assessment specification

All OpenSpec files use EARS format: `SHALL` + `WHEN/THEN` scenarios.

---

### 3.7 Data Models Summary

| Component | Key Data Structure | Fields |
|-----------|-------------------|--------|
| **GeneratedArtifact** (DZ2) | `EnhancedGeneratedArtifact` | artifact_type, result, user_prompt, attempts, quality_metrics, cases_with_requirements |
| **QualityMetrics** | `QualityMetrics` | readability, clarity_goals, scenario_coverage, consistency, overall |
| **ScenarioData** | Dict | filename, artifact_type, quality_metrics, cases_with_requirements, prompt_sha256 |
| **PipelineData** | Dict | scenarios, metrics, averages, generated, input_dir, artifact_type |
| **JSON Report** | Nested dict | project, generated, total_scenarios, scenarios[], averages |
| **Markdown Report** | String | Structured text per assignment format |

---

## 4. Interface Specifications

### 4.1 CLI Interface

```
Usage: python -m graduation_project [OPTIONS]

Options:
  --generate               Generate test artifacts from requirement files
  --assess                 Assess quality of generated artifacts
  --visualize              Create charts from quality metrics
  --report                 Generate JSON + Markdown reports
  --input-dir DIR         Input directory with requirement files
  --type TYPE             Artifact type: testcases, checklist, testplan, qareport, coveragematrix
  --count N               Number of artifacts per file
  --url URL               URL of test site (overrides file content)
  --normalize              Normalize requirements using DZ2 normalizer
  --preset ID             Preset ID from presets directory
  --output-dir DIR        Base output directory for all artifacts
  --help                  Show this message and exit
```

### 4.2 Python Module Interfaces

#### `graduation_project.workflow.main(args: argparse.Namespace) -> None`
- Main entry point for the graduation project
- Orchestrates all pipeline phases based on CLI args

#### `graduation_project.generator.service.generate_artifact_with_metrics(...) -> EnhancedGeneratedArtifact`
- Generates test artifact and assesses quality
- Extends DZ2 service with NLP metrics

#### `graduation_project.quality.assessor.assess_scenarios(data: dict) -> dict`
- Assesses quality of all scenarios
- Returns aggregated metrics and detailed per-scenario results

#### `graduation_project.visualization.charts.create_all_charts(pipeline_data, output_dir) -> dict`
- Creates all visualization charts
- Returns dict of chart file paths

#### `graduation_project.reporting.report_generator.generate_all_reports(pipeline_data, output_dir) -> dict`
- Generates JSON + Markdown reports + charts
- Returns dict of generated file paths

---

## 5. Error Handling

### 5.1 Error Classes

```python
class GraduationProjectError(Exception):
    """Base error for graduation project."""
    pass

class OllamaConnectionError(GraduationProjectError):
    """Ollama service not accessible."""
    pass

class GenerationValidationError(GraduationProjectError):
    """Generated artifact failed validation."""
    pass

class QualityAssessmentError(GraduationProjectError):
    """Quality assessment failed."""
    pass

class OpenSpecViolationError(GraduationProjectError):
    """OpenSpec format violation."""
    pass
```

### 5.2 Error Handling Strategy

1. **Ollama Unavailable**: Catch connection errors, fallback to demo mode, mark artifacts with `# ЧЕРНОВИК` comment
2. **Validation Failed**: Retry with feedback (max_retries=3 from DZ2 config), then mark and continue
3. **Missing Requirements**: Neutral metrics (0.5 score), log warning, continue with available data
4. **OpenSpec Violation**: Log warning, continue generation, document in report
5. **Chart Generation Failure**: Fallback to text-only report, log error

### 5.3 Recovery Mechanisms

- **Partial Generation**: Save whatever was generated, mark incomplete artifacts
- **Missing Data**: Use neutral scores (0.5) for missing metrics, document in report
- **Chart Failures**: Generate text-based tables as fallback
- **Report Corruption**: Regenerate from JSON source of truth

---

## 6. Testing Strategy

### 6.1 Unit Tests

| Module | Test Cases | Location |
|--------|-----------|----------|
| **Quality Assessor** | `assess_readability()` with various text lengths | `tests/test_assessor.py` |
| | `assess_clarity_goals()` pattern matching | |
| | `assess_scenario_coverage()` with BR-N mapping | |
| | `assess_consistency()` with multiple generations | |
| **Generator** | Successful generation with valid prompt | `tests/test_generator.py` |
| | Retry on invalid response | |
| | Boundary value handling | |
| **Visualization** | Chart PNG saved without errors | `tests/test_charts.py` |
| | Correct data series | |
| **Reporting** | Markdown format matches spec | `tests/test_reports.py` |
| | JSON structure valid | |

### 6.2 Integration Tests

- **Full pipeline**: Generate → Assess → Visualize → Report
- **With live Ollama**: Requires `ohw-ollama` container
- **Without Ollama**: Demo mode with fallback results
- **With DZ2 normalizer**: Test `--normalize` flag
- **All 5 artifact types**: Test each `--type` value

### 6.3 Test Data

- **Requirement files**: Use DZ2 test files from `dz2/docs/`
- **Baseline texts**: Use DZ8 `golden/baseline/llama3.json`
- **Expected metrics**: Pre-computed values for regression testing

### 6.4 Regression Testing

- Run all unit tests: `uv run pytest -q`
- Check OpenSpec format compliance
- Verify JSON report structure matches schema
- Verify Markdown report format matches assignment requirements

---

## 7. Deployment Configuration

### 7.1 Environment Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| **Python** | 3.13 | 3.13 |
| **uv** | Installed | Latest |
| **Ollama** | Installed | Docker container |
| **Model** | `qwen2.5:7b-instruct` or `llama3` | `qwen2.5:7b-instruct` |
| **Memory** | 4 GB RAM | 8+ GB RAM |
| **Disk** | 1 GB free | 5+ GB free |
| **CPU** | 1 core | 2+ cores |
| **GPU** | Optional | Recommended for performance |

### 7.2 Docker Configuration

No new Docker container needed. Uses existing infrastructure:

```yaml
# Uses existing ohw-ollama from infra/
services:
  ohw-ollama:
    # Already defined in d:/Otus/ohw/infra/
    # Model: qwen2.5:7b-instruct or llama3
    # Port: 11434

# Uses existing dz2-ui-1 for UI
services:
  dz2-ui-1:
    # Already defined in d:/Otus/ohw/dz2/
    # Port: 8501
```

### 7.3 Configuration Files

| File | Purpose | Default |
|------|---------|---------|
| `pyproject.toml` | Project dependencies | As created |
| `.env` | Environment variables | OLLAMA_BASE_URL, OLLAMA_MODEL, etc. |
| `normalizer.toml` | DZ2 normalizer config | From DZ2 |
| `presets/*.toml` | Prompt presets | From DZ2/presets/ |

### 7.4 .env Example

```env
# Graduation project environment
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct
OLLAMA_TIMEOUT=600
DZ2_PROMPT_DIR=../dz7/prompts
DZ2_PRESETS_DIR=../dz2/presets
DZ2_NORMALIZER_CONFIG=../dz2/normalizer.toml
AI_TESTGEN_ROOT=.
GRADUATION_PROJECT=1
```

---

## 8. Security and Constraints

### 8.1 Security Constraints (NOT Applied)

❌ **Security code from DZ5 is NOT reused**
- No attack/defense mechanisms from LLM security
- No OWASP Top 10 checks
- No security wall or guardrails

✅ **Applied Constraints:**

- **No external LLM APIs**: Uses only local Ollama
- **No model training**: Inference only
- **No prompt engineering as judgment**: Uses NLP metrics, not LLM-as-judge
- **Data isolation**: Generated artifacts stored locally only
- **OpenSpec compliance**: All specifications in EARS format

### 8.2 Data Constraints

- **Input files**: Only requirement files in BR-N format from DZ2
- **Output location**: `output/` directory, not committed to git (per `.gitignore`)
- **Model usage**: Only `qwen2.5:7b-instruct` or `llama3` via Ollama
- **No hardcoded prompts**: All prompts from external `dz7/templates/<type>.txt`

### 8.3 Performance Constraints

- **Timeout**: 600 seconds per Ollama request (configurable via `OLLAMA_TIMEOUT`)
- **Retry**: Max 3 retries on validation failure (from DZ2 config)
- **Memory**: Should not exceed 8GB RAM for qwen2.5:7b on CPU
- **Batch size**: 10 artifacts per file (configurable via `--count`)

---

## 9. Migration and Compatibility

### 9.1 Compatible With

- **DZ2** `ai-testcase-generator` version 0.1.0+
- **DZ7** prompt templates version (any, externally maintained)
- **DZ8** quality checks (hallucination, consistency)
- **Ohw infrastructure** `ohw-ollama` and `ohw-python:3.13`

### 9.2 Breaking Changes

- **New CLI flags**: `--assess`, `--visualize`, `--report`
- **New modules**: `src/quality/`, `src/visualization/`, `src/reporting/`
- **Enhanced data structures**: `QualityMetrics` adds 4 new fields
- **Output directory**: Adds `charts/`, `quality-metrics.json`, `report.md`

### 9.3 Migration Path

1. Install graduation project alongside existing DZ2/DZ7/DZ8
2. Run with `--generate` first to verify generation works
3. Add `--assess` to evaluate quality
4. Add `--visualize` to see charts
5. Add `--report` for full output
6. Update any custom scripts referencing old output formats

### 9.3 Versioning

- **Major**: Incompatible API changes
- **Minor**: New features, backward-compatible
- **Patch**: Bug fixes, backward-compatible
- **Current**: 0.1.0 (initial release for graduation project)

---

## 10. Future Enhancements (Post-Graduation)

| Priority | Feature | Description |
|----------|---------|-------------|
| **P1** | Streamlit Workbench Tab | Add quality assessment tab to `dz2-ui-1` |
| **P2** | Model Comparison | Compare qwen2.5:7b vs llama3 vs other models |
| **P3** | Custom NLP Models | Train small classifier for each metric |
| **P4** | CI/CD Integration | Automated testing in GitHub Actions |
| **P5** | Multi-language Support | Generate tests for requirements in other languages |
| **P6** | Web API | REST API for generation + assessment |
| **P7** | Database Storage | Persist results in SQLite/PostgreSQL |
| **P8** | Template Auto-generation | Auto-generate prompt templates from examples |

---

## 11. Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 09.11.2026 | Vladislav Chernov | Initial SDD for graduation project |

---

## Appendix A: OpenSpec Compliance

All specifications in this project follow OpenSpec format:

- **Project spec**: `openspec/project.md`
- **Changes**: `openspec/changes/<change>/`
  - `proposal.md` - Scope proposal
  - `design.md` - Architecture design
  - `tasks.md` - Implementation tasks
- **Specifications**: `openspec/specs/<capability>/spec.md`
- **Format**: EARS - `SHALL` + `WHEN/THEN` scenarios
- **Language**: Russian documentation, English code/comments

## Appendix B: Metric Reference

| Metric | Range | Description | Formula/Method |
|--------|-------|-------------|----------------|
| **Readability** | 0.0-1.0 | How easy text is to read | Avg sentence length, optimal ~17 words |
| **Clarity Goals** | 0.0-1.0 | How clear test objectives are | Pattern matching for measurable goals |
| **Scenario Coverage** | 0.0-1.0 | % of BR-N requirements covered | BR-N ID matching in generated text |
| **Consistency** | 0.0-1.0 | Stability across generations | Jaccard similarity of case descriptions |
| **Overall** | 0.0-1.0 | Weighted average | 0.25*readability + 0.25*clarity + 0.30*coverage + 0.20*consistency |

## Appendix C: Example Workflow

```bash
# Full pipeline graduation project
cd d:/Otus/ohw
python -m graduation_project \
  --generate \
  --assess \
  --visualize \
  --report \
  --input-dir dz2/docs \
  --type testcases \
  --count 10 \
  --url https://example.com \
  --normalize \
  --output-dir output

# Expected output structure:
output/
├── quality-metrics.json   # Structured data (source of truth)
├── report.md              # Markdown report in assignment format
├── charts/
│   ├── metrics_boxplot.png
│   ├── radar_chart.png
│   └── per_scenario_scores.png
└── scenarios/
    ├── business-requirements_catalog-courses.md.md
    └── ...
```