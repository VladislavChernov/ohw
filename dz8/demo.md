# Демонстрация Dz8 — шпаргалка

[← README](README.md) · [User Guide](docs/user-guide.md) · [Глоссарий](docs/glossary.md)

Один файл для показа: что запустить, куда нажать, что сказать на каждой
вкладке и где лежит подробное описание. Расчётное время показа — 10–15 минут
(без длинных серий прогонов).

## 1. Что показывает лаборатория

1. **Стенд и модель** — какая Ollama и какая именно `llama3` (digest) сейчас
   работают.
2. **Глюки** — галлюцинации в артефактах, контракт HTTP API, канарейка
   «LLM-нормализатор против детерминированного».
3. **Деградацию** — согласованность серии прогонов и сравнение baseline против
   candidate с доверительными интервалами.

Всё живёт отдельной страницей в существующем UI Dz2: новых контейнеров,
портов и второго Ollama нет.

## 2. Запуск

Нужен Docker (Docker Desktop на Windows/macOS, Engine на Linux), ~10 ГБ и
интернет при первом запуске. GPU не обязателен, но с ним первый ответ идёт
секунды вместо минут.

### Вариант А. Полный клон

```powershell
git clone https://github.com/VladislavChernov/ohw.git
cd ohw\dz8
.\run.ps1 -Check          # auto: GPU при наличии runtime nvidia, иначе CPU
```

На Linux/macOS:

```bash
cd ohw/dz8
./run.sh --check          # CPU; ./run.sh --gpu — Linux + NVIDIA
```

### Вариант Б. Только нужные папки (sparse checkout)

```bash
git clone --depth 1 --filter=blob:none --sparse \
  https://github.com/VladislavChernov/ohw.git
cd ohw
git sparse-checkout set dz8 dz2 infra
cd dz8
./run.sh --check
```

`dz8` — лаборатория, `dz2` — исполнитель (нормализатор, промпты, UI-контейнер),
`infra` — общий `ohw-ollama` и базовый образ. Подробнее и с типовыми ошибками
— [docs/install.md](docs/install.md).

### Что делает скрипт

1. проверяет Docker и наличие папок `dz2/`, `infra/`;
2. в режиме `auto` выбирает GPU, если `nvidia-smi` видит карту и в Docker есть
   runtime `nvidia`, иначе CPU;
3. поднимает стенд существующими скриптами `dz2/scripts/up-*.ps1` / `up-*.sh`;
4. проверяет `llama3` в `ohw-ollama` и при необходимости докачивает;
5. печатает адрес UI; с `-Check` / `--check` прогоняет `health` и `api-check`.

Если стенд уже здоров, шаг подъёма пропускается — модель не выгружается.
Принудительный перезапуск: `-Rebuild` / `--rebuild`.

Windows без PowerShell-команд: двойной клик по `run.cmd`.

## 3. Открыть лабораторию

1. `http://localhost:8501`
2. Слева в боковой панели: **Страница** → **«Проверка модели»**.
3. В боковой панели: **«Обновить список моделей»**, выбрать `llama3`,
   **«Проверить связь»** — должна появиться зелёная строка `Ollama 0.33.3`.

Первый ответ может идти **2–3 минуты** (загрузка модели в память) — это не
зависание.

## 4. Порядок показа по вкладкам

| # | Вкладка | Кнопка запуска | Коротко о чём | Подробно |
|---|---|---|---|---|
| 1 | Стенд | **«Проверить стенд»** | версия Ollama, наличие `llama3`, digest — фиксируем окружение | [docs/tabs/stand.md](docs/tabs/stand.md) |
| 2 | API | **«Проверить API»** | 12 проверок контракта `/api/version`, `/tags`, `/show`, `/chat`: коды, схема JSON, `format=json`, `seed`, ошибки | [docs/tabs/api.md](docs/tabs/api.md) |
| 3 | Канарейка | **«Запустить»** | LLM нормализует тот же документ, что и детерминированный нормализатор; расхождение ID/URL/текста — ранний сигнал глюка | [docs/tabs/canary.md](docs/tabs/canary.md) |
| 4 | Галлюцинации | **«Запустить»** | 8 probe-ов: выдуманные `BR-N`, URL, сущности, результаты прогонов; метрика `hallucination_rate` | [docs/tabs/hallucinations.md](docs/tabs/hallucinations.md) |
| 5 | Согласованность | **«Запустить»** | серия из `runs` прогонов одного промпта: `pass_at_1`, покрытие `BR`, пересечение `TC-id` и заголовков | [docs/tabs/consistency.md](docs/tabs/consistency.md) |
| 6 | Деградация | **«Сравнить»** | baseline против candidate: pass rate, валидность, покрытие, выдуманные ID, задержка, Wilson и bootstrap CI | [docs/tabs/degradation.md](docs/tabs/degradation.md) |
| 7 | Последний прогон | — | сводка, вердикт, прогоны и файлы самого свежего запуска; кнопки скачивания `report.json` / `report.md` | [docs/tabs/last-run.md](docs/tabs/last-run.md) |
| 8 | Отчёты | — | список сохранённых отчётов, выбор прогона, просмотр и скачивание JSON/Markdown | [docs/tabs/reports.md](docs/tabs/reports.md) |

Как показывать: 1 → 2 → 3 → 4 занимают секунды-минуты, дальше 5 и 6 запускать
по времени (10 прогонов на CPU — десятки минут). Если время ограничено,
покажите 1–4, затем 7–8 на уже сохранённых отчётах.

Прогресс есть на всех вкладках: процент, число шагов, прошедшее время и
оценка остатка; после завершения — строка «Заняло: …».

## 5. Те же проверки из CLI

Из папки `dz2` (внутри существующего контейнера `dz2-ui-1`):

```powershell
cd D:\Otus\ohw\dz2

docker compose exec ui python -m llm_degradation health --model llama3 --json
docker compose exec ui python -m llm_degradation api-check --model llama3 --json
docker compose exec ui python -m llm_degradation canary --model llama3 `
  --input /opt/dz8/golden/inputs/catalog.md --json
docker compose exec ui python -m llm_degradation hallucinations --model llama3 --runs 1 --json
docker compose exec ui python -m llm_degradation consistency --model llama3 `
  --input /opt/dz8/golden/inputs/catalog.md --runs 10 --seed 42 --json
docker compose exec ui python -m llm_degradation degradation `
  --baseline-model llama3 --candidate-model llama3 `
  --input /opt/dz8/golden/inputs/catalog.md --runs 5 --json
docker compose exec ui python -m llm_degradation report --list --json
```

`--input` нужен, если в `dz8/input/` нет файла требований: `canary`,
`consistency` и `degradation` без него возвращают код 2. `hallucinations`
берёт вход из файла проб и работает без него.

Коды выхода: `0` — успех (включая `warn`), `2` — ошибка конфигурации/входа,
`3` — Ollama недоступна, `4` — вердикт `fail`/`insufficient_data`,
`5` — деградация при `--fail-on-regression`.

## 6. Куда смотреть в результате

- `dz8/output/<run_id>/report.json` — машинный отчёт (его читают UI и CLI);
- `dz8/output/<run_id>/report.md` — тот же отчёт для человека;
- `dz8/output/<run_id>/normalized.md` и `prompt.txt` — вход и промпт запуска;
- `dz8/output/<run_id>/probes/` — по каталогу на каждую пробу (request, response, метрики);
- `dz8/output/<run_id>/raw/` — сырые запросы и ответы модели;
- `dz8/golden/baseline/llama3.json` — baseline для вкладки «Деградация»
  (под очистку не попадает).

Хранятся только последние `reports.keep_runs` запусков (по умолчанию 3),
старые каталоги с валидным `report.json` удаляются при записи нового.

## 7. Что считать успехом и о чём предупредить

| Проверка | Ожидаемое на `llama3` |
|---|---|
| `health` | `ok: true`, Ollama 0.33.3, digest `365c0bd3…` |
| `api-check` | 12/12 pass |
| `canary` | pass (с `golden/inputs/catalog.md`); возможен `parse_error`/`warn` |
| `hallucinations --runs 1` | pass, 8/8 probe-ов; при `--runs 3` проба **H-04** может дать `fail` (нестабильность модели) |
| `consistency --runs 10` | вердикт **fail** по `br_coverage_min`: 0.29–0.43 при пороге 0.80 |
| `degradation` | `unchanged` при сравнении `llama3` с самим собой |

Это не поломка стенда, а измеренный факт: `llama3` действительно теряет
требования и нестабилен на части проб. Baseline
`golden/baseline/llama3.json` записан живым прогоном и тоже имеет вердикт
`fail` — так и должно быть, пороги не подкручивались.

## 8. Остановка

```powershell
cd D:\Otus\ohw\dz2
.\scripts\down.ps1           # только UI
.\scripts\down.ps1 -Infra    # ещё и общий ohw-ollama (нужен другим заданиям!)
```

```bash
cd ohw/dz2
./scripts/down.sh
./scripts/down.sh -i
```

## 9. Если что-то не так

| Симптом | Что сделать |
|---|---|
| `docker not found` / daemon не запущен | запустить Docker Desktop / `sudo systemctl start docker` |
| `The lab needs the dz2 and infra folders` | `git sparse-checkout set dz8 dz2 infra` |
| Порт 8501 или 11434 занят | освободить порт; 11434 принадлежит общему `ohw-ollama` |
| UI открылся, но «Проверка модели» пустая | проверить, что `dz8` смонтирован: `docker compose -f ..\dz2\compose.yaml logs ui` |
| Долгий первый ответ | модель загружается; дождаться |
| Модель не найдена | `docker exec ohw-ollama ollama pull llama3` |

Больше — [docs/install.md](docs/install.md) и
[dz2/scripts/README.md](../dz2/scripts/README.md).

## 10. Документы

| Документ | Что внутри |
|---|---|
| [docs/install.md](docs/install.md) | установка на другом ПК, git, запуск, типовые ошибки |
| [docs/user-guide.md](docs/user-guide.md) | полный гайд: параметры, порядок проверок, чтение отчёта, ограничения |
| [docs/glossary.md](docs/glossary.md) | все ключи «Сводки», формулы, пороги |
| [docs/tabs/](docs/tabs/) | по документу на каждую вкладку |
| [README.md](README.md) | что сделано, тесты, архитектура, соответствие заданию |
