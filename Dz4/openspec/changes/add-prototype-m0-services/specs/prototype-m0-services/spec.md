# Delta Spec: prototype-m0-services (Config/Glossary + compose)

## ADDED Requirements

### Requirement: Автономность контура

Прототип ДОЛЖЕН запускаться полностью самостоятельно: собственная docker-сеть
`ohw_net` (bridge), собственные volumes, без зависимостей от общего `D:\Otus\infra`,
shared ollama (`ohw-ollama`) и без использования пакета `ohw-kit`.

#### Scenario: Старт compose с профилями config и graph

- **WHEN** выполнен `docker compose -f prototype/infra/compose.yaml
  --profile config --profile graph up -d`
- **THEN** контейнеры `config-service`, `glossary-service` и Neo4j стартуют без
  внешних сетей и не требуют доступа к `D:\Otus\infra` / shared ollama

### Requirement: Config Service — список профилей

Config Service ДОЛЖЕН отдавать перечень доступных Domain-профилей из каталога
`prototype/domain_profiles/` (файлы `domain_profile.{domain}.yaml`).

#### Scenario: Получение списка доменов

- **WHEN** `GET /api/v1/config/domain/profiles` на :8001
- **THEN** ответ содержит `it`, `library`, `cinema`

### Requirement: Config Service — валидация и активация профиля

Config Service ДОЛЖЕН валидировать структуру YAML-профиля
(`POST /api/v1/config/domain/validate`) и активировать его
(`POST /api/v1/config/domain/activate`), сохраняя активный профиль
в локальном хранилище (SQLite). Активация НЕ должна «пушить» события наружу:
потребители (Glossary Service) сами запрашивают активный домен (pull-модель).
Новая загрузка профиля (`POST /api/v1/config/domain/profile`) выходит за скоуп M0
(файлы читаются с read-only volume) и реализуется на фазе конфигуратора (M5+).

#### Scenario: Валидация корректного профиля

- **WHEN** отправлен `domain_profile.it.yaml` корректной структуры
- **THEN** эндпоинт возвращает успех; профиль может быть активирован

#### Scenario: Ошибки валидации

- **WHEN** запрос содержит битый/не-маппинговый YAML
- **THEN** возвращается `400`
- И **WHEN** профиль валиден по синтаксису, но нарушает структуру онтологии
  (например, edge_types ссылаются на несуществующие node_types)
- **THEN** возвращается `200` с `{"valid": false, "errors": [...]}` и агрегированными
  ошибками в теле ответа

#### Scenario: Активация домена

- **WHEN** вызывается `POST /api/v1/config/domain/activate` с `{"domain": "it"}`
- **THEN** активный профиль становится `it` и сохраняется в SQLite;
  Glossary Service узнает о нём запросом `GET /api/v1/config/domain/active`

#### Scenario: Активация профиля с несовпадающим name

- **WHEN** `profile.name` в YAML отличается от `domain` в запросе на активацию
- **THEN** возвращается `422` с явным описанием конфликта

### Requirement: Config Service — дефолты runtime namespace

Config Service ДОЛЖЕН загружать дефолты из `prototype/infra/config/namespaces.yaml`
(SSOT `docs/04` §5) и использовать их как значения по умолчанию для runtime config,
включая активный профиль по умолчанию.

#### Scenario: Значение по умолчанию

- **WHEN** namespace не переопределён в рантайме
- **THEN** используется значение из `namespaces.yaml`

#### Scenario: Активный профиль по умолчанию

- **WHEN** `namespaces.yaml` задаёт `domain.active_profile`
- **THEN** `GET /api/v1/config/domain/active` без предшествующей активации
  возвращает этот профиль

### Requirement: Glossary Service — получение словаря

Glossary Service ДОЛЖЕН отдавать содержимое словаря для домена
(`GET /api/v1/glossary/{domain}`), подгружая `glossary.{domain}.yaml`
из `prototype/domain_profiles/`.

#### Scenario: Словарь для активного домена доступен

- **WHEN** активен домен `it`, запрошен `GET /api/v1/glossary/it`
- **THEN** ответ содержит термины глоссария (terms, data_types, complexity_aliases,
  unicode_map, function_synonyms по `docs/04` §4)

#### Scenario: Домен без словаря

- **WHEN** запрошен глоссарий для несуществующего/незагруженного домена
- **THEN** сервис возвращает ошибку «словарь не найден» (404)

### Requirement: Glossary Service — RESOLVE тега

Glossary Service ДОЛЖЕН переводить тег в канонический ряд
(`POST /api/v1/glossary/resolve`): синонимы/варианты записи → `canonical_name`
c набором `variants`. Домен выбирается полем `domain` в теле запроса, а если оно
не указано — активным доменом из Config Service (pull):
`GET /api/v1/config/domain/active` (fallback — `it`). Контракт соответствуют
`docs/05_adr_log.md` ADR-018.

#### Scenario: Тег найден

- **WHEN** на `resolve` отправлен тег-синоним из `glossary.it.yaml`
- **THEN** возвращается `{"canonical_name": "...", "variants": [...]}`
  для соответствующего термина

#### Scenario: Активный домен без указания domain

- **WHEN** `domain` не указан в теле запроса
- **THEN** Glossary Service запрашивает активный домен у Config Service
  (`GET /api/v1/config/domain/active`) и работает со словарём этого домена

#### Scenario: Тег ненайден

- **WHEN** тег отсутствует в словаре
- **THEN** возвращается ответ `{"canonical_name": null, "variants": []}` (без паники)

### Requirement: Glossary Service — VALIDATE уникальности

Glossary Service ДОЛЖЕН проверять, что в рамках домена один вариант записи не
ведёт к двум каноническим терминам (и наоборот, пересечения словарей).
Эндпоинт `POST /api/v1/glossary/validate` (без домена в пути; домен — в теле).

#### Scenario: Дубликат тега в словаре

- **WHEN** в словаре один тег мапится на два канонических термина
- **THEN** `validate` возвращает ошибку с указанием конфликта

### Requirement: Neo4j профиль graph

В профиль `graph` ДОЛЖЕН входить Neo4j Community с ограничениями JVM
(max heap 1G, initial heap 512M, pagecache 512M) и volume для данных.

#### Scenario: Neo4j поднят в graph

- **WHEN** compose-профиль `graph` активирован
- **THEN** Neo4j доступен на 7687 (Bolt) / 7474 (HTTP) в сети `ohw_net`

### Requirement: Toolchain (dev-container/ВМ)

Проект ДОЛЖЕН поддерживать запуск `uv run pytest`, `uv run ruff check`,
`uv run mypy` в dev-container (VS Code) или на ВМ; локально (на хосте) Python
отсутствует.

#### Scenario: Проверки в dev-container

- **WHEN** проект открыт в dev-container и выполнены три команды
- **THEN** тесты зелёные, ruff чисто, mypy без ошибок