from attack_runner.manifest import build_manifest
from attack_runner.runner import (
    DEFAULT_AUGUSTUS_PROBES,
    _augustus_probes,
    _augustus_scan_config,
    _redact_value,
    redact,
)


def test_redact_masks_synthetic_pii():
    text = "admin@ohw-lab.local 4512 123456 +7 (999) 123-45-67 sk-lab-token"
    result = redact(text)
    assert "admin@ohw-lab.local" not in result
    assert "4512 123456" not in result
    assert "+7 (999) 123-45-67" not in result
    assert "sk-lab-token" not in result
    assert "<email>" in result
    assert "<passport>" in result
    assert "<phone>" in result
    assert "<token>" in result


def test_manifest_records_pinned_model_digest(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "huihui_ai/llama3.2-abliterate:3b")
    monkeypatch.setenv(
        "OLLAMA_MODEL_DIGEST",
        "sha256:2ffe056672b5e8028209eaf3a86c256ea6bf690dda5414335913418ed4f6183b",
    )

    manifest = build_manifest(
        run_id="20261023T000000Z",
        started_at="2026-10-23T00:00:00Z",
        finished_at="2026-10-23T00:00:01Z",
        target="http://agent:8080",
        preset=1,
        runs=3,
        scenarios=[],
        complete=False,
        augustus=None,
    )

    assert manifest["model"] == "huihui_ai/llama3.2-abliterate:3b"
    assert manifest["model_digest"] == (
        "sha256:2ffe056672b5e8028209eaf3a86c256ea6bf690dda5414335913418ed4f6183b"
    )
    assert manifest["env"]["OLLAMA_MODEL_DIGEST"] == manifest["model_digest"]


def test_augustus_config_targets_agent_chat(tmp_path):
    source = tmp_path / "augustus.yaml"
    source.write_text(
        "generators:\n"
        "  rest.Rest:\n"
        "    uri: http://localhost:11434\n"
        "output:\n"
        "  format: table\n",
        encoding="utf-8",
    )

    config = _augustus_scan_config(
        source,
        "http://agent-fixed:8080",
        tmp_path / "results.jsonl",
    )

    generator = config["generators"]["rest.Rest"]
    assert generator["uri"] == "http://agent-fixed:8080/chat"
    assert generator["req_template_json_object"] == {"message": "$INPUT"}
    assert generator["response_json"] is True
    assert generator["response_json_field"] == "$.reply"
    assert config["output"]["format"] == "jsonl"
    assert config["output"]["path"] == str(tmp_path / "results.jsonl")


def test_augustus_probes_use_defaults_or_csv(monkeypatch):
    monkeypatch.delenv("AUGUSTUS_PROBES", raising=False)
    assert _augustus_probes() == list(DEFAULT_AUGUSTUS_PROBES)

    monkeypatch.setenv("AUGUSTUS_PROBES", "dan.DAN_Jailbreak, ,goodside.PayloadSplitting")
    assert _augustus_probes() == [
        "dan.DAN_Jailbreak",
        "goodside.PayloadSplitting",
    ]


def test_redact_value_walks_nested_body():
    body = {
        "reply": "email: admin@ohw-lab.local",
        "tool_calls": [{"sql": "SELECT email FROM users", "rows": 1}],
        "nested": ["4512 123456", {"token": "sk-lab-token"}],
    }
    result = _redact_value(body)
    assert result["reply"] == "email: <email>"
    assert result["tool_calls"][0]["sql"] == "SELECT email FROM users"
    assert result["nested"][0] == "<passport>"
    assert result["nested"][1]["token"] == "<token>"
