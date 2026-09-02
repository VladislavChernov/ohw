"""Tests for documentation layers, detection, and OpenAPI enumeration."""

from __future__ import annotations

import httpx

from json_testgen_advanced.docs import (
    build_bundle,
    detect_source,
    fetch_url_page,
    openapi_digest,
    parse_openapi,
)

OPENAPI_JSON = """
{
  "openapi": "3.0.0",
  "info": {"title": "x", "version": "1"},
  "paths": {
    "/posts": {"get": {"responses": {"200": {"description": "ok"}, "404": {"description": "nf"}}}},
    "/users/{id}": {"delete": {"responses": {"204": {"description": "ok"}}}}
  }
}
""".strip()

OPENAPI_YAML = """
openapi: 3.0.0
info: {title: x, version: "1"}
paths:
  /posts:
    get:
      responses:
        "200": {description: ok}
""".strip()


def test_detect_openapi_json() -> None:
    assert detect_source(OPENAPI_JSON) == "openapi"


def test_detect_openapi_yaml() -> None:
    assert detect_source(OPENAPI_YAML) == "openapi"


def test_detect_markdown() -> None:
    assert detect_source("# Заголовок\n\nКакой-то текст") == "markdown"


def test_parse_openapi_and_digest() -> None:
    spec = parse_openapi(OPENAPI_JSON)
    layer = openapi_digest(spec)
    assert "GET /posts" in layer.content
    assert "DELETE /users/{id}" in layer.content
    assert "200" in layer.content
    assert "Маркеры покрытия" in layer.content


def test_build_bundle_api_only() -> None:
    bundle = build_bundle(api_page="Страница API", base_text=None, supplement_texts=[])
    assert bundle.mode == "api-only"
    assert bundle.context == "Страница API"
    assert bundle.base is None


def test_build_bundle_api_plus_xyz() -> None:
    bundle = build_bundle(api_page="page", base_text=OPENAPI_JSON, supplement_texts=[])
    assert bundle.mode == "api+xyz"
    assert bundle.base is not None
    assert bundle.base.is_contract


def test_build_bundle_api_plus_xxx_no_xyz_warns() -> None:
    bundle = build_bundle(api_page="page", base_text=None, supplement_texts=["маркеры покрытия: posts"])
    assert bundle.mode == "api+xxx"
    assert any("базовая" in w.lower() for w in bundle.warnings)


def test_build_bundle_merge_xxx_wins() -> None:
    base = "base_endpoint: из базовой"
    supp = "base_endpoint: из дополнения"
    bundle = build_bundle(api_page="", base_text=base, supplement_texts=[supp])
    assert bundle.mode == "api+xyz+xxx"
    merged = bundle.merged_contract()
    assert "переопределено дополнением" in merged
    assert any("Конфликт" in w for w in bundle.warnings)


def test_build_bundle_no_specs_none_mode() -> None:
    bundle = build_bundle(api_page="", base_text=None, supplement_texts=[])
    assert bundle.mode == "none"
    assert any("best effort" in w for w in bundle.warnings)


def test_fetch_url_page_strips_html_and_truncates() -> None:
    html_doc = "<html><body><script>var x=1;</script><h1>API</h1><p>" + "a" * 2000 + "</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html_doc)

    text = fetch_url_page("http://docs", transport=httpx.MockTransport(handler), limit=100)
    assert "API" in text
    assert "<h1>" not in text
    assert "var x=1" not in text
    assert len(text) < 2000
    assert "выжимка" in text


def test_fetch_url_page_within_limit_not_truncated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Возвращённый текст просто так")

    text = fetch_url_page("http://docs", transport=httpx.MockTransport(handler), limit=8000)
    assert "Возвращённый текст" in text
    assert "выжимка" not in text
