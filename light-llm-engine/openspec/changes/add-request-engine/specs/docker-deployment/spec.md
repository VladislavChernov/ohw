# Delta Spec: docker-deployment

## ADDED Requirements

### Requirement: Запуск движка контейнером

Проект ДОЛЖЕН содержать `Dockerfile`, из которого собирается образ движка, и
`compose.yaml`, поднимающий стек целиком: сервис `ollama` с локальной моделью
и сервис `app` с движком.

#### Scenario: Запуск через docker compose

- **WHEN** пользователь выполняет `docker compose up --build`
- **THEN** сервисы `ollama` и `app` запускаются, движок обрабатывает файлы из
  `./input` (смонтированного в `/data/input`) и пишет результаты в `./output`
  (смонтированного в `/data/output`)

### Requirement: Ожидание готовности модели

Сервис `app` ДОЛЖЕН запускаться только после того, как сервис `ollama`
поднял модель, указанную в `OLLAMA_MODEL`. Подготовка модели ДОЛЖНА быть
идемпотентной: при повторном запуске модель не скачивается заново.

#### Scenario: Модели ещё нет в volume

- **WHEN** контейнер `ollama` запущен впервые и модель отсутствует
- **THEN** контейнер скачивает модель один раз и становится готовым
  (healthcheck = успешный `ollama show`)

#### Scenario: Модель уже скачана

- **WHEN** модель уже есть в volume моделей
- **THEN** контейнер `ollama` не выполняет повторную загрузку и переходит
  к обслуживанию запросов

### Requirement: Передача конфигурации в контейнер

Сервис `app` ДОЛЖЕН получать адрес ollama и имя модели через переменные
окружения: `OLLAMA_BASE_URL` (внутри сети compose — `http://ollama:11434`) и
`OLLAMA_MODEL` (единая точка настройки — файл `.env` в корне проекта). Образ
ДОЛЖЕН запускаться от непривилегированного пользователя.

#### Scenario: Запуск без явных аргументов CLI

- **WHEN** контейнер `app` запущен `docker compose` без дополнительных
  аргументов
- **THEN** движок использует `OLLAMA_BASE_URL` и `OLLAMA_MODEL` из окружения
  контейнера и дефолтные `/data/input` и `/data/output`

### Requirement: Поддержка GPU и CPU

Базовый `compose.yaml` ДОЛЖЕН запускаться на CPU. GPU подключается
override-файлом `compose.gpu.yaml` (резервация NVIDIA-устройств), чтобы стек
работал на машинах без CUDA.

#### Scenario: Запуск без GPU-файла

- **WHEN** пользователь выполняет `docker compose up` без overrides
- **THEN** стек запускается на CPU и отвечает на запросы

#### Scenario: Запуск с GPU

- **WHEN** пользователь выполняет
  `docker compose -f compose.yaml -f compose.gpu.yaml up --build`
  на машине с NVIDIA GPU и драйвером
- **THEN** ollama использует GPU для инференса

### Requirement: Dev Container для разработки

Проект ДОЛЖЕН содержать конфигурацию VS Code Dev Container с Python
актуальной версии, установленным uv и доступом к Docker для интеграционных
проверок.

#### Scenario: Открытие проекта в VS Code

- **WHEN** разработчик открывает проект через «Reopen in Container»
- **THEN** окружение готово к работе после `uv sync --dev`, тесты запускаются
  командой `uv run pytest -m "not integration"`