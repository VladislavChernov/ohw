"""Streamlit-обвязка демо-контура (add-demo-ui-e2e).

Вкладки: «Документы» (upload txt/md → стадии INGEST → soft-delete) и «Запросы»
(POST /query → SSE-стрим status/token/done → текст ответа + sources + тайминги).
Адреса и ключ — в sidebar; контракты вынесены в `graphrag_proto.demo_ui.client`.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import streamlit as st

from graphrag_proto.demo_ui.client import (
    CONFIG_URL,
    DEFAULT_SLIDER_DEPTH,
    DEPTH_MAX,
    DEPTH_MIN,
    INGESTION_URL,
    QUERY_URL,
    X_API_KEY,
    DemoClient,
    Settings,
)

TERMINAL_INGEST = {"succeeded", "failed", "cancelled"}

#: Подсказка ползунка. Формулировка про то, что человек получит, а не про механизм: слова
#: «глубина обхода по связи» приглашают пользователя считать хопы, а ему это не нужно.
#: Про то, что значение выше предела не отвергается, сказано прямо, потому что зажим
#: объявляется и без пояснения выглядит как ошибка.
DEPTH_HELP = (
    "Насколько широко опрашивать граф вокруг найденного. Меньше - быстрее и точнее; "
    f"больше - больше контекста и медленнее. Дальше {DEPTH_MAX} не пройти: значение "
    "обрежется до предела, и об этом будет сказано под ответом."
)


def _depth_from_state() -> int:
    return int(st.session_state.get("query_max_depth", DEFAULT_SLIDER_DEPTH))


def _event_body(envelope: dict[str, Any]) -> dict[str, Any]:
    """Тело события из SSE-конверта ADR-016 `{type, task_id, ts, payload}`."""
    body = envelope.get("payload")
    return body if isinstance(body, dict) else envelope


def _client() -> DemoClient:
    settings = Settings(
        ingestion_url=st.session_state.get("ingestion_url", INGESTION_URL),
        query_url=st.session_state.get("query_url", QUERY_URL),
        config_url=st.session_state.get("config_url", CONFIG_URL),
        api_key=st.session_state.get("api_key", X_API_KEY),
    )
    return DemoClient(settings)


def _domain_options(client: DemoClient) -> list[str]:
    try:
        domains = client.list_domains()
    except RuntimeError:
        return ["doc"]
    return domains or ["doc"]


def _render_stages(job: dict[str, Any]) -> None:
    stages = job.get("stages")
    if not isinstance(stages, list) or not stages:
        return
    rows: list[dict[str, str]] = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        rows.append(
            {
                "стадия": str(stage.get("stage", "")),
                "статус": str(stage.get("status", "")),
                "детали": str(stage.get("message", "")),
            }
        )
    st.dataframe(rows, hide_index=True)


def _watch_ingest(client: DemoClient, job_id: str, max_attempts: int = 60) -> dict[str, Any]:
    placeholder = st.empty()
    job: dict[str, Any] = {}
    for attempt in range(1, max_attempts + 1):
        job = client.get_job(job_id)
        placeholder.caption(
            f"INGEST {job_id}: статус «{job.get('status', '')}» (попытка {attempt}/{max_attempts})"
        )
        if job.get("status") in TERMINAL_INGEST:
            break
        time.sleep(1)
    return job


def _render_documents(client: DemoClient, domains: list[str]) -> None:
    st.subheader("Документы")
    domain = st.selectbox("Домен", options=domains, key="upload_domain")
    upload = st.file_uploader(
        "txt/md файл",
        type=["txt", "md"],
        key="upload_file",
    )
    if upload is not None:
        st.caption(f"выбран: {upload.name}")
    st.caption("PDF в демо недоступен: JSON-контракт M1 /ingestion/documents не несёт бинарный контент.")
    if st.button("Загрузить", type="primary"):
        if upload is None:
            st.info("Выберите файл txt/md для загрузки.")
        else:
            name = Path(upload.name)
            doc_type = name.suffix.lower().lstrip(".")
            source_url = f"s://demo/{name.name}"
            content = upload.getvalue().decode("utf-8", errors="replace")
            try:
                response = client.upload_document(content, doc_type, domain, source_url)
            except RuntimeError as exc:
                st.error(str(exc))
            else:
                job_id = str(response.get("job_id", ""))
                st.session_state.setdefault("demo_jobs", []).append(
                    {
                        "job_id": job_id,
                        "source_url": source_url,
                        "domain": domain,
                        "status": "queued",
                    }
                )
                job = _watch_ingest(client, job_id)
                st.session_state["demo_jobs"][-1]["status"] = job.get("status", "unknown")
                st.write("Стадии:")
                _render_stages(job)
                if job.get("status") == "succeeded":
                    st.success(f"Загрузка завершена: {source_url}")
                elif job.get("status") == "failed":
                    st.error(f"Загрузка упала: {job.get('error', '')}")
                else:
                    st.error(f"Не завершилось за отведённое время: {job.get('status', '')}")

    st.divider()
    jobs = st.session_state.get("demo_jobs", [])
    if jobs:
        st.markdown("**Загруженные в этой сессии источники**")
        rows: list[dict[str, str]] = []
        for item in jobs:
            rows.append(
                {
                    "job_id": str(item.get("job_id", "")),
                    "source_url": str(item.get("source_url", "")),
                    "domain": str(item.get("domain", "")),
                    "status": str(item.get("status", "")),
                }
            )
        st.dataframe(rows, hide_index=True)
        source_url = st.selectbox("Источник для soft-delete", options=[r["source_url"] for r in rows])
        if st.button("Удалить (soft-delete)"):
            item_domain = next((r["domain"] for r in rows if r["source_url"] == source_url), domain)
            try:
                client.delete_document(item_domain, source_url)
            except RuntimeError as exc:
                st.error(str(exc))
            else:
                st.success(f"Источник {source_url} снят с поиска (его чанки убраны из retrieval).")


def _render_depth_control() -> int:
    """Ползунок глубины. Границы приходят из кода, начальное положение - из состояния сессии."""
    left, right = st.columns([3, 1])
    with left:
        depth = st.slider(
            "Глубина поиска",
            min_value=DEPTH_MIN,
            max_value=DEPTH_MAX,
            value=_depth_from_state(),
            key="query_max_depth",
            help=DEPTH_HELP,
        )
    with right:
        st.caption(f"1 = только прямые связи, {DEPTH_MAX} = максимум")
    return int(depth)


def _render_effective_depth(done: dict[str, Any]) -> None:
    """Показать, какая глубина реально применилась, и объявить зажим.

    Без этого ползунок врёт: человек ставит 6, а получает ответ, собранный на 3, и не
    понимает почему. Зажим объявляется в ответе (`depth_clamped`), и здесь он
    превращается в предупреждение, а не остаётся полем в JSON.
    """
    effective = done.get("effective_retrieval")
    if not isinstance(effective, dict):
        # Ответа может не быть: попадание в семантический кэш не отдаёт effective_retrieval,
        # и об этом стоит сказать, иначе человек решит, что ползунок сработал.
        st.warning(
            "Ответ собран из кэша: запрошенная глубина не применялась. "
            "Переформулируйте вопрос или прокрутите кэш.",
        )
        return
    requested = effective.get("max_depth_requested")
    applied = effective.get("max_depth_effective")
    clamped = effective.get("depth_clamped")
    st.caption(f"Глубина: запрошено {requested}, применено {applied}")
    if clamped:
        st.warning(
            f"Запрошенная глубина {requested} выше предела {DEPTH_MAX}: применено {applied}. "
            f"Дальше {DEPTH_MAX} обход не идёт.",
        )


def _render_queries(client: DemoClient, domains: list[str]) -> None:
    st.subheader("Запросы к графу")
    domain = st.selectbox("Домен вопроса", options=domains, key="query_domain")
    query = st.text_input("Вопрос", key="query_text", placeholder="Например: что регулирует HSTPA?")
    depth = _render_depth_control()
    if st.button("Отправить запрос", type="primary"):
        if not query.strip():
            st.info("Введите текст вопроса.")
        else:
            started = time.monotonic()
            try:
                task_id = client.submit_query(query.strip(), domain, max_depth=depth)
            except RuntimeError as exc:
                st.error(str(exc))
            else:
                st.session_state.setdefault("demo_query_history", []).append(
                    {
                        "task_id": task_id,
                        "query": query.strip(),
                        "domain": domain,
                        "depth": depth,
                        "status": "running",
                    }
                )
                transcript: list[str] = []
                answer_placeholder = st.empty()
                error: dict[str, Any] | None = None
                done: dict[str, Any] | None = None
                with st.status(f"Стриминг задачи {task_id}…") as status_box:
                    try:
                        for event_type, envelope in client.stream_task(task_id):
                            body = _event_body(envelope)
                            if event_type == "status":
                                status_box.write(f"status: {body.get('stage') or body.get('status')}")
                            elif event_type == "token":
                                transcript.append(str(body.get("text", "")))
                                answer_placeholder.markdown("".join(transcript))
                            elif event_type == "done":
                                done = body
                                seconds = body.get("generation_time_s")
                                label = f"done (генерация {seconds} с)"
                                status_box.update(label=label, state="complete")
                            elif event_type == "error":
                                error = body
                                status_box.update(label="error", state="error")
                    except RuntimeError as exc:
                        st.error(str(exc))
                        for record in st.session_state["demo_query_history"]:
                            if record["task_id"] == task_id:
                                record["status"] = "error"
                elapsed = time.monotonic() - started
                if done is not None:
                    for record in st.session_state["demo_query_history"]:
                        if record["task_id"] == task_id:
                            record["status"] = "done"
                    st.markdown("### Ответ")
                    st.markdown(str(done.get("text", "")) or "_пусто_")
                    _render_effective_depth(done)
                    caption = f"task {task_id} · e2e {elapsed:.1f} с"
                    retrieval_s = done.get("retrieval_time_s")
                    total_s = done.get("total_time_s")
                    if isinstance(retrieval_s, (int, float)) and isinstance(total_s, (int, float)):
                        caption += f" · retrieval {float(retrieval_s):.3f} с · total {float(total_s):.3f} с"
                    st.caption(caption)
                    sources = done.get("sources")
                    if isinstance(sources, list) and sources:
                        rows: list[dict[str, Any]] = []
                        for source in sources:
                            if isinstance(source, dict):
                                rows.append(
                                    {
                                        "source_url": str(source.get("source_url", "")),
                                        "relevance": source.get("relevance", ""),
                                    }
                                )
                        st.markdown("**Источники**")
                        st.dataframe(rows, hide_index=True)
                elif error is not None:
                    for record in st.session_state["demo_query_history"]:
                        if record["task_id"] == task_id:
                            record["status"] = "error"
                    st.error(str(error.get("message", "")))

    st.divider()
    history = st.session_state.get("demo_query_history", [])
    if history:
        st.markdown("**История запросов сессии**")
        rows = [
            {
                "task_id": str(item.get("task_id", "")),
                "query": str(item.get("query", "")),
                "domain": str(item.get("domain", "")),
                "глубина": str(item.get("depth", "")),
                "status": str(item.get("status", "")),
            }
            for item in history
        ]
        st.dataframe(rows, hide_index=True)
        running = [r for r in history if r["status"] == "running"]
        if running:
            task_id = st.selectbox(
                "Задача для отмены",
                options=[r["task_id"] for r in running],
            )
            if st.button("Отменить задачу"):
                try:
                    client.cancel_task(task_id)
                except RuntimeError as exc:
                    st.error(str(exc))
                else:
                    for record in st.session_state["demo_query_history"]:
                        if record["task_id"] == task_id:
                            record["status"] = "cancelled"
                    st.success(f"Задача {task_id} отменена.")


def main() -> None:
    st.set_page_config(page_title="GraphRAG: демо-контур", layout="wide")
    st.title("GraphRAG: демо-контур")
    st.caption("Демо-UI: загрузка документа → запрос → ответ со sources. Контракты M1/M2.")

    with st.sidebar:
        st.subheader("Подключение")
        st.text_input("Ingestion URL", value=INGESTION_URL, key="ingestion_url")
        st.text_input("Query URL", value=QUERY_URL, key="query_url")
        st.text_input("Config URL", value=CONFIG_URL, key="config_url")
        st.text_input("X-API-Key", value=X_API_KEY, key="api_key", type="password")

    client = _client()
    domains = _domain_options(client)
    if not domains:
        st.error("Нет доменов в Config Service: проверьте GET /api/v1/config/domain/profiles.")

    tab_documents, tab_queries = st.tabs(["Документы", "Запросы"])
    with tab_documents:
        _render_documents(client, domains)
    with tab_queries:
        _render_queries(client, domains)


if __name__ == "__main__":
    main()