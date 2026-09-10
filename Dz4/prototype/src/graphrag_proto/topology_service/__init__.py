"""Topology Orchestrator Service (:8005, профиль `topology`, ADR-019).

add-topology-adapters (M3): читает infra_topology.yaml, отдаёт топологию и активную
карту адаптеров; `PUT /api/v1/config/adapters` переключает провайдеров на лету
(override в SQLite + монотонный revision). Каталог реализованных провайдеров —
общий источник с фабрикой retrieval (retrieval/adapters/factory.py).
"""