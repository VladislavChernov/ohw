# Задачи: PlanCodec — реестр версионированных декодеров JSON-плана

Порядок: сначала нейтральный рефакторинг с сохранением поведения, затем
реестр и точки подключения, затем тесты и верификация. Формат ответа LLM
(v3) **не меняем** — только выделяем кодовый слой под него.

## 1. Абстракция PlanCodec

- [x] 1.1 Определить `PlanCodec` (Protocol) в `plan.py` (или вынести в
      `codec.py`): `version: str`, `schema_file: Path`, `matches(raw) -> bool`,
      `decode(raw, *, doc_mode) -> TestPlan`, `validate(raw) -> ValidationResult`.
- [x] 1.2 Добавить `DEFAULT_VERSION` как единственный источник версии по
      умолчанию для `schema_version()` и подстановки `{schema_version}`.
- [x] 1.3 Упомянуть в docstring-ах, что `TestPlan` — единая внутренняя модель
      для всех версий, а codec — только переводчик «формат → модель».

## 2. Первый кодек PlanCodecV3 (паритет без регрессий)

- [x] 2.1 Реализовать `PlanCodecV3`: `version="v3"`,
      `schema_file=../plan_schema/v3.json`.
- [x] 2.2 `matches(raw)` — детект v3: наличие `service`/`tests` (+ плоская v1
      сигнатура `request`/`expect` на тесте, по аналогии с текущим парсером).
- [x] 2.3 `decode(raw, *, doc_mode)` — перенести ровно текущую логику
      `TestPlan.from_dict` (+ `TestSpec/StepSpec/RequestSpec/ExpectSpec.from_dict`
      и нормализацию плоской v1) без изменения поведения.
- [x] 2.4 `validate(raw)` — перенести текущую `validate_plan_schema` (структура
      `service`/`tests`/`name`/`steps` или плоской `request`).
- [x] 2.5 Убедиться, что существующие юнит-тесты plan-модуля проходят без правок
      (поведение эквивалентно).

## 3. Реестр и точки подключения

- [x] 3.1 `get_codec(version: str | None, raw: object) -> PlanCodec`:
      явная `version` → кодек по реестру; иначе `DEFAULT_VERSION`;
      неизвестная версия → `ValueError` (без тихого fallback).
- [x] 3.2 Внутренний регистр версий `{DEFAULT_VERSION: PlanCodecV3()}` +
      функция регистрации дополнительных кодеков (задел под будущие версии).
- [x] 3.3 Заменить в `load_plan` прямой `TestPlan.from_dict` на
      `get_codec(None, raw).decode(raw)`.
- [x] 3.4 Заменить `validate_plan_schema(raw)` на `get_codec(None, raw).validate(raw)`
      (или оставить точечную обёртку с тем же именем, делегирующую в codec),
      сохранив публичные имена для `cli.py`/`generator.py`.
- [x] 3.5 `schema_version()` возвращает `DEFAULT_VERSION`; убедиться, что
      `{schema_version}` в промпте и шапке отчёта не изменился (`v3`).

## 4. Тесты

- [x] 4.1 Тест: `matches` для v3-плана и для плоской v1-формы → `True`;
      для заведомо «другой» структуры — `False`.
- [x] 4.2 Тест: `get_codec("v3", ...)` и `get_codec(None, v3-план)` возвращают
      кодек с `version=="v3"`; `/decode` даёт `TestPlan` с ожидаемыми шагами.
- [x] 4.3 Тест: `get_codec("v99", ...)` бросает `ValueError`.
- [x] 4.4 Паритет: `PlanCodecV3.decode(x)` даёт тот же `TestPlan`, что прямой
      старый парсинг (дублирующий сниппет на основе былой логики или фикстура).

## 5. Верификация

- [x] 5.1 `uv run pytest -q` (advanced) — зелёные (старые + новые).
- [x] 5.2 `uv run ruff check src tests` — чисто.
- [x] 5.3 `uv run mypy src` — чисто.
