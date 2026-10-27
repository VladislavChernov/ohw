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
  - Ollama запущен (по умолчанию http://localhost:11434), модель: qwen2.5:7b-instruct.
  - pip install -r requirements/requirements.txt

Если Ollama недоступен — скрипт работает в демо-режиме (fallback): берёт
заранее написанный код и отчёт. Источник каждого артефакта (LLM или fallback)
и причина указываются в отчёте оптимизации.

Метрики, которые измерить нельзя (например, код не разбирается парсером или
сопоставление тестов с требованиями не реализовано), печатаются как `unknown`,
а не как ноль.
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import re
import sys
from dataclasses import dataclass, field
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
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b-instruct"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
REQUEST_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))

# HTTP-методы, для которых таймаут в вызове считается осмысленным.
HTTP_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "request"}
)


@dataclass
class TestMetrics:
    """Метрики статического анализа.

    `None` означает «не измерено» и печатается как `unknown`. Ноль означает
    измеренный ноль: например, в коде действительно нет дублей.
    """

    total_tests: Optional[int] = None
    duplicate_tests: Optional[int] = None
    near_duplicate_tests: Optional[int] = None
    parametrized_tests: Optional[int] = None
    tests_with_fixtures: Optional[int] = None
    tests_with_docstrings: Optional[int] = None
    hardcoded_urls: Optional[int] = None
    missing_assertions: Optional[int] = None
    readability_score: Optional[float] = None
    requirements_coverage_pct: Optional[float] = None


@dataclass
class CodeProfile:
    """Измеренные по AST факты о файле с тестами."""

    parses: bool
    parse_error: Optional[str] = None
    test_count: Optional[int] = None
    duplicate_tests: Optional[int] = None
    duplicate_group_sizes: List[int] = field(default_factory=list)
    near_duplicate_pairs: Optional[int] = None
    parametrized_tests: Optional[int] = None
    fixtures: List[str] = field(default_factory=list)
    tests_with_fixtures: Optional[int] = None
    tests_with_docstrings: Optional[int] = None
    tests_without_assertions: Optional[int] = None
    hardcoded_urls: List[str] = field(default_factory=list)
    env_url_defaults: List[str] = field(default_factory=list)
    has_timeout: Optional[bool] = None
    has_teardown: Optional[bool] = None
    avg_test_length: Optional[float] = None
    max_test_length: Optional[int] = None


@dataclass
class ReviewIssue:
    category: str
    severity: str  # critical / high / medium / low
    description: str
    line: Optional[int] = None
    suggestion: Optional[str] = None


@dataclass
class Generation:
    """Ответ LLM или fallback. Источник виден в отчёте, а не угадывается."""

    text: str
    source: str  # "llm" | "fallback"
    reason: str = ""


@dataclass
class ValidationResult:
    parses: bool
    tests: Optional[int] = None
    parse_error: Optional[str] = None
    problems: List[str] = field(default_factory=list)
    forbidden: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.parses and not self.problems and not self.forbidden


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


def parse_code(code: str) -> Tuple[Optional[ast.Module], Optional[str]]:
    """Разбирает код и возвращает (дерево, текст ошибки). Не бросает исключений."""
    try:
        return ast.parse(code), None
    except SyntaxError as exc:
        where = f"строка {exc.lineno}" if exc.lineno else "строка неизвестна"
        return None, f"{exc.msg} ({where})"


def iter_test_functions(
    code: str,
) -> Tuple[List[Any], Optional[str]]:
    """Возвращает test_*-функции по порядку и ошибку разбора (если есть)."""
    tree, error = parse_code(code)
    if tree is None:
        return [], error
    tests = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    tests.sort(key=lambda node: (node.lineno, node.col_offset))
    return tests, None


def count_test_functions(code: str) -> Optional[int]:
    """Число test_*-функций; None, если код не разбирается."""
    tests, error = iter_test_functions(code)
    return None if error else len(tests)


class _DataNeutralizer(ast.NodeTransformer):
    """Заменяет тестовые данные, сохраняя структуру кода.

    Нейтрализуются только строковые литералы-значения. Не трогаются:
      * ключи словарей и срезы `body["name"]` — это структура, а не данные;
      * строки внутри f-строк — обычно это путь эндпоинта;
      * числа — это коды ответов, длины и пороги, а не тестовые данные.

    Так `assert body["name"] == "Alice"` и `... == "Bob"` считаются одной
    структурой, а `["name"]` и `["email"]` или `201` и `400` — разными.
    """

    def __init__(self, preserve: set[int]) -> None:
        self._preserve = preserve

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if id(node) in self._preserve:
            return node
        if isinstance(node.value, str):
            return ast.copy_location(ast.Constant(value="STR"), node)
        return node


def _strings_to_preserve(node: Any) -> set[int]:
    preserve: set[int] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Dict):
            for key in sub.keys:
                if key is None:
                    continue
                for inner in ast.walk(key):
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                        preserve.add(id(inner))
        elif isinstance(sub, ast.Subscript):
            for inner in ast.walk(sub.slice):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    preserve.add(id(inner))
        elif isinstance(sub, ast.JoinedStr):
            for inner in ast.walk(sub):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    preserve.add(id(inner))
    return preserve


def _normalized_body(node: Any) -> str:
    body = copy.deepcopy(node.body)
    module = ast.Module(body=body, type_ignores=[])
    neutralized = _DataNeutralizer(_strings_to_preserve(module)).visit(module)
    return ast.unparse(neutralized)


def _function_source(node: Any, lines: List[str]) -> str:
    """Исходный текст функции вместе с декораторами."""
    start = min([d.lineno for d in node.decorator_list] + [node.lineno])
    end = node.end_lineno or node.lineno
    return "\n".join(lines[start - 1:end])


def extract_test_blocks(code: str) -> List[Tuple[str, str, str]]:
    """Возвращает список (имя_теста, исходный_текст, нормализованное_тело).

    Границы функции берутся у AST (`lineno`/`end_lineno`), поэтому блок одного
    теста не «съедает» соседние. Нормализация заменяет строковые и числовые
    литералы, чтобы тесты, различающиеся только данными, считались дублями.
    """
    tests, error = iter_test_functions(code)
    if error:
        return []
    lines = code.splitlines()
    return [
        (node.name, _function_source(node, lines), _normalized_body(node))
        for node in tests
    ]


# ---------------------------------------------------------------------------
# Статический анализ (поиск проблем, дубликатов, метрик)
# ---------------------------------------------------------------------------
def _decorator_name(decorator: ast.AST) -> str:
    """Имя декоратора без аргументов: `pytest.mark.parametrize`, `pytest.fixture`."""
    node: Any = decorator
    if isinstance(node, ast.Call):
        node = node.func
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _is_fixture(node: Any) -> bool:
    return any(
        _decorator_name(d) in {"pytest.fixture", "fixture"}
        for d in node.decorator_list
    )


def _has_parametrize(node: Any) -> bool:
    return any(
        _decorator_name(d).endswith("parametrize") for d in node.decorator_list
    )


def _fixture_names(tree: ast.Module) -> List[str]:
    names = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _is_fixture(node)
    ]
    return sorted(names)


def _argument_names(node: Any) -> List[str]:
    args = node.args
    names = [a.arg for a in getattr(args, "posonlyargs", []) + args.args]
    names += [a.arg for a in args.kwonlyargs]
    return [name for name in names if name not in {"self", "cls"}]


def _called_name(node: Any) -> str:
    """Полное имя вызываемого объекта для `pytest.raises(...)` и подобных."""
    func = node.func if isinstance(node, ast.Call) else node
    parts: List[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def _has_assertion(node: Any) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assert):
            return True
        if isinstance(sub, ast.Call) and _called_name(sub) in {
            "pytest.raises",
            "pytest.fail",
        }:
            return True
    return False


def _collect_urls(tree: ast.Module) -> Tuple[List[str], List[str]]:
    """URL в коде: (жёстко заданные, значения по умолчанию в os.getenv).

    URL, переданный вторым аргументом `os.getenv(...)`, не считается хардкодом:
    это документированное значение по умолчанию, которое переопределяется
    окружением. Он возвращается отдельным списком, чтобы отчёт не скрывал его.
    """
    parents: Dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    hardcoded: List[str] = []
    env_defaults: List[str] = []
    url_re = re.compile(r"https?://[^\s'\"<>`]+")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        urls = url_re.findall(node.value)
        if not urls:
            continue
        parent = parents.get(id(node))
        bucket = hardcoded
        if isinstance(parent, ast.Call):
            func = parent.func
            is_getenv = (
                isinstance(func, ast.Attribute) and func.attr == "getenv"
            ) or (isinstance(func, ast.Name) and func.id == "getenv")
            if is_getenv and node in parent.args[1:]:
                bucket = env_defaults
        bucket.extend(urls)
    return sorted(set(hardcoded)), sorted(set(env_defaults))


def _has_timeout(tree: ast.Module) -> bool:
    """Есть ли таймаут в HTTP-вызовах (requests/клиент), а не в любом вызове."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        method = _called_name(node).split(".")[-1].lower()
        if method not in HTTP_METHODS:
            continue
        if any(keyword.arg == "timeout" for keyword in node.keywords):
            return True
    return False


def _contains_yield(block: List[ast.stmt]) -> bool:
    return any(
        isinstance(node, ast.Yield) for stmt in block for node in ast.walk(stmt)
    )


def _block_has_teardown(block: List[ast.stmt]) -> bool:
    for index, stmt in enumerate(block):
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Yield):
            if index < len(block) - 1:
                return True
        # try/finally: код в finally выполняется после yield из try.
        if isinstance(stmt, ast.Try) and stmt.finalbody and _contains_yield(stmt.body):
            return True
        for attr in ("body", "orelse", "finalbody"):
            inner = getattr(stmt, attr, None)
            if isinstance(inner, list) and _block_has_teardown(inner):
                return True
        for handler in getattr(stmt, "handlers", []):
            if _block_has_teardown(handler.body):
                return True
    return False


def _uses_addfinalizer(node: Any) -> bool:
    return any(
        isinstance(sub, ast.Call) and _called_name(sub) == "request.addfinalizer"
        for sub in ast.walk(node)
    )


def _has_teardown(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_fixture(node):
            continue
        if _block_has_teardown(node.body) or _uses_addfinalizer(node):
            return True
    return False


def _has_autouse_fixture(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not _decorator_name(decorator).endswith("fixture"):
                continue
            for keyword in decorator.keywords:
                if keyword.arg == "autouse" and isinstance(
                    keyword.value, ast.Constant
                ) and keyword.value.value is True:
                    return True
    return False


def analyze_code(code: str) -> CodeProfile:
    """Считает всё, что можно измерить по AST, за один проход."""
    tree, error = parse_code(code)
    if tree is None:
        return CodeProfile(parses=False, parse_error=error)

    tests, _ = iter_test_functions(code)
    fixtures = _fixture_names(tree)
    fixture_set = set(fixtures)
    blocks = extract_test_blocks(code)

    seen: Dict[str, List[str]] = {}
    for name, _source, normalized in blocks:
        seen.setdefault(normalized, []).append(name)
    duplicate_groups = [names for names in seen.values() if len(names) > 1]

    near_pairs = 0
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            if blocks[i][2] == blocks[j][2]:
                continue
            if _similarity(blocks[i][2], blocks[j][2]) > 0.7:
                near_pairs += 1

    test_lengths = [
        ((node.end_lineno or node.lineno) - node.lineno + 1) for node in tests
    ]
    hardcoded, env_defaults = _collect_urls(tree)

    return CodeProfile(
        parses=True,
        test_count=len(tests),
        duplicate_tests=sum(len(group) - 1 for group in duplicate_groups),
        duplicate_group_sizes=sorted((len(group) for group in duplicate_groups), reverse=True),
        near_duplicate_pairs=near_pairs,
        parametrized_tests=sum(1 for node in tests if _has_parametrize(node)),
        fixtures=fixtures,
        tests_with_fixtures=(
            len(tests)
            if _has_autouse_fixture(tree)
            else sum(1 for node in tests if set(_argument_names(node)) & fixture_set)
        ),
        tests_with_docstrings=sum(
            1 for node in tests if ast.get_docstring(node) is not None
        ),
        tests_without_assertions=sum(
            1 for node in tests if not _has_assertion(node)
        ),
        hardcoded_urls=hardcoded,
        env_url_defaults=env_defaults,
        has_timeout=_has_timeout(tree),
        has_teardown=_has_teardown(tree),
        avg_test_length=(sum(test_lengths) / len(test_lengths) if test_lengths else 0.0),
        max_test_length=max(test_lengths) if test_lengths else 0,
    )


def readability_score(profile: CodeProfile) -> Optional[float]:
    """Оценка 1–10 по документированной формуле; None, если мерить нечего.

    Формула измерима и повторяема:
      * штраф 0.5 за каждую строку средней длины теста сверх 8;
      * штраф 1.0, если самый длинный тест больше 30 строк;
      * штраф до 3.0 за отсутствие docstrings (пропорционально доле тестов).

    Это оценка стиля, а не качества набора: дубли, покрытие и изоляция
    измеряются отдельными полями.
    """
    if not profile.parses or not profile.test_count:
        return None
    score = 10.0
    if profile.avg_test_length:
        score -= max(0.0, profile.avg_test_length - 8) * 0.5
    if profile.max_test_length and profile.max_test_length > 30:
        score -= 1.0
    doc_ratio = (profile.tests_with_docstrings or 0) / profile.test_count
    score -= 3.0 * (1.0 - doc_ratio)
    return round(min(10.0, max(1.0, score)), 2)


def static_analysis(
    code: str, metrics: TestMetrics, issues: List[ReviewIssue]
) -> CodeProfile:
    """Заполняет метрики и список проблем; возвращает измеренный профиль."""
    profile = analyze_code(code)

    if not profile.parses:
        issues.append(
            ReviewIssue(
                category="Синтаксическая ошибка",
                severity="critical",
                description=(
                    f"Код не разбирается парсером Python: {profile.parse_error}. "
                    f"Метрики не измерены (unknown)."
                ),
                suggestion="Исправить синтаксис и повторить анализ.",
            )
        )
        return profile

    metrics.total_tests = profile.test_count
    metrics.duplicate_tests = profile.duplicate_tests
    metrics.near_duplicate_tests = profile.near_duplicate_pairs
    metrics.parametrized_tests = profile.parametrized_tests
    metrics.tests_with_fixtures = profile.tests_with_fixtures
    metrics.tests_with_docstrings = profile.tests_with_docstrings
    metrics.hardcoded_urls = len(profile.hardcoded_urls)
    metrics.missing_assertions = profile.tests_without_assertions
    metrics.readability_score = readability_score(profile)
    # Сопоставления «тест -> требование» в проекте нет, поэтому процент
    # покрытия не вычисляется. Ноль здесь означал бы измеренный ноль.
    metrics.requirements_coverage_pct = None

    if profile.duplicate_group_sizes:
        for size in profile.duplicate_group_sizes:
            issues.append(
                ReviewIssue(
                    category="Дублирующие тесты",
                    severity="high",
                    description=(
                        f"Найдена группа из {size} тестов с идентичной "
                        f"структурой (различаются только данными). Лишних "
                        f"копий: {size - 1}. Рекомендуется параметризация "
                        f"(@pytest.mark.parametrize)."
                    ),
                    suggestion="Объединить в один параметризованный тест.",
                )
            )

    if profile.near_duplicate_pairs:
        issues.append(
            ReviewIssue(
                category="Похожие тесты (near-duplicate)",
                severity="medium",
                description=(
                    f"Пар тестов со сходством структуры выше 70%: "
                    f"{profile.near_duplicate_pairs}. Проверить на дублирование."
                ),
                suggestion="Объединить при целесообразности или вынести шаги в фикстуру.",
            )
        )

    if profile.hardcoded_urls:
        issues.append(
            ReviewIssue(
                category="Жёстко заданный URL",
                severity="medium",
                description=(
                    f"URL задан в коде: {', '.join(profile.hardcoded_urls)}. "
                    f"Рекомендуется вынести в переменную окружения / конфиг."
                ),
                suggestion="Использовать os.getenv('BASE_URL', 'http://localhost:8000')",
            )
        )

    if not profile.fixtures:
        issues.append(
            ReviewIssue(
                category="Отсутствуют фикстуры",
                severity="high",
                description=(
                    "В коде нет фикстур pytest (setup/teardown, клиент API, "
                    "подготовка данных). Тесты не изолированы и могут оставлять "
                    "мусор в системе."
                ),
                suggestion=(
                    "Добавить фикстуру для клиента API и фикстуру для "
                    "подготовки/очистки тестовых данных."
                ),
            )
        )
    elif profile.tests_with_fixtures < profile.test_count:
        issues.append(
            ReviewIssue(
                category="Тесты без фикстур",
                severity="low",
                description=(
                    f"Локальные фикстуры объявлены ({', '.join(profile.fixtures)}), "
                    f"но используются только в {profile.tests_with_fixtures} из "
                    f"{profile.test_count} тестов."
                ),
                suggestion="Подключить фикстуры клиента и очистки данных в остальных тестах.",
            )
        )

    if profile.tests_without_assertions:
        issues.append(
            ReviewIssue(
                category="Нет assert'ов",
                severity="high",
                description=(
                    f"Тестов без assert (и без pytest.raises): "
                    f"{profile.tests_without_assertions}. Такой тест проверяет "
                    f"только отсутствие исключения."
                ),
                suggestion="Добавить проверки статуса и полей ответа.",
            )
        )

    if profile.avg_test_length and profile.avg_test_length > 12:
        issues.append(
            ReviewIssue(
                category="Читаемость",
                severity="medium",
                description=(
                    f"Средняя длина тестовой функции "
                    f"{profile.avg_test_length:.0f} строк (рекомендуется ≤ 10)."
                ),
                suggestion="Вынести повторяющийся код во вспомогательные функции и фикстуры.",
            )
        )

    if metrics.readability_score is not None and metrics.readability_score < 6:
        issues.append(
            ReviewIssue(
                category="Низкая читаемость",
                severity="medium",
                description=(
                    f"Оценка читаемости {metrics.readability_score}/10 по формуле "
                    f"длины тестов и наличия docstrings."
                ),
                suggestion="Сократить тесты и добавить docstrings.",
            )
        )

    return profile


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
        self.last_error: Optional[str] = None
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
        self.last_error = None
        try:
            with urllib_request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
                obj = json.loads(raw)
                return obj.get("response", "")
        except Exception as exc:
            self.last_error = str(exc)
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
        self.last_error = None
        try:
            with urllib_request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
                obj = json.loads(raw)
                msg = obj.get("message", {})
                return msg.get("content", "")
        except Exception as exc:
            self.last_error = str(exc)
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
FALLBACK_OPTIMIZED_CODE = '''"""Демонстрационный вариант тестов API /users (pytest + requests).

Это fallback: код написан заранее и не является ответом модели. Он показывает
структуру, которую требует промпт: параметризация, фикстуры, конфигурация из
окружения, изоляция созданных данных. Стенда в проекте нет, поэтому код не
запускался — проверяйте его на своём API.
"""

import os
import uuid

import pytest
import requests

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = float(os.getenv("API_TIMEOUT", "5"))


@pytest.fixture(scope="session")
def api_client():
    """HTTP-сессия с общим Accept; таймаут задаётся в каждом запросе."""
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    yield session
    session.close()


@pytest.fixture
def created_users(api_client):
    """Собирает id созданных пользователей и удаляет только их после теста."""
    user_ids = []
    yield user_ids
    for user_id in reversed(user_ids):
        api_client.delete(f"{BASE_URL}/users/{user_id}", timeout=TIMEOUT)


@pytest.mark.parametrize(
    "name",
    ["Alice", "Bob", "Charlie"],
)
def test_create_user_valid(api_client, created_users, name):
    """Валидный пользователь создаётся, поля ответа совпадают с запросом."""
    payload = {
        "name": name,
        "email": f"{name.lower()}.{uuid.uuid4().hex[:8]}@example.com",
    }
    response = api_client.post(f"{BASE_URL}/users", json=payload, timeout=TIMEOUT)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == payload["name"]
    assert body["email"] == payload["email"]
    assert body["id"]
    created_users.append(body["id"])


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Test", "email": "bad-email"},
        {"email": "test@example.com"},
    ],
    ids=["invalid-email", "missing-name"],
)
def test_create_user_invalid_payload(api_client, payload):
    """Невалидные данные отклоняются с кодом 400."""
    response = api_client.post(f"{BASE_URL}/users", json=payload, timeout=TIMEOUT)
    assert response.status_code == 400, response.text


def test_create_user_duplicate_email(api_client, created_users):
    """Повторная регистрация с тем же email отклоняется."""
    payload = {
        "name": "Alice",
        "email": f"duplicate.{uuid.uuid4().hex[:8]}@example.com",
    }

    first = api_client.post(f"{BASE_URL}/users", json=payload, timeout=TIMEOUT)
    assert first.status_code == 201, first.text
    created_users.append(first.json()["id"])

    second = api_client.post(f"{BASE_URL}/users", json=payload, timeout=TIMEOUT)
    assert second.status_code == 400, second.text
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

BAD_TESTS_CRITERIA = """## Критерии "плохих" автотестов (список)
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
def _empty_response_reason(client: OllamaClient) -> str:
    """Почему ответа нет: сбой запроса или пустой ответ модели."""
    if getattr(client, "last_error", None):
        return f"запрос к Ollama не удался: {client.last_error}"
    return "модель вернула пустой ответ"


def llm_review(client: OllamaClient, source_code: str) -> Generation:
    if not client.available:
        return Generation(FALLBACK_REVIEW_REPORT, "fallback", "Ollama недоступен")
    prompt = build_review_prompt(source_code)
    system = render_prompt("system_review")
    result = client.generate(prompt, system=system)
    if result.strip():
        return Generation(result, "llm")
    return Generation(
        FALLBACK_REVIEW_REPORT, "fallback", _empty_response_reason(client)
    )


def llm_optimize(client: OllamaClient, source_code: str) -> Generation:
    """Возвращает код и его источник. Fallback никогда не молчит."""
    if not client.available:
        return Generation(
            FALLBACK_OPTIMIZED_CODE, "fallback", "Ollama недоступен"
        )
    prompt = build_optimization_prompt(source_code)
    system = render_prompt("system_optimization")
    result = client.generate(prompt, system=system)
    if not result.strip():
        return Generation(
            FALLBACK_OPTIMIZED_CODE, "fallback", _empty_response_reason(client)
        )

    code_block = _extract_python_code(result)
    if code_block:
        return Generation(code_block, "llm")

    # Некоторые модели отвечают кодом без markdown-обёртки. Принимаем такой
    # ответ только если он действительно разбирается как Python.
    if parse_code(result)[0] is not None:
        return Generation(
            result.strip(),
            "llm",
            "ответ без markdown-блока, но синтаксически корректен",
        )

    print(
        "[WARN] Ответ модели не содержит Python-кода; используется fallback. "
        f"Начало ответа: {result.strip()[:200]!r}",
        file=sys.stderr,
    )
    return Generation(
        FALLBACK_OPTIMIZED_CODE,
        "fallback",
        "ответ модели не является Python-кодом",
    )


def llm_prioritization(
    client: OllamaClient, source_code: str, requirements_text: str
) -> Generation:
    if not client.available:
        return Generation(
            FALLBACK_PRIORITIZATION, "fallback", "Ollama недоступен"
        )
    prompt = build_prioritization_prompt(source_code, requirements_text)
    system = render_prompt("system_prioritization")
    result = client.generate(prompt, system=system)
    if result.strip():
        return Generation(result, "llm")
    return Generation(
        FALLBACK_PRIORITIZATION, "fallback", _empty_response_reason(client)
    )


FALLBACK_PRIORITIZATION = (
    "## Приоритеты автотестов (демо-режим)\n\n"
    "| Тест | Требование | Приоритет | В регрессии | Пробелы в покрытии |\n"
    "|------|------------|-----------|-------------|--------------------|\n"
    "| test_create_user_valid | R01 | P0 | Да | нет |\n"
    "| test_create_user_invalid_email | R03 | P1 | Да | нет |\n"
    "| test_create_user_missing_name | R04 | P2 | Да | нет |\n"
    "| test_create_user_duplicate_email | R02 | P1 | Да | нет |\n"
    "| test_get_nonexistent_user | (новое) | P3 | Нет | требует уточнения |\n"
    "\n> Демонстрационная таблица: ID требований и пробелы не проверялись, "
    "процент покрытия не измерен (unknown).\n"
)


def _extract_python_code(text: str) -> str:
    match = re.search(r"```python\n(.*?)```", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"```\n(.*?)```", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


# ---------------------------------------------------------------------------
# Валидация сгенерированного кода
# ---------------------------------------------------------------------------
def _forbidden_findings(tree: ast.Module) -> List[str]:
    """Запрещённые конструкции, найденные по AST (а не по тексту строк)."""
    findings: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assert)
            and isinstance(node.test, ast.Constant)
            and node.test.value is True
        ):
            findings.append((node.lineno, "assert True"))
        elif isinstance(node, ast.Call) and _called_name(node) == "time.sleep":
            findings.append((node.lineno, "time.sleep(...)"))
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            findings.append((node.lineno, "bare except"))
    return [f"{label} (строка {lineno})" for lineno, label in sorted(findings)]


def validate_generated_tests(code: str) -> ValidationResult:
    """Статическая проверка до сохранения артефакта.

    Проверяет синтаксис, наличие тестов и запрещённые паттерны. Не запускает
    код: стенда с тестируемым API в проекте нет.
    """
    result = ValidationResult(parses=False)
    tree, error = parse_code(code)
    if tree is None:
        result.parse_error = error
        result.problems.append(f"код не разбирается: {error}")
        return result

    result.parses = True
    result.tests = count_test_functions(code)
    if not result.tests:
        result.problems.append("нет ни одной функции test_*")
    result.forbidden = _forbidden_findings(tree)
    return result


def _draft_header(validation: ValidationResult) -> str:
    lines = ["# ЧЕРНОВИК: сгенерированный код не прошёл статическую проверку."]
    lines += [f"# - {problem}" for problem in validation.problems]
    lines += [f"# - запрещённый паттерн: {problem}" for problem in validation.forbidden]
    return "\n".join(lines) + "\n\n"


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
def _fmt(value: Any, unit: str = "") -> str:
    """Печать метрики: None — это `unknown`, а не ноль."""
    if value is None:
        return "unknown"
    return f"{value}{unit}"


def _yes_no(value: Optional[bool]) -> str:
    if value is None:
        return "unknown"
    return "да" if value else "нет"


def describe_changes(before: CodeProfile, after: CodeProfile) -> str:
    """Факты об изменениях, измеренные по AST, а не текстом шаблона."""
    if not after.parses:
        return (
            "- unknown: сгенерированный код не разбирается, поэтому изменения "
            f"не измерены ({after.parse_error})."
        )
    lines = [
        f"- Функций `test_*` (без учёта параметров): "
        f"{_fmt(before.test_count)} → {_fmt(after.test_count)}",
        f"- Дублирующих тестов (лишних копий): "
        f"{_fmt(before.duplicate_tests)} → {_fmt(after.duplicate_tests)}",
        f"- Параметризованных тестов: "
        f"{_fmt(before.parametrized_tests)} → {_fmt(after.parametrized_tests)}",
        f"- Объявленных фикстур: {len(before.fixtures)} → {len(after.fixtures)}"
        + (f" ({', '.join(after.fixtures)})" if after.fixtures else ""),
        f"- Тестов с docstring: "
        f"{_fmt(before.tests_with_docstrings)} → {_fmt(after.tests_with_docstrings)}",
        f"- Тестов без assert: "
        f"{_fmt(before.tests_without_assertions)} → "
        f"{_fmt(after.tests_without_assertions)}",
        f"- Жёстко заданных URL: "
        f"{len(before.hardcoded_urls)} → {len(after.hardcoded_urls)}",
    ]
    if after.env_url_defaults:
        defaults = ", ".join(f"`{url}`" for url in after.env_url_defaults)
        lines.append(f"- URL в значении по умолчанию `os.getenv`: {defaults}")
    lines += [
        f"- Таймаут в запросах: {_yes_no(before.has_timeout)} → {_yes_no(after.has_timeout)}",
        f"- Teardown в фикстурах: {_yes_no(before.has_teardown)} → {_yes_no(after.has_teardown)}",
        f"- Средняя длина теста: {_fmt(_rounded(before.avg_test_length))} → "
        f"{_fmt(_rounded(after.avg_test_length))} строк",
    ]
    return "\n".join(lines)


def _rounded(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 1)


def validation_to_md(validation: ValidationResult) -> str:
    status = "принят" if validation.ok else "**ЧЕРНОВИК** (файл помечен комментарием)"
    lines = [f"- Статус: {status}"]
    if validation.parses:
        lines.append("- `ast.parse`: OK")
    else:
        lines.append(f"- `ast.parse`: ошибка — {validation.parse_error}")
    lines.append(f"- Функций `test_*` в файле: {_fmt(validation.tests)}")

    if not validation.parses:
        lines.append(
            "- Запрещённые паттерны: unknown (код не разбирается)"
        )
    elif validation.forbidden:
        lines.append("- Запрещённые паттерны:")
        lines += [f"  - {problem}" for problem in validation.forbidden]
    else:
        lines.append(
            "- Запрещённые паттерны (`assert True`, `time.sleep`, bare `except`): нет"
        )
    for problem in validation.problems:
        lines.append(f"- Проблема: {problem}")
    return "\n".join(lines)


def _generation_md(label: str, generation: Generation) -> str:
    if generation.source == "llm":
        suffix = f" ({generation.reason})" if generation.reason else ""
        return f"- {label}: получен от модели{suffix}"
    return f"- {label}: **fallback** — {generation.reason}"


def build_optimization_report(
    test_file: Path,
    metrics: TestMetrics,
    issues: List[ReviewIssue],
    analysis: CodeProfile,
    review: Generation,
    optimization: Generation,
    prioritization: Generation,
    validation: ValidationResult,
    client_available: bool,
) -> str:
    after = analyze_code(optimization.text)
    mode = "доступен" if client_available else "недоступен"
    return f"""# Отчёт оптимизации автотестов

## Исходные данные
- Источник: `{test_file}`
- Модель LLM: `{OLLAMA_MODEL}` (Ollama: {mode})
{_generation_md("Отчёт ревью", review)}
{_generation_md("Оптимизированный код", optimization)}
{_generation_md("Приоритизация", prioritization)}

## Валидация сгенерированного кода
{validation_to_md(validation)}

> Проверка статическая: сгенерированные тесты не запускались — стенда с
> тестируемым API в проекте нет.

## Метрики до оптимизации
{metrics_to_md(metrics)}

## Найденные проблемы (ревью)
{issues_to_md(issues)}

## Приоритизация по требованиям
{prioritization.text}

## Что изменилось (измерено по AST)
{describe_changes(analysis, after)}

> Это факты о коде, а не подтверждение поведения: тесты не запускались.

{BAD_TESTS_CRITERIA}
## Результат
- Оптимизированные автотесты: `{test_file.stem}_optimized_tests.py`
- Отчёт ревью: `{test_file.stem}_code_review_report.md`
"""


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
    analysis = static_analysis(source_code, metrics, issues)
    print(
        f"[2] Статический анализ: тестов={_fmt(metrics.total_tests)}, "
        f"дублей={_fmt(metrics.duplicate_tests)}, "
        f"near-dup={_fmt(metrics.near_duplicate_tests)}, "
        f"читаймость={_fmt(metrics.readability_score)}/10"
    )
    if not analysis.parses:
        print(
            f"[WARN] Исходный код не разбирается: {analysis.parse_error}",
            file=sys.stderr,
        )

    # 3. Ollama клиент уже создан снаружи
    print(
        f"[3] Ollama ({OLLAMA_BASE_URL}, модель {OLLAMA_MODEL}): "
        f"{'доступен' if client.available else 'НЕ доступен (демо-режим)'}"
    )

    # 4. Ревью кода
    review = llm_review(client, source_code)
    review_path = save_output(
        review.text, f"{stem}_code_review_report.md", output_dir
    )
    print(f"[4] Отчёт ревью сохранён: {review_path} (источник: {review.source})")

    # 5. Оптимизация кода + статическая валидация до сохранения
    optimization = llm_optimize(client, source_code)
    validation = validate_generated_tests(optimization.text)
    code_to_save = optimization.text
    if not validation.ok:
        code_to_save = _draft_header(validation) + code_to_save
        print(
            "[WARN] Сгенерированный код не прошёл валидацию: "
            + "; ".join(validation.problems + validation.forbidden),
            file=sys.stderr,
        )
    opt_code_path = save_output(
        code_to_save, f"{stem}_optimized_tests.py", output_dir
    )
    print(
        f"[5] Оптимизированные автотесты сохранены: {opt_code_path} "
        f"(источник: {optimization.source}, валидация: {'OK' if validation.ok else 'ЧЕРНОВИК'})"
    )

    # 6. Отчёт оптимизации + приоритизация
    prioritization = llm_prioritization(client, source_code, requirements_text)
    opt_report = build_optimization_report(
        test_file=test_file,
        metrics=metrics,
        issues=issues,
        analysis=analysis,
        review=review,
        optimization=optimization,
        prioritization=prioritization,
        validation=validation,
        client_available=client.available,
    )
    opt_report_path = save_output(
        opt_report, f"{stem}_optimization_report.md", output_dir
    )
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
        f"- Всего тестов: {_fmt(m.total_tests)}\n"
        f"- Дублирующих тестов (лишних копий): {_fmt(m.duplicate_tests)}\n"
        f"- Близких дублей (пар): {_fmt(m.near_duplicate_tests)}\n"
        f"- Параметризованных тестов: {_fmt(m.parametrized_tests)}\n"
        f"- Тестов с локальными фикстурами (в этом файле): {_fmt(m.tests_with_fixtures)}\n"
        f"- Тестов с docstring: {_fmt(m.tests_with_docstrings)}\n"
        f"- Жёстко заданных URL: {_fmt(m.hardcoded_urls)}\n"
        f"- Тестов без assert: {_fmt(m.missing_assertions)}\n"
        f"- Оценка читаемости (1–10): {_fmt(m.readability_score)}\n"
        f"- Покрытие требований: {_fmt(m.requirements_coverage_pct)} "
        f"(сопоставление тестов с требованиями не реализовано — нужен RTM)\n"
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