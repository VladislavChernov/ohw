"""LLM-адаптеры M2: OpenAICompatibleAdapter (llama.cpp /v1) и FakeLLM для тестов.

Контракт ADR-022: llama.cpp server отдаёт OpenAI-совместимое `/v1/chat/completions`
(LLM_BASE_URL=http://llm:8080). Стрим токенов парсится из SSE-строчек `data: {...}`.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

from graphrag_proto.retrieval.adapters.base import LLMInference


class LLMAdapterError(RuntimeError):
    """Ошибка LLM-адаптера: транспорт или форма ответа.

    Иерархия нужна для разбора прогонов, а не для красоты. Раньше адаптер поднимал
    `RuntimeError` с текстовым префиксом, и на границе пайплайна всё это схлопывалось в
    одну причину «модель не справилась»: недоступный стенд, 500-й ответ, таймаут и
    негодный JSON давали одинаковый счётчик и одинаковое действие в отчёте. А действия
    разные: поднять стенд, посмотреть логи сервиса, поправить промпт.
    """


class LLMUnavailableError(LLMAdapterError):
    """Сервис не отвечает: соединение отклонено, хост не резолвится, сокет закрыт."""


class LLMTimeoutError(LLMAdapterError):
    """Не уложился в отведённое время: connect, read или стрим.

    Отдельный класс, потому что `urlopen(timeout=…)` при таймауте поднимает
    `socket.timeout`, то есть подкласс `OSError`: без порядка `except` он уезжал бы в
    «недоступен», и таймаут — самая частая причина сбоя на длинном стриме — был бы
    неотличим от того, что сервис выключен.
    """


class LLMHTTPError(LLMAdapterError):
    """Сервис ответил, но кодом ошибки."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"LLM HTTP {status}: {body!r}")
        self.status = status
        self.body = body


class LLMResponseError(LLMAdapterError):
    """Сервис ответил кодом успеха, но телом, которое не является ответом.

    Разбор ответа — наш код, и раньше он индексировал `choices[0].message.content` без
    проверки: пустой или иной объект давал `KeyError`, который на границе выглядел как
    «модель вернула ерунду». Теперь это названная ошибка с проверкой формы: сервис нарушил
    контракт, а не модель плохо ответила.
    """


DEFAULT_TIMEOUT_S = 600.0


class OpenAICompatibleAdapter(LLMInference):
    """`/v1/chat/completions` на любой OpenAI-совместимый сервер (llama.cpp, vLLM, LM Studio)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        seed: int | None = None,
    ) -> None:
        """`model` обязателен и не имеет дефолта намеренно.

        Здесь стояло `qwen2.5-coder-7b-instruct-abliterated-q4_k_m`, и это худшее из всех
        мест для такого имени: прямой вызов конструктора минует фабрику и молча получал
        7B-модель, а LLM-сервер в поле `model` принимает что угодно и отдаёт загруженную -
        то есть подмена модели не давала ошибки. Имя модели приходит из профиля
        `infra/config/namespaces.yaml` или из env стенда, см. `factory._build_llm`.
        """
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s
        self._seed = seed

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
        # `seed` полеется в тело, а не задаётся заголовком: разные OpenAI-совместимые
        # серверы понимают его по-разному, а лишнее поле у тех, кто его не знает, просто
        # игнорируется. При `temperature = 0` он не нужен - жадный декодер детерминирован.
        if self._seed is not None:
            body["seed"] = self._seed
        req = urllib.request.Request(
            f"{self._base_url}/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            deadline = time.monotonic() + self._timeout_s if self._timeout_s > 0 else None
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                if stream:
                    yield from self._iter_stream(resp, deadline)
                else:
                    yield self._extract_content(resp.read().decode("utf-8"))
        except LLMAdapterError:
            # Уже классифицировано: перечитывать текст префикса, чтобы угадать причину,
            # значит вернуть ту самую неразличимость, ради устранения которой типы и
            # введены.
            raise
        except TimeoutError as exc:
            # ПЕРЕД `OSError`: socket.timeout — подкласс OSError, и без этого порядка
            # таймаут отдавался бы как «сервис недоступен».
            raise LLMTimeoutError("LLM таймаут соединения или чтения") from exc
        except urllib.error.HTTPError as exc:
            raise LLMHTTPError(exc.code, exc.read()[:200].decode("utf-8", errors="replace")) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise LLMUnavailableError(f"LLM недоступен ({self._base_url}): {exc}") from exc

    @staticmethod
    def _extract_content(raw: str) -> str:
        """Достать текст ответа, проверив форму.

        Проверка обязательна: без неё неверное тело даёт `KeyError`/`IndexError` из
        индексации, и на границе пайплайна это читается как «модель вернула ерунду», то
        есть наш парсинг выдаёт себя за вину модели.
        """
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMResponseError("ответ LLM не является JSON") from exc
        if not isinstance(payload, dict):
            raise LLMResponseError(f"ответ LLM не является объектом: {type(payload).__name__}")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMResponseError("в ответе LLM нет непустого choices")
        first = choices[0] if isinstance(choices[0], dict) else {}
        _reject_truncation(first.get("finish_reason"))
        message = first.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LLMResponseError("в ответе LLM нет строкового choices[0].message.content")
        return content

    @staticmethod
    def _iter_stream(resp: Any, deadline: float | None = None) -> Iterator[str]:
        finish_reason: str | None = None
        for raw_line in resp:
            if deadline is not None and time.monotonic() >= deadline:
                raise LLMTimeoutError("LLM stream timeout")
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            raw = line[len("data:"):].strip()
            if raw == "[DONE]":
                break
            delta, chunk_finish = _extract_delta(raw)
            if chunk_finish:
                finish_reason = chunk_finish
            if delta:
                yield delta
        # Проверка после `break`, а не внутри цикла: в потоковом ответе причина
        # завершения приезжает последним фрагментом, и проверка на каждом чанке
        # обрывала бы нормальный поток на первом же куске без причины.
        _reject_truncation(finish_reason)


def _reject_truncation(finish_reason: object) -> None:
    """Обрыв ответа по длине должен быть виден, а не молча давать битый JSON.

    Сервер присылает `finish_reason: "length"`, когда ответ упёрся в `max_tokens`. Раньше
    это поле не читалось, `content` оставался строкой - просто обрезанной посреди JSON, -
    и дальше срабатывал `json.JSONDecodeError`. Для стадии EXTRACT с
    `optional_failure=True` это означало тихую деградацию к детерминированному пути:
    документ попадал в граф успешно, просто без извлечённых сущностей. Отказ выглядел
    как успех, и `enrichment.cause` не мог отличить обрыв по длине от таймаута.

    Поле отсутствует у части серверов, поэтому отсутствие - не повод для ошибки: молчаливый
    отказ там, где сервер просто не сообщает причину, был бы хуже.
    """
    if not isinstance(finish_reason, str) or not finish_reason or finish_reason == "stop":
        return
    raise LLMResponseError(
        f"ответ LLM неполон: finish_reason={finish_reason!r}. "
        f"Почти всегда это обрыв по max_tokens - увеличьте его либо уменьшите чанк, "
        f"иначе JSON не распарсится и извлечение молча деградирует к детерминированному пути."
    )


def _extract_delta(raw: str) -> tuple[str, str | None]:
    """Дельта текущего фрагмента и причина завершения, если сервер её прислал."""
    try:
        chunk = json.loads(raw)
    except json.JSONDecodeError:
        return "", None
    choices = chunk.get("choices") or []
    if not choices:
        return "", None
    first = choices[0] if isinstance(choices[0], dict) else {}
    reason = first.get("finish_reason")
    delta = first.get("delta") or {}
    content = delta.get("content") if isinstance(delta, dict) else None
    return (content if isinstance(content, str) else ""), (reason if isinstance(reason, str) else None)


class FakeLLM(LLMInference):
    """Детерминированный LLM для тестов и демо без GPU: делит фиксированный текст.

    Разбиение с сохранением разделителей: `"".join(deltas) == text` (SSE токены
    не теряют пробелов).
    """

    def __init__(
        self,
        text: str = "Ответ прототипа: детерминированный фейк без LLM.",
        by_words: bool = True,
        is_fake: bool = True,
    ) -> None:
        self._text = text
        self._deltas = re.split(r"(\s+)", text) if (by_words and text) else [text]
        self.is_fake = is_fake

    def generate(self, prompt: str, system: str = "", stream: bool = True) -> Iterator[str]:
        yield from self._deltas

    @property
    def full_text(self) -> str:
        return self._text