# Предложения по исправлению контракта онтологии и ингеста (Вариант А)

**Superseded 2026-09-25:** этот вариант описывает typed-ontology линию и не является
актуальным контрактом. Текущий target — vector-only baseline и optional graph experiment
из `add-lightweight-context-graph`.

**Команда:** GLM  
**Дата:** 2026-09-24  
**Статус:** Историческая проработка (не исполнять как DoD)  
**Основание:** директива «Дипсик», живой прогон M5 (варнинги Neo4j), анализ `Ingest/proposals` и `Ingest/analitic/`

---

## 1. Контекст

На стадии M5/TVAL графовая ось писала в лог предупреждения, что узлов с метками из онтологии нет. Причина установлена: из YAML-профиля в граф не попадали типизированные узлы (`Requirement`/`Concept`/`Contract`) — `ExtractStage` писал все сущности одной меткой `Entity`, а рёбра строились по сырому `name`.

В ходе ревью (`ontology_contract_review.md`) и аналитики (`Ingest/analitic/`) был выбран **Вариант А**: единый уникальный ключ всех типов — `canonical_name` после нормализации `_identity_key()`, бизнес-ID `id` — опциональное свойство. Директива «Дипсик» (пункты 1-5) требует довести до ума конфиг, оркестратор, чанкинг и тесты.

**Архив документации.** В `docs/archive/v1_skeleton/` лежит старая (v1) версия docs: `00_hi_level_architecture.md`, `02_pipeline_and_normalizer.md`, `data_model.md`, `adapters_specification.md`. По `docs/archive/README.md` эти файлы **устарели и не являются основой** — их можно использовать только как историческую справку. Правки идут только в актуальных docs (корень `docs/`), старые версии не патчим.

---

## 2. Исследованные материалы

| Файл | Роль |
|---|---|
| Директива «Дипсик» | 5 пунктов ТЗ на правки (конфиг, оркестратор ×2, чанкинг, тесты) |
| `prototype/domain_profiles/domain_profile.it.yaml` | Эталонный профиль; исправлен под Вариант А |
| `prototype/domain_profiles/domain_profile.{cinema,library,lawyer,hr,shekspi}.yaml` | 5 других доменных профилей |
| `prototype/domain_profiles/ontology_matrix.csv` | Справочник инвариантов связки чанков (НЕ замена YAML) |
| `prototype/src/.../pipeline/orchestrator.py` | Стадии INGEST→COMMIT; правки откатились (git checkout), нужно писать заново |
| `prototype/src/.../pipeline/chunker.py` | Декларативные параметры стратегий (правки применены) |
| `prototype/tests/test_typed_graph.py` | Тесты типизированного графа — адаптация под `canonical_name` |
| `docs/archive/README.md` | Архив v1 — не трогаем, только справка |
| `backlog.md`, `docs/history.md` | BLG-02/BLG-03, хронология проекта |
| `Ingest/proposals/*`, `Ingest/analitic/*` | Аналитика ингеста/хранения (вне git, в `.gitignore`) |
---

> **Состояние ссылок на момент архивации (2026-09-26).** Текст сохранён как есть,
> но часть путей из раздела «Исследованные материалы» больше не существует:
> docs/archive/v1_skeleton/ и docs/archive/README.md (архив v1 не заводился),
> prototype/domain_profiles/ontology_matrix.csv (черновик, удалён как расходник).
> Доменных профилей в репозитории три — cinema, it, library; перечисленные
> в тексте lawyer, hr, shekspi не создавались. Ingest/proposals/ и
> Ingest/analitic/ больше не вне git: первый перенесён в
> docs/archive/ingest_proposals_v1/, вторая разнесена по модулям.
