"""API tests for the ``/users`` endpoint.

The suite covers user creation (happy path) and payload validation, keeps tests
isolated by removing created entities afterwards, and reports network problems
as explicit failures instead of opaque tracebacks.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Callable, Iterator

import pytest
import requests

# --------------------------------------------------------------------------- #
# Configuration (overridable through environment variables)
# --------------------------------------------------------------------------- #
BASE_URL: str = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT: float = float(os.getenv("API_TIMEOUT", "5"))
USERS_PATH: str = "/users"

VALID_USER_NAMES: list[str] = ["Alice", "Bob", "Charlie"]

JSON_CONTENT_TYPE = "application/json"


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #
class ApiClient:
    """Thin wrapper around :class:`requests.Session` with explicit error handling."""

    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Accept": JSON_CONTENT_TYPE})

    def close(self) -> None:
        """Release the underlying connection pool."""
        self._session.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        raise_on_error: bool = True,
        **kwargs: Any,
    ) -> requests.Response | None:
        """Perform an HTTP request, converting network errors into test failures.

        Args:
            method: HTTP verb.
            path: Path relative to the configured base URL.
            raise_on_error: When ``True`` (default), network problems fail the
                test; when ``False``, ``None`` is returned instead (used by
                teardown code).
            **kwargs: Passed straight to :meth:`requests.Session.request`.

        Returns:
            The response object, or ``None`` if the request failed and
            ``raise_on_error`` is ``False``.
        """
        url = f"{self.base_url}{path}"
        try:
            return self._session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.Timeout as exc:
            if not raise_on_error:
                return None
            pytest.fail(f"Request {method} {url} timed out after {self.timeout}s: {exc}")
        except requests.ConnectionError as exc:
            if not raise_on_error:
                return None
            pytest.fail(f"Cannot connect to {url}: {exc}")
        except requests.RequestException as exc:  # pragma: no cover - safety net
            if not raise_on_error:
                return None
            pytest.fail(f"Request {method} {url} failed: {exc}")
        return None  # pragma: no cover - unreachable, keeps type checkers happy

    def post(self, path: str, **kwargs: Any) -> requests.Response:
        """Send a POST request and return the response."""
        response = self.request("POST", path, **kwargs)
        assert response is not None
        return response

    def delete(self, path: str) -> bool:
        """Best-effort DELETE used for cleanup; never raises on network errors."""
        response = self.request("DELETE", path, raise_on_error=False)
        return response is not None and response.status_code in (200, 202, 204)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def assert_json_response(response: requests.Response) -> Any:
    """Assert that a response is JSON and return the decoded payload."""
    content_type = response.headers.get("Content-Type", "")
    assert content_type.startswith(JSON_CONTENT_TYPE), (
        f"Expected JSON response, got Content-Type={content_type!r} "
        f"(status={response.status_code}, body={response.text[:200]!r})"
    )
    try:
        return response.json()
    except ValueError as exc:
        pytest.fail(f"Response body is not valid JSON: {exc}\nBody: {response.text[:500]!r}")


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def api_client() -> Iterator[ApiClient]:
    """Session-scoped API client reusing a single HTTP connection pool."""
    client = ApiClient(BASE_URL, TIMEOUT)
    yield client
    client.close()


@pytest.fixture
def unique_email() -> str:
    """Return an email address that is unique for the current test run."""
    return f"qa-{uuid.uuid4().hex}@example.com"


@pytest.fixture
def track_user(api_client: ApiClient) -> Iterator[Callable[[Any], None]]:
    """Register created users and delete them during teardown.

    Yields:
        A callable accepting a user identifier that should be removed after
        the test completes.
    """
    created_ids: list[Any] = []

    def register(user_id: Any) -> None:
        created_ids.append(user_id)

    yield register

    for user_id in created_ids:
        api_client.delete(f"{USERS_PATH}/{user_id}")


# --------------------------------------------------------------------------- #
# Test data builders for negative cases
# --------------------------------------------------------------------------- #
def build_payload_with_invalid_email(email: str) -> dict[str, str]:
    """Return a payload whose ``email`` value violates the expected format."""
    return {"name": "Test", "email": "bad-email"}


def build_payload_without_name(email: str) -> dict[str, str]:
    """Return a payload with the required ``name`` field omitted."""
    return {"email": email}


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", VALID_USER_NAMES)
def test_create_user_returns_created_user(
    api_client: ApiClient,
    unique_email: str,
    track_user: Callable[[Any], None],
    name: str,
) -> None:
    """A valid payload creates a user and echoes the submitted data back."""
    payload = {"name": name, "email": unique_email}

    response = api_client.post(USERS_PATH, json=payload)

    assert response.status_code == 201, response.text
    body = assert_json_response(response)
    assert isinstance(body, dict), f"Expected a JSON object, got {type(body).__name__}"
    assert {"id", "name", "email"} <= body.keys(), f"Unexpected response keys: {sorted(body)}"
    assert body["name"] == name
    assert body["email"] == unique_email
    assert body["id"] not in (None, ""), "Created user must have a non-empty id"

    track_user(body["id"])


@pytest.mark.parametrize(
    ("build_payload", "expected_status"),
    [
        pytest.param(build_payload_with_invalid_email, 400, id="invalid-email-format"),
        pytest.param(build_payload_without_name, 400, id="missing-name"),
    ],
)
def test_create_user_rejects_invalid_payload(
    api_client: ApiClient,
    unique_email: str,
    build_payload: Callable[[str], dict[str, str]],
    expected_status: int,
) -> None:
    """Invalid payloads are rejected and no user is created."""
    payload = build_payload(unique_email)

    response = api_client.post(USERS_PATH, json=payload)

    assert response.status_code == expected_status, response.text
    body = assert_json_response(response)
    assert isinstance(body, dict), f"Expected a JSON object, got {type(body).__name__}"
    assert body, "Error response body must not be empty"
