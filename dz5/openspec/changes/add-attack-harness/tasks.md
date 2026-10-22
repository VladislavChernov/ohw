# Tasks: add-attack-harness

## 1. Планирование

- [x] Зафиксировать отсутствие раннера и недостоверность старого red-team.
- [x] Определить 16 сценариев и вердикты (`blocked`/`bypass`/`mixed`/
      `unknown`/`error`).
- [x] Определить manifest и структуру результатов.

## 2. Каркас раннера

- [ ] `attack-runner/pyproject.toml` / `requirements.txt` (httpx, pyyaml,
      pytest; версии проверить).
- [ ] `attack_runner/__main__.py` — CLI (`run`, аргументы, exit codes).
- [ ] `attack_runner/runner.py` — цикл сценарий × повтор.
- [ ] `attack_runner/evaluators.py` — leak/tool/DB-проверки.
- [ ] `attack_runner/manifest.py` — git commit, образы, модель, env.
- [ ] `attack_runner/report.py` — `results.jsonl`, `summary.json`, details.
- [ ] `attack-runner/Dockerfile` — Python-образ, non-root.

## 3. Сценарии

- [ ] 16 YAML-сценариев (LLM01–LLM10), ids стабильны.
- [ ] Для destructive — `setup`/`teardown` и `db-restore`.
- [ ] Валидация схемы сценариев (pydantic/dataclass).
- [ ] Проверить, что все 10 категорий покрыты.

## 4. Выполнение

- [ ] HTTP-клиент с таймаутом и ограничением размера ответа.
- [ ] `POST /chat`, разбор `reply`/`tool_calls`/`blocked`.
- [ ] `n>=3`; `<3` → `unknown` + предупреждение.
- [ ] DB restore перед destructive и в `finally`.
- [ ] Сбор latency; `docker stats` при доступности.
- [ ] Ошибки инфраструктуры → `error`, не `blocked`.

## 5. Augustus

- [ ] Проверить актуальный CLI/версию Augustus.
- [ ] Привести `scripts/augustus/augustus.config.yaml` к рабочему виду.
- [ ] `--augustus=on`: запуск, сохранение вывода, `skipped` при отсутствии.
- [ ] Зафиксировать версию в manifest.

## 6. Compose

- [ ] Сервис `attack-runner` (профиль `attack`), volume `reports/`.
- [ ] `depends_on` agent/agent-fixed/wall по условию healthy.
- [ ] Env `TARGET`, `PRESET`, `RUNS`, `AUGUSTUS`.
- [ ] Не публиковать наружу; сеть `ib-network`.

## 7. Проверка

- [ ] Smoke: preset 1, один сценарий, `n=3`.
- [ ] Smoke: preset 5, один сценарий, `n=3`.
- [ ] Полный прогон preset 1–5 (или зафиксировать, какие не готовы).
- [ ] Проверить `mixed` на нестабильном сценарии (если воспроизводится).
- [ ] Проверить `error` при остановленном агенте.
- [ ] Проверить восстановление БД после `DROP`.

## 8. Завершение

- [ ] Результаты в `reports/<run-id>/`; `manifest.json` полный.
- [ ] README: команда запуска, ограничения, `n>=3`.
- [ ] Коммит только `dz5/` после явной просьбы.
