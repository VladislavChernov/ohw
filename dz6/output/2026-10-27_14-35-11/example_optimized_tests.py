import pytest
import requests

BASE_URL = "http://localhost:8000"
TIMEOUT = 10

@pytest.fixture
def api_client():
    return requests.Session()

@pytest.fixture
def clean_data(api_client):
    yield
    response = api_client.get(f"{BASE_URL}/users")
    for user in response.json():
        api_client.delete(f"{BASE_URL}/users/{user['id']}")

@pytest.mark.usefixtures("clean_data")
def test_create_user(api_client):
    """
    Test creating a user with valid data and expected response.
    """
    payload = {"name": "Alice", "email": "alice@example.com"}
    response = api_client.post(f"{BASE_URL}/users", json=payload, timeout=TIMEOUT)
    assert response.status_code == 201, f"Status code is {response.status_code}"
    assert response.headers["Content-Type"] == "application/json"
    response_data = response.json()
    assert response_data["name"] == "Alice"
    assert response_data["email"] == "alice@example.com"

@pytest.mark.usefixtures("clean_data")
def test_create_user_invalid_email(api_client):
    """
    Test creating a user with invalid email and expected 400 status.
    """
    payload = {"name": "Test", "email": "bad-email"}
    response = api_client.post(f"{BASE_URL}/users", json=payload, timeout=TIMEOUT)
    assert response.status_code == 400, f"Status code is {response.status_code}"

@pytest.mark.usefixtures("clean_data")
def test_create_user_missing_name(api_client):
    """
    Test creating a user with missing name and expected 400 status.
    """
    payload = {"email": "test@example.com"}
    response = api_client.post(f"{BASE_URL}/users", json=payload, timeout=TIMEOUT)
    assert response.status_code == 400, f"Status code is {response.status_code}"

@pytest.mark.usefixtures("clean_data")
def test_create_user_other_name(api_client):
    """
    Test creating another user with different name and expected correct response.
    """
    payload = {"name": "Bob", "email": "bob@example.com"}
    response = api_client.post(f"{BASE_URL}/users", json=payload, timeout=TIMEOUT)
    assert response.status_code == 201, f"Status code is {response.status_code}"
    assert response.json()["name"] == "Bob"

@pytest.mark.usefixtures("clean_data")
def test_create_user_new_new_name(api_client):
    """
    Test creating a new user with a new name and expected correct response.
    """
    payload = {"name": "Charlie", "email": "charlie@example.com"}
    response = api_client.post(f"{BASE_URL}/users", json=payload, timeout=TIMEOUT)
    assert response.status_code == 201, f"Status code is {response.status_code}"
    assert response.json()["name"] == "Charlie"