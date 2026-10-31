# Design: как Dz2 читает промпты и рендерит новые типы

## Контракт файлов

```
dz7/templates/<type>.txt  ──(--emit-templates / --all-types)──►
dz2/prompts/<type>.txt    ──(render)──► user prompt ──► Ollama
```

`<type>` — значения `ArtifactType`:
`testcases`, `checklist`, `testplan`, `qareport`, `coveragematrix`.

## Подстановка

`prompt.py`:

- `strip_template_comments()` срезает ведущий блок `#`-строк;
- `render_template()` заменяет `\{([a-z][a-z0-9_]*)\}`; `str.format`
  не используется, потому что в шаблоне есть литеральный `{"cases": [...]}`;
- `build_user_prompt()` собирает значения, удаляет строку
  `Target site: {url}` при неизвестном URL, схлопывает лишние пустые
  строки и падает на оставшихся плейсхолдерах.

| Плейсхолдер | Значение |
|---|---|
| `{url}` | URL или удаление строки |
| `{count}`, `{count_noun}`, `{positive}`, `{negative}` | число кейсов и слово «test case(s)» |
| `{requirement_rule}` | правило BR-N или пусто |
| `{content}` | текст требований |

## Поиск каталога

`resolve_prompt_dir(explicit)`:

1. `--prompt-dir` — строго;
2. `DZ2_PROMPT_DIR` — строго;
3. `./prompts`;
4. `<repo>/prompts` (editable-установка, тесты);
5. `<package>/prompts`.

Любая ошибка → `PromptError` → `EXIT_USAGE` (2) до создания клиента Ollama.

## Обработчики

`ArtifactHandler` восстановлен; `get_handler(type, prompt_dir)` загружает
шаблон и собирает обработчик:

| Тип | `wants_json` | Валидатор |
|---|---|---|
| `testcases` | да | pydantic-схема + ограничения набора |
| `checklist` | нет | markdown: пункты под каждой зоной |
| `testplan` | нет | markdown: 5 фиксированных разделов |
| `qareport` | нет | markdown: 4 фиксированных раздела |
| `coveragematrix` | нет | markdown: раздел + таблица |

## Валидация матрицы

`validate_markdown(..., require_table=True)` ищет строку-разделитель
Markdown-таблицы (`|---|---|`). Строка данных `| BR-1 | ... |` разделителем
не считается. Раздел — `## Матрица покрытия требований`.

## Docker

- `Dockerfile`: `COPY prompts /app/prompts`,
  `ENV DZ2_PROMPT_DIR=/app/prompts` (нередактируемая установка больше не
  зависит от `__file__`).
- `compose.yaml`: `./prompts:/app/prompts:ro` — подмена без пересборки.
- `dz2/prompts/*.txt` — дефолтные шаблоны в репозитории; Dz7 может их
  перезаписать через `--emit-templates`.

## Требования

Входная директория — `dz2/input` (в `.gitignore`). Демо-требования лежат в
`dz2/examples/input/` (в т.ч. `login.md` с BR-1…BR-4). Dz7 по умолчанию
читает `../dz2/input`.
