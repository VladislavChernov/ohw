# API Test Generation Report

- **Result:** `FAILED`
- **Test file:** `output/generated_tests.py`
- **Exit code:** `1`

## Summary

| Metric | Value |
|---|---|
| Total tests | 7 |
| Passed | 5 |
| Failed | 2 |
| Errors | 0 |

## Pytest output

```
============================= test session starts ==============================
collecting ... collected 6 items

output/generated_tests.py::test_list_posts FAILED                        [ 16%]
output/generated_tests.py::test_get_post PASSED                          [ 33%]
output/generated_tests.py::test_create_post PASSED                       [ 50%]
output/generated_tests.py::test_update_post PASSED                       [ 66%]
output/generated_tests.py::test_patch_post PASSED                        [ 83%]
output/generated_tests.py::test_delete_post PASSED                       [100%]

=================================== FAILURES ===================================
_______________________________ test_list_posts ________________________________
output/generated_tests.py:10: in test_list_posts
    assert "posts" in response.json()
E   AssertionError: assert 'posts' in [{'userId': 1, 'id': 1, 'title': 'sunt aut facere repellat provident occaecati excepturi optio reprehenderit', 'body': 'quia et suscipit\nsuscipit recusandae consequuntur expedita et cum\nreprehenderit molestiae ut ut quas totam\nnostrum rerum est autem sunt rem eveniet architecto'}, {'userId': 1, 'id': 2, 'title': 'qui est esse', 'body': 'est rerum tempore vitae\nsequi sint nihil reprehenderit dolor beatae ea dolores neque\nfugiat blanditiis voluptate porro vel nihil molestiae ut reiciendis\nqui aperiam non debitis possimus qui neque nisi nulla'}, {'userId': 1, 'id': 3, 'title': 'ea molestias quasi exercitationem repellat qui ipsa sit aut', 'body': 'et iusto sed quo iure\nvoluptatem occaecati omnis eligendi aut ad\nvoluptatem doloribus vel accusantium quis pariatur\nmolestiae porro eius odio et labore et velit aut'}, {'userId': 1, 'id': 4, 'title': 'eum et est occaecati', 'body': 'ullam et saepe reiciendis voluptatem adipisci\nsit amet autem assumenda provident rerum culpa\nquis hic commodi nesciunt rem tenetur doloremque ipsam iure\nquis sunt voluptatem rerum illo velit'}, {'userId': 1, 'id': 5, 'title': 'nesciunt quas odio', 'body': 'repudiandae veniam quaerat sunt sed\nalias aut fugiat sit autem sed est\nvoluptatem omnis possimus esse voluptatibus quis\nest aut tenetur dolor neque'}, {'userId': 1, 'id': 6, 'title': 'dolorem eum magni eos aperiam quia', 'body': 'ut aspernatur corporis harum nihil quis provident sequi\nmollitia nobis aliquid molestiae\nperspiciatis et ea nemo ab reprehenderit accusantium quas\nvoluptate dolores velit et doloremque molestiae'}, ...]
E    +  where [{'userId': 1, 'id': 1, 'title': 'sunt aut facere repellat provident occaecati excepturi optio reprehenderit', 'body': 'quia et suscipit\nsuscipit recusandae consequuntur expedita et cum\nreprehenderit molestiae ut ut quas totam\nnostrum rerum est autem sunt rem eveniet architecto'}, {'userId': 1, 'id': 2, 'title': 'qui est esse', 'body': 'est rerum tempore vitae\nsequi sint nihil reprehenderit dolor beatae ea dolores neque\nfugiat blanditiis voluptate porro vel nihil molestiae ut reiciendis\nqui aperiam non debitis possimus qui neque nisi nulla'}, {'userId': 1, 'id': 3, 'title': 'ea molestias quasi exercitationem repellat qui ipsa sit aut', 'body': 'et iusto sed quo iure\nvoluptatem occaecati omnis eligendi aut ad\nvoluptatem doloribus vel accusantium quis pariatur\nmolestiae porro eius odio et labore et velit aut'}, {'userId': 1, 'id': 4, 'title': 'eum et est occaecati', 'body': 'ullam et saepe reiciendis voluptatem adipisci\nsit amet autem assumenda provident rerum culpa\nquis hic commodi nesciunt rem tenetur doloremque ipsam iure\nquis sunt voluptatem rerum illo velit'}, {'userId': 1, 'id': 5, 'title': 'nesciunt quas odio', 'body': 'repudiandae veniam quaerat sunt sed\nalias aut fugiat sit autem sed est\nvoluptatem omnis possimus esse voluptatibus quis\nest aut tenetur dolor neque'}, {'userId': 1, 'id': 6, 'title': 'dolorem eum magni eos aperiam quia', 'body': 'ut aspernatur corporis harum nihil quis provident sequi\nmollitia nobis aliquid molestiae\nperspiciatis et ea nemo ab reprehenderit accusantium quas\nvoluptate dolores velit et doloremque molestiae'}, ...] = json()
E    +    where json = <Response [200]>.json
=============================== warnings summary ===============================
../app/.venv/lib/python3.13/site-packages/_pytest/cacheprovider.py:469
  /app/.venv/lib/python3.13/site-packages/_pytest/cacheprovider.py:469: PytestCacheWarning: could not create cache path /data/.pytest_cache/v/cache/nodeids: [Errno 13] Permission denied: '/data/pytest-cache-files-jzgljv0j'
    config.cache.set("cache/nodeids", sorted(self.cached_nodeids))

../app/.venv/lib/python3.13/site-packages/_pytest/cacheprovider.py:423
  /app/.venv/lib/python3.13/site-packages/_pytest/cacheprovider.py:423: PytestCacheWarning: could not create cache path /data/.pytest_cache/v/cache/lastfailed: [Errno 13] Permission denied: '/data/pytest-cache-files-60w49e96'
    config.cache.set("cache/lastfailed", self.lastfailed)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
FAILED output/generated_tests.py::test_list_posts - AssertionError: assert 'p...
=================== 1 failed, 5 passed, 2 warnings in 5.29s ====================
```
