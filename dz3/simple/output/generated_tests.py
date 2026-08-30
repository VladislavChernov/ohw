import requests

def test_list_posts_returns_100_items():
    """
    Тестирование получения списка постов.
    Ожидается, что будет возвращен список из 100 постов.
    """
    response = requests.get('https://jsonplaceholder.typicode.com/posts')
    assert response.status_code == 200, "Неверный код статуса при получении списка постов"
    assert len(response.json()) == 100, "Неверное количество постов в списке"

def test_get_single_post():
    """
    Тестирование получения одного поста.
    Ожидается, что будет возвращен один пост с заданным id.
    """
    response = requests.get('https://jsonplaceholder.typicode.com/posts/1')
    assert response.status_code == 200, "Неверный код статуса при получении одного поста"
    assert 'id' in response.json(), "Ответ не содержит поле 'id'"
    assert 'title' in response.json(), "Ответ не содержит поле 'title'"
    assert 'body' in response.json(), "Ответ не содержит поле 'body'"

def test_create_post():
    """
    Тестирование создания нового поста.
    Ожидается, что будет создан новый пост с сгенерированным id.
    """
    new_post = {
        'title': 'Test Title',
        'body': 'Test Body',
        'userId': 1
    }
    response = requests.post('https://jsonplaceholder.typicode.com/posts', json=new_post)
    assert response.status_code == 201, "Неверный код статуса при создании поста"
    assert response.json()['title'] == new_post['title'], "Название поста не совпадает"
    assert response.json()['body'] == new_post['body'], "Тело поста не совпадает"
    assert response.json()['userId'] == new_post['userId'], "Идентификатор пользователя не совпадает"

def test_update_post():
    """
    Тестирование обновления существующего поста.
    Ожидается, что пост будет обновлен с сохранением id.
    """
    updated_post = {
        'id': 1,
        'title': 'Updated Title',
        'body': 'Updated Body',
        'userId': 1
    }
    response = requests.put('https://jsonplaceholder.typicode.com/posts/1', json=updated_post)
    assert response.status_code == 200, "Неверный код статуса при обновлении поста"
    assert response.json()['title'] == updated_post['title'], "Название поста не совпадает"
    assert response.json()['body'] == updated_post['body'], "Тело поста не совпадает"
    assert response.json()['userId'] == updated_post['userId'], "Идентификатор пользователя не совпадает"

def test_patch_post():
    """
    Тестирование частичного обновления существующего поста.
    Ожидается, что пост будет частично обновлен с сохранением id.
    """
    updated_post = {
        'id': 1,
        'title': 'Patched Title'
    }
    response = requests.patch('https://jsonplaceholder.typicode.com/posts/1', json=updated_post)
    assert response.status_code == 200, "Неверный код статуса при частичном обновлении поста"
    assert response.json()['title'] == updated_post['title'], "Название поста не совпадает"
    assert response.json()['body'] == 'Test Body', "Тело поста изменилось"
    assert response.json()['userId'] == 1, "Идентификатор пользователя изменился"

def test_delete_post():
    """
    Тестирование удаления поста.
    Ожидается, что будет возвращен пустой объект {}.
    """
    response = requests.delete('https://jsonplaceholder.typicode.com/posts/1')
    assert response.status_code == 200, "Неверный код статуса при удалении поста"
    assert response.json() == {}, "Ответ не пустой объект {}"