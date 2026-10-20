"""Гард: скрипты демо не должны разойтись между собой и не должны врать.

Шесть файлов, и это не «две пары с одной логикой», а матрица вариантов (ADR-065): на
Windows два вида счёта для каждого режима, на Unix и macOS - один, на процессоре.

    режим                    Windows (видеокарта)   Windows (процессор)   Linux/macOS
    стенд                    demo.ps1               demo-cpu.ps1          demo.sh
    наполнение базы          ingest.ps1             ingest-cpu.ps1        ingest.sh

Парность поэтому считается не «`.sh` против `.ps1`» (это разные варианты!), а по
инвариантам, которые обязаны держаться на каждом файле матрицы: набор команд по режиму,
один и тот же пароль и ключ во всех шести, объявленный набор compose-файлов, вид счёта
(ноль слоёв GPU у варианта на процессоре, оверлей видеокарты - только у варианта на GPU).

Проверяем не текст, а то, что действительно ломает работу при расхождении:

- набор команд (не появилась новая команда только в одном файле);
- учётные данные (разные пароль или ключ означают: один из них не поднимет стенд);
- перечень compose-файлов;
- набор томов и имён контейнеров, к которым идут обращения;
- подсказка стенда называет ингест ТОГО ЖЕ вида счёта;
- **поведение shell-вариантов**: подкоманда обязана дойти до `docker compose`.

Последнее - не украшение. Сравнением текста этот дефект пропускается: оба файла были
сломаны одинаково, а значит одинаковы (см. `test_sh_passes_the_up_subcommand_to_compose`).

Положительный контраст: «каждый файл матрицы задаёт одинаковые учётные данные и набор
команд, положенный его режиму», а не «в файле нет такой-то строки».
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

DEMO_DIR = Path(__file__).resolve().parents[2] / "Demo"
SH = DEMO_DIR / "demo.sh"
PS1 = DEMO_DIR / "demo.ps1"
PS1_CPU = DEMO_DIR / "demo-cpu.ps1"
SH_INGEST = DEMO_DIR / "ingest.sh"
PS1_INGEST = DEMO_DIR / "ingest.ps1"
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

RERANKER_OVERLAY = "compose.runtime-reranker.yaml"
GPU_OVERLAY = "compose.runtime-gpu.yaml"
INGEST_OVERLAY = "compose.ingest.yaml"
BASE_OVERLAY = "compose.runtime.yaml"


@dataclass(frozen=True)
class Script:
    """Файл матрицы: что он делает (`kind`) и на чём считает (`device`).

    Ожидаемый набор compose-файлов выведен из этих двух полей, а не вписан рядом
    списком: разойтись они не могут, потому что второе определено через первое.
    """

    path: Path
    kind: str  # "stand" | "ingest"
    device: str  # "cpu" | "gpu"

    @property
    def compose(self) -> frozenset[str]:
        files = {BASE_OVERLAY}
        if self.kind == "ingest":
            # Оверлей ингеста подключается ВСЕГДА: без него `ingestion-api` не существует.
            files.add(INGEST_OVERLAY)
        else:
            # Оверлей реранкера назван во всех скриптах стенда, но включается по флагу:
            # сам факт упоминания не обязан его поднимать.
            files.add(RERANKER_OVERLAY)
        if self.device == "gpu":
            files.add(GPU_OVERLAY)
        return frozenset(files)


# Матрица - источник для всех проверок ниже. Unix и macOS дают ровно один вариант на
# режим, и он на процессоре: файлов `demo-gpu.sh` / `ingest-gpu.sh` нет намеренно.
STAND_MATRIX = (
    Script(SH, kind="stand", device="cpu"),
    Script(PS1, kind="stand", device="gpu"),
    Script(PS1_CPU, kind="stand", device="cpu"),
)
INGEST_MATRIX = (
    Script(SH_INGEST, kind="ingest", device="cpu"),
    Script(PS1_INGEST, kind="ingest", device="gpu"),
    Script(PS1_INGEST_CPU, kind="ingest", device="cpu"),
)
MATRIX = STAND_MATRIX + INGEST_MATRIX

MATRIX_PARAMS = tuple(pytest.param(s, id=s.path.name) for s in MATRIX)
STAND_PARAMS = tuple(pytest.param(s, id=s.path.name) for s in STAND_MATRIX)
INGEST_PARAMS = tuple(pytest.param(s, id=s.path.name) for s in INGEST_MATRIX)

SH_SCRIPTS = (SH, SH_INGEST)
POWERSHELL_SCRIPTS = (PS1, PS1_CPU, PS1_INGEST, PS1_INGEST_CPU)
INGEST_PATHS = (SH_INGEST, PS1_INGEST, PS1_INGEST_CPU)

# Подсказка стенда обязана звать ингест того же вида счёта: `demo-cpu.ps1` когда-то отсылал
# к GPU-ингесту, то есть человек на машине без карты шёл запускать неподходящий скрипт.
# Второй элемент - файл того же вида, но другого счёта: его в подсказке быть не должно.
STAND_INGEST_COMPANION: dict[Path, tuple[str, str | None]] = {
    SH: ("ingest.sh", None),
    PS1: ("ingest.ps1", "ingest-cpu.ps1"),
    PS1_CPU: ("ingest-cpu.ps1", "ingest.ps1"),
}


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.fail(f"нет файла {path.name}: Demo без него не запускается на этой системе")
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
        pytest.fail("DEMO_PASSWORD не найден: пароль базы обязан быть задан в скрипте")
    return match.group(1)


def _api_key(text: str) -> str:
    match = re.search(r"DEMO_API_KEY\s*=\s*'([^']+)'", text)
    if match is None:
        pytest.fail("DEMO_API_KEY не найден: ключ API обязан быть задан в скрипте")
    return match.group(1)


def _commands(text: str) -> set[str]:
    """Команды, объявленные как точки входа, а не упомянутые в тексте."""
    found = set(re.findall(r"^\s*(?:cmd_)?([a-z]+)\s*\(\s*\)\s*\{", text, re.MULTILINE))
    found |= set(re.findall(r"'([a-z]+)'\s*\{", text))
    found |= set(re.findall(r"^\s*([a-z]+)\)\s*$", text, re.MULTILINE))
    return found & COMMANDS


def test_matrix_matches_the_declared_files() -> None:
    """Набор скриптов на диске равен объявленной матрице - целиком, без лишних файлов.

    Контракт положительный: «на диске ровно то, что описано». Появление `demo-gpu.sh`
    (варианта, которого на Unix не должно быть) или пропажа `ingest-cpu.ps1` ломает не
    текст, а матрицу, и тогда инструкция в документации начинает звать несуществующее
    или умалчивать о существующем. Поэтому отказ называет и лишние, и недостающие файлы.
    """
    declared = {script.path.name for script in MATRIX}
    on_disk = {
        path.name
        for path in DEMO_DIR.iterdir()
        if path.is_file() and path.suffix in (".sh", ".ps1")
    }
    extra = sorted(on_disk - declared)
    missing = sorted(declared - on_disk)
    assert on_disk == declared, (
        f"набор скриптов разошёлся с матрицей (ADR-065). Лишние: {extra}. "
        f"Недостающие: {missing}. Матрица: Windows - demo.ps1/ingest.ps1 (видеокарта) и "
        "demo-cpu.ps1/ingest-cpu.ps1 (процессор); Unix и macOS - demo.sh/ingest.sh "
        "(процессор)."
    )


def test_gpu_layers_variable_is_single_named() -> None:
    """Имя переменной слоёв должно быть одно на все стенды.

    Два имени для одного параметра - это расхождение, которое обнаруживается только по
    скорости: переменная задана, а на стенд не действует.
    """
    compose_files = sorted(INFRA.glob("compose*.yaml"))
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

    for script in MATRIX:
        assert legacy not in _read(script.path), (
            f"{script.path.name} ссылается на устаревшее {legacy}"
        )


@pytest.mark.parametrize("script", MATRIX_PARAMS)
def test_device_variant_sets_gpu_layers_explicitly(script: Script) -> None:
    """Вид счёта задан явно: вариант на процессоре ставит ноль слоёв, вариант на GPU - нет.

    Без видеокарты значение 99 бессмысленно, но рассчитывать на то, что llama.cpp сам
    перейдёт на CPU, нельзя: молчаливое решение за ресурс выглядит как «тормозит».
    Обратная ошибка не лучше: обнуление слоёв у варианта на видеокарте тихо уводит стенд
    на процессор, и это видно только по времени ответа.
    """
    code = "\n".join(_code_lines(script.path))
    zeroes = re.search(r"LLM_NGPU_LAYERS\s*=\s*['\"]?0", code)
    if script.device == "cpu":
        assert zeroes, (
            f"{script.path.name} - вариант на процессоре и обязан задавать "
            "LLM_NGPU_LAYERS=0: без видеокарты слои некуда выгружать."
        )
    else:
        assert not zeroes, (
            f"{script.path.name} - вариант на видеокарте и не должен обнулять слои: "
            "иначе LLM молча считает на процессоре."
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
    overlay = INFRA / GPU_OVERLAY
    runtime = INFRA / BASE_OVERLAY
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


def test_credentials_are_the_same_in_every_script() -> None:
    """Пароль базы и ключ API обязаны совпадать во всех шести файлах.

    Том набора создан один раз, а ингест и стенд работают с ним по очереди: разные
    значения означают, что один из скриптов не поднимет базу или получит 401. Проверка
    идёт по множеству значений - «у всех одно», а не «у пары совпало»: пара исчезла
    вместе с прежней матрицей (ADR-065).
    """
    passwords = {_password(_read(script.path)) for script in MATRIX}
    assert len(passwords) == 1, (
        f"пароль базы разный в файлах матрицы: {sorted(passwords)}. Том один, и стенд "
        "поднимется только с тем значением, которое совпадает с базой."
    )
    keys = {_api_key(_read(script.path)) for script in MATRIX}
    assert len(keys) == 1, (
        f"ключ API разный в файлах матрицы: {sorted(keys)}: запросы будут уходить с ним и "
        "получать 401."
    )


@pytest.mark.parametrize("script", MATRIX_PARAMS)
def test_compose_files_match_the_declared_set(script: Script) -> None:
    """Каждый файл называет ровно те compose-файлы, что положены его режиму и виду счёта.

    Набор выведен из `Script.compose`, то есть из тех же двух полей, что задают матрицу.
    Лишний оверлей означает лишний шаг подъёма; пропавший - молча выключенное устройство
    (оверлей GPU) или несуществующий сервис (оверлей ингеста).
    """
    found = _compose_files_in(script.path)
    assert found == set(script.compose), (
        f"{script.path.name} ссылается на {sorted(found)}, ожидалось "
        f"{sorted(script.compose)}. Файл относится к режиму '{script.kind}' и варианту "
        f"'{script.device}'."
    )


@pytest.mark.parametrize("script", STAND_PARAMS)
def test_stand_scripts_expose_only_stand_commands(script: Script) -> None:
    """Стенд умеет возить том, но не умеет грузить документы.

    Набор проверяется поштучно, а не сравнением с соседним файлом: иначе можно добавить
    команду во все файлы сразу и сохранить равенство между ними.
    """
    commands = _commands(_read(script.path))
    assert commands == STAND_COMMANDS, (
        f"{script.path.name}: команды {sorted(commands)}, ожидалось {sorted(STAND_COMMANDS)}"
    )


@pytest.mark.parametrize("script", INGEST_PARAMS)
def test_ingest_scripts_expose_only_ingest_commands(script: Script) -> None:
    """Скрипт ингеста не умеет ни выгружать, ни восстанавливать том.

    Набор проверяется поштучно, а не сравнением с набором соседних скриптов: иначе
    можно добавить команду в оба варианта и сохранить равенство.
    """
    commands = _commands(_read(script.path))
    assert commands == INGEST_COMMANDS, (
        f"{script.path.name}: команды {sorted(commands)}, ожидалось {sorted(INGEST_COMMANDS)}"
    )


@pytest.mark.parametrize("script", INGEST_PARAMS)
def test_ingest_scripts_always_carry_the_ingest_overlay(script: Script) -> None:
    """Оверлей ингеста подключается ВСЕГДА, а не по флагу.

    Без него `ingestion-api` не существует и грузить нечем, то есть скрипт молча
    ничего бы не сделал.
    """
    assert INGEST_OVERLAY in _compose_files_in(script.path), (
        f"{script.path.name} не подкладывает {INGEST_OVERLAY}"
    )
    assert "--profile" in _read(script.path), (
        f"{script.path.name} подкладывает оверлей, но не включает профиль `ingest`: "
        "сервис останется выключенным."
    )


@pytest.mark.parametrize("script", INGEST_PARAMS)
def test_ingest_scripts_name_the_container_they_upload_into(script: Script) -> None:
    """Контейнер, в который идёт загрузка, назван в каждом варианте ингеста.

    Загрузка идёт через `docker cp` в контейнер ингеста, и имя контейнера обязано быть
    в коде каждого варианта: иначе один из них загрузит «куда-то».
    """
    assert "ohw-runtime-ingestion" in _read(script.path), (
        f"{script.path.name} не знает контейнер ингеста, а грузить ему нужно именно туда"
    )


@pytest.mark.parametrize("script", MATRIX_PARAMS)
def test_containers_and_volumes_match(script: Script) -> None:
    """Контракт - правило по режиму, а не замороженный перечень имён.

    Перечислять «правильные» имена контейнеров здесь нельзя: такой список завтра устареет и
    начнёт врать. Правило: стенд знает Neo4j и не знает ингеста (ADR-058/065), ингест
    знает оба.
    """
    text = _read(script.path)
    containers = set(re.findall(r"ohw-runtime-[a-z0-9-]+", text))
    assert "ohw-runtime-neo4j" in containers, (
        f"{script.path.name} не обращается к neo4j по имени"
    )
    if script.kind == "stand":
        assert "ohw-runtime-ingestion" not in containers, (
            f"{script.path.name} знает контейнер ингеста. Ингест вынесен в отдельный скрипт "
            "(ADR-065), и упоминание здесь означало бы, что разделение снова срослось."
        )
    else:
        assert "ohw-runtime-ingestion" in containers, (
            f"{script.path.name} не знает контейнер ингеста, а грузить ему нужно туда"
        )

    for suffix in NAMED_VOLUME_SUFFIXES:
        assert suffix in text, f"{script.path.name} не упоминает том с суффиксом {suffix}"


@pytest.mark.parametrize("script", STAND_PARAMS)
def test_stand_hint_names_the_ingest_of_the_same_device(script: Script) -> None:
    """Подсказка стенда обязана звать ингест ТОГО ЖЕ вида счёта.

    Было: `demo-cpu.ps1` отсылал к `Demo/ingest.ps1`, то есть человек на машине без
    видеокарты шёл запускать скрипт, который требует GPU. Стенд и ингест должны
    называться одним вариантом, иначе матрица распадается на «что написали в справке» и
    «что существует на самом деле».
    """
    expected, wrong = STAND_INGEST_COMPANION[script.path]
    text = _read(script.path)
    assert expected in text, (
        f"{script.path.name} не называет ингест того же вида счёта ({expected}): стенд "
        f"варианта '{script.device}' обязан отсылать к ингесту варианта '{script.device}'."
    )
    if wrong is not None:
        assert wrong not in text, (
            f"{script.path.name} называет {wrong} - ингест ДРУГОГО вида счёта. Стенд "
            f"варианта '{script.device}' и наполнение обязаны совпадать по виду счёта."
        )


def test_no_script_switches_the_variant_by_flag() -> None:
    """Флаг переключения вида счёта запрещён: два способа дают два разных скрипта.

    `-Cpu` в `ingest.ps1` и отдельный `ingest-cpu.ps1` описывали бы одно и то же, и со
    временем разошлись бы. Различие обязано быть именем файла - и это правило должно
    держаться на всех шести файлах, а не только на shell-вариантах: прежний гард смотрел
    только `.sh`, поэтому `-Cpu` в `ingest.ps1` прожил незамеченным.
    """
    for script in MATRIX:
        code = "\n".join(_code_lines(script.path))
        assert "--cpu" not in code and "-Cpu" not in code, (
            f"{script.path.name} переключает вид счёта флагом. Отдельный файл понятнее: "
            "две точки выбора означают два места, которые надо держать одинаковыми."
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


def test_ingest_scripts_fail_on_lost_llm_layer() -> None:
    """Скрипт ингеста обязан падать, когда LLM-слой потерян, даже при `succeeded`.

    Найдено 2026-10-18: из 24 завершившихся джоб набора 8 потеряли часть или весь LLM-слой
    и ВСЕ отчитались `succeeded`. Причина в конвейере (ADR-032): негодный слой уничтожается,
    а задача продолжается. Статус джобы потерю не показывает, и общее число узлей в базе
    тоже - оно велико от других документов.

    Проверка живёт в скрипте загрузки, а не в коде сервиса: сервис уже отдаёт `enrichment`
    в ответе джобы, и читать его обязан тот, кто загружает.
    """
    for path in INGEST_PATHS:
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
    for path in INGEST_PATHS:
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
    for path in INGEST_PATHS:
        assert "no-op" in _read(path), (
            f"{path.name} не предупреждает, что повторная загрузка того же content_hash - no-op"
        )


def test_ingest_scripts_keep_the_total_node_check() -> None:
    """Прежняя проверка «узлы не ноль» остаётся: потеря слоя и пустой граф — разные отказы.

    Проверка потерь ловит вырожденную экстракцию, проверка узлов ловит vector-only загрузку,
    где LLM не сбой, а выключен. Убрать вторую, добавив первую, значит вернуть молчащий
    vector-only.
    """
    for path in INGEST_PATHS:
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
@pytest.mark.parametrize("script", SH_SCRIPTS)
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
@pytest.mark.parametrize("script", SH_SCRIPTS)
def test_sh_passes_the_down_subcommand_to_compose(script: Path) -> None:
    """Подкоманда `down` обязана быть последним аргументом compose."""
    seen = _compose_arguments_seen_by_stub(script, ["down", "--set", "empty"])
    assert seen[-1] == "down", (
        f"{script.name} down: compose получил {seen}, последним должен идти `down`"
    )


@pytest.mark.skipif(not _sh_is_runnable(SH), reason="нет POSIX-оболочки")
def test_ingest_overlay_is_absent_from_stand_and_present_in_ingest() -> None:
    """Оверлей ингеста есть в скрипте ингеста и отсутствует в стенде (ADR-065).

    Проверяется тем же прогоном с подменой `docker`, что и выше, потому что оверлей —
    это тоже поведение, а не текст.
    """
    stand = _compose_arguments_seen_by_stub(SH, ["up", "--set", "empty"])
    assert not any(INGEST_OVERLAY in token for token in stand), (
        f"стенд подкладывает оверлей ингеста: {stand}"
    )
    ingest = _compose_arguments_seen_by_stub(SH_INGEST, ["up", "--set", "empty"])
    assert any(INGEST_OVERLAY in token for token in ingest), (
        f"скрипт ингеста не подкладывает оверлей: {ingest}"
    )
    assert "ingest" in ingest, f"скрипт ингеста не включил профиль ingest: {ingest}"


@pytest.mark.skipif(not _sh_is_runnable(SH), reason="нет POSIX-оболочки")
def test_unix_stand_and_ingest_run_on_the_processor() -> None:
    """Shell-варианты не подкладывают оверлей видеокарты.

    Иначе на машине без NVIDIA они не поднимутся, а ошибка будет выглядеть как проблема
    драйвера, а не как неверный скрипт. Видеокарта на Unix и macOS не выдаётся намеренно
    (ADR-065): вариант там один, и он считает на процессоре.
    """
    for script in (SH, SH_INGEST):
        assert GPU_OVERLAY not in _compose_files_in(script), (
            f"{script.name} подкладывает {GPU_OVERLAY}: на Unix и macOS вариант один, и он "
            "считает на процессоре."
        )


def test_gpu_is_granted_only_by_overlay() -> None:
    """Бронь устройства обязана жить только в оверлее.

    Проверено, что перекрыть её сверху нельзя: ни `devices: []`, ни `devices: null` её не
    снимают, потому что в базовом файле она пришла через YAML-merge. Значит бронь можно
    убрать только в базовом файле, и гард обязан следить именно за этим.
    """
    runtime = INFRA / BASE_OVERLAY
    overlay = INFRA / GPU_OVERLAY
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


@pytest.mark.parametrize("script", SH_SCRIPTS)
def test_sh_has_no_bash_only_constructs(script: Path) -> None:
    """Shell-варианты объявлены как POSIX sh, поэтому конструкций bash в них быть не должно."""
    text = _read(script)
    for marker, why in (
        (r"\[\[", "двойные скобки [[ ]] — это bash, а не POSIX sh"),
        (r"\bfunction\s+\w+\s*\(\)", "объявление function — это bash"),
        (r"\bset\s+-[a-z]*o\s", "опция set -o — это bash"),
        (r"\$\{[A-Za-z_][A-Za-z0-9_]*\[[^}]*\]\}", "косвенное расширение ${a[b]} — это bash"),
        (r"<<-", "heredoc с табами — это bash"),
    ):
        assert not re.search(marker, text), f"{why}: {marker} в {script.name}"


@pytest.mark.parametrize("script", STAND_PARAMS)
def test_reranker_overlay_is_reachable_from_every_stand_script(script: Script) -> None:
    """Реранкерный запуск обязан существовать во всех скриптах стенда.

    Отдельный запуск с реранкером — это оверлей плюс флаг. Если флаг есть хотя бы в
    одном скрипте, стенд ведёт себя по-разному в зависимости от того, на какой системе
    его запустили, и это обнаружится замерами, а не чтением.
    """
    assert (INFRA / RERANKER_OVERLAY).is_file(), (
        f"нет оверлея {RERANKER_OVERLAY}: запуск с реранкером недостижим"
    )
    assert RERANKER_OVERLAY in _read(script.path), (
        f"{script.path.name} не умеет подключить {RERANKER_OVERLAY}: запуск с реранкером "
        "существует только на части систем"
    )


def test_reranker_overlay_enables_slot_on_both_pipeline_services() -> None:
    """Оверлей включает слот реранкера у обоих сервисов, способных исполнить пайплайн.

    `query-api` сегодня только принимает задание и отдаёт его воркеру, поэтому отсутствие
    слота у него безобидно. Но обход очереди — это синхронный путь, и тогда api исполнил бы
    пайплайн с дефолтом профиля, то есть ось переранжирования оказалась бы выключенной при
    включённой настройке. Проверяется положительный инвариант: оба сервиса объявляют слот.
    """
    document = yaml.safe_load((INFRA / RERANKER_OVERLAY).read_text(encoding="utf-8"))
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
    base = (INFRA / BASE_OVERLAY).read_text(encoding="utf-8")
    assert "bge_reranker" not in base, (
        "базовый runtime-контур включает bge_reranker: штатный запуск Демо начнёт "
        "обращаться к сервису реранкера, которого в нём нет"
    )
