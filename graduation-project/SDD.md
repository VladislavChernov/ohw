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

---

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