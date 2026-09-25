# Архитектура и структура данных мультидоменного ингеста

Этот документ содержит Pydantic-модели (Python) и JSON-схемы для структурированного извлечения данных (Structured Outputs) с помощью LLM в мультидоменную графовую БД (Legal, IT, HR).

---

## 1. Концепция разметки (JSON-схема для LLM)

Чтобы ИИ стабильно возвращал данные для графа, его ответ должен строго соответствовать структуре, состоящей из двух списков: узлов (Nodes) и связей (Edges).

### Pydantic-модель верхнего уровня (Python)

```python
from typing import List, Dict, Any, Optional
from enum import Enum
from pydantic import BaseModel, Field

class DomainEnum(str, Enum):
    LEGAL = "LEGAL"
    IT = "IT"
    HR = "HR"
    CROSS_DOMAIN = "CROSS_DOMAIN"

class GraphNode(BaseModel):
    id: str = Field(..., description="Уникальный строковый ID узла, сформированный LLM на основе сущности (например, 'person_ivanov', 'case_2026_007')")
    label: str = Field(..., description="Тип узла в верхнем регистре (например: :Person, :Case, :Event, :Document, :Incident, :Server, :Department)")
    domain: DomainEnum = Field(..., description="К какому домену относится данный узел")
    properties: Dict[str, Any] = Field(..., description="Плоский словарь со свойствами узла (ключ-значение). Специфично для каждого типа узла.")

class GraphEdge(BaseModel):
    source: str = Field(..., description="ID исходного узла (from)")
    target: str = Field(..., description="ID целевого узла (to)")
    type: str = Field(..., description="Тип связи в верхнем регистре (например: :BELONGS_TO, :ASSIGNED_TO, :ROLE)")
    properties: Optional[Dict[str, Any]] = Field(default=None, description="Дополнительные свойства связи, например, {'type': 'Адвокат'} для связи :ROLE")

class ExtractedGraph(BaseModel):
    summary: str = Field(..., description="Краткое описание документа или контекста, из которого извлечены данные")
    main_domain: DomainEnum = Field(..., description="Доминирующий домен анализируемого документа")
    nodes: List[GraphNode] = Field(default_..., description="Список всех обнаруженных узлов")
    edges: List[GraphEdge] = Field(default_..., description="Список всех обнаруженных связей между узлами")
```

---

## 2. Паспорта свойств для каждого домена (Спецификация свойств)

Каждая сущность внутри `GraphNode.properties` должна следовать строгому формату в зависимости от её `label`. Ниже описаны структуры полей для основных узлов.

### А. Сквозные узлы (Cross-Domain)
Эти узлы связывают домены между собой.

*   **`:Person` (Сотрудник / Клиент / Контрагент)**
    ```json
    {
      "full_name": "Иванов Иван Иванович",
      "corporate_email": "ivanov@company.com",
      "personal_email": "ivan.ivanov@mail.ru",
      "phone": "+79991112233",
      "inn": "770000000000"
    }
    ```
*   **`:Project` (Проект / Продукт)**
    ```json
    {
      "project_name": "Миграция Почты 2026",
      "status": "active",
      "budget_code": "PRJ-2026-09"
    }
    ```

### Б. Юридический домен (LEGAL)
*   **`:Case` (Дело / Судебный спор)**
    ```json
    {
      "case_number": "Дело №2026-007",
      "title": "АГАЙС-2026",
      "case_type": "гражданское",
      "status": "активно",
      "date_opened": "2026-09-19"
    }
    ```
*   **`:Document` (Документ / Договор / Иск)**
    ```json
    {
      "doc_name": "Договор поставки №44-ФЗ",
      "doc_type": "контракт",
      "version": "1.0",
      "date_created": "2026-09-15"
    }
    ```

### В. IT домен (IT)
*   **`:Incident` (Инцидент / Тикет / Сбой)**
    ```json
    {
      "ticket_id": "INC-991",
      "summary": "Падение почтового сервера Exchange",
      "severity": "CRITICAL",
      "created_at": "2026-09-19T14:20:00Z"
    }
    ```
*   **`:Server` / `:Service` (Инфраструктурный элемент)**
    ```json
    {
      "system_name": "Exchange-Server-01",
      "ip_address": "192.168.1.50",
      "environment": "production"
    }
    ```

### Г. Кадровый домен (HR)
*   **`:Department` (Подразделение)**
    ```json
    {
      "dept_name": "Департамент ИТ-инфраструктуры",
      "cost_center": "CC-404"
    }
    ```
*   **`:HR_Event` (Прием / Увольнение / Повышение)**
    ```json
    {
      "event_type": "прием на работу",
      "effective_date": "2026-09-01",
      "position": "Ведущий системный администратор"
    }
    ```

---

## 3. Эталонный JSON-ответ от LLM (Пример работы ингеста)

Ниже представлен пример валидного JSON, который генерирует LLM, когда конвейер ингеста обрабатывает текст вида: *«19.09.2026 системный администратор Иван Иванов занимался устранением критического инцидента INC-991 на сервере Exchange. Данный инцидент привел к утечке данных, по поводу которой юрист Петр Петров начал подготовку иска по Делу №2026-007»*.

```json
{
  "summary": "Извлечение связей по инциденту утечки данных и заведению юридического дела.",
  "main_domain": "CROSS_DOMAIN",
  "nodes": [
    {
      "id": "person_ivan_ivanov",
      "label": "Person",
      "domain": "CROSS_DOMAIN",
      "properties": {
        "full_name": "Иванов Иван Иванович",
        "corporate_email": "ivanov@company.com"
      }
    },
    {
      "id": "person_petr_petrov",
      "label": "Person",
      "domain": "LEGAL",
      "properties": {
        "full_name": "Петров Петр Петрович"
      }
    },
    {
      "id": "incident_inc_991",
      "label": "Incident",
      "domain": "IT",
      "properties": {
        "ticket_id": "INC-991",
        "summary": "Падение почтового сервера Exchange и утечка",
        "severity": "CRITICAL"
      }
    },
    {
      "id": "server_exchange",
      "label": "Server",
      "domain": "IT",
      "properties": {
        "system_name": "Exchange-Server-01"
      }
    },
    {
      "id": "case_2026_007",
      "label": "Case",
      "domain": "LEGAL",
      "properties": {
        "case_number": "Дело №2026-007",
        "title": "Судебный спор по утечке данных"
      }
    }
  ],
  "edges": [
    {
      "source": "person_ivan_ivanov",
      "target": "incident_inc_991",
      "type": "ASSIGNED_TO"
    },
    {
      "source": "incident_inc_991",
      "target": "server_exchange",
      "type": "CRASHED_BY"
    },
    {
      "source": "person_petr_petrov",
      "target": "case_2026_007",
      "type": "ROLE",
      "properties": {
        "type": "Адвокат"
      }
    },
    {
      "source": "case_2026_007",
      "target": "incident_inc_991",
      "type": "BASED_ON"
    }
  ]
}
```