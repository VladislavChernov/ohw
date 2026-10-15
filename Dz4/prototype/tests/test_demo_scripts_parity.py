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

import pytest

DEMO_DIR = Path(__file__).resolve().parents[2] / "Demo"
SH = DEMO_DIR / "demo.sh"
PS1 = DEMO_DIR / "demo.ps1"
SH_CPU = DEMO_DIR / "demo-cpu.sh"
PS1_CPU = DEMO_DIR / "demo-cpu.ps1"

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