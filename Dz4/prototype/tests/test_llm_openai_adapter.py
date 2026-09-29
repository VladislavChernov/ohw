"""Контрактный тест OpenAICompatibleAdapter (ADR-022): streaming, non-streaming, ошибки."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from graphrag_proto.ingestion_service.app import EXTRACTION_LLM_TEMPERATURE_ENV
from graphrag_proto.retrieval.adapters.llm import (
    LLMAdapterError,
    LLMHTTPError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
    OpenAICompatibleAdapter,
)


def _make_handler(payloads: dict[str, Any]):
    """Фабрика HTTPHandler: payloads — mapping path -> (status, body)."""

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = payloads.get(self.path)
            if body is None:
                self.send_error(404)
                return
            status, response = body
            if isinstance(response, str):
                response = response.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: Any) -> None:
            pass  # тихий HTTP

    return _Handler


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _start_server(handler_class: type) -> tuple[HTTPServer, int]:
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


# --- streaming --------------------------------------------------------

def test_streaming_sse_deltas() -> None:
    sse = (
        'data: {"choices":[{"delta":{"content":"привет"}}]}\n'
        'data: {"choices":[{"delta":{"content":" мир"}}]}\n'
        "data: [DONE]\n"
    )
    handler = _make_handler({"/v1/chat/completions": (200, sse)})
    server, port = _start_server(handler)
    try:
        llm = OpenAICompatibleAdapter(f"http://127.0.0.1:{port}", model="test", timeout_s=5)
        deltas = list(llm.generate("вопрос", stream=True))
        assert deltas == ["привет", " мир"]
    finally:
        server.shutdown()


def test_streaming_total_deadline_stops_slow_token_stream() -> None:
    class _SlowStream:
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"start"}}]}\n'
            time.sleep(0.05)
            yield b'data: {"choices":[{"delta":{"content":"late"}}]}\n'

    adapter = OpenAICompatibleAdapter("http://127.0.0.1:1", model="test", timeout_s=0.01)
    with pytest.raises(LLMTimeoutError):
        list(adapter._iter_stream(_SlowStream(), time.monotonic() + 0.01))


# --- non-streaming ----------------------------------------------------

def test_non_streaming_content() -> None:
    resp = json.dumps({"choices": [{"message": {"content": "ответ"}}]})
    handler = _make_handler({"/v1/chat/completions": (200, resp)})
    server, port = _start_server(handler)
    try:
        llm = OpenAICompatibleAdapter(f"http://127.0.0.1:{port}", model="test", timeout_s=5)
        deltas = list(llm.generate("вопрос", stream=False))
        assert deltas == ["ответ"]
    finally:
        server.shutdown()


# --- ошибки ----------------------------------------------------------
# Типы, а не текст. Разбор прогонов строится на причине, и причина берётся из класса
# исключения: «стенд упал» и «модель вернула ерунду» требуют противоположных действий,
# а оба раньше были `RuntimeError` с префиксом в сообщении.

def test_http_500_raises_typed_http_error_with_status() -> None:
    handler = _make_handler({"/v1/chat/completions": (500, b"Internal Server Error")})
    server, port = _start_server(handler)
    try:
        llm = OpenAICompatibleAdapter(f"http://127.0.0.1:{port}", model="test", timeout_s=5)
        with pytest.raises(LLMHTTPError) as excinfo:
            list(llm.generate("вопрос"))
        assert excinfo.value.status == 500
    finally:
        server.shutdown()


def test_read_timeout_is_timeout_not_unavailable() -> None:
    """Таймаут чтения обязан быть таймаутом.

    `urlopen(timeout=…)` при таймауте поднимает `socket.timeout`, то есть подкласс
    `OSError`. Без порядка `except` он уезжал в «сервис недоступен», и самая частая причина
    сбоя на длинном стриме была неотличима от того, что сервис выключен.
    """

    class _SlowHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            time.sleep(2)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"choices":[{"message":{"content":"late"}}]}')

        def log_message(self, format: str, *args: Any) -> None:
            pass

    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        llm = OpenAICompatibleAdapter(f"http://127.0.0.1:{port}", model="test", timeout_s=0.1)
        with pytest.raises(LLMTimeoutError):
            list(llm.generate("вопрос"))
    finally:
        server.shutdown()


def test_connection_refused_is_unavailable_not_timeout() -> None:
    llm = OpenAICompatibleAdapter("http://127.0.0.1:59999", model="test", timeout_s=1)
    with pytest.raises(LLMUnavailableError):
        list(llm.generate("вопрос"))


@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b'{"choices":[]}',
        b'{"choices":[{"message":{}}]}',
        b'{"choices":[{"message":{"content":123}}]}',
        "не json".encode(),
        b"[1,2,3]",
    ],
    ids=["empty", "no-choices", "no-content", "content-not-str", "not-json", "not-object"],
)
def test_malformed_success_body_is_typed_response_error(body: bytes) -> None:
    """Сервис ответил кодом успеха, но телом, которое не является ответом.

    Раньше `payload["choices"][0]["message"]["content"]` падал в `KeyError`/`IndexError`/
    `TypeError` прямо в адаптере, и на границе пайплайна это выглядело как «модель вернула
    ерунду». То есть наш парсинг выдавал себя за вину модели — ровно то, ради чего граница
    с моделью и сужалась. Проверка формы переносит вину на того, кто нарушил контракт.
    """
    handler = _make_handler({"/v1/chat/completions": (200, body)})
    server, port = _start_server(handler)
    try:
        llm = OpenAICompatibleAdapter(f"http://127.0.0.1:{port}", model="test", timeout_s=5)
        with pytest.raises(LLMResponseError):
            list(llm.generate("вопрос", stream=False))
    finally:
        server.shutdown()


def test_all_adapter_errors_share_one_base() -> None:
    """Общий базовый класс — чтобы поймать «это всё про адаптер» одним isinstance.

    Без него на границе пришлось бы перечислять четыре класса, и пятый (новый вид сбоя)
    молча попал бы в дефолт.
    """
    for exc in (
        LLMUnavailableError("u"),
        LLMTimeoutError("t"),
        LLMHTTPError(500, "b"),
        LLMResponseError("r"),
    ):
        assert isinstance(exc, LLMAdapterError)
        assert isinstance(exc, RuntimeError)


# --- factory wiring (LLM_ADAPTER=openai + LLM_BASE_URL) ----------------

def test_factory_openai_adapter_hits_stub(monkeypatch: Any) -> None:
    """LLM_ADAPTER=openai + LLM_BASE_URL собирают OpenAICompatibleAdapter против стаб-сервера."""
    sse = (
        'data: {"choices":[{"delta":{"content":"factory"}}]}\n'
        'data: {"choices":[{"delta":{"content":" ok"}}]}\n'
        "data: [DONE]\n"
    )
    handler = _make_handler({"/v1/chat/completions": (200, sse)})
    server, port = _start_server(handler)
    try:
        from graphrag_proto.retrieval.adapters.factory import build_llm

        monkeypatch.setenv("LLM_ADAPTER", "openai")
        monkeypatch.setenv("LLM_BASE_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("LLM_MODEL", "stub-model")
        llm = build_llm()
        deltas = list(llm.generate("вопрос"))
        assert deltas == ["factory", " ok"]
    finally:
        server.shutdown()


def test_truncated_answer_is_reported_as_such() -> None:
    """Обрыв по `max_tokens` обязан называться, а не выглядеть как битый JSON.

    Регрессия на молчаливую деградацию. Сервер честно присылает
    `finish_reason: "length"` и обрезанный посреди объекта JSON, но поле не читалось, и
    дальше срабатывал `json.JSONDecodeError`. Для стадии EXTRACT с
    `optional_failure=True` это означало, что документ попадал в граф успешно, просто без
    извлечённых сущностей, а `enrichment.cause` не позволял отличить обрыв по длине от
    таймаута. Теперь причина названа прямо.
    """
    body = json.dumps(
        {"choices": [{"message": {"content": '{"requirements": [{"canonical_name": "обр'}, "finish_reason": "length"}]}
    )
    handler = _make_handler({"/v1/chat/completions": (200, body)})
    server, port = _start_server(handler)
    try:
        adapter = OpenAICompatibleAdapter(base_url=f"http://127.0.0.1:{port}", model="m")
        # `generate` - генератор, поэтому оборачивать надо вычитку: обернутое создание
        # генератора не исполняет ни строчки его тела, и проверка прошла бы, ничего не
        # проверив. Это тот же урок, что и с проверкой на границе, только в интерфейсе.
        with pytest.raises(LLMResponseError, match="length"):
            "".join(adapter.generate("вопрос", stream=False))
    finally:
        server.shutdown()


def test_finish_reason_absent_is_tolerated() -> None:
    """Сервер, не присылающий `finish_reason`, не должен ломаться.

    Иначе проверка обрыва превратилась бы в требование к конкретной реализации: часть
    OpenAI-совместимых серверов это поле не отдаёт, и тогда нормальный ответ падал бы.
    """
    body = json.dumps({"choices": [{"message": {"content": "ok"}}]})
    handler = _make_handler({"/v1/chat/completions": (200, body)})
    server, port = _start_server(handler)
    try:
        adapter = OpenAICompatibleAdapter(base_url=f"http://127.0.0.1:{port}", model="m")
        assert "".join(adapter.generate("вопрос", stream=False)) == "ok"
    finally:
        server.shutdown()


def test_max_tokens_larger_than_context_window_is_refused(monkeypatch: Any) -> None:
    """`max_tokens` больше окна модели - ошибка конфигурации, а не молчаливый обрыв.

    `context_window` был объявлен в профиле и не читался нигде, поэтому у `max_tokens`
    не было ни одной точки сверки. Теперь сверка есть на сборке адаптера: подсчитать
    токены промпта без токенизатора можно только эвристикой по символам, а ложное
    срабатывание на кириллице хуже грубой, но однозначной ошибки.
    """
    from graphrag_proto.retrieval.adapters.factory import build_llm

    monkeypatch.setenv("LLM_ADAPTER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "http://llm:8080")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_MAX_TOKENS", "4096")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW", "2048")
    with pytest.raises(RuntimeError, match="больше окна модели"):
        build_llm()

    monkeypatch.setenv("LLM_MAX_TOKENS", "1024")
    assert build_llm() is not None


def test_extraction_temperature_is_zero_and_overridable(monkeypatch: Any) -> None:
    """Извлечение обязано быть детерминированным, а генерация - нет.

    `temperature` была одна на оба случая и задавалась из профиля (0.3). Для структурированного
    JSON это означало семплирование: выход модели на одном и том же корпусе гулял между
    прогонами (измерено на стенде: 15 против 214 связей), то есть результат зависел от
    случайности, а не от кода. Поэтому дефолт для стадии EXTRACT - 0, переопределяется
    явно и виден в точке вызова.
    """
    from graphrag_proto.ingestion_service.app import _extraction_temperature

    monkeypatch.delenv(EXTRACTION_LLM_TEMPERATURE_ENV, raising=False)
    assert _extraction_temperature() == 0.0, "извлечение по умолчанию недетерминировано"

    monkeypatch.setenv(EXTRACTION_LLM_TEMPERATURE_ENV, "0.7")
    assert _extraction_temperature() == 0.7

    monkeypatch.setenv(EXTRACTION_LLM_TEMPERATURE_ENV, "тепло")
    with pytest.raises(RuntimeError, match="не число"):
        _extraction_temperature()


def test_seed_reaches_request_body(monkeypatch: Any) -> None:
    """`seed` передаётся в теле запроса, а не хранится в адаптере молча.

    Нужен ровно при ненулевой температуре: при `temperature = 0` жадный декодер
    детерминирован и seed ничего не добавляет.
    """
    seen: list[dict[str, Any]] = []

    def _handler_for(seen: list[dict[str, Any]]):
        class _H(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                seen.append(json.loads(self.rfile.read(length).decode("utf-8")))
                payload = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        return _H

    server, port = _start_server(_handler_for(seen))
    try:
        without_seed = OpenAICompatibleAdapter(base_url=f"http://127.0.0.1:{port}", model="m")
        "".join(without_seed.generate("вопрос", stream=False))
        with_seed = OpenAICompatibleAdapter(
            base_url=f"http://127.0.0.1:{port}", model="m", seed=4242
        )
        "".join(with_seed.generate("вопрос", stream=False))
    finally:
        server.shutdown()

    assert "seed" not in seen[0], "seed без явной настройки не должен попадать в запрос"
    assert seen[1]["seed"] == 4242


def test_llm_model_must_be_declared(monkeypatch, tmp_path):
    """Имя модели приходит из профиля или env; своего дефолта у кода нет.

    Проверяется то, что стоило дороже всего: LLM-сервер принимает в поле `model` любое
    значение и отдаёт загруженную модель, поэтому угаданное имя не даёт ошибки, а тихо
    работает. Значит отсутствие объявления обязано падать, а не подставлять 7B.
    """
    import pytest

    from graphrag_proto.retrieval.adapters.factory import build_llm

    profile = tmp_path / "namespaces.yaml"
    profile.write_text(
        'llm:\n  base_url: "http://from-profile:8080"\n  model: "from-profile"\n'
        '  temperature: 0.7\n  max_tokens: 111\n  timeout_s: 42\n',
        encoding="utf-8",
    )
    for name in (
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_TEMPERATURE",
        "LLM_MAX_TOKENS",
        "LLM_TIMEOUT_S",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("NAMESPACES_PATH", str(profile))
    monkeypatch.setenv("LLM_ADAPTER", "openai")
    llm = build_llm()
    assert llm._model == "from-profile"
    assert llm._base_url == "http://from-profile:8080"
    assert llm._max_tokens == 111

    # env переопределяет профиль: стенд объявляет свою модель явно.
    monkeypatch.setenv("LLM_MODEL", "from-env")
    assert build_llm()._model == "from-env"

    # Профиль без `model` - ошибка конфигурации, а не зашитое имя.
    without_model = tmp_path / "without_model.yaml"
    without_model.write_text(
        'llm:\n  base_url: "http://x:8080"\n  temperature: 0.7\n'
        '  max_tokens: 111\n  timeout_s: 42\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("NAMESPACES_PATH", str(without_model))
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="LLM_MODEL"):
        build_llm()

    # Совсем нет профиля и нет env - тоже ошибка, а не зашитое имя.
    monkeypatch.setenv("NAMESPACES_PATH", str(tmp_path / "absent.yaml"))
    with pytest.raises(RuntimeError, match="обязана объявляться явно"):
        build_llm()