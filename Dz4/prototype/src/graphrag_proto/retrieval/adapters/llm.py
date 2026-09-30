"""LLM-адаптеры M2: OpenAICompatibleAdapter (llama.cpp /v1) и FakeLLM для тестов.

Контракт ADR-022: llama.cpp server отдаёт OpenAI-совместимое `/v1/chat/completions`
(LLM_BASE_URL=http://llm:8080). Стрим токенов парсится из SSE-строчек `data: {...}`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
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

    `raw` - то, что прислал сервер, в усечённом виде. Нужен для диагностики обрыва по
    длине: раньше текст, на котором JSON не закрылся, исчезал вместе с исключением, и
    вопрос «что именно модель пишет в ответ» был неотвечаемым без нового прогона. Теперь
    улика едет в сообщении ошибки и оказывается в записи джобы.
    """

    def __init__(self, message: str, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw


#: Сколько символов сырого ответа едет в ошибку. Без предела в записи джобы оказался бы
#: многосоткилобайтный текст, и запись перестала бы читаться; с пределом видно начало
#: вывода, где модель ещё пишет осмысленно, и хвост, где она уже зациклилась.
RAW_EXCERPT_LIMIT = 2000


def _excerpt(raw: str | None) -> str | None:
    if raw is None:
        return None
    if len(raw) <= RAW_EXCERPT_LIMIT:
        return raw
    half = RAW_EXCERPT_LIMIT // 2
    return f"{raw[:half]}…[середина усечена]…{raw[-half:]}"


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
        context_window: int | str | None = None,
        exchange_stage: str = "llm",
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
        self._context_window = context_window
        # Учёт токенов последнего вызова. `generate` - генератор, и вернуть значение из
        # него без переписывания всех вызывающих нельзя, поэтому состояние явное и
        # одноимённое: относится к ПОСЛЕДНЕМУ вызову и обнуляется в его начале.
        self.last_usage: dict[str, int] | None = None
        self._exchange = _ExchangeLog()
        self._exchange_stage = exchange_stage

    def generate(
        self,
        prompt: str,
        system: str = "",
        stream: bool = True,
        labels: dict[str, str] | None = None,
    ) -> Iterator[str]:
        """Обмен с моделью. `labels` — метки вызывающего для журнала обмена.

        В метках кладются **идентификаторы** (источник, чанк), а не текст: по ним записи
        соединяются с документом, и без этого следующая обработка получает строки, которые
        нечем сгруппировать. Текст чанка в журнал не пишется.

        Стоит понимать про уровень `full`: промпт извлечения САМ содержит текст чанка,
        поэтому `full` = «текст документа попал в лог». Если нужен только вывод модели
        без раздувания журнала, есть уровень `response` - он пишет ответ и не пишет
        промпт. Это осознанный выбор на момент включения, а не свойство журнала;
        `LLM_EXCHANGE_LOG_FILE` позволяет увести записи из stdout и из Loki.
        """
        self.last_usage = None
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
            started = time.monotonic()
            collected: list[str] = []
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                if stream:
                    yield from self._iter_stream(resp, deadline, collected)
                else:
                    # НЕ `text = yield ...`: в генераторе такая запись присваивает значение,
                    # которое потребитель отправляет через send(), а `"".join(...)` не
                    # отправляет ничего. Текст берётся ДО yield - иначе в журнал уходит None
                    # вместо ответа, то есть ровно в том случае, когда он нужен.
                    text = self._extract_content(resp.read().decode("utf-8"))
                    collected.append(text)
                    yield text
            self._exchange.record(
                model=self._model,
                stage=self._exchange_stage,
                stream=stream,
                system=system,
                prompt=prompt,
                response="".join(collected),
                usage=self.last_usage,
                duration_ms=int((time.monotonic() - started) * 1000),
                labels=labels,
            )
        except LLMAdapterError as exc:
            # Уже классифицировано: перечитывать текст префикса, чтобы угадать причину,
            # значит вернуть ту самую неразличимость, ради устранения которой типы и
            # ошибок введены. Но обмен в журнал пишется ДО re-raise: иначе именно тот
            # случай, ради которого журнал и включают, не был бы виден в журнале.
            self._exchange.record(
                model=self._model,
                stage=self._exchange_stage,
                stream=stream,
                system=system,
                prompt=prompt,
                response=getattr(exc, "raw", None),
                usage=self.last_usage,
                error=type(exc).__name__,
                labels=labels,
            )
            raise
        except TimeoutError as exc:
            # ПЕРЕД `OSError`: socket.timeout — подкласс OSError, и без этого порядка
            # таймаут отдавался бы как «сервис недоступен».
            raise LLMTimeoutError("LLM таймаут соединения или чтения") from exc
        except urllib.error.HTTPError as exc:
            raise LLMHTTPError(exc.code, exc.read()[:200].decode("utf-8", errors="replace")) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise LLMUnavailableError(f"LLM недоступен ({self._base_url}): {exc}") from exc

    def _extract_content(self, raw: str) -> str:
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
        message = first.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LLMResponseError("в ответе LLM нет строкового choices[0].message.content")
        # Учёт токенов снимается здесь, а не у вызывающего: сервер OpenAI-совместимого
        # режима (llama.cpp) возвращает `usage` в том же теле, и это точные числа для этой
        # модели, а не оценка чужим токенизатором. Раньше поле не читалось вовсе, поэтому
        # вопрос «кто жрёт токены - промпт или ответ» не имел ответа.
        usage = payload.get("usage")
        self.last_usage = _normalize_usage(usage)
        _reject_truncation(first.get("finish_reason"), content)
        return content

    @staticmethod
    def _iter_stream(
        resp: Any,
        deadline: float | None = None,
        collected: list[str] | None = None,
    ) -> Iterator[str]:
        finish_reason: str | None = None
        # Ответ накапливается, хотя и отдаётся по частям: без накопленного текста обрыв по
        # длине в потоковом режиме уносит с собой всю улику. Список передаётся вызывающим,
        # чтобы журнал обмена увидел то же, что получит пайплайн, а не отдельную копию.
        sink = collected if collected is not None else []
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
                sink.append(delta)
                yield delta
        # Проверка после `break`, а не внутри цикла: в потоковом ответе причина
        # завершения приезжает последним фрагментом, и проверка на каждом чанке
        # обрывала бы нормальный поток на первом же куске без причины.
        _reject_truncation(finish_reason, "".join(sink))


#: Уровни режима логирования обмена с моделью. `off` - умолчание, и это не осторожность:
#: полный обмен - это весь промпт и весь ответ, то есть потенциально весь документ в логах.
#:
#: `response` отвечает на вопрос «что модель выдаёт» и НЕ содержит текста документа: в
#: извлечении текст чанка лежит внутри промпта, поэтому «ответ без промпта» - это ровно
#: «лог без раздувания». `full` добавляет промпт и системную инструкцию, и включается
#: осознанно: с этого момента текст документа попадает в журнал.
_EXCHANGE_LEVELS = {"off": 0, "meta": 1, "response": 2, "full": 3}


class _ExchangeLog:
    """Журнал обмена с моделью: отдельный режим вместо «запишем всё всегда».

    Зачем он нужен. Пользовательский UI показывает ответ модели в потоке, и из этого
    разговора видно, что нехватка ответа - обычное дело, а не редкий сбой. Из ingest
    обмена видно не было ничего: при обрыве по длине текст исчезал вместе с исключением,
    и вопрос «что именно модель пишет в ответ» требовал нового прогона каждый раз.

    Три уровня, и третий включается осознанно:
      * `off` (по умолчанию) - ничего;
      * `meta` - модель, расход токенов, причина завершения, длины. Без содержимого;
      * `response` - плюс текст ОТВЕТА. Текста документа в записи нет: он лежит в промпте,
        а промпт на этом уровне не пишется;
      * `full` - плюс промпт и системная инструкция, то есть текст документа.

    Куда писать: в stdout (тот же structlog-конвейер, что и остальное, `docs/06` §2) либо в
    файл, если он задан через `LLM_EXCHANGE_LOG_FILE`. Файл - это случай «хочу посмотреть
    глазами весь обмен по конкретному прогону»: в Loki такой объём не кладут.

    Логирование не должно ломать разбор: ошибка записи журнала молча игнорируется, иначе
    включённая диагностика могла бы уронить джобы, ради которых включена.
    """

    def __init__(self) -> None:
        level = os.environ.get("LLM_EXCHANGE_LOG", "off").strip().lower() or "off"
        self.level = _EXCHANGE_LEVELS.get(level, 0)
        self.path = os.environ.get("LLM_EXCHANGE_LOG_FILE", "").strip()
        try:
            self.limit = max(0, int(os.environ.get("LLM_EXCHANGE_LOG_MAX_CHARS", "8000")))
        except ValueError:
            self.limit = 8000
        self._log = logging.getLogger("graphrag_proto.llm.exchange")

    def _clip(self, text: str) -> tuple[str, bool]:
        """Текст и признак усечения. Само усечение помечается, иначе следующая обработка
        примет обрезанный ответ за полный и посчитает выводы по неполным данным."""
        if not self.limit or len(text) <= self.limit:
            return text, False
        return f"{text[: self.limit]}…[усечено, всего {len(text)} символов]", True

    def record(
        self,
        *,
        model: str,
        stage: str,
        stream: bool,
        system: str = "",
        prompt: str = "",
        response: str | None = None,
        finish_reason: str | None = None,
        usage: dict[str, int] | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
        labels: dict[str, str] | None = None,
    ) -> None:
        if not self.level:
            return
        record: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "model": model,
            "stage": stage,
            "stream": stream,
            "finish_reason": finish_reason,
            "usage": usage,
            "prompt_chars": len(prompt),
            "response_chars": len(response or ""),
            "error": error,
            "duration_ms": duration_ms,
        }
        if self.level >= _EXCHANGE_LEVELS["response"]:
            response_text, response_cut = self._clip(response or "")
            record["response"] = response_text
        truncated: dict[str, bool] = {}
        if self.level >= _EXCHANGE_LEVELS["response"]:
            truncated["response"] = response_cut
        if self.level >= _EXCHANGE_LEVELS["full"]:
            prompt_text, prompt_cut = self._clip(prompt)
            system_text, system_cut = self._clip(system)
            record["system"] = system_text
            record["prompt"] = prompt_text
            truncated["system"] = system_cut
            truncated["prompt"] = prompt_cut
        # Признаки усечения обязательны: «полный обмен» и «усечённый обмен» - разные
        # данные, и молча склеивать их нельзя. На уровне `response` промпта в записи нет,
        # поэтому и признака усечения промпта нет - ключ не выдумывается.
        if truncated:
            record["truncated"] = truncated
        # Метки вызывающего. Без них записи не соединяются с документом и чанком, а
        # следующая обработка получает строки, которые нечем сгруппировать.
        if labels:
            record["labels"] = {str(k): str(v) for k, v in labels.items()}
        line = json.dumps(record, ensure_ascii=False)
        try:
            if self.path:
                with Path(self.path).open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            else:
                self._log.info("llm exchange %s", line)
        except OSError as exc:
            # Диагностика не должна ломать разбор: ошибка записи журнала - это ошибка
            # журнала, а не документа, и ронять из-за неё джобы нельзя.
            self._log.warning("не удалось записать журнал обмена с LLM: %s", exc)


def _normalize_usage(usage: object) -> dict[str, int] | None:
    """`usage` приводится к целым числам, а отсутствие остаётся отсутствием.

    Приводить нужно осторожно: часть серверов отдаёт `usage: null` в стриме, а часть -
    числа строкой. Нечитаемое значение даёт `None`, а не ноль: ноль означал бы «модель не
    потратила токены», то есть выглядел бы как ответ без содержимого.
    """
    if not isinstance(usage, dict):
        return None
    result: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        result[key] = value
    return result or None


def _reject_truncation(finish_reason: object, raw: str | None = None) -> None:
    """Обрыв ответа по длине должен быть виден, а не молча давать битый JSON.

    Сервер присылает `finish_reason: "length"`, когда ответ упёрся в `max_tokens`. Раньше
    это поле не читалось, `content` оставался строкой - просто обрезанной посреди JSON, -
    и дальше срабатывал `json.JSONDecodeError`. Для стадии EXTRACT с
    `optional_failure=True` это означало тихую деградацию к детерминированному пути:
    документ попадал в граф успешно, просто без извлечённых сущностей. Отказ выглядел
    как успех, и `enrichment.cause` не мог отличить обрыв по длине от таймаута.

    Поле отсутствует у части серверов, поэтому отсутствие - не повод для ошибки: молчаливый
    отказ там, где сервер просто не сообщает причину, был бы хуже.

    Сырой ответ едет вместе с ошибкой: обрыв виден, но что модель наговорила до него -
    нет, а без этого любой разбор упирается в «нужно ещё раз прогнать».
    """
    if not isinstance(finish_reason, str) or not finish_reason or finish_reason == "stop":
        return
    excerpt = _excerpt(raw)
    message = (
        f"ответ LLM неполон: finish_reason={finish_reason!r}. "
        f"Почти всегда это обрыв по max_tokens - увеличьте его либо уменьшите чанк, "
        f"иначе JSON не распарсится и извлечение молча деградирует к детерминированному пути."
    )
    if excerpt:
        message = f"{message}\n--- начало/конец ответа модели ---\n{excerpt}"
    raise LLMResponseError(message, raw=raw)


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

    def generate(
        self,
        prompt: str,
        system: str = "",
        stream: bool = True,
        labels: dict[str, str] | None = None,
    ) -> Iterator[str]:
        # `labels` принимается и не используется: сигнатура обязана совпадать с настоящим
        # адаптером, иначе вызывающий, который честно передаёт метки, падает на тестах.
        del prompt, system, stream, labels
        yield from self._deltas

    @property
    def full_text(self) -> str:
        return self._text