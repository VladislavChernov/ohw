"""Guard-тесты статического анализа и валидации оптимизатора.

Это не тесты сгенерированного кода (стенда с API в проекте нет), а проверки
самого измерительного инструмента: он должен считать тесты по отдельности,
честно отдавать `unknown` и не выдавать шаблонный текст за результат прогона.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.test_optimizer import (
    DEFAULT_OLLAMA_MODEL,
    FALLBACK_OPTIMIZED_CODE,
    Generation,
    TestMetrics as Metrics,
    _draft_header,
    analyze_code,
    build_optimization_report,
    describe_changes,
    extract_test_blocks,
    llm_optimize,
    process_single_file,
    readability_score,
    static_analysis,
    validate_generated_tests,
    validation_to_md,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = (ROOT / "input" / "example.py").read_text(encoding="utf-8")


def analyze(code: str):
    metrics = Metrics()
    issues = []
    profile = static_analysis(code, metrics, issues)
    return metrics, issues, profile


class _FakeClient:
    def __init__(self, available: bool, response: str = "") -> None:
        self.available = available
        self._response = response

    def generate(self, prompt: str, system: str | None = None) -> str:
        return self._response


# ---------------------------------------------------------------------------
# Разбор тестов
# ---------------------------------------------------------------------------
def test_each_test_function_is_a_separate_block() -> None:
    blocks = extract_test_blocks(EXAMPLE)
    assert [name for name, _src, _norm in blocks] == [
        "test_create_user",
        "test_create_user_other_name",
        "test_create_user_new_new_name",
        "test_create_user_invalid_email",
        "test_create_user_missing_name",
    ]
    # Ни один блок не содержит объявления другого теста.
    for _name, source, _norm in blocks:
        assert source.count("def test_") == 1


def test_three_create_tests_are_one_duplicate_group() -> None:
    metrics, issues, profile = analyze(EXAMPLE)
    assert profile.duplicate_group_sizes == [3]
    # Два теста из трёх — лишние копии; исходный не считается дублем.
    assert metrics.duplicate_tests == 2
    assert any(issue.category == "Дублирующие тесты" for issue in issues)


def test_different_field_names_and_status_codes_are_not_duplicates() -> None:
    code = '''
def test_returns_name():
    body = response.json()
    assert body["name"] == "Alice"


def test_returns_email():
    body = response.json()
    assert body["email"] == "alice@example.com"


def test_created_status():
    assert response.status_code == 201


def test_rejected_status():
    assert response.status_code == 400
'''
    metrics, _issues, _profile = analyze(code)
    assert metrics.duplicate_tests == 0


def test_fixture_teardown_forms_are_recognised() -> None:
    try_finally = '''
import pytest


@pytest.fixture
def client():
    session = object()
    try:
        yield session
    finally:
        session.close()
'''
    addfinalizer = '''
import pytest


@pytest.fixture
def client(request):
    session = object()
    request.addfinalizer(session.close)
    return session
'''
    assert analyze_code(try_finally).has_teardown is True
    assert analyze_code(addfinalizer).has_teardown is True


def test_autouse_fixture_covers_all_tests() -> None:
    code = '''
import pytest


@pytest.fixture(autouse=True)
def clean():
    yield


def test_one():
    assert 1 == 1


def test_two():
    assert 2 == 2
'''
    metrics, _issues, _profile = analyze(code)
    assert metrics.tests_with_fixtures == 2


# ---------------------------------------------------------------------------
# Метрики: измеренные значения и unknown
# ---------------------------------------------------------------------------
def test_input_metrics_are_measured() -> None:
    metrics, _issues, profile = analyze(EXAMPLE)
    assert metrics.total_tests == 5
    assert metrics.parametrized_tests == 0
    assert metrics.tests_with_fixtures == 0
    assert metrics.tests_with_docstrings == 0
    assert metrics.hardcoded_urls == 1
    assert metrics.missing_assertions == 0
    assert metrics.readability_score == pytest.approx(7.0)
    assert metrics.requirements_coverage_pct is None
    assert profile.has_timeout is False
    assert profile.has_teardown is False


def test_syntax_error_gives_unknown_not_zero() -> None:
    metrics, issues, profile = analyze("def test_broken(:\n    assert True\n")
    assert profile.parses is False
    assert metrics.total_tests is None
    assert metrics.duplicate_tests is None
    assert metrics.readability_score is None
    assert issues[0].severity == "critical"
    assert "не разбирается" in issues[0].description


def test_no_tests_gives_unknown_readability() -> None:
    metrics, _issues, profile = analyze("import pytest\n")
    assert profile.parses is True
    assert metrics.total_tests == 0
    assert readability_score(profile) is None


def test_fixtures_and_docstrings_are_counted_per_test() -> None:
    code = '''
import pytest


@pytest.fixture
def client():
    return object()


@pytest.fixture
def cleanup():
    yield
    pass


def test_uses_client(client):
    """Docstring of the first test."""
    assert client is not None


def test_uses_both(client, cleanup):
    assert cleanup is not None


def test_uses_none():
    assert True
'''
    metrics, _issues, profile = analyze(code)
    assert metrics.tests_with_fixtures == 2
    assert metrics.tests_with_docstrings == 1
    assert profile.fixtures == ["cleanup", "client"]


def test_hardcoded_urls_vs_env_default() -> None:
    code = '''
import os

BASE_URL = os.getenv("API_URL", "http://localhost:8000")
OTHER = "https://api.example.com/v1"
'''
    profile = analyze_code(code)
    assert profile.hardcoded_urls == ["https://api.example.com/v1"]
    assert profile.env_url_defaults == ["http://localhost:8000"]


def test_timeout_is_counted_only_for_http_calls() -> None:
    subprocess_code = 'import subprocess\nsubprocess.run(["x"], timeout=5)\n'
    http_code = 'def test_x(client):\n    client.post("u", timeout=5)\n'
    assert analyze_code(subprocess_code).has_timeout is False
    assert analyze_code(http_code).has_timeout is True


# ---------------------------------------------------------------------------
# Валидация сгенерированного кода
# ---------------------------------------------------------------------------
def test_validation_flags_forbidden_patterns() -> None:
    code = '''
import time
import pytest


def test_cheats():
    assert True
    time.sleep(1)


def test_swallows():
    try:
        pass
    except:
        pass
'''
    validation = validate_generated_tests(code)
    assert validation.parses is True
    assert validation.ok is False
    joined = " | ".join(validation.forbidden)
    assert "assert True" in joined
    assert "time.sleep" in joined
    assert "bare except" in joined
    assert validation.problems == []


def test_validation_ignores_forbidden_words_in_docstring() -> None:
    code = '''
def test_sleep_is_not_used():
    """Проверка без time.sleep(1) в коде и без assert True."""
    assert 1 == 1
'''
    validation = validate_generated_tests(code)
    assert validation.ok is True
    assert validation.forbidden == []


def test_validation_report_puts_parse_error_in_problems() -> None:
    validation = validate_generated_tests("def test_x(:\n    pass\n")
    report = validation_to_md(validation)
    assert "- Проблема: код не разбирается" in report
    assert "invalid syntax (строка 1)" in report
    assert "Запрещённые паттерны: unknown" in report
    assert "  - код не разбирается" not in report


def test_validation_flags_unparsable_code() -> None:
    validation = validate_generated_tests("def test_x(:\n")
    assert validation.parses is False
    assert validation.ok is False
    assert "не разбирается" in validation.problems[0]


def test_validation_flags_file_without_tests() -> None:
    validation = validate_generated_tests("VALUE = 1\n")
    assert validation.parses is True
    assert "нет ни одной функции test_*" in validation.problems


def test_draft_header_lists_problems() -> None:
    validation = validate_generated_tests("def test_x(:\n")
    header = _draft_header(validation)
    assert header.startswith("# ЧЕРНОВИК")
    assert "не разбирается" in header


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------
def test_fallback_is_users_semantics_and_valid() -> None:
    validation = validate_generated_tests(FALLBACK_OPTIMIZED_CODE)
    assert validation.ok is True
    assert "/users" in FALLBACK_OPTIMIZED_CODE
    assert "/items" not in FALLBACK_OPTIMIZED_CODE
    assert "email" in FALLBACK_OPTIMIZED_CODE

    profile = analyze_code(FALLBACK_OPTIMIZED_CODE)
    assert profile.test_count == 3
    assert profile.parametrized_tests == 2
    assert profile.duplicate_tests == 0
    assert profile.fixtures == ["api_client", "created_users"]
    assert profile.tests_with_fixtures == 3
    assert profile.has_timeout is True
    assert profile.has_teardown is True
    assert profile.hardcoded_urls == []


def test_llm_optimize_marks_unavailable_fallback() -> None:
    generation = llm_optimize(_FakeClient(available=False), EXAMPLE)
    assert generation.source == "fallback"
    assert "недоступен" in generation.reason
    assert generation.text == FALLBACK_OPTIMIZED_CODE


def test_llm_optimize_extracts_python_block() -> None:
    answer = "```python\ndef test_ok():\n    assert 1 == 1\n```"
    generation = llm_optimize(_FakeClient(available=True, response=answer), EXAMPLE)
    assert generation.source == "llm"
    assert generation.reason == ""
    assert generation.text.startswith("def test_ok")


def test_llm_optimize_rejects_prose_answer() -> None:
    answer = "Конечно! Вот ваш код, но без блока."
    generation = llm_optimize(_FakeClient(available=True, response=answer), EXAMPLE)
    assert generation.source == "fallback"
    assert "не является Python-кодом" in generation.reason


def test_fallback_reason_keeps_transport_error() -> None:
    class _BrokenClient(_FakeClient):
        def __init__(self) -> None:
            super().__init__(available=True, response="")
            self.last_error = "HTTP Error 500: Internal Server Error"

    generation = llm_optimize(_BrokenClient(), EXAMPLE)
    assert generation.source == "fallback"
    assert "запрос к Ollama не удался" in generation.reason
    assert "500" in generation.reason


# ---------------------------------------------------------------------------
# Отчёт строится из артефакта, а не из шаблона
# ---------------------------------------------------------------------------
def test_report_describes_actual_changes() -> None:
    metrics, issues, analysis = analyze(EXAMPLE)
    generated = "def test_only_one():\n    assert 1 == 1\n"
    report = build_optimization_report(
        test_file=ROOT / "input" / "example.py",
        metrics=metrics,
        issues=issues,
        analysis=analysis,
        review=Generation("review", "llm"),
        optimization=Generation(generated, "llm"),
        prioritization=Generation("prio", "llm"),
        validation=validate_generated_tests(generated),
        client_available=True,
    )
    assert "Проделанные оптимизации" not in report
    assert "- Функций `test_*` (без учёта параметров): 5 → 1" in report
    assert "- Параметризованных тестов: 0 → 0" in report
    assert "- Объявленных фикстур: 0 → 0" in report
    assert "- Дублирующих тестов (лишних копий): 2 → 0" in report
    assert "Получен от модели" not in report
    assert "получен от модели" in report


def test_report_marks_fallback_source() -> None:
    metrics, issues, analysis = analyze(EXAMPLE)
    optimization = Generation(
        FALLBACK_OPTIMIZED_CODE, "fallback", "Ollama недоступен"
    )
    report = build_optimization_report(
        test_file=ROOT / "input" / "example.py",
        metrics=metrics,
        issues=issues,
        analysis=analysis,
        review=Generation("review", "fallback", "Ollama недоступен"),
        optimization=optimization,
        prioritization=Generation("prio", "fallback", "Ollama недоступен"),
        validation=validate_generated_tests(optimization.text),
        client_available=False,
    )
    assert "**fallback** — Ollama недоступен" in report
    assert "Покрытие требований: unknown" in report
    assert "- Параметризованных тестов: 0 → 2" in report
    assert "- Дублирующих тестов (лишних копий): 2 → 0" in report


def test_report_says_unknown_when_output_does_not_parse() -> None:
    before = analyze_code(EXAMPLE)
    after = analyze_code("def test_broken(:\n")
    changes = describe_changes(before, after)
    assert changes.startswith("- unknown:")
    assert "не разбирается" in changes


def test_invalid_output_is_saved_as_draft(tmp_path: Path, capsys) -> None:
    class _BadCodeClient(_FakeClient):
        def __init__(self) -> None:
            super().__init__(
                available=True,
                response="```python\ndef test_broken(:\n    pass\n```",
            )

    process_single_file(
        _BadCodeClient(),
        ROOT / "input" / "example.py",
        requirements_text="",
        output_dir=tmp_path,
    )
    artifact = (tmp_path / "example_optimized_tests.py").read_text(encoding="utf-8")
    report = (tmp_path / "example_optimization_report.md").read_text(encoding="utf-8")
    assert artifact.startswith("# ЧЕРНОВИК")
    assert "def test_broken(:" in artifact
    assert "- Статус: **ЧЕРНОВИК**" in report
    assert "- Проблема: код не разбирается" in report
    assert "Запрещённые паттерны: unknown" in report

    stderr = capsys.readouterr().err
    assert "не прошёл валидацию" in stderr
    assert "не разбирается" in stderr


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------
def test_default_model_matches_env_file() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        pytest.skip(".env не копируется в образ; проверка выполняется на хосте")
    env_text = env_path.read_text(encoding="utf-8")
    env_values = dict(
        line.split("=", 1)
        for line in env_text.splitlines()
        if line and not line.startswith("#")
    )
    assert env_values["OLLAMA_MODEL"] == DEFAULT_OLLAMA_MODEL
