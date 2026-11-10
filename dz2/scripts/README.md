# Скрипты стенда dz2

> Для лаборатории Dz8 есть обёртка: `dz8/run.ps1` (Windows) и `dz8/run.sh`
> (Linux/macOS). Она вызывает эти же скрипты, проверяет `llama3` и печатает
> адрес UI. Подробности — `dz8/docs/install.md`.

Короткая инструкция для тех, кто впервые запускает стенд. Скрипты поднимают
**общий** Ollama и **один** контейнер приложения: новых постоянных контейнеров,
портов и сетей они не создают.

## Что поднимается

| Компонент | Имя | Порт | Назначение |
|---|---|---|---|
| Ollama | `ohw-ollama` | `11434` | локальная LLM, общий контейнер для всех проектов |
| UI | `dz2-ui-1` | `8501` | Streamlit-рабочее место (включая страницу «Проверка модели») |

Образы: `ohw-python:3.13` (базовый, собирается один раз) и `ohw/dz2:local`
(проект, пересобирается при `--rebuild`).

## Требования

- **Docker Desktop** (Windows/macOS) или **Docker Engine + Compose plugin**
  (Linux); демон должен быть запущен.
- Свободное место: около 10 ГБ (образы + модель).
- Интернет при первом запуске: скачиваются базовый образ и модель `llama3`.
- **Windows GPU**: NVIDIA GPU, актуальный драйвер, NVIDIA Container Toolkit и
  Docker Desktop с WSL2-backend. Проверка: `nvidia-smi` и
  `docker info --format '{{json .Runtimes}}'` (должен быть `nvidia`).
- **Windows CPU / Linux / macOS**: ничего кроме Docker. На CPU генерация
  медленная (единицы токенов в секунду), это ожидаемо.
- Команды выполняются из корня проекта `dz2` (там, где лежит `compose.yaml`).

## Windows (PowerShell)

```powershell
cd D:\Otus\ohw\dz2

.\scripts\up-gpu.ps1            # GPU (NVIDIA), рекомендуется
.\scripts\up-cpu.ps1            # CPU, если GPU нет
.\scripts\up-gpu.ps1 -Rebuild   # принудительно пересобрать образ проекта

.\scripts\down.ps1              # остановить UI
.\scripts\down.ps1 -Infra       # остановить ещё и общий ohw-ollama
```

Если PowerShell блокирует локальные скрипты:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\up-cpu.ps1
```

## Linux / macOS (bash)

Поддерживаемый кроссплатформенный путь — **CPU**:

```bash
cd ohw/dz2
chmod +x scripts/*.sh          # один раз, если файлы пришли без +x

./scripts/up-cpu.sh            # CPU, Linux и macOS
./scripts/up-cpu.sh --rebuild  # принудительно пересобрать образ проекта

./scripts/down.sh              # остановить UI
./scripts/down.sh -i           # остановить ещё и общий ohw-ollama
```

`up-gpu.sh` существует для Linux с NVIDIA (драйвер + Container Toolkit). На
macOS Docker Desktop не пробрасывает NVIDIA GPU в Linux-контейнеры: скрипт
предупредит и автоматически поднимет стенд на CPU.

## Что делать после запуска

1. Откройте `http://localhost:8501`.
2. В боковой панели выберите страницу **«Проверка модели»**.
3. Нажмите **«Обновить список моделей»** и **«Проверить связь»**.
4. Дальше действуйте по [User Guide](../../dz8/docs/user-guide.md).

Проверка без UI:

```bash
curl http://localhost:11434/api/version
docker ps --filter name=ohw-ollama --filter name=dz2-ui-1
```

Модель скачивается один раз в общий volume. Если её нет:

```bash
docker exec ohw-ollama ollama pull llama3
```

## Типичные проблемы

| Симптом | Что сделать |
|---|---|
| `docker: command not found` | установить Docker Desktop / Docker Engine |
| `Docker daemon is not available` | запустить Docker Desktop / `sudo systemctl start docker` |
| `port is already allocated` | освободить `8501` или `11434`; порт `11434` принадлежит `ohw-ollama` |
| GPU-режим: `nvidia-smi` не видит GPU | обновить драйвер NVIDIA |
| GPU-режим: нет runtime `nvidia` | установить NVIDIA Container Toolkit и перезапустить Docker |
| Модель не найдена | `docker exec ohw-ollama ollama pull llama3` |
| Первый ответ идёт минутами | модель загружается в память; дождитесь завершения |
| UI не отвечает | `docker compose -f compose.yaml logs ui` |

`down.ps1 -Infra` / `down.sh -i` останавливают **общий** Ollama: он может быть
нужен другим проектам монорепозитория. Без флага останавливается только UI.
