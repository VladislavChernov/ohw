# tasks.md — harden-testgen-run

## 1. Отчёты

- [x] 1.1 Парсинг итоговой строки pytest (`N passed, M failed, E errors`) в `runner.run_pytest`; fallback на подсчёт подстрок
- [x] 1.2 Добавлен `-p no:cacheprovider` в аргументы pytest
- [x] 1.3 Тесты: парсинг итоговой строки (с дублированием FAILED в short summary), fallback

## 2. Валидация покрытия + feedback-луп

- [x] 2.1 Новый `validator.py`: `find_missing(code, required) -> list[str]`
- [x] 2.2 `ollama.generate_code` принимает опциональный валидатор; при неполном покрытии повторная отправка с фидбеком
- [x] 2.3 Config/CLI: `--required-markers` (env `REQUIRED_MARKERS`), `--temperature`, `--num-predict`, `--seed`
- [x] 2.4 Тесты: обнаружение недостающих маркеров, формирование фидбека

## 3. Документация / выключение infra

- [x] 3.1 README: `infra/down.ps1` после прогона; описаны флаги семплирования
- [x] 3.2 Юнит-тесты зелёные
