# ai-testcase-generator

CLI-утилита на Python, которая с помощью локальной LLM через ollama
генерирует по файлам требований три вида артефактов: тест-кейсы,
чек-листы и тест-планы. Для каждого входного файла создаётся
markdown-отчёт.

## Как это работает

```
input/                output/
├── auth.md     ──►   ├── auth.md      # 10 тест-кейсов (6 позитивных, 4 негативных)
└── cart.txt    ──►   └── cart.md
        │
        ▼
   ollama (GPU) → валидация ответа → retry при нарушениях → markdown-отчёт
```

- Каждый файл из `--input-dir` обрабатывается независимо.
- URL сайта берётся из текста файла; аргумент `--url` переопределяет его для всех файлов.
- Коллизия имён (`auth.md` + `auth.txt`) — ошибка: удалите или переименуйте один из файлов.

## Типы артефактов

Тип артефакта объявляется во front-matter в начале входного файла;
без front-matter файл считается тест-кейсами:

```
---
type: checklist
---
Сайт: https://example.com/catalog
Зоны проверки: навигация, поиск, пагинация...
```

| `type` | Ответ модели | Что проверяется |
|---|---|---|
| `testcases` | JSON `{"cases": [...]}` | каждый кейс проходит pydantic-схему; количество равно `--count`; негативные кейсы появляются всегда, когда запрошено 2 и больше (минимум один), а при N≥5 их должно быть не меньше трети; у каждого кейса поле `requirement` со ссылкой на проверяемое требование |
| `checklist` | Markdown-документ | линт: зоны как `## ` заголовки, под каждой ≥1 пункт списка; пункты завершаются номером требования `(BR-N)` |
| `testplan` | Markdown-документ | линт: обязательные разделы (цели, объём, подход, критерии, риски) непусты; объём перечисляет охваченные BR |

Нарушения формата отправляются модели на исправление (`--max-retries`);
после исчерпания попыток — код выхода 4. Для markdown-типов `--count`
игнорируется.

Трассировка: если во входном файле требования пронумерованы (BR-1,
BR-2, …), каждый тест-кейс получает ссылку на проверяемое требование,
пункты чек-листа заканчиваются его номером, а тест-план перечисляет
охваченные требования в разделе объёма.

## Требования

- Локальный запуск: [uv](https://docs.astral.sh/uv/) (сам установит Python 3.13
  при первом запуске) **или** уже установленный Python 3.13+ с pip,
  плюс доступный сервис ollama с моделью
- Запуск через Compose: Docker (для общего ollama — любой; для автономного
  GPU-режима — NVIDIA CUDA или AMD ROCm, см. «Запуск через Docker Compose»)

## Быстрый старт (локально)

```bash
# установка uv, если ещё нет:
curl -LsSf https://astral.sh/uv/install.sh | sh

uv sync --dev
uv run ai-testgen -n 10
```

Пути берутся из конфига по умолчанию (`input_dir = "input"`,
`output_dir = "output"` в `ai-testgen.toml`): положите файлы требований
в `./input` — отчёты появятся в `./output`. Можно переопределить
ключами:

```bash
uv run ai-testgen -n 10 --input-dir my-requirements --output-dir reports
```

В каталоге `examples/input` лежат примеры входных файлов (тест-кейсы
для авторизации и корзины) — это демонстрация формата, не рабочий
материал:

```bash
uv run ai-testgen -n 5 --input-dir examples/input --output-dir output
```

Готовые ответы модели для этих же запросов лежат рядом —
`examples/output/response_auth.md` и `response_cart.md`
(сгенерированы `qwen2.5:7b-instruct`, `-n 5`). Их можно посмотреть
без запуска LLM; при повторной генерации ответы будут отличаться.

Модель и адрес ollama берутся из `ai-testgen.toml` в корне репозитория;
при необходимости переопределите их переменными окружения или CLI-аргументами:

```bash
export OLLAMA_MODEL=llama3.1:8b   # вместо модели из конфига
```

## Без uv: системный Python + свой контейнер ollama

Если на машине уже стоит Python 3.13+, uv не обязателен — поднимите ollama
вручную и установите пакет через pip:

```bash
# контейнер ollama с моделью (--gpus all можно опустить — тогда CPU)
docker run -d --name ollama --gpus all -p 11434:11434 \
  -v ollama_models:/root/.ollama ollama/ollama
docker exec ollama ollama pull qwen2.5:7b-instruct

# программа из корня репозитория
pip install .
ai-testgen -n 10 --input-dir examples/input --output-dir output
```

Ни env-переменных, ни правки конфига не требуется: `ai-testgen.toml`
в корне репозитория уже указывает на `http://localhost:11434`, порт
контейнера проброшен наружу. Если репозиторий склонирован, первые две
команды заменяются подъёмом общей инфраструктуры
(`.\infra\up.ps1 -Project D:\Otus\Dz2`) — контейнер и модель поднимутся
автоматически.

## Запуск в Docker

```bash
docker build -t ai-testgen .
docker run --rm \
  -v ./examples/input:/data/input \
  -v ./output:/data/output \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e OLLAMA_MODEL=qwen2.5:7b-instruct \
  ai-testgen -n 10
```

Внутри контейнера рабочая папка `/data`: вход монтируется в `/data/input`,
выход появляется в `/data/output`.

## Запуск через Docker Compose

Проект использует **общую инфраструктуру** из каталога
[`infra/`](../infra/README.md): компоненты (сейчас — `ollama`) описываются
один раз в [`infra/compose.yaml`](../infra/compose.yaml) и поднимаются по мере
надобности. Объявление проекта лежит в [`infra.yaml`](infra.yaml):
`components: [ollama]`. `docker compose up` в каталоге проекта поднимает
**только приложение**.

```powershell
# 1. один раз — поднимаем нужные компоненты (читает этот проект's infra.yaml)
cd d:/Otus/infra
.\up.ps1 -Project D:\Otus\Dz2
#    эквивалент: docker compose -f infra/compose.yaml --profile ollama up -d

# 2. в каталоге dz2 — только приложение (ollama уже поднят в общей сети ohw_net)
cd d:/Otus/Dz2
docker compose up --build --abort-on-container-exit
# → приложение сгенерирует тест-кейсы: ./input/*.md|txt → ./output/*.md
```

Приложение ждёт готовности модели (healthcheck); `./input` и `./output`
монтируются как volume; `--abort-on-container-exit` гасит compose, когда
работа завершена. Сменить модель — `OLLAMA_MODEL=` в `infra/.env` +
`.\infra\up.ps1 -Project D:\Otus\Dz2` (или `docker compose -f infra/compose.yaml restart ollama`).

Compose-сервис `app` подключается к общей сети `ohw_net` и обращается к
`ollama` по имени контейнера `ohw-ollama` (см. `.env` проекта).

GPU-ускорение общего ollama (NVIDIA/AMD) — оверлеи задаются при запуске инфры:

```powershell
cd d:/Otus/infra
.\up.ps1 -Project D:\Otus\Dz2 -Gpu    # NVIDIA CUDA
.\up.ps1 -Project D:\Otus\Dz2 -Amd    # AMD ROCm
```

Без GPU инференс идёт на CPU (медленно, qwen2.5:7b ≈ 2–4 токена/с) — при
полной генерации поднимите `timeout` в секции `[ollama]` файла
`ai-testgen.toml` (например, до 600 секунд). Для заметного ускорения на Mac
поставьте родную сборку [Ollama](https://ollama.com/download) (Metal) и
запускайте программу локально через uv/pip.

Полезные варианты:

```bash
docker compose -f infra/compose.yaml ps     # статус общего ollama
docker compose run --rm app -n 5            # разовая генерация с другим количеством
```

- Модель хранится в общем volume `ohw_ollama_models` и переживает перезапуски;
  скачивается один раз для всех проектов.
- Имя модели задаётся в `infra/.env` (`OLLAMA_MODEL=...`).
- Адрес службы: хост `http://localhost:11434`, devcontainer
  `http://host.docker.internal:11434`, compose (сеть `ohw_net`) — `http://ohw-ollama:11434`.

## Конфигурация

Параметры складываются из четырёх слоёв (нижний имеет наименьший приоритет):

```
defaults  <  ai-testgen.toml  <  переменные окружения OLLAMA_*  <  аргументы CLI
```

Файл `ai-testgen.toml` ищется в текущем каталоге автоматически (или укажите
`--config <path>`); отсутствие файла ошибкой не считается.

```toml
[ollama]
base_url = "http://localhost:11434"
model = "qwen2.5:7b-instruct"
timeout = 180.0

[paths]
input_dir = "input"
output_dir = "output"

[generation]
temperature = 0.7
max_retries = 3
```

| Переменная | По умолчанию | Описание |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` (хост, из `ai-testgen.toml`) | адрес сервиса ollama; для devcontainer `http://host.docker.internal:11434`, для compose-сервиса `http://ohw-ollama:11434` |
| `OLLAMA_MODEL` | — (или `model` в конфиге) | имя установленной модели |

### Аргументы CLI

| Аргумент | По умолчанию | Описание |
|---|---|---|
| `-n`, `--count` | — (обязательный) | тест-кейсов на каждый файл типа `testcases`; для чек-листов и планов игнорируется |
| `--config` | `./ai-testgen.toml` | путь к TOML-конфигу |
| `--input-dir` | из конфига (`./input`, `/data/input`) | папка с файлами требований (.md/.txt) |
| `--output-dir` | из конфига (`./output`, `/data/output`) | папка для markdown-отчётов |
| `--url` | из текста файла | URL тестируемого сайта |
| `--max-retries` | из конфига (`3`) | повторные попытки при невалидном ответе модели |
| `--temperature` | из конфига (`0.7`) | температура сэмплирования |

## Коды выхода

| Код | Значение |
|-----|----------|
| 0 | успех |
| 2 | ошибки аргументов, конфига или входных файлов (нет папки, пусто, коллизия имён, битый `--config`, модель не задана ни в env, ни в конфиге) |
| 3 | сервис ollama недоступен |
| 4 | не удалось получить валидный артефакт (кейсы/чек-лист/план) за все попытки |

## Разработка

VS Code: откройте проект и выполните «Dev Containers: Reopen in Container» —
окружение (Python 3.13, uv, доступ к Docker) настроится автоматически.

```bash
uv sync --dev                      # установка зависимостей
uv run pytest -m "not integration" # unit-тесты
uv run pytest -m integration       # интеграционные (нужен живой ollama,
                                   # модель — в OLLAMA_MODEL; иначе skip)
uv run ruff check . && uv run mypy src tests
```
