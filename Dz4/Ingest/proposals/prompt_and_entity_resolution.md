# Системный промпт для LLM и логика Entity Resolution

Этот документ содержит финальные компоненты для построения мультидоменного конвейера ингеста: системный промпт для извлечения графа (Prompt Engineering) и архитектуру модуля разрешения сущностей (Entity Resolution).

---

## 1. Системный промпт (Prompt Engineering)

Данный промпт предназначен для использования с моделями, поддерживающими Structured Outputs (JSON Schema / Pydantic). Он заставляет модель строго следовать структуре данных и находить сквозные связи.

```text
Вы — специализированный AI-инженер по анализу текстовых данных и извлечению знаний (Knowledge Graph Extraction). 
Ваша задача — проанализировать входящий текст, определить вовлеченные операционные домены (LEGAL, IT, HR, CROSS_DOMAIN) и извлечь структурированный граф в виде узлов (Nodes) и связей (Edges) в строгом соответствии с заданной JSON-схемой.

### ИНСТРУКЦИИ ПО АНАЛИЗУ:
1. Выделите ключевые сущности в тексте и сопоставьте их с типами узлов:
   - LEGAL: Case (Дела), Document (Документы).
   - IT: Incident (Инциденты), Asset (Инфраструктура/Серверы/ПО).
   - HR: Department (Департаменты), EmploymentEvent (Кадровые события).
   - CROSS_DOMAIN: Person (Люди, сотрудники, контрагенты, судьи).

2. Определите свойства для каждого узла. Переносите только факты из текста. Не придумывайте ID, используйте нормализованные текстовые ключи (например, "иван_иванов", "case_2026_007") или системные ID, если они явно указаны.

3. Извлеките направленные связи (Edges) между узлами. Всегда связывайте доменные узлы со сквозными узлами (например, Person), чтобы обеспечить целостность мультидоменного графа. Для каждой связи обязательно укажите тип из разрешенного списка:
   - [:ROLE] -> от Person к Case или Department. Обязательно заполняйте свойство role_type ("Адвокат", "Разработчик").
   - [:PARTICIPATED_IN] -> от Person к EmploymentEvent или Incident.
   - [:BELONGS_TO] -> от доменных узлов к главным хабам (например, Document -> Case или Incident -> Asset).
   - [:NEXT_VERSION] -> для связи старых версий документов/тикетов с новыми.
   - [:TRIGGERED_BY] -> для междоменных связей (например, Судебный иск спровоцирован ИТ-инцидентом).

### ПРАВИЛА ВАЛИДАЦИИ:
- Запрещено создавать связи к несуществующим узлам. Каждый source_id и target_id в массиве 'edges' должен строго соответствовать полю 'id' одного из узлов в массиве 'nodes'.
- Соблюдайте регистр типов узлов и связей.
- Если сущность упоминается в тексте под разными синонимами, используйте для нее один и тот же 'id' на этапе генерации.
- Выдайте ответ строго в формате JSON, соответствующем Pydantic-модели ExtractedGraph. Любой текст вне JSON-структуры запрещен.
```

---

## 2. Логика Entity Resolution (Разрешение сущностей)

Модуль Entity Resolution (ER) предотвращает появление дубликатов в графовой базе данных (Neo4j/Memgraph) при слиянии данных из разных источников. Ниже представлена логика работы Python-скрипта ингеста перед выполнением `MERGE`-запросов.

### Стратегия дедупликации (Конвейер ER)

```
[JSON от LLM] 
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ ЭТАП А: Дедупликация по строгим бизнес-ключам         │
│ • Проверка уникальных ID (Email, СНИЛС, ИНН, Jira ID)  │
└────────────────────────────────────────────────────────┘
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ ЭТАП Б: Семантическое выравнивание (Векторный поиск)   │
│ • Сравнение эмбеддингов для текстовых названий        │
│ • Порог схожести (Cosine Similarity > 0.88)            │
└────────────────────────────────────────────────────────┘
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ ЭТАП В: Безопасная запись через Cypher (MERGE)         │
│ • Генерация атомарных транзакций                       │
└────────────────────────────────────────────────────────┘
```

### Реализация алгоритма на Python

Ниже приведен концептуальный код на Python с использованием библиотеки `neo4j` и векторных эмбеддингов (на примере абстрактного `embedding_service`), демонстрирующий логику дедупликации узлов типа `Person` и `Case`.

```python
from neo4j import GraphDatabase
import numpy as np

class EntityResolutionIngestor:
    def __init__(self, uri, auth):
        self.driver = GraphDatabase.driver(uri, auth=auth)
        
    def close(self):
        self.driver.close()

    def get_embedding(self, text: str):
        # Абстрактный вызов модели эмбеддингов (например, OpenAI text-embedding-3-small)
        # Возвращает массив float
        return embedding_service.get_vector(text)

    def resolve_person(self, tx, person_node: dict) -> str:
        """
        Этап А: Строгое разрешение сущности Person по бизнес-ключам.
        """
        props = person_node.get("properties", {})
        email = props.get("email")
        
        if email:
            # Проверяем, есть ли уже в базе человек с таким email
            result = tx.run(
                "MATCH (p:Person {email: $email}) RETURN p.id AS id", 
                email=email
            )
            record = result.single()
            if record:
                return record["id"] # Найдено точное совпадение
                
        # Если строгих ключей нет или совпадений не найдено, генерируем новый или используем ID от LLM
        return person_node["id"]

    def resolve_case_vector(self, tx, case_node: dict, threshold=0.88) -> str:
        """
        Этап Б: Семантическое разрешение сущностей (для названий дел/инцидентов).
        """
        title = case_node["properties"].get("title", "")
        new_embedding = self.get_embedding(title)
        
        # Ищем в базе все дела для проверки семантической близости
        result = tx.run("MATCH (c:Case) WHERE c.embedding IS NOT NULL RETURN c.id AS id, c.embedding AS embedding")
        
        for record in result:
            db_embedding = np.array(record["embedding"])
            # Считаем косинусное сходство
            similarity = np.dot(new_embedding, db_embedding) / (np.linalg.norm(new_embedding) * np.linalg.norm(db_embedding))
            
            if similarity > threshold:
                return record["id"] # Найдено семантическое совпадение (дело уже существует под похожим именем)
                
        return case_node["id"]

    def save_graph_to_neo4j(self, extracted_graph: dict):
        """
        Этап В: Запись графа в БД с использованием разрешенных ID.
        """
        id_mapping = {} # Карта соответствия: старый_id_от_LLM -> реальный_id_в_БД

        with self.driver.session() as session:
            # 1. Сначала обрабатываем и записываем все узлы
            for node in extracted_graph["nodes"]:
                with session.begin_transaction() as tx:
                    if node["type"] == "Person":
                        resolved_id = self.resolve_person(tx, node)
                    elif node["type"] == "Case":
                        resolved_id = self.resolve_case_vector(tx, node)
                    else:
                        resolved_id = node["id"]
                        
                    id_mapping[node["id"]] = resolved_id
                    
                    # Выполняем MERGE операцию
                    cypher_query = f"""
                    MERGE (n:{node['type']} {{id: $id}})
                    ON CREATE SET n += $properties
                    ON MATCH SET n += $properties
                    """
                    # Если есть вектор, добавляем его в свойства
                    properties = node["properties"]
                    if node["type"] == "Case":
                        properties["embedding"] = self.get_embedding(properties.get("title", ""))
                        
                    tx.run(cypher_query, id=resolved_id, properties=properties)
                    tx.commit()

            # 2. Затем прокладываем связи между разрешенными ID
            for edge in extracted_graph["edges"]:
                resolved_source = id_mapping.get(edge["source_id"])
                resolved_target = id_mapping.get(edge["target_id"])
                
                if not resolved_source or not resolved_target:
                    continue # Пропускаем, если один из узлов не был успешно создан
                    
                with session.begin_transaction() as tx:
                    # Динамическое имя связи подставляется безопасно через строку, 
                    # так как типы связей жестко валидируются на этапе Pydantic/LLM
                    cypher_edge_query = f"""
                    MATCH (source {{id: $source_id}})
                    MATCH (target {{id: $target_id}})
                    MERGE (source)-[r:{edge['type']}]->(target)
                    ON CREATE SET r += $properties
                    ON MATCH SET r += $properties
                    """
                    tx.run(cypher_edge_query, 
                           source_id=resolved_source, 
                           target_id=resolved_target, 
                           properties=edge.get("properties", {}))
                    tx.commit()
