# Tasks: add-db-service

## 1. Образ и каркас сервиса БД

- [x] `infra/db/Dockerfile`: образ CLI SQLite (`keinos/sqlite3:3.53.4`, точный
      тег); `/data` подготовлен и отдан пользователю `sqlite` (uid 100),
      entrypoint — `/bin/sh`, скрипты вызываются как `sh /db/<script>`.
- [x] Сервисы `db`, `db-seed`, `db-restore`, `db-query` и volume `db_data` в
      `infra/compose.yaml` (порт не публикуется — БД только внутри сети стенда).

## 2. Схема и seed (с ПДн-мишенями)

- [x] `infra/db/init.sql`: таблицы `users(id, username, role, email,
      password_hash, api_token, created_at)` и
      `personal_data(id, user_id, full_name, birth_date, passport, inn, snils,
      phone, address, card_last4, note)`.
- [x] `infra/db/seed.sql` + `seed.sh`: 5 пользователей (роли admin/user/auditor)
      с `password_hash` и `api_token`; синтетические ПДн (ФИО, паспорт, ИНН,
      СНИЛС, телефон, адрес, card_last4) — мишени для LLM02/LLM03.
- [x] `infra/db/restore.sh`: идемпотентный сброс к seed-состоянию.

## 3. Проверка сервиса БД (прогнано вживую)

- [x] `db-seed` наполняет таблицы (users=5, personal_data=5).
- [x] `db-query` читает `users` и `personal_data` (ПДн присутствуют, UTF-8 корректен).
- [x] Tool-abuse симуляция: `DROP TABLE users` проходит (уязвимость воспроизводима).
- [x] `db-restore` возвращает состояние (users=5 после DROP); повторный restore
      идемпотентен.

## 4. Read-only контракт (со стороны агента)

- [ ] Read-only режим `db_query` по умолчанию (allow-list `SELECT`) и режим
      tool-abuse за флагом — реализуется в бандле `add-agent-core`
      (контракт зафиксирован в `specs/db-service/spec.md`).

## 5. Отметка выполнения

- [x] Все проверки из §3 зелёные; галочки проставлены по факту.

