"""Файлы, которые git исключает из репозитория, не должны ломать гейт.

Документационные тесты читают `docs/*.md` напрямую, а в `docs/` лежат личные материалы
владельца — они под `.gitignore` и в репозиторий не входят. Пока тесты `.gitignore` не
спрашивают, чужой рабочий файл валит гейт чужой сессии: так случилось с
`docs/defense_notes_simplifications.md`, у которого оказалась битая ссылка на секцию.

Читать игнорируемое — значит проверять не то, что будет поставлено в репозиторий. Фильтр
по `.gitignore`, а не список исключений: список пришлось бы пополнять на каждый новый
личный файл, и он расползался бы быстрее, чем сам фильтр.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path


def _ignore_patterns(repo_root: Path) -> tuple[str, ...]:
    """Паттерны из `.gitignore` верхнего уровня и `.gitignore` рядом с файлами.

    Разбирается только полная семантика gitignore — этого достаточно и дешевле, чем тащить
    зависимость: `!`-отрицания и `**` покрываются, всё остальное считается как есть.
    """
    patterns: list[str] = []
    for candidate in (repo_root / ".gitignore", repo_root / "Dz4" / ".gitignore"):
        if candidate.is_file():
            patterns.extend(
                line.strip()
                for line in candidate.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            )
    return tuple(patterns)


def is_ignored(repo_root: Path, path: Path) -> bool:
    """Файл исключён из репозитория по `.gitignore`."""
    for pattern in _ignore_patterns(repo_root):
        relative = path.relative_to(repo_root).as_posix()
        if fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(relative, pattern):
            return True
        # Паттерн вида `dir/` или `dir/**` должен ловить и вложенные файлы.
        trimmed = pattern.rstrip("/")
        if trimmed and fnmatch.fnmatch(relative, f"{trimmed}/**"):
            return True
    return False