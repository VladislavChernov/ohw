# Tasks: add-demo-scripts

## 1. Планирование

- [x] Описать шаги демонстрации и флаги.
- [x] Зафиксировать очистку, exit codes и логи.

## 2. PowerShell

- [ ] `infra/scripts/demo.ps1`: параметры, `--help`.
- [ ] Preflight: docker/compose/файлы.
- [ ] Build/up/seed/health с таймаутом.
- [ ] Smoke и запуск `attack-runner`.
- [ ] `report_builder build`.
- [ ] `try/finally`: restore DB, down без volumes.
- [ ] Ctrl+C → cleanup, exit `130`.
- [ ] Проверка `$LASTEXITCODE` после каждой внешней команды.
- [ ] Пути с пробелами, без `&&`.

## 3. Bash

- [ ] `infra/scripts/demo.sh`: те же флаги/шаги.
- [ ] `set -euo pipefail`; `trap cleanup EXIT INT TERM`.
- [ ] Проверка на Linux/WSL.

## 4. Проверка

- [ ] `demo.ps1 --no-attacks` — стенд поднят, health зелёный.
- [ ] `demo.ps1 --preset 1 --runs 3` — отчёт создан.
- [ ] `demo.ps1 --preset 5 --runs 3` — preset 5 прогнан.
- [ ] `--keep-up` оставляет контейнеры; без флага — останавливает.
- [ ] Ошибка (например, занятый порт) → exit `1` и понятное сообщение.
- [ ] `demo.sh` — те же проверки (хотя бы `--no-attacks`).

## 5. Завершение

- [ ] README: быстрый старт через demo-скрипты.
- [ ] Логи без секретов.
- [ ] Коммит только `dz5/` после явной просьбы.
