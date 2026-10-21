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
    DEFAULT_SLIDER_GRAPH_NODES,
    DEPTH_MAX,
    DEPTH_MIN,
    GRAPH_NODES_MAX,
    GRAPH_NODES_MIN,
    INGESTION_URL,
    QUERY_URL,
    X_API_KEY,
    DemoClient,
    Settings,
)
from graphrag_proto.retrieval.adapters.llm import RESPONSE_EXCERPT_MARKER

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


#: Описание лимита узлов. Оно обязано говорить, ЧТО считается: строки обхода, а не
#: документы и не найденные чанки, - иначе человек ждёт от ползунка «больше источников»,
#: а получает больше промежуточных вершин (ADR-069).
GRAPH_NODES_HELP = (
    "Сколько строк обхода графа взять. Считаются строки пути, а не документы и не "
    "найденные чанки: один и тот же узел, достигнутый разными путями, занимает несколько "
    "строк. Меньше - быстрее и предметнее; больше - больше материала, но без ранжирования "
    f"выбор произволен. Дальше {GRAPH_NODES_MAX} не пройти: значение обрежется, и об этом "
    "будет сказано под ответом."
)


def _graph_nodes_from_state() -> int:
    return int(st.session_state.get("query_max_graph_nodes", DEFAULT_SLIDER_GRAPH_NODES))


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


#: Инструкция, которую видит человек, когда ингест вынесен в отдельный скрипт
#: (ADR-065). Команда приведена ДОБРОВОЛЬНО в одном месте и используется и в слое, и в
#: подсказке заголовка вкладки: две формулировки разъедутся, и одна из них будет врать.
INGEST_SEPARATE_NOTICE = (
    "Ингест вынесен в отдельный скрипт: стенд поднимается без него, и это штатно "
    "(ADR-065). Запросы и поиск работают — не работает только загрузка документов."
)

#: Команды запуска ингеста отдельно. Windows и POSIX названы отдельно намеренно: одна
#: строка на оба варианта была бы враньём на половине машин.
INGEST_STANDALONE_COMMANDS = (
    ("Windows", r"Demo\ingest.ps1 up -Set ready"),
    ("Linux/macOS", "sh Demo/ingest.sh up --set ready"),
)


def ingest_separated_lines() -> list[tuple[str, str]]:
    """Строки слоя «ингест вынесен отдельно». Чистая функция, без Streamlit.

    Вынесена отдельно по образцу `maintenance_preview`: разметку `app.py` не импортирует
    ни один тест, поэтому проверяемо то, что можно вызвать без неё. Слой, который
    молчал бы, читался бы как поломка — а это ровно тот исход, которого здесь быть не
    должно.
    """
    lines: list[tuple[str, str]] = [("notice", INGEST_SEPARATE_NOTICE)]
    lines += [("caption", "Как загрузить документы:")]
    lines += [("command", command) for _platform, command in INGEST_STANDALONE_COMMANDS]
    lines.append(
        (
            "caption",
            (
                "После загрузки выполните `down` тем же скриптом: наполненный том остаётся, "
                "а вопросы продолжают работать."
            ),
        )
    )
    lines.append(
        (
            "caption",
            (
                "Быстрый путь для демонстрации: готовый набор выгружается архивом и "
                "восстанавливается без ингеста — `demo.ps1 restore` / `demo.sh restore`."
            ),
        )
    )
    return lines


def _render_ingest_separated_notice() -> None:
    """Слой поверх вкладки: почему она серая и что сделать, чтобы она ожила."""
    for kind, text in ingest_separated_lines():
        if kind == "notice":
            st.info(text)
        elif kind == "command":
            st.code(text, language="powershell" if text.endswith(".ps1 up -Set ready") else "shell")
        else:
            st.caption(text)


#: Что показывать, когда LLM-слой потерян. Формулировки заданы здесь, а не в разметке,
#: потому что этот же текст уходит в отчёт и в гард.
ENRICHMENT_LOST_HEADER = "Слой сущностей потерян"

#: Причина, при которой потеря считается отдельным исходом: джоба при этом завершается
#: `succeeded`, и без отдельной строки человек видит успех (ADR-066).
ENRICHMENT_LOST_CAUSE_NOTE = (
    "джоба при этом числится успешной, а перезагрузка того же файла не поможет: "
    "повтор того же content_hash — no-op"
)


def enrichment_loss_lines(job: dict[str, Any]) -> list[str]:
    """Что потеряно при экстракции. Чистая функция, без Streamlit.

    Ответ джобы отдаёт потерю в `enrichment`: `cause`, `lost_entities`, `lost_edges`.
    Читать их обязан тот, кто показывает результат: статус джобы одинаков при полной
    потере и при полном успехе, а общее число узлей в базе её не отражает (ADR-066).

    Пустой список означает «потери нет». Отсутствие блока `enrichment` - тоже «нет»:
    его не приходит у джоб, которые шли без LLM-слоя, и выдумывать потерю там нельзя.
    """
    enrichment = job.get("enrichment")
    if not isinstance(enrichment, dict):
        return []
    lost_entities = int(enrichment.get("lost_entities") or 0)
    lost_edges = int(enrichment.get("lost_edges") or 0)
    if not lost_entities and not lost_edges:
        return []
    cause = str(enrichment.get("cause") or "причина не названа")
    lines = [
        f"{ENRICHMENT_LOST_HEADER}: потеряно сущностей {lost_entities}, связей {lost_edges}.",
        f"Причина: {cause}.",
        ENRICHMENT_LOST_CAUSE_NOTE,
    ]
    declared = enrichment.get("llm_relations_declared")
    unique = enrichment.get("llm_relations_unique")
    if declared is not None and unique is not None and declared != unique:
        lines.append(
            f"Модель объявила связей {declared}, уникальных {unique}: повторение ответа, "
            "обрыв по лимиту токенов."
        )
    answer_lines = enrichment.get("llm_answer_lines")
    answer_unique = enrichment.get("llm_answer_lines_unique")
    if answer_lines is not None and answer_unique is not None and answer_lines != answer_unique:
        lines.append(f"Строк ответа {answer_lines}, уникальных {answer_unique}.")
    return lines


def model_response_fragment(job: dict[str, Any]) -> str:
    """Что модель успела вернуть до обрыва. Чистая функция, без Streamlit.

    Ответ не лежит в отдельном поле джобы: адаптер встраивает его в текст ошибки, а
    оркестратор кладёт текст в `message` стадии. Искать его в `enrichment` - значит не найти
    никогда: там только числа.

    Разбор потери без самого ответа упирается в «нужно ещё раз прогнать», а прогон стоит
    минут тринадцать. Поэтому ответ показывается вместе с потерей, а не прячется в записи
    стадии, где его никто не открывает.
    """
    stages = job.get("stages")
    if not isinstance(stages, list):
        return ""
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        message = str(stage.get("message") or "")
        if RESPONSE_EXCERPT_MARKER not in message:
            continue
        return message.split(RESPONSE_EXCERPT_MARKER, 1)[1].strip()
    return ""


def _render_response_log(job: dict[str, Any]) -> None:
    """Показать ответ модели при потере слоя."""
    fragment = model_response_fragment(job)
    if not fragment:
        return
    with st.expander("Ответ модели: что она успела вернуть (фрагмент)", expanded=False):
        st.caption(
            "Начало и конец ответа, который адаптер приложил к ошибке. "
            "Повторяющиеся объекты здесь и есть причина обрыва."
        )
        st.code(fragment, language="json")


def _render_enrichment_loss(job: dict[str, Any]) -> bool:
    """Показать потерю, если она есть. Возвращает `True`, когда потеря показана."""
    lines = enrichment_loss_lines(job)
    if not lines:
        return False
    for line in lines:
        st.error(line)
    _render_response_log(job)
    return True


def _render_documents(client: DemoClient, domains: list[str], *, ingest_up: bool) -> None:
    st.subheader("Документы")
    if not ingest_up:
        # Проба сделана ДО отрисовки элементов: иначе человек узнал бы о недоступности
        # ингеста только после того, как выбрал файл и нажал кнопку.
        _render_ingest_separated_notice()
        return
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
                loss_lines = enrichment_loss_lines(job)
                st.session_state["demo_jobs"][-1]["loss"] = (
                    loss_lines[0] if loss_lines else ""
                )
                st.write("Стадии:")
                _render_stages(job)
                lost = _render_enrichment_loss(job)
                if job.get("status") == "succeeded":
                    # Успех без оговорки показывается только когда потери нет. Иначе
                    # зелёная плашка противоречит красной строкой прямо под ней.
                    st.success(
                        "Загрузка завершена без потерь"
                        if not lost
                        else "Загрузка завершилась, НО часть извлечённого потеряна — см. выше"
                    )
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
                    "потеря слоя": str(item.get("loss", "") or ""),
                }
            )
        st.dataframe(rows, hide_index=True)
        # Потеря переживает перезагрузку страницы: без отдельной строки человек закрыл вкладку
        # и потерял признак, что часть документа в базу не попала.
        lost_rows = [row for row in rows if row["потеря слоя"]]
        if lost_rows:
            st.error(
                f"Загружено источников: {len(rows)}, из них с потерей слоя: {len(lost_rows)}."
            )
            for row in lost_rows:
                st.write(f"{row['source_url']}: {row['потеря слоя']}")
        source_url = st.selectbox("Источник для soft-delete", options=[r["source_url"] for r in rows])
        # Soft-delete необратим: чанки снимаются с поиска сразу, а `source_ids` у
        # сущностей вычищаются. Поэтому кнопка без шага подтверждения опаснее её
        # отсутствия — тот же довод, что у вкладки обслуживания. Подтверждение вводится
        # тем же именем: несовпадение не удаляет ничего, а не удаляет не то.
        typed = st.text_input(
            f"Введите имя документа для подтверждения: `{source_url}`",
            key="soft_delete_confirm",
        )
        confirmed = typed.strip() == source_url.strip() and bool(source_url.strip())
        if st.button("Удалить (soft-delete)", disabled=not confirmed):
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


def _render_graph_nodes_control() -> int:
    """Ползунок лимита строк обхода (ADR-069).

    Стоит рядом с глубиной, а не под ней: оба ограничивают один и тот же обход, и по
    отдельности любой из них вводит в заблуждение. Глубина без лимита выглядит
    управляемой, хотя упирается в него; лимит без глубины непонятно, зачем он.
    """
    left, right = st.columns([3, 1])
    with left:
        nodes = st.slider(
            "Лимит узлов графа",
            min_value=GRAPH_NODES_MIN,
            max_value=GRAPH_NODES_MAX,
            value=_graph_nodes_from_state(),
            key="query_max_graph_nodes",
            help=GRAPH_NODES_HELP,
        )
    with right:
        st.caption(f"строк обхода, {GRAPH_NODES_MIN} = только прямые")
    return int(nodes)


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
    allowed = effective.get("max_depth_allowed", effective.get("max_depth_effective"))
    traversed = effective.get("hops_traversed")
    clamped = effective.get("depth_clamped")
    # ADR-059: «применено» обещало исполнение, а значило только зажим числа в диапазон.
    # На пустой базе подпись утверждала «применено 3» при нуле пройденных хопов.
    parts = [f"Глубина: запрошено {requested}, разрешено {allowed}"]
    if isinstance(traversed, int):
        parts.append(f", пройдено {traversed}")
    st.caption("".join(parts))
    if clamped:
        st.warning(
            f"Запрошенная глубина {requested} выше предела {DEPTH_MAX}: разрешено {allowed}. "
            f"Дальше {DEPTH_MAX} обход не идёт.",
        )
    # Лимит узлов объявляется рядом с глубиной: он режет тот же обход, и его зажим тоже
    # должен быть виден, иначе человек увидит «применено 500» и получит 50 (ADR-069).
    nodes_requested = effective.get("max_graph_nodes_requested")
    nodes_effective = effective.get("max_graph_nodes_effective")
    if isinstance(nodes_requested, int) and isinstance(nodes_effective, int):
        parts.append(f" | Лимит узлов: запрошено {nodes_requested}, применено {nodes_effective}")
    if effective.get("graph_nodes_clamped"):
        st.warning(
            f"Запрошенный лимит узлов {nodes_requested} выше предела {GRAPH_NODES_MAX}: "
            f"применено {nodes_effective}."
        )


def _render_graph_contribution(done: dict[str, Any]) -> None:
    """Показать, что граф добавил сверх векторной оси, в чанках (ADR-072, ADR-075)."""
    summary = graph_contribution(done)
    st.markdown("**Вклад графа**")
    for line in graph_contribution_lines(summary):
        st.write(line)
    st.caption(
        "graph-only — чанки, которые граф добавил сверх векторной оси. "
        "«Материализовано» — текст чанка был собран; «доехало в контекст» — он реально "
        "вошёл в промпт модели. Это разные утверждения."
    )
    rows = graph_only_chunk_rows(summary)
    if rows:
        st.dataframe(rows, hide_index=True)


def _render_queries(client: DemoClient, domains: list[str]) -> None:
    st.subheader("Запросы к графу")
    domain = st.selectbox("Домен вопроса", options=domains, key="query_domain")
    query = st.text_input("Вопрос", key="query_text", placeholder="Например: что регулирует HSTPA?")
    depth = _render_depth_control()
    graph_nodes = _render_graph_nodes_control()
    if st.button("Спросить", type="primary"):
        if not query.strip():
            st.info("Введите текст вопроса.")
        else:
            started = time.monotonic()
            try:
                task_id = client.submit_query(
                    query.strip(),
                    domain,
                    max_depth=depth,
                    max_graph_nodes=graph_nodes,
                )
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
                    _render_graph_contribution(done)
                    caption = f"task {task_id} · e2e {elapsed:.1f} с"
                    retrieval_s = done.get("retrieval_time_s")
                    total_s = done.get("total_time_s")
                    if isinstance(retrieval_s, (int, float)) and isinstance(total_s, (int, float)):
                        caption += f" · retrieval {float(retrieval_s):.3f} с · total {float(total_s):.3f} с"
                    # Настоящий расход токенов от модели. При отсутствии `usage` подпись
                    # молчит, а не пишет «0 токенов»: провайдер мог не отдать счётчики, и
                    # это «не измерено», а не «расхода не было».
                    usage = done.get("usage")
                    if isinstance(usage, dict) and usage:
                        spent = ", ".join(
                            f"{key} {usage[key]}"
                            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                            if isinstance(usage.get(key), int)
                        )
                        if spent:
                            caption += f" · токены: {spent}"
                    st.caption(caption)
                    sources = done.get("sources")
                    if isinstance(sources, list) and sources:
                        rows = source_rows(sources)
                        st.markdown("**Источники**")
                        st.dataframe(rows, hide_index=True)
                        st.caption(
                            "axis: «вектор» — документ найден векторной осью, «граф» — "
                            "графом. graph_knowledge: «граф дал текст» — чанк вошёл в "
                            "контекст; «граф дошёл, текст не взят» — связь есть, текст не "
                            "попал; «граф не знает источник» — документ не был в графе."
                        )
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


GRAPH_AXIS_LABELS = {
    "vector": "вектор",
    "graph": "граф",
}

GRAPH_KNOWLEDGE_LABELS = {
    "contributed": "граф дал текст",
    "reachable_unused": "граф дошёл, текст не взят",
    "blind": "граф не знает источник",
    "unknown": "не измерено",
}


def graph_contribution(done: dict[str, Any]) -> dict[str, Any]:
    """Сводка вклада графа на уровне чанков (ADR-072, ADR-075).

    Документная `necessity` здесь не ответ: при одном размеченном документе она равна
    нулю по построению. Три состояния обязаны остаться разными: граф выключен,
    измеренный ноль и «не измерено». Кэш-попадание не отдаёт `effective_retrieval`, и
    превращать его в ноль нельзя.
    """
    effective = done.get("effective_retrieval")
    if not isinstance(effective, dict):
        return {"status": "unknown", "reason": "в ответе нет effective_retrieval"}
    if not effective.get("graph_search_enabled"):
        return {"status": "disabled", "reason": "graph_search_enabled=false"}

    raw_ids = effective.get("graph_only_chunk_ids")
    if not isinstance(raw_ids, list):
        return {"status": "unknown", "reason": "graph_only_chunk_ids отсутствует"}
    chunk_ids = [str(chunk_id) for chunk_id in raw_ids if chunk_id is not None]

    raw_materialized = effective.get("graph_materialized_chunk_ids")
    materialized: set[str] | None = None
    if isinstance(raw_materialized, list):
        materialized = {str(chunk_id) for chunk_id in raw_materialized if chunk_id is not None}

    source_map: dict[str, str] = {}
    raw_sources = effective.get("graph_only_chunk_sources")
    if isinstance(raw_sources, dict):
        source_map = {
            str(chunk_id): str(source_url)
            for chunk_id, source_url in raw_sources.items()
            if chunk_id is not None and source_url
        }

    context = done.get("context")
    raw_body_ids = context.get("body_chunk_ids") if isinstance(context, dict) else None
    body_ids: set[str] | None = None
    if isinstance(raw_body_ids, list):
        body_ids = {str(chunk_id) for chunk_id in raw_body_ids if chunk_id is not None}

    rows: list[dict[str, Any]] = []
    adr_graph_only = 0
    adr_in_context = 0
    for chunk_id in chunk_ids:
        source_url = source_map.get(chunk_id, "")
        in_context = chunk_id in body_ids if body_ids is not None else None
        is_adr = source_url.replace("\\", "/").rsplit("/", 1)[-1] == "05_adr_log.md"
        if is_adr:
            adr_graph_only += 1
            if in_context is True:
                adr_in_context += 1
        rows.append(
            {
                "chunk_id": chunk_id,
                "source_url": source_url,
                "in_context": in_context,
                "materialized": (
                    chunk_id in materialized if materialized is not None else None
                ),
            }
        )

    in_context_count = (
        sum(1 for row in rows if row["in_context"] is True) if body_ids is not None else None
    )
    materialized_count = (
        sum(1 for row in rows if row["materialized"] is True)
        if materialized is not None
        else None
    )
    return {
        "status": "measured",
        "graph_only": len(rows),
        "materialized": materialized_count,
        "in_context": in_context_count,
        "adr_graph_only": adr_graph_only,
        "adr_in_context": adr_in_context if body_ids is not None else None,
        "rows": rows,
    }


def graph_contribution_lines(summary: dict[str, Any]) -> list[str]:
    """Тексты блока «Вклад графа» — без Streamlit, чтобы их можно было проверить."""
    status = summary.get("status")
    if status == "disabled":
        return [
            "Граф выключен: вклад не измерялся. Это не нулевой вклад, а выключенная ось."
        ]
    if status != "measured":
        return ["Вклад графа не измерен: в ответе нет данных для этой проверки."]

    graph_only = summary.get("graph_only")
    if not isinstance(graph_only, int) or graph_only == 0:
        return ["Граф не добавил ни одного чанка сверх векторной оси."]

    lines = [f"Граф добавил чанков сверх векторной оси: {graph_only}."]
    materialized = summary.get("materialized")
    if isinstance(materialized, int):
        lines.append(f"Материализовано в текст: {materialized}.")
    in_context = summary.get("in_context")
    if isinstance(in_context, int):
        if in_context == 0:
            lines.append("Ни один из них не доехал в текст, который получила модель.")
        else:
            lines.append(f"Доехало в контекст: {in_context}.")
    else:
        lines.append("Попадание в контекст: не измерено.")

    adr_graph_only = summary.get("adr_graph_only")
    adr_in_context = summary.get("adr_in_context")
    if isinstance(adr_graph_only, int) and adr_graph_only:
        if isinstance(adr_in_context, int):
            lines.append(
                f"Из 05_adr_log.md: {adr_graph_only}; доехало в контекст: {adr_in_context}."
            )
        else:
            lines.append(f"Из 05_adr_log.md: {adr_graph_only}; попадание в контекст: не измерено.")
    return lines


def graph_only_chunk_rows(summary: dict[str, Any]) -> list[dict[str, str]]:
    """Строки таблицы graph-only чанков. Пустой список — только когда измерять нечего."""
    if summary.get("status") != "measured":
        return []
    raw_rows = summary.get("rows")
    if not isinstance(raw_rows, list):
        return []
    rows: list[dict[str, str]] = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        materialized = row.get("materialized")
        in_context = row.get("in_context")
        if materialized is False:
            fate = "не материализован"
        elif in_context is True:
            fate = "да"
        elif in_context is False:
            fate = "нет"
        else:
            fate = "не измерено"
        rows.append(
            {
                "chunk_id": str(row.get("chunk_id", "")),
                "source_url": str(row.get("source_url", "")) or "источник не указан",
                "в контексте": fate,
            }
        )
    return rows


def source_rows(sources: Any) -> list[dict[str, str]]:
    """Таблица источников с осью и меткой вклада графа (ADR-075)."""
    if not isinstance(sources, list):
        return []
    rows: list[dict[str, str]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        axis_raw = str(source.get("axis") or "")
        knowledge_raw = str(source.get("graph_knowledge") or "")
        relevance = source.get("relevance")
        rows.append(
            {
                "source_url": str(source.get("source_url", "")),
                "axis": GRAPH_AXIS_LABELS.get(axis_raw, axis_raw or "—"),
                "graph_knowledge": (
                    GRAPH_KNOWLEDGE_LABELS.get(knowledge_raw, knowledge_raw or "не измерено")
                ),
                "relevance": "—" if relevance is None else str(relevance),
                "graph_knowledge_reason": str(source.get("graph_knowledge_reason") or "—"),
            }
        )
    return rows


def maintenance_preview(domain: str) -> str:
    """Текст предупреждения вкладки обслуживания — **чистая функция без Streamlit**.

    Вынесена отдельно от `_render_maintenance` намеренно: разметка Streamlit не импортируется
    ни одним тестом, и проверять её нечем. Смысловая часть — что именно оператор увидит до
    запуска — обязана быть проверяемой, поэтому она здесь, а `st.*` остаётся тонкой обёрткой.
    Это же требование снимает вопрос «до нажатия видно, что удаляется».
    """
    return (
        "Будет удалено: сиротские связи и пустые сущности домена "
        f"`{domain or '—'}`. Планировщика нет намеренно — запуск ваш выбор. "
        "Чанки уборка не трогает: их снимает удаление документа."
    )


def maintenance_result_lines(response: dict[str, Any]) -> list[str]:
    """Строки отчёта по ответу уборки — тоже чистая функция.

    Ключевое различие, которое обязано остаться видимым: `skipped` — это «не разрешено»,
    а не «удалять нечего». Смешивать их нельзя, и проверяется это здесь, а не глазами.
    """
    lines = [f"Проход завершён: `{response.get('job_id', '')}`."]
    if response.get("skipped"):
        lines.append(
            f"Проход **пропущен** политикой уборки (режим `{response.get('mode')}`), "
            "ничего не удалено. Это не «удалять нечего» — это «не разрешено»."
        )
        return lines
    lines.append(
        f"Связи: запланировано **{response.get('planned_relations')}**, "
        f"удалено **{response.get('removed_relations')}**. "
        f"Узлы: запланировано **{response.get('planned_nodes')}**, "
        f"удалено **{response.get('removed_nodes')}**."
    )
    return lines


def _render_maintenance(client: DemoClient, domains: list[str]) -> None:
    """Обслуживание графа: уборка сиротских связей и узлов по домену.

    До запуска показываем, **что** будет удалено. Уборка необратима, поэтому кнопка без
    такого предупреждения опаснее отсутствия кнопки: оператор должен видеть и предмет,
    и идентификатор прохода. Идентификатор виден до нажатия — он принадлежит операции,
    а не кнопке.
    """
    st.subheader("Обслуживание")
    st.caption(
        "Уборка удаляет связи и узлы, у которых не осталось ни одного поддерживающего чанка. "
        "Возврата нет: факт уборки пишется в реестр, и по `job_id` его можно найти."
    )
    domain = st.selectbox("Домен", options=domains, key="maintenance_domain")
    st.warning(maintenance_preview(domain))
    run_id = st.text_input(
        "Идентификатор прохода (для поиска по журналу)",
        value="maintenance-demo",
        key="maintenance_run_id",
    )
    if st.button("Запустить уборку", type="primary"):
        try:
            response = client.run_maintenance(domain)
        except RuntimeError as exc:
            st.error(str(exc))
        else:
            job_id = str(response.get("job_id", ""))
            st.success(f"Проход завершён: `{job_id}` (ваш идентификатор — `{run_id}`).")
            for line in maintenance_result_lines(response)[1:]:
                if line.startswith("Проход"):
                    st.warning(line)
                else:
                    st.write(line)
            st.caption(
                "До ADR-047 узлы попадали в счётчик связей, а счётчик узлов всегда был нулём. "
                "Теперь счётчики раздельные (ADR-047)."
            )


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

    # Проба ОДИН раз на загрузку страницы: и заголовок вкладки, и её содержимое обязаны
    # говорить об одном состоянии. Две пробы дали бы два разных ответа при нестабильной
    # сети, и вкладка выглядела бы сама по себе серой при живом сервисе.
    ingest_up = client.ingestion_is_up()
    tab_documents, tab_queries, tab_maintenance = st.tabs(
        [
            "Документы" if ingest_up else "Документы (нужен отдельный скрипт ингеста)",
            "Запросы",
            "Обслуживание",
        ]
    )
    with tab_documents:
        _render_documents(client, domains, ingest_up=ingest_up)
    with tab_queries:
        _render_queries(client, domains)
    with tab_maintenance:
        _render_maintenance(client, domains)


if __name__ == "__main__":
    main()
