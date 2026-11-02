# Демонстрация Dz7: генератор промптов

Сценарий показа ДЗ7. Предмет показа — сам генератор: шаблоны, пресеты,
детерминированная сборка промпта, `prompt_sha256` и передача промптов на стенд.

**Dz2 в этом сценарии — стенд.** Он нормализует требования, вызывает Ollama,
проверяет ответ модели и считает оценку. В ДЗ7 показываем только
`generator.py`, `templates/`, `presets/` и то, что из них собирается.

## Роли

| Компонент | Что делает | Что показываем |
|---|---|---|
| **Dz7** (`dz7/`) | собирает промпт из шаблона, требований и пресетов | `generator.py`, `templates/*.txt`, `presets/*.toml`, `output/prompts*` |
| **Dz2** (`dz2/`) | стенд: нормализация, вызов Ollama, валидация, оценка | только как исполнитель промптов |
| **Ollama** | локальная модель `qwen2.5:7b-instruct` | общий контейнер `ohw-ollama` |

## Сценарий на 10 минут

1. Показать `templates/testcases.txt` — промпт лежит текстом, а не в коде.
2. Собрать один промпт в stdout.
3. Собрать все пять и проверить, что два прогона дают одинаковый `sha256`.
4. Показать пресет `boundary-values`: как `{extra}` меняет промпт.
5. Скопировать шаблоны и пресеты на стенд, прогнать один промпт через Ollama.
6. По желанию — оценка судьёй и сверка `prompt_sha256` в JSON с файлом промпта.

## Подготовка

Все команды — PowerShell. Репозиторий: `D:\Otus\ohw`.

```powershell
cd D:\Otus\ohw

# общий Ollama; модель уже в volume, на новой машине скачается один раз
docker compose -f infra\compose.yaml --profile ollama up -d

# рабочее место стенда (для UI и для запуска CLI в контейнере)
docker compose -f dz2\compose.yaml up -d ui

docker ps --filter name=ohw-ollama --filter name=dz2-ui --format "{{.Names}} {{.Status}}"
```

Ожидаемо: `ohw-ollama` и `dz2-ui` в статусе `Up`. UI — http://localhost:8501.

### Как запускать генератор

На этой машине Python на хосте не установлен, поэтому генератор запускается в
образе `ohw-python:3.13`. Один раз определяем функцию:

```powershell
function Invoke-Dz7 {
    docker run --rm -v "D:\Otus\ohw:/work" -w /work/dz7 `
        ohw-python:3.13 python generator.py @args
}
```

Дальше все команды ниже используют `Invoke-Dz7`; хостовые пути (`Get-FileHash`,
`Get-ChildItem`) считаются от каталога `dz7`, поэтому перейдите в него:

```powershell
cd D:\Otus\ohw\dz7
```

Если Python 3.11+ установлен локально, вместо функции можно выполнять те же
аргументы из каталога `dz7`:

```powershell
cd D:\Otus\ohw\dz7
python generator.py --list-presets
```

## Где брать требования

| Что | Путь | Зачем |
|---|---|---|
| Основные требования | `dz2\docs\business-requirements_catalog-courses.md` | 7 требований `BR-1…BR-7` в таблице; это вход по умолчанию для Dz7 |
| Копия для прогона на стенде | `dz2\input\e2e\requirements.md` | тот же текст, но в отдельной папке — в прогон не попадут другие файлы |
| «Грязный» файл без ID | `dz2\examples\input\auth.md` | 5 пунктов без `BR-N`; удобно показать, зачем стенду нормализатор |
| Список готовых тест-кейсов | `dz2\input\e2e-testcases-brief.txt` | вход для матрицы покрытия |

По умолчанию `--input-dir` указывает на `../dz2/docs`, поэтому имя
`business-requirements_catalog-courses.md` можно передавать без пути.

## Демо 1. Один промпт из шаблона

Сначала показать сам шаблон:

```powershell
Get-Content D:\Otus\ohw\dz7\templates\testcases.txt -Encoding UTF8
```

В нём видны плейсхолдеры `{url}`, `{count}`, `{positive}`, `{negative}`,
`{requirement_rule}`, `{extra}`, `{content}`. Строки, начинающиеся с `#`, —
служебные и в готовый промпт не попадают.

Собрать промпт в stdout:

```powershell
Invoke-Dz7 --requirements-file business-requirements_catalog-courses.md `
    --type testcases --count 10
```

Ожидаемое начало:

```text
Target site: https://otus.ru/catalog/courses?categories=programming

Generate exactly 10 test cases: 6 positive and 4 negative.

Respond with a JSON object {"cases": [...]} containing all 10 test cases.
Each test case must have: id, type (positive/negative), title, preconditions, steps, expected, requirement.

Every test case MUST set its 'requirement' field to the business requirement id (BR-N) from the Requirements section that it verifies.
```

Что проговорить:

- `{count}`, `{positive}`, `{negative}` подставлены по правилу
  `target_negative_count` — 6 позитивных и 4 негативных;
- `{requirement_rule}` появился потому, что в требованиях есть `BR-N`;
- `{content}` — это весь файл требований, дословно;
- `{extra}` пуст, потому что пресет не выбран;
- JSON-пример `{"cases": [...]}` не сломан подстановкой.

Если взять файл без ID, правило трассировки исчезнет — это честное поведение,
а не ошибка:

```powershell
Invoke-Dz7 --input-dir ../dz2/examples/input --requirements-file auth.md `
    --type testcases --count 5
```

В промпте не будет строки `Every test case MUST set its 'requirement'...`,
потому что в `auth.md` нет `BR-N`. На стенде это лечится нормализацией.

## Демо 2. Все пять промптов и детерминизм

```powershell
New-Item -ItemType Directory -Force -Path output\demo\run1, output\demo\run2 | Out-Null

Invoke-Dz7 --requirements-file business-requirements_catalog-courses.md `
    --count 10 --all-types --output-dir output/demo/run1

Invoke-Dz7 --requirements-file business-requirements_catalog-courses.md `
    --count 10 --all-types --output-dir output/demo/run2
```

Проверить, что прогоны совпадают байт-в-байт:

```powershell
Get-ChildItem output\demo\run1\*.txt | ForEach-Object {
    $other = Join-Path output\demo\run2 $_.Name
    [pscustomobject]@{
        Prompt = $_.Name
        Same   = (Get-FileHash $_.FullName).Hash -eq (Get-FileHash $other).Hash
        Sha256 = (Get-FileHash $_.FullName).Hash.ToLower().Substring(0, 12)
    }
} | Format-Table -AutoSize
```

Ожидаемо на этом коммите:

```text
Prompt              Same Sha256
------              ---- ------
checklist.txt       True 13818e8d9f1e
coveragematrix.txt  True 25e5055a1a64
qareport.txt        True 608f70ba8387
testcases.txt       True 115c9d6c0b73
testplan.txt        True 3b5254886521
```

Все `Same = True`: в сборке промпта не участвует модель, поэтому одинаковые
входы дают одинаковые байты. Числа `Sha256` зависят от шаблонов и `--count`;
важен не конкретный хеш, а совпадение двух прогонов.

В этом рабочем каталоге готовые файлы уже лежат в `dz7\output\prompts\` — они
собраны этой же командой (текущие шаблоны, `--count 10`). Их можно открыть и
показать без ожидания. После чистого клона каталог `output/` пуст: те же файлы
создаёт команда выше.

Что важно: детерминирован **промпт**, а не ответ модели. Артефакт при
`temperature=0.7` каждый раз новый; фиксирован только вход.

## Демо 3. Пресеты и плейсхолдер `{extra}`

Список пресетов:

```powershell
Invoke-Dz7 --list-presets
```

```text
accessibility: Доступность [testcases, checklist, qareport]
boundary-values: Граничные значения [testcases, checklist, coveragematrix]
input-validation: Валидация ввода и безопасность [testcases, checklist, testplan]
localization: Локализация и тексты [testcases, checklist]
negative-scenarios: Негативные сценарии [testcases, checklist, qareport]
regression: Регрессия [testplan, qareport, checklist]
```

Показать файл пресета:

```powershell
Get-Content D:\Otus\ohw\dz7\presets\boundary-values.toml -Encoding UTF8
```

Пресет — это TOML с `id`, `label`, `types`, `text`. Он не заменяет шаблон,
а добавляет инструкции в `{extra}`. Собрать промпт с пресетом:

```powershell
Invoke-Dz7 --requirements-file business-requirements_catalog-courses.md `
    --type testcases --count 10 --preset boundary-values
```

В промпте появится блок:

```text
Additional instructions:
- Apply them only to the requirements below; do not add pages, features,
  products or data the requirements do not mention, and do not change
  the required output format.
- Add boundary-value scenarios derived from the requirements below: an empty
  result set, exactly one item, ... Do not invent login, cart, checkout or other
  pages that the requirements do not mention.
```

При этом обязательный формат не меняется: те же 10 кейсов, 6 позитивных и
4 негативных, тот же JSON и то же правило `BR-N`.

Показать защиту от неверного выбора: пресет для неподдерживаемого типа — это
ошибка, а не молчаливый пропуск.

```powershell
Invoke-Dz7 --requirements-file business-requirements_catalog-courses.md `
    --type testplan --preset localization

$LASTEXITCODE
```

Ожидаемо:

```text
error: preset 'localization' does not apply to testplan (types: testcases, checklist)
2
```

Так же ведёт себя `--all-types` с пресетом, который поддерживают не все пять
типов: команда завершится кодом 2 на первом неподходящем типе. Поэтому в
сквозных прогонах пары «тип + пресет» выбираются явно (см. ниже).

Готовые промпты с пресетами — в `dz7\output\prompts-presets\`.

## Демо 4. Передача на стенд и прогон через Ollama

Скопировать шаблоны и пресеты на стенд. Копирование байт-в-байт — это контракт
ДЗ7: стенд должен исполнять ровно те шаблоны, которые лежат в `dz7/templates`.

```powershell
cd D:\Otus\ohw\dz7
Invoke-Dz7 --emit-templates --output-dir ../dz2/output/demo/prompts
Invoke-Dz7 --emit-presets  --output-dir ../dz2/output/demo/presets
```

Проверить, что копия совпадает с `dz2\prompts` (штатное место стенда):

```powershell
(Get-FileHash ..\dz2\output\demo\prompts\testcases.txt).Hash -eq `
(Get-FileHash ..\dz2\prompts\testcases.txt).Hash
```

Ожидаемо: `True`.

Запустить один промпт на стенде. `--prompt-dir` и `--presets-dir` указывают на
свежую копию, `--input-dir` — на файл с семью требованиями:

```powershell
cd D:\Otus\ohw\dz2
New-Item -ItemType Directory -Force -Path output\demo\run | Out-Null

docker compose run --rm app --count 2 --timeout 600 `
    --type testcases --preset boundary-values `
    --prompt-dir /data/output/demo/prompts `
    --presets-dir /data/output/demo/presets `
    --input-dir /data/input/e2e `
    --output-dir /data/output/demo/run
```

Ожидаемый вывод:

```text
written: /data/output/demo/run/requirements.testcases.md (2 test cases)
```

На хосте: `dz2\output\demo\run\requirements.testcases.md`.

Что показать в файле:

- кейсы на русском;
- поле `**Проверяет требование:** BR-N` — трассировка на требования;
- ровно 2 кейса, потому что в демо стоит `--count 2` (для полного прогона —
  `--count 10`).

Почему `--timeout 600`: на CPU модель 7B не всегда отвечает за дефолтные 180 с.
Первый ответ может занять 1–3 минуты. На GPU быстрее.

Если показывать UI стенда, а не CLI: http://localhost:8501 → вкладка
«Требования» → загрузить `dz2\input\e2e\requirements.md` → вкладка «Промпт».
Там видно, как тот же шаблон и пресет собраны в запрос. Это стенд, не предмет
ДЗ7.

## Демо 5. Оценка и сверка `prompt_sha256` (по желанию)

Оценка — это тоже стенд: судья читает собранный промпт и ставит баллы по
четырём критериям. Генератор лишь поставляет текст.

```powershell
cd D:\Otus\ohw\dz2
New-Item -ItemType Directory -Force -Path output\demo\eval | Out-Null

docker compose run --rm app -n 10 --timeout 600 --evaluate `
    --type testcases --preset boundary-values `
    --prompt-dir /data/output/demo/prompts `
    --presets-dir /data/output/demo/presets `
    --input-dir /data/input/e2e `
    --output-dir /data/output/demo/eval
```

Появится файл:

```text
dz2\output\demo\eval\quality\testcases-boundary-values-<run-id>.json
```

В нём:

- `judge_version: 5`, `judge_temperature: 0.0`;
- `scores` и `comments` по четырём критериям, комментарии по-русски;
- `evidence` — дословные цитаты из промпта;
- `prompt_sha256` — хеш ровно того промпта, который оценивался.

Сверка: собрать тот же промпт генератором и сравнить хеши.

```powershell
cd D:\Otus\ohw\dz7

Invoke-Dz7 --requirements-file business-requirements_catalog-courses.md `
    --count 10 --type testcases --preset boundary-values `
    --output output/demo/eval/testcases-boundary-values.txt

(Get-FileHash output\demo\eval\testcases-boundary-values.txt).Hash.ToLower()

$quality = Get-ChildItem ..\dz2\output\demo\eval\quality\testcases-boundary-values-*.json |
    Sort-Object LastWriteTime | Select-Object -Last 1
(Get-Content $quality.FullName -Raw | ConvertFrom-Json).prompt_sha256
```

Обе строки должны совпасть (на текущих шаблонах — `4851afbb2f48...`). Это
доказывает, что стенд оценивал именно промпт ДЗ7, а не «примерно такой же».

Если оценка на демо не нужна, показать готовые JSON из прошлого прогона:
`dz2\output\e2e-all\quality\` и `dz2\output\e2e-presets\*\quality\`. Там
`judge_version: 4`; версия 5 пересчитывает комментарии по-русски и не ставит
балл ниже без конкретного пропуска. Старый пример «до» —
`dz2\output\quality\testcases-20261101T082645415295Z.json`.

## Ожидаемые артефакты

| Артефакт | Путь |
|---|---|
| Промпты всех пяти типов | `dz7\output\prompts\<type>.txt` |
| Промпты с пресетами | `dz7\output\prompts-presets\<type>-<preset>.txt` |
| Два прогона для проверки детерминизма | `dz7\output\demo\run1\`, `run2\` |
| Копия шаблонов для стенда | `dz2\output\demo\prompts\` |
| Копия пресетов для стенда | `dz2\output\demo\presets\` |
| Тест-кейсы от Ollama | `dz2\output\demo\run\requirements.testcases.md` |
| Оценка судьи | `dz2\output\demo\eval\quality\*.json` |

## Если что-то не поднялось

```powershell
# Ollama и стенд
docker compose -f infra\compose.yaml --profile ollama up -d
docker compose -f dz2\compose.yaml up -d ui
docker exec ohw-ollama ollama ls

# ошибка "python not found" на хосте — используйте функцию Invoke-Dz7 из начала
# ошибка "network ohw_net not found" — сначала поднимите infra/ollama

# таймаут генерации — повторите с --timeout 600 (или 900 на медленном CPU)

# ошибка "prompt template not found" — запускайте из каталога dz7
# или передайте --templates-dir с абсолютным путём
```

Если файл в PowerShell выглядит «кракозябрами», это кодировка консоли, а не
файла: откройте его в VS Code или выполните `chcp 65001`. Генератор пишет
UTF-8.

## Что именно проверяет тест

```powershell
cd D:\Otus\ohw

docker run --rm -v "D:\Otus\ohw\dz7:/src" -w /src `
    ohw-python:3.13 python -m unittest discover -s tests -q
```

39 тестов: подстановка всех плейсхолдеров, отсутствие «протёкших» `{...}`,
валидность JSON-примера, правило `BR-N`, удаление строки URL, ошибки
неизвестного типа/шаблона, `--emit-templates`, `--emit-presets`, `--all-types`,
пресеты и `{extra}`, чтение требований из файла и конфига.
