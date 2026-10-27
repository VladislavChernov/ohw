import pytest
import requests

BASE_URL = "http://localhost:8000"
PARAMS = {
    "create_user": {"name": "Alice", "email": "alice@example.com"},
    "create_user_other_name": {"name": "Bob", "email": "bob@example.com"},
    "create_user_new_new_name": {"name": "Charlie", "email": "charlie@example.com"},
    "create_user_invalid_email": {"name": "Test", "email": "bad-email"},
    "create_user_missing_name": {"email": "test@example.com"},
}

@pytest.fixture
def api_client():
    return requests.Session()

@pytest.fixture
def user_data():
    for name, data in PARAMS.items():
        yield name, data

@pytest.mark.parametrize("test_name, data", [(k, v) for k, v in PARAMS.items()])
def test_create_user(test_name, data, api_client):
    """
    Тестирование создания нового пользователя.
    """
    try:
        response = api_client.post(f"{BASE_URL}/users", json=data, timeout=5)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        pytest.fail(f"Network error: {e}")

    assert response.status_code == 201, f"Unexpected status code: {response.status_code}"
    json_response = response.json()
    assert json_response["name"] == data["name"], f"Name mismatch: expected {data['name']}, got {json_response['name']}"
    assert "id" in json_response, "User ID not returned"

    if test_name != "create_user_invalid_email":
        assert "email" in json_response, "Email not returned"
    else:
        assert "detail" in response.json(), "Invalid email response does not contain detail"

def test_create_user_missing_name(api_client):
    """
    Тестирование создания пользователя с недостающим именем.
    """
    try:
        response = api_client.post(f"{BASE_URL}/users", json={"email": "test@example.com"}, timeout=5)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        pytest.fail(f"Network error: {e}")

    assert response.status_code == 400, f"Unexpected status code: {response.status_code}"
    assert "name" in response.json(), "Field 'name' not in response"