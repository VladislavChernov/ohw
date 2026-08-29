"""Tests for ohw_kit.render."""

from __future__ import annotations

from ohw_kit.render import render_markdown


def test_render_markdown_wraps_content() -> None:
    out = render_markdown("  body text  ", source_name="auth.md")
    assert out.startswith("# Ответ модели — auth.md")
    assert "body text" in out
    assert out.endswith("body text\n")


def test_render_markdown_custom_title() -> None:
    out = render_markdown("work", source_name="a.md", title="Отчёт")
    assert out.startswith("# Отчёт")
    assert "сгенерировано" in out