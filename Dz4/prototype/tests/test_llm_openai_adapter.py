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
    _ExchangeLog,
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
    monkeypatch.setenv("LLM_CTX_SIZE", "2048")
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


def _exchange(tmp_path, monkeypatch, level: str, limit: str = "8000") -> Any:
    """Журнал обмена, включённый на уровень `level`, пишущий в файл."""
    log = tmp_path / "exchange.jsonl"
    monkeypatch.setenv("LLM_EXCHANGE_LOG", level)
    monkeypatch.setenv("LLM_EXCHANGE_LOG_FILE", str(log))
    monkeypatch.setenv("LLM_EXCHANGE_LOG_MAX_CHARS", limit)
    return _ExchangeLog()


def _read(path: Any) -> dict[str, Any]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1, lines
    return json.loads(lines[0])


def test_exchange_log_off_by_default_writes_nothing(tmp_path, monkeypatch) -> None:
    """Молчание - это дефолт, и оно осознанное: полный обмен - это текст документа."""
    monkeypatch.delenv("LLM_EXCHANGE_LOG", raising=False)
    monkeypatch.delenv("LLM_EXCHANGE_LOG_FILE", raising=False)
    log = _ExchangeLog()

    log.record(model="m", stage="extract", stream=False, prompt="документ", response="ответ")

    assert log.level == 0
    assert not list(tmp_path.iterdir()), "выключенный журнал не должен создавать файлов"


def test_exchange_meta_level_has_numbers_but_no_content(tmp_path, monkeypatch) -> None:
    """`meta` отвечает на вопрос «сколько стоило», не раскрывая содержимое."""
    log = _exchange(tmp_path, monkeypatch, "meta")

    log.record(
        model="m",
        stage="extract",
        stream=False,
        prompt="текст чанка",
        response="ответ модели",
        usage={"prompt_tokens": 100, "completion_tokens": 20},
    )

    record = _read(tmp_path / "exchange.jsonl")
    assert record["usage"] == {"prompt_tokens": 100, "completion_tokens": 20}
    assert record["prompt_chars"] == len("текст чанка")
    assert record["response_chars"] == len("ответ модели")
    assert "prompt" not in record
    assert "response" not in record
    assert "truncated" not in record


def test_exchange_full_level_keeps_content_and_marks_truncation(tmp_path, monkeypatch) -> None:
    """Усечение обязано быть помечено: иначе следующая обработка примет обрезку за полное."""
    log = _exchange(tmp_path, monkeypatch, "full", limit="10")

    log.record(
        model="m",
        stage="extract",
        stream=False,
        prompt="длинный промпт извлечения",
        response="длинный ответ модели",
        labels={"source_url": "s://a", "chunk_id": "chk:1"},
    )

    record = _read(tmp_path / "exchange.jsonl")
    assert record["prompt"].startswith("длинный пр")
    assert "усечено" in record["prompt"]
    assert record["truncated"] == {"system": False, "prompt": True, "response": True}
    # Полная длина остаётся рядом с усечённой, иначе потерян сам масштаб потери.
    assert record["prompt_chars"] == len("длинный промпт извлечения")
    assert record["labels"] == {"source_url": "s://a", "chunk_id": "chk:1"}


def test_exchange_limit_zero_means_no_truncation(tmp_path, monkeypatch) -> None:
    """Для разбора «что модель пишет» усечение недопустимо, поэтому 0 - это «не резать»."""
    log = _exchange(tmp_path, monkeypatch, "full", limit="0")

    log.record(model="m", stage="extract", stream=False, prompt="x" * 5000, response="y" * 5000)

    record = _read(tmp_path / "exchange.jsonl")
    assert record["truncated"] == {"system": False, "prompt": False, "response": False}
    assert len(record["response"]) == 5000


def test_exchange_records_failure_with_raw_response(tmp_path, monkeypatch) -> None:
    """Именно этот случай и есть смысл журнала: обрыв виден, и видно, что модель наговорила."""
    payload = {
        "choices": [
            {
                "message": {"content": '{"entities": ['},
                "finish_reason": "length",
            }
        ],
        "usage": {"prompt_tokens": 900, "completion_tokens": 4096},
    }
    server, port = _start_server(
        _make_handler({"/v1/chat/completions": (200, json.dumps(payload))})
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Адаптер сам подхватывает настройки журнала из env - присваивать ему `_exchange`
    # вручную не нужно, иначе тест проверял бы не то подключение, которым пользуется сервис.
    _exchange(tmp_path, monkeypatch, "full")
    adapter = OpenAICompatibleAdapter(base_url=f"http://127.0.0.1:{port}", model="m")
    try:
        with pytest.raises(LLMResponseError):
            list(adapter.generate("промпт", system="система", stream=False, labels={"chunk_id": "chk:9"}))
    finally:
        server.shutdown()

    record = _read(tmp_path / "exchange.jsonl")
    assert record["error"] == "LLMResponseError"
    assert record["response"] == '{"entities": ['
    assert record["labels"] == {"chunk_id": "chk:9"}
    assert (record["usage"] or {}).get("completion_tokens") == 4096


def test_exchange_records_usage_on_success(tmp_path, monkeypatch) -> None:
    """Расход токенов попадает в журнал при УСПЕШНОМ вызове - это основной случай."""
    payload = {
        "choices": [{"message": {"content": '{"entities": []}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 812, "completion_tokens": 37, "total_tokens": 849},
    }
    server, port = _start_server(
        _make_handler({"/v1/chat/completions": (200, json.dumps(payload))})
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _exchange(tmp_path, monkeypatch, "full")
    adapter = OpenAICompatibleAdapter(base_url=f"http://127.0.0.1:{port}", model="m")
    try:
        text = "".join(adapter.generate("промпт", system="система", stream=False))
    finally:
        server.shutdown()

    assert text == '{"entities": []}'
    record = _read(tmp_path / "exchange.jsonl")
    assert record["usage"] == {"prompt_tokens": 812, "completion_tokens": 37, "total_tokens": 849}
    assert record["finish_reason"] is None or record["error"] is None


def test_exchange_survives_unwritable_path(tmp_path, monkeypatch) -> None:
    """Включённая диагностика не должна ронять джобы, ради которых включена."""
    monkeypatch.setenv("LLM_EXCHANGE_LOG", "full")
    monkeypatch.setenv("LLM_EXCHANGE_LOG_FILE", str(tmp_path / "нет" / "такого" / "x.jsonl"))
    log = _ExchangeLog()

    log.record(model="m", stage="extract", stream=False, prompt="p", response="r")


def test_exchange_response_level_keeps_document_text_out(tmp_path, monkeypatch) -> None:
    """Уровень `response` отвечает на вопрос «что модель выдаёт», не раздувая журнал текстом.

    В извлечении текст чанка лежит внутри промпта, поэтому «ответ без промпта» - это ровно
    «запись без текста документа». Отдельного «чанка» в журнале нет и не нужно: лишний
    дубль текста только раздувает файл и создаёт второй источник правды о документе.
    """
    log = _exchange(tmp_path, monkeypatch, "response", limit="0")

    log.record(
        model="m",
        stage="extract",
        stream=False,
        system="системная инструкция",
        prompt="текст чанка документа",
        response="ответ модели",
        labels={"source_url": "s://a"},
    )

    record = _read(tmp_path / "exchange.jsonl")
    assert record["response"] == "ответ модели"
    assert "prompt" not in record
    assert "system" not in record
    # Длина промпта остаётся: это число полезно само по себе и текста не раскрывает.
    assert record["prompt_chars"] == len("текст чанка документа")
    assert record["truncated"] == {"response": False}
    assert record["labels"] == {"source_url": "s://a"}
    assert "текст чанка" not in json.dumps(record, ensure_ascii=False)


def test_exchange_level_names_are_closed_set(tmp_path, monkeypatch) -> None:
    """Неизвестное значение молча не превращается в дефолт: журнал включается и выключается
    только названными уровнями, и опечатка не должна приводить к неожиданному «всё пишем»."""
    monkeypatch.setenv("LLM_EXCHANGE_LOG", "FULL-ОПЕЧАТКА")
    assert _ExchangeLog().level == 0