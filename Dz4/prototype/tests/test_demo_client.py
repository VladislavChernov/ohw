from __future__ import annotations

import pytest
import requests

from graphrag_proto.demo_ui.client import (
    DEFAULT_SLIDER_DEPTH,
    DEPTH_MAX,
    DEPTH_MIN,
    DemoClient,
    Settings,
    _raise_for_status,
    parse_sse,
)


def _response(status_code: int, body: str) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = body.encode("utf-8")
    return response


def test_parse_sse_event_pairs() -> None:
    lines = [
        "event: status",
        'data: {"stage": "retrieve"}',
        "",
        "event: token",
        'data: {"text": "привет"}',
        "",
        'event: done',
        'data: {"text": "итог", "sources": [], "generation_time_s": 0.5}',
        "",
    ]
    events = list(parse_sse(lines))
    assert events[0] == ("status", {"stage": "retrieve"})
    assert events[1] == ("token", {"text": "привет"})
    assert events[2][0] == "done"
    assert events[2][1]["text"] == "итог"
    assert events[2][1]["generation_time_s"] == 0.5


def test_parse_sse_comment_and_default_event_type() -> None:
    events = list(parse_sse([": keepalive", "data: {}", ""]))
    assert events == [("message", {})]


def test_parse_sse_keepalive_comments_only() -> None:
    assert list(parse_sse([": keepalive", ": ping", ""])) == []


def test_parse_sse_unknown_event_type_passthrough() -> None:
    events = list(parse_sse(["event: custom", 'data: {"a": 1}', ""]))
    assert events == [("custom", {"a": 1})]


def test_raise_for_status_error_detail() -> None:
    response = _response(422, '{"detail": "doc_type=\'bin\' не поддерживается"}')
    with pytest.raises(RuntimeError, match="422"):
        _raise_for_status(response)


def test_upload_builds_expected_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, headers: dict[str, str], json: object, timeout: object) -> requests.Response:
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _response(202, '{"job_id": "j1", "status": "queued"}')

    monkeypatch.setattr(requests, "post", fake_post)
    client = DemoClient(Settings(ingestion_url="http://ing:8002", api_key="k123"))
    body = client.upload_document("текст документа", "txt", "doc", "s://demo/a.txt")
    assert body["job_id"] == "j1"
    assert captured["url"] == "http://ing:8002/api/v1/ingestion/documents"
    assert captured["headers"] == {"X-API-Key": "k123"}
    assert captured["json"] == {
        "source_url": "s://demo/a.txt",
        "domain": "doc",
        "doc_type": "txt",
        "content": "текст документа",
    }


def test_submit_query_requires_task_id(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, headers: dict[str, str], json: object, timeout: object) -> requests.Response:
        return _response(202, '{"status": "accepted"}')

    monkeypatch.setattr(requests, "post", fake_post)
    client = DemoClient(Settings(query_url="http://query:8000", api_key="k"))
    with pytest.raises(RuntimeError, match="task_id"):
        client.submit_query("вопрос", "doc")


def test_submit_query_payload_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, headers: dict[str, str], json: object, timeout: object) -> requests.Response:
        captured["url"] = url
        captured["json"] = json
        return _response(202, '{"task_id": "t1"}')

    monkeypatch.setattr(requests, "post", fake_post)
    client = DemoClient(Settings(query_url="http://query:8000", api_key="k"))
    assert client.submit_query("вопрос про домен", "doc") == "t1"
    assert captured["url"] == "http://query:8000/query"
    assert captured["json"] == {"query": "вопрос про домен", "metadata": {"domain": "doc"}}


def test_submit_query_sends_max_depth_when_chosen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Выбранная глубина едет в запрос явным полем, а не «в metadata мимоходом»."""
    captured: dict[str, object] = {}

    def fake_post(url: str, headers: dict[str, str], json: object, timeout: object) -> requests.Response:
        captured["json"] = json
        return _response(202, '{"task_id": "t1"}')

    monkeypatch.setattr(requests, "post", fake_post)
    client = DemoClient(Settings(query_url="http://query:8000", api_key="k"))

    assert client.submit_query("вопрос", "doc", max_depth=5) == "t1"
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["max_depth"] == 5
    assert payload["metadata"] == {"domain": "doc"}


def test_submit_query_omits_max_depth_when_not_chosen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отсутствие значения - это «решает конфигурация», а не «глубина 0» и не «глубина None».

    Поле не должно уезжать в запрос вовсе: иначе конвейер примет его за намерение и
    заменит профиль значением по умолчанию из UI, то есть профиль перестанет решать.
    """
    captured: dict[str, object] = {}

    def fake_post(url: str, headers: dict[str, str], json: object, timeout: object) -> requests.Response:
        captured["json"] = json
        return _response(202, '{"task_id": "t1"}')

    monkeypatch.setattr(requests, "post", fake_post)
    client = DemoClient(Settings(query_url="http://query:8000", api_key="k"))

    client.submit_query("вопрос", "doc")
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert "max_depth" not in payload


def test_slider_bounds_come_from_the_cap_not_from_markup() -> None:
    """Границы ползунка - тот же потолок, что и у обхода.

    Второе число в UI означало бы второе место, где живёт предел, и именно это расхождение
    ползунок обязан устранять, а не воспроизводить.
    """
    from graphrag_proto.retrieval.adapters.base import MAX_EXPANSION_DEPTH

    assert DEPTH_MAX == MAX_EXPANSION_DEPTH
    assert DEPTH_MIN == 1
    assert DEPTH_MIN < DEFAULT_SLIDER_DEPTH <= DEPTH_MAX


def test_slider_default_matches_profiles() -> None:
    """Начальное положение ползунка не должно разойтись с профилями.

    Профили объявляют `retrieval.max_depth: 3`, и UI показывает 3. Если это число
    разъедется, человек будет стартовать с другого значения, чем система применяет по
    умолчанию, и расхождение обнаружится только в ответе. Тест читает настоящие профили,
    а не их копию.
    """
    from pathlib import Path

    import yaml

    profiles = Path(__file__).resolve().parents[1] / "domain_profiles"
    files = sorted(profiles.glob("domain_profile.*.yaml"))
    assert files, f"профили не найдены: {profiles}"
    for path in files:
        profile = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        declared = (profile.get("retrieval") or {}).get("max_depth")
        assert declared == DEFAULT_SLIDER_DEPTH, (
            f"{path.name}: retrieval.max_depth={declared!r}, а ползунок стартует с "
            f"{DEFAULT_SLIDER_DEPTH}"
        )


# --- обслуживание (add-operator-maintenance-controls) ---------------------------


def test_run_maintenance_builds_expected_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, headers: dict[str, str], json: object, timeout: object) -> requests.Response:
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _response(
            200,
            '{"job_id": "maintenance:abc", "domain": "it", "mode": "current", "skipped": false,'
            ' "planned_relations": 3, "removed_relations": 3,'
            ' "planned_nodes": 0, "removed_nodes": 0}',
        )

    monkeypatch.setattr(requests, "post", fake_post)
    client = DemoClient(Settings(ingestion_url="http://ing:8002", api_key="k123"))

    body = client.run_maintenance("it")

    assert captured["url"] == "http://ing:8002/api/v1/maintenance/orphan-cleanup"
    assert captured["headers"] == {"X-API-Key": "k123"}
    assert captured["json"] == {"domain": "it"}
    assert body["job_id"] == "maintenance:abc"
    assert body["removed_relations"] == 3
    assert body["skipped"] is False


def test_run_maintenance_reports_skipped_rather_than_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пропущенный проход и «удалять нечего» — разные результаты, и оба приходят кодом.

    Проверяется то, что клиент **не теряет** признак `skipped`: если бы он его отбросил,
    UI показал бы «готово» там, где уборка была запрещена политикой.
    """
    def fake_post(url: str, headers: dict[str, str], json: object, timeout: object) -> requests.Response:
        return _response(
            200,
            '{"job_id": "maintenance:z", "domain": "it", "mode": "archive", "skipped": true,'
            ' "planned_relations": 0, "removed_relations": 0,'
            ' "planned_nodes": 0, "removed_nodes": 0}',
        )

    monkeypatch.setattr(requests, "post", fake_post)
    client = DemoClient(Settings(ingestion_url="http://ing:8002", api_key="k"))

    body = client.run_maintenance("it")

    assert body["skipped"] is True
    assert body["removed_relations"] == 0


def test_maintenance_response_fields_exist_in_the_route() -> None:
    """Имена полей ответа зафиксированы по коду маршрута, а не придуманы.

    `run_orphan_cleanup` (`ingestion_service/retention_policy.py:126-143`) отдаёт именно эти
    ключи. Расхождение здесь означало бы, что UI читает поля, которых нет, и показывает
    пустые нули вместо подсчётов. Ключи берутся из тела функции, а не из докстринга:
    докстринг — это описание, а не контракт.
    """
    import inspect

    from graphrag_proto.ingestion_service import retention_policy

    body = inspect.getsource(retention_policy.run_orphan_cleanup)
    for key in ("mode", "skipped", "planned_relations", "removed_relations"):
        assert f'"{key}"' in body, f"маршрут не отдаёт {key}"
    # Идентификатор прохода — параметр функции, но не ключ возвращаемого факта: маршрут
    # добавляет его сам. Смешивать их нельзя, иначе «факт» станет привязан к вызову.
    assert '"job_id":' not in body
