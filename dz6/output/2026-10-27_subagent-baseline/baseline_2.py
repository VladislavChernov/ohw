from __future__ import annotations

import os
import uuid
import warnings
from collections.abc import Callable, Iterator
from typing import Any, Mapping, Optional
from urllib.parse import quote

import pytest
import requests

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
API_TIMEOUT = float(os.getenv("API_TIMEOUT", "5.0"))

TEST_EMAIL_DOMAIN = os.getenv("TEST_EMAIL_DOMAIN", "example.com")
TEST_USER_NAMES = tuple(
    name.strip()
    for name in os.getenv("TEST_USER_NAMES", "Alice,Bob,Charlie").split(",")
    if name.strip()
)
INVALID_EMAIL = os.getenv("TEST_INVALID_EMAIL", "bad-email")
MISSING_NAME_EMAIL = os.getenv(
    "TEST_MISSING_NAME_EMAIL",
    f"missing-name@{TEST_EMAIL_DOMAIN}",
)

CLEANUP_OK_STATUSES = {200, 202, 204, 404}


class ApiClient:
    """Thin HTTP client for the users API with explicit network-error handling."""

    def __init__(
        self,
        base_url: str,
        timeout: float,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def close(self) -> None:
        """Release the underlying HTTP session."""
        self._session.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Send a request and fail with a clear message on network errors."""
        url = f"{self.base_url}{path}"
        try:
            return self._session.request(
                method,
                url,
                timeout=self.timeout,
                **kwargs,
            )
        except requests.Timeout as exc:
            pytest.fail(
                f"Request timed out after {self.timeout}s: "
                f"{method} {url}: {exc}"
            )
        except requests.ConnectionError as exc:
            pytest.fail(f"Could not connect to {url}: {exc}")
        except requests.RequestException as exc:
            pytest.fail(f"Request failed: {method} {url}: {exc}")

    def create_user(self, payload: Mapping[str, Any]) -> requests.Response:
        """Create a user via POST /users."""
        return self._request("POST", "/users", json=dict(payload))

    def delete_user(
        self,
        user_id: Any,
        *,
        strict: bool = True,
    ) -> Optional[requests.Response]:
        """Delete a user.

        Non-strict mode is used by cleanup: network errors are reported as
        warnings instead of failing the test during teardown.
        """
        path = f"/users/{quote(str(user_id), safe='')}"

        if strict:
            return self._request("DELETE", path)

        try:
            return self._session.delete(
                f"{self.base_url}{path}",
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            warnings.warn(
                f"Failed to clean up user {user_id!r}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            return None


def _assert_json_response(response: requests.Response) -> Any:
    """Assert that the response is JSON and return its decoded body."""
    content_type = response.headers.get("Content-Type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()

    assert media_type == "application/json" or media_type.endswith("+json"), (
        f"Expected a JSON Content-Type, got {content_type!r}. "
        f"Body: {response.text!r}"
    )

    try:
        return response.json()
    except ValueError as exc:
        pytest.fail(f"Response body is not valid JSON: {response.text!r} ({exc})")


def _assert_json_error(
    response: requests.Response,
    expected_status: int = 400,
) -> Mapping[str, Any]:
    """Assert an error status and a non-empty JSON error payload."""
    assert response.status_code == expected_status, (
        f"Expected HTTP {expected_status}, got {response.status_code}: "
        f"{response.text!r}"
    )

    body = _assert_json_response(response)
    assert isinstance(body, dict), (
        f"Expected a JSON object, got {type(body).__name__}"
    )
    assert body, "Error response body must not be empty"

    error_fields = {"error", "detail", "message", "errors"}
    assert any(key in body for key in error_fields), (
        f"Expected an error description in the response, got keys: "
        f"{sorted(body.keys())}"
    )

    return body


def _unique_email(name: str) -> str:
    """Build a unique e-mail address to isolate parallel and repeated runs."""
    local_part = "".join(char for char in name.lower() if char.isalnum()) or "user"
    return f"{local_part}.{uuid.uuid4().hex}@{TEST_EMAIL_DOMAIN}"


@pytest.fixture(scope="session")
def api_client() -> Iterator[ApiClient]:
    """Provide a shared API client with a bounded request timeout."""
    client = ApiClient(BASE_URL, API_TIMEOUT)
    yield client
    client.close()


@pytest.fixture
def user_payload_factory() -> Callable[[str], dict[str, str]]:
    """Return a factory that builds unique, valid user payloads."""

    def make_payload(name: str) -> dict[str, str]:
        return {"name": name, "email": _unique_email(name)}

    return make_payload


@pytest.fixture
def created_users(api_client: ApiClient) -> Iterator[list[Any]]:
    """Track users created by a test and delete them during teardown."""
    user_ids: list[Any] = []
    yield user_ids

    cleanup_errors: list[str] = []
    for user_id in reversed(user_ids):
        response = api_client.delete_user(user_id, strict=False)

        if response is not None and response.status_code not in CLEANUP_OK_STATUSES:
            cleanup_errors.append(
                f"user {user_id!r}: HTTP {response.status_code}"
            )

    if cleanup_errors:
        warnings.warn(
            "Could not clean up all test users: " + "; ".join(cleanup_errors),
            RuntimeWarning,
            stacklevel=2,
        )


@pytest.mark.parametrize("name", TEST_USER_NAMES)
def test_create_user_returns_created_resource(
    api_client: ApiClient,
    user_payload_factory: Callable[[str], dict[str, str]],
    created_users: list[Any],
    name: str,
) -> None:
    """A valid payload creates a user and returns the expected JSON structure."""
    payload = user_payload_factory(name)
    response = api_client.create_user(payload)

    assert response.status_code == 201, (
        f"Expected HTTP 201 for {payload!r}, got {response.status_code}: "
        f"{response.text!r}"
    )

    body = _assert_json_response(response)
    assert isinstance(body, dict), (
        f"Expected a JSON object, got {type(body).__name__}"
    )

    user_id = body.get("id")
    if user_id is not None:
        created_users.append(user_id)

    assert {"id", "name", "email"} <= set(body), (
        f"Missing user fields, got: {sorted(body.keys())}"
    )
    assert body["name"] == payload["name"]
    assert body["email"] == payload["email"]
    assert isinstance(user_id, (int, str)) and not isinstance(user_id, bool), (
        f"User id must be a non-empty string or integer, got: {user_id!r}"
    )
    assert str(user_id).strip(), "User id must not be empty"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {"name": "Test", "email": INVALID_EMAIL},
            id="invalid-email",
        ),
        pytest.param(
            {"email": MISSING_NAME_EMAIL},
            id="missing-name",
        ),
    ],
)
def test_create_user_rejects_invalid_payload(
    api_client: ApiClient,
    payload: Mapping[str, Any],
) -> None:
    """Invalid user data is rejected with HTTP 400 and a JSON error payload."""
    response = api_client.create_user(payload)
    _assert_json_error(response)
