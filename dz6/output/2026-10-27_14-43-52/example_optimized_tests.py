import os
import pytest
import requests

BASE_URL = os.getenv('BASE_URL', 'http://localhost:8000')

@pytest.fixture
def api_client():
    return requests.Session()

@pytest.fixture
def user_data():
    yield {"name": "Alice", "email": "alice@example.com"}

@pytest.fixture
def invalid_user_data():
    yield {"name": "Test", "email": "bad-email"}

@pytest.fixture
def missing_name_data():
    yield {"email": "test@example.com"}

def test_create_user(api_client, user_data):
    """
    Test creating a valid user.
    """
    response = api_client.post(f"{BASE_URL}/users", json=user_data)
    assert response.status_code == 201
    assert response.headers['Content-Type'] == 'application/json'
    assert response.json()['name'] == user_data['name']

def test_create_user_invalid_email(api_client, invalid_user_data):
    """
    Test creating a user with an invalid email.
    """
    response = api_client.post(f"{BASE_URL}/users", json=invalid_user_data)
    assert response.status_code == 400
    assert response.headers['Content-Type'] == 'application/json'
    assert 'email' in response.json()
    assert response.json()['email'] == ['Invalid email address']

def test_create_user_missing_name(api_client, missing_name_data):
    """
    Test creating a user with missing name.
    """
    response = api_client.post(f"{BASE_URL}/users", json=missing_name_data)
    assert response.status_code == 400
    assert response.headers['Content-Type'] == 'application/json'
    assert 'name' in response.json()
    assert response.json()['name'] == ['Missing data for required field.', 'Name is required']

def test_create_user_other_name(api_client, user_data):
    """
    Test creating another valid user with different name.
    """
    user_data['name'] = 'Bob'
    user_data['email'] = 'bob@example.com'
    response = api_client.post(f"{BASE_URL}/users", json=user_data)
    assert response.status_code == 201
    assert response.headers['Content-Type'] == 'application/json'
    assert response.json()['name'] == user_data['name']

def test_create_user_new_new_name(api_client, user_data):
    """
    Test creating another valid user with different name.
    """
    user_data['name'] = 'Charlie'
    user_data['email'] = 'charlie@example.com'
    response = api_client.post(f"{BASE_URL}/users", json=user_data)
    assert response.status_code == 201
    assert response.headers['Content-Type'] == 'application/json'
    assert response.json()['name'] == user_data['name']