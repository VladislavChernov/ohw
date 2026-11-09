#!/usr/bin/env python3
"""NLP quality assessment for generated test documentation."""

import re
from typing import List, Dict, Any


def assess_readability(text: str) -> float:
    """Calculate readability score (0-1, higher=more readable)."""
    sentences = [s for s in text.split('.') if s.strip()]
    if not sentences:
        return 0.0
    avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
    # Optimal ~17 words per sentence for technical text
    return max(0.0, min(1.0, 1.0 - abs(avg_len - 17) / 30))


def assess_clarity_goals(text: str) -> float:
    """Assess clarity of test goals (0-1, higher=clearer)."""
    score = 0.0
    text_lower = text.lower()
    if 'should' in text_lower and 'error' in text_lower:
        score += 0.3
    if 'expected' in text_lower:
        score += 0.3
    if 'must' in text_lower:
        score += 0.2
    return min(1.0, score)


def assess_scenario_coverage(generated_text: str, requirement_ids: List[str]) -> float:
    """Calculate percentage of requirements covered by generated test scenarios."""
    if not requirement_ids:
        return 0.5
    br_ids_found = set(re.findall(r'BR-\d+', generated_text))
    if not br_ids_found:
        return 0.0
    covered = len(br_ids_found.intersection(set(requirement_ids)))
    return round(min(1.0, covered / len(requirement_ids)), 3)


def assess_consistency(generation_texts: List[str]) -> float:
    """Assess consistency across multiple generations (0-1, higher=more consistent)."""
    if len(generation_texts) < 2:
        return 1.0
    unique = len(set(generation_texts))
    total = len(generation_texts)
    duplicate_rate = 1 - (unique / total) if total > 0 else 0
    return min(1.0, duplicate_rate * 2)