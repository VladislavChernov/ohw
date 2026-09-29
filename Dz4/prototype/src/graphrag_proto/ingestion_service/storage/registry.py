"""Document Registry (SQLite): идемпотентность INGEST, версии, soft-delete.

ADR-014: идентичность источника — (domain, source_url) в пределах активного профиля;
повторная загрузка того же content_hash — no-op; изменение контента — новая версия,
старая помечается superseded; удаление — soft delete.
"""

from __future__ import annotations

import builtins
import hashlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from graphrag_proto.ingestion_service.document import Document
from graphrag_proto.sqlite_utils import connect_sqlite

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_DELETED = "deleted"


class DocumentRegistry:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.RLock()
        self._source_locks: dict[tuple[str, str], threading.RLock] = {}
        self._conn = connect_sqlite(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS documents ("
            "doc_id TEXT PRIMARY KEY, "
            "source_url TEXT NOT NULL, "
            "domain TEXT NOT NULL, "
            "doc_type TEXT NOT NULL, "
            "version INTEGER NOT NULL, "
            "content_hash TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "created_at TEXT NOT NULL"
            ")"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_identity "
            "ON documents (domain, source_url)"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def source_lock(self, domain: str, source_url: str) -> Iterator[None]:
        key = (domain, source_url)
        with self._lock:
            lock = self._source_locks.setdefault(key, threading.RLock())
        with lock:
            yield

    @contextmanager
    def domain_lock(self, domain: str) -> Iterator[None]:
        del domain
        with self._lock:
            yield

    def active_source_count(self, domain: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(DISTINCT source_url) FROM documents "
                "WHERE domain = ? AND status = ?",
                (domain, STATUS_ACTIVE),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def current_version(self, domain: str, source_url: str) -> int:
        """Максимальная версия источника (0 — нет). Монотонные версии.

        Учитывает и deleted/superseded: повторный INGEST после soft-delete не
        переиспользует номер версии удалённой записи (ADR-014 — новая версия).
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(version) FROM documents "
                "WHERE domain = ? AND source_url = ?",
                (domain, source_url),
            ).fetchone()
        return row[0] if row and row[0] is not None else 0

    def latest_active(self, domain: str, source_url: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT doc_id, version, content_hash, status FROM documents "
                "WHERE domain = ? AND source_url = ? AND status = ? "
                "ORDER BY version DESC LIMIT 1",
                (domain, source_url, STATUS_ACTIVE),
            ).fetchone()
        if not row:
            return None
        return {
            "doc_id": row[0],
            "version": row[1],
            "content_hash": row[2],
            "status": row[3],
        }

    def upsert(self, document: Document) -> tuple[str, int, bool]:
        """Идемпотентная регистрация документа.

        Возвращает (doc_id, version, created_new):
        - неизменённый source_url (тот же content_hash) -> (существующий doc_id, версия, False);
        - изменился контент -> superseded старая, новая версия -> (новый doc_id, version+1, True).
        """
        import uuid
        from datetime import datetime

        with self._lock:
            now = datetime.now(UTC).isoformat(timespec="seconds")
            current = self.latest_active(document.domain, document.source_url)
            if current and current["content_hash"] == document.content_hash:
                return current["doc_id"], current["version"], False

            version = self.current_version(document.domain, document.source_url) + 1
            if current and current["status"] == STATUS_ACTIVE:
                self._conn.execute(
                    "UPDATE documents SET status = ? WHERE doc_id = ?",
                    (STATUS_SUPERSEDED, current["doc_id"]),
                )
            doc_id = str(uuid.uuid4())
            self._conn.execute(
                "INSERT INTO documents (doc_id, source_url, domain, doc_type, version, "
                "content_hash, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc_id,
                    document.source_url,
                    document.domain,
                    document.doc_type,
                    version,
                    document.content_hash,
                    STATUS_ACTIVE,
                    now,
                ),
            )
            self._conn.commit()
            return doc_id, version, True

    def soft_delete(self, domain: str, source_url: str) -> bool:
        """Soft delete активной версии источника (ADR-014: статус deleted)."""
        with self._lock:
            current = self.latest_active(domain, source_url)
            if not current:
                return False
            self._conn.execute(
                "UPDATE documents SET status = ? WHERE doc_id = ?",
                (STATUS_DELETED, current["doc_id"]),
            )
            self._conn.commit()
            return True

    def rollback_soft_delete(self, domain: str, source_url: str) -> bool:
        """Откат soft-delete: последняя deleted-версия возвращается в active.

        UC12-06 (ADR-028, spec §2а): компенсирующее действие при окончательном
        отказе удаления в хранилищах — реестр не остаётся в статусе deleted при
        живых данных осей; повторная джоба проходит полный путь (L2-06).
        Возвращает True, если deleted → active переведена запись, иначе False.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT doc_id FROM documents "
                "WHERE domain = ? AND source_url = ? AND status = ? "
                "ORDER BY version DESC LIMIT 1",
                (domain, source_url, STATUS_DELETED),
            ).fetchone()
            if not row:
                return False
            self._conn.execute(
                "UPDATE documents SET status = ? WHERE doc_id = ?",
                (STATUS_ACTIVE, row[0]),
            )
            self._conn.commit()
            return True

    def data_revision(self, domain: str) -> str | None:
        """Ревизия данных домена — fingerprint активного сета (ADR-026).

        ``sha256`` над отсортированным набором пар ``(source_url, content_hash)``
        активных документов домена. Идемпотентен: повторный INGEST того же источника
        и ``content_hash`` (no-op, ADR-014) не меняет отпечаток; добавление/изменение/
        soft-delete меняет. ``None`` — активных документов у домена нет.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT source_url, content_hash FROM documents "
                "WHERE domain = ? AND status = ?",
                (domain, STATUS_ACTIVE),
            ).fetchall()
        if not rows:
            return None
        digest = hashlib.sha256()
        for source_url, content_hash in sorted(set(rows)):
            digest.update(source_url.encode("utf-8"))
            digest.update(b"\0")
            digest.update(content_hash.encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()

    def data_revision_after(self, document: Document) -> str | None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT source_url, content_hash FROM documents "
                "WHERE domain = ? AND status = ?",
                (document.domain, STATUS_ACTIVE),
            ).fetchall()
        active = {
            (str(source_url), str(content_hash))
            for source_url, content_hash in rows
            if str(source_url) != document.source_url
        }
        active.add((document.source_url, document.content_hash))
        if not active:
            return None
        digest = hashlib.sha256()
        for source_url, content_hash in sorted(active):
            digest.update(source_url.encode("utf-8"))
            digest.update(b"\0")
            digest.update(content_hash.encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()

    def data_revision_updated_at(self, domain: str) -> str | None:
        """Максимальный ``created_at`` активных документов домена (или None)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(created_at) FROM documents "
                "WHERE domain = ? AND status = ?",
                (domain, STATUS_ACTIVE),
            ).fetchone()
        return row[0] if row and row[0] is not None else None


class JobStore:
    """SQLite-журнал джоб ingestion: статус, текущий этап, сообщение об ошибке."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn = connect_sqlite(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS jobs ("
            "job_id TEXT PRIMARY KEY, "
            "source_url TEXT NOT NULL, "
            "domain TEXT NOT NULL, "
            "doc_type TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "stage TEXT, "
            "error TEXT, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL"
            ")"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS job_stages ("
            "job_id TEXT NOT NULL, "
            "stage TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "message TEXT, "
            "ts TEXT NOT NULL, "
            "PRIMARY KEY (job_id, stage)"
            ")"
        )
        # Структурные сигналы джобы — отдельная таблица, а НЕ текст в `job_stages.message`.
        # Причина: число, на котором строится решение, не должно зависеть от разбора
        # сообщения. Одна рефакторинга формата — и метрика потерь молча читает ноль, а
        # «мы никогда не теряем слой» выглядит как хорошая новость. Тихий ноль в метрике
        # потерь — худший вид поломки, он не выглядит как поломка.
        #
        # Новая таблица, а не колонка в `job_stages`: `CREATE TABLE IF NOT EXISTS` создаёт
        # её на существующих базах без миграции, тогда как добавление колонки потребовало бы
        # ALTER TABLE на каждой развёрнутой базе.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS job_signals ("
            "job_id TEXT NOT NULL, "
            "stage TEXT NOT NULL, "
            "name TEXT NOT NULL, "
            "ts TEXT NOT NULL, "
            "PRIMARY KEY (job_id, stage, name)"
            ")"
        )
        # ЧИСЛОВЫЕ факты обогащения: причина и объёмы. Флагов здесь нет намеренно —
        # «деградация» и «слой потерян» уже живут в `job_signals`, и вторая копия
        # означала бы два источника для одного факта: они разъедутся, и потребитель
        # будет читать не тот, что сработал. Здесь только то, чего у флагов нет: сколько
        # именно исчезло и сколько модель вернула.
        #
        # Отдельная таблица, а не колонка в `job_stages`: `CREATE TABLE IF NOT EXISTS`
        # создаёт её на существующих базах без миграции, тогда как добавление колонки
        # потребовало бы ALTER TABLE на каждой развёрнутой базе.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS job_enrichment ("
            "job_id TEXT PRIMARY KEY, "
            "cause TEXT, "
            "lost_entities INTEGER NOT NULL DEFAULT 0, "
            "lost_edges INTEGER NOT NULL DEFAULT 0, "
            "llm_records INTEGER NOT NULL DEFAULT 0, "
            "llm_edges INTEGER NOT NULL DEFAULT 0, "
            "ts TEXT NOT NULL"
            ")"
        )
        # ДЛИТЕЛЬНОСТИ стадий: измеряются в момент перехода и хранятся явно.
        #
        # Почему не разность `job_stages.ts`: `update_stage` при переходе к следующей
        # стадии перезаписывает `ts` предыдущей, поэтому у всех завершённых стадий `ts` —
        # момент ОКОНЧАНИЯ. Разности соседних `ts` длительностями не являются, а
        # длительность последней стадии не восстанавливается вовсе. По этой же причине
        # повышение разрешения `ts` до миллисекунд контракт не удовлетворяет: моменты
        # останутся моментами окончания, просто более точными.
        #
        # Почему новая таблица, а не колонка в `job_stages`: `CREATE TABLE IF NOT EXISTS`
        # создаёт её на уже развёрнутой базе без миграции, а колонка потребовала бы
        # ALTER TABLE везде, где пайплайн уже отработал. Тот же довод, что и у
        # `job_signals`/`job_enrichment`.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS job_stage_durations ("
            "job_id TEXT NOT NULL, "
            "stage TEXT NOT NULL, "
            "started_ts TEXT NOT NULL, "
            "duration_ms INTEGER, "
            "PRIMARY KEY (job_id, stage)"
            ")"
        )
        # Факт уборки осиротевших связей/узлов (ADR-014, `docs/02` §4.5). Таблица, а не
        # сигнал в `job_signals`: решение строится на числах (сколько посчитано, сколько
        # удалено), а сигнал — имя, и разбор текста для числа недопустим.
        # `planned_*` и `removed_*` разнесены: подсчёт и удаление разделены по времени,
        # и их расхождение — сигнал о гонке, который нельзя терять.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS job_orphan_cleanup ("
            "job_id TEXT PRIMARY KEY, "
            "domain TEXT NOT NULL, "
            "planned_relations INTEGER NOT NULL DEFAULT 0, "
            "removed_relations INTEGER NOT NULL DEFAULT 0, "
            "planned_nodes INTEGER NOT NULL DEFAULT 0, "
            "removed_nodes INTEGER NOT NULL DEFAULT 0, "
            "mode TEXT NOT NULL, "
            "ts TEXT NOT NULL"
            ")"
        )
        # Паспорт извлечения - отдельной таблицей по тем же основаниям, что и факты выше:
        # это числа и имена, которые должен сравнивать потребитель, а не текст, который
        # он разбирает. `message` стадии занят причиной деградации, и `note_stage`
        # перетирает - закреплено тестом, - поэтому паспорт не мог бы туда поместиться.
        # Инструкция хранится целиком: она и есть переиспользуемая часть, а текст чанков
        # уже лежит в узлах `Chunk`, и вторая копия корпуса в базе джоб была бы дороже
        # пользы. `CREATE TABLE IF NOT EXISTS` - как и у соседних таблиц, миграций нет.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS job_extraction_passport ("
            "job_id TEXT PRIMARY KEY, "
            "domain TEXT NOT NULL, "
            "source_url TEXT NOT NULL, "
            "document_version INTEGER NOT NULL DEFAULT 0, "
            "profile TEXT NOT NULL, "
            "llm_enabled INTEGER NOT NULL DEFAULT 0, "
            "model TEXT NOT NULL, "
            "temperature TEXT NOT NULL, "
            "max_tokens TEXT NOT NULL, "
            "timeout_s TEXT NOT NULL, "
            "seed TEXT NOT NULL, "
            "instruction_fingerprint TEXT NOT NULL, "
            "identity TEXT NOT NULL, "
            "instruction TEXT NOT NULL, "
            "ts TEXT NOT NULL"
            ")"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def create(self, job_id: str, source_url: str, domain: str, doc_type: str) -> None:
        from datetime import datetime

        with self._lock:
            now = datetime.now(UTC).isoformat(timespec="seconds")
            self._conn.execute(
                "INSERT INTO jobs (job_id, source_url, domain, doc_type, status, stage, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', NULL, ?, ?)",
                (job_id, source_url, domain, doc_type, now, now),
            )
            self._conn.commit()

    def _open_stage_clock(self, job_id: str, stage: str, now: datetime) -> None:
        """Открыть часы стадии в момент её фактического начала.

        Разрешение миллисекундное — контракт требует границ гистограммы от 0.005 с, и
        секундные часы дали бы ровно тот ноль, ради устранения которого всё затевалось.

        `INSERT OR REPLACE` означает «последнее наблюдение побеждает»: повторный вход в
        ту же стадию перезапускает её часы. Это совпадает с семантикой `job_stages`,
        где повторный вызов тоже перезаписывает строку, и не требует отдельного решения
        про накопление по попыткам.
        """
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO job_stage_durations "
                "(job_id, stage, started_ts, duration_ms) VALUES (?, ?, ?, NULL)",
                (job_id, stage, now.isoformat(timespec="milliseconds")),
            )

    def _close_stage_clock(self, job_id: str, stage: str, now: datetime) -> None:
        """Записать прошедшее время стадии в момент её завершения.

        Повторное закрытие ничего не делает: длительность факта, а не пересчёт задним
        числом. Отсутствие строки тоже не ошибка — стадии могла не быть.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT started_ts, duration_ms FROM job_stage_durations "
                "WHERE job_id=? AND stage=?",
                (job_id, stage),
            ).fetchone()
            if row is None or row[1] is not None:
                return
            started = datetime.fromisoformat(str(row[0]))
            elapsed_ms = round((now - started).total_seconds() * 1000)
            self._conn.execute(
                "UPDATE job_stage_durations SET duration_ms=? WHERE job_id=? AND stage=?",
                # Часы могут «поехать назад» при переводе; длительность отрицательной
                # быть не может, а молчаливый ноль в метрике хуже явного нуля.
                (max(0, int(elapsed_ms)), job_id, stage),
            )

    def update_stage(self, job_id: str, stage: str, message: str = "") -> None:
        from datetime import datetime

        with self._lock:
            now_dt = datetime.now(UTC)
            now = now_dt.isoformat(timespec="seconds")
            status_row = self._conn.execute(
                "SELECT status, stage FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if status_row is None:  # pragma: no cover - джоба удалена до старта
                return
            if status_row[0] == "cancelled":
                return  # отменённую джобу не перезапускаем (ADR-018)
            prev_stage = status_row[1] if status_row[1] else ""
            if prev_stage and prev_stage != stage:
                # предыдущий этап завершён -> журнал показывает реальное выполнение
                self._conn.execute(
                    "UPDATE job_stages SET status='succeeded', ts=? "
                    "WHERE job_id=? AND stage=? AND status='running'",
                    (now, job_id, prev_stage),
                )
                # длительность меряется здесь, а не восстанавливается из `ts` позже
                self._close_stage_clock(job_id, prev_stage, now_dt)
            self._conn.execute(
                "UPDATE jobs SET status='running', stage=?, updated_at=? WHERE job_id=?",
                (stage, now, job_id),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO job_stages (job_id, stage, status, message, ts) "
                "VALUES (?, ?, 'running', ?, ?)",
                (job_id, stage, message, now),
            )
            self._open_stage_clock(job_id, stage, now_dt)
            self._conn.commit()

    def finish(self, job_id: str, status: str, error: str | None = None) -> None:
        from datetime import datetime

        with self._lock:
            now_dt = datetime.now(UTC)
            now = now_dt.isoformat(timespec="seconds")
            status_row = self._conn.execute(
                "SELECT status FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            # отмена финальна: успех после неё не перезаписывает 'cancelled'
            terminal = "cancelled" if (
                status_row is not None and status_row[0] == "cancelled"
            ) else status
            self._conn.execute(
                "UPDATE jobs SET status=?, error=?, updated_at=? WHERE job_id=?",
                (terminal, error, now, job_id),
            )
            # все «висящие» running-этапы приводим к финальному статусу журнала
            stage_status = "succeeded" if terminal == "succeeded" else terminal
            running = self._conn.execute(
                "SELECT stage FROM job_stages WHERE job_id=? AND status='running'",
                (job_id,),
            ).fetchall()
            # Последняя стадия закрывается только здесь: `update_stage` больше не
            # вызывается, поэтому без этого её время не сохранилось бы вовсе.
            for (running_stage,) in running:
                self._close_stage_clock(job_id, str(running_stage), now_dt)
            self._conn.execute(
                "UPDATE job_stages SET status=? WHERE job_id=? AND status='running'",
                (stage_status, job_id),
            )
            self._conn.commit()

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        return bool(row and row[0] == "cancelled")

    def cancel(self, job_id: str) -> bool:
        from datetime import datetime

        with self._lock:
            now = datetime.now(UTC).isoformat(timespec="seconds")
            cur = self._conn.execute(
                "SELECT status FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if not cur or cur[0] in ("succeeded", "failed", "cancelled"):
                return False
            self._conn.execute(
                "UPDATE jobs SET status='cancelled', stage=NULL, updated_at=? WHERE job_id=?",
                (now, job_id),
            )
            self._conn.commit()
            return True

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT job_id, source_url, domain, doc_type, status, stage, error, created_at "
                "FROM jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "job_id": row[0],
            "source_url": row[1],
            "domain": row[2],
            "doc_type": row[3],
            "status": row[4],
            "stage": row[5],
            "error": row[6],
            "created_at": row[7],
        }

    def list(self, page: int, page_size: int) -> tuple[builtins.list[dict[str, Any]], int]:
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM jobs"
            ).fetchone()[0]
            rows = self._conn.execute(
                "SELECT job_id, source_url, domain, doc_type, status, stage, created_at "
                "FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (page_size, (page - 1) * page_size),
            ).fetchall()
        items = [
            {
                "job_id": r[0],
                "source_url": r[1],
                "domain": r[2],
                "doc_type": r[3],
                "status": r[4],
                "stage": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]
        return items, total

    def stage_durations(self, job_id: str) -> builtins.dict[str, int]:
        """Длительности завершённых стадий в миллисекундах: ``{стадия: мс}``.

        Идущая стадия в словаре ОТСУТСТВУЕТ, а не равна нулю. Нулевая длительность и
        отсутствие наблюдения — разные вещи, и слияние их делает метрику
        правдоподобной и неверной одновременно: в среднем по стадиям появился бы
        ноль, который на самом деле означает «не считали».

        Единица хранения — миллисекунды; в отчёт метрики уходит в секундах, как того
        требует конвенция Prometheus для имени длительности.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT stage, duration_ms FROM job_stage_durations "
                "WHERE job_id=? AND duration_ms IS NOT NULL ORDER BY started_ts",
                (job_id,),
            ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    def note_stage(self, job_id: str, stage: str, message: str) -> None:
        """Дописать message к уже начатой стадии, не трогая её статус.

        `update_stage` для этого не годится: он помечает предыдущую running-стадию
        как succeeded и новую ставит в running. Вызов после завершения джобы
        вернул бы stage в состояние `running` у уже финальной джобы.
        """
        with self._lock:
            # Перетирание, а не дописывание: закреплено тестом
            # `test_job_signals_are_structural_and_independent_of_message`, где две
            # последовательные пометки дают в итоге последнюю. Докстринг выше утверждал
            # обратное, и на него поведёт ли паспорт извлечения - но спецификация
            # здесь это тест, и он прав. Поэтому паспорт пишется в собственную таблицу,
            # а не конкурирует за `message`: одна ячейка на стадию - один факт.
            self._conn.execute(
                "UPDATE job_stages SET message=? WHERE job_id=? AND stage=?",
                (message, job_id, stage),
            )
            self._conn.commit()

    def record_extraction_passport(self, job_id: str, passport: dict[str, Any]) -> None:
        """Паспорт извлечения: чем выполнялся разбор, кроме текста документа.

        **Единица воспроизведения — документ и его версия, а не джоба** (решение владельца
        2026-09-29). `job_id` уникален, но воспроизводим только при повторе той же джобы,
        чего на практике не делает никто, а при ретрае он меняется; `source_url` плюс версия
        переживают ретрай, и «переизвлечь этот документ и получить то же» работает.

        Значения влияющих параметров приходят из адаптера, а не из профиля: `extraction.*`
        в профиле объявлены, но не читаются, и действующие числа живут в блоке `llm`.
        Записать профильные означало бы записать неправду - это и есть то расхождение
        (0.1 против 0.3), ради которого паспорт и понадобился.
        """
        from datetime import datetime

        def _text(key: str) -> str:
            # Не `or ""`: температура извлечения равна 0.0, а ноль ложен, и пустая строка
            # съедала бы ровно то значение, ради которого паспорт и писался. В базу
            # кладём текст, потому что SQLite хранит числа REAL и потерял бы 0.0 против
            # NULL неразличимо, а «не задано» и «ноль» здесь — разные вещи.
            value = passport.get(key)
            return "" if value is None else str(value)

        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO job_extraction_passport ("
                "job_id, domain, source_url, document_version, profile, llm_enabled, "
                "model, temperature, max_tokens, timeout_s, seed, "
                "instruction_fingerprint, identity, instruction, ts"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    _text("domain"),
                    _text("source_url"),
                    int(passport.get("document_version") or 0),
                    _text("profile"),
                    1 if passport.get("llm_enabled") else 0,
                    _text("model"),
                    _text("temperature"),
                    _text("max_tokens"),
                    _text("timeout_s"),
                    _text("seed"),
                    _text("instruction_fingerprint"),
                    _text("identity"),
                    _text("instruction"),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
            self._conn.commit()

    def extraction_passport(self, job_id: str) -> dict[str, Any] | None:
        """Паспорт джобы, `None` - если извлечение не дошло до LLM-пути.

        Отсутствие паспорта - не ноль: это разные вещи, и путать их нельзя, потому что
        «извлечение не запускалось» и «запускалось с такой-то моделью» требуют разных
        действий.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM job_extraction_passport WHERE job_id=?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        keys = [
            "job_id", "domain", "source_url", "document_version", "profile",
            "llm_enabled", "model", "temperature", "max_tokens", "timeout_s", "seed",
            "instruction_fingerprint", "identity", "instruction", "ts",
        ]
        return dict(zip(keys, row, strict=False))

    def record_orphan_cleanup(
        self,
        job_id: str,
        domain: str,
        *,
        planned_relations: int,
        removed_relations: int,
        planned_nodes: int,
        removed_nodes: int,
        mode: str,
    ) -> None:
        """Факт уборки осиротевших связей и узлов — отдельной таблицей, не текстом.

        Отдельная таблица, а не сигнал в `job_signals`, потому что здесь нужны **числа**,
        а сигнал это имя. Мотив тот же, что у `job_enrichment`: решение строится на числе, а
        число не должно зависеть от разбора сообщения.

        Подсчёт и удаление разведены по времени (между проходами может прийти другой ingest),
        поэтому `planned_*` и `removed_*` хранятся раздельно: расхождение между ними — это
        сигнал о гонке, а не повод его терять.

        Требовалось писать факт в той же транзакции, что и удаление, — иначе сбой между
        коммитами даст правдоподобный ноль, который читается как «удалять нечего».
        **В коде этого нет:** удаление идёт в Neo4j отдельными коммитами, а эта запись — в
        SQLite, то есть ни общей транзакции, ни общей базы. Правило удаления от этого не
        меняется, меняется только доставка факта; решение и фактическая последовательность —
        ADR-014, четвёртая корректирующая запись (пп. 20–23), средство — transactional
        outbox, отложенный на M6-Growth.
        """
        from datetime import datetime

        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO job_orphan_cleanup ("
                "job_id, domain, planned_relations, removed_relations, "
                "planned_nodes, removed_nodes, mode, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    domain,
                    int(planned_relations),
                    int(removed_relations),
                    int(planned_nodes),
                    int(removed_nodes),
                    mode,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
            self._conn.commit()

    def orphan_cleanup(self, job_id: str) -> dict[str, Any] | None:
        """Факт уборки джобы; отсутствие факта — `None`, а не ноль."""
        with self._lock:
            row = self._conn.execute(
                "SELECT domain, planned_relations, removed_relations, planned_nodes, "
                "removed_nodes, mode, ts FROM job_orphan_cleanup WHERE job_id=?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "domain": str(row[0]),
            "planned_relations": int(row[1]),
            "removed_relations": int(row[2]),
            "planned_nodes": int(row[3]),
            "removed_nodes": int(row[4]),
            "mode": str(row[5]),
            "ts": str(row[6]),
        }

    def set_signal(self, job_id: str, stage: str, name: str) -> None:
        """Отметить структурный сигнал джобы (например, потерю LLM-слоя).

        Сигнал — машинное поле, а не текст. Consumer читает его без разбора
        `message`, поэтому формат сообщения можно менять свободно.
        """
        from datetime import datetime

        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO job_signals (job_id, stage, name, ts) "
                "VALUES (?, ?, ?, ?)",
                (job_id, stage, name, datetime.now(UTC).isoformat(timespec="seconds")),
            )
            self._conn.commit()

    def signals(self, job_id: str) -> dict[str, str]:
        """Сигналы джобы как ``{имя: стадия}``; отсутствие сигнала — отсутствие ключа."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, stage FROM job_signals WHERE job_id=? ORDER BY ts",
                (job_id,),
            ).fetchall()
        return {str(name): str(stage) for name, stage in rows}

    def record_enrichment(
        self,
        job_id: str,
        *,
        cause: str | None,
        lost_entities: int,
        lost_edges: int,
        llm_records: int,
        llm_edges: int,
    ) -> None:
        """Записать числовые факты обогащения джобы (одна строка на джобу).

        Пишется и для недеградировавшей джобы: объём LLM-слоя нужен как
        ЗНАМЕНАТЕЛЬ. Считать «записи на документ, где экстракция вообще не
        запускалась» — значит приписать детерминированному fallback то, что сделала
        модель, либо приписать модели то, что сделал fallback.
        """
        from datetime import datetime

        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO job_enrichment (job_id, cause, lost_entities, "
                "lost_edges, llm_records, llm_edges, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    cause,
                    int(lost_entities),
                    int(lost_edges),
                    int(llm_records),
                    int(llm_edges),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
            self._conn.commit()

    def enrichment(self, job_id: str) -> dict[str, Any]:
        """Числовые факты обогащения; ``{}`` — их не было (EXTRACT не дошёл до записи)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT cause, lost_entities, lost_edges, llm_records, llm_edges "
                "FROM job_enrichment WHERE job_id=?",
                (job_id,),
            ).fetchone()
        if row is None:
            return {}
        return {
            "cause": row[0],
            "lost_entities": int(row[1]),
            "lost_edges": int(row[2]),
            "llm_records": int(row[3]),
            "llm_edges": int(row[4]),
        }

    def stages(self, job_id: str) -> builtins.list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT stage, status, message, ts FROM job_stages "
                "WHERE job_id=? ORDER BY ts",
                (job_id,),
            ).fetchall()
        return [
            {"stage": r[0], "status": r[1], "message": r[2], "ts": r[3]}
            for r in rows
        ]