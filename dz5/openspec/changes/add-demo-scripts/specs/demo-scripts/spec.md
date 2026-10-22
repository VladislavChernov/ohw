# Spec: demo-scripts

## ADDED Requirements

### Requirement: Единый интерфейс

```powershell
.\infra\scripts\demo.ps1 [-Preset all|1..5] [-Runs 3] [-SkipBuild]
                         [-KeepUp] [-AllowWrites] [-NoAttacks]
```

```bash
./infra/scripts/demo.sh [--preset all|1..5] [--runs 3] [--skip-build]
                        [--keep-up] [--allow-writes] [--no-attacks]
```

- `--preset all` — пресеты 1–5; `5` использует `agent-fixed`.
- `--runs` — повторов на сценарий; `<3` допустим только с предупреждением
  и даёт `unknown` в отчёте.
- `--skip-build` — не собирать образы.
- `--keep-up` — не останавливать стенд после прогона.
- `--allow-writes` — включить `ALLOW_WRITES=1` только для контролируемого
  LLM03-сценария; в конце `db-restore`.
- `--no-attacks` — только поднять/проверить стенд.
- `--help` — справка, exit `0`.

### Requirement: Шаги

1. Preflight: `docker`, `docker compose`, файл `infra/compose.yaml`.
2. `docker compose build` (если не `--skip-build`).
3. `docker compose up -d ollama db agent agent-fixed wall`.
4. `docker compose --profile tools run --rm db-seed`.
5. Health: `agent:8080/healthz`, `agent-fixed:8081/healthz`,
   `wall:8000/health-check`; ждать с таймаутом (модель может грузиться долго).
6. Smoke: легитимный `/chat` в preset 1 и 5 (если выбран).
7. Attack: `attack-runner` по выбранным пресетам, `--out reports/<run-id>`.
8. Report: `report_builder build --run reports/<run-id>`.
9. Итог: путь к `reports/<run-id>/summary.md` и `reports/latest.md`.

### Requirement: Ошибки и очистка

- Любой шаг с ошибкой → сообщение, `demo.log`, exit `1`.
- `trap`/`finally`:
  - `db-restore`, если БД менялась;
  - `docker compose down` без `--volumes`, если не `--keep-up`;
  - не трогать чужие compose-проекты.
- Прерывание Ctrl+C → очистка и exit `130`.

### Requirement: Логи

- `reports/<run-id>/demo.log` — команды, время, статусы, без секретов.
- Вывод в консоль краткий; полный — в лог.
- `run-id` один для всех шагов прогона; передаётся в раннер/отчёт.

### Requirement: Кроссплатформенность

- PowerShell 5.1 (Windows) и bash (Linux/WSL).
- Одинаковые флаги/шаги/exit codes.
- Пути только через `Join-Path`/кавычки; поддержка пробелов в пути.
- Не использовать `&&` в PowerShell; проверять `$LASTEXITCODE`.

### Requirement: Безопасность

- `ALLOW_WRITES` только по флагу; после — восстановление.
- Не выводить содержимое `.env` и токены.
- Не отправлять данные во внешние URL.

## Definition of done

- [ ] `demo.ps1` и `demo.sh` реализованы.
- [ ] Полный прогон на чистом стенде.
- [ ] `--preset 1`, `--preset 5`, `--no-attacks` проверены.
- [ ] `--keep-up` и очистка работают.
- [ ] Ошибка даёт ненулевой exit code.
- [ ] README обновлён.
