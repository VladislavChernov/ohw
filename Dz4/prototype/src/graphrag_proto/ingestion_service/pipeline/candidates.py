"""Кандидаты сущностей, приходящие из стадии pre-ingest (ADR-053).

Зачем. Без списка кандидатов модель выдумывает имена: ADR-052 измерил 137 из 274 (50.0%)
объявленных имён, отсутствующих в тексте своего чанка, и выдуманное имя разрешается в
графе так же, как настоящее. Стадия `pre_ingest` производит список заранее, этот модуль
читает его при загрузке.

Два свойства, без которых модуль бесполезен:

* **свежесть.** Список привязан к `sha256` документа. Документ изменился, а список остался
  от прежней версии - и разбор молча работает по чужому списку. Симптом выглядел бы как
  «модель не вытянула сущности», и виноватым оказался бы разбор, а не список.
* **отсутствие списка - не ошибка, а режим.** По решению владельца документ без списка
  грузится только в вектор: графая ось для него не строится вовсе.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

#: Имя переменной окружения с папкой, где лежат `<документ>_candidates.json`.
#: Значение обязательно: пустая переменная означала бы «стадия не запускалась», и без
#: явной ошибки корпус молча разбирался бы без ограничения имён.
CANDIDATES_DIR_ENV = "CANDIDATES_DIR"


class CandidateListStaleError(RuntimeError):
    """Список кандидатов не соответствует текущей версии документа.

    Отдельный тип, потому что это единственная ситуация, где молчать нельзя: работа по
    чужому списку даёт правдоподобный, но неверный граф.
    """


@dataclass(frozen=True)
class CandidateList:
    """Список кандидатов одного документа."""

    source: str
    source_sha256: str
    candidates: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.candidates)

    def for_chunk(self, text: str) -> tuple[str, ...]:
        """Кандидаты, реально встречающиеся в тексте чанка.

        Список документа в 7 раз больше типичного чанка: на `docs/05_adr_log.md` это
        1076 кандидатов против медианных 43 на чанк. Отдавать модели весь список значило бы
        платить токенами за то, чего в этом куске текста нет.
        """
        haystack = text.casefold()
        return tuple(name for name in self.candidates if name.casefold() in haystack)


#: Контракт свежести живёт в одном месте - `pre_ingest.generate.sha256_of`. Своё второе
#: определение означало бы, что стадия и ингест могут посчитать хеш по-разному, и
#: расхождение выглядело бы как «список устарел» на вполне свежем списке.
from graphrag_proto.pre_ingest.generate import sha256_of

__all__ = [
    "CANDIDATES_DIR_ENV",
    "CandidateList",
    "CandidateListConfigError",
    "CandidateListStaleError",
    "candidates_dir_from_env",
    "candidates_path",
    "load_candidates",
    "sha256_of",
]


def candidates_path(directory: Path, source_name: str) -> Path:
    """`<папка>/<имя документа>_candidates.json`."""
    return directory / f"{source_name}_candidates.json"


class CandidateListConfigError(RuntimeError):
    """Папка с кандидатами не настроена.

    Отдельный тип и отдельная судьба, потому что это НЕ «списка нет у документа».
    Настройка не задана - значит стадия `pre_ingest` не запускалась вовсе, и система
    молча разбирала бы весь корпус без ограничения, выглядя как работающая. Тот же
    принцип, что у `LLM_MODEL`: отсутствие обязательной настройки - ошибка
    конфигурации, а не повод угадать.
    """


def load_candidates(
    directory: Path | None,
    source_name: str,
    text: str,
    *,
    on_missing: str = "vector_only",
) -> CandidateList | None:
    """Читает список кандидатов для документа.

    `directory=None` - ошибка конфигурации, а не отсутствие списка: папка обязана быть
    настроена, иначе ограничение не работает ни для одного документа.

    `on_missing="vector_only"` - у документа нет своего файла, и по решению владельца он
    грузится только в вектор (функция возвращает `None`). `on_missing="error"` - то же
    считается сбоем.
    """
    if directory is None:
        raise CandidateListConfigError(
            f"{CANDIDATES_DIR_ENV} не задан: список кандидатов для {source_name} прочитать "
            f"неоткуда. Запустите стадию pre-ingest (run_pre_ingest.ps1) и задайте "
            f"{CANDIDATES_DIR_ENV}. Без этого извлечение работает без ограничения имён."
        )

    path = candidates_path(directory, source_name)
    if not path.is_file():
        if on_missing == "error":
            raise FileNotFoundError(f"файл кандидатов не найден: {path}")
        return None

    data = json.loads(path.read_text(encoding="utf-8"))
    expected = sha256_of(text)
    actual = str(data.get("source_sha256") or "")
    if actual != expected:
        raise CandidateListStaleError(
            f"список кандидатов устарел для {source_name}: "
            f"в файле {actual}, документ даёт {expected}. "
            f"Перезапустите стадию pre-ingest для этого документа."
        )

    raw = data.get("candidates")
    if not isinstance(raw, list):
        raise TypeError(f"в {path} поле candidates не список")
    candidates = tuple(str(name).strip() for name in raw if str(name).strip())
    return CandidateList(
        source=source_name,
        source_sha256=actual,
        candidates=candidates,
    )


def candidates_dir_from_env(env: dict[str, str] | None = None) -> Path | None:
    """Папка с кандидатами из окружения.

    Пустая переменная - это «стадия не запускалась», а не «искать в корне»: искать в
    корне означало бы подхватить чужой каталог и разбирать документ по случайному списку.
    """
    source = os.environ if env is None else env
    raw = (source.get(CANDIDATES_DIR_ENV) or "").strip()
    return Path(raw) if raw else None