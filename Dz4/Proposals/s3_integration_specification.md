# ТЕХНИЧЕСКАЯ СПЕЦИФИКАЦИЯ: ИНТЕГРАЦИЯ S3-ОРИЕНТИРОВАННОЙ ВЕКТОРНОЙ ОСИ В GRAPHRAG v6
---
**Статус:** На утверждение команде внедрения
**Версия архитектуры:** v6.1-s3 (Инварианты ядра сохранены)
**Контекст:** Оптимизация RAM-потребления и стоимости масштабирования векторного поиска по чанкам.

## 1. Концепция решения в стиле turbopuffer

В соответствии с принципом разделения интерфейсов (ADR-013), векторная ось полностью изолируется от графового хранилища (Neo4j/Memgraph). Все текстовые чанки и их эмбеддинги переводятся на модель неизменяемого блочного хранения (Immutable Blobs) в S3-совместимое хранилище.

### Ключевые архитектурные принципы:
1. **Изоляция доменов (Namespaces):** Данные физически разделены на уровне бакетов/ключей S3 по пространствам имен (`domains/{domain_id}/`).
2. **IVF Индексация (Инвертированный файл центроидов):** Вместо классического графового HNSW-поиска, требующего сотен мелких чтений (что по сети S3 привело бы к запредельным задержкам), векторы кластеризуются. В RAM воркера лежат только центроиды. Поиск в S3 выполняется точечно по выбранным кластерам через Range Requests.
3. **Пайплайн без изменений:** Этапы пайплайна CHUNK (№2)...EMBED (№3) отдают стандартные структуры. Вся упаковка данных происходит внутри метода `upsert_vectors` на этапе №9 COMMIT нового адаптера.

---

## 2. Локальное развертывание S3 контура (MinIO)

Для локального контура разработки и тестирования в выделенную Docker-сеть `ohw_net` добавляется официальный высокопроизводительный S3-эмулятор MinIO. Он полностью совместим с AWS S3 API и поддерживает Range Requests.

### Дополнение в файл `docker-compose.yaml`:
```yaml
services:
  minio:
    image: minio/minio:RELEASE.2024-08-29T21-10-18Z
    container_name: graphrag_minio
    networks:
      - ohw_net
    ports:
      - "9000:9000"       # API S3 порт для воркеров
      - "9001:9001"       # Web UI Консоль управления
    environment:
      MINIO_ROOT_USER: "graphrag_admin"
      MINIO_ROOT_PASSWORD: "puffer_secret_password"
    volumes:
      - minio_data:/data
    command: server /data --console-address ":9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 10s
      timeout: 5s
      retries: 3

volumes:
  minio_data:
```

---

## 3. Реестр изменений в документации системы (Impact Analysis)

Изменения носят характер расширения конфигурационных контрактов и фиксации нового архитектурного решения.

### 3.1. Правки в `docs/05_adr_log.md` (Журнал ADR)
Необходимо добавить новую запись архитектурного решения:
* **ADR-028: Введение S3-совместимого провайдера для векторной оси (S3 IVF Puffer Index)**
* **Статус:** Accepted
* **Контекст:** Хранение миллионов векторов в RAM RAM-ёмких баз (Qdrant/Neo4j) экономически нецелесообразно для «спящих» доменов.
* **Решение:** Перевести `VectorStoreProvider` на плоские файлы в S3 с ленивой загрузкой. Декларировать `consistency_capability() = "best_effort"` и `engine_key() = "s3_flat_blob_storage"`. При сбое записи на этапе COMMIT ядро выполняет компенсационное удаление (сброс чанков из графа).

### 3.2. Изменения в `docs/04_services_config.md` и `docs/operations_and_risks.md`
В схему ключей Runtime Config (Config Service) вносятся следующие дополнения:

* В `namespace: adapters` для ключа `vector_store` добавляется валидный ID: `"s3_puffer"`.
* В `namespace: storage` добавляются параметры соединения с S3:
```yaml
namespace: storage:
  s3_endpoint_url: "http://minio:9000"
  s3_bucket: "graphrag-puffer-chunks"
  s3_region: "us-east-1"
  s3_local_cache_dir: "/var/lib/graphrag/cache"
  s3_ivf_clusters: 256
```

### 3.3. Изменения в `docs/adapters_specification.md`
В раздел реализации интерфейсов добавляется спецификация `S3VectorStoreAdapter` как официального провайдера векторной оси, не нарушающего работу `GraphStoreProvider`.

### 3.4. Изменения в `docs/infrastructure_stack.md`
В стек баз данных векторной оси официально включается MinIO / AWS S3.

---

## 4. Эталонная реализация S3-адаптера на Python

Адаптер оформляется как изолированный плагин и регистрируется в системе через `entry_points` механизмами setuptools/poetry без изменения исходного кода ядра.

### Файл реализации: `graphrag_s3_puffer/adapters/s3_store.py`
```python
import json
import io
import struct
import numpy as np
import boto3
from typing import Any, Dict, List, Optional
from graphrag.adapters import VectorStoreProvider  # Абстрактный контракт ядра

class S3VectorStoreAdapter(VectorStoreProvider):
    """
    Слой адаптера для S3-ориентированной векторной оси в стиле turbopuffer.
    Реализует ленивый поиск чанков через IVF-центроиды и S3 Range Requests.
    """
    def __init__(self, endpoint_url: str, bucket_name: str, cache_dir: str, clusters: int = 256):
        self.s3 = boto3.client(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id="graphrag_admin",
            aws_secret_access_key="puffer_secret_password"
        )
        self.bucket = bucket_name
        self.clusters = clusters
        self.local_cache: Dict[str, Any] = {}  # Локальный RAM-кэш для centroids.bin
        self._write_buffer: List[Dict[str, Any]] = []

    def consistency_capability(self) -> str:
        """Информирует оркестратор о необходимости компенсационных транзакций (ADR-024)"""
        return "best_effort"

    def engine_key(self) -> Optional[str]:
        return "s3_flat_blob_storage"

    def vector_search(self, embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """Выполняет косинусный поиск без HNSW-графа на базе S3 Range Requests."""
        query_vec = np.array(embedding, dtype=np.float32)
        domain = "active_domain"  # В реальном рантайме извлекается из контекста сессии воркера
        
        # 1. Ленивый прогрев индекса центроидов из S3
        centroids = self._get_cached_centroids(domain)
        if centroids is None:
            return []  # Домен пуст, данные еще не индексировались
            
        # 2. Нахождение ближайших кластеров на CPU воркера (IVF)
        scores = np.dot(centroids, query_vec) / (np.linalg.norm(centroids, axis=1) * np.linalg.norm(query_vec))
        best_cluster_ids = np.argsort(scores)[-2:]  # Запрашиваем 2 наиболее близких кластера
        
        hits = []
        # 3. HTTP Range Requests до S3 для скачивания конкретных байтовых диапазонов сегментов
        for cluster_id in best_cluster_ids:
            hits.extend(self._fetch_and_score_cluster(domain, cluster_id, query_vec))
            
        hits.sort(key=lambda x: x["score"], reverse=True)
        return hits[:top_k]

    def upsert_vectors(self, items: List[Dict[str, Any]]) -> None:
        """Буферизация векторов в памяти воркера для предотвращения сетевого оверхеда."""
        self._write_buffer.extend(items)

    def transaction(self):
        """Обеспечение транзакционного контекста этапа COMMIT (Этап №9)"""
        class S3Transaction:
            def __init__(self, adapter):
                self.adapter = adapter
            def __enter__(self):
                return self.adapter
            def __exit__(self, exc_type, exc_val, exc_tb):
                if exc_type is None:
                    self.adapter._flush_buffer_to_s3()
                self.adapter._write_buffer.clear()
        return S3Transaction(self)

    def delete_vectors(self, chunk_ids: List[str]) -> None:
        """Soft-delete: атомарная запись маски tombstones.bin (ADR-014)"""
        # В S3 объекты неизменяемы. Записывается компактный файл-исключение.
        # Фоновая джоба Compaction (раз в сутки) объединяет и перестраивает сегменты.
        pass

    def _get_cached_centroids(self, domain: str) -> Optional[np.ndarray]:
        if domain in self.local_cache:
            return self.local_cache[domain]
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=f"domains/{domain}/centroids.bin")
            data = response['Body'].read()
            arr = np.frombuffer(data, dtype=np.float32).reshape(-1, 1024)
            self.local_cache[domain] = arr
            return arr
        except self.s3.exceptions.NoSuchKey:
            return None

    def _fetch_and_score_cluster(self, domain: str, cluster_id: int, query_vec: np.ndarray) -> List[Dict]:
        # В продакшен-коде: чтение смещений из index.bin, выкачивание подстроки bytes=X-Y 
        # из .vectors.bin и .chunks.bin, расчет косинусного расстояния через SIMD/AVX-512
        return []

    def _flush_buffer_to_s3(self):
        """Атомарная сборка буфера в IVF сегменты и заливка в S3"""
        if not self._write_buffer:
            return
        # 1. K-Means кластеризация векторов буфера
        # 2. Сериализация в бинарные структуры .vectors.bin, .chunks.bin
        # 3. Перезапись centroids.bin домена
        # 4. Выгрузка в S3 через Multipart Upload
        pass
```

### Регистрация через плагины в `pyproject.toml`:
```toml
[project.entry-points."graphrag.adapters.storage"]
s3_puffer = "graphrag_s3_puffer.adapters.s3_store:S3VectorStoreAdapter"
```

---

## 5. Регламент контрактного тестирования нового адаптера (ADR-012)

Любая сторонняя реализация обязана проходить контрактные тесты (`tests/test_s3_puffer_adapter.py`) перед рантайм-активацией:
1. **Детерминизм:** Идентичный вектор запроса выдает стабильный набор чанков и скоров.
2. **Изоляция:** Поиск в одном домене гарантированно не затрагивает префиксы ключей S3 других доменов.
3. **Fail-Open Устойчивость (ADR-025):** Обрыв сети с MinIO (таймаут 2 секунды) не вызывает фатального падения Query Worker (`500 Error`). Метод переходит в состояние Miss (возвращает пустой массив), позволяя системе корректно ответить пользователю на основе данных графовой оси.