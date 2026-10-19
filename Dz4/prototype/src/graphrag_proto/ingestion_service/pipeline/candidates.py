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
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

_log = logging.getLogger("graphrag_proto.ingestion.candidates")


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
    "CANDIDATES_KEEP_ENV",
    "CANDIDATES_SUFFIX",
    "CandidateList",
    "CandidateListStaleError",
    "CandidatesCleanupError",
    "CandidatesProductionError",
    "DiscardResult",
    "candidates_path_for_document",
    "count_remaining",
    "discard_candidates",
    "discard_document_upload",
    "keep_requested",
    "load_candidates",
    "produce_candidates",
    "sha256_of",
]


class CandidatesProductionError(RuntimeError):
    """Кандидаты для документа получить не удалось.

    Отдельный тип и отдельная судьба: раньше отсутствие списка означало тихий уход в
    vector-only со статусом `succeeded` (ADR-053), и джоба выглядела обработанной при пустом
    графе. Теперь это отказ с поимённой причиной, а не режим.
    """


def _ner() -> tuple[Any, Any]:
    """Модель NER создаётся один раз на процесс.

    `NewsNERTagger` поднимает модель slovnet, и создание на каждый документ съедало бы время
    дороже самой разметки. Кэш живёт в процессе: пайплайн однопоточный на стадию, а на
    перезагрузку корпуса процесс пересоздаётся, и кэш не переживает смену модели.
    """
    global _NER
    if _NER is None:
        from natasha import NewsEmbedding, Segmenter
        from natasha.ner import NewsNERTagger

        _NER = (NewsNERTagger(NewsEmbedding()), Segmenter())
    return _NER


_NER: tuple[Any, Any] | None = None


def produce_candidates(document_path: Path, text: str) -> Path:
    """Вычисляет кандидатов по тексту документа и кладёт файл рядом с ним.

    Логика стадии переиспользуется, а не переписывается: `build_candidate_set` остаётся
    единственным определением того, что считается именем. Второе определение разошлось бы
    со стадией при первом же изменении правил, и пайплайн стал бы мерить не то.

    Ошибка стадии не остаётся молчаливой: она поднимается как `CandidatesProductionError`,
    и джоба уходит в `failed`, а не в vector-only со статусом `succeeded`.
    """
    from graphrag_proto.pre_ingest.generate import (
        build_candidate_set,
        to_envelope,
    )

    # Каталог загрузки должен существовать: положить список рядом некуда. Отдельная
    # поимённая ошибка, а не сырое `FileNotFoundError` из `write_text` - по стеке было бы
    # не видно, что именно не так.
    if not document_path.parent.is_dir():
        raise CandidatesProductionError(
            f"каталог загрузки не найден: {document_path.parent}. Список кандидатов "
            f"кладётся рядом с файлом документа, и положить его некуда."
        )

    try:
        ner, segmenter = _ner()
        candidate_set = build_candidate_set(document_path, text, ner, segmenter)
    except Exception as exc: 
        raise CandidatesProductionError(
            f"кандидаты получить не удалось: {exc}. Без списка модель выдумывает имена "
            f"(ADR-052: 137 из 274), поэтому загрузка остановлена, а не упрощена."
        ) from exc

    envelope = to_envelope(candidate_set, "natasha+CANDIDATE_PATTERNS")
    target = candidates_path_for_document(document_path)
    target.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return target


def candidates_path_for_document(document_path: Path) -> Path:
    """Файл кандидатов РЯДОМ с файлом документа.

    Путь выводится из файла документа, а не приходит снаружи (ADR-053, решение от
    2026-10-14). Поэтому кандидаты не пересекают границу контейнеров: пустая папка,
    смонтированная снаружи, больше не может привести к их тихой потере, а документ и его
    список лежат в одном месте — во временном каталоге загрузки.
    """
    return document_path.with_name(f"{document_path.stem}{CANDIDATES_SUFFIX}")


#: Имя файла-признака, по которому считаются остатки во временной папке. Отдельный суффикс
#: не нужен: все файлы стадии называются `<имя>_candidates.json` (ADR-053), и суффикс
#: единственное, по чему остаток отличить от постороннего файла в той же папке.
CANDIDATES_SUFFIX = "_candidates.json"

#: Оставить кандидатов в папке после загрузки. По умолчанию ВЫКЛЮЧЕНО, то есть уборка
#: работает. Значение задаётся для отладки: посмотреть, чем именно разбирался документ,
#: иначе файл исчезает в тот же момент, когда стал интересен.
CANDIDATES_KEEP_ENV = "CANDIDATES_KEEP"


def keep_requested() -> bool:
    """Оставлена ли уборка выключенной намеренно.

    Нераспознанное значение НЕ трактуется молча как «убирать»: молчание здесь опаснее
    отказа - папка копит файлы, и накопившийся мусор выглядит как «уборка не работает»
    при работающей уборке. Тот же приём, что у `MD_KEEP_CODE_SPLIT`.
    """
    raw = (os.environ.get(CANDIDATES_KEEP_ENV) or "").strip()
    if not raw:
        return False
    value = raw.lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    _log.warning(
        "%s=%r не распознано; ожидается 1/0. Трактуется как «убираем», но отладка "
        "может молча не включиться.",
        CANDIDATES_KEEP_ENV,
        raw,
    )
    return False


class CandidatesCleanupError(RuntimeError):
    """Не удалось удалить файл кандидатов после состоявшейся загрузки.

    Отдельный тип, потому что это единственный случай, где молчать нельзя: файл не
    удалён - значит он уедет вместе с папкой в следующий запуск и предъявит себя как
    «свежий» список для следующего документа. Но загрузка при этом состоялась, и падать
    из-за этого джобу нельзя: запись уже в базе. Поэтому ошибка возвращается вызывающему,
    а он объявляет её в логе и в отчёте, не меняя статус джобы.
    """


#: Результат уборки. Строки, а не флаг: «файла не было», «убрали» и «оставили по
#: просьбе» - три разных состояния, и свёрнутые в один булев флаг они выглядели бы
#: одинаково - а это ровно тот случай, когда отладчик не видит, что уборка выключена.
DiscardResult = Literal["removed", "absent", "kept"]


def discard_candidates(document_path: Path) -> DiscardResult:
    """Удаляет файл кандидатов документа.

    Путь берётся из `candidates_path_for_document` - единственного определения. Второе
    определение, собиравшее имя из полного имени файла, дало бы `...md_candidates.json`
    вместо `..._candidates.json`, и уборщик тихо удалял бы не тот файл, выглядя исправным.

    Вызывается после состоявшейся загрузки и после отсечки no-op: в обоих случаях
    кандидаты больше не нужны, а оставленный файл уехал бы в следующий запуск.

    Намеренно НЕ вызывается после отказа или деградации: там файл остаётся, он нужен для
    разбора причины.

    При `CANDIDATES_KEEP=1` файл сохраняется и возвращается `kept` - вызывающий обязан
    это объявить, иначе отключённая уборка выглядит как работающая.
    """
    path = candidates_path_for_document(document_path)
    if keep_requested():
        return "kept" if path.is_file() else "absent"
    try:
        path.unlink()
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        raise CandidatesCleanupError(
            f"файл кандидатов не удалён: {path}: {exc}. Он уедет вместе с папкой в следующий "
            f"запуск и предъявит себя как список текущей версии документа."
        ) from exc
    return "removed"


def count_remaining(directory: Path | None) -> int:
    """Сколько файлов кандидатов осталось во временной папке.

    Считает файлы, а не байты: вопрос «осталось ли что-то убирать» и вопрос «сколько это
    занимает» - разные, и молчание по первому при полном втором означало бы, что уборка
    не работает.
    """
    if directory is None or not directory.is_dir():
        return 0
    return sum(1 for p in directory.glob(f"*{CANDIDATES_SUFFIX}") if p.is_file())


class CandidateListConfigError(RuntimeError):
    """Файл документа для кандидатов неизвестен.

    Отдельный тип, потому что это НЕ «списка нет у документа». Файла документа нет -
    значит негде положить и список, и пайплайн не знает, чей текст разбирать. Это ошибка
    вызывающего, а не режим загрузки.
    """


def load_candidates(document_path: Path, text: str) -> CandidateList:
    """Читает список кандидатов, лежащий рядом с файлом документа.

    Возвращает список, а не `None`: после решения от 2026-10-14 у документа не может не
    быть своего списка, и «загрузить только в вектор» перестало быть допустимым исходом.
    Отсутствие файла — отказ с поимённой причиной, потому что тихий vector-only при статусе
    `succeeded` и есть тот ложный успех, который ADR-053 сформулировал.

    Сверка `sha256` сохранена намеренно. Теперь файл кладёт сам пайплайн, так что в обычном
    пути равенство истинно по построению - и именно поэтому проверка ловит другое: оборванную
    или подложенную запись. Снимать её можно отдельным решением, когда каталог станет
    недоступен снаружи (ADR-053, решение от 2026-10-14, п. 5).
    """
    path = candidates_path_for_document(document_path)
    if not path.is_file():
        raise CandidatesProductionError(
            f"файл кандидатов не найден: {path}. Без списка модель выдумывает имена "
            f"(ADR-052: 137 из 274), поэтому загрузка остановлена, а не упрощена."
        )

    data = json.loads(path.read_text(encoding="utf-8"))
    expected = sha256_of(text)
    actual = str(data.get("source_sha256") or "")
    if actual != expected:
        raise CandidateListStaleError(
            f"список кандидатов не соответствует тексту документа {document_path.name}: "
            f"в файле {actual}, документ даёт {expected}."
        )

    raw = data.get("candidates")
    if not isinstance(raw, list):
        raise TypeError(f"в {path} поле candidates не список")
    candidates = tuple(str(name).strip() for name in raw if str(name).strip())
    if not candidates:
        # Пустой список - НЕ отказ. Документ может законно не содержать ни одного имени,
        # и валить из-за этого загрузку означало бы запретить грузить такие документы.
        # Ограничение при этом не исчезает, а становится строже (ADR-053, Дополнение 4):
        # `render_candidates_block(())` запрещает модели выдать что-либо вообще. То есть
        # пустой список означает «список пуст», а не «списка нет».
        _log.warning(
            "список кандидатов пуст для %s: разбор пойдёт с полным запретом называть",
            document_path.name,
        )
    return CandidateList(
        source=document_path.name,
        source_sha256=actual,
        candidates=candidates,
    )


def discard_document_upload(document_path: Path) -> DiscardResult:
    """Убирает файл документа после состоявшейся загрузки.

    До этого файл удалялся только при обрыве потока, и в каталоге загрузки копились полные
    копии документов: проверено на томе, 62 файла, 2.8 МБ. «Документ в двух местах» было
    нарушено с этой стороны, пока файлы жили.

    Те же правила, что у кандидатов: после успеха и при no-op удаляем, при отказе оставляем,
    отладка отключает уборку через тот же ключ.
    """
    if keep_requested():
        return "kept" if document_path.is_file() else "absent"
    try:
        document_path.unlink()
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        raise CandidatesCleanupError(
            f"файл документа не удалён: {document_path}: {exc}"
        ) from exc
    return "removed"