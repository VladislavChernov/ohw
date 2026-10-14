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

COMMANDS = {"up", "down", "status", "load", "export", "restore"}

# Тома, к которым скрипты обращаются по имени: их нужно назвать, чтобы архивировать и
# восстановить. Остальные тома создаёт compose, и скриптам их знать незачем.
NAMED_VOLUME_SUFFIXES = ("neo4j", "ingestion")


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.fail(f"нет файла {path.name}: Demo без него не запускается ни на одной системе")
    return path.read_text(encoding="utf-8-sig")


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


def test_commands_match() -> None:
    sh_commands = _commands(_read(SH))
    ps_commands = _commands(_read(PS1))
    assert sh_commands, "в demo.sh не найдено ни одной команды"
    assert ps_commands, "в demo.ps1 не найдено ни одной команды"
    assert sh_commands == ps_commands == COMMANDS, (
        f"наборы команд разошлись: sh={sorted(sh_commands)}, ps1={sorted(ps_commands)}, "
        f"ожидалось {sorted(COMMANDS)}"
    )


def test_credentials_match() -> None:
    sh_text, ps_text = _read(SH), _read(PS1)
    assert _password(sh_text) == _password(ps_text), (
        "пароль базы в demo.sh и demo.ps1 разный. Том создан один раз, и стенд "
        "поднимется только с тем значением, которое совпадает с базой."
    )
    assert _api_key(sh_text) == _api_key(ps_text), (
        "ключ API в demo.sh и demo.ps1 разный: запросы будут уходить с ним и получать 401."
    )


def test_compose_files_match() -> None:
    expected = {"compose.runtime.yaml", "compose.ingest.yaml"}
    for path in (SH, PS1):
        text = _read(path)
        found = set(re.findall(r"compose\.[a-z]+\.yaml", text))
        assert found == expected, (
            f"{path.name} ссылается на {sorted(found)}, ожидалось {sorted(expected)}. "
            "Список compose-файлов должен быть одинаковым в обоих скриптах."
        )


def test_containers_and_volumes_match() -> None:
    """Контракт - равенство двух файлов, а не замороженный перечень имён.

    Перечислять «правильные» имена контейнеров здесь нельзя: такой список завтра устареет и
    начнёт врать, а расхождение между файлами - это то, что ломает работу по-настоящему.
    """
    sh_containers = set(re.findall(r"ohw-runtime-[a-z0-9-]+", _read(SH)))
    ps_containers = set(re.findall(r"ohw-runtime-[a-z0-9-]+", _read(PS1)))
    assert sh_containers == ps_containers, (
        f"скрипты обращаются к разным контейнерам: sh={sorted(sh_containers)}, "
        f"ps1={sorted(ps_containers)}. Один из них обратится не туда."
    )
    # Контейнеры, к которым скрипты обязаны обращаться: без них сценарий не работает.
    for required in ("ohw-runtime-neo4j", "ohw-runtime-ingestion"):
        assert required in sh_containers, f"ни один из скриптов не обращается к {required}"

    for suffix in NAMED_VOLUME_SUFFIXES:
        for path in (SH, PS1):
            assert suffix in _read(path), f"{path.name} не упоминает том с суффиксом {suffix}"


def test_sh_has_no_bash_only_constructs() -> None:
    """`demo.sh` объявлен как POSIX sh, поэтому конструкций bash в нём быть не должно."""
    text = _read(SH)
    for marker, why in (
        (r"\[\[", "двойные скобки [[ ]] — это bash, а не POSIX sh"),
        (r"\bfunction\s+\w+\s*\(\)", "объявление function — это bash"),
        (r"\bset\s+-[a-z]*o\s", "опция set -o — это bash"),
        (r"\$\{[A-Za-z_][A-Za-z0-9_]*\[[^}]*\]\}", "косвенное расширение ${a[b]} — это bash"),
        (r"<<-", "heredoc с табами — это bash"),
    ):
        assert not re.search(marker, text), f"{why}: {marker}"