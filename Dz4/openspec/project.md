# Project: GraphRAG прототип (ДЗ4)

Учебный проект (Otus, ДЗ4): доменно-агностичная GraphRAG-платформа на локальном
железе. Прототип валидирует гипотезы (eval-гейт ADR-015) на трёх доменах
(IT, библиотека, кино) на RTX 2070 Super (8 ГБ VRAM) + 16 ГБ RAM.

## Стек (прототип, ADR-001…020)

- Python 3.11+, uv, FastAPI (ИИ-контур; Query API :8000 — Python на прототипе, ADR-020).
- Neo4j Community — выбранный backend прототипа для graph/vector; graph и vector
  подключаются через независимые `GraphStoreProvider`/`VectorStoreProvider` и не требуют
  общей Neo4j-транзакции. Ollama + qwen2.5:7b-instruct, bge-m3, bge-reranker-base.
- Valkey / Redis Streams (Task Queue, веха 2), SQLite + YAML (Config/Glossary), Docker Compose.
- Собственная сеть `ohw_net`, собственные volumes. Проект полностью автономный
  (не использует общий `<local-path>`, shared ollama или `ohw_kit`).

## Основные соглашения

- Spec-first: изменения начинаются с OpenSpec-бандла (`openspec/changes/<slug>/`),
  код и тесты — после.
- Верификация задачи (= done) — зелёные pytest/ruff/mypy, а не «код смотрится ок».
- `/review` — adversarial-ревью diff перед коммитом (агент `reviewer`, только читает).
- `Proposals/` — непроектные идеи; перед переносом в контракт проходят `add-proposal-review-gate`
  с решением `accepted/partial/deferred/rejected`.
- Python локально отсутствует: toolchain гоняется в dev-container (VS Code,
  `prototype/.devcontainer`/корневом) или на ВМ для запуска.

## Структура

- `docs/` — живая документация (SSOT: `docs/00`–`06`, ADR, спeки контрактов).
- `openspec/` — спеки изменений (по вехам).
  Активные lifecycle-контракты: `add-lightweight-context-graph` и `add-offline-graph-projection`.
- `prototype/` — код и контур прототипа (compose, config, domain_profiles, src).