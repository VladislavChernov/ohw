import json as jsonlib
from pathlib import Path

import httpx

from ai_testgen.handlers import get_handler
from ai_testgen.input_doc import RequirementDoc
from ai_testgen.models import ArtifactType
from ai_testgen.ollama_client import OllamaClient
from ai_testgen.prompt import QAREPORT_SECTIONS, TESTPLAN_SECTIONS


def _doc(artifact_type: ArtifactType) -> RequirementDoc:
    return RequirementDoc(
        path=Path("doc.md"),
        content="requirements body",
        url="https://x.com",
        artifact_type=artifact_type,
    )


def test_registry_covers_all_types() -> None:
    for artifact_type in ArtifactType:
        handler = get_handler(artifact_type)
        assert handler.artifact_type is artifact_type


def test_only_testcases_want_json() -> None:
    assert get_handler(ArtifactType.TESTCASES).wants_json is True
    assert get_handler(ArtifactType.CHECKLIST).wants_json is False
    assert get_handler(ArtifactType.TESTPLAN).wants_json is False
    assert get_handler(ArtifactType.QAREPORT).wants_json is False
    assert get_handler(ArtifactType.COVERAGEMATRIX).wants_json is False


def test_custom_prompt_dir_is_used_for_every_type(tmp_path: Path) -> None:
    for artifact_type in ArtifactType:
        (tmp_path / f"{artifact_type.value}.txt").write_text(
            f"SENTINEL {artifact_type.value} {{content}}", encoding="utf-8"
        )

        prompt = get_handler(artifact_type, tmp_path).build_user(
            _doc(artifact_type), 5
        )

        assert prompt.startswith(f"SENTINEL {artifact_type.value}")
        assert "requirements body" in prompt


def test_qareport_prompt_lists_same_sections_as_linter() -> None:
    prompt = get_handler(ArtifactType.QAREPORT).build_user(
        _doc(ArtifactType.QAREPORT), 10
    )

    for section in QAREPORT_SECTIONS:
        assert f"- ## {section}" in prompt


def test_coveragematrix_prompt_requests_table() -> None:
    prompt = get_handler(ArtifactType.COVERAGEMATRIX).build_user(
        _doc(ArtifactType.COVERAGEMATRIX), 10
    )

    assert "## Матрица покрытия требований" in prompt
    assert "| Requirement ID |" in prompt


def test_checklist_prompt_mentions_zone_headings_rule() -> None:
    prompt = get_handler(ArtifactType.CHECKLIST).build_user(_doc(ArtifactType.CHECKLIST), 10)

    assert "'## '" in prompt
    assert "at least one" in prompt


def test_testplan_prompt_lists_same_sections_as_linter() -> None:
    prompt = get_handler(ArtifactType.TESTPLAN).build_user(_doc(ArtifactType.TESTPLAN), 10)

    for section in TESTPLAN_SECTIONS:
        assert f"- ## {section}" in prompt


def _payload_for(json_mode: bool) -> dict:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = request.read()
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "ok"}, "done": True},
        )

    client = OllamaClient(transport=httpx.MockTransport(handler))
    client.chat("sys", "user", 0.7, json_mode=json_mode)
    return jsonlib.loads(captured["payload"])


def test_json_mode_sends_format_flag() -> None:
    payload = _payload_for(json_mode=True)

    assert payload["format"] == "json"


def test_markdown_mode_omits_format_flag() -> None:
    payload = _payload_for(json_mode=False)

    assert "format" not in payload