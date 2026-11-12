"""Streamlit workbench: requirements -> prompt -> generation -> metrics -> report.

The app is deliberately thin. It calls the same ``src.workflow.stages``
functions as the CLI, so the two fronts cannot drift. Open it with
``python -m src.ui`` or ``streamlit run src/ui/app.py``.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, cast

import streamlit as st

from src.config import DEFAULT_MODEL
from src.generator.handlers import get_handler
from src.generator.input_doc import InputError, load_docs
from src.generator.models import ArtifactType
from src.generator.normalizer import decode_bytes
from src.generator.ollama_client import (
    OllamaClient,
    OllamaConnectionError,
    OllamaResponseError,
)
from src.generator.pipeline import GenerationRequest
from src.generator.presets import (
    Preset,
    PresetError,
    load_presets,
    resolve_presets_dir,
    select_extras,
)
from src.generator.prompt import PromptError
from src.models import METRIC_KEYS, PipelineData
from src.quality.assessor import extract_requirement_ids
from src.workflow import stages

TYPE_LABELS: dict[ArtifactType, str] = {
    ArtifactType.TESTCASES: "Тест-кейсы",
    ArtifactType.CHECKLIST: "Чек-лист",
    ArtifactType.TESTPLAN: "Тест-план",
    ArtifactType.QAREPORT: "QA-отчёт",
    ArtifactType.COVERAGEMATRIX: "Матрица покрытия",
}

METRIC_LABELS: dict[str, str] = {
    "readability": "Readability",
    "clarity_goals": "Clarity of goals",
    "scenario_coverage": "Scenario coverage",
    "consistency": "Consistency",
    "overall": "Overall",
}

MANUAL_MODEL = "Другая модель (ввести вручную)"

CONNECTION_PRESETS: tuple[tuple[str, str], ...] = (
    ("Локальный Ollama (localhost:11434)", "http://localhost:11434"),
    ("Ollama в Docker-сети (ohw-ollama:11434)", "http://ohw-ollama:11434"),
    (
        "Docker Desktop (host.docker.internal:11434)",
        "http://host.docker.internal:11434",
    ),
    ("Свой адрес", ""),
)


def main() -> None:
    st.set_page_config(
        page_title="Генератор тестовой документации",
        layout="wide",
    )
    _init_state()
    _sidebar()

    st.title("Рабочее место: тестовая документация и её качество")
    st.caption(
        "Требования → локальная Ollama → артефакты → метрики → отчёт. "
        "UI и CLI используют одни и те же стадии."
    )

    tabs = st.tabs(
        [
            "1. Требования (DZ2)",
            "2. Промпт (DZ7)",
            "3. Генерация (DZ2)",
            "4. Метрики (DZ8)",
            "5. Отчёт",
        ]
    )
    with tabs[0]:
        _requirements_tab()
    with tabs[1]:
        _prompt_tab()
    with tabs[2]:
        _generation_tab()
    with tabs[3]:
        _metrics_tab()
    with tabs[4]:
        _report_tab()


# --------------------------------------------------------------------------
# State and sidebar
# --------------------------------------------------------------------------


def _init_state() -> None:
    defaults: dict[str, Any] = {
        "base_url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        "model": os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL),
        "timeout": float(os.environ.get("OLLAMA_TIMEOUT", "600")),
        "temperature": 0.7,
        "output_dir": os.environ.get("GRADUATION_OUTPUT_DIR", "output"),
        "models": [],
        "models_error": "",
        "ui_files": {},
        "ui_signatures": {},
        "ui_prompts": {},
        "data": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _sidebar() -> None:
    with st.sidebar:
        st.header("Подключение к модели")
        _connection_panel()
        st.divider()
        st.text_input(
            "Папка результатов",
            key="output_dir",
            help="Сюда пишутся scenarios, charts и отчёты.",
        )
        st.caption("Требования загружаются на вкладке 1.")
        st.caption("Сгенерированные файлы: `output/scenarios/`.")


def _connection_panel() -> None:
    labels = [label for label, _ in CONNECTION_PRESETS]
    current_url = str(st.session_state.get("base_url", ""))
    preset_index = next(
        (
            index
            for index, (_, url) in enumerate(CONNECTION_PRESETS)
            if url and url == current_url
        ),
        len(CONNECTION_PRESETS) - 1,
    )
    st.selectbox(
        "Способ подключения",
        labels,
        index=preset_index,
        key="connection_preset",
        on_change=_apply_connection_preset,
        help="Готовые адреса Ollama; «Свой адрес» оставляет URL для ручного ввода.",
    )
    st.text_input(
        "Ollama URL",
        key="base_url",
        help="Из контейнера в общей сети — http://ohw-ollama:11434.",
    )

    if st.button(
        "Загрузить модели",
        help="Запрос GET /api/tags: показывает установленные модели.",
    ):
        _refresh_models()
        st.rerun()

    options = _model_options()
    if st.session_state.get("model_choice") not in options:
        st.session_state.pop("model_choice", None)
    model = str(st.session_state.get("model", "")).strip()
    default_index = (
        options.index(model) if model in options else options.index(MANUAL_MODEL)
    )
    choice = st.selectbox(
        "Модель из Ollama",
        options,
        index=default_index,
        key="model_choice",
        on_change=_apply_model_choice,
        help="Список заполняется кнопкой «Загрузить модели».",
    )
    if choice == MANUAL_MODEL:
        st.text_input(
            "Модель",
            key="model",
            placeholder="например qwen2.5:7b-instruct",
        )
    else:
        st.session_state["model"] = choice

    models = st.session_state.get("models") or []
    if models:
        st.caption("Скачано: " + ", ".join(models))
    elif not st.session_state.get("models_error"):
        st.caption("Список моделей не загружен — нажмите «Загрузить модели».")
    if st.session_state.get("models_error"):
        st.error(st.session_state["models_error"])

    with st.expander("Параметры генерации"):
        st.slider("Температура", 0.0, 2.0, step=0.05, key="temperature")
        st.number_input(
            "Таймаут запроса, с",
            min_value=30.0,
            max_value=7200.0,
            step=30.0,
            key="timeout",
        )


def _model_options() -> list[str]:
    current = str(st.session_state.get("model", "")).strip()
    probed = st.session_state.get("models") or []
    options = list(dict.fromkeys(name for name in [current, *probed] if name))
    options.append(MANUAL_MODEL)
    return options


def _apply_connection_preset() -> None:
    url = dict(CONNECTION_PRESETS).get(
        str(st.session_state.get("connection_preset", "")), ""
    )
    if url:
        st.session_state["base_url"] = url
    st.session_state["models"] = []
    st.session_state["models_error"] = ""


def _apply_model_choice() -> None:
    choice = st.session_state.get("model_choice")
    if choice and choice != MANUAL_MODEL:
        st.session_state["model"] = choice


def _refresh_models() -> None:
    client = _client(show_error=False)
    if client is None:
        return
    try:
        models = client.list_models()
    except (OllamaConnectionError, OllamaResponseError) as exc:
        st.session_state["models"] = []
        st.session_state["models_error"] = str(exc)
        return
    st.session_state["models"] = models
    st.session_state["models_error"] = ""
    current = str(st.session_state.get("model", "")).strip()
    if models and current not in models:
        st.session_state["model"] = models[0]
    st.session_state.pop("model_choice", None)


def _client(*, show_error: bool = True) -> OllamaClient | None:
    model = str(st.session_state.get("model", "")).strip()
    if not model:
        if show_error:
            st.error("Модель не задана: укажите её в боковой панели.")
        return None
    base_url = str(st.session_state.get("base_url", "")).strip()
    return OllamaClient(
        base_url=base_url or "http://localhost:11434",
        model=model,
        timeout=float(st.session_state.get("timeout", 600.0)),
    )


# --------------------------------------------------------------------------
# Tab 1: requirements
# --------------------------------------------------------------------------


def _requirements_tab() -> None:
    st.caption(
        "Загрузите файлы .md или .txt. Текст можно править: перед генерацией он "
        "копируется в output/ui-input/."
    )
    uploaded = st.file_uploader(
        "Файлы требований",
        type=["md", "txt"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )
    if uploaded:
        _remember_uploads(uploaded)

    files: dict[str, str] = st.session_state["ui_files"]
    if not files:
        st.info(
            "Файлы не загружены. Перетащите .md/.txt в поле выше или выберите "
            "через «Browse files»."
        )
        st.session_state["ui_requirements"] = None
        return

    names = list(files)
    if st.session_state.get("ui_selected") not in names:
        st.session_state.pop("ui_selected", None)
    selected = st.selectbox("Файл требований", names, key="ui_selected")
    text_key = f"ui_text_{selected}"
    if text_key not in st.session_state:
        st.session_state[text_key] = files[selected]
    text = st.text_area(
        "Текст требований (можно править)",
        key=text_key,
        height=320,
    )
    files[selected] = text

    url = st.text_input(
        "URL тестируемого сайта (необязательно)",
        key="ui_url",
        help="Если пусто, URL берётся из текста требований.",
    )

    columns = st.columns(3)
    artifact_type = cast(
        ArtifactType,
        columns[0].selectbox(
            "Тип артефакта",
            list(ArtifactType),
            format_func=lambda item: TYPE_LABELS[item],
            key="ui_type",
        ),
    )
    count = columns[1].number_input(
        "Кейсов на файл", min_value=1, max_value=100, value=10, key="ui_count"
    )
    runs = columns[2].number_input(
        "Повторов", min_value=1, max_value=10, value=1, key="ui_runs"
    )

    presets = _load_presets()
    applicable = [
        preset for preset in presets.values() if preset.applies_to(artifact_type)
    ]
    selected_presets = st.multiselect(
        "Пресеты (готовые дополнения к промпту)",
        options=[preset.id for preset in applicable],
        format_func=lambda preset_id: next(
            preset.label for preset in applicable if preset.id == preset_id
        ),
        key="ui_presets",
    )
    normalize = st.checkbox(
        "Нормализовать требования перед сборкой промпта",
        key="ui_normalize",
        help="Детерминированный нормализатор: заголовки, URL, разделы BR-N.",
    )

    requirement_ids = extract_requirement_ids(text)
    columns = st.columns(3)
    columns[0].metric("Требований с ID", len(set(requirement_ids)))
    columns[1].metric("Символов", len(text))
    columns[2].metric("Файлов", len(files))

    new_config = {
        "files": dict(files),
        "url": url.strip() or None,
        "artifact_type": artifact_type,
        "count": int(count),
        "runs": int(runs),
        "normalize": bool(normalize),
        "presets": tuple(str(item) for item in selected_presets),
    }
    previous = st.session_state.get("ui_requirements")
    if previous is not None and _prompt_inputs(previous) != _prompt_inputs(new_config):
        # A prompt built for an older type/count/presets must not leak into the
        # next generation.
        st.session_state["ui_prompts"] = {}
    st.session_state["ui_requirements"] = new_config


def _prompt_inputs(config: dict[str, Any]) -> tuple[Any, ...]:
    """The fields that change the rendered prompt for every file."""
    return (
        tuple(sorted(config["files"].items())),
        config["url"],
        config["artifact_type"],
        config["count"],
        config["normalize"],
        config["presets"],
    )


def _sync_inputs(config: dict[str, Any]) -> Path:
    """Copy the edited requirements into ``output/ui-input/``."""
    output_dir = Path(st.session_state["output_dir"])
    input_dir = output_dir / "ui-input"
    input_dir.mkdir(parents=True, exist_ok=True)
    for name, text in config["files"].items():
        (input_dir / name).write_text(text, encoding="utf-8")
    return input_dir


def _remember_uploads(uploaded: list[Any]) -> None:
    for item in uploaded:
        data = item.getvalue()
        signature = hashlib.sha256(data).hexdigest()
        if st.session_state["ui_signatures"].get(item.name) == signature:
            continue
        text, _encoding = decode_bytes(data)
        st.session_state["ui_files"][item.name] = text
        st.session_state["ui_signatures"][item.name] = signature
        st.session_state.pop(f"ui_text_{item.name}", None)


def _load_presets() -> dict[str, Preset]:
    directory = resolve_presets_dir()
    if directory is None:
        return {}
    try:
        return load_presets(directory)
    except PresetError as exc:
        st.warning(f"Пресеты не загружены: {exc}")
        return {}


# --------------------------------------------------------------------------
# Tab 2: prompt (DZ7 templates and presets)
# --------------------------------------------------------------------------


def _prompt_tab() -> None:
    config = st.session_state.get("ui_requirements")
    if not config:
        st.info("Сначала загрузите файл требований на вкладке 1.")
        return

    input_dir = _sync_inputs(config)
    selected_value = st.session_state.get("ui_selected")
    selected: str = (
        str(selected_value)
        if selected_value in config["files"]
        else next(iter(config["files"]))
    )

    try:
        docs = load_docs(
            input_dir,
            config["url"],
            normalize=bool(config["normalize"]),
        )
        doc = next((item for item in docs if item.path.name == selected), None)
        if doc is None:
            st.warning(f"Файл {selected!r} не найден во входной папке.")
            return
        extras = select_extras(
            _load_presets(),
            list(config["presets"]),
            config["artifact_type"],
        )
        handler = get_handler(config["artifact_type"])
        rendered = handler.build_user(doc, int(config["count"]), extras)
    except (InputError, PresetError, PromptError) as exc:
        st.error(str(exc))
        return

    st.caption(
        "DZ7: шаблон артефакта и выбранные пресеты. Промпт можно править — "
        "правка применяется к выбранному файлу в текущей сессии."
    )
    signature = (
        f"{selected}|{hashlib.sha256(doc.content.encode('utf-8')).hexdigest()}|"
        f"{config['artifact_type']}|{config['count']}|"
        f"{','.join(config['presets'])}|{bool(config['normalize'])}"
    )
    if st.session_state.get("ui_prompt_signature") != signature:
        st.session_state["ui_prompt_signature"] = signature
        st.session_state["ui_prompt_text"] = rendered
    if st.button("Собрать промпт заново"):
        st.session_state["ui_prompt_text"] = rendered

    prompt = st.text_area(
        "Промпт для модели",
        key="ui_prompt_text",
        height=360,
    )
    st.download_button(
        "Скачать промпт",
        data=prompt,
        file_name=f"{Path(selected).stem}.{config['artifact_type'].value}.prompt.txt",
        mime="text/plain",
        key="download_prompt",
    )
    st.session_state["ui_prompts"][selected] = prompt


# --------------------------------------------------------------------------
# Tab 3: generation
# --------------------------------------------------------------------------


def _generation_tab() -> None:
    config = st.session_state.get("ui_requirements")
    if not config:
        st.info("Сначала загрузите файл требований на вкладке 1.")
        return

    st.caption(
        f"Файлов: {len(config['files'])} · тип: "
        f"{TYPE_LABELS[config['artifact_type']]} · кейсов: {config['count']} · "
        f"повторов: {config['runs']}"
    )
    if st.button("Сгенерировать", type="primary"):
        _run_generation(config)

    data: PipelineData | None = st.session_state.get("data")
    if data is not None:
        _show_generation(data)


def _run_generation(config: dict[str, Any]) -> None:
    output_dir = Path(st.session_state["output_dir"])
    input_dir = _sync_inputs(config)

    request = GenerationRequest(
        input_dir=input_dir,
        artifact_type=config["artifact_type"],
        count=int(config["count"]),
        runs=int(config["runs"]),
        temperature=float(st.session_state["temperature"]),
        max_retries=3,
        url=config["url"],
        normalize=bool(config["normalize"]),
        presets=tuple(config["presets"]),
        output_dir=output_dir,
        prompts=dict(st.session_state.get("ui_prompts") or {}),
    )
    client = _client()
    if client is None:
        return

    notices: list[str] = []

    def remember(notice: Any) -> None:
        notices.append(
            f"попытка {notice.attempt}/{notice.total_attempts}: "
            + "; ".join(notice.issues)
        )

    try:
        with st.spinner("Ollama генерирует артефакты..."):
            data = stages.generate(request, client, on_retry=remember)
    except (OllamaConnectionError, OllamaResponseError, PresetError) as exc:
        st.error(str(exc))
        for notice in notices:
            st.warning(notice)
        return

    st.session_state["data"] = data
    if notices:
        st.warning("Модель исправлялась:\n\n" + "\n\n".join(notices))
    if data.scenarios:
        st.success(
            f"Готово: {len(data.scenarios)} сценариев, "
            f"файлы в `{output_dir / 'scenarios'}`."
        )
    else:
        st.error("Ни один сценарий не сгенерирован; смотрите ошибки ниже.")


def _show_generation(data: PipelineData) -> None:
    if data.errors:
        st.warning("Ошибки файлов:\n\n" + "\n\n".join(data.errors))
    if not data.scenarios:
        return
    for scenario in data.scenarios:
        overall = (
            "n/a"
            if scenario.quality.overall is None
            else f"{scenario.quality.overall:.3f}"
        )
        with st.expander(
            f"{scenario.filename} — overall {overall}, "
            f"попыток {scenario.attempts}"
        ):
            st.markdown(scenario.rendered)
            st.download_button(
                "Скачать артефакт",
                data=scenario.rendered,
                file_name=scenario.filename,
                mime="text/markdown",
                key=f"download_{scenario.filename}",
            )


# --------------------------------------------------------------------------
# Tab 4: metrics
# --------------------------------------------------------------------------


def _metrics_tab() -> None:
    data: PipelineData | None = st.session_state.get("data")
    if data is None:
        st.info("Сначала выполните генерацию на вкладке 2.")
        return

    output_dir = Path(st.session_state["output_dir"])
    columns = st.columns(2)
    if columns[0].button("Пересчитать метрики"):
        data = stages.assess(data, output_dir)
        st.session_state["data"] = data
        st.success("Метрики пересчитаны по сохранённым генерациям.")
    if columns[1].button("Построить графики"):
        data = stages.visualize(data, output_dir)
        st.session_state["data"] = data
        if data.charts:
            st.success("Графики построены.")
        else:
            st.warning("Нет метрик для построения графиков.")

    st.subheader("Средние метрики")
    metric_columns = st.columns(len(METRIC_KEYS) + 1)
    for column, key in zip(metric_columns, (*METRIC_KEYS, "overall"), strict=True):
        column.metric(METRIC_LABELS[key], _fmt(data.averages.get(key)))

    st.subheader("По сценариям")
    rows = [
        {
            "Файл": scenario.filename,
            "Overall": _fmt(scenario.quality.overall),
            "Readability": _fmt(scenario.quality.readability),
            "Clarity": _fmt(scenario.quality.clarity_goals),
            "Coverage": _fmt(scenario.quality.scenario_coverage),
            "Consistency": _fmt(scenario.quality.consistency),
            "Попыток": scenario.attempts,
        }
        for scenario in data.scenarios
    ]
    st.dataframe(rows, hide_index=True, width="stretch")

    if data.charts:
        st.subheader("Графики")
        titles = {
            "boxplot": "Boxplot распределения метрик",
            "radar": "Radar средних метрик",
            "per_scenario": "Оценки по сценариям",
        }
        for key, path in data.charts.items():
            chart_path = Path(path)
            if not chart_path.is_absolute():
                chart_path = output_dir / chart_path
            st.image(chart_path, caption=titles.get(key, key))


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


# --------------------------------------------------------------------------
# Tab 5: report
# --------------------------------------------------------------------------


def _report_tab() -> None:
    data: PipelineData | None = st.session_state.get("data")
    if data is None:
        st.info("Сначала выполните генерацию на вкладке 2.")
        return

    output_dir = Path(st.session_state["output_dir"])
    if st.button("Сформировать отчёт", type="primary"):
        paths = stages.report(data, output_dir)
        st.success(f"Отчёт: {paths['markdown']}")

    report_path = output_dir / "report.md"
    json_path = output_dir / "quality-metrics.json"
    if not report_path.is_file():
        st.info("Отчёт ещё не сформирован.")
        return

    st.download_button(
        "Скачать report.md",
        data=report_path.read_bytes(),
        file_name="report.md",
        mime="text/markdown",
    )
    if json_path.is_file():
        st.download_button(
            "Скачать quality-metrics.json",
            data=json_path.read_bytes(),
            file_name="quality-metrics.json",
            mime="application/json",
        )
    st.divider()
    st.markdown(report_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
