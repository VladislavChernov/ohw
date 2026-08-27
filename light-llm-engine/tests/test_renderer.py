"""Unit tests for the markdown renderer."""

from llm_engine.renderer import render


def test_render_wraps_reply() -> None:
    out = render("hello model", "request.md")
    assert "# Ответ модели — request.md" in out
    assert "hello model" in out
    assert out.endswith("\n")


def test_render_strips_whitespace() -> None:
    out = render("  hello \n\n", "a.txt")
    assert "  hello" not in out
    assert "hello" in out