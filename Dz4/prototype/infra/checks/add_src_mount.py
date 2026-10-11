"""Добавить монтирование исходников во все сервисы нашего кода.

Две ошибки первой версии, обе закрыты здесь:

1. Правило требовало наличия `volumes:`. У `demo-ui` и `configurator-ui` такого блока
   НЕТ, поэтому они молча пропускались - класс «сервис без volumes» остался бы без тома.
2. Разбор блоков ловил и `  query_data:` внутри верхнеуровневого `volumes:`, и том
   попадал в объявление томов, ломая YAML.

Признак своего кода - Dockerfile проекта. Внешние инструменты исключены явно:
`Dockerfile.llm` и `Dockerfile.reranker` - это llama.cpp и модель, по решению владельца
они остаются запечёнными.

Сервисы определяются ТОЛЬКО внутри секции `services:` - обход останавливается на первом
верхнеуровневом ключе.
"""

import re
import sys
from pathlib import Path

INFRA = Path("/repo/prototype/infra")
MOUNT = "      - ../src:/app/src:ro\n"
VOLUMES_KEY = "    volumes:\n"

OURS = ("dockerfile: Dockerfile\n", "dockerfile: Dockerfile.embeddings\n")
EXTERNAL = ("dockerfile: Dockerfile.llm\n", "dockerfile: Dockerfile.reranker\n")

FILES = (
    "compose.yaml",
    "compose.eval-minimal.yaml",
    "compose.eval.yaml",
    "compose.eval-qwen3b.yaml",
    "compose.runtime.yaml",
    "compose.preingest.yaml",
)

SERVICE = re.compile(r"^  ([a-z][a-z0-9-]*):\s*$")


def main() -> int:
    for filename in FILES:
        path = INFRA / filename
        if not path.exists():
            print(f"{filename}: НЕТ ФАЙЛА")
            continue

        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

        # Границы секции services:.
        services_at = next((i for i, line in enumerate(lines) if line.startswith("services:")), None)
        if services_at is None:
            print(f"{filename}: нет секции services")
            continue
        end = next(
            (i for i in range(services_at + 1, len(lines)) if re.match(r"^[a-z]", lines[i])),
            len(lines),
        )

        bounds: list[tuple[str, int, int]] = []
        for index in range(services_at + 1, end):
            match = SERVICE.match(lines[index])
            if match:
                bounds.append((match.group(1), index, 0))
        for position, (name, begin, _) in enumerate(bounds):
            stop = bounds[position + 1][1] if position + 1 < len(bounds) else end
            bounds[position] = (name, begin, stop)

        inserts: list[tuple[int, str]] = []
        touched: list[str] = []
        for name, begin, stop in bounds:
            body = "".join(lines[begin:stop])
            if any(token in body for token in EXTERNAL):
                continue
            if not any(token in body for token in OURS):
                continue
            if "/app/src" in body:
                continue
            volumes_at = next(
                (i for i in range(begin, stop) if lines[i].strip() == "volumes:"), None
            )
            if volumes_at is None:
                inserts.append((stop, VOLUMES_KEY + MOUNT))
            else:
                inserts.append((volumes_at + 1, MOUNT))
            touched.append(name)

        for at, payload in sorted(inserts, key=lambda item: -item[0]):
            lines.insert(at, payload)

        path.write_text("".join(lines), encoding="utf-8")
        print(f"{filename}: смонтировано сервисов - {len(touched)} {touched if touched else ''}")

    return 0


if __name__ == "__main__":
    sys.exit(main())