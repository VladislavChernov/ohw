"""Bridge to the Dz2 normalizer and prompt templates."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from ai_testgen.models import ArtifactType

from llm_degradation.normalizer_bridge import (
    BridgeError,
    build_prompt,
    is_idempotent,
    load_normalized,
    prompt_sha256,
    resolve_input_file,
)

SAMPLE = (
    "# Требования\n\n"
    "**URL:** https://example.test/catalog\n\n"
    "## Requirements\n\n"
    "### BR-1\nПервое требование о каталоге.\n\n"
    "### BR-2\nВторое требование о корзине.\n"
)


def test_utf8_and_cp1251_give_the_same_text(tmp_path: Path, normalizer_config: Path) -> None:
    utf8 = tmp_path / "utf8.md"
    cp1251 = tmp_path / "cp1251.md"
    utf8.write_text(SAMPLE, encoding="utf-8")
    cp1251.write_bytes(SAMPLE.encode("cp1251"))

    first = load_normalized(utf8, normalizer_config=normalizer_config)
    second = load_normalized(cp1251, normalizer_config=normalizer_config)

    assert first.text == second.text
    assert first.encoding.lower().replace("-", "") in {"utf8", "cp1251"}
    assert second.encoding.lower().replace("-", "") == "cp1251"


def test_normalization_is_idempotent(normalized: object, normalizer_config: Path) -> None:
    assert is_idempotent(normalized)  # type: ignore[arg-type]


def test_br_ids_and_url_are_extracted(normalized: object, normalizer_config: Path) -> None:
    assert normalized.br_ids == ("BR-1", "BR-2", "BR-3", "BR-4")  # type: ignore[attr-defined]
    assert normalized.url == "https://otus.ru/catalog/courses"  # type: ignore[attr-defined]


def test_source_and_text_hashes_are_stable(tmp_path: Path, normalizer_config: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_text(SAMPLE, encoding="utf-8")
    first = load_normalized(path, normalizer_config=normalizer_config)
    second = load_normalized(path, normalizer_config=normalizer_config)
    assert first.source_sha256 == second.source_sha256
    assert first.text_sha256 == hashlib.sha256(first.text.encode("utf-8")).hexdigest()


def test_build_prompt_uses_dz2_templates(
    normalized: object, prompt_dir: Path, presets_dir: Path
) -> None:
    bundle = build_prompt(
        normalized,  # type: ignore[arg-type]
        ArtifactType.TESTCASES,
        7,
        prompt_dir=prompt_dir,
        presets_dir=presets_dir,
    )
    assert bundle.artifact_type is ArtifactType.TESTCASES
    assert bundle.count == 7
    assert bundle.system
    assert bundle.user
    assert bundle.prompt_sha256 == prompt_sha256(bundle.user)


def test_prompt_hash_changes_with_count(normalized: object, prompt_dir: Path) -> None:
    first = build_prompt(normalized, ArtifactType.TESTCASES, 5, prompt_dir=prompt_dir)  # type: ignore[arg-type]
    second = build_prompt(normalized, ArtifactType.TESTCASES, 6, prompt_dir=prompt_dir)  # type: ignore[arg-type]
    assert first.prompt_sha256 != second.prompt_sha256


def test_missing_input_raises(tmp_path: Path, normalizer_config: Path) -> None:
    with pytest.raises(BridgeError, match="not found"):
        load_normalized(tmp_path / "missing.md", normalizer_config=normalizer_config)


def test_unsupported_extension_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "doc.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(BridgeError, match="unsupported input extension"):
        resolve_input_file(path)


def test_directory_with_no_supported_files_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(BridgeError, match="no .md/.txt"):
        resolve_input_file(tmp_path)


def test_directory_name_collision_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "doc.txt").write_text(SAMPLE, encoding="utf-8")
    with pytest.raises(BridgeError, match="collision"):
        resolve_input_file(tmp_path)


def test_directory_picks_the_only_file(tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_text(SAMPLE, encoding="utf-8")
    assert resolve_input_file(tmp_path) == path
