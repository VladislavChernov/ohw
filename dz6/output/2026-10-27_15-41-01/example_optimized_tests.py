import pytest
import requests

BASE_URL = "http://localhost:8000"

@pytest.fixture
def api_client():
    return requests.Session()

@pytest.fixture
def user_data():
    return [
        {"name": "Alice", "email": "alice@example.com"},
        {"name": "Bob", "email": "bob@example.com"},
        {"name": "Charlie", "email": "charlie@example.com"},
        {"name": "Test", "email": "bad-email"},
        {"email": "test@example.com"}
    ]

@pytest.fixture
def user_url():
    return f"{BASE_URL}/users"

def test_create_users(api_client, user_data, user_url):
    """
    Проверяет создание пользователей с различными данными.
    """
    for data in user_data:
        response = api_client.post(user_url, json=data)
        assert response.status_code == 201
        response_data = response.json()
        assert response_data["name"] == data["name"]
        assert response_data["email"] == data["email"]

@pytest.mark.parametrize("invalid_data", [
    {"name": "Test", "email": "bad-email"},
    {"email": "test@example.com"}
])
def test_create_user_invalid_data(api_client, invalid_data, user_url):
    """
    Проверяет создание пользователя с некорректными данными.
    """
    response = api_client.post(user_url, json=invalid_data)
    assert response.status_code == 400

def test_create_user_missing_data(api_client, user_url):
    """
    Проверяет создание пользователя без обязательных данных.
    """
    response = api_client.post(user_url, json={"name": "Test"})
    assert response.status_code == 400

def test_create_user_timeout(api_client, user_url):
    """
    Проверяет временную задержку при создании пользователя.
    """
    response = api_client.post(user_url, json={"name": "Test", "email": "test@example.com"})
    assert response.status_code == 201, response.text

def test_create_user_connection_error(api_client, user_url):
    """
    Проверяет обработку ошибки соединения при создании пользователя.
    """
    try:
        response = api_client.post("http://invalid-url/users", json={"name": "Test", "email": "test@example.com"})
    except requests.exceptions.RequestException as e:
        assert "Connection error" in str(e)