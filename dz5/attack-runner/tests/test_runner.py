from attack_runner.runner import _redact_value, redact


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
