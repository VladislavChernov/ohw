"""Small shared helpers: UTC timestamps, run ids, atomic file writes."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(moment: datetime | None = None) -> str:
    value = moment or utc_now()
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_run_id(moment: datetime | None = None) -> str:
    value = moment or utc_now()
    stamp = value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_json_atomic(path: Path, payload: Any) -> None:
    write_text_atomic(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
    )
