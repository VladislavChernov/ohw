"""Tests for api_testgen."""

from pathlib import Path

import pytest

from api_testgen.config import Config
from api_testgen.extractor import extract_code
from api_testgen.prompt import load_prompt, resolve_prompt_path


def test_config_default_base_url_is_localhost():
    assert Config().ollama_base_url == "http://localhost:11434"


def test_config_from_env_override(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    assert Config.from_env().ollama_base_url == "http://host.docker.internal:11434"


def test_load_prompt_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_prompt(tmp_path / "prompt.txt")


def test_load_prompt_reads_file(tmp_path):
    file = tmp_path / "prompt.txt"
    file.write_text("Hello world", encoding="utf-8")
    assert load_prompt(file) == "Hello world"


def test_resolve_prompt_path_explicit():
    assert resolve_prompt_path("custom.txt", Path("input")) == Path("custom.txt")


def test_resolve_prompt_path_default():
    assert resolve_prompt_path(None, Path("input")) == Path("input") / "prompt.txt"


def test_extract_code_from_fences():
    response = "Here is the code:\n```python\nimport requests\ndef test_ok():\n    pass\n```\n"
    code = extract_code(response)
    assert "def test_ok" in code
    assert "```" not in code


def test_extract_code_bare_block():
    response = "import requests\n\ndef test_1():\n    pass\n"
    code = extract_code(response)
    assert "def test_1" in code


def test_format_report_markdown_ok():
    from api_testgen.runner import format_report_markdown

    results = {
        "file": "output/generated_tests.py",
        "exit_code": 0,
        "passed": 4,
        "failed": 0,
        "errors": 0,
        "total": 4,
        "stdout": "4 passed",
        "stderr": "",
    }
    md = format_report_markdown(results)
    assert "# API Test Generation Report" in md
    assert "`OK`" in md
    assert "| Passed | 4 |" in md


def test_format_report_markdown_runner_failure_is_not_ok():
    """pytest itself failed (e.g. no module / import error): verdict must be FAILED."""
    from api_testgen.runner import format_report_markdown

    results = {
        "file": "output/generated_tests.py",
        "exit_code": 1,
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "total": 0,
        "stdout": "",
        "stderr": "/app/.venv/bin/python: No module named pytest",
    }
    md = format_report_markdown(results)
    assert "`FAILED`" in md
    assert "`OK`" not in md


def test_format_report_markdown_no_tests_collected_is_not_ok():
    """pytest exit code 5 (nothing collected) must NOT report OK."""
    from api_testgen.runner import format_report_markdown

    results = {
        "file": "output/generated_tests.py",
        "exit_code": 5,
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "total": 0,
        "stdout": "no tests ran",
        "stderr": "",
    }
    md = format_report_markdown(results)
    assert "`FAILED`" in md


def test_save_report_writes_file(tmp_path):
    from pathlib import Path

    from api_testgen.runner import save_report

    results = {
        "file": "output/generated_tests.py",
        "exit_code": 0,
        "passed": 4,
        "failed": 0,
        "errors": 0,
        "total": 4,
        "stdout": "4 passed",
        "stderr": "",
    }
    path = save_report(results, tmp_path)
    assert path == tmp_path / "report.md"
    content = Path(path).read_text(encoding="utf-8")
    assert "## Summary" in content


# --- pytest summary parsing -------------------------------------------


def test_parse_counts_from_summary_line():
    from api_testgen.runner import _parse_counts

    stdout = (
        "test_a PASSED\n" "FAILED test_b.py::test_c - assert 404 == 201\n"
        "=== short test summary info ===\n" "FAILED test_b.py::test_c\n"
        "1 failed, 5 passed in 5.29s\n"
    )
    assert _parse_counts(stdout) == (5, 1, 0)


def test_parse_counts_with_errors():
    from api_testgen.runner import _parse_counts

    assert _parse_counts("2 passed, 1 failed, 1 error in 1.00s\n") == (2, 1, 1)


def test_parse_counts_fallback_without_summary():
    from api_testgen.runner import _parse_counts

    stdout = "test_a PASSED\ntest_b FAILED\ntest_c PASSED\n"
    assert _parse_counts(stdout) == (2, 1, 0)


# --- failure report humanization --------------------------------------


def test_parse_failures_from_short_summary():
    from api_testgen.runner import _parse_failures

    stdout = (
        "test_a PASSED\n"
        "=== short test summary info ===\n"
        "FAILED output/generated_tests.py::test_list_posts - AssertionError: assert 'p...\n"
        "=== 1 failed, 5 passed in 5.29s ===\n"
    )
    assert _parse_failures(stdout) == [
        ("output/generated_tests.py::test_list_posts", "AssertionError: assert 'p...")
    ]


def test_failed_tests_section_is_human_readable():
    from api_testgen.runner import format_report_markdown

    big_payload = "x" * 500
    stdout = (
        "=== short test summary info ===\n"
        f"FAILED output/generated_tests.py::test_list_posts - AssertionError: assert 'posts' in [{big_payload}]\n"
    )
    results = {
        "file": "output/generated_tests.py",
        "exit_code": 1,
        "passed": 5,
        "failed": 1,
        "errors": 0,
        "total": 6,
        "stdout": stdout,
        "stderr": "",
        "model": "qwen2.5:7b-instruct",
    }
    md = format_report_markdown(results)
    assert "## Failed tests" in md
    assert "**`test_list_posts`**" in md
    assert "assert failed:" in md
    assert "…(truncated)" in md
    assert "- **Model:** `qwen2.5:7b-instruct`" in md
    assert big_payload not in md.split("## Pytest output")[0].split("## Failed tests")[1]


def test_no_failed_section_when_all_passed():
    from api_testgen.runner import format_report_markdown

    results = {
        "file": "output/generated_tests.py",
        "exit_code": 0,
        "passed": 6,
        "failed": 0,
        "errors": 0,
        "total": 6,
        "stdout": "6 passed in 1.00s\n",
        "stderr": "",
    }
    md = format_report_markdown(results)
    assert "## Failed tests" not in md



def test_find_missing_markers():
    from api_testgen.validator import find_missing

    code = "def test_get(): requests.get(...)\n# POST also used"
    assert find_missing(code, ["GET", "POST", "PUT", "DELETE"]) == ["PUT", "DELETE"]


def test_find_missing_all_present():
    from api_testgen.validator import find_missing

    code = ".get(...) .post(...) .put(...) .patch(...) .delete(...)"
    assert find_missing(code, ["GET", "POST", "PUT", "PATCH", "DELETE"]) == []


def test_build_feedback_mentions_missing():
    from api_testgen.validator import build_feedback

    fb = build_feedback(["PUT", "DELETE"])
    assert "PUT" in fb and "DELETE" in fb
    assert "Regenerate" in fb


def test_generate_code_feedback_loop(monkeypatch):
    """Model returns lazy answer first, complete one after feedback."""
    import api_testgen.ollama as ollama_mod

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": FakeResp.code}

    calls: list[str] = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            calls.append(json["prompt"])
            return FakeResp()

    monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", FakeClient)

    async def no_sleep(_):
        pass

    monkeypatch.setattr("asyncio.sleep", no_sleep)

    FakeResp.code = "def test_get():\n    requests.get('http://x')"
    import asyncio

    async def run():
        # attempt 1: lazy answer (no POST) -> rejected with feedback;
        # attempt 2: full answer -> accepted
        FakeResp.codes = iter(
            [
                "def test_get():\n    requests.get('http://x')",
                "def test_get():\n    requests.get('http://x')\n"
                "def test_post():\n    requests.post('http://x')",
            ]
        )

        def json(self):
            return {"response": next(FakeResp.codes)}

        FakeResp.json = json

        return await ollama_mod.generate_code(
            "http://fake", "m", "p", max_retries=2, required_markers=["GET", "POST"]
        )

    result = asyncio.run(run())
    assert "test_post" in result
    assert len(calls) == 2  # rejected, then accepted
    assert "Previous answer was rejected" in calls[1]
    assert "POST" in calls[1]

