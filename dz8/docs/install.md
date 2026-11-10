# Установка и запуск на другом ПК

[← README](../README.md) · [User Guide](user-guide.md) · [Глоссарий](glossary.md)

Инструкция для того, кто забирает проект из git на чистой машине и хочет
поднять стенд одной командой. Новых контейнеров, портов и сетей Dz8 не
создаёт: используется общий `ohw-ollama` и существующий UI `dz2-ui-1`.

## 1. Что нужно на машине

| Требование | Зачем |
|---|---|
| Docker Desktop (Windows/macOS) или Docker Engine + Compose plugin (Linux) | контейнеры `ohw-ollama` и `dz2-ui-1` |
| Запущенный Docker daemon | иначе скрипт останавливается с подсказкой |
| ~10 ГБ на диске | образы `ohw-python:3.13`, `ohw/dz2:local` и модель `llama3` |
| Интернет при первом запуске | скачиваются базовый образ, зависимости и модель |
| NVIDIA GPU + драйвер + Container Toolkit | только для GPU-режима; без него стенд идёт на CPU |
| Git | чтобы скачать проект |

GPU нужен для скорости, а не для работы: CPU-путь полностью поддерживается,
но генерация идёт медленно (единицы токенов в секунду).

## 2. Забрать файлы из git

### Вариант А. Весь репозиторий

```bash
git clone https://github.com/VladislavChernov/ohw.git
cd ohw
```

### Вариант Б. Только то, что нужно Dz8 (sparse checkout)

```bash
git clone --depth 1 --filter=blob:none --sparse \
  https://github.com/VladislavChernov/ohw.git
cd ohw
git sparse-checkout set dz8 dz2 infra
```

`--filter=blob:none` не скачивает содержимое остальных заданий, а
`git sparse-checkout set dz8 dz2 infra` оставляет в рабочей копии только
нужные папки. Файлы корня (`LICENSE.md`, `.gitattributes`) доступны всегда.

Что именно нужно и почему:

| Папка | Зачем |
|---|---|
| `dz8/` | сама лаборатория: код, промпты, golden-данные, страница UI, `run.ps1` / `run.sh` |
| `dz2/` | исполнитель: нормализатор, промпты и пресеты, `Dockerfile`, `compose.yaml` с сервисом `ui` |
| `infra/` | общий `ohw-ollama` и базовый образ `ohw-python:3.13` |

Проверка, что всё на месте:

```bash
ls dz8/run.sh dz2/compose.yaml infra/compose.yaml
```

```powershell
Test-Path dz8\run.ps1, dz2\compose.yaml, infra\compose.yaml
```

Все три пути должны существовать (в PowerShell — три `True`).

Позже можно добавить ещё задание, не переклонируя репозиторий:

```bash
git sparse-checkout add dz7
```

Обновление до свежей версии:

```bash
cd ohw
git pull
```

### Вариант В. Только код Dz8, без запуска

Если нужен лишь исходный код (посмотреть, сдать в виде архива), папку можно
забрать без остальных заданий:

```bash
git archive --format=zip --output dz8.zip HEAD:dz8
```

Такой архив **не запустится**: для стенда нужны `dz2/` и `infra/`. Для
запуска используйте вариант А или Б.

## 3. Поднять стенд одной командой

Windows (PowerShell), из папки `dz8`:

```powershell
cd ohw\dz8
.\run.ps1                 # auto: GPU, если Docker видит runtime nvidia, иначе CPU
.\run.ps1 -Mode gpu       # принудительно GPU
.\run.ps1 -Mode cpu       # принудительно CPU
.\run.ps1 -Rebuild        # пересобрать образ проекта
.\run.ps1 -Check          # дополнительно прогнать health и api-check
.\run.ps1 -NoPull         # не проверять и не докачивать llama3 (офлайн-демо)
```

Двойной клик по `run.cmd` делает то же самое без PowerShell-команд и оставляет
окно открытым (аргументы тоже передаются).

Если PowerShell блокирует локальные скрипты:

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

Linux / macOS:

```bash
cd ohw/dz8
./run.sh                  # CPU (Linux и macOS)
./run.sh --gpu            # Linux + NVIDIA; на macOS скрипт сам уйдёт на CPU
./run.sh --rebuild
./run.sh --check
./run.sh --no-pull
```

Если файлы пришли без бита исполнения: `bash run.sh` или
`chmod +x run.sh`.

Что скрипт делает по шагам:

1. проверяет, что Docker установлен и запущен, а рядом есть `dz2/` и `infra/`;
2. в режиме `auto` определяет GPU: `nvidia-smi` видит карту и в
   `docker info --format '{{json .Runtimes}}'` есть runtime `nvidia`;
3. вызывает существующий скрипт стенда `dz2/scripts/up-gpu.ps1` или
   `up-cpu.ps1` (`up-cpu.sh` / `up-gpu.sh` на Linux/macOS) — тот собирает
   образы и поднимает `ohw-ollama` и `dz2-ui-1`;
4. проверяет, что модель `llama3` есть в `ohw-ollama`, и при необходимости
   докачивает её (один раз, несколько ГБ);
5. печатает адрес UI и команду остановки; с флагом `-Check` / `--check`
   дополнительно запускает `health` и `api-check`.

Повторный запуск безопасен: если стенд уже здоров (`ohw-ollama` healthy,
UI отвечает) и режим совпадает, скрипт не пересоздаёт контейнеры, а сразу
проверяет модель — модель при этом не выгружается и первый ответ не ждёт
2–3 минуты. Принудительно перезапустить стенд: `-Rebuild` / `--rebuild`;
сменить режим: `-Mode cpu|gpu` / `--cpu|--gpu`.

## 4. Открыть лабораторию

1. Откройте `http://localhost:8501`.
2. В боковой панели выберите страницу **«Проверка модели»**.
3. Нажмите **«Проверить связь»**, затем работайте по
   [User Guide](user-guide.md) или по документам вкладок в
   [`docs/tabs/`](tabs/).

Первый ответ после старта может идти **2–3 минуты**: модель загружается в
память. Это не зависание.

## 5. Остановка

Windows:

```powershell
..\dz2\scripts\down.ps1          # только UI
..\dz2\scripts\down.ps1 -Infra   # ещё и общий ohw-ollama (нужен другим заданиям!)
```

Linux / macOS:

```bash
../dz2/scripts/down.sh           # только UI
../dz2/scripts/down.sh -i        # ещё и общий ohw-ollama
```

## 6. Если что-то не поднимается

| Симптом | Что сделать |
|---|---|
| `docker not found` | установить Docker Desktop / Docker Engine |
| `Docker daemon is not available` | запустить Docker Desktop или `sudo systemctl start docker` |
| `The lab needs the dz2 and infra folders` | выполнить `git sparse-checkout set dz8 dz2 infra` |
| `port is already allocated` (8501/11434) | освободить порт; `11434` принадлежит общему `ohw-ollama` |
| GPU-режим: `nvidia-smi` не видит GPU | обновить драйвер NVIDIA |
| GPU-режим: `Docker has no nvidia runtime` | установить NVIDIA Container Toolkit и перезапустить Docker |
| `ollama pull llama3` падает | проверить интернет и свободное место, повторить `docker exec ohw-ollama ollama pull llama3` |
| UI не отвечает | `docker compose -f ..\dz2\compose.yaml logs ui` (Linux: `../dz2/compose.yaml`) |
| Первый ответ идёт минутами | модель загружается в память; дождитесь завершения |

Те же ошибки с другими словами описаны в
[`dz2/scripts/README.md`](../../dz2/scripts/README.md).

## 7. Проверка без UI

```powershell
cd ohw\dz8

docker exec ohw-ollama ollama list
docker compose -f ..\dz2\compose.yaml exec -T ui `
  python -m llm_degradation health --model llama3 --json
docker compose -f ..\dz2\compose.yaml exec -T ui `
  python -m llm_degradation api-check --model llama3 --json
```

Ожидаемо: `ollama list` содержит `llama3`, `health` возвращает `"ok": true`,
`api-check` — 12 проверок без ошибок.

Полный CLI-список — в [User Guide, раздел 8](user-guide.md#8-cli).
