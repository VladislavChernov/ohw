import pytest
import requests

BASE_URL = "http://localhost:8000"


def test_create_user():
    payload = {"name": "Alice", "email": "alice@example.com"}
    response = requests.post(f"{BASE_URL}/users", json=payload)
    assert response.status_code == 201
    assert response.json()["name"] == "Alice"


def test_create_user_other_name():
    payload = {"name": "Bob", "email": "bob@example.com"}
    response = requests.post(f"{BASE_URL}/users", json=payload)
    assert response.status_code == 201
    assert response.json()["name"] == "Bob"


def test_create_user_new_new_name():
    payload = {"name": "Charlie", "email": "charlie@example.com"}
    response = requests.post(f"{BASE_URL}/users", json=payload)
    assert response.status_code == 201
    assert response.json()["name"] == "Charlie"


def test_create_user_invalid_email():
    payload = {"name": "Test", "email": "bad-email"}
    response = requests.post(f"{BASE_URL}/users", json=payload)
    assert response.status_code == 400


def test_create_user_missing_name():
    payload = {"email": "test@example.com"}
    response = requests.post(f"{BASE_URL}/users", json=payload)
    assert response.status_code == 400
