# Интеграция с turbopuffer: Архитектура Dual-Write и Hybrid Search

## 1. Концепция Интеграции
- **Графовая БД (Neo4j/Memgraph):** Хранит только «скелет» данных — узлы сущностей (Дела, Инциденты, Люди) и их связи. Текст документов внутрь графа не пишется.
- **Векторная БД (turbopuffer):** Хранит разбитый на чанки тяжелый текст документов, эмбеддинги чанков и теги метаданных в мелких изолированных неймспейсах.

## 2. Пример реализации на Python (Конвейер записи и поиска)

```python
import json
# Симулируем клиенты баз данных
class MultiDomainIngestPipeline:
    def __init__(self, graph_client, puffer_client, embedding_client):
        self.graph = graph_client
        self.puffer = puffer_client
        self.embed = embedding_client

    def ingest_document(self, doc_id, text, metadata, connections):
        """
        Этап 1: Запись топологии в Графовую БД
        """
        # Создаем узел документа и его мета-связи в графе
        self.graph.run("""
            MERGE (d:Document {id: $doc_id})
            SET d.name = $name, d.domain = $domain
        """, doc_id=doc_id, name=metadata['name'], domain=metadata['domain'])
        
        for conn in connections:
            self.graph.run(f"""
                MATCH (d:Document {{id: $doc_id}})
                MERGE (target:{conn['label']} {{id: $target_id}})
                MERGE (d)-[:{conn['rel_type']}]->(target)
            """, doc_id=doc_id, target_id=conn['target_id'])

        """
        Этап 2: Запись текстовых чанков с тегами в turbopuffer (Изолированный Namespace)
        """
        chunks = self.split_text_to_chunks(text)
        namespace = f"domain_{metadata['domain'].lower()}_docs"
        
        puffer_data = []
        for i, chunk in enumerate(chunks):
            vector = self.embed.get_embedding(chunk)
            puffer_data.append({
                "id": f"{doc_id}_chunk_{i}",
                "vector": vector,
                "attributes": {
                    "doc_id": doc_id,
                    "tags": metadata.get('tags', []),
                    "text_content": chunk # Храним исходный текст прямо в атрибутах чанка
                }
            })
            
        # Запись напрямую в S3-backed индекс turbopuffer
        self.puffer.namespace(namespace).upsert(puffer_data)

    def hybrid_search(self, query, domain, required_tag, limit=5):
        """
        Двухэтапный гибридный поиск (Graph + Vector RAG)
        """
        query_vector = self.embed.get_embedding(query)
        namespace = f"domain_{domain.lower()}_docs"
        
        # 1. Поиск релевантных чанков в turbopuffer с фильтрацией по инвертированному индексу тегов
        puffer_results = self.puffer.namespace(namespace).query(
            vector=query_vector,
            filters={"tags": {"In": [required_tag]}},
            limit=limit
        )
        
        # Собираем ID уникальных документов
        doc_ids = list(set([res['attributes']['doc_id'] for res in puffer_results]))
        
        # 2. Мгновенное раскрытие контекста и связей вокруг найденных документов в Графе
        graph_context = self.graph.run("""
            MATCH (d:Document) WHERE d.id IN $doc_ids
            MATCH (d)-[r]-(connected)
            RETURN d.id as doc, type(r) as rel, labels(connected) as type, connected.id as entity_id
        """, doc_ids=doc_ids)
        
        return {
            "text_chunks": puffer_results,
            "structural_connections": graph_context.data()
        }

    def split_text_to_chunks(self, text):
        # Базовый сплиттер для примера
        return [text[i:i+1000] for i in range(0, len(text), 1000)]
```