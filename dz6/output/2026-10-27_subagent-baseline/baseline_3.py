"""API-тесты для эндпоинта POST /users.

Конфигурация через переменные окружения:
    API_BASE_URL — базовый URL сервиса (по умолчанию http://localhost:8000)
    API_TIMEOUT  — таймаут HTTP-запроса в секундах (по умолчанию 5)

Запуск:
    API_BASE_URL=http://localhost:8000 pytest -v
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any, Callable, Iterator

import pytest
import requests

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
API_TIMEOUT = float(os.getenv("API_TIMEOUT", "5"))

USERS_PATH = "/users"

# Ключи, в которых API обычно возвращает описание ошибки.
ERROR_KEYS = ("error", "detail", "message", "errors")


# --------------------------------------------------------------------------- #
# Клиент API
# --------------------------------------------------------------------------- #
class ApiClient:
    """Обёртка над requests.Session с таймаутом и обработкой сетевых ошибок."""

    def __init__(self, base_url: str = API_BASE_URL, timeout: float = API_TIMEOUT) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Выполнить запрос, превратив сетевые сбои в понятные падения теста."""
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", self.timeout)
        try:
            return self._session.request(method, url, **kwargs)
        except requests.Timeout:
            pytest.fail(f"{method} {url}: превышен таймаут {self.timeout}s")
        except requests.ConnectionError:
            pytest.fail(f"{method} {url}: сервис недоступен по адресу {self.base_url}")
        except requests.RequestException as exc:  # остальные сетевые/протокольные ошибки
            pytest.fail(f"{method} {url}: ошибка запроса — {exc}")

    def create_user(self, payload: dict[str, Any]) -> requests.Response:
        """Создать пользователя."""
        return self.request("POST", USERS_PATH, json=payload)

    def delete_user(self, user_id: Any) -> requests.Response:
        """Удалить пользователя (используется для очистки тестовых данных)."""
        return self.request("DELETE", f"{USERS_PATH}/{user_id}")

    def close(self) -> None:
        self._session.close()


# --------------------------------------------------------------------------- #
# Вспомогательные функции
# --------------------------------------------------------------------------- #
def json_body(response: requests.Response) -> dict[str, Any]:
    """Проверить, что ответ — JSON-объект, и вернуть его."""
    content_type = response.headers.get("Content-Type", "")
    assert content_type.startswith("application/json"), (
        f"Ожидался JSON, получен Content-Type={content_type!r}, "
        f"тело: {response.text[:200]!r}"
    )

    try:
        body = response.json()
    except ValueError as exc:
        raise AssertionError(f"Тело ответа не является валидным JSON: {response.text[:200]!r}") from exc

    assert isinstance(body, dict), (
        f"Ожидался JSON-объект, получен {type(body).__name__}: {body!r}"
    )
    return body


def unique_email(name: str, domain: str = "example.com") -> str:
    """Сгенерировать уникальный e-mail, чтобы тесты были изолированы друг от друга."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "user"
    return f"{slug}-{uuid.uuid4().hex[:8]}@{domain}"


# --------------------------------------------------------------------------- #
# Фикстуры
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def api_base_url() -> str:
    """Базовый URL сервиса из конфигурации."""
    return API_BASE_URL


@pytest.fixture()
def api(api_base_url: str) -> Iterator[ApiClient]:
    """HTTP-клиент API; соединение закрывается после теста."""
    client = ApiClient(base_url=api_base_url)
    yield client
    client.close()


@pytest.fixture()
def created_user_ids(api: ApiClient) -> Iterator[list[Any]]:
    """Список созданных в тесте пользователей, удаляемых по завершении теста."""
    user_ids: list[Any] = []
    yield user_ids

    for user_id in user_ids:
        response = api.delete_user(user_id)
        # Эндпоинт удаления не входит в тестируемый контракт: 404/405/501 допустимы.
        if response.status_code >= 500:
            pytest.fail(
                f"Не удалось очистить пользователя {user_id!r}: "
                f"HTTP {response.status_code}, тело: {response.text[:200]!r}"
            )


@pytest.fixture()
def user_payload() -> Callable[..., dict[str, Any]]:
    """Фабрика валидных payload'ов с уникальным e-mail по умолчанию."""

    def _make(name: str, email: str | None = None) -> dict[str, Any]:
        return {"name": name, "email": email or unique_email(name)}

    return _make


# --------------------------------------------------------------------------- #
# Позитивные сценарии
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name",
    ["Alice", "Bob", "Charlie"],
    ids=["alice", "bob", "charlie"],
)
def test_create_user_returns_created_user(
    api: ApiClient,
    created_user_ids: list[Any],
    user_payload: Callable[..., dict[str, Any]],
    name: str,
) -> None:
    """POST /users с валидными данными создаёт пользователя и возвращает его поля."""
    payload = user_payload(name)

    response = api.create_user(payload)

    assert response.status_code == 201, f"Тело ответа: {response.text[:200]!r}"

    body = json_body(response)
    assert set(body) >= {"id", "name", "email"}, f"Неожиданная структура ответа: {body!r}"
    assert body["name"] == payload["name"]
    assert body["email"] == payload["email"]
    assert body["id"] is not None

    created_user_ids.append(body["id"])


def test_create_users_generates_distinct_ids(
    api: ApiClient,
    created_user_ids: list[Any],
    user_payload: Callable[..., dict[str, Any]],
) -> None:
    """Два последовательных создания возвращают разных пользователей с разными id."""
    first = api.create_user(user_payload("Alice"))
    second = api.create_user(user_payload("Alice"))

    assert first.status_code == 201
    assert second.status_code == 201

    first_body = json_body(first)
    second_body = json_body(second)

    assert first_body["id"] != second_body["id"]
    assert first_body["email"] != second_body["email"]

    created_user_ids.extend([first_body["id"], second_body["id"]])


# --------------------------------------------------------------------------- #
# Негативные сценарии
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        pytest.param({"name": "Test", "email": "bad-email"}, "некорректный e-mail", id="invalid-email"),
        pytest.param({"email": "test@example.com"}, "отсутствует name", id="missing-name"),
        pytest.param({"name": "Test"}, "отсутствует email", id="missing-email"),
    ],
)
def test_create_user_rejects_invalid_payload(
    api: ApiClient,
    payload: dict[str, Any],
    reason: str,
) -> None:
    """POST /users с невалидными данными отвечает 400 и описанием ошибки."""
    response = api.create_user(payload)

    assert response.status_code == 400, (
        f"Ожидался 400 ({reason}), получен {response.status_code}: {response.text[:200]!r}"
    )

    body = response.json()
    assert isinstance(body, dict), f"Ожидался JSON-объект с ошибкой, получен {body!r}"
    assert any(key in body for key in ERROR_KEYS), (
        f"В ответе нет описания ошибки ({', '.join(ERROR_KEYS)}): {body!r}"
    )
