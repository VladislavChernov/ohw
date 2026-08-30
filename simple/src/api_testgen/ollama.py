"""Ollama HTTP client."""

from __future__ import annotations

import httpx


async def generate_code(
    base_url: str,
    model: str,
    prompt: str,
    max_retries: int = 3,
    timeout: float = 600.0,
    temperature: float = 0.3,
    num_predict: int = 4096,
    seed: int | None = None,
    required_markers: list[str] | None = None,
) -> str:
    """Send prompt to Ollama and return generated Python code.

    Retries on empty/invalid responses up to max_retries. When
    `required_markers` is given, a response missing any marker is rejected
    and the next attempt includes explicit feedback about what is missing.
    """
    url = f"{base_url.rstrip('/')}/api/generate"
    options: dict = {
        "temperature": temperature,
        "num_predict": num_predict,
    }
    if seed is not None:
        options["seed"] = seed
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": options,
    }

    from api_testgen.validator import build_feedback, find_missing

    current_prompt = prompt
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            payload["prompt"] = current_prompt
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                code = data.get("response", "").strip()
                if code and "def test_" in code:
                    missing = find_missing(code, required_markers or [])
                    if not missing:
                        return code
                    last_error = ValueError(
                        f"Missing required coverage: {', '.join(missing)} (attempt {attempt})"
                    )
                    current_prompt = (
                        f"{prompt}\n\n---\nPrevious answer was rejected.\n"
                        f"{build_feedback(missing)}\n"
                    )
                else:
                    last_error = ValueError(f"No valid test functions in response (attempt {attempt})")
        except httpx.HTTPError as exc:
            last_error = exc

        if attempt < max_retries:
            import asyncio

            await asyncio.sleep(2 * attempt)

    detail = f"{type(last_error).__name__}: {last_error}" if last_error else "unknown error"
    raise RuntimeError(f"Ollama generation failed after {max_retries} attempts ({detail})")
