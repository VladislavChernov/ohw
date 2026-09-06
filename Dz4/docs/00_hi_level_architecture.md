# GraphRAG: Высокоуровневая архитектура

> **Версия:** v6 (итерация поверх базы v5)
> **Последнее обновление:** 2026-09-05
> **Рендер:** схемы заданы как Mermaid-блоки — GitHub и совместимые превью рисуют их нативно. Экспорт в PNG/SVG при необходимости: [mermaid.live](https://mermaid.live) или `npx @mermaid-js/mermaid-cli -i diagram.mmd -o diagram.svg`. Актуальное изображение: [./assets/hi_level_architecture_v6.png](./assets/hi_level_architecture_v6.png).
> История: блоки-срезы v4 и отдельная схема v5 не поддерживаются; исторические копии — в `docs.zip` и `docs/history.md`. Детализация текущей итерации — в этом файле (Mermaid) и `docs/05_adr_log.md` (ADR-013).

## 1. Актуальная архитектура (итерация v6)

Одна цельная картина: база v5 (гибридный ретривер Graph + Vector, Adapter Layer, сервисы) слита с обновлением v6 (асинхронный Task Queue контур, Query Workers, Topology Orchestrator). На Этапе 8 Topology Orchestrator выделен в отдельный сервис с собственным настроечным UI (ADR-019): конфигуратор «Бизнес-онтология» (:8501) и Topology UI (:8502) — раздельные приложения.

```mermaid
flowchart LR
    classDef actor fill:#f1f3f4,stroke:#5f6368
    classDef net fill:#e8eaed,stroke:#5f6368
    classDef worker fill:#fff7e6,stroke:#f29900
    classDef queue fill:#e0f2f1,stroke:#0f766e
    classDef py fill:#e8f0fe,stroke:#1a73e8
    classDef hybrid fill:#fff7e6,stroke:#f29900
    classDef adapt fill:#e6f4ea,stroke:#188038
    classDef orch fill:#e6f4ea,stroke:#188038
    classDef infra fill:#f3e8fd,stroke:#9334e6
    classDef store fill:#e0f2f1,stroke:#0f766e
    classDef obs fill:#e8eaed,stroke:#5f6368

    %% ---- Актёры ----
    U["Пользователь<br/>(разработчик / аналитик / ИИ-агент через MCP)"]
    A["Конфигуратор<br/>(Web UI «Бизнес-онтология», :8501)"]
    OP["Оператор<br/>(Topology UI, :8502)"]
    CI["CI/CD / CRON<br/>(GitLab webhook, CLI)"]

    %% ---- Сетевой контур ----
    subgraph NET["Сетевой контур (Query API, Task Queue, Workers)"]
        direction TB
        GATE["Query API Gateway / MCP-шлюз :8000"]
        QUEUE[("Task Queue<br/>Valkey / Redis Streams")]
        W["Query Workers<br/>(пул процессов, асимметричный забор)"]
    end

    %% ---- ИИ-контур (Python) ----
    subgraph PY["ИИ-контур (Python) :8001-8004"]
        direction TB
        CONF["Config Service :8001<br/>(Domain Profile, namespace: adapters)"]
        ING["Ingestion API :8002"]
        GLOS["Glossary Service :8003"]
        EMB["Embeddings Service :8004"]

        subgraph HYBRID["Гибридный ретривер (Graph + Vector)"]
            direction TB
            RET["Retriever"]
            GR["Graph Retriever"]
            VR["Vector Retriever"]
            CA["Context Assembly<br/>(граф = скелет, вектор = тело, лимит 4096 токенов)"]
        end
        PP["Ingestion Pipeline<br/>(EMBED → EXTRACT → NORMALIZE → COMMIT)"]
    end

    %% ---- Topology контур (отдельный сервис, ADR-019) ----
    subgraph TOPO_CT["Topology контур (ADR-019) :8005, :8502"]
        direction TB
        TOPO["Topology Orchestrator Service :8005<br/>(фабрика провайдеров, infra_topology.yaml)"]
        TOPOUI["Topology UI (Streamlit :8502)<br/>настроечное приложение оператора"]
    end

    %% ---- Адаптерный слой ----
    subgraph ADAPT["Adapter Layer — программные интерфейсы (namespace: adapters)"]
        direction TB
        GSP["GraphStoreProvider"]
        VSP["VectorStoreProvider"]
        LLM["LLMInference"]
        EMD["Embedder"]
        RER["Reranker"]
    end

    %% ---- Инфраструктура ИИ ----
    subgraph INFRA["Инфраструктура ИИ"]
        direction TB
        OLL["Ollama :11434<br/>Qwen 2.5 7B (GPU)"]
        BGE["BGE-M3 / LocalSentenceTransformer<br/>(embeddings, GPU)"]
        BGR["bge-reranker-base (CPU)"]
    end

    %% ---- Хранилища ----
    subgraph STORE["Хранилища"]
        direction TB
        NEO4J[("Neo4j<br/>граф. движок + нативный векторный индекс")]
        MEMGRAPH[("Memgraph<br/>граф, альтернатива (фаза Рост)")]
        QDRANT[("Qdrant<br/>вектор, альтернатива (фаза Рост)")]
        REG[("Document Registry<br/>реестр документов и версий")]
    end

    subgraph OBS["Наблюдаемость"]
        direction TB
        PR["Prometheus"]
        GF["Grafana"]
        LK["Loki"]
    end

    %% ---- Асинхронный контур запросов (v6) ----
    U -->|"POST /query → 202 Accepted + task_id"| GATE
    GATE -->|"публикация задачи"| QUEUE
    QUEUE -->|"асимметричное распределение"| W
    W -->|"граф-поиск + инференс"| RET
    GATE -->|"WebSockets / SSE — стриминг токенов"| U

    %% ---- Управление ----
    A -->|"HTTP :8001"| CONF
    OP -->|"HTTP :8005"| TOPOUI
    TOPOUI -->|"управление топологией"| TOPO
    CI -->|"HTTP / CLI"| ING
    TOPO -.->|"пулы соединений: InMemory → Redis/RabbitMQ/vLLM"| QUEUE
    TOPO -.->|"runtime config"| GSP & VSP & LLM & EMD & RER

    %% ---- Гибридный ретривер (база v5) ----
    RET --> GR
    RET --> VR
    GR --> CA
    VR --> CA
    CA -->|"инференс промпта"| LLM
    CA --> RER

    GR -->|"Cypher-запросы, обход связей"| GSP
    VR -->|"vector_search (top-N чанков)"| VSP

    GSP -->|"графовая ось"| NEO4J
    GSP -.->|"альтернатива"| MEMGRAPH
    VSP -->|"векторная ось"| NEO4J
    VSP -.->|"альтернатива"| QDRANT

    LLM --> OLL
    EMD --> EMB
    EMD -.->|"встроенный режим"| BGE
    EMB --> BGE
    RER --> BGR

    %% ---- Ingestion (база v5) ----
    ING --> PP
    ING --> EMD
    PP --> GSP
    PP --> VSP
    PP --> REG

    GLOS -->|"resolve терминов"| GR
    GLOS -->|"resolve терминов"| VR
    CONF -.->|"runtime config"| GSP & VSP & LLM & EMD & RER

    %% ---- Наблюдаемость ----
    GATE -.->|"метрики"| PR
    ING -.->|"метрики"| PR
    CONF -.->|"метрики"| PR
    EMB -.->|"метрики"| PR
    RET -.->|"метрики"| PR
    OLL -.->|"метрики"| PR
    NEO4J -.->|"метрики"| PR
    GATE -.->|"логи"| LK
    ING -.->|"логи"| LK
    PR --> GF
    LK --> GF

    style NET fill:#f4f5f6,stroke:#5f6368,color:#3c4043
    style PY fill:#f5f9ff,stroke:#1a73e8,color:#1a3a6b
    style TOPO_CT fill:#f2fbf4,stroke:#188038,color:#0b3b16
    style HYBRID fill:#fffaf0,stroke:#f29900,color:#6b4c00
    style ADAPT fill:#f2fbf4,stroke:#188038,color:#0b3b16
    style INFRA fill:#faf4fd,stroke:#9334e6,color:#4a1172
    style STORE fill:#f0faf9,stroke:#0f766e,color:#064e3b
    style OBS fill:#f4f5f6,stroke:#5f6368,color:#3c4043

    class U,A,OP,CI actor;
    class GATE net;
    class W worker;
    class QUEUE queue;
    class CONF,ING,GLOS,EMB,PP,TOPO,TOPOUI py;
    class RET,GR,VR,CA hybrid;
    class GSP,VSP,LLM,EMD,RER adapt;
    class OLL,BGE,BGR infra;
    class NEO4J,MEMGRAPH,QDRANT,REG store;
    class PR,GF,LK obs;
```
*Легенда:* серый — актёры и сетевой контур (язык реализации — по ADR-020 для прототипа,
на усмотрение реализации; см. `docs/web_layer_replacement.md`); оранжевый — Query Workers;
бирюзовый — очередь и хранилища; синий — ИИ-контур (Python, включая Topology контур/UI
по ADR-019); фиолетовый — инфраструктура ИИ; жёлтый — гибридный ретривер.

![Актуальная архитектура (v6)](./assets/hi_level_architecture_v6.png)

---

## 2. Соответствие блоков SSOT

| Блок на схеме                     | Источник |
|-----------------------------------|----------|
| Базис v5 (гибридный ретривер, Adapter Layer, сервисы) | CONCEPT.md v5 (§2.1, §4, §5), `docs/05_adr_log.md` (ADR-012, ADR-013) |
| Асинхронный контур (Gateway, Task Queue, Workers) | CONCEPT.md v5 (база), `docs/history.md` (Этап 6), `docs/05_adr_log.md` (ADR-013), `docs/06_operations_and_risks.md` |
| Topology Orchestrator / `prototype/infra_topology.yaml` | `docs/history.md` (Этап 6), `docs/infrastructure_stack.md` §2, `docs/05_adr_log.md` (ADR-019 — отдельный сервис + Topology UI) |
| `GraphStoreProvider` / `VectorStoreProvider` | CONCEPT.md §2.1, `docs/adapters_specification.md` |
| Хранилища: Neo4j / Memgraph / Qdrant / Document Registry | CONCEPT.md §2.1, §6.5 `namespace: storage` |
| Наблюдаемость: Prometheus / Grafana / Loki | CONCEPT.md §8 Operations |

---

## 3. Как обновлять схему при изменении концепта

1. Правь только Mermaid-блоки — они рендерятся автоматически (git diff читаемый).
2. Держи названия блоков в синхроне с SSOT: имена интерфейсов (`GraphStoreProvider`, `VectorStoreProvider`) — из CONCEPT.md §2.1, порты — из `docs/04_services_config.md`, асинхронный контур — из `docs/05_adr_log.md` (ADR-013).
3. Новая итерация концепции замещает актуальную схему целиком; прежние срезы не поддерживаются и живут только в истории (`docs.zip`, `docs/history.md`).