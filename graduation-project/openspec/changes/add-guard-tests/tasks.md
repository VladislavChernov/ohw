# Чек-лист: guard-тесты

- [x] `proposal.md`
- [x] `tests/test_docs_encoding.py` — UTF-8, `U+FFFD`, управляющие символы
- [x] `tests/test_launchers.py` — ASCII для `run.ps1` и оберток, контракт `compose`,
      `sh -n`, передача аргументов в `run-cpu.ps1` / `run-gpu.ps1`
- [x] README: упоминание guard-тестов
- [x] `uv run pytest` — 93 passed, 1 skipped
- [x] `ruff check src scripts tests`
- [x] `mypy src`
- [x] Коммит и push
