from pathlib import Path

import pytest
from conftest import write_input

from ai_testgen.input_doc import InputError, collect_input_files, extract_url, load_docs
from ai_testgen.models import ArtifactType

SITE_URL = "https://example.com"


def test_collects_multiple_files_sorted(input_dir: Path) -> None:
    write_input(input_dir, "cart.md", f"Test cart on {SITE_URL}")
    write_input(input_dir, "auth.txt", f"Test auth on {SITE_URL}")

    files = collect_input_files(input_dir)

    assert [f.name for f in files] == ["auth.txt", "cart.md"]


def test_collision_same_stem_different_extensions(input_dir: Path) -> None:
    write_input(input_dir, "auth.md", "md variant")
    write_input(input_dir, "auth.txt", "txt variant")

    with pytest.raises(InputError) as exc_info:
        collect_input_files(input_dir)
    assert "auth.md" in str(exc_info.value) and "auth.txt" in str(exc_info.value)


def test_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="does not exist"):
        collect_input_files(tmp_path / "nowhere")


def test_empty_directory_raises(input_dir: Path) -> None:
    input_dir.mkdir()

    with pytest.raises(InputError, match="no .md/.txt"):
        collect_input_files(input_dir)


def test_unsupported_extension_skipped_with_warning(
    input_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_input(input_dir, "good.md", f"Test {SITE_URL}")
    write_input(input_dir, "notes.pdf", "ignored")

    files = collect_input_files(input_dir)

    assert [f.name for f in files] == ["good.md"]
    assert "notes.pdf" in capsys.readouterr().out


def test_url_extracted_from_content(input_dir: Path) -> None:
    path = write_input(input_dir, "doc.md", f"# Требования\nПроверить {SITE_URL}/login внимательно")

    docs = load_docs(input_dir, None)

    assert docs[0].path == path
    assert docs[0].url == f"{SITE_URL}/login"


def test_url_override_takes_precedence(input_dir: Path) -> None:
    write_input(input_dir, "doc.md", f"Check {SITE_URL} please")
    other = "https://other.example.org"

    docs = load_docs(input_dir, other)

    assert docs[0].url == other


def test_missing_url_leaves_none(input_dir: Path) -> None:
    write_input(input_dir, "doc.md", "no url here at all")

    docs = load_docs(input_dir, None)

    assert docs[0].url is None


def test_txt_parsed_same_as_md(input_dir: Path) -> None:
    write_input(input_dir, "plain.txt", f"plain text requirements for {SITE_URL}")

    docs = load_docs(input_dir, None)

    assert SITE_URL in docs[0].content
    assert docs[0].url == SITE_URL


def test_extract_url_strips_trailing_punctuation() -> None:
    assert extract_url("see https://site.ru.") == "https://site.ru"
    assert extract_url("no url") is None


def test_front_matter_absent_defaults_to_testcases(input_dir: Path) -> None:
    write_input(input_dir, "doc.md", f"plain body with {SITE_URL}")

    docs = load_docs(input_dir, None)

    assert docs[0].artifact_type is ArtifactType.TESTCASES
    assert "plain body" in docs[0].content


def test_front_matter_type_checklist_stripped_from_content(input_dir: Path) -> None:
    write_input(input_dir, "doc.md", f"---\ntype: checklist\n---\n\nBody with {SITE_URL}\n")

    docs = load_docs(input_dir, None)

    assert docs[0].artifact_type is ArtifactType.CHECKLIST
    assert "---" not in docs[0].content
    assert "type:" not in docs[0].content
    assert docs[0].url == SITE_URL


def test_front_matter_type_testplan(input_dir: Path) -> None:
    write_input(input_dir, "doc.txt", "---\ntype: testplan\n---\nbody")

    docs = load_docs(input_dir, None)

    assert docs[0].artifact_type is ArtifactType.TESTPLAN
    assert docs[0].content == "body"


def test_front_matter_unknown_type_raises(input_dir: Path) -> None:
    write_input(input_dir, "doc.md", "---\ntype: presentation\n---\nbody")

    with pytest.raises(InputError, match="unknown artifact type"):
        load_docs(input_dir, None)


def test_front_matter_unknown_key_raises(input_dir: Path) -> None:
    write_input(input_dir, "doc.md", "---\ntype: checklist\nlang: ru\n---\nbody")

    with pytest.raises(InputError, match="unknown front-matter key"):
        load_docs(input_dir, None)


def test_front_matter_malformed_line_raises(input_dir: Path) -> None:
    write_input(input_dir, "doc.md", "---\njust text\n---\nbody")

    with pytest.raises(InputError, match="malformed front-matter line"):
        load_docs(input_dir, None)


def test_front_matter_without_closing_marker_is_body_text(input_dir: Path) -> None:
    write_input(input_dir, "doc.md", f"--- not a marker\nbody with {SITE_URL}")

    docs = load_docs(input_dir, None)

    assert docs[0].artifact_type is ArtifactType.TESTCASES
    assert SITE_URL in docs[0].content


def test_front_matter_type_case_insensitive(input_dir: Path) -> None:
    write_input(input_dir, "doc.md", "---\ntype: Checklist\n---\nbody")

    docs = load_docs(input_dir, None)

    assert docs[0].artifact_type is ArtifactType.CHECKLIST
