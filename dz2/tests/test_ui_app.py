import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import balanced_set
from streamlit.testing.v1 import AppTest
from test_cli_artifacts import CHECKLIST_OK, MATRIX_OK, PLAN_OK, QA_OK

from ai_testgen.models import ArtifactType
from ai_testgen.ollama_client import OllamaClient

APP_PATH = Path(__file__).parent.parent / "src" / "ai_testgen" / "ui" / "app.py"

REQUIREMENTS = """# Требования: вход в личный кабинет

**URL:** https://shop.example.com/login

| ID | Требование |
|----|------------|
| BR-1 | Вход по email и паролю открывает профиль |
| BR-2 | Неверный пароль показывает ошибку |
| BR-3 | Пустые поля не отправляют форму |
"""

CATALOG = """# Каталог

**URL:** https://otus.ru/catalog

| ID | Требование |
|----|------------|
| BR-1 | Список курсов |
"""


@pytest.fixture
def workbench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=30)


def _upload(at: AppTest, *files: tuple[str, str]) -> AppTest:
    at.file_uploader[0].set_value(
        [(name, text.encode("utf-8"), "text/markdown") for name, text in files]
    )
    return at.run()


def _file(at: AppTest, name: str = "login.md", text: str = REQUIREMENTS) -> AppTest:
    return _upload(at, (name, text))


def _quality(workbench: Path, stem: str = "testcases") -> dict[str, Any]:
    """Read the one timestamped score file written for ``stem``."""
    files = sorted((workbench / "output" / "quality").glob(f"{stem}-*.json"))
    assert len(files) == 1, files
    return json.loads(files[0].read_text(encoding="utf-8"))


def _text(at: AppTest, index: int) -> str:
    value = at.text_area[index].value
    assert value is not None
    return value


def _selectbox(at: AppTest, label: str) -> Any:
    return next(item for item in at.selectbox if item.label == label)


def _button(at: AppTest, label: str) -> Any:
    return next(item for item in at.button if item.label == label)


def test_workbench_renders_uploaded_requirements(workbench: Path) -> None:
    at = _file(_app().run())

    assert not at.exception
    assert at.title[0].value == "Рабочее место: тесты по требованиям"
    assert _selectbox(at, "Файл требований").value == "login.md"
    assert _text(at, 0).startswith("# Требования")
    metric = {item.label: item.value for item in at.metric}
    assert metric["Требований BR"] == "3"
    assert metric["URL"] == "https://shop.example.com/login"
    assert "Target site: https://shop.example.com/login" in _text(at, 1)


def test_prompt_waits_for_upload(workbench: Path) -> None:
    at = _app().run()

    assert not at.exception
    assert any("Файлы не загружены" in item.value for item in at.info)
    assert any("Сначала загрузите файл требований" in item.value for item in at.info)
    assert not at.text_area


def test_uploaded_files_can_be_switched(workbench: Path) -> None:
    at = _file(_app().run())
    at.file_uploader[0].set_value(
        [
            ("login.md", REQUIREMENTS.encode("utf-8"), "text/markdown"),
            ("catalog.md", CATALOG.encode("utf-8"), "text/markdown"),
        ]
    ).run()

    _selectbox(at, "Файл требований").set_value("catalog.md").run()

    assert not at.exception
    assert "https://otus.ru/catalog" in _text(at, 0)
    assert at.session_state["url_input"] == "https://otus.ru/catalog"


def test_editing_requirements_updates_metrics(workbench: Path) -> None:
    at = _file(_app().run())

    at.text_area[0].set_value(REQUIREMENTS + "\n| BR-4 | Выход |").run()

    metric = {item.label: item.value for item in at.metric}
    assert metric["Требований BR"] == "4"


def test_non_utf8_upload_is_reported(workbench: Path) -> None:
    at = _app().run()
    at.file_uploader[0].set_value(
        [("broken.md", b"\xff\xfe\x00", "text/markdown")]
    ).run()

    assert not at.exception
    assert any("не в UTF-8" in item.value for item in at.error)
    assert "Сначала загрузите файл требований" in at.info[0].value


def test_type_switch_rebuilds_prompt(workbench: Path) -> None:
    at = _file(_app().run())

    _selectbox(at, "Тип артефакта").set_value(ArtifactType.CHECKLIST).run()

    assert not at.exception
    assert "Create a test checklist" in _text(at, 1)


def test_preset_adds_instructions_to_prompt(workbench: Path) -> None:
    at = _file(_app().run())

    at.multiselect[0].set_value(["boundary-values"]).run()

    assert not at.exception
    prompt = _text(at, 1)
    assert "Additional instructions:" in prompt
    assert "boundary-value scenarios" in prompt


def test_generate_button_produces_validated_artifact(
    workbench: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        OllamaClient,
        "chat_messages",
        lambda self, messages, temperature, json_mode=False: json.dumps(
            balanced_set(10)
        ),
    )
    at = _file(_app().run())
    model_input = next(
        item for item in at.text_input if item.label == "Модель"
    )
    model_input.set_value("test-model").run()

    _button(at, "Сгенерировать").click().run()

    assert not at.exception
    assert any("Готово" in item.value for item in at.success)
    assert any("TC-01" in item.value for item in at.markdown)


def test_matrix_prompt_reuses_generated_testcases(
    workbench: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        OllamaClient,
        "chat_messages",
        lambda self, messages, temperature, json_mode=False: json.dumps(
            balanced_set(10)
        ),
    )
    at = _file(_app().run())
    model_input = next(
        item for item in at.text_input if item.label == "Модель"
    )
    model_input.set_value("test-model").run()

    _button(at, "Сгенерировать").click().run()
    _selectbox(at, "Тип артефакта").set_value(ArtifactType.COVERAGEMATRIX).run()

    assert not at.exception
    assert at.session_state["testcase_index"]["doc"].endswith("login.md")
    prompt = _text(at, 1)
    assert "Test cases already generated for these requirements:" in prompt
    assert "- TC-01 (negative, no requirement): title TC-01" in prompt
    assert "No test cases have been generated" not in prompt


def test_all_five_types_are_generated_and_saved(
    workbench: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through the workbench: five types, matrix reuses case ids."""

    def fake_chat(self, system, user, temperature, json_mode=True):
        if "coverage matrix" in user:
            return MATRIX_OK
        if "checklist" in user:
            return CHECKLIST_OK
        if "test plan" in user:
            return PLAN_OK
        if "QA report" in user:
            return QA_OK
        if "test cases" in user:
            return json.dumps(balanced_set(10))
        raise AssertionError(f"unexpected user prompt: {user[:80]}")

    monkeypatch.setattr(OllamaClient, "chat", fake_chat)
    at = _file(_app().run())
    model_input = next(item for item in at.text_input if item.label == "Модель")
    model_input.set_value("test-model").run()

    prompts: dict[ArtifactType, str] = {}
    for artifact_type in ArtifactType:
        _selectbox(at, "Тип артефакта").set_value(artifact_type).run()
        prompts[artifact_type] = _text(at, 1)
        _button(at, "Сгенерировать").click().run()
        assert not at.exception
        _button(at, "Сохранить в output").click().run()
        assert not at.exception

    for artifact_type in ArtifactType:
        path = workbench / "output" / f"login.{artifact_type.value}.md"
        assert path.is_file(), artifact_type
        assert path.stat().st_size > 0

    matrix_prompt = prompts[ArtifactType.COVERAGEMATRIX]
    assert "Test cases already generated for these requirements:" in matrix_prompt
    assert "TC-01" in matrix_prompt


def test_connection_preset_fills_ollama_url(workbench: Path) -> None:
    at = _app().run()

    _selectbox(at, "Способ подключения").set_value(
        "Ollama в Docker-сети (ohw-ollama:11434)"
    ).run()

    assert not at.exception
    url = next(item for item in at.text_input if item.label == "Ollama URL")
    assert url.value == "http://ohw-ollama:11434"


def test_models_are_listed_and_selectable(
    workbench: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        OllamaClient,
        "list_models",
        lambda self: ["llama3:8b", "qwen2.5:7b-instruct"],
    )
    at = _app().run()

    _button(at, "Загрузить модели").click().run()

    assert not at.exception
    picker = _selectbox(at, "Модель из Ollama")
    assert "qwen2.5:7b-instruct" in picker.options
    picker.set_value("llama3:8b").run()

    assert at.session_state["model"] == "llama3:8b"


def test_manual_model_can_be_typed(workbench: Path) -> None:
    at = _app().run()

    model_input = next(item for item in at.text_input if item.label == "Модель")
    model_input.set_value("my-own-model").run()

    assert not at.exception
    assert at.session_state["model"] == "my-own-model"


def test_llm_evaluation_is_saved(
    workbench: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scores = {
        "readability": 4,
        "context": 5,
        "goal": 4,
        "coverage": 3,
    }

    def fake_chat(self, system, user, temperature, json_mode=True):
        # One call per criterion: the rubric names the criterion in the system
        # message, so the fake must answer for that one only.
        criterion = next(
            item for item in scores if f"({item})" in system
        )
        return json.dumps(
            {
                "evidence": ["Generate exactly 10"],
                "score": scores[criterion],
                "comment": f"reasoning for {criterion}",
            }
        )

    monkeypatch.setattr(OllamaClient, "chat", fake_chat)
    at = _file(_app().run())
    model_input = next(item for item in at.text_input if item.label == "Модель")
    model_input.set_value("test-model").run()

    _button(at, "Оценить через LLM").click().run()

    assert not at.exception
    assert any("Оценка LLM сохранена" in item.value for item in at.success)
    payload = _quality(workbench)
    assert payload["source"] == "llm"
    assert payload["model"] == "test-model"
    assert payload["scores"] == scores
    assert payload["average"] == 4.0
    assert payload["prompt_sha256"]


def test_evaluation_links_generated_artifact(
    workbench: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scores = {"readability": 4, "context": 5, "goal": 4, "coverage": 3}

    def fake_chat(self, system, user, temperature, json_mode=True):
        criterion = next((item for item in scores if f"({item})" in system), None)
        if criterion is None:
            return json.dumps(balanced_set(10))
        return json.dumps(
            {
                "evidence": ["Generate exactly 10"],
                "score": scores[criterion],
                "comment": "ok",
            }
        )

    monkeypatch.setattr(OllamaClient, "chat", fake_chat)
    at = _file(_app().run())
    model_input = next(item for item in at.text_input if item.label == "Модель")
    model_input.set_value("test-model").run()

    _button(at, "Сгенерировать").click().run()
    _button(at, "Сохранить в output").click().run()
    artifact = workbench / "output" / "login.testcases.md"
    assert artifact.is_file()
    _button(at, "Оценить через LLM").click().run()

    assert not at.exception
    payload = _quality(workbench)
    reference = payload["generated_artifact"]
    assert reference["path"] == "login.testcases.md"
    assert reference["bytes"] == artifact.stat().st_size
    assert reference["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert any(
        "login.testcases.md" in item.value for item in at.caption
    )


def test_quality_score_is_saved(workbench: Path) -> None:
    at = _file(_app().run())

    save = next(button for button in at.button if button.label == "Сохранить оценку")
    save.click().run()

    assert not at.exception
    payload = _quality(workbench)
    assert payload["artifact"] == "testcases"
    assert payload["scores"] == {
        "readability": 3,
        "context": 3,
        "goal": 3,
        "coverage": 3,
    }
