"""Гард: скрипты демо не должны разойтись между собой и не должны врать.

Четыре файла — две пары с одной логикой: стенд (`demo.sh` / `demo.ps1`, плюс вариант на
процессоре) и наполнение базы (`ingest.sh` / `ingest.ps1`). Разведённые наборы команд у
пар — не опечатка, а решение ADR-065.

Проверяем не текст, а то, что действительно ломает работу при расхождении:

- набор команд (не появилась новая команда только в одном файле);
- учётные данные (разные пароль или ключ означают: один из них не поднимет стенд);
- перечень compose-файлов;
- набор томов и имён контейнеров, к которым идут обращения;
- **поведение shell-вариантов**: подкоманда обязана дойти до `docker compose`.

Последнее - не украшение. Сравнением текста этот дефект пропускается: оба файла были
сломаны одинаково, а значит одинаковы (см. `test_sh_passes_the_subcommand_to_compose`).

Положительный контраст: «оба файла задают одинаковые учётные данные и одинаковый набор
команд», а не «в файле нет такой-то строки».
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

DEMO_DIR = Path(__file__).resolve().parents[2] / "Demo"
SH = DEMO_DIR / "demo.sh"
PS1 = DEMO_DIR / "demo.ps1"
SH_CPU = DEMO_DIR / "demo-cpu.sh"
PS1_CPU = DEMO_DIR / "demo-cpu.ps1"
SH_INGEST = DEMO_DIR / "ingest.sh"
PS1_INGEST = DEMO_DIR / "ingest.ps1"
SH_INGEST_CPU = DEMO_DIR / "ingest-cpu.sh"
PS1_INGEST_CPU = DEMO_DIR / "ingest-cpu.ps1"
INFRA = DEMO_DIR.parent / "prototype" / "infra"

# Стенд и ингест разведены (ADR-065), и наборы команд у них РАЗНЫЕ: `load` грузит
# документы, то есть дело ингеста, а `export`/`restore` возят наполненный том, то есть
# дело стенда. Смешанный набор означал бы, что разделение снова срослось.
STAND_COMMANDS = {"up", "down", "status", "export", "restore"}
INGEST_COMMANDS = {"up", "down", "status", "load"}
COMMANDS = STAND_COMMANDS | INGEST_COMMANDS

# Тома, к которым скрипты обращаются по имени: их нужно назвать, чтобы архивировать и
# восстановить. Остальные тома создаёт compose, и скриптам их знать незачем.
NAMED_VOLUME_SUFFIXES = ("neo4j", "ingestion")

# Пары файлов, которые обязаны вести себя одинаково, и ожидаемый набор compose-файлов
# у каждой. Видеокарту выдаёт compose сам, поэтому GPU-вариант подкладывает оверлей, а
# CPU-вариант обходится базовым файлом: на этом различии и держится всё разделение.
# Оверлей реранкера подключается по флагу и потому назван во всех скриптах, но
# включается только по требованию: сам факт упоминания не обязан его поднимать.
RERANKER_OVERLAY = "compose.runtime-reranker.yaml"

PAIRS = (
    pytest.param(
        SH, PS1,
        {
            "compose.runtime.yaml",
            "compose.runtime-gpu.yaml",
            RERANKER_OVERLAY,
        },
        id="gpu",
    ),
    pytest.param(
        SH_CPU, PS1_CPU,
        {"compose.runtime.yaml", RERANKER_OVERLAY},
        id="cpu",
    ),
)

GPU_OVERLAY = "compose.runtime-gpu.yaml"


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.fail(f"нет файла {path.name}: Demo без него не запускается ни на одной системе")
    return path.read_text(encoding="utf-8-sig")


def _code_lines(path: Path) -> list[str]:
    """Строки без комментариев: и `#`, и блочных `<# … #>`.

    Нужно, чтобы объяснение в комментарии не считалось использованием: файл вправе
    написать «оверлей сюда НЕ подкладывается», и гард обязан это разрешать, а не считать
    нарушением. Иначе запрет на саму формулировку мешает объяснить причину.
    """
    kept: list[str] = []
    inside_block = False
    for line in _read(path).splitlines():
        stripped = line.strip()
        if inside_block:
            if "#>" in stripped:
                inside_block = False
            continue
        if stripped.startswith("<#"):
            if "#>" not in stripped:
                inside_block = True
            continue
        if stripped.startswith("#"):
            continue
        kept.append(line)
    return kept


def _compose_files_in(path: Path) -> set[str]:
    return set(re.findall(r"compose\.[a-z-]+\.yaml", "\n".join(_code_lines(path))))


def _password(text: str) -> str:
    match = re.search(r"DEMO_PASSWORD\s*=\s*'([^']+)'", text)
    if match is None:
        pytest.fail("DEMO_PASSWORD не найден: пароль базы обязан быть задан в обоих файлах")
    return match.group(1)


def _api_key(text: str) -> str:
    match = re.search(r"DEMO_API_KEY\s*=\s*'([^']+)'", text)
    if match is None:
        pytest.fail("DEMO_API_KEY не найден: ключ API обязан быть задан в обоих файлах")
    return match.group(1)


def _commands(text: str) -> set[str]:
    """Команды, объявленные как точки входа, а не упомянутые в тексте."""
    found = set(re.findall(r"^\s*(?:cmd_)?([a-z]+)\s*\(\s*\)\s*\{", text, re.MULTILINE))
    found |= set(re.findall(r"'([a-z]+)'\s*\{", text))
    found |= set(re.findall(r"^\s*([a-z]+)\)\s*$", text, re.MULTILINE))
    return found & COMMANDS


def test_gpu_layers_variable_is_single_named() -> None:
    """Имя переменной слоёв должно быть одно на все стенды.

    Два имени для одного параметра - это расхождение, которое обнаруживается только по
    скорости: переменная задана, а на стенд не действует.
    """
    infra = DEMO_DIR.parent / "prototype" / "infra"
    compose_files = sorted(infra.glob("compose*.yaml"))
    assert compose_files, "не найдено ни одного compose-файла"

    legacy = "RUNTIME_LLM_GPU_LAYERS"
    for path in compose_files:
        text = path.read_text(encoding="utf-8-sig")
        if "--n-gpu-layers" not in text:
            continue
        assert legacy not in text, (
            f"{path.name} читает {legacy}. Единое имя - LLM_NGPU_LAYERS, как в eval; "
            "второе имя означает, что переменная молча не действует."
        )

    for path in (SH, PS1, SH_CPU, PS1_CPU):
        assert legacy not in _read(path), f"{path.name} ссылается на устаревшее {legacy}"


def test_cpu_variant_zeroes_gpu_layers() -> None:
    """Вариант на процессоре обязан сам ставить ноль слоёв.

    Без видеокарты значение 99 бессмысленно. Рассчитывать на то, что llama.cpp сам
    перейдёт на CPU, нельзя: молчаливое решение за ресурс выглядит как «тормозит».
    """
    for path in (SH_CPU, PS1_CPU):
        code = "\n".join(_code_lines(path))
        assert re.search(r"LLM_NGPU_LAYERS\s*=\s*['\"]?0", code), (
            f"{path.name} обязан задавать LLM_NGPU_LAYERS=0: без видеокарты слои некуда "
            "выгружать."
        )
    for path in (SH, PS1):
        code = "\n".join(_code_lines(path))
        assert not re.search(r"LLM_NGPU_LAYERS\s*=\s*['\"]?0", code), (
            f"{path.name} не должен обнулять слои: это вариант на видеокарте, где модель "
            "должна выгружаться целиком."
        )


def test_gpu_overlay_reserves_only_real_gpu_consumers() -> None:
    """Оверлей GPU выдаёт бронь только тем, кто на самом деле считает на GPU.

    Уже было: в оверлей попал `embeddings-service`, у которого `EMBEDDINGS_DEVICE` по
    умолчанию `cpu`. Модель считала на процессоре, а compose требовал под неё драйвер
    NVIDIA и держал видеопамять. Выглядело безобидно, но на машине без карты такая бронь
    ломает подъём целиком.

    Контракт сформулирован положительно: «оверлей даёт бронь, и каждый получивший её
    сервис по умолчанию считает на GPU». Отрицание «в оверлее не должно быть N сервисов»
    было бы замороженным списком и завтра начало бы врать.
    """
    infra = DEMO_DIR.parent / "prototype" / "infra"
    overlay = infra / GPU_OVERLAY
    runtime = infra / "compose.runtime.yaml"
    for path in (overlay, runtime):
        if not path.is_file():
            pytest.fail(f"нет файла {path.name}")

    overlay_text = overlay.read_text(encoding="utf-8-sig")
    base_text = runtime.read_text(encoding="utf-8-sig")

    granted = {
        name for name in re.findall(r"^  ([a-z0-9-]+):", overlay_text, re.MULTILINE)
        if name in ("llm", "embeddings-service")
    }
    assert granted, (
        f"{GPU_OVERLAY} не объявляет ни одного сервиса с бронью - оверлей пуст, "
        "вариант на видеокарте не отличается от варианта на процессоре"
    )

    # Разделитель `:-` отделяем от значения. Если написать `(:-?...)`, группа захватит
    # и дефис разделителя, получится "-cpu" вместо "cpu", и сравнение `!= "cpu"` будет
    # всегда истинным: гард молча ничего не проверяет.
    pattern = r"(\w*DEVICE\w*):\s*\$\{\s*\w*DEVICE\s*:-\s*([^}]+)\}"
    assert re.search(pattern, base_text), (
        "в базовом runtime файле не найдено ни одного *DEVICE* параметра - гард нечем проверять"
    )

    for service in granted:
        m = re.search(
            rf"^  {re.escape(service)}:\n(.*?)(?=^  [a-z0-9-]+:\n|\Z)",
            base_text,
            re.MULTILINE | re.DOTALL,
        )
        assert m, f"сервис {service} есть в оверлее, но его нет в базовом файле"
        for var, default in re.findall(pattern, m.group(1)):
            assert default != "cpu", (
                f"{service} получает бронь видеокарты, но `{var}` по умолчанию `cpu`. "
                "Требование без пользы: на машине без карты ломает подъём, на машине с "
                "картой занимает память впустую."
            )


def _compose(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    return data


def _mounts(service: dict[str, Any]) -> dict[str, str]:
    """`источник → точка` для длинной и короткой формы объявления томов.

    У короткой формы точка идёт с суффиксом режима (`:ro`), и его надо снять: иначе
    сравнение точек монтирования молча всегда расходится, а гард не проверяет ничего.
    """
    result: dict[str, str] = {}
    for entry in service.get("volumes") or []:
        if isinstance(entry, dict):
            result[str(entry.get("source"))] = str(entry.get("target"))
        else:
            parts = str(entry).split(":")
            result[parts[0]] = parts[1] if len(parts) > 1 else ""
    return result


def test_eval_runner_shares_projection_state_with_query_worker() -> None:
    """Runner и `query-worker` обязаны смотреть в одно состояние проекции.

    Отдельный путь состояния даёт `projection_status=missing`, и тогда readiness-gate
    выключает графическую ось: замер честно меряет vector-only, оставаясь «графовым» по
    названию режима. Отказ выглядит как успех, поэтому он закрывается гардом, а не
    аккуратностью в комментарии.

    Контракт положительный: «у обоих том проекции», а не «в оверлее нет такого-то тома» —
    замороженный список завтра начал бы врать при легитимной правке.
    """
    runtime = _compose(INFRA / "compose.runtime.yaml")
    overlay = _compose(INFRA / "compose.eval-runtime.yaml")

    worker = (runtime.get("services") or {}).get("query-worker")
    runner = (overlay.get("services") or {}).get("eval-runner")
    assert worker, "в compose.runtime.yaml нет query-worker"
    assert runner, "в compose.eval-runtime.yaml нет eval-runner"

    worker_projection = {
        target for target in _mounts(worker).values() if "projection" in target
    }
    runner_projection = {
        target for target in _mounts(runner).values() if "projection" in target
    }
    assert worker_projection, "у query-worker нет тома проекции — гард нечем проверять"
    assert runner_projection == worker_projection, (
        f"eval-runner монтирует {sorted(runner_projection)}, а query-worker — "
        f"{sorted(worker_projection)}. Графическая ось уйдёт в vector-only с "
        "`projection_status=missing`, и прогон это не заметит."
    )

    # Тот же отпечаток конфигурации: иначе `config_mismatch` выключит граф по другой
    # причине с тем же результатом.
    worker_fingerprint = (worker.get("environment") or {}).get("PROJECTION_CONFIG_FINGERPRINT")
    runner_fingerprint = (runner.get("environment") or {}).get("PROJECTION_CONFIG_FINGERPRINT")
    assert runner_fingerprint == worker_fingerprint, (
        "PROJECTION_CONFIG_FINGERPRINT у eval-runner и query-worker разный: проекция "
        "считается готовой по одному отпечатку, а проверяется по другому."
    )


def test_eval_runner_measures_working_copy_and_reports_to_host() -> None:
    """Runner меряет рабочую копию и оставляет артефакты на хосте.

    Две тихие поломки, которые выглядят как успешный прогон: запечённый в образ код вместо
    рабочего (пакет ставится editable и указывает на `/app/src`, поэтому без монтирования
    измеряется снимок образа), и артефакты, исчезающие вместе с контейнером.
    """
    runner = (_compose(INFRA / "compose.eval-runtime.yaml").get("services") or {}).get(
        "eval-runner"
    )
    assert runner, "в compose.eval-runtime.yaml нет eval-runner"

    mounts = _mounts(runner)
    assert any(target == "/app/src" for target in mounts.values()), (
        "eval-runner не монтирует рабочую копию на /app/src: пакет указывает на этот путь, "
        "и без монтирования измеряется код, запечённый в образ."
    )
    assert any(target == "/reports" for target in mounts.values()), (
        "eval-runner не отдаёт артефакты на хост: они исчезнут вместе с контейнером."
    )
    # Ревизия кода приходит с хоста, а не вычисляется в контейнере: source control
    # принадлежит хосту, и контейнер не угадывает.
    assert "RUN_CODE_COMMIT" in (runner.get("environment") or {})


def test_eval_runner_authenticates_with_the_same_key_as_the_contours() -> None:
    """Ключ прибора должен называть оба имени, которые читают стороны.

    Контур проверяет `AUTH_API_KEY`, а `run_eval.py` (`X_API_KEY` или `GRAPH_AUTH_API_KEY`)
    — разные переменные для одного значения. Объявить только одну значит отправить
    `changeme` вместо ключа стенда: ingestion-api ответит 401, и прогон измерит vector вместо
    графа, отчитавшись `revision_unknown`. Отказ выглядит как успех, поэтому он закрыт
    гардом.

    Контракт положительный: оба имени есть и оба выведены из одного `GRAPH_AUTH_API_KEY`.
    Литерал означал бы второй источник правды для ключа.
    """
    runner = (_compose(INFRA / "compose.eval-runtime.yaml").get("services") or {}).get(
        "eval-runner"
    )
    assert runner, "в compose.eval-runtime.yaml нет eval-runner"
    environment = runner.get("environment") or {}

    for name in ("AUTH_API_KEY", "X_API_KEY"):
        assert name in environment, (
            f"у eval-runner нет {name}. Контур проверяет AUTH_API_KEY, а прибор читает "
            "X_API_KEY или GRAPH_AUTH_API_KEY: без второго имени запрос уходит с ключом по "
            "умолчанию и измерение молча деградирует до vector-only."
        )
        assert "GRAPH_AUTH_API_KEY" in str(environment[name]), (
            f"{name} задан литералом, а не из GRAPH_AUTH_API_KEY: значение разойдётся со "
            "стендом при смене ключа."
        )


@pytest.mark.parametrize(("sh", "ps1", "expected_compose"), PAIRS)
def test_commands_match(sh: Path, ps1: Path, expected_compose: set[str]) -> None:
    sh_commands = _commands(_read(sh))
    ps_commands = _commands(_read(ps1))
    assert sh_commands, f"в {sh.name} не найдено ни одной команды"
    assert ps_commands, f"в {ps1.name} не найдено ни одной команды"
    assert sh_commands == ps_commands == STAND_COMMANDS, (
        f"наборы команд разошлись: {sh.name}={sorted(sh_commands)}, "
        f"{ps1.name}={sorted(ps_commands)}, ожидалось {sorted(STAND_COMMANDS)}"
    )


@pytest.mark.parametrize(("sh", "ps1", "expected_compose"), PAIRS)
def test_credentials_match(sh: Path, ps1: Path, expected_compose: set[str]) -> None:
    sh_text, ps_text = _read(sh), _read(ps1)
    assert _password(sh_text) == _password(ps_text), (
        "пароль базы в .sh и .ps1 разный. Том создан один раз, и стенд поднимется только "
        "с тем значением, которое совпадает с базой."
    )
    assert _api_key(sh_text) == _api_key(ps_text), (
        "ключ API в .sh и .ps1 разный: запросы будут уходить с ним и получать 401."
    )


@pytest.mark.parametrize(("sh", "ps1", "expected_compose"), PAIRS)
def test_compose_files_match(sh: Path, ps1: Path, expected_compose: set[str]) -> None:
    for path in (sh, ps1):
        found = _compose_files_in(path)
        assert found == expected_compose, (
            f"{path.name} ссылается на {sorted(found)}, ожидалось {sorted(expected_compose)}. "
            "Набор compose-файлов должен быть одинаковым в парных скриптах."
        )


def _sh_is_runnable(path: Path) -> bool:
    """Есть ли в системе `sh`, которым можно выполнить скрипт.

    Гард поведенческий, поэтому на машине без POSIX-оболочки он честно пропускается, а
    не делает вид, что проверил.
    """
    return shutil.which("sh") is not None


def _powershell_is_runnable() -> bool:
    """Есть ли PowerShell, которым можно разобрать скрипт."""
    return shutil.which("powershell") is not None


POWERSHELL_SCRIPTS = (PS1, PS1_CPU, PS1_INGEST)

INGEST_SCRIPTS = (SH_INGEST, SH_INGEST_CPU, PS1_INGEST)


def test_ingest_scripts_fail_on_lost_llm_layer() -> None:
    """Скрипт ингеста обязан падать, когда LLM-слой потерян, даже при `succeeded`.

    Найдено 2026-10-18: из 24 завершившихся джоб набора 8 потеряли часть или весь LLM-слой
    и ВСЕ отчитались `succeeded`. Причина в конвейере (ADR-032): негодный слой уничтожается,
    а задача продолжается. Статус джобы потерю не показывает, и общее число узлей в базе
    тоже - оно велико от других документов.

    Проверка живёт в скрипте загрузки, а не в коде сервиса: сервис уже отдаёт `enrichment`
    в ответе джобы, и читать его обязан тот, кто загружает.
    """
    for path in INGEST_SCRIPTS:
        code = _read(path)
        assert "lost_entities" in code and "lost_edges" in code, (
            f"{path.name} не читает потери LLM-слоя из ответа джобы"
        )
        assert "enrichment" in code, f"{path.name} не смотрит блок enrichment"
        assert "ПОТЕРЯ LLM-СЛОЯ" in code, (
            f"{path.name} не называет потерю внятно в выводе"
        )


def test_ingest_scripts_turn_loss_into_a_nonzero_exit() -> None:
    """Потеря обязана давать ненулевой код возврата, а не только текст в выводе.

    Проверка `== 0` молча прошла бы при ОТСУТСТВИИ поля, то есть при самом молчаливом
    варианте отказа. Поэтому сравнение «больше нуля», иначе потеря станет предупреждением,
    которым можно пренебречь.
    """
    for path in INGEST_SCRIPTS:
        code = _read(path)
        assert "if lost_entities or lost_edges:" in code or (
            "$lostEntities -or $lostEdges" in code
        ), f"{path.name} не превращает потерю в отказ"


def test_ingest_scripts_say_reload_of_the_same_hash_will_not_help() -> None:
    """Сообщение обязано предупреждать, что повторная загрузка того же файла не поможет.

    Повторная загрузка того же `content_hash` — no-op, экстракция не перезапустится
    (ADR-032). Без этой оговорки человек перезапустит загрузку, получит «уже загружен» и
    решит, что починил.
    """
    for path in INGEST_SCRIPTS:
        assert "no-op" in _read(path), (
            f"{path.name} не предупреждает, что повторная загрузка того же content_hash - no-op"
        )


def test_ingest_scripts_keep_the_total_node_check() -> None:
    """Прежняя проверка «узлы не ноль» остаётся: потеря слоя и пустой граф — разные отказы.

    Проверка потерь ловит вырожденную экстракцию, проверка узлов ловит vector-only загрузку,
    где LLM не сбой, а выключен. Убрать вторую, добавив первую, значит вернуть молчащий
    vector-only.
    """
    for path in INGEST_SCRIPTS:
        assert "ContextNode" in _read(path), (
            f"{path.name} перестал проверять, что граф вообще наполнен"
        )


@pytest.mark.parametrize("script", POWERSHELL_SCRIPTS)
def test_powershell_scripts_keep_their_bom(script: Path) -> None:
    """PowerShell 5.1 читает `.ps1` БЕЗ BOM как ANSI, и кириллица в комментариях ломает парсер.

    Найдено 2026-10-18: `demo.ps1` и `demo-cpu.ps1` работали годами, потом потеряли BOM при
    правке, и перестали запускаться вообще — с ошибкой про неожиданный токен, а не про
    кодировку. Заметить это без запуска нельзя: все проверки этого файла сравнивают текст,
    а текст был в порядке.
    """
    raw = script.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf", (
        f"{script.name} без BOM. Windows PowerShell 5.1 прочитает его в ANSI-кодировке "
        "хоста, кириллица в комментариях превратится в мусор, и парсер упадёт на "
        "неожиданном токене. BOM обязателен."
    )


@pytest.mark.skipif(not _powershell_is_runnable(), reason="нет PowerShell")
@pytest.mark.parametrize("script", POWERSHELL_SCRIPTS)
def test_powershell_scripts_parse(script: Path) -> None:
    """Скрипт обязан разбираться. Разбор не исполняет скрипт и ничего не меняет.

    Более дешёвая проверка, чем запуск: она ловит синтаксис, включая порчу текста
    строковыми заменами, и не поднимает ни одного контейнера.
    """
    check = (
        "$e=$null;[void][System.Management.Automation.Language.Parser]"
        ".ParseFile($args[0],[ref]$null,[ref]$e);"
        "if($e -and $e.Count){$e[0].Message}"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", check, str(script)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert not completed.stdout.strip(), (
        f"{script.name} не разбирается: {completed.stdout.strip()}"
    )


def _compose_arguments_seen_by_stub(script: Path, arguments: list[str]) -> list[str]:
    """Запустить скрипт с подменой `docker` и вернуть аргументы, дошедшие до compose.

    Подмена нужна, чтобы не трогать стенд: скрипт печатает всё, что передал в
    `docker compose`, и ничего не выполняет.
    """
    with tempfile.TemporaryDirectory() as tmp:
        stub_dir = pathlib.Path(tmp) / "stub"
        stub_dir.mkdir()
        stub = stub_dir / "docker"
        # Метка включает слово `compose`: скрипт сначала зовёт `docker info` для проверки
        # демона, и без фильтра первым «увиденным» оказался бы именно он.
        stub.write_text('#!/bin/sh\necho "SEEN-COMPOSE: ${*}"\n', encoding="utf-8")
        stub.chmod(0o755)
        env = {**os.environ, "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}
        completed = subprocess.run(
            ["sh", str(script), *arguments],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            check=False,
        )
    seen = [
        line for line in completed.stdout.splitlines() if line.startswith("SEEN-COMPOSE: compose")
    ]
    if not seen:
        pytest.fail(
            f"{script.name} не вызвал docker compose при {arguments}. "
            f"Вывод: {completed.stdout!r} / {completed.stderr!r}"
        )
    return seen[0][len("SEEN-COMPOSE:"):].split()


@pytest.mark.skipif(not _sh_is_runnable(SH), reason="нет POSIX-оболочки")
@pytest.mark.parametrize("script", (SH, SH_CPU, SH_INGEST, SH_INGEST_CPU))
def test_sh_passes_the_up_subcommand_to_compose(script: Path) -> None:
    """Подкоманда `up` с её флагами обязана дойти до compose.

    Найдено 2026-10-18: внутри функции `set --` заменяет позиционные параметры, поэтому
    `compose_cmd up -d --wait` терял `up` целиком. compose получал только список файлов,
    выдавал usage, а скрипт печатал «поднят» — то есть неудача выглядела как успех.

    Проверка СРАВНЕНИЕМ ТЕКСТА этот дефект пропускала и пропустит снова: оба варианта
    были одинаково сломаны, а значит одинаковы. Поэтому проверка поведенческая.

    Позиции названы явно, а не «последние токены»: за флагами стоит значение таймаута,
    и сравнение хвоста сдвигалось бы на него.
    """
    seen = _compose_arguments_seen_by_stub(script, ["up", "--set", "empty"])
    assert seen[-5] == "up", (
        f"{script.name} up: compose получил {seen}, а флагом перед таймаутом должен идти `up`. "
        "Подкоманда потеряна — скрипт напечатает успех при неработающем compose."
    )
    assert seen[-4:-1] == ["-d", "--wait", "--wait-timeout"], (
        f"{script.name} up: после `up` ожидались флаги запуска, а получено {seen[-4:-1]}"
    )


@pytest.mark.skipif(not _sh_is_runnable(SH), reason="нет POSIX-оболочки")
@pytest.mark.parametrize("script", (SH, SH_CPU, SH_INGEST, SH_INGEST_CPU))
def test_sh_passes_the_down_subcommand_to_compose(script: Path) -> None:
    """Подкоманда `down` обязана быть последним аргументом compose."""
    seen = _compose_arguments_seen_by_stub(script, ["down", "--set", "empty"])
    assert seen[-1] == "down", (
        f"{script.name} down: compose получил {seen}, последним должен идти `down`"
    )


def test_ingest_cpu_variant_is_unix_only_and_says_where_windows_is_described() -> None:
    """Варианта ингеста на процессоре для Windows нет намеренно, и это записано.

    Решение владельца 2026-10-18: скрипт только для Unix и macOS, а для Windows в системные
    требования. Гард сторожит именно границу: появившийся `ingest-cpu.ps1` сделал бы
    требования неправдой, и человек пошёл бы искать описание того, что уже есть в коде.
    """
    if not SH_INGEST_CPU.is_file():
        pytest.fail(f"нет файла {SH_INGEST_CPU.name}: вариант ингеста на процессоре обязателен")
    text = _read(SH_INGEST_CPU)
    assert "Linux" in text and "macOS" in text, (
        f"{SH_INGEST_CPU.name} не называет, где он работает"
    )
    assert "prototype_requirements" in text, (
        f"{SH_INGEST_CPU.name} не ссылается на документ с требованиями для Windows"
    )
    assert not PS1_INGEST_CPU.exists(), (
        f"{PS1_INGEST_CPU.name} существует, но Windows-вариант описан в требованиях. "
        "Либо удалите скрипт, либо перепишите требования - держать оба нельзя."
    )


def test_ingest_cpu_variant_does_not_grant_a_gpu() -> None:
    """Вариант на процессоре не должен подкладывать оверлей видеокарты.

    Иначе на машине без NVIDIA он не поднимется, а ошибка будет выглядеть как проблема
    драйвера, а не как неверный скрипт.
    """
    assert GPU_OVERLAY not in _compose_files_in(SH_INGEST_CPU), (
        f"{SH_INGEST_CPU.name} подкладывает {GPU_OVERLAY}: вариант на процессоре обходится "
        "базовым файлом."
    )
    assert "compose.ingest.yaml" in _compose_files_in(SH_INGEST_CPU), (
        f"{SH_INGEST_CPU.name} обязан подкладывать оверлей ингеста"
    )


def test_ingest_variants_do_not_reach_the_same_result_two_ways() -> None:
    """Флаг переключения вида счита запрещён: два способа дают два разных скрипта.

    `--cpu` в `ingest.sh` и отдельный `ingest-cpu.sh` описывали бы одно и то же, и со
    временем разошлись бы. Различие обязано быть именем файла.
    """
    for path in (SH_INGEST, SH_CPU, SH):
        code = "\n".join(_code_lines(path))
        assert "--cpu" not in code and "-Cpu" not in code, (
            f"{path.name} переключает вид вычислений флагом. Отдельный скрипт понятнее: "
            "две точки выбора означают два места, которые надо держать одинаковыми."
        )


@pytest.mark.skipif(not _sh_is_runnable(SH), reason="нет POSIX-оболочки")
def test_ingest_overlay_is_absent_from_stand_and_present_in_ingest() -> None:
    """Оверлей ингеста есть в одном скрипте и отсутствует в другом (ADR-065).

    Проверяется тем же прогоном с подменой `docker`, что и выше, потому что оверлей —
    это тоже поведение, а не текст.
    """
    stand = _compose_arguments_seen_by_stub(SH, ["up", "--set", "empty"])
    assert not any("compose.ingest.yaml" in token for token in stand), (
        f"стенд подкладывает оверлей ингеста: {stand}"
    )
    ingest = _compose_arguments_seen_by_stub(SH_INGEST, ["up", "--set", "empty"])
    assert any("compose.ingest.yaml" in token for token in ingest), (
        f"скрипт ингеста не подкладывает оверлей: {ingest}"
    )
    assert "ingest" in ingest, f"скрипт ингеста не включил профиль ingest: {ingest}"
    """Различие между вариантами обязано быть ровно одно: оверлей GPU.

    Если оверлей появится у CPU-варианта, тот начнёт требовать драйвер NVIDIA и не
    поднимется там, где собирались считать на процессоре. Если исчезнет у GPU-варианта,
    стенд потихоньку уйдёт на процессор - и отвечать будет в разы дольше, что выглядит
    как «тормозит», а не как «не включилась видеокарта».
    """
    for path in (SH_CPU, PS1_CPU):
        assert GPU_OVERLAY not in _compose_files_in(path), (
            f"{path.name} не должен подкладывать {GPU_OVERLAY}: вариант на процессоре "
            "обходится базовым файлом."
        )
    for path in (SH, PS1):
        assert GPU_OVERLAY in _compose_files_in(path), (
            f"{path.name} обязан подкладывать {GPU_OVERLAY}, иначе LLM считает "
            "на процессоре вместо видеокарты."
        )


def test_gpu_is_granted_only_by_overlay() -> None:
    """Бронь устройства обязана жить только в оверлее.

    Проверено, что перекрыть её сверху нельзя: ни `devices: []`, ни `devices: null` её не
    снимают, потому что в базовом файле она пришла через YAML-merge. Значит бронь можно
    убрать только в базовом файле, и гард обязан следить именно за этим.
    """
    runtime = DEMO_DIR.parent / "prototype" / "infra" / "compose.runtime.yaml"
    overlay = DEMO_DIR.parent / "prototype" / "infra" / GPU_OVERLAY
    for path in (runtime, overlay):
        if not path.is_file():
            pytest.fail(f"нет файла {path.name}")

    base_text = runtime.read_text(encoding="utf-8-sig")
    # Якорь сам по себе безвреден, пока на него не ссылается сервис. Проверяем именно
    # ссылки: `x-gpu:` может остаться как объявление, но `<<: [*gpu` означает бронь.
    assert "<<: [*gpu" not in base_text, (
        "в compose.runtime.yaml сервис всё ещё забирает бронь видеокарты через YAML-merge. "
        "Снять её сверху невозможно, поэтому вариант на процессоре не соберётся."
    )
    assert "driver: nvidia" in overlay.read_text(encoding="utf-8-sig"), (
        f"в {GPU_OVERLAY} нет брони устройства - значит видеокарта никогда не выдастся"
    )


@pytest.mark.parametrize(("sh", "ps1", "expected_compose"), PAIRS)
def test_containers_and_volumes_match(sh: Path, ps1: Path, expected_compose: set[str]) -> None:
    """Контракт - равенство двух файлов, а не замороженный перечень имён.

    Перечислять «правильные» имена контейнеров здесь нельзя: такой список завтра устареет и
    начнёт врать, а расхождение между файлами - это то, что ломает работу по-настоящему.
    """
    sh_containers = set(re.findall(r"ohw-runtime-[a-z0-9-]+", _read(sh)))
    ps_containers = set(re.findall(r"ohw-runtime-[a-z0-9-]+", _read(ps1)))
    assert sh_containers == ps_containers, (
        f"скрипты обращаются к разным контейнерам: {sh.name}={sorted(sh_containers)}, "
        f"{ps1.name}={sorted(ps_containers)}. Один из них обратится не туда."
    )
# Контейнер, о котором стенд обязан знать по имени. ingestion-api в него не входит
    # (ADR-058), и упоминать его в стенде после разделения (ADR-065) нечего.
    assert "ohw-runtime-neo4j" in sh_containers, "ни один из скриптов не обращается к neo4j"
    assert "ohw-runtime-ingestion" not in sh_containers, (
        f"{sh.name} знает контейнер ингеста. Ингест вынесен в отдельный скрипт (ADR-065), "
        "и упоминание здесь означало бы, что разделение снова срослось."
    )

    for suffix in NAMED_VOLUME_SUFFIXES:
        for path in (sh, ps1):
            assert suffix in _read(path), f"{path.name} не упоминает том с суффиксом {suffix}"


INGEST_PAIR = pytest.param(
    SH_INGEST, PS1_INGEST,
    {
        "compose.runtime.yaml",
        "compose.ingest.yaml",
        "compose.runtime-gpu.yaml",
    },
    id="ingest",
)


@pytest.mark.parametrize(("sh", "ps1", "expected_compose"), (INGEST_PAIR,))
def test_ingest_scripts_expose_only_ingest_commands(
    sh: Path, ps1: Path, expected_compose: set[str]
) -> None:
    """Скрипт ингеста не умеет ни выгружать, ни восстанавливать том.

    Набор проверяется поштучно, а не сравнением с набором соседних скриптов: иначе
    можно добавить команду в оба варианта и сохранить равенство.
    """
    for path in (sh, ps1):
        commands = _commands(_read(path))
        assert commands == INGEST_COMMANDS, (
            f"{path.name}: команды {sorted(commands)}, ожидалось {sorted(INGEST_COMMANDS)}"
        )


@pytest.mark.parametrize(("sh", "ps1", "expected_compose"), (INGEST_PAIR,))
def test_ingest_scripts_always_carry_the_ingest_overlay(
    sh: Path, ps1: Path, expected_compose: set[str]
) -> None:
    """Оверлей ингеста подключается ВСЕГДА, а не по флагу.

    Без него `ingestion-api` не существует и грузить нечем, то есть скрипт молча
    ничего бы не сделал.
    """
    for path in (sh, ps1):
        assert "compose.ingest.yaml" in _compose_files_in(path), (
            f"{path.name} не подкладывает compose.ingest.yaml"
        )
        assert "--profile" in _read(path), (
            f"{path.name} подкладывает оверлей, но не включает профиль `ingest`: "
            "сервис останется выключенным."
        )


@pytest.mark.parametrize(("sh", "ps1", "expected_compose"), (INGEST_PAIR,))
def test_ingest_scripts_name_the_container_they_upload_into(
    sh: Path, ps1: Path, expected_compose: set[str]
) -> None:
    """Контейнер, в который идёт загрузка, назван в обоих вариантах.

    Загрузка идёт через `docker cp` в контейнер ингеста, и имя контейнера обязано быть
    в коде обоих вариантов: иначе один из них загрузит «куда-то».
    """
    for path in (sh, ps1):
        assert "ohw-runtime-ingestion" in _read(path), (
            f"{path.name} не знает контейнер ингеста, а грузить ему нужно именно туда"
        )


@pytest.mark.parametrize(("sh", "ps1", "expected_compose"), PAIRS)
def test_sh_has_no_bash_only_constructs(
    sh: Path, ps1: Path, expected_compose: set[str]
) -> None:
    """`demo.sh` объявлен как POSIX sh, поэтому конструкций bash в нём быть не должно."""
    text = _read(sh)
    for marker, why in (
        (r"\[\[", "двойные скобки [[ ]] — это bash, а не POSIX sh"),
        (r"\bfunction\s+\w+\s*\(\)", "объявление function — это bash"),
        (r"\bset\s+-[a-z]*o\s", "опция set -o — это bash"),
        (r"\$\{[A-Za-z_][A-Za-z0-9_]*\[[^}]*\]\}", "косвенное расширение ${a[b]} — это bash"),
        (r"<<-", "heredoc с табами — это bash"),
    ):
        assert not re.search(marker, text), f"{why}: {marker} в {sh.name}"

def test_reranker_overlay_is_reachable_from_every_demo_script() -> None:
    """Реранкерный запуск обязан существовать во всех скриптах Демо.

    Отдельный запуск с реранкером — это оверлей плюс флаг. Если флаг есть хотя бы в
    одном скрипте, стенд ведёт себя по-разному в зависимости от того, на какой системе
    его запустили, и это обнаружится замерами, а не чтением.
    """
    infra = DEMO_DIR.parent / "prototype" / "infra"
    overlay = infra / RERANKER_OVERLAY
    assert overlay.is_file(), f"нет оверлея {RERANKER_OVERLAY}: запуск с реранкером недостижим"
    for path in (SH, PS1, SH_CPU, PS1_CPU):
        assert RERANKER_OVERLAY in _read(path), (
            f"{path.name} не умеет подключить {RERANKER_OVERLAY}: запуск с реранкером "
            "существует только на части систем"
        )


def test_reranker_overlay_enables_slot_on_both_pipeline_services() -> None:
    """Оверлей включает слот реранкера у обоих сервисов, способных исполнить пайплайн.

    `query-api` сегодня только принимает задание и отдаёт его воркеру, поэтому отсутствие
    слота у него безобидно. Но обход очереди — это синхронный путь, и тогда api исполнил бы
    пайплайн с дефолтом профиля, то есть ось переранжирования оказалась бы выключенной при
    включённой настройке. Проверяется положительный инвариант: оба сервиса объявляют слот.
    """
    import yaml

    infra = DEMO_DIR.parent / "prototype" / "infra"
    document = yaml.safe_load((infra / RERANKER_OVERLAY).read_text(encoding="utf-8"))
    services = document.get("services") or {}
    for service in ("query-api", "query-worker"):
        environment = (services.get(service) or {}).get("environment") or {}
        assert environment.get("RERANKER") == "bge_reranker", (
            f"{service} в оверлее реранкера не переводит слот в bge_reranker: "
            "запуск поднимет сервис, но выдача продолжит идти без переранжирования"
        )
        assert environment.get("RERANKER_URL"), f"{service} не знает адрес сервиса реранкера"
    assert "reranker" in services, "оверлей объявляет слот, но не поднимает сам сервис"


def test_reranker_stays_off_in_plain_demo_run() -> None:
    """Штатный запуск Демо переранживание не включает.

    Оверлей подключается флагом, а базовый runtime-контур слот не трогает. Если в базовом
    файле появится `bge_reranker`, демо начнёт обращаться к сервису, которого в этом
    запуске нет, - то есть поведение станет зависеть от того, переопределил ли кто-то
    переменную окружения.
    """
    infra = DEMO_DIR.parent / "prototype" / "infra"
    base = (infra / "compose.runtime.yaml").read_text(encoding="utf-8")
    assert "bge_reranker" not in base, (
        "базовый runtime-контур включает bge_reranker: штатный запуск Демо начнёт "
        "обращаться к сервису реранкера, которого в нём нет"
    )
