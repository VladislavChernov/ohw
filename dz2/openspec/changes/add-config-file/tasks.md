# Tasks: add-config-file

- [x] 1. `config.py`: `Config`, `ConfigError`, `load_config()` (автопоиск, TOML, defaults)
- [x] 2. `cli.py`: аргумент `--config`, резолвинг слоёв, проверки после резолвинга
- [x] 3. Корневой `ai-testgen.toml` с локальными значениями по умолчанию
- [x] 4. Unit: test_config.py (полный/частичный файл, автопоиск, битый TOML, неизвестные ключи)
- [x] 5. Unit: precedence-тесты CLI (config < env < CLI; модель из конфига)
- [x] 6. README: секция «Конфигурация» с приоритетами
