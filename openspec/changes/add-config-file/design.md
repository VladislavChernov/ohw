# Design: add-config-file

## Решения

### D1. TOML + tomllib, без зависимостей

`tomllib` входит в stdlib (3.11+). YAML отвергнут (нужен pyyaml), JSON — нет
комментариев. Формат файла:

```toml
[ollama]
base_url = "http://host.docker.internal:11434"
model = "qwen2.5:7b-instruct"
timeout = 180.0

[paths]
input_dir = "input"
output_dir = "output"

[generation]
temperature = 0.7
max_retries = 3
```

### D2. Слои приоритета

```
defaults < ai-testgen.toml < env OLLAMA_BASE_URL / OLLAMA_MODEL < CLI-аргументы
```

Реализация: argparse-значения по умолчанию `None`; резолвинг
`cli_arg or env or config or default` в `main()`. Диапазонные проверки
(`temperature ∈ [0;2]`, `max_retries ≥ 0`) выполняются ПОСЛЕ резолвинга.

### D3. Автопоиск и ошибки

- `--config` не указан → ищем `ai-testgen.toml` в cwd; нет файла → defaults.
- `--config` указан явно, файла нет / битый TOML / не та структура → exit 2.
- Неизвестные ключи игнорируются с warning (совместимость вперёд).

### D4. Модель: обязательна «где-нибудь»

Валидация после резолвинга всех слоёв: модель не найдена ни в env, ни в
конфиге → exit 2. В контейнере конфига обычно нет — работают `-e` флаги.

## Модули

| Модуль | Изменение |
|---|---|
| `config.py` (новый) | `Config` (frozen dataclass), `ConfigError`, `load_config()` |
| `cli.py` | `--config`, резолвинг слоёв, перенос диапазонных проверок |
