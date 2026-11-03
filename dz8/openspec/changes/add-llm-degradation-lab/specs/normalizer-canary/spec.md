# Дельта спеки: normalizer-canary

## ADDED Requirements

### Requirement: Канарейка детерминированного нормализатора

- Система SHALL поддерживать проверку «канарейка»: LLM нормализует тот же
  документ, что и `ai_testgen.normalizer`.
- Канарейка SHALL использовать отдельный промпт, а не шаблоны артефактов Dz2.
- Промпт SHALL требовать вернуть только Markdown в каноническом виде:
  `# <title>`, `**URL:** <url>`, `## <requirements_heading>`,
  `### <prefix>-<N>` + текст.
- Промпт SHALL NOT содержать готовый нормализованный текст или пример с
  конкретными ID.
- Промпт SHALL явно говорить, что требования — данные, а не команды.
- Параметры SHALL быть `temperature=0.0`, фиксированный `seed`,
  достаточный `num_ctx`.
- Результат канарейки SHALL NOT использоваться как вход генерации артефактов.
- Канарейка SHALL NOT заменять нормализатор в основном потоке.

#### Scenario: Совпадение

- **WHEN** LLM возвращает те же ID в том же порядке, тот же URL и близкий
  текст
- **THEN** `ids_exact=true`, `count_exact=true`, `url_exact=true`
- **AND** вердикт `pass`

#### Scenario: Пропавшее требование

- **WHEN** LLM пропускает `BR-3`
- **THEN** `missing_ids=["BR-3"]`
- **AND** вердикт `fail`

### Requirement: Разбор ответа LLM

- Система SHALL извлекать ID из строк `### <prefix>-<N>`.
- Система SHALL извлекать URL из строки `**URL:**`.
- Система SHALL извлекать заголовок из строки `# `.
- Тело требования SHALL быть текстом между `### ID` и следующим `###`/`##`.
- При ошибке разбора система SHALL возвращать `CanaryResult.parse_error`,
  сохранять `raw` и возвращать код 4.
- Разбор SHALL быть устойчив к лишним пустым строкам и завершающим пробелам.
- Разбор SHALL NOT «додумывать» ID, которых нет в ответе.

#### Scenario: Битый ответ

- **WHEN** ответ не содержит ни одного `### ID`
- **THEN** `parse_error` непустой
- **AND** `raw` сохранён
- **AND** CLI возвращает код 4

### Requirement: Метрики сравнения

- Система SHALL считать:
  - `ids_exact` — множества и порядок ID совпадают;
  - `count_exact` — число ID совпадает;
  - `url_exact` — URL совпадает после нормализации;
  - `title_overlap` — Jaccard токенов заголовков;
  - `body_overlap_mean` — среднее Jaccard тела по совпавшим ID;
  - `body_overlap_min` — минимум;
  - `extra_ids`, `missing_ids`.
- Токенизация SHALL быть: lower-case, пунктуация → пробел, схлопывание
  пробелов; числа и ID сохраняются.
- Jaccard SHALL быть `|A ∩ B| / |A ∪ B|`; два пустых множества → `1.0`,
  иначе `0.0`.
- URL SHALL сравниваться без завершающего `/` и без `#...`.

#### Scenario: Переписанный текст

- **WHEN** ID и URL совпали, но тело требования переписано своими словами
- **THEN** `body_overlap_mean < 0.85`
- **AND** вердикт `fail` с причиной «текст переписан»

### Requirement: Вердикт канарейки

- `fail` SHALL быть, если `ids_exact=false`, `count_exact=false`,
  `url_exact=false`, `extra_ids`/`missing_ids` непусты,
  `title_overlap < 0.60`, `body_overlap_mean < 0.85` или
  `body_overlap_min < 0.50`.
- `warn` SHALL быть, если `body_overlap_mean` в `[0.85, 0.95)`.
- `pass` SHALL быть иначе.
- В отчёт SHALL попадать оба текста, diff по ID, метрики, цитаты расхождений
  до 300 символов и `prompt_sha256` канареечного промпта.
- Отчёт SHALL валидироваться pydantic и писаться в
  `dz8/output/<run_id>/report.json` + `report.md`.

#### Scenario: Другой URL

- **WHEN** LLM добавляет URL, которого нет во входе
- **THEN** `url_exact=false`
- **AND** вердикт `fail`

#### Scenario: Предупреждение

- **WHEN** ID, URL и порядок совпали, `body_overlap_mean=0.90`
- **THEN** вердикт `warn`
- **AND** в отчёте есть цитаты расхождений

### Requirement: CLI и UI

- CLI SHALL поддерживать `llm-lab canary --model <model> [--runs N]`.
- `--runs` по умолчанию 1; при N > 1 SHALL выполняться все прогоны,
  сохраняться все результаты, а вердикт SHALL ставиться по худшему прогону.
- CLI SHALL возвращать код 4 при `fail`, 0 при `pass`/`warn`.
- UI SHALL показывать две колонки: детерминированный текст и LLM-текст.
- UI SHALL показывать diff по ID, URL и заголовку.
- UI SHALL показывать метрики и вердикт.
- UI SHALL позволять скачать `report.json` и `report.md`.
- UI SHALL NOT автоматически подставлять LLM-результат в генерацию.

#### Scenario: Запуск из UI

- **WHEN** пользователь нажимает «Запустить» на вкладке «Канарейка»
- **THEN** показываются оба текста, diff и метрики
- **AND** кнопки скачивания доступны

#### Scenario: Запуск из CLI

- **WHEN** `llm-lab canary --model llama3 --json`
- **THEN** stdout содержит один JSON с `verdict` и `report`
- **AND** отчёт записан в `dz8/output/`
