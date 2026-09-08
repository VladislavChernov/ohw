"""Document Registry (SQLite): идемпотентность INGEST, версии, soft-delete.

ADR-014: идентичность источника — (domain, source_url) в пределах активного профиля;
повторная загрузка того же content_hash — no-op; изменение контента — новая версия,
старая помечается superseded; удаление — soft delete.
"""

from __future__ import annotations

import builtins
import sqlite3
import threading
from datetime import UTC
from pathlib import Path
from typing import Any

from graphrag_proto.ingestion_service.document import Document

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_DELETED = "deleted"


class DocumentRegistry:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
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


class JobStore:
    """SQLite-журнал джоб ingestion: статус, текущий этап, сообщение об ошибке."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
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

    def update_stage(self, job_id: str, stage: str, message: str = "") -> None:
        from datetime import datetime

        with self._lock:
            now = datetime.now(UTC).isoformat(timespec="seconds")
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
            self._conn.execute(
                "UPDATE jobs SET status='running', stage=?, updated_at=? WHERE job_id=?",
                (stage, now, job_id),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO job_stages (job_id, stage, status, message, ts) "
                "VALUES (?, ?, 'running', ?, ?)",
                (job_id, stage, message, now),
            )
            self._conn.commit()

    def finish(self, job_id: str, status: str, error: str | None = None) -> None:
        from datetime import datetime

        with self._lock:
            now = datetime.now(UTC).isoformat(timespec="seconds")
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