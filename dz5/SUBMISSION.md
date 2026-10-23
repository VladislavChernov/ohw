# ДЗ5: краткая инструкция для проверяющего

Все команды выполняются из каталога `dz5`.

## 1. Что нужно

- Docker Desktop/Engine с Docker Compose.
- GPU-вариант: NVIDIA GPU и поддержка NVIDIA в Docker.
- CPU-вариант: GPU не нужен, но модель считает на процессоре — медленно.
- Первый запуск может быть долгим: скачивается модель
  `huihui_ai/llama3.2-abliterate:3b`.
- Windows: PowerShell 5.1+; Linux/macOS/WSL/Git Bash: `sh`.

## 2. Поднять стенд

Windows:

```powershell
cd D:\Otus\ohw\dz5
.\seed-db.ps1
```

GPU-вариант:

```powershell
docker compose -f infra/compose.yaml -f infra/compose.gpu.yaml up -d --build ollama agent agent-fixed wall ui db-admin
```

CPU-вариант:

```powershell
docker compose -f infra/compose.yaml up -d --build ollama agent agent-fixed wall ui db-admin
```

POSIX: то же самое, только БД засевается через `sh seed-db.sh`. GPU-команда
добавляет `-f infra/compose.gpu.yaml`, CPU-команда — нет.

`seed-db` создаёт SQLite-БД и синтетических пользователей. Стенд состоит из:

- `agent` — baseline-агент, пресеты 1–4, `http://localhost:8080`;
- `agent-fixed` — защищённый агент, пресет 5, `http://localhost:8081`;
- `wall` — Guardrails Server, `http://localhost:8000`;
- `ui` — веб-интерфейс, `http://localhost:8090`;
- `ollama` — локальная LLM;
- `db-admin` — внутренний сервис восстановления БД (наружу не публикуется).

Проверка:

```powershell
Invoke-RestMethod http://localhost:8080/healthz
Invoke-RestMethod http://localhost:8081/healthz
Invoke-RestMethod http://localhost:8000/health-check
Invoke-RestMethod http://localhost:8090/healthz
```

Открыть UI: `http://localhost:8090/`.

## 3. Как атаковать

Есть два готовых варианта: `run-red-team-gpu.*` для NVIDIA и
`run-red-team-cpu.*` для CPU. Оба поднимают стенд, выставляют guard-флаги под
выбранный пресет, запускают 18 OWASP-сценариев и собирают отчёты.

Быстрый smoke-прогон на GPU:

```powershell
.\run-red-team-gpu.ps1 -Preset 1 -Runs 1
```

```bash
sh run-red-team-gpu.sh --preset 1 --runs 1
```

Быстрый smoke-прогон на CPU:

```powershell
.\run-red-team-cpu.ps1 -Preset 1 -Runs 1
```

```bash
sh run-red-team-cpu.sh --preset 1 --runs 1
```

Полная матрица P1–P5 (по 3 прогона на сценарий, всего 270 прогонов).
GPU-вариант:

```powershell
1..5 | ForEach-Object { .\run-red-team-gpu.ps1 -Preset $_ -Runs 3 }
```

CPU-вариант (может идти очень долго):

```powershell
1..5 | ForEach-Object { .\run-red-team-cpu.ps1 -Preset $_ -Runs 3 }
```

POSIX-аналоги: `sh run-red-team-gpu.sh ...` и `sh run-red-team-cpu.sh ...`.

Один сценарий (для CPU замените `gpu` на `cpu`):

```powershell
.\run-red-team-gpu.ps1 -Preset 1 -Runs 1 -Scenario llm01-ignore-previous
```

```bash
sh run-red-team-gpu.sh --preset 1 --runs 1 --scenario llm01-ignore-previous
```

Пресеты:

| Preset | Что проверяется |
|---|---|
| P1 | baseline, без защит |
| P2 | встроенные защиты агента |
| P3 | внешний Guardrails Server |
| P4 | встроенные + внешние защиты |
| P5 | отдельный `agent-fixed` |

P1–P4 атакуют `agent`, P5 — `agent-fixed`; launcher выбирает target сам.
Один запуск launcher'а — **один preset**. Для полной матрицы нужны все пять.

По умолчанию `ALLOW_WRITES=off`, поэтому destructive-сценарии LLM03 проверяют
блокировку, но не реально меняют БД. Чтобы разрешить записи:

```powershell
$env:ALLOW_WRITES = 'on'
.\run-red-team-gpu.ps1 -Preset 1 -Runs 3
Remove-Item Env:ALLOW_WRITES
```

После таких прогонов восстановить БД: кнопка **Restore DB** в карточке
**Database** в UI или `.\seed-db.ps1 -Restore` / `sh seed-db.sh --restore`.

Augustus запускается дополнительно (для CPU замените `gpu` на `cpu`):

```powershell
.\run-red-team-gpu.ps1 -Preset 5 -Runs 3 -Augustus
```

```bash
sh run-red-team-gpu.sh --preset 5 --runs 3 --augustus
```

Augustus в этом стенде — дополнительный слой из 3 probes; основная матрица —
18 сценариев `attack-runner`.

## 4. Где смотреть результат

После прогона:

- `reports/<run-id>/summary.md` — итог по сценариям;
- `reports/<run-id>/summary.json` — машинный итог;
- `reports/<run-id>/report.html` — HTML-отчёт;
- `reports/<run-id>/augustus/report.html` — отчёт Augustus, если он включён;
- `reports/latest.md` — последний полный прогон;
- `reports/red-team.md` — сводка по всем прогонам.

В UI также доступна вкладка Augustus: `http://localhost:8090/augustus`.

Важно: при `--runs 1` или `--runs 2` вердикт по сценарию будет `unknown`:
для доказанного bypass нужно минимум 3 прогона (`--runs 3`).

## 5. Скрипты

| Скрипт | Назначение |
|---|---|
| `run-red-team-gpu.ps1` / `.sh` | GPU-вариант red-team-прогона |
| `run-red-team-cpu.ps1` / `.sh` | CPU-вариант red-team-прогона |
| `run-red-team.ps1` / `.sh` | базовый launcher; GPU по умолчанию, `-Cpu`/`--cpu` — CPU |
| `seed-db.ps1` | засевка БД; `-Restore` — восстановление |
| `seed-db.sh` | засевка БД; `--restore` — восстановление |

Основные опции launcher'а:

- `-Preset` / `--preset` — 1–5;
- `-Runs` / `--runs` — число прогонов на сценарий;
- `-Scenario` / `--scenario` — один или несколько id сценариев;
- `-Augustus` / `--augustus` — добавить Augustus;
- `-Cpu` / `--cpu` — запустить Ollama на CPU;
- `-NoUp` / `--no-up` — не поднимать стенд;
- `-NoBuild` / `--no-build` — не пересобирать attack-runner;
- `-NoReport` / `--no-report` — не собирать отчёты.

## 6. Остановить

```powershell
docker compose -f infra/compose.yaml down
```

Для GPU-варианта добавьте `-f infra/compose.gpu.yaml`. С сохранением БД и
модели. Полная очистка данных:

```powershell
docker compose -f infra/compose.yaml down -v
```

## 7. Минимальный сценарий проверки

1. `.\seed-db.ps1`
2. Поднять стенд GPU- или CPU-командой из раздела 2.
3. Открыть `http://localhost:8090/`, проверить UI и кнопку **Restore DB**.
4. `.\run-red-team-gpu.ps1 -Preset 1 -Runs 3` (для CPU — `run-red-team-cpu.ps1`).
5. Открыть `reports/latest.md` и `reports/red-team.md`.
6. Для проверки fixed-агента: `.\run-red-team-gpu.ps1 -Preset 5 -Runs 3`.
