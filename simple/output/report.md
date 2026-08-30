# API Test Generation Report

- **Result:** `OK`
- **Test file:** `output/generated_tests.py`
- **Exit code:** `0`
- **Model:** `qwen2.5:7b-instruct`

## Summary

| Metric | Value |
|---|---|
| Total tests | 6 |
| Passed | 6 |
| Failed | 0 |
| Errors | 0 |

## Pytest output

```
============================= test session starts ==============================
collecting ... collected 6 items

output/generated_tests.py::test_list_posts_returns_100_items PASSED      [ 16%]
output/generated_tests.py::test_get_single_post_has_correct_fields PASSED [ 33%]
output/generated_tests.py::test_create_post_returns_new_post_with_id PASSED [ 50%]
output/generated_tests.py::test_update_post_returns_updated_post PASSED  [ 66%]
output/generated_tests.py::test_patch_post_returns_patched_post PASSED   [ 83%]
output/generated_tests.py::test_delete_post_returns_empty_object PASSED  [100%]

============================== 6 passed in 1.87s ===============================
```
