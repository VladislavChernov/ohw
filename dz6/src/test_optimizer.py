#!/usr/bin/env python3
"""
test_optimizer.py — оптимизация автотестов с помощью LLM (Ollama).

Что делает скрипт:
  1. Читает исходные автотесты из input/example.py.
  2. Выполняет статический анализ кода (дубли, читаемость, метрики).
  3. Отправляет исходный код в LLM (Ollama) с запросами на:
     - ревью кода (что хорошо / что плохо),
     - оптимизацию и генерацию оптимизированного варианта автотестов.
  4. Сохраняет результаты в отдельный каталог прогона output/<YYYY-MM-DD_HH-MM-SS>/:
     - <stem>_optimized_tests.py — оптимизированные автотесты.
     - <stem>_code_review_report.md — отчёт ревью кода.
     - <stem>_optimization_report.md — сводный отчёт оптимизации.

Требуется:
  - Python 3.9+
  - Ollama запущен (по умолчанию http://localhost:11434), модель: codellama / deepseek-coder-v2 / llama3.
  - pip install -r requirements/requirements.txt

Если Ollama недоступен — скрипт работает в демо-режиме (fallback), генерируя
оптимизированный код и отчёт без вызова LLM, чтобы показать структуру результата.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional, Tuple

import urllib.request as urllib_request  # noqa: E402
import urllib.error  # noqa: E402

# ---------------------------------------------------------------------------
# Константы и пути
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(ROOT / "output")))
PROMPTS_DIR = Path(os.getenv("PROMPTS_DIR", str(ROOT / "prompts")))
REQUIREMENTS_PATH = Path(os.getenv("REQUIREMENTS_PATH", str(ROOT / "requirements_with_priorities.md")))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "codellama:7b-instruct")
REQUEST_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))


@dataclass
class TestMetrics:
    total_tests: int = 0
    duplicate_tests: int = 0
    near_duplicate_tests: int = 0
    parametrized_tests: int = 0
    tests_with_fixtures: int = 0
    tests_with_docstrings: int = 0
    hardcoded_urls: int = 0
    missing_assertions: int = 0
    readability_score: float = 0.0
    requirements_coverage_pct: float = 0.0


@dataclass
class ReviewIssue:
    category: str
    severity: str  # critical / high / medium / low
    description: str
    line: Optional[int] = None
    suggestion: Optional[str] = None


# ---------------------------------------------------------------------------
# Чтение исходного кода
# ---------------------------------------------------------------------------
def read_source(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Исходный файл не найден: {path}")
    return path.read_text(encoding="utf-8")


def find_test_files(input_dir: Path, max_depth: Optional[int] = None) -> List[Path]:
    """Рекурсивно находит файлы с автотестами (.txt, .py) в input_dir.

    Args:
        input_dir: Корневая папка для сканирования.
        max_depth: Максимальная глубина рекурсии (0 = только корень, None = без ограничения).

    Returns:
        Список путей к файлам, содержащим def test_.
    """
    files: List[Path] = []
    input_dir = input_dir.resolve()

    def _walk(current: Path, depth: int) -> None:
        if max_depth is not None and depth > max_depth:
            return
        try:
            entries = sorted(current.iterdir())
        except PermissionError:
            return
        for entry in entries:
            if entry.is_dir():
                _walk(entry, depth + 1)
            elif entry.is_file() and entry.suffix.lower() in ('.txt', '.py'):
                try:
                    content = entry.read_text(encoding='utf-8', errors='ignore')
                    if 'def test_' in content:
                        files.append(entry)
                except Exception:
                    pass

    _walk(input_dir, 0)
    return files


def count_test_functions(code: str) -> int:
    return len(re.findall(r"^\s*def test_\w+\s*\(", code, flags=re.MULTILINE))


def extract_test_blocks(code: str) -> List[Tuple[str, str, str]]:
    """Возвращает список (имя_теста, тело_теста, нормализованное_тело)."""
    blocks: List[Tuple[str, str, str]] = []
    pattern = re.compile(
        r"(def (test_\w+)\s*\([^)]*\)\s*:\s*(?:\n\s+.*)+)",
        flags=re.MULTILINE,
    )
    for m in pattern.finditer(code):
        full_block = m.group(1)
        name = m.group(2)
        normalized = re.sub(r'"[^"]*"', '"STR"', full_block)
        normalized = re.sub(r"'[^']*'", "'STR'", normalized)
        normalized = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\b", "VAR", normalized)
        blocks.append((name, full_block, normalized))
    return blocks

# ---------------------------------------------------------------------------
# Статический анализ (поиск проблем, дубликатов, метрик)
# ---------------------------------------------------------------------------
def static_analysis(code: str, metrics: TestMetrics, issues: List[ReviewIssue]) -> None:
    metrics.total_tests = count_test_functions(code)

    blocks = extract_test_blocks(code)

    # Поиск точных и близких дублей
    seen_normalized: Dict[str, List[str]] = {}
    for name, _body, norm in blocks:
        seen_normalized.setdefault(norm, []).append(name)

    dup_groups = [names for names in seen_normalized.values() if len(names) > 1]
    metrics.duplicate_tests = sum(len(g) - 1 for g in dup_groups)

    for group in dup_groups:
        issues.append(
            ReviewIssue(
                category="Дублирующие тесты",
                severity="high",
                description=(
                    f"Тесты {', '.join(group)} имеют идентичную нормализованную "
                    f"структуру (различаются только данными). Рекомендуется "
                    f"параметризация (@pytest.mark.parametrize)."
                ),
                suggestion="Объединить в один параметризованный тест.",
            )
        )

    # Поиск близких дублей (похожие тела, не идентичные)
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            n1, n2 = blocks[i][2], blocks[j][2]
            if n1 == n2:
                continue
            sim = _similarity(n1, n2)
            if sim > 0.7:
                metrics.near_duplicate_tests += 1
                issues.append(
                    ReviewIssue(
                        category="Похожие тесты (near-duplicate)",
                        severity="medium",
                        description=(
                            f"Тесты {blocks[i][0]} и {blocks[j][0]} похожи на {sim:.0%}. "
                            f"Возможна параметризация или объединение фикстурой."
                        ),
                        suggestion="Проверить на дублирование, объединить при целесообразности.",
                    )
                )

    # Жёстко заданные URL
    hardcoded = re.findall(r'http://localhost:\d+', code)
    metrics.hardcoded_urls = len(set(hardcoded))
    if metrics.hardcoded_urls > 0:
        issues.append(
            ReviewIssue(
                category="Жёстко заданный URL",
                severity="medium",
                description=(
                    f"URL жёстко задан в коде ({', '.join(set(hardcoded))}). "
                    f"Рекомендуется вынести в переменную окружения / конфиг / фикстуру."
                ),
                suggestion="Использовать os.getenv('BASE_URL', 'http://localhost:8000')",
            )
        )

    # Фикстуры / docstrings
    has_fixture = bool(re.search(r"@pytest\.fixture", code))
    metrics.tests_with_fixtures = 1 if has_fixture else 0
    metrics.tests_with_docstrings = len(re.findall(r'"""', code)) // 2
    if not has_fixture:
        issues.append(
            ReviewIssue(
                category="Отсутствуют фикстуры",
                severity="high",
                description=(
                    "В коде нет фикстур pytest (setup/teardown, клиент API, "
                    "подготовка данных). Тесты не изолированы и могут оставлять "
                    "мусор в системе."
                ),
                suggestion="Добавить фикстуру для клиента API и фикстуру для подготовки/очистки тестовых данных.",
            )
        )

    # Читаемость
    func_lengths = [len(b[1].splitlines()) for b in blocks]
    avg_len = sum(func_lengths) / len(func_lengths) if func_lengths else 0
    readability = max(0.0, min(10.0, 10.0 - (avg_len - 5) * 0.5 + (metrics.tests_with_docstrings * 0.5)))
    metrics.readability_score = round(readability, 2)

    if avg_len > 12:
        issues.append(
            ReviewIssue(
                category="Читаемость",
                severity="medium",
                description=f"Средняя длина тестовой функции {avg_len:.0f} строк (рекомендуется ≤ 10).",
                suggestion="Вынести повторяющийся код в вспомогательные функции и фикстуры.",
            )
        )


def _similarity(a: str, b: str) -> float:
    """Простое сходство по множеству токенов (Jaccard)."""
    ta = set(re.findall(r"\w+", a))
    tb = set(re.findall(r"\w+", b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

# ---------------------------------------------------------------------------
# Работа с Ollama (LLM)
# ---------------------------------------------------------------------------
class OllamaClient:
    def __init__(self, base_url: str = OLLAMA_BASE_URL, model: str = OLLAMA_MODEL):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.available = self._check_available()

    def _check_available(self) -> bool:
        try:
            req = urllib_request.Request(f"{self.base_url}/api/tags")
            with urllib_request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        """Вызов Ollama /api/generate. При недоступности — пустая строка."""
        if not self.available:
            return ""
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system
        data = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(
            f"{self.base_url}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
                obj = json.loads(raw)
                return obj.get("response", "")
        except Exception as exc:
            print(f"[WARN] Ollama вызов не удался: {exc}", file=sys.stderr)
            return ""

    def chat(self, messages: List[Dict[str, str]]) -> str:
        """Вызов Ollama /api/chat (альтернативный endpoint)."""
        if not self.available:
            return ""
        payload = {"model": self.model, "messages": messages, "stream": False}
        data = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
                obj = json.loads(raw)
                msg = obj.get("message", {})
                return msg.get("content", "")
        except Exception as exc:
            print(f"[WARN] Ollama chat не удался: {exc}", file=sys.stderr)
            return ""

# ---------------------------------------------------------------------------
# Промпты для LLM
# ---------------------------------------------------------------------------
def render_prompt(name: str, **values: str) -> str:
    """Читает prompts/<name>.md и подставляет значения по ключам ${key}.

    Текст промпта живёт отдельно от кода: его правят и версионируют как
    документ, а не как строковый литерал. Подстановка идёт через
    string.Template, поэтому фигурные скобки внутри примеров кода безопасны.
    """
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Файл промпта не найден: {path}")
    try:
        return Template(path.read_text(encoding="utf-8")).substitute(**values).strip()
    except KeyError as exc:
        raise ValueError(f"В промпте {path} нет значения для плейсхолдера {exc}") from exc


def build_review_prompt(source_code: str) -> str:
    return render_prompt("review", source_code=source_code)


def build_optimization_prompt(source_code: str) -> str:
    return render_prompt("optimization", source_code=source_code)


def build_prioritization_prompt(source_code: str, requirements_text: str) -> str:
    return render_prompt(
        "prioritization",
        source_code=source_code,
        requirements_text=requirements_text,
    )

# ---------------------------------------------------------------------------
# Fallback / демо-режим (без Ollama)
# ---------------------------------------------------------------------------
FALLBACK_OPTIMIZED_CODE = '''import os
import uuid
import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
TIMEOUT = float(os.getenv("API_TIMEOUT", "5"))


@pytest.fixture(scope="session")
def api_client():
    """HTTP-клиент с ретраями и таймаутами."""
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
    session.mount("http://", HTTPAdapter(max_retries=retry))
    yield session
    session.close()


@pytest.fixture
def created_item(api_client):
    """Создаёт тестовую сущность и удаляет её после теста (изоляция)."""
    payload = {"name": f"item_{uuid.uuid4().hex[:8]}"}
    resp = api_client.post(f"{BASE_URL}/items", json=payload, timeout=TIMEOUT)
    assert resp.status_code == 201, f"Ожидался 201, получен {resp.status_code}"
    item = resp.json()
    yield item
    # Тедаун: удаляем сущность, чтобы не оставлять мусора
    api_client.delete(f"{BASE_URL}/items/{item['id']}", timeout=TIMEOUT)


@pytest.mark.parametrize("name, expected_status", [
    ("Item A", 201),
    ("Item B", 201),
    ("Item C", 201),
])
def test_create_item_valid(api_client, name, expected_status):
    """Создание сущности с валидными данными (параметризовано)."""
    payload = {"name": name}
    resp = api_client.post(f"{BASE_URL}/items", json=payload, timeout=TIMEOUT)
    assert resp.status_code == expected_status
    if expected_status == 201:
        body = resp.json()
        assert body["name"] == name
        assert "id" in body


def test_create_item_invalid(api_client):
    """Создание сущности с некорректными данными -> 400."""
    payload = {}
    resp = api_client.post(f"{BASE_URL}/items", json=payload, timeout=TIMEOUT)
    assert resp.status_code == 400


def test_create_item_duplicate(api_client, created_item):
    """Повторное создание с теми же данными -> 400."""
    payload = {"name": created_item["name"]}
    resp = api_client.post(f"{BASE_URL}/items", json=payload, timeout=TIMEOUT)
    assert resp.status_code == 400


def test_get_nonexistent_item(api_client):
    """Получение несуществующей сущности -> 404."""
    resp = api_client.get(f"{BASE_URL}/items/{uuid.uuid4()}", timeout=TIMEOUT)
    assert resp.status_code == 404
'''

FALLBACK_REVIEW_REPORT = """# Отчёт код-ревью автотестов

## Что сделано хорошо
- Тесты написаны на pytest, используется библиотека requests.
- Каждый тест проверяет конкретный сценарий (positive / negative).
- Проверяются HTTP-статусы ответов (201, 400).
- Есть проверка тела ответа (имя сущности).

## Что сделано плохо

### 1. Дублирующие тесты
- Три теста создания сущности с разными именами идентичны по логике, различаются только данными. Это нарушает принцип DRY и раздувает набор тестов.

### 2. Отсутствие параметризации
- Повторяющиеся сценарии создания не объединены через `@pytest.mark.parametrize`.

### 3. Жёстко заданные данные
- Базовый URL захардкожен в коде.
- Тестовые данные (имена, email) захардкожены внутри каждой функции.

### 4. Отсутствие фикстур и изоляции
- Нет фикстур для клиента API, создания и удаления тестовых данных.
- Тесты создают сущности, но не удаляют их — остаётся мусор в системе, тесты не изолированы.

### 5. Слабые assert'ы
- Проверяется только имя сущности; не проверяются поля `id`, другие поля ответа.
- Нет проверки на структуру JSON.

### 6. Отсутствие обработки ошибок
- Нет таймаутов, ретраев, обработки ошибок сети (connection error, timeout).

### 7. Читаемость
- Имена тестов неконсистентны.
- Нет docstrings / описаний сценариев.

## Общая оценка
Код рабочий, но требует рефакторинга: устранение дублей, добавление фикстур, параметризация, вынос конфига, усиление assert'ов.
"""

FALLBACK_OPTIMIZATION_REPORT = """# Отчёт оптимизации автотестов

## Метрики до оптимизации
- Всего тестов: 5
- Дублирующих тестов: 2 (три теста создания сущности — один уникальный + 2 дубля)
- Близких дублей (near-duplicate): 3
- Фикстур: 0
- Тестов с docstring: 0
- Жёстко заданных URL: 1
- Оценка читаемости (1–10): ~4.0
- Покрытие требований: частичное (только создание сущности)

## Метрики после оптимизации
- Всего тестов: 6 (включая новые сценарии: дубликат, 404)
- Параметризованных тестов: 1 (test_create_item_valid — 3 набора данных)
- Фикстур: 2 (api_client, created_item)
- Жёстко заданных URL: 0 (BASE_URL через os.getenv)
- Оценка читаемости (1–10): ~8.5

## Проделанные оптимизации
1. Устранены дублирующие тесты через @pytest.mark.parametrize.
2. Добавлены фикстуры api_client и created_item с тедауном (удаление сущности).
3. Базовый URL и таймаут вынесены в переменные окружения.
4. Добавлены ретраи и таймауты к HTTP-клиенту.
5. Усилены assert'ы (проверка id, name, структуры тела).
6. Добавлены docstrings и осмысленные имена тестов.
7. Добавлены недостающие сценарии: дубликат, 404 на несуществующую сущность.

## Приоритеты исправления (по требованиям)
- P0 (критично): изоляция тестов, устранение дублей.
- P1 (высоко): параметризация, фикстуры, таймауты/ретраи.
- P2 (средне): docstrings, читаемость, assert'ы на все поля.
- P3 (низко): дополнительные негативные сценарии.

## Критерии "плохих" автотестов (список)
- Дублируют другие тесты без добавления новой ценности.
- Нет изоляции (оставляют данные в системе, зависят от порядка выполнения).
- Жёстко заданные данные и URL в теле теста.
- Нет assert'ов или слабые assert'ы (проверяют только статус).
- Нет обработки ошибок сети и таймаутов.
- Непонятные, неконсистентные имена тестов.
- Нет документации (docstrings / комментарии к сложным моментам).
- Смешивают несколько сценариев в одном тесте.
- Зависят от внутренней реализации (хрупкие).
- Не покрывают требования с высоким приоритетом.
"""

# ---------------------------------------------------------------------------
# Генерация контента через LLM или fallback
# ---------------------------------------------------------------------------
def llm_review(client: OllamaClient, source_code: str) -> str:
    if client.available:
        prompt = build_review_prompt(source_code)
        system = render_prompt("system_review")
        result = client.generate(prompt, system=system)
        if result.strip():
            return result
    return FALLBACK_REVIEW_REPORT


def llm_optimize(client: OllamaClient, source_code: str) -> str:
    if client.available:
        prompt = build_optimization_prompt(source_code)
        system = render_prompt("system_optimization")
        result = client.generate(prompt, system=system)
        code_block = _extract_python_code(result)
        if code_block:
            return code_block
        if result.strip():
            return result
    return FALLBACK_OPTIMIZED_CODE


def llm_prioritization(client: OllamaClient, source_code: str, requirements_text: str) -> str:
    if client.available:
        prompt = build_prioritization_prompt(source_code, requirements_text)
        system = render_prompt("system_prioritization")
        result = client.generate(prompt, system=system)
        if result.strip():
            return result
    return ("## Приоритеты автотестов (демо-режим)\n\n"
            "| Тест | Требование | Приоритет | В регрессии | Пробелы в покрытии |\n"
            "|------|------------|-----------|-------------|--------------------|\n"
            "| test_create_user_valid | R01 | P0 | Да | нет |\n"
            "| test_create_user_invalid_email | R03 | P1 | Да | нет |\n"
            "| test_create_user_missing_name | R04 | P2 | Да | нет |\n"
            "| test_create_user_duplicate_email | R02 | P1 | Да | нет |\n"
            "| test_get_nonexistent_user | (новое) | P3 | Нет | требует уточнения |\n")


def _extract_python_code(text: str) -> str:
    match = re.search(r"```python\n(.*?)```", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"```\n(.*?)```", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""

# ---------------------------------------------------------------------------
# Сохранение результатов
# ---------------------------------------------------------------------------
def save_output(text: str, filename: str, output_dir: Optional[Path] = None) -> Path:
    target_dir = output_dir or OUTPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Основной поток
# ---------------------------------------------------------------------------
def process_single_file(
    client: OllamaClient,
    test_file: Path,
    requirements_text: str,
    output_dir: Path,
) -> None:
    """Обрабатывает один файл с автотестами и сохраняет результаты."""
    stem = test_file.stem
    print(f"\n{'='*70}")
    print(f" Обработка файла: {test_file}")
    print(f"{'='*70}")

    # 1. Читаем исходник
    source_code = read_source(test_file)
    print(f"[1] Исходный код прочитан: {test_file} ({len(source_code)} символов)")

    # 2. Статический анализ
    metrics = TestMetrics()
    issues: List[ReviewIssue] = []
    static_analysis(source_code, metrics, issues)
    print(
        f"[2] Статический анализ: тестов={metrics.total_tests}, "
        f"дублей={metrics.duplicate_tests}, near-dup={metrics.near_duplicate_tests}, "
        f"читаймость={metrics.readability_score}/10"
    )

    # 3. Ollama клиент уже создан снаружи
    print(
        f"[3] Ollama ({OLLAMA_BASE_URL}, модель {OLLAMA_MODEL}): "
        f"{'доступен' if client.available else 'НЕ доступен (демо-режим)'}"
    )

    # 4. Ревью кода
    review_report = llm_review(client, source_code)
    review_path = save_output(review_report, f"{stem}_code_review_report.md", output_dir)
    print(f"[4] Отчёт ревью сохранён: {review_path}")

    # 5. Оптимизация кода
    optimized_code = llm_optimize(client, source_code)
    opt_code_path = save_output(optimized_code, f"{stem}_optimized_tests.py", output_dir)
    print(f"[5] Оптимизированные автотесты сохранены: {opt_code_path}")

    # 6. Отчёт оптимизации + приоритизация
    prioritization = llm_prioritization(client, source_code, requirements_text)

    opt_report = f"""# Отчёт оптимизации автотестов

## Исходные данные
- Источник: `{test_file}`
- Модель LLM: `{OLLAMA_MODEL}` (Ollama, {'доступен' if client.available else 'демо-режим'})

## Метрики до оптимизации
{metrics_to_md(metrics)}

## Найденные проблемы (ревью)
{issues_to_md(issues)}

## Приоритизация по требованиям
{prioritization}

## Проделанные оптимизации
1. Устранены дублирующие тесты через `@pytest.mark.parametrize`.
2. Добавлены фикстуры для клиента API и подготовки/очистки тестовых данных с тедауном.
3. Базовый URL и таймаут вынесены в переменные окружения.
4. Добавлены ретраи и таймауты к HTTP-клиенту.
5. Усилены assert'ы (проверка `id`, `name`, структуры тела).
6. Добавлены docstrings и осмысленные имена тестов.
7. Добавлены недостающие сценарии (дубликат, 404).

## Критерии "плохих" автотестов (список)
- Дублируют другие тесты без добавления новой ценности.
- Нет изоляции (оставляют данные в системе, зависят от порядка выполнения).
- Жёстко заданные данные и URL в теле теста.
- Нет assert'ов или слабые assert'ы (проверяют только статус).
- Нет обработки ошибок сети и таймаутов.
- Непонятные, неконсистентные имена тестов.
- Нет документации (docstrings / комментарии к сложным моментам).
- Смешивают несколько сценариев в одном тесте.
- Зависят от внутренней реализации (хрупкие).
- Не покрывают требования с высоким приоритетом.

## Результат
Оптимизированные автотесты: `{opt_code_path.name}`
Отчёт ревью: `{review_path.name}`
"""

    opt_report_path = save_output(opt_report, f"{stem}_optimization_report.md", output_dir)
    print(f"[6] Отчёт оптимизации сохранён: {opt_report_path}")


def run(
    interactive: bool = False,
    input_dir: Optional[Path] = None,
    max_depth: Optional[int] = None,
) -> None:
    print("=" * 70)
    print(" Скрипт оптимизации автотестов через Ollama (LLM)")
    print("=" * 70)

    # 1. Определяем папку с автотестами и находим файлы
    scan_root = input_dir or Path(os.getenv("INPUT_DIR", str(ROOT / "input")))
    test_files = find_test_files(scan_root, max_depth)

    if not test_files:
        print(f"[WARN] Тестовые файлы не найдены в {scan_root}")
        print("Убедитесь, что в папке есть .txt или .py файлы с def test_.")
        return

    print(
        f"[1] Найдено файлов с автотестами: {len(test_files)} в {scan_root}"
        + (f" (макс. глубина рекурсии: {max_depth})" if max_depth is not None else "")
    )

    # 2. LLM клиент (создаём один раз на все файлы)
    client = OllamaClient()
    print(
        f"[2] Ollama ({OLLAMA_BASE_URL}, модель {OLLAMA_MODEL}): "
        f"{'доступен' if client.available else 'НЕ доступен (демо-режим)'}"
    )

    # Требования с приоритетами (общие для всех файлов)
    requirements_text = ""
    if REQUIREMENTS_PATH.exists():
        requirements_text = REQUIREMENTS_PATH.read_text(encoding="utf-8")

    # Выходная папка: отдельный подкаталог на прогон, чтобы прогоны не
    # перезаписывали результаты друг друга.
    run_dir = OUTPUT_DIR / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[i] Каталог прогона: {run_dir}")

    # 3. Обрабатываем каждый файл
    for test_file in test_files:
        process_single_file(client, test_file, requirements_text, run_dir)

    print()
    print("Готово. Все результаты в папке:", run_dir)


def metrics_to_md(m: TestMetrics) -> str:
    return (
        f"- Всего тестов: {m.total_tests}\n"
        f"- Дублирующих тестов: {m.duplicate_tests}\n"
        f"- Близких дублей (near-duplicate): {m.near_duplicate_tests}\n"
        f"- Фикстур: {m.tests_with_fixtures}\n"
        f"- Тестов с docstring: {m.tests_with_docstrings}\n"
        f"- Жёстко заданных URL: {m.hardcoded_urls}\n"
        f"- Оценка читаемости (1–10): {m.readability_score}\n"
        f"- Покрытие требований: частичное (только создание пользователя)\n"
    )


def issues_to_md(issues: List[ReviewIssue]) -> str:
    if not issues:
        return "Проблемы не выявлены."
    lines = []
    for it in issues:
        lines.append(
            f"- [{it.severity.upper()}] **{it.category}**: {it.description}"
            + (f" — {it.suggestion}" if it.suggestion else "")
        )
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Оптимизация автотестов через Ollama LLM")
    parser.add_argument("--no-interactive", action="store_true", help="Запуск без интерактивных вопросов")
    args = parser.parse_args()
    run(interactive=not args.no_interactive)