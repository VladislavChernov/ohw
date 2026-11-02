"""Streamlit workbench: requirements -> prompt -> Ollama -> artifact.

The app is intentionally thin: it calls the same ``ai_testgen`` core as the
CLI (``service.generate_artifact``), so the two fronts cannot drift. Open it
with ``ai-testgen-ui`` or ``streamlit run src/ai_testgen/ui/app.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, cast

import streamlit as st

from ai_testgen.config import Config, load_config
from ai_testgen.evaluation import (
    CRITERIA,
    CRITERIA_LABELS,
    EvaluationError,
    EvaluationResult,
    artifact_reference,
    evaluate_prompt,
    quality_filename,
    quality_record,
)
from ai_testgen.handlers import ArtifactHandler, get_handler
from ai_testgen.input_doc import RequirementDoc, extract_url
from ai_testgen.models import ArtifactType, GenerationResult
from ai_testgen.normalizer import (
    NormalizationError,
    NormalizationResult,
    NormalizerSettings,
    NormalizerSettingsError,
    decode_bytes,
    default_settings_path,
    normalize_text,
)
from ai_testgen.normalizer import (
    load_settings as load_normalizer_settings,
)
from ai_testgen.ollama_client import (
    OllamaClient,
    OllamaConnectionError,
    OllamaResponseError,
)
from ai_testgen.presets import (
    Preset,
    PresetError,
    load_presets,
    resolve_presets_dir,
    select_extras,
)
from ai_testgen.prompt import PromptError, resolve_prompt_dir
from ai_testgen.service import (
    GeneratedArtifact,
    GenerationError,
    GenerationOptions,
    RetryNotice,
    generate_artifact,
    testcase_index,
)

TYPE_LABELS: dict[ArtifactType, str] = {
    ArtifactType.TESTCASES: "Тест-кейсы",
    ArtifactType.CHECKLIST: "Чек-лист",
    ArtifactType.TESTPLAN: "Тест-план",
    ArtifactType.QAREPORT: "QA-отчёт",
    ArtifactType.COVERAGEMATRIX: "Матрица покрытия",
}

def _id_pattern(prefix: str) -> re.Pattern[str]:
    """Requirement ids: ``BR-N`` always, plus the configured prefix."""
    names = ["BR"] if prefix.upper() == "BR" else ["BR", prefix]
    alternation = "|".join(re.escape(name) for name in names)
    return re.compile(rf"\b(?:{alternation})-\d+\b", re.IGNORECASE)
_FENCE_PATTERN = re.compile(r"^\s*```[a-zA-Z]*\s*$", re.MULTILINE)

MANUAL_MODEL_OPTION = "Другая модель (ввести вручную)"

STRATEGY_LABELS: dict[str, str] = {
    "heading": "разделы BR-N",
    "table": "таблица",
    "list": "список",
    "inline": "строки BR-N",
    "paragraph": "абзацы",
}

#: Ready-made Ollama addresses. The last entry keeps the URL below editable.
CONNECTION_PRESETS: tuple[tuple[str, str], ...] = (
    ("Локальный Ollama (localhost:11434)", "http://localhost:11434"),
    ("Ollama в Docker-сети (ohw-ollama:11434)", "http://ohw-ollama:11434"),
    ("Docker Desktop (host.docker.internal:11434)", "http://host.docker.internal:11434"),
    ("Свой адрес", ""),
)


def main() -> None:
    st.set_page_config(page_title="Генератор тестов по требованиям", layout="wide")
    _render()


def _render() -> None:
    st.title("Рабочее место: тесты по требованиям")
    st.caption(
        "Загруженные требования → промпт из шаблона Dz7 → "
        "локальная Ollama → проверенный артефакт."
    )

    try:
        settings = load_config(None)
        prompt_dir = resolve_prompt_dir(settings.prompt_dir)
        presets = _presets(settings)
    except (PromptError, PresetError) as exc:
        st.error(str(exc))
        return

    st.session_state["prompt_dir"] = str(prompt_dir)
    # Same precedence as the CLI: env (how Docker passes the address) > config.
    if "base_url" not in st.session_state:
        st.session_state["base_url"] = (
            os.environ.get("OLLAMA_BASE_URL") or settings.base_url
        )
    if "model" not in st.session_state:
        st.session_state["model"] = os.environ.get("OLLAMA_MODEL") or settings.model
    if "temperature" not in st.session_state:
        st.session_state["temperature"] = settings.temperature
    if "timeout" not in st.session_state:
        st.session_state["timeout"] = settings.timeout
    if "models" not in st.session_state:
        st.session_state["models"] = []
    _sidebar(settings)

    requirement_tab, prompt_tab, result_tab, chat_tab, quality_tab = st.tabs(
        ["1. Требования", "2. Промпт", "3. Результат", "4. Чат с LLM", "5. Оценка"]
    )

    with requirement_tab:
        source = _requirements_panel()

    if source is None:
        with prompt_tab:
            st.info("Сначала загрузите файл требований на вкладке 1.")
        with result_tab:
            st.info("Промпт собирается после загрузки требований.")
        with chat_tab:
            st.info("Чат доступен после генерации артефакта.")
        with quality_tab:
            st.info("Оценка станет доступна после загрузки требований.")
        return

    with prompt_tab:
        artifact_type, count, _, prompt_text = _prompt_panel(
            source, prompt_dir, presets
        )
    with result_tab:
        _result_panel(source, artifact_type, count, prompt_text, settings)
    with chat_tab:
        _chat_panel(source, artifact_type, count, prompt_text)
    with quality_tab:
        _quality_panel(settings, artifact_type, prompt_text, source)


# --------------------------------------------------------------------------
# Sidebar and discovery
# --------------------------------------------------------------------------


def _sidebar(settings: Config) -> None:
    with st.sidebar:
        st.header("Подключение к модели")
        _connection_panel()
        st.divider()
        st.caption("Требования: файл загружается на вкладке 1")
        st.caption(f"Шаблоны: `{settings.prompt_dir or './prompts'}`")
        st.caption(f"Пресеты: `{settings.presets_dir or './presets'}`")
        st.caption(f"Результаты: `{settings.output_dir}`")


def _connection_panel() -> None:
    """Address presets, model picker and generation parameters.

    Both the address and the model can be chosen in two ways: from ready-made
    options (probed with ``GET /api/tags``) or typed by hand. The URL stays a
    normal editable field, so the preset is only a shortcut.
    """
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
        help=(
            "Готовые адреса Ollama. «Свой адрес» оставляет поле URL ниже "
            "для ручного ввода."
        ),
    )
    st.text_input(
        "Ollama URL",
        key="base_url",
        help=(
            "Адрес сервиса Ollama. Из контейнера в общей сети — "
            "http://ohw-ollama:11434, из devcontainer — "
            "http://host.docker.internal:11434."
        ),
    )

    if st.button(
        "Загрузить модели",
        help="Запрос GET /api/tags: показывает, какие модели уже скачаны.",
    ):
        _refresh_models()
        st.rerun()

    options = _model_options()
    if st.session_state.get("model_choice") not in options:
        st.session_state.pop("model_choice", None)
    model = str(st.session_state.get("model", "")).strip()
    default_index = (
        options.index(model) if model in options else options.index(MANUAL_MODEL_OPTION)
    )
    choice = st.selectbox(
        "Модель из Ollama",
        options,
        index=default_index,
        key="model_choice",
        on_change=_apply_model_choice,
        help=(
            "Список заполняется кнопкой «Загрузить модели». Если нужной модели "
            "нет — выберите «Другая модель» и введите имя вручную."
        ),
    )
    if choice == MANUAL_MODEL_OPTION:
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
            help=(
                "Сколько ждать ответ модели. Для CPU-инференса "
                "qwen2.5:7b разумно 600–1200 секунд."
            ),
        )


def _model_options() -> list[str]:
    """Configured model plus the probed ones, deduplicated, with the manual entry."""
    current = str(st.session_state.get("model", "")).strip()
    probed = st.session_state.get("models") or []
    options = list(dict.fromkeys(name for name in [current, *probed] if name))
    options.append(MANUAL_MODEL_OPTION)
    return options


def _apply_connection_preset() -> None:
    url = dict(CONNECTION_PRESETS).get(
        str(st.session_state.get("connection_preset", "")), ""
    )
    if url:
        st.session_state["base_url"] = url
    # The probe result belongs to the previous address.
    st.session_state["models"] = []
    st.session_state["models_error"] = ""


def _apply_model_choice() -> None:
    choice = st.session_state.get("model_choice")
    if choice and choice != MANUAL_MODEL_OPTION:
        st.session_state["model"] = choice


def _refresh_models() -> None:
    client = OllamaClient(
        base_url=str(st.session_state.get("base_url", "")),
        model=str(st.session_state.get("model", "")),
        timeout=float(st.session_state.get("timeout", 180.0)),
    )
    try:
        models = client.list_models()
        st.session_state["models"] = models
        st.session_state["models_error"] = ""
        current = str(st.session_state.get("model", "")).strip()
        if models and current not in models:
            # Picking the first downloaded model saves typing when the address
            # was just switched and the configured name is not installed here.
            st.session_state["model"] = models[0]
        st.session_state.pop("model_choice", None)
    except (OllamaConnectionError, OllamaResponseError) as exc:
        st.session_state["models"] = []
        st.session_state["models_error"] = str(exc)


def _presets(settings: Config) -> dict[str, Preset]:
    directory = resolve_presets_dir(settings.presets_dir)
    if directory is None:
        return {}
    return load_presets(directory)


# --------------------------------------------------------------------------
# Tabs
# --------------------------------------------------------------------------


def _requirements_panel() -> RequirementDoc | None:
    st.caption(
        "Загрузите файл требований (.md или .txt). Можно выбрать несколько — "
        "артефакт собирается для выбранного файла."
    )
    uploaded = st.file_uploader(
        "Файлы требований",
        type=["md", "txt"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )
    if not uploaded:
        st.info(
            "Файлы не загружены. Перетащите .md/.txt в поле выше или выберите "
            "через «Browse files»."
        )
        return None

    names = [item.name for item in uploaded]
    if st.session_state.get("source_name") not in names:
        st.session_state.pop("source_name", None)
    selected = st.selectbox("Файл требований", names, key="source_name")
    file = next(item for item in uploaded if item.name == selected)
    data = file.getvalue()

    signature = hashlib.sha256(data).hexdigest()
    if st.session_state.get("requirements_signature") != signature:
        st.session_state["requirements_signature"] = signature
        file_settings, _ = _normalizer_defaults()
        text, encoding = decode_bytes(data, file_settings)
        st.session_state["requirements_text"] = text
        st.session_state["requirements_encoding"] = encoding
        st.session_state["url_input"] = requirements_url(text)
        st.session_state.pop("normalization", None)
        st.session_state.pop("use_normalized", None)

    text = st.text_area(
        "Текст требований (можно править)",
        key="requirements_text",
        height=320,
    )
    url = st.text_input(
        "URL тестируемого сайта (пусто — взять из текста)",
        key="url_input",
    )
    encoding = str(st.session_state.get("requirements_encoding", "utf-8"))
    if encoding != "utf-8":
        st.caption(
            f"Кодировка файла: {encoding} — текст перекодирован в UTF-8. "
            "В промпт уйдёт UTF-8."
        )

    default_settings, _ = _normalizer_defaults()
    requirements = _id_pattern(default_settings.id_prefix).findall(text)
    columns = st.columns(3)
    columns[0].metric("Требований с ID", len({item.upper() for item in requirements}))
    columns[1].metric("URL", url or "не найден")
    columns[2].metric("Символов", len(text))

    content = _normalization_panel(text, url, selected, encoding)
    return RequirementDoc(
        path=Path(selected),
        content=content,
        url=url or extract_url(text),
    )


def _normalization_panel(text: str, url: str, name: str, encoding: str) -> str:
    """Show the canonical form and return the text that goes into the prompt."""
    st.subheader("Нормализация требований")
    heading = _normalizer_defaults()[0].requirements_heading
    st.caption(
        f"Детерминированно, без модели: заголовок, URL, раздел «{heading}» и "
        "по разделу на каждое требование с ID."
    )
    if not text.strip():
        st.caption("Загрузите требования, чтобы нормализовать их.")
        return text

    settings = _normalization_settings_form()

    if st.button("Нормализовать", key="normalize_requirements"):
        try:
            normalized = normalize_text(
                text, url or None, encoding=encoding, settings=settings
            )
        except NormalizationError as exc:
            st.session_state.pop("normalization", None)
            st.error(f"Нормализация не удалась: {exc}")
            return text
        st.session_state["normalization"] = {
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "settings": settings,
            "result": normalized,
        }

    stored = st.session_state.get("normalization")
    if not stored:
        if not _id_pattern(settings.id_prefix).search(text):
            st.warning(
                f"В тексте нет ID с префиксом {settings.id_prefix}: трассировка "
                "и матрица покрытия потеряют ссылки на требования. Нажмите "
                "«Нормализовать»."
            )
        return text

    current = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if stored["sha256"] != current:
        st.warning(
            "Текст изменён после нормализации — нажмите «Нормализовать» заново, "
            "иначе он не попадёт в промпт."
        )
        return text
    if stored.get("settings") != settings:
        st.warning(
            "Настройки нормализации изменены — нажмите «Нормализовать» заново, "
            "чтобы применить их."
        )
        return text

    result: NormalizationResult = stored["result"]
    columns = st.columns(3)
    columns[0].metric("Требований после нормализации", len(result.requirements))
    columns[1].metric("Кодировка", result.encoding)
    columns[2].metric(
        "Стратегия", STRATEGY_LABELS.get(result.strategy, result.strategy)
    )
    for warning in result.warnings:
        st.warning(warning)
    if not result.changed:
        st.caption("Текст уже в каноническом виде: нормализация ничего не изменила.")

    original_tab, normalized_tab = st.tabs(["Исходный", "Нормализованный"])
    with original_tab:
        st.code(text, language="markdown")
    with normalized_tab:
        st.code(result.text, language="markdown")

    use_normalized = st.checkbox(
        "Использовать нормализованный текст в промптах",
        key="use_normalized",
        help=(
            "Если выключено, в промпт уходит текст из редактора. "
            "Нормализованный текст проверен на свежесть по sha256."
        ),
    )
    st.download_button(
        "Скачать нормализованный текст",
        data=result.text,
        file_name=f"{Path(name).stem}.normalized.md",
        mime="text/markdown",
    )
    if use_normalized:
        st.caption("В промпт уйдёт нормализованный текст.")
        return result.text
    st.caption("В промпт уйдёт текст из редактора.")
    return text


_NORMALIZER_ENCODINGS = ("utf-8", "cp1251", "cp866", "koi8-r")
_NORMALIZER_WIDGET_KEYS = (
    "nz_prefix",
    "nz_heading",
    "nz_url_label",
    "nz_fallback_title",
    "nz_encodings",
    "nz_min_length",
)


def _normalizer_defaults() -> tuple[NormalizerSettings, Path | None]:
    """Settings from ``normalizer.toml`` (or the built-in defaults), cached."""
    cached = st.session_state.get("normalizer_defaults")
    if cached is not None:
        return cached
    path = default_settings_path()
    try:
        settings = load_normalizer_settings(path)
    except NormalizerSettingsError as exc:
        st.session_state["normalizer_error"] = str(exc)
        settings = NormalizerSettings()
    st.session_state["normalizer_defaults"] = (settings, path)
    return settings, path


def _normalization_settings_form() -> NormalizerSettings:
    """Widgets over the file defaults; the file itself stays the source of truth."""
    base, path = _normalizer_defaults()
    error = st.session_state.pop("normalizer_error", None)
    if error:
        st.warning(f"Настройки из файла не применены: {error}")

    with st.expander("Настройки нормализации", expanded=False):
        st.caption(
            f"По умолчанию: {path.name if path else 'встроенные значения'} "
            f"({path if path else 'normalizer.toml не найден'}). Правки ниже "
            "действуют на текущую сессию; чтобы изменить значения по умолчанию, "
            "правьте файл."
        )
        prefix = st.text_input(
            "Префикс ID требований", value=base.id_prefix, key="nz_prefix", max_chars=10
        )
        heading = st.text_input(
            "Заголовок раздела", value=base.requirements_heading, key="nz_heading"
        )
        url_label = st.text_input(
            "Подпись строки URL", value=base.url_label, key="nz_url_label"
        )
        fallback_title = st.text_input(
            "Заголовок, если его нет в тексте",
            value=base.fallback_title,
            key="nz_fallback_title",
        )
        encodings = st.multiselect(
            "Кодировки для определения (после UTF-8 и BOM)",
            options=list(_NORMALIZER_ENCODINGS),
            default=[name for name in base.encodings],
            key="nz_encodings",
            help="Порядок важен: при равном счёте выигрывает первая кодировка.",
        )
        min_length = st.number_input(
            "Минимальная длина абзаца в fallback-режиме",
            min_value=1,
            max_value=2000,
            value=base.paragraph_min_length,
            step=1,
            key="nz_min_length",
        )
        if st.button("Вернуть значения из файла", key="nz_reset"):
            for key in _NORMALIZER_WIDGET_KEYS:
                st.session_state.pop(key, None)
            st.session_state.pop("normalizer_defaults", None)
            st.rerun()

    try:
        return NormalizerSettings(
            id_prefix=prefix.strip(),
            requirements_heading=heading.strip(),
            url_label=url_label.strip(),
            fallback_title=fallback_title.strip(),
            encodings=tuple(encodings),
            paragraph_min_length=int(min_length),
        )
    except NormalizerSettingsError as exc:
        st.error(f"Настройки нормализации некорректны: {exc}")
        return base


def requirements_url(text: str) -> str:
    """First URL of the requirements text (empty string when absent)."""
    return extract_url(text) or ""


def _prompt_panel(
    doc: RequirementDoc,
    prompt_dir: Path,
    presets: dict[str, Preset],
) -> tuple[ArtifactType, int, list[str], str]:
    columns = st.columns([2, 1])
    artifact_type = cast(
        ArtifactType,
        columns[0].selectbox(
            "Тип артефакта",
            list(ArtifactType),
            format_func=lambda item: TYPE_LABELS[item],
            key="artifact_type",
        ),
    )
    count = columns[1].number_input(
        "Тест-кейсов", min_value=1, max_value=50, value=10, key="count"
    )

    applicable = [preset for preset in presets.values() if preset.applies_to(artifact_type)]
    selected = st.multiselect(
        "Пресеты (готовые примеры запросов)",
        options=[preset.id for preset in applicable],
        format_func=lambda preset_id: next(
            preset.label for preset in applicable if preset.id == preset_id
        ),
        key="preset_ids",
    )
    extras = _extras(presets, selected, artifact_type)
    st.caption(
        "Пресет добавляет блок «Additional instructions» в промпт. "
        "Правка ниже действует только на текущий запуск."
    )
    testcases = _testcases_for_prompt(doc, artifact_type)
    if artifact_type is ArtifactType.COVERAGEMATRIX:
        st.caption(
            "Матрица соберётся по тест-кейсам этого файла из вкладки 3."
            if testcases
            else "Тест-кейсы для этого файла ещё не сгенерированы: матрица "
            "будет без TC-id (—/Not Covered), а не с придуманными номерами."
        )

    signature = (
        f"{doc.path}|{hashlib.sha256(doc.content.encode('utf-8')).hexdigest()}|"
        f"{doc.url}|{artifact_type}|{count}|{','.join(selected)}|{bool(testcases)}"
    )
    try:
        handler = get_handler(artifact_type, prompt_dir)
        rendered = handler.build_user(doc, int(count), extras, testcases)
    except PromptError as exc:
        st.error(str(exc))
        return artifact_type, int(count), extras, ""

    if st.session_state.get("prompt_signature") != signature:
        st.session_state["prompt_signature"] = signature
        st.session_state["prompt_text"] = rendered
    elif not st.session_state.get("prompt_text"):
        st.session_state["prompt_text"] = rendered

    if st.button("Собрать промпт заново"):
        st.session_state["prompt_text"] = rendered

    prompt_text = st.text_area("Промпт для модели", key="prompt_text", height=360)
    st.download_button(
        "Скачать промпт",
        data=prompt_text,
        file_name=f"{doc.path.stem}.{artifact_type.value}.prompt.txt",
        mime="text/plain",
    )
    return artifact_type, int(count), extras, prompt_text


def _extras(
    presets: dict[str, Preset], selected: list[str], artifact_type: ArtifactType
) -> list[str]:
    try:
        return select_extras(presets, selected, artifact_type)
    except PresetError as exc:
        st.error(str(exc))
        return []


def _result_panel(
    doc: RequirementDoc,
    artifact_type: ArtifactType,
    count: int,
    prompt_text: str,
    settings: Config,
) -> None:
    if st.button("Сгенерировать", type="primary"):
        _generate(doc, artifact_type, count, prompt_text)

    generated = _current_artifact(artifact_type)
    if generated is None:
        st.info("Промпт готов. Нажмите «Сгенерировать».")
        return

    markdown = generated.render(doc.url, doc.path.name)
    st.success(
        f"Готово: {artifact_type.value}, попыток — {generated.attempts}"
    )
    tabs = st.tabs(["Документ", "Markdown", "Промпт"])
    with tabs[0]:
        st.markdown(markdown)
    with tabs[1]:
        st.code(markdown, language="markdown")
    with tabs[2]:
        st.code(generated.user_prompt, language="text")

    columns = st.columns(2)
    columns[0].download_button(
        "Скачать .md",
        data=markdown,
        file_name=f"{doc.path.stem}.{artifact_type.value}.md",
        mime="text/markdown",
    )
    if columns[1].button("Сохранить в output"):
        _save_artifact(doc, artifact_type, generated, settings)


def _chat_panel(
    doc: RequirementDoc,
    artifact_type: ArtifactType,
    count: int,
    prompt_text: str,
) -> None:
    generated = _current_artifact(artifact_type)
    if generated is None:
        st.info("Сначала сгенерируйте артефакт на шаге 3.")
        return

    st.caption(
        "Чат дорабатывает текущий артефакт. Изменения применяются только "
        "кнопкой «Применить как новую версию»."
    )
    history = st.session_state.setdefault("chat_history", [])
    for message in history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("Что доработать в артефакте?")
    if question:
        _ask_model(doc, artifact_type, prompt_text, generated, history, question)

    pending = st.session_state.get("pending_revision")
    if pending:
        st.warning("Есть ответ модели, ещё не применённый к артефакту.")
        if st.button("Применить как новую версию"):
            handler = _handler(artifact_type)
            if handler is not None:
                _apply_chat(
                    doc,
                    artifact_type,
                    count,
                    pending["raw"],
                    pending["prompt"],
                    handler,
                )
                st.session_state.pop("pending_revision", None)


def _ask_model(
    doc: RequirementDoc,
    artifact_type: ArtifactType,
    prompt_text: str,
    generated: GeneratedArtifact,
    history: list[dict[str, str]],
    question: str,
) -> None:
    history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    client = _client()
    if client is None:
        return
    handler = _handler(artifact_type)
    if handler is None:
        return

    messages = [
        {
            "role": "system",
            "content": handler.build_system()
            + "\n\nYou edit an existing QA document. Return the full corrected "
            "document only, keeping the required structure.",
        },
        {
            "role": "user",
            "content": (
                f"Requirements:\n{doc.content}\n\n"
                f"Current {artifact_type.value}:\n"
                f"{generated.render(doc.url, doc.path.name)}\n\n"
                f"Original prompt:\n{prompt_text}"
            ),
        },
        *history,
    ]
    try:
        answer = client.chat_messages(
            messages, float(st.session_state["temperature"]), json_mode=False
        )
    except (OllamaConnectionError, OllamaResponseError) as exc:
        st.error(str(exc))
        return

    history.append({"role": "assistant", "content": answer})
    st.session_state["pending_revision"] = {
        "raw": answer,
        "prompt": prompt_text,
    }
    with st.chat_message("assistant"):
        st.markdown(answer)


def _quality_panel(
    settings: Config,
    artifact_type: ArtifactType,
    prompt_text: str,
    doc: RequirementDoc,
) -> None:
    st.caption(
        "Рубрика ДЗ7: четыре критерия по шкале 1–5. Оценку ставит LLM по тексту "
        "промпта; ниже её можно скорректировать вручную."
    )
    presets = [str(item) for item in st.session_state.get("preset_ids", [])]
    count = int(st.session_state.get("count", 10))
    prompt_hash = (
        hashlib.sha256(prompt_text.encode("utf-8")).hexdigest() if prompt_text else ""
    )
    artifact_path = (
        settings.output_dir / f"{doc.path.stem}.{artifact_type.value}.md"
    )
    reference = artifact_reference(artifact_path, settings.output_dir)
    if reference is not None:
        st.caption(
            f"Сгенерированный артефакт: `{reference['path']}` "
            f"({reference['bytes']} байт, sha256 `{reference['sha256'][:12]}`)."
        )
        st.download_button(
            "Скачать артефакт",
            data=artifact_path.read_bytes(),
            file_name=artifact_path.name,
            mime="text/markdown",
        )
    else:
        st.caption(
            "Сгенерированного артефакта для этого файла и типа пока нет: "
            "оценка будет сохранена без ссылки."
        )

    if not prompt_text.strip():
        st.info("Промпт ещё не собран: загрузите требования и вернитесь на вкладку 2.")
    elif st.button("Оценить через LLM", type="primary"):
        _evaluate_with_llm(
            settings,
            artifact_type,
            prompt_text,
            presets,
            count,
            prompt_hash,
            reference,
        )

    stored = st.session_state.get("llm_evaluation")
    if (
        stored
        and stored["artifact"] == artifact_type
        and stored["prompt_sha256"] == prompt_hash
    ):
        _show_evaluation(stored["result"])

    st.divider()
    st.subheader("Ручная корректировка")
    st.caption(
        "Если не согласны с моделью: выставьте баллы сами и сохраните — "
        "файл оценки будет помечен source=human."
    )
    scores = {
        key: st.slider(CRITERIA_LABELS[key], 1, 5, 3, key=f"score_{key}")
        for key in CRITERIA
    }
    note = st.text_area("Наблюдения", key="quality_note", height=100)
    if st.button("Сохранить оценку"):
        if not prompt_text.strip():
            st.error("Сначала соберите промпт.")
            return
        result = EvaluationResult(
            scores=scores,
            comments={criterion: "" for criterion in CRITERIA},
            summary=note,
        )
        payload = quality_record(
            artifact_type=artifact_type,
            model=str(st.session_state.get("model", "")),
            prompt=prompt_text,
            result=result,
            presets=presets,
            count=count,
            source="human",
            note=note,
            generated_artifact=reference,
        )
        _write_quality(settings, artifact_type, payload)


def _evaluate_with_llm(
    settings: Config,
    artifact_type: ArtifactType,
    prompt_text: str,
    presets: list[str],
    count: int,
    prompt_hash: str,
    reference: dict[str, Any] | None,
) -> None:
    client = _client()
    if client is None:
        return
    try:
        with st.spinner("LLM оценивает промпт по четырём критериям..."):
            result = evaluate_prompt(
                client,
                prompt_text,
                artifact_type=artifact_type,
                presets=presets,
            )
    except (OllamaConnectionError, OllamaResponseError, EvaluationError) as exc:
        st.error(f"Не удалось получить оценку: {exc}")
        return

    payload = quality_record(
        artifact_type=artifact_type,
        model=str(st.session_state.get("model", "")),
        prompt=prompt_text,
        result=result,
        presets=presets,
        count=count,
        generated_artifact=reference,
    )
    path = _write_quality(settings, artifact_type, payload)
    st.session_state["llm_evaluation"] = {
        "artifact": artifact_type,
        "prompt_sha256": prompt_hash,
        "result": result,
    }
    st.success(f"Оценка LLM сохранена: {path}")


def _show_evaluation(result: EvaluationResult) -> None:
    columns = st.columns(len(CRITERIA))
    for column, criterion in zip(columns, CRITERIA, strict=True):
        column.metric(CRITERIA_LABELS[criterion], f"{result.scores[criterion]}/5")
    for criterion in CRITERIA:
        st.markdown(
            f"**{CRITERIA_LABELS[criterion]} — {result.scores[criterion]}/5.** "
            f"{result.comments[criterion]}"
        )
    weakest = min(CRITERIA, key=lambda item: result.scores[item])
    st.caption(
        f"Среднее по четырём критериям: {result.average:.1f} из 5; "
        f"слабее всего — {CRITERIA_LABELS[weakest].lower()} "
        f"({result.scores[weakest]}/5)."
    )


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------


def _generate(
    doc: RequirementDoc,
    artifact_type: ArtifactType,
    count: int,
    prompt_text: str,
) -> None:
    client = _client()
    handler = _handler(artifact_type)
    if client is None or handler is None:
        return

    notices: list[str] = []

    def remember(notice: RetryNotice) -> None:
        notices.append(
            f"попытка {notice.attempt}/{notice.total_attempts}: "
            + "; ".join(notice.issues)
        )

    options = GenerationOptions(
        count=count, temperature=float(st.session_state["temperature"])
    )
    try:
        with st.spinner("Ollama генерирует ответ..."):
            generated = generate_artifact(
                client,
                handler,
                doc,
                options,
                user_prompt=prompt_text or None,
                on_retry=remember,
            )
    except (OllamaConnectionError, OllamaResponseError, GenerationError) as exc:
        st.error(str(exc))
        for notice in notices:
            st.warning(notice)
        return

    _store_artifact(generated, doc)
    if notices:
        st.warning("Модель исправлялась после замечаний:\n\n" + "\n\n".join(notices))


def _apply_chat(
    doc: RequirementDoc,
    artifact_type: ArtifactType,
    count: int,
    raw: str,
    prompt_text: str,
    handler: ArtifactHandler,
) -> None:
    result = _validated(handler, raw, count)
    if result is None:
        return
    generated = GeneratedArtifact(
        artifact_type=artifact_type,
        result=result,
        user_prompt=prompt_text,
        attempts=1,
    )
    _store_artifact(generated, doc)
    st.success("Новая версия применена.")


def _validated(
    handler: ArtifactHandler, raw: str, count: int
) -> GenerationResult | None:
    cleaned = _FENCE_PATTERN.sub("", raw).strip()
    result = handler.validate_response(cleaned, count)
    if result.ok:
        return result
    st.error("Ответ не прошёл валидацию:\n\n- " + "\n- ".join(result.issues))
    return None


def _store_artifact(generated: GeneratedArtifact, doc: RequirementDoc) -> None:
    versions = st.session_state.setdefault("versions", [])
    versions.append(generated)
    st.session_state["artifact"] = generated
    if generated.artifact_type is ArtifactType.TESTCASES:
        # The coverage matrix may only reference ids that already exist.
        st.session_state["testcase_index"] = {
            "doc": str(doc.path),
            "text": testcase_index(generated.result),
        }


def _testcases_for_prompt(
    doc: RequirementDoc, artifact_type: ArtifactType
) -> str | None:
    if artifact_type is not ArtifactType.COVERAGEMATRIX:
        return None
    stored = st.session_state.get("testcase_index")
    if not stored or stored.get("doc") != str(doc.path):
        return None
    return str(stored["text"])


def _current_artifact(artifact_type: ArtifactType) -> GeneratedArtifact | None:
    generated = st.session_state.get("artifact")
    if generated is None or generated.artifact_type is not artifact_type:
        return None
    return generated


def _save_artifact(
    doc: RequirementDoc,
    artifact_type: ArtifactType,
    generated: GeneratedArtifact,
    settings: Config,
) -> None:
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    path = settings.output_dir / f"{doc.path.stem}.{artifact_type.value}.md"
    path.write_text(generated.render(doc.url, doc.path.name), encoding="utf-8")
    st.success(f"Сохранено: {path}")


def _write_quality(
    settings: Config,
    artifact_type: ArtifactType,
    payload: dict[str, Any],
) -> Path:
    directory = settings.output_dir / "quality"
    directory.mkdir(parents=True, exist_ok=True)
    presets = [str(item) for item in payload.get("presets", [])]
    run_id = payload.get("run_id")
    path = directory / quality_filename(
        artifact_type, presets, str(run_id) if run_id else None
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    st.caption(f"Файл: {path}")
    return path


def _client() -> OllamaClient | None:
    base_url = st.session_state.get("base_url", "")
    model = st.session_state.get("model", "")
    if not model:
        st.error("Модель не задана: укажите её в боковой панели.")
        return None
    timeout = float(st.session_state.get("timeout", 180.0))
    return OllamaClient(base_url=base_url, model=model, timeout=timeout)


def _handler(artifact_type: ArtifactType) -> ArtifactHandler | None:
    directory = st.session_state.get("prompt_dir")
    try:
        return get_handler(
            artifact_type, Path(directory) if directory else None
        )
    except PromptError as exc:
        st.error(str(exc))
        return None


if __name__ == "__main__":
    main()
