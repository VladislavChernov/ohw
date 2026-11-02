# Демонстрация Dz2: нормализатор, генерация и оценка

Пошаговый сценарий показа: что запустить, какие файлы открыть и что должно
получиться. Все команды — PowerShell, из корня `D:\Otus\ohw`, если не указано
иное. Docker Desktop должен быть запущен.

## Что показываем

1. Настройки нормализатора: отдельный `normalizer.toml` + блок в UI.
2. Нормализацию «грязного» файла без ID: кодировка, заголовок, `BR-N`.
3. Смену префикса ID (`BR` → `REQ`) без правки кода.
4. Сборку промпта с трассировкой на требования.
5. Генерацию тест-кейсов через локальную Ollama.
6. Оценку качества судьёй v5: русские комментарии, адекватные баллы.

## Подготовка

```powershell
cd D:\Otus\ohw

# общий Ollama (модель уже в volume; на новой машине скачается один раз)
docker compose -f infra\compose.yaml --profile ollama up -d

# рабочее место; при первом запуске соберёт образ
docker compose -f dz2\compose.yaml up -d ui

docker ps --filter name=ohw-ollama --filter name=dz2-ui --format "{{.Names}} {{.Status}}"
```

Открыть **http://localhost:8501**. Дождаться заголовка «Рабочее место: тесты по
требованиям».

Важно: на CPU модель 7B не всегда укладывается в дефолтные 180 секунд. В
командах ниже стоит `--timeout 600`; в UI таймаут поднимается в «Параметрах
генерации» в сайдбаре.

## Демо 1. Нормализатор в UI (без модели, ~2 минуты)

1. Вкладка **«1. Требования»** → **Browse files** →
   `D:\Otus\ohw\dz2\examples\input\auth.md`.
2. Если загружено несколько файлов, выбрать `auth.md` в списке «Файл требований».
3. Показать метрики: «Требований с ID — 0», «URL — https://www.saucedemo.com».
4. Ниже — блок **«Нормализация требований»**. Раскрыть **«Настройки
   нормализации»**: шесть полей, значения по умолчанию взяты из
   `normalizer.toml`:
   - префикс ID — `BR`;
   - заголовок раздела — `Requirements`;
   - подпись URL — `URL`;
   - запасной заголовок — `Требования`;
   - кодировки — `cp1251`, `cp866`;
   - минимальная длина абзаца — `20`.
5. Нажать **«Нормализовать»**. Ожидаемо: 5 требований, кодировка `utf-8`,
   стратегия «список», без предупреждений.
6. Вкладка **«Нормализованный»**: заголовок, `**URL:**`, `## Requirements`,
   `### BR-1 … BR-5`.
7. Сменить «Префикс ID требований» на `REQ`. Появится предупреждение, что
   настройки изменены. Нажать **«Нормализовать»** ещё раз → `### REQ-1 … REQ-5`.
8. Включить **«Использовать нормализованный текст в промптах»** и перейти на
   вкладку **«2. Промпт»**: в промпте видны `### REQ-N` и правило про поле
   `requirement`.

Если хочется показать, что файл — источник значений по умолчанию: открыть
`dz2\normalizer.toml`, поменять `id_prefix` на `REQ`, нажать «Вернуть значения из
файла» — виджет перечитает файл. Глоссарий по ключам —
[`NORMALIZER-SETTINGS.md`](NORMALIZER-SETTINGS.md).

## Демо 2. CLI-нормализация (без модели)

```powershell
cd D:\Otus\ohw\dz2
docker run --rm `
  -v "D:\Otus\ohw\dz2\examples\input:/in:ro" `
  -v "D:\Otus\ohw\dz2\output\demo:/out" `
  ohw/dz2:local --normalize-only --input-dir /in --output-dir /out
```

Ожидаемый вывод:

```
normalized: /out/normalized/auth.md (utf-8, 5 requirements, strategy: list)
warning: cart.txt: структура не распознана: требования выделены по абзацам, проверьте результат
normalized: /out/normalized/cart.md (utf-8, 1 requirements, strategy: paragraph)
normalized: /out/normalized/login.md (utf-8, 4 requirements, strategy: heading)
```

Результаты: `dz2\output\demo\normalized\auth.md`, `cart.md`, `login.md`.
`cart.txt` — пример ограничения: список без заголовка требований уходит в
резервную стратегию «абзацы» с предупреждением.

Нормализация реальных требований:

```powershell
docker compose run --rm app --normalize-only `
  --input-dir /data/input/e2e --output-dir /data/output/demo
```

Ожидаемо: `7 requirements, strategy: table` →
`dz2\output\demo\normalized\requirements.md`.

## Демо 3. Определение кодировки cp1251 (по желанию)

```powershell
New-Item -ItemType Directory -Force D:\Otus\ohw\dz2\output\demo | Out-Null
$src = Get-Content -Raw -Encoding UTF8 D:\Otus\ohw\dz2\examples\input\auth.md
[IO.File]::WriteAllBytes(
  "D:\Otus\ohw\dz2\output\demo\auth-cp1251.md",
  [Text.Encoding]::GetEncoding(1251).GetBytes($src)
)
```

Загрузить `auth-cp1251.md` в UI. Под полем появится «Кодировка файла: cp1251 —
текст перекодирован в UTF-8», нормализация даст те же 5 требований. Флаг
`-Encoding UTF8` обязателен: Windows PowerShell иначе прочитает UTF-8 как ANSI и
испортит текст.

## Демо 4. Генерация тест-кейсов

```powershell
cd D:\Otus\ohw\dz2
docker compose run --rm app --count 10 --timeout 600 `
  --input-dir /data/input/e2e --output-dir /data/output/demo
```

Результат: `dz2\output\demo\requirements.md` — тест-кейсы с полями `id`, `type`,
`title`, `preconditions`, `steps`, `expected`, `requirement`. Поле
`**Проверяет требование:** BR-N` подтверждает трассировку.

Первый ответ 7B на CPU может занять 1–3 минуты. Если генерация на демо не нужна,
показать готовые артефакты: `dz2\output\e2e-all\` (пять типов и `quality/*.json`).

## Демо 5. Оценка качества судьёй v5 (по желанию)

```powershell
docker compose run --rm app -n 10 --timeout 600 --evaluate `
  --input-dir /data/input/e2e --output-dir /data/output/demo-eval

Get-ChildItem dz2\output\demo-eval\quality
```

В JSON: `"judge_version": 5`, `comments` по-русски, `evidence` — дословные цитаты
из промпта. Для сравнения «до»: `dz2\output\quality\testcases-20261101T082645415295Z.json`
(`judge_version: 4`, английский, все баллы 3).

Что изменилось в v5: судья обязан искать элемент по всему промпту, не объявлять
присутствующее отсутствующим, называть конкретный отсутствующий элемент перед
баллом 3 и писать комментарий по-русски. Остаточное ограничение: 7B может
поставить 3 за реальную причину (повторы в промпте, отсутствие граничных
сценариев).

## Где брать требования

| Что | Путь |
|---|---|
| Исходные бизнес-требования | `dz2\docs\business-requirements_catalog-courses.md` |
| Та же выгрузка для прогона | `dz2\input\e2e\requirements.md` (7 требований, таблица) |
| Готовые тест-кейсы для матрицы покрытия | `dz2\input\e2e-testcases-brief.txt` |
| Демо-файлы нормализатора | `dz2\examples\input\auth.md`, `login.md`, `cart.txt` |
| Пресеты | `dz2\presets\*.toml` |
| Шаблоны промптов | `dz2\prompts\*.txt` |
| Настройки нормализатора | `dz2\normalizer.toml` |
| Глоссарий настроек | `dz2\NORMALIZER-SETTINGS.md` |

## Ожидаемые артефакты

- нормализация: `<output-dir>/normalized/<stem>.md`;
- артефакты генерации: `<output-dir>/<stem>.md` (в режиме `--all-types` —
  `<stem>.<type>.md`);
- оценки: `<output-dir>/quality/<type>-<run-id>.json`;
- готовый e2e-набор: `dz2\output\e2e-all\`, `dz2\output\e2e-presets\*`.

## Если что-то не поднялось

```powershell
docker compose -f dz2\compose.yaml logs ui --tail 50
docker compose -f dz2\compose.yaml up -d --build ui
docker compose -f infra\compose.yaml --profile ollama up -d
docker exec ohw-ollama ollama ls
```

Если файл открывается «кракозябрами» в PowerShell — это кодировка консоли, а не
файла: откройте результат в VS Code или задайте `chcp 65001`. Нормализатор
пишет UTF-8.
