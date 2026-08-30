import requests

# Base URL for the JSONPlaceholder service
BASE_URL = "https://jsonplaceholder.typicode.com"

# Test for listing posts
def test_list_posts():
    response = requests.get(f"{BASE_URL}/posts")
    assert response.status_code == 200
    assert "posts" in response.json()

# Test for getting a single post
def test_get_post():
    response = requests.get(f"{BASE_URL}/posts/1")
    assert response.status_code == 200
    assert "title" in response.json()
    assert "body" in response.json()

# Test for creating a post
def test_create_post():
    new_post = {
        "title": "Test Post",
        "body": "This is a test post.",
        "userId": 1
    }
    response = requests.post(f"{BASE_URL}/posts", json=new_post)
    assert response.status_code == 201
    assert response.json()["title"] == new_post["title"]
    assert response.json()["body"] == new_post["body"]
    assert response.json()["userId"] == new_post["userId"]

# Test for updating a post
def test_update_post():
    updated_post = {
        "id": 1,
        "title": "Updated Test Post",
        "body": "This is an updated test post.",
        "userId": 1
    }
    response = requests.put(f"{BASE_URL}/posts/1", json=updated_post)
    assert response.status_code == 200
    assert response.json()["title"] == updated_post["title"]
    assert response.json()["body"] == updated_post["body"]
    assert response.json()["userId"] == updated_post["userId"]

# Test for patching a post
def test_patch_post():
    patched_post = {
        "id": 1,
        "title": "Patched Test Post"
    }
    response = requests.patch(f"{BASE_URL}/posts/1", json=patched_post)
    assert response.status_code == 200
    assert response.json()["title"] == patched_post["title"]
    assert "body" in response.json()

# Test for deleting a post
def test_delete_post():
    response = requests.delete(f"{BASE_URL}/posts/1")
    assert response.status_code == 200
    assert response.json() == {}