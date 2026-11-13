"""UI tests for the Streamlit workbench (no network calls)."""

import json
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from src.generator.models import ArtifactType
from src.generator.ollama_client import OllamaClient, OllamaConnectionError

APP_PATH = Path(__file__).parent.parent / "src" / "ui" / "app.py"

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


def _case(index: int, case_type: str) -> dict[str, Any]:
    return {
        "id": f"TC-{index:02d}",
        "type": case_type,
        "title": f"Сценарий {index}",
        "preconditions": ["Пользователь на странице входа"],
        "steps": ["Открыть страницу", "Ввести данные", "Нажать кнопку"],
        "expected": "Система показывает ожидаемый результат",
        "requirement": f"BR-{(index - 1) % 3 + 1}",
    }


def _testcases(count: int = 10) -> str:
    # At least a third of the set must be negative cases.
    negatives = max(1, (count + 2) // 3)
    cases = [
        _case(index, "negative" if index <= negatives else "positive")
        for index in range(1, count + 1)
    ]
    return json.dumps({"cases": cases}, ensure_ascii=False)


@pytest.fixture
def workbench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=60)


def _upload(at: AppTest, *files: tuple[str, str]) -> AppTest:
    if not at.file_uploader:
        at = at.run()
    at.file_uploader[0].set_value(
        [(name, text.encode("utf-8"), "text/markdown") for name, text in files]
    )
    return at.run()


def _selectbox(at: AppTest, label: str) -> Any:
    return next(item for item in at.selectbox if item.label == label)


def _text_input(at: AppTest, label: str) -> Any:
    return next(item for item in at.text_input if item.label == label)


def _text_area(at: AppTest, label: str) -> Any:
    return next(item for item in at.text_area if item.label == label)


def _number_input(at: AppTest, label: str) -> Any:
    return next(item for item in at.number_input if item.label == label)


def _button(at: AppTest, label: str) -> Any:
    return next(item for item in at.button if item.label == label)


def _checkbox(at: AppTest, label: str) -> Any:
    return next(item for item in at.checkbox if item.label == label)


def _metrics(at: AppTest) -> dict[str, str]:
    return {item.label: item.value for item in at.metric}


def test_workbench_renders_without_network(
    workbench: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(self: OllamaClient) -> list[str]:
        raise AssertionError("Ollama must not be queried on startup")

    monkeypatch.setattr(OllamaClient, "list_models", fail)
    at = _app().run()

    assert not at.exception
    assert at.title[0].value == "Рабочее место: тестовая документация и её качество"
    assert any("Файлы не загружены" in item.value for item in at.info)
    assert any("Сначала загрузите файл требований" in item.value for item in at.info)
    assert not at.text_area


def test_uploaded_requirements_are_counted(workbench: Path) -> None:
    at = _upload(_app(), ("login.md", REQUIREMENTS))

    assert not at.exception
    assert _selectbox(at, "Файл требований").value == "login.md"
    assert at.text_area[0].value.startswith("# Требования")
    metric = _metrics(at)
    assert metric["Требований с ID"] == "3"
    assert metric["Файлов"] == "1"
    assert metric["Символов"] == str(len(REQUIREMENTS))


def test_requirements_can_be_edited_and_switched(workbench: Path) -> None:
    at = _upload(_app(), ("login.md", REQUIREMENTS), ("catalog.md", CATALOG))
    _selectbox(at, "Файл требований").set_value("catalog.md").run()

    assert _metrics(at)["Требований с ID"] == "1"
    assert at.text_area[0].value.startswith("# Каталог")

    at.text_area[0].set_value(CATALOG + "\n| BR-2 | Карточка курса |").run()
    assert _metrics(at)["Требований с ID"] == "2"


def test_artifact_type_count_and_runs_are_configurable(workbench: Path) -> None:
    at = _upload(_app(), ("login.md", REQUIREMENTS))
    _selectbox(at, "Тип артефакта").set_value(ArtifactType.CHECKLIST).run()
    _number_input(at, "Кейсов на файл").set_value(5).run()
    _number_input(at, "Повторов").set_value(2).run()

    assert not at.exception
    assert any(
        "тип: Чек-лист" in item.value and "кейсов: 5" in item.value
        and "повторов: 2" in item.value
        for item in at.caption
    )


def test_generation_uses_the_shared_stages(
    workbench: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        OllamaClient,
        "chat",
        lambda self, system, user, temperature, json_mode=True: _testcases(10),
    )
    at = _upload(_app(), ("login.md", REQUIREMENTS))
    _button(at, "Сгенерировать").click().run()

    assert not at.exception
    assert any("Готово" in item.value for item in at.success)
    assert any("TC-01" in item.value for item in at.markdown)

    output = workbench / "output"
    assert (output / "ui-input" / "login.md").read_text(encoding="utf-8") == REQUIREMENTS
    assert (output / "generation.json").is_file()
    scenarios = list((output / "scenarios").glob("*.md"))
    assert len(scenarios) == 1
    assert "TC-01" in scenarios[0].read_text(encoding="utf-8")


def test_continue_on_error_checkbox_keeps_successful_files(
    workbench: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_chat(
        self: OllamaClient,
        system: str,
        user: str,
        temperature: float,
        json_mode: bool = True,
    ) -> str:
        if "первого файла" in user:
            raise OllamaConnectionError("ollama service is unreachable")
        return _testcases(10)

    monkeypatch.setattr(OllamaClient, "chat", fake_chat)
    at = _upload(
        _app(),
        (
            "a-first.md",
            "# Первый\n\nBR-1. Проверка первого файла. Система должна работать.\n",
        ),
        (
            "b-second.md",
            "# Второй\n\nBR-2. Проверка второго файла. Система должна работать.\n",
        ),
    )
    _checkbox(at, "Продолжать при ошибке Ollama").set_value(True).run()
    _button(at, "Сгенерировать").click().run()

    assert not at.exception
    assert any("Готово" in item.value for item in at.success)
    assert any("a-first.md" in item.value for item in at.warning)
    snapshot = json.loads(
        (workbench / "output" / "generation.json").read_text(encoding="utf-8")
    )
    assert [item["filename"] for item in snapshot["scenarios"]] == ["b-second.md"]
    assert "a-first.md" in snapshot["errors"][0]


def test_prompt_tab_builds_and_applies_edited_prompt(
    workbench: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    def fake_chat(
        self: OllamaClient,
        system: str,
        user: str,
        temperature: float,
        json_mode: bool = True,
    ) -> str:
        captured.append(user)
        return _testcases(10)

    monkeypatch.setattr(OllamaClient, "chat", fake_chat)
    at = _upload(_app(), ("login.md", REQUIREMENTS))

    prompt = _text_area(at, "Промпт для модели")
    assert "BR-1" in prompt.value
    assert "Вход по email и паролю" in prompt.value

    prompt.set_value("EDITED PROMPT").run()
    _button(at, "Сгенерировать").click().run()

    assert not at.exception
    assert captured
    assert captured[0] == "EDITED PROMPT"
    snapshot = json.loads(
        (workbench / "output" / "generation.json").read_text(encoding="utf-8")
    )
    assert snapshot["scenarios"][0]["prompt"] == "EDITED PROMPT"


def test_metrics_charts_and_report_are_produced(
    workbench: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        OllamaClient,
        "chat",
        lambda self, system, user, temperature, json_mode=True: _testcases(10),
    )
    at = _upload(_app(), ("login.md", REQUIREMENTS))
    _button(at, "Сгенерировать").click().run()
    _button(at, "Пересчитать метрики").click().run()

    assert not at.exception
    assert "Overall" in _metrics(at)
    assert (workbench / "output" / "quality-metrics.json").is_file()

    _button(at, "Построить графики").click().run()
    assert not at.exception
    charts = list((workbench / "output" / "charts").glob("*.png"))
    assert len(charts) == 3

    _button(at, "Сформировать отчёт").click().run()
    assert not at.exception
    assert (workbench / "output" / "report.md").is_file()
    assert any("Отчёт:" in item.value for item in at.success)


def test_missing_model_blocks_generation(workbench: Path) -> None:
    at = _upload(_app(), ("login.md", REQUIREMENTS))
    _selectbox(at, "Модель из Ollama").set_value(
        "Другая модель (ввести вручную)"
    ).run()
    _text_input(at, "Модель").set_value("").run()
    _button(at, "Сгенерировать").click().run()

    assert not at.exception
    assert any("Модель не задана" in item.value for item in at.error)
    assert not (workbench / "output" / "generation.json").exists()
