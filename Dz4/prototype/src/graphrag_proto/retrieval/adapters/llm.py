"""LLM-адаптеры M2: OpenAICompatibleAdapter (llama.cpp /v1) и FakeLLM для тестов.

Контракт ADR-022: llama.cpp server отдаёт OpenAI-совместимое `/v1/chat/completions`
(LLM_BASE_URL=http://llm:8080). Стрим токенов парсится из SSE-строчек `data: {...}`.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

from graphrag_proto.retrieval.adapters.base import LLMInference

DEFAULT_TIMEOUT_S = 600.0


class OpenAICompatibleAdapter(LLMInference):
    """`/v1/chat/completions` на любой OpenAI-совместимый сервер (llama.cpp, vLLM, LM Studio)."""

    def __init__(
        self,
        base_url: str,
        model: str = "qwen2.5-coder-7b-instruct-abliterated-q4_k_m",
        temperature: float = 0.3,
        max_tokens: int = 2048,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s

    def generate(self, prompt: str, system: str = "", stream: bool = True) -> Iterator[str]:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": stream,
        }
        req = urllib.request.Request(
            f"{self._base_url}/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                if stream:
                    yield from self._iter_stream(resp)
                else:
                    payload = json.loads(resp.read().decode("utf-8"))
                    content = payload["choices"][0]["message"]["content"]
                    yield content
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"LLM HTTP {exc.code}: {exc.read()[:200]!r}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"LLM недоступен ({self._base_url}): {exc}") from exc

    @staticmethod
    def _iter_stream(resp: Any) -> Iterator[str]:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            raw = line[len("data:"):].strip()
            if raw == "[DONE]":
                return
            delta = _extract_delta(raw)
            if delta:
                yield delta


def _extract_delta(raw: str) -> str:
    try:
        chunk = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    return content if isinstance(content, str) else ""


class FakeLLM(LLMInference):
    """Детерминированный LLM для тестов и демо без GPU: делит фиксированный текст.

    Разбиение с сохранением разделителей: `"".join(deltas) == text` (SSE токены
    не теряют пробелов).
    """

    def __init__(self, text: str = "Ответ прототипа: детерминированный фейк без LLM.", by_words: bool = True) -> None:
        self._text = text
        self._deltas = re.split(r"(\s+)", text) if (by_words and text) else [text]

    def generate(self, prompt: str, system: str = "", stream: bool = True) -> Iterator[str]:
        yield from self._deltas

    @property
    def full_text(self) -> str:
        return self._text