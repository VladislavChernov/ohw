"""Сверка эталонных кандидатов с корпусом тем же кодом, что и пайплайн.

Считать sha256 вручную по сырому файлу нельзя: генератор берёт
`path.read_text(encoding="utf-8")`, а Python нормализует CRLF в LF, поэтому хеш
совершенно правильных файлов не сойдётся с попыткой посчитать его снаружи.
"""

import json
import sys
from pathlib import Path

from graphrag_proto.pre_ingest.generate import sha256_of

DOCS = Path("/data/src")
CANDIDATES = Path("/data/dst")

docs = sorted(DOCS.glob("*.md"))
stale = []
missing = []
total = 0

# Без этой проверки прибор, не нашедший ни одного документа, рапортовал бы
# «согласовано» - и это выглядело бы как успешная сверка. Пустой вход и
# отсутствие расхождений - разные результаты.
if not docs:
    print("НУЛЕВОЙ ЗНАМЕНАТЕЛЬ: в папке источника нет ни одного .md")
    print("Сверка не выполнялась. Это 'не измерено', а не 'расхождений нет'.")
    sys.exit(2)

for doc in docs:
    total += 1
    path = CANDIDATES / f"{doc.name}_candidates.json"
    if not path.exists():
        missing.append(doc.name)
        continue
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("source_sha256") != sha256_of(doc.read_text(encoding="utf-8")):
        stale.append(doc.name)

extra = sorted(
    p.name for p in CANDIDATES.glob("*_candidates.json")
    if not (DOCS / p.name.replace("_candidates.json", "")).exists()
)

print(f"документов        : {total}")
print(f"нет файла кандидатов: {len(missing)}")
for name in missing:
    print(f"    {name}")
print(f"устаревших по sha256: {len(stale)}")
for name in stale:
    print(f"    {name}")
print(f"лишних файлов кандидатов: {len(extra)}")
for name in extra:
    print(f"    {name}")

print()
print("ВЕРДИКТ:", "согласовано" if not stale and not missing and not extra else "ЕСТЬ РАСХОЖДЕНИЯ")
sys.exit(0 if not stale and not missing and not extra else 1)