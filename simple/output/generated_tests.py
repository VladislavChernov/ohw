import requests

def test_list_posts_returns_100_items():
    """
    Verify that listing posts returns 100 items.
    """
    response = requests.get('https://jsonplaceholder.typicode.com/posts')
    assert response.status_code == 200, "Unexpected status code"
    assert len(response.json()) == 100, "Expected 100 posts, got different number"

def test_get_single_post_has_correct_fields():
    """
    Verify that a single post has the expected fields.
    """
    response = requests.get('https://jsonplaceholder.typicode.com/posts/1')
    assert response.status_code == 200, "Unexpected status code"
    post = response.json()
    assert set(post.keys()) == {'userId', 'id', 'title', 'body'}, "Fields do not match expected schema"

def test_create_post_returns_new_post_with_id():
    """
    Verify that creating a new post returns a new post with a generated id.
    """
    new_post = {
        'title': 'Test Title',
        'body': 'Test Body',
        'userId': 1
    }
    response = requests.post('https://jsonplaceholder.typicode.com/posts', json=new_post)
    assert response.status_code == 201, "Unexpected status code"
    created_post = response.json()
    assert created_post['id'] == 101, "Expected id 101, got different id"
    assert set(created_post.keys()) == {'userId', 'id', 'title', 'body'}, "Fields do not match expected schema"

def test_update_post_returns_updated_post():
    """
    Verify that updating a post returns the updated post with preserved id.
    """
    updated_post = {
        'id': 1,
        'title': 'Updated Title',
        'body': 'Updated Body',
        'userId': 1
    }
    response = requests.put('https://jsonplaceholder.typicode.com/posts/1', json=updated_post)
    assert response.status_code == 200, "Unexpected status code"
    updated_response = response.json()
    assert updated_response['id'] == 1, "Expected id 1, got different id"
    assert updated_response['title'] == 'Updated Title', "Title did not match expected value"
    assert updated_response['body'] == 'Updated Body', "Body did not match expected value"
    assert set(updated_response.keys()) == {'userId', 'id', 'title', 'body'}, "Fields do not match expected schema"

def test_patch_post_returns_patched_post():
    """
    Verify that patching a post returns the patched post with preserved id.
    """
    patched_post = {
        'id': 1,
        'title': 'Patched Title'
    }
    response = requests.patch('https://jsonplaceholder.typicode.com/posts/1', json=patched_post)
    assert response.status_code == 200, "Unexpected status code"
    patched_response = response.json()
    assert patched_response['id'] == 1, "Expected id 1, got different id"
    assert patched_response['title'] == 'Patched Title', "Title did not match expected value"
    assert set(patched_response.keys()) == {'userId', 'id', 'title', 'body'}, "Fields do not match expected schema"

def test_delete_post_returns_empty_object():
    """
    Verify that deleting a post returns an empty object.
    """
    response = requests.delete('https://jsonplaceholder.typicode.com/posts/1')
    assert response.status_code == 200, "Unexpected status code"
    assert response.json() == {}, "Expected empty object, got different response"