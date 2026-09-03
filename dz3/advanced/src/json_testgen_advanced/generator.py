"""LLM plan generation with a plan-level feedback loop (via ohw_kit).

Uses the shared ``OllamaClient`` (json_mode) and ``extract_json``; validates
the returned plan (schema + coverage + cleanup discipline) and, if it fails,
sends the issues back to the model within the retry budget. The model's code
is never executed — only its JSON plan is.
"""

from __future__ import annotations

import os
import time

from ohw_kit.jsonreply import JsonReplyError, extract_json
from ohw_kit.ollama_client import OllamaClient

from json_testgen_advanced.plan import (
    TestPlan,
    load_plan,
    validate_plan,
    validate_plan_schema,
)


class GenerationFailed(RuntimeError):
    """The model could not produce a valid plan within the retry budget."""


def generate_plan(
    client: OllamaClient,
    prompt: str,
    *,
    max_retries: int = 3,
    required_resources: list[str] | None = None,
) -> TestPlan:
    """Send the prompt, validate the JSON plan, and retry with feedback."""
    if not client.json_mode:
        client.json_mode = True
    current_prompt = prompt
    last_issue = "неизвестная ошибка"
    for attempt in range(1, max_retries + 1):
        reply = client.chat(system="Ты — инженер по тестированию API. Отвечай ТОЛЬКО JSON-планом.", user=current_prompt)
        debug_dir = os.getenv("DEBUG_RAW_DIR")
        if debug_dir:
            os.makedirs(debug_dir, exist_ok=True)
            with open(os.path.join(debug_dir, f"raw_attempt_{attempt}.txt"), "w", encoding="utf-8") as fh:
                fh.write(reply)
        try:
            raw = extract_json(reply)
        except JsonReplyError as exc:
            last_issue = str(exc)
            current_prompt = _feedback(prompt, [last_issue])
            continue

        schema_ok = validate_plan_schema(raw)
        if not schema_ok.ok:
            last_issue = " ".join(schema_ok.issues)
            current_prompt = _feedback(prompt, schema_ok.issues)
            continue


        plan = load_plan(reply)
        plan_ok = validate_plan(plan, required_resources or [])
        if plan_ok.ok:
            return plan
        last_issue = " ".join(plan_ok.issues)
        current_prompt = _feedback(prompt, plan_ok.issues)
        if attempt < max_retries:
            time.sleep(1.0 * attempt)

    raise GenerationFailed(
        f"не удалось получить валидный план за {max_retries} попыток: {last_issue}"
    )


def _feedback(original: str, issues: list[str]) -> str:
    return (
        "{original}\n\n---\n"
        "Предыдущий JSON-план отклонён. Исправь:\n"
        + "\n".join(f"- {issue}" for issue in issues)
        + "\nВыведи ТОЛЬКО исправленный JSON-план (схема v3), без текста."
    )
