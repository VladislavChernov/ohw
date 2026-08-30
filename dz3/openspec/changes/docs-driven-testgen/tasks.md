# tasks.md — docs-driven-testgen (advanced, JSON-план)

> Зависит от утверждения JSON-контракта (draft в proposal.md).

## 1. Ядро-исполнитель (`core/`)

- [ ] 1.1 HTTP-исполнитель шагов: `request` из плана (method/path/headers/body), сбор ответа (status, body, время); подстановка `{var}` из `vars`/`extract`
- [ ] 1.2 Выполнение сценария: упорядоченные `steps`, `extract` (JSONPath → переменные), `on_fail` (abort|continue); отдельный массив `cleanup` на уровне теста (выполняется всегда после steps, если сценарий начат; не влияет на статус; предупреждения в отчёт); переменные из steps видны в cleanup; `provisional` у create; нормализация плоской формы v1 к одному шагу
- [ ] 1.3 Оценщик `expect`: **`ohw_kit.checks.evaluate`** (эталон из kit): DSL `eq/len_eq/contains/fields_eq/type` по JSONPath, `CheckResult` → строки отчёта; собственного оценщика в проекте нет
- [ ] 1.4 Отчёт: имя теста из плана, результат каждого шага и каждой проверки (ожидание vs факт), человекочитаемый FAILURES без трейсбеков, режим документации в шапке

## 2. JSON-план (`plan/`)

- [ ] 2.1 Схема плана `schema/v1.json` (версионируемая): тесты-сценарии со `steps` + `cleanup`, `vars`/`extract`/`on_fail`/`provisional`; валидация структуры плана
- [ ] 2.2 Валидатор плана: (а) покрытие ресурсы×глаголы (OpenAPI-контракт / ручной `--required-resources` / только глаголы); (б) мутирующий сценарий обязан иметь `cleanup` с откатом либо `provisional` у create; (в) переменные cleanup извлекаются в steps
- [ ] 2.3 Feedback-луп на уровне плана: «нет покрытия X / нет cleanup в сценарии Y — перегенерируй JSON» в рамках retry-бюджета

## 3. Слои документации (`docs.py`)

- [ ] 3.1 Детектор типа источника: OpenAPI (JSON/YAML) / Markdown / URL страницы
- [ ] 3.2 Слои и приоритеты: страница = «Контекст» (не контракт); `contracts/base` = XYZ; `contracts/supplements` = XXX (лексикографический приоритет, XXX побеждает, конфликты помечаются)
- [ ] 3.3 URL: скачивание + чистка HTML, лимит выжимки на слой; OpenAPI: структурное перечисление `путь → методы → коды → схемы` + автогенерация маркеров
- [ ] 3.4 Случай «только XXX»: пометка «базовая спека недоступна»; случай «без спек»: деградация к best effort (глаголы/ручные ресурсы)

## 4. Промпт и интеграция (через ohw_kit)

- [ ] 4.1 Зависимость `../kit` (`uv add ../kit`); **весь LLM-доступ через `ohw_kit.ollama_client.OllamaClient`** (`json_mode=True`; transport-инъекция для тестов) — своего ollama-модуля нет
- [ ] 4.2 Чтение контрактов через `ohw_kit.io.load_input` + проектный ридер OpenAPI (`register_reader`), рендер отчёта через `ohw_kit.render.render_markdown`
- [ ] 4.3 Шаблон `input/prompt.txt`: плейсхолдеры `{docs_context}`, `{contracts_base}`, `{contracts_supplements}` + описание JSON-контракта v3 (сценарии/шаги/extract/cleanup, пример, допустимые `op`), «выводи ТОЛЬКО JSON плана»
- [ ] 4.4 `config.py`/`cli.py`: парсинг JSON-ответа модели через **`ohw_kit.jsonreply.extract_json`**, `ValidationResult` для feedback-лупа; `--contracts-dir`, `--required-resources`, семплирование
- [ ] 4.5 Сборка отчёта ядром (не pytest): счётчики из результатов проверок плана

## 5. Тесты и документация

- [ ] 5.1 Юнит-тесты: выполнение сценария (extract→подстановка, abort/continue, cleanup при падении сценария и при abort, cleanup не влияет на статус, provisional, переменные steps→cleanup), нормализация v1, валидация схемы плана, требование cleanup у мутирующих сценариев, покрытие/feedback, слои доки (мердж, конфликты, все 4 комбинации) — DSL проверок и парсинг JSON тестируются в kit (`tests/test_checks.py`, `tests/test_jsonreply.py` — уже зелёные)
- [ ] 5.2 README advanced: архитектура (ядро/план/доки), флаги, 4 сценария документации, сравнение с simple
- [ ] 5.3 E2E-прогон на JSONPlaceholder: сценарий PUT-обновления с откатом исходного состояния проходит целиком; план покрывает все ресурсы; ядро исполняет без pytest; отчёт человекочитаем; фидбек-луп не срабатывает зря