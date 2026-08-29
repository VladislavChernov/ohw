"""Tests for api_testgen."""

from api_testgen.extractor import extract_code
from api_testgen.prompt import build_prompt
from api_testgen.swagger import parse_endpoints


def test_parse_endpoints_returns_list():
    spec = {"paths": {"/posts": {"get": {"summary": "Get posts"}}}}
    endpoints = parse_endpoints(spec)
    assert len(endpoints) == 1
    assert endpoints[0].method == "GET"
    assert endpoints[0].path == "/posts"


def test_parse_endpoints_skips_non_http():
    spec = {"paths": {"/posts": {"parameters": [{"name": "id"}]}}}
    endpoints = parse_endpoints(spec)
    assert len(endpoints) == 0


def test_build_prompt_contains_base_url():
    prompt = build_prompt([], "https://example.com")
    assert "https://example.com" in prompt


def test_extract_code_from_fences():
    response = "Here is the code:\n```python\nimport requests\ndef test_ok():\n    pass\n```\n"
    code = extract_code(response)
    assert "def test_ok" in code
    assert "```" not in code


def test_extract_code_bare_block():
    response = "import requests\n\ndef test_1():\n    pass\n"
    code = extract_code(response)
    assert "def test_1" in code
