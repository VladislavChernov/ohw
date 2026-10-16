"""Гард: `Demo/demo.sh` и `Demo/demo.ps1` не должны разойтись.

Два файла с одной логикой - заведомое второе место правды, а в прошлой сессии расхождение
между каталогом и прибором как раз стоило двух молчаливых отказов. Проверяем не текст, а то,
что действительно ломает работу при расхождении:

- набор команд (не появилась новая команда только в одном файле);
- учётные данные (разные пароль или ключ означают: один из них не поднимет стенд);
- перечень compose-файлов;
- набор томов и имён контейнеров, к которым идут обращения.

Положительный контраст: «оба файла задают одинаковые учётные данные и одинаковый набор
команд», а не «в файле нет такой-то строки».
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

DEMO_DIR = Path(__file__).resolve().parents[2] / "Demo"
SH = DEMO_DIR / "demo.sh"
PS1 = DEMO_DIR / "demo.ps1"
SH_CPU = DEMO_DIR / "demo-cpu.sh"
PS1_CPU = DEMO_DIR / "demo-cpu.ps1"
INFRA = DEMO_DIR.parent / "prototype" / "infra"

COMMANDS = {"up", "down", "status", "load", "export", "restore"}

# Тома, к которым скрипты обращаются по имени: их нужно назвать, чтобы архивировать и
# восстановить. Остальные тома создаёт compose, и скриптам их знать незачем.
NAMED_VOLUME_SUFFIXES = ("neo4j", "ingestion")

# Пары файлов, которые обязаны вести себя одинаково, и ожидаемый набор compose-файлов
# у каждой. Видеокарту выдаёт compose сам, поэтому GPU-вариант подкладывает оверлей, а
# CPU-вариант обходится базовым файлом: на этом различии и держится всё разделение.
PAIRS = (
    pytest.param(
        SH, PS1,
        {"compose.runtime.yaml", "compose.ingest.yaml", "compose.runtime-gpu.yaml"},
        id="gpu",
    ),
    pytest.param(
        SH_CPU, PS1_CPU,
        {"compose.runtime.yaml", "compose.ingest.yaml"},
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
    assert sh_commands == ps_commands == COMMANDS, (
        f"наборы команд разошлись: {sh.name}={sorted(sh_commands)}, "
        f"{ps1.name}={sorted(ps_commands)}, ожидалось {sorted(COMMANDS)}"
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


def test_only_gpu_variant_uses_gpu_overlay() -> None:
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
    # Контейнеры, к которым скрипты обязаны обращаться: без них сценарий не работает.
    for required in ("ohw-runtime-neo4j", "ohw-runtime-ingestion"):
        assert required in sh_containers, f"ни один из скриптов не обращается к {required}"

    for suffix in NAMED_VOLUME_SUFFIXES:
        for path in (sh, ps1):
            assert suffix in _read(path), f"{path.name} не упоминает том с суффиксом {suffix}"


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