# Документация: Онтология и Domain Profile

> **Версия:** v5.0  
> **Последнее обновление:** 2026-09-05

## 1. Принцип изоляции предметной области

Ядро системы (пайплайн, ретривер, СУБД) полностью изолировано от специфики конкретной индустрии. Вся доменная логика вынесена в Domain Profile (YAML).

Базовый профиль (поставляется из коробки): "it" (Requirement -> Concept -> Contract).  
Примеры расширения: "library" (литература), "cinema" (кино/рекомендации).

## 2. Структура конфигурации Domain Profile (Контракт ядра)

Каждый подключаемый YAML-профиль обязан декларировать секции:

- `profile`: name, description, language.
- `ontology`: node_types (с полем unique_key), edge_types (направления from -> to).
- `extraction`: prompt_template (ID промпта), temperature, max_tokens.
- `validation`: массив правил rules (каждое правило содержит Cypher-запрос).
- `canonicalization`: перечень слоёв трансформации для каждой категории узла.
- `chunking`: стратегии нарезки по типам источников (book/schema/poem и др.).
- `context_assembly`: template (шаблон сборки промпта) и приоритет вывода.

## 3. Автоматическая генерация Cypher constraints

При активации нового доменного профиля, Config Service парсит секцию node_types и автоматически выполняет в Neo4j команды создания индексов уникальности:

```cypher
CREATE CONSTRAINT FOR (n:{node_type}) REQUIRE n.{unique_key} IS UNIQUE;
```