# API Test Generation Report

- **Result:** `FAILED`
- **Test file:** `output\generated_tests.py`
- **Exit code:** `1`
- **Model:** `qwen2.5:7b-instruct`

## Summary

| Metric | Value |
|---|---|
| Total tests | 6 |
| Passed | 5 |
| Failed | 1 |
| Errors | 0 |

## Failed tests

- **`test_patch_post`** — assert failed: Тело пост...

Full tracebacks with payloads are in the [Pytest output](#pytest-output) appendix.

## Pytest output

```
============================= test session starts =============================
collecting ... collected 6 items

output/generated_tests.py::test_list_posts_returns_100_items PASSED      [ 16%]
output/generated_tests.py::test_get_single_post PASSED                   [ 33%]
output/generated_tests.py::test_create_post PASSED                       [ 50%]
output/generated_tests.py::test_update_post PASSED                       [ 66%]
output/generated_tests.py::test_patch_post FAILED                        [ 83%]
output/generated_tests.py::test_delete_post PASSED                       [100%]

================================== FAILURES ===================================
_______________________________ test_patch_post _______________________________
/data/output/generated_tests.py:68: in test_patch_post
    ???
E   AssertionError: Тело поста изменилось
E   assert 'quia et susc...et architecto' == 'Test Body'
E     
E     - Test Body
E     + quia et suscipit
E     + suscipit recusandae consequuntur expedita et cum
E     + reprehenderit molestiae ut ut quas totam
E     + nostrum rerum est autem sunt rem eveniet architecto
=========================== short test summary info ===========================
FAILED output/generated_tests.py::test_patch_post - AssertionError: Тело пост...
========================= 1 failed, 5 passed in 3.45s =========================
```
