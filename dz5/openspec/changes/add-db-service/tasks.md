# Tasks: add-db-service

## 1. Образ и каркас сервиса БД

- [ ] `infra/db/Dockerfile`: образ CLI SQLite (`keinos/sqlite3`, точный тег),
      entrypoint для init/seed/restore.
- [ ] Сервис `db` и volume `db_data` в `infra/compose.yaml` (без публикации
      порта — БД доступна только внутри сети стенда).

## 2. Схема и seed

- [ ] `infra/db/init.sql`: таблица `users(id, username, role, email, password_hash)`.
- [ ] `infra/db/seed.sh`: стартовые строки (`admin`, `user`, «чужие» строки).
- [ ] `infra/db/restore.sh`: идемпотентный сброс к seed-состоянию.

## 3. Проверка сервиса БД

- [ ] `docker compose run --rm db-seed` наполняет таблицу.
- [ ] `docker compose run --rm db-restore` восстанавливает состояние.
- [ ] Ручная проверка: `SELECT` возвращает ожидаемые строки; повторный restore
      идемпотентен.

## 4. Read-only контракт (со стороны БД/инструмента)

- [ ] Read-only режим `db_query` по умолчанию (allow-list `SELECT`).
- [ ] Режим tool-abuse (модификации разрешены) — вынесен за флаг, для
      демонстрации уязвимости (детали интеграции — в `add-agent-core`).

## 5. Отметка выполнения

- [ ] Все проверки из §3 зелёные; галочки проставлены по факту.
