# Project: graduation-project

Автоматизация генерации и оценки качества тестовой документации с использованием LLM

## Стек
- Python 3.13, uv, lock-файл коммитится
- HTTP: httpx; схемы: pydantic; тесты: pytest; UI: streamlit
- Инференс: существующий контейнер `ohw-ollama`, модель `qwen2.5:7b-instruct`
- UI: работаем в `dz2-ui-1` на 8501
- Зависимость `ai-testcase-generator` — path-зависимость на `../dz2`, не копия кода
- OpenSpec конвенции: спеки в `openspec/changes/<change>/{proposal,design,tasks}.md`
- Требования — EARS: `SHALL` + сценарии `WHEN/THEN`
- Код и комментарии — на английском; документация (README, спеки) — на русском
- Отчёты — JSON (источник правды) + Markdown (представление)

## Окружение
- `OLLAMA_BASE_URL` — адрес Ollama (в Docker `http://ohw-ollama:11434`, с хоста `http://localhost:11434`)
- `OLLAMA_MODEL` — имя модели (по умолчанию `qwen2.5:7b-instruct`)
- `OLLAMA_TIMEOUT` — таймаут запроса (по умолчанию 600 с)
- `DZ2_PROMPT_DIR`, `DZ2_PRESETS_DIR`, `AI_TESTGEN_NORMALIZER_CONFIG` — ресурсы Dz2

## Контейнеры
- Используем `infra/compose.yaml`: `ohw-ollama`, сеть `ohw_net`
- Используем `infra/python`: `ohw-python:3.13`
- Используем `dz2/compose.yaml`: `dz2-ui-1`
- Новых постоянных контейнеров, сервисов и портов нет
- `qwen2.5:7b` докачивается в `ohw-ollama`, второй Ollama не поднимается
- Тесты идут в `dz2-ui-1` с временным venv в `/tmp`

## Границы
- Выпускной проект не меняет `dz2/src`, кроме адаптеров и тестов
- Не меняет `dz2/prompts`, `dz2/presets`, `dz2/normalizer.toml`
- Не обучает модель и не использует LLM-судью текста промпта как метрику деградации
- Использует детерминированный нормализатор ДЗ2 до сборки промпта

## Связь с домашними заданиями
- **DZ2**: Ядро генерации тест-кейсов и артефактов (5 типов: testcases, checklist, testplan, qareport, coveragematrix)
- **DZ7**: Шаблоны промптов для 5 типов артефактов (в `templates/<type>.txt`)
- **DZ8**: Проверка качества генерации (галлюцинации, консистентность, деградация)
- **Infra**: Общий Ollama сервис и Docker окружение
- **Цель**: Собрать существующие компоненты в единый выпускной проект с полной отчетностью по требованиям курса