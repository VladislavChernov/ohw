"""Ollama HTTP client."""

from __future__ import annotations

import httpx


async def generate_code(
    base_url: str,
    model: str,
    prompt: str,
    max_retries: int = 3,
) -> str:
    """Send prompt to Ollama and return generated Python code.

    Retries on empty/invalid responses up to max_retries.
    """
    url = f"{base_url.rstrip('/')}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 4096,
        },
    }

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                code = data.get("response", "").strip()
                if code and "def test_" in code:
                    return code
                last_error = ValueError(f"No valid test functions in response (attempt {attempt})")
        except httpx.HTTPError as exc:
            last_error = exc

        if attempt < max_retries:
            import asyncio

            await asyncio.sleep(2 * attempt)

    raise RuntimeError(f"Ollama generation failed after {max_retries} attempts: {last_error}")
