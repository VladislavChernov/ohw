"""Streamlit page rendered inside the Dz2 workbench.

The page never calls ``st.set_page_config`` and all widget keys start with
``lab_`` so they do not collide with the host page.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import streamlit as st

from llm_degradation.config import ConfigError, LabConfig, load_config
from llm_degradation.degradation import DegradationError, run_degradation
from llm_degradation.models import (
    EnvironmentInfo,
    ExperimentReport,
    InputInfo,
    PromptInfo,
    VerdictInfo,
)
from llm_degradation.normalizer_bridge import (
    BridgeError,
    build_prompt,
    load_normalized_from_config,
    normalizer_snapshot,
    prompt_sha256,
)
from llm_degradation.ollama import LabOllamaClient, ModelMissing, OllamaUnavailable
from llm_degradation.probes.api_contract import (
    TOTAL_API_CHECKS,
    api_verdict,
    run_api_checks,
)
from llm_degradation.probes.canary import canary_prompt, run_canary
from llm_degradation.probes.consistency import run_consistency
from llm_degradation.probes.hallucinations import (
    DEFAULT_PROBES_PATH,
    load_probes,
    run_hallucinations,
)
from llm_degradation.report import (
    KIND_TITLES,
    ReportError,
    create_run,
    list_reports,
    load_report,
    write_report,
)
from llm_degradation.runner import SeriesOptions
from llm_degradation.util import iso_utc

ARTIFACT_TYPES = ("testcases", "checklist", "testplan", "qareport", "coveragematrix")


def render_page() -> None:
    """Render the Dz8 page without touching the host page configuration."""
    st.header("Проверка модели")
    st.caption(
        "Галлюцинации, согласованность, API, канарейка и деградация. "
        "Все прогоны сохраняются в каталог отчётов."
    )

    try:
        config = load_config()
    except ConfigError as exc:
        st.error(f"Конфигурация не загружена: {exc}")
        return

    _sidebar(config)
    tabs = st.tabs(
        [
            "Стенд",
            "Галлюцинации",
            "Согласованность",
            "API",
            "Канарейка",
            "Деградация",
            "Последний прогон",
            "Отчёты",
        ]
    )
    with tabs[0]:
        _tab_health(config)
    with tabs[1]:
        _tab_hallucinations(config)
    with tabs[2]:
        _tab_consistency(config)
    with tabs[3]:
        _tab_api(config)
    with tabs[4]:
        _tab_canary(config)
    with tabs[5]:
        _tab_degradation(config)
    with tabs[6]:
        _tab_last_run(config)
    with tabs[7]:
        _tab_reports(config)


def _sidebar(config: LabConfig) -> None:
    with st.sidebar:
        st.subheader("Проверка модели")
        st.session_state.setdefault("lab_base_url", config.base_url)
        st.session_state.setdefault("lab_model", config.model)
        st.session_state.setdefault("lab_timeout", config.timeout)
        st.session_state.setdefault("lab_temperature", config.temperature)
        st.session_state.setdefault("lab_seed_fixed", config.seed is not None)
        st.session_state.setdefault("lab_seed", config.seed if config.seed is not None else 42)
        st.session_state.setdefault("lab_num_ctx", config.num_ctx)
        st.session_state.setdefault("lab_num_predict", config.num_predict)
        st.session_state.setdefault("lab_max_retries", config.max_retries)
        st.session_state.setdefault("lab_models", [])

        st.text_input("Base URL", key="lab_base_url")
        models = list(st.session_state.get("lab_models", []))
        choice = st.selectbox(
            "Модель из /api/tags",
            ["—", *models],
            key="lab_model_select",
        )
        if choice != "—":
            st.session_state["lab_model"] = choice
        st.text_input("Модель (ручной ввод)", key="lab_model")

        st.number_input("timeout, с", min_value=1.0, step=10.0, key="lab_timeout")
        st.slider("temperature", 0.0, 2.0, step=0.05, key="lab_temperature")
        seed_fixed = st.checkbox("Фиксировать seed", key="lab_seed_fixed")
        st.number_input("seed", min_value=0, step=1, key="lab_seed", disabled=not seed_fixed)
        st.number_input("num_ctx", min_value=512, step=512, key="lab_num_ctx")
        st.number_input("num_predict", min_value=16, step=16, key="lab_num_predict")
        st.number_input("max_retries", min_value=0, step=1, key="lab_max_retries")

        if st.button("Проверить связь", key="lab_health_button"):
            _check_health()
        if st.button("Обновить список моделей", key="lab_refresh_models"):
            client = _client()
            if client is not None:
                progress = _LabProgress(1, "Модели")
                try:
                    st.session_state["lab_models"] = client.list_models()
                except Exception as exc:  # noqa: BLE001 - surface any transport error
                    st.error(f"Не удалось получить модели: {exc}")
                else:
                    progress.finish()
                finally:
                    client.close()

        health = st.session_state.get("lab_health")
        if health and not health.get("ok"):
            st.error(health.get("error", "модель недоступна"))
            if health.get("command_hint"):
                st.code(str(health["command_hint"]))
        elif health:
            st.success(f"Ollama {health.get('ollama_version', '')}")

        st.caption(f"output_dir: {config.output_dir}")


def _client() -> LabOllamaClient | None:
    model = str(st.session_state.get("lab_model", ""))
    if not model:
        st.error("Укажите модель в боковой панели.")
        return None
    return LabOllamaClient(
        base_url=str(st.session_state.get("lab_base_url", "")),
        model=model,
        timeout=float(st.session_state.get("lab_timeout", 600.0)),
    )


def _check_health() -> dict[str, Any] | None:
    client = _client()
    if client is None:
        return None
    progress = _LabProgress(1, "Стенд")
    try:
        health = client.health()
    finally:
        client.close()
    st.session_state["lab_health"] = health
    st.session_state["lab_health_elapsed"] = progress.finish()
    return health


def _options(config: LabConfig, runs: int) -> SeriesOptions:
    seed_value = st.session_state.get("lab_seed", config.seed)
    seed = (
        int(seed_value)
        if bool(st.session_state.get("lab_seed_fixed", config.seed is not None))
        and seed_value is not None
        else None
    )
    return SeriesOptions(
        runs=runs,
        temperature=float(st.session_state.get("lab_temperature", config.temperature)),
        seed=seed,
        num_ctx=int(st.session_state.get("lab_num_ctx", config.num_ctx)),
        num_predict=int(st.session_state.get("lab_num_predict", config.num_predict)),
        max_retries=int(st.session_state.get("lab_max_retries", config.max_retries)),
    )


def _format_duration(seconds: float) -> str:
    """Format a duration in Russian, keeping it short enough for a caption."""
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, sec = divmod(remainder, 60)
    if hours:
        return f"{hours} ч {minutes:02d} мин"
    if minutes:
        return f"{minutes} мин {sec:02d} с"
    return f"{sec} с"


class _LabProgress:
    """Progress bar with percent, elapsed time and a running ETA.

    Streamlit renders each delta immediately, so a synchronous long run still
    updates the browser while the callback fires after every item.
    """

    def __init__(self, total: int, label: str) -> None:
        self.total = max(int(total), 1)
        self.label = label
        self.done = 0
        self.started = time.monotonic()
        self._bar = st.progress(0.0, text=self._bar_text(""))
        self._status = st.empty()
        self._status.caption(self._status_text(""))

    def update(self, done: int, detail: str = "") -> None:
        self.done = min(max(int(done), 0), self.total)
        self._bar.progress(self.done / self.total, text=self._bar_text(detail))
        self._status.caption(self._status_text(detail))

    def finish(self) -> float:
        elapsed = time.monotonic() - self.started
        self.done = self.total
        self._bar.progress(
            1.0, text=f"{self.label}: 100% ({self.total}/{self.total})"
        )
        self._status.caption(f"{self.label}: завершено за {_format_duration(elapsed)}")
        return elapsed

    def _bar_text(self, detail: str) -> str:
        percent = int(round(100 * self.done / self.total))
        text = f"{self.label}: {percent}% ({self.done}/{self.total})"
        return f"{text} — {detail}" if detail else text

    def _status_text(self, detail: str) -> str:
        elapsed = time.monotonic() - self.started
        parts = [f"Прошло: {_format_duration(elapsed)}"]
        eta = self._eta(elapsed)
        if eta is not None:
            parts.append(f"Осталось: ~{_format_duration(eta)}")
        if detail:
            parts.append(detail)
        return " | ".join(parts)

    def _eta(self, elapsed: float) -> float | None:
        if self.done <= 0 or self.done >= self.total:
            return None
        return elapsed / self.done * (self.total - self.done)


def _elapsed_caption(key: str) -> None:
    seconds = st.session_state.get(key)
    if seconds is not None:
        st.caption(f"Заняло: {_format_duration(float(seconds))}")


def _load_input(
    config: LabConfig, path: str, artifact_type: str, count: int
) -> tuple[Any, Any] | None:
    try:
        normalized = load_normalized_from_config(path, normalizer_config=config.normalizer_config)
        prompt = build_prompt(
            normalized,
            _artifact_type(artifact_type),
            count,
            prompt_dir=config.prompt_dir,
            presets_dir=config.presets_dir,
        )
    except (BridgeError, ConfigError, ValueError) as exc:
        st.error(str(exc))
        return None
    return normalized, prompt


def _artifact_type(raw: str) -> Any:
    from ai_testgen.models import ArtifactType

    return ArtifactType(raw)


def _tab_health(config: LabConfig) -> None:
    if st.button("Проверить стенд", key="lab_health_tab_button"):
        _check_health()
    health = st.session_state.get("lab_health")
    if not health:
        st.info("Нажмите «Проверить стенд» или «Проверить связь» в боковой панели.")
        return
    _elapsed_caption("lab_health_elapsed")
    rows = [
        {"Параметр": "base_url", "Значение": health.get("base_url", "")},
        {"Параметр": "Ollama", "Значение": health.get("ollama_version", "")},
        {"Параметр": "model", "Значение": health.get("model", "")},
        {"Параметр": "digest", "Значение": health.get("digest") or "-"},
        {"Параметр": "models", "Значение": ", ".join(health.get("models", []))},
    ]
    st.dataframe(rows, use_container_width=True, key="lab_health_table")
    if not health.get("ok"):
        st.error(health.get("error", "стенд недоступен"))
        if health.get("command_hint"):
            st.code(str(health["command_hint"]))
    with st.expander("Сырой ответ /api/tags"):
        st.json(health.get("models", []))


def _tab_hallucinations(config: LabConfig) -> None:
    default_input = str(config.golden_dir / "inputs" / "catalog.md")
    path = st.text_input("Вход", value=default_input, key="lab_hall_input")
    probes_path = st.text_input(
        "Файл probe-ов",
        value=str(config.golden_dir / DEFAULT_PROBES_PATH),
        key="lab_hall_probes",
    )
    runs = st.number_input("runs", min_value=1, max_value=20, value=3, key="lab_hall_runs")
    strict = st.checkbox("strict", key="lab_hall_strict")
    if st.button("Запустить", key="lab_hall_run"):
        client = _client()
        if client is None:
            return
        try:
            probes = load_probes(Path(probes_path))
            options = _options(config, int(runs))
            context = create_run(config.output_dir, "hallucinations")
            progress = _LabProgress(len(probes), "Галлюцинации")
            result = run_hallucinations(
                client,
                probes,
                model=client.model,
                root=config.root,
                golden_dir=config.golden_dir,
                options=options,
                runs=int(runs),
                strict=strict,
                thresholds=config.thresholds,
                normalizer_config=config.normalizer_config,
                prompt_dir=config.prompt_dir,
                probe_prompt_dir=config.probe_prompt_dir,
                runs_dir=context.directory,
                progress=lambda position, _total, probe_id: progress.update(
                    position, f"probe {probe_id}"
                ),
            )
            st.session_state["lab_hall_elapsed"] = progress.finish()
            normalized = load_normalized_from_config(
                path, normalizer_config=config.normalizer_config
            )
            prompt = build_prompt(
                normalized,
                _artifact_type("testcases"),
                5,
                prompt_dir=config.prompt_dir,
                presets_dir=config.presets_dir,
            )
            summary = dict(result.summary)
            summary["probes"] = [item.model_dump(mode="json") for item in result.probes]
            report = _build_report(
                config, "hallucinations", normalized, prompt, options, summary, result.verdict
            )
            _store_report(context, report, config)
            st.session_state["lab_hall_result"] = summary
        except (OllamaUnavailable, ModelMissing, BridgeError, ReportError, ValueError) as exc:
            st.error(str(exc))
            st.code(_stand_command(), language="powershell")
        finally:
            client.close()

    stored_summary = st.session_state.get("lab_hall_result")
    if stored_summary:
        _elapsed_caption("lab_hall_elapsed")
        st.metric("hallucination_rate", stored_summary.get("hallucination_rate", 0.0))
        st.write(
            {
                "failed_probes": stored_summary.get("failed_probes"),
                "runs_total": stored_summary.get("runs_total"),
                "critical_failures": stored_summary.get("critical_failures"),
            }
        )
        rows = stored_summary.get("probes", [])
        if rows:
            st.dataframe(
                [
                    {
                        "id": item["id"],
                        "kind": item["kind"],
                        "status": item["status"],
                        "critical": item["critical"],
                        "failed_runs": item["failed_runs"],
                        "evidence": item["evidence"],
                    }
                    for item in rows
                ],
                use_container_width=True,
                key="lab_hall_table",
            )


def _tab_consistency(config: LabConfig) -> None:
    default_input = str(config.golden_dir / "inputs" / "catalog.md")
    path = st.text_input("Вход", value=default_input, key="lab_cons_input")
    artifact_type = st.selectbox("Тип артефакта", ARTIFACT_TYPES, key="lab_cons_type")
    count = st.number_input("count", min_value=1, max_value=50, value=5, key="lab_cons_count")
    runs = st.number_input("runs", min_value=1, max_value=50, value=10, key="lab_cons_runs")
    if st.button("Запустить", key="lab_cons_run"):
        loaded = _load_input(config, path, artifact_type, int(count))
        client = _client()
        if loaded is None or client is None:
            return
        normalized, prompt = loaded
        try:
            context = create_run(config.output_dir, "consistency")
            options = _options(config, int(runs))
            progress = _LabProgress(int(runs), "Согласованность")
            result = run_consistency(
                client,
                normalized,
                prompt,
                options=options,
                thresholds=config.thresholds,
                prompt_dir=config.prompt_dir,
                runs_dir=context.directory / "runs",
                progress=lambda index, total, _record: progress.update(
                    index, f"run {index}/{total}"
                ),
            )
            st.session_state["lab_cons_elapsed"] = progress.finish()
            report = _build_report(
                config,
                "consistency",
                normalized,
                prompt,
                options,
                result.summary,
                result.verdict,
                runs=result.runs,
            )
            _store_report(context, report, config)
            st.session_state["lab_cons_result"] = {
                "summary": result.summary,
                "runs": [item.model_dump(mode="json") for item in result.runs],
            }
        except (OllamaUnavailable, ModelMissing, BridgeError, ReportError) as exc:
            st.error(str(exc))
        finally:
            client.close()

    stored = st.session_state.get("lab_cons_result")
    if stored:
        _elapsed_caption("lab_cons_elapsed")
        st.write(stored["summary"])
        st.dataframe(
            [
                {
                    "run": item["index"],
                    "validator": item["validator_pass"],
                    "attempts": item["attempts"],
                    "cases": item["metrics"]["cases_total"],
                    "pos": item["metrics"]["positive"],
                    "neg": item["metrics"]["negative"],
                    "br_coverage": item["metrics"]["br_coverage"],
                    "latency_ms": item["latency_ms"],
                }
                for item in stored["runs"]
            ],
            use_container_width=True,
            key="lab_cons_table",
        )


def _tab_api(config: LabConfig) -> None:
    if st.button("Проверить API", key="lab_api_run"):
        client = _client()
        if client is None:
            return
        try:
            progress = _LabProgress(TOTAL_API_CHECKS, "API")
            checks = run_api_checks(
                client,
                client.model,
                progress=lambda index, _total, check: progress.update(index, check.id),
                prompt_dir=config.probe_prompt_dir,
            )
            st.session_state["lab_api_elapsed"] = progress.finish()
            verdict = api_verdict(checks)
            context = create_run(config.output_dir, "api")
            report = _build_report(
                config,
                "api",
                None,
                None,
                _options(config, 0),
                {"checks": [item.model_dump(mode="json") for item in checks]},
                verdict,
            )
            _store_report(context, report, config)
            st.session_state["lab_api_checks"] = [item.model_dump(mode="json") for item in checks]
            st.session_state["lab_api_verdict"] = verdict.model_dump(mode="json")
        except OllamaUnavailable as exc:
            st.error(str(exc))
            st.code(_stand_command(), language="powershell")
        finally:
            client.close()

    stored_checks = st.session_state.get("lab_api_checks")
    if stored_checks:
        _elapsed_caption("lab_api_elapsed")
        st.dataframe(
            [
                {
                    "id": item["id"],
                    "check": item["name"],
                    "status": item["status"],
                    "http": item["http_status"],
                    "latency_ms": item["latency_ms"],
                    "detail": item["detail"],
                }
                for item in stored_checks
            ],
            use_container_width=True,
            key="lab_api_table",
        )
        st.json(st.session_state.get("lab_api_verdict", {}), expanded=False)


def _tab_canary(config: LabConfig) -> None:
    default_input = str(config.golden_dir / "inputs" / "no-cart.md")
    path = st.text_input("Вход", value=default_input, key="lab_canary_input")
    runs = st.number_input("runs", min_value=1, max_value=5, value=1, key="lab_canary_runs")
    if st.button("Запустить", key="lab_canary_run"):
        client = _client()
        if client is None:
            return
        try:
            normalized = load_normalized_from_config(
                path, normalizer_config=config.normalizer_config
            )
            options = _options(config, int(runs))
            context = create_run(config.output_dir, "canary")
            progress = _LabProgress(int(runs), "Канарейка")
            result = run_canary(
                client,
                normalized,
                model=client.model,
                runs=int(runs),
                options=options,
                thresholds=config.thresholds,
                on_run=lambda index, total, _result: progress.update(
                    index, f"run {index}/{total}"
                ),
                probe_prompt_dir=config.probe_prompt_dir,
            )
            st.session_state["lab_canary_elapsed"] = progress.finish()
            payload = [item.model_dump(mode="json") for item in result.results]
            summary = {"runs": int(runs), "results": payload}
            prompt_info = PromptInfo(
                artifact_type="canary",
                count=0,
                presets=[],
                prompt_sha256=prompt_sha256(canary_prompt(normalized, config.probe_prompt_dir)),
            )
            report = _build_report(
                config,
                "canary",
                normalized,
                None,
                options,
                summary,
                VerdictInfo(
                    status=result.verdict,
                    reasons=result.reasons,
                    thresholds={
                        "canary_title_overlap_min": config.thresholds.canary_title_overlap_min,
                        "canary_body_overlap_mean_min": (
                            config.thresholds.canary_body_overlap_mean_min
                        ),
                        "canary_body_overlap_min": config.thresholds.canary_body_overlap_min,
                    },
                ),
                prompt_info=prompt_info,
            )
            _store_report(context, report, config)
            st.session_state["lab_canary_result"] = payload
        except (OllamaUnavailable, ModelMissing, BridgeError, ReportError) as exc:
            st.error(str(exc))
        finally:
            client.close()

    stored_payload = st.session_state.get("lab_canary_result")
    if stored_payload:
        _elapsed_caption("lab_canary_elapsed")
        item = stored_payload[0]
        col_left, col_right = st.columns(2)
        with col_left:
            st.markdown("**Детерминированный нормализатор**")
            st.code(item.get("det_canonical", ""))
        with col_right:
            st.markdown("**LLM**")
            st.code(item.get("llm_canonical", ""))
        st.write(
            {
                "ids_exact": item.get("ids_exact"),
                "url_exact": item.get("url_exact"),
                "title_overlap": item.get("title_overlap"),
                "body_overlap_mean": item.get("body_overlap_mean"),
                "body_overlap_min": item.get("body_overlap_min"),
                "verdict": item.get("verdict"),
            }
        )


def _tab_degradation(config: LabConfig) -> None:
    default_input = str(config.golden_dir / "inputs" / "catalog.md")
    path = st.text_input("Вход", value=default_input, key="lab_deg_input")
    artifact_type = st.selectbox("Тип артефакта", ARTIFACT_TYPES, key="lab_deg_type")
    count = st.number_input("count", min_value=1, max_value=50, value=5, key="lab_deg_count")
    runs = st.number_input("runs", min_value=1, max_value=50, value=5, key="lab_deg_runs")
    baseline_mode = st.radio(
        "Baseline",
        ("Сохранённый отчёт", "Живая модель"),
        key="lab_deg_baseline_mode",
    )
    baseline_report_path = None
    baseline_model = None
    if baseline_mode == "Сохранённый отчёт":
        candidates = _baseline_candidates(config)
        if candidates:
            baseline_report_path = st.selectbox(
                "Отчёт baseline", candidates, key="lab_deg_baseline_report"
            )
        else:
            st.warning("Нет сохранённых отчётов baseline.")
    else:
        baseline_model = st.text_input(
            "Baseline модель",
            value=str(st.session_state.get("lab_model", config.model)),
            key="lab_deg_baseline_model",
        )
    candidate_model = st.text_input(
        "Candidate модель",
        value=str(st.session_state.get("lab_model", config.model)),
        key="lab_deg_candidate_model",
    )
    fail_on_regression = st.checkbox("fail-on-regression", key="lab_deg_fail_on_regression")

    if st.button("Сравнить", key="lab_deg_run"):
        loaded = _load_input(config, path, artifact_type, int(count))
        client = _client()
        if loaded is None or client is None:
            return
        normalized, prompt = loaded
        try:
            baseline_report = (
                load_report(Path(baseline_report_path)) if baseline_report_path else None
            )
            context = create_run(config.output_dir, "degradation")
            options = _options(config, int(runs))
            live_baseline = baseline_report is None
            candidate_offset = int(runs) if live_baseline else 0
            total_work = int(runs) * (2 if live_baseline else 1)
            progress = _LabProgress(total_work, "Деградация")

            def _on_cell(cell: str, index: int, total: int, _record: Any) -> None:
                offset = 0 if cell == "baseline" else candidate_offset
                progress.update(offset + index, f"{cell}: {index}/{total}")

            result = run_degradation(
                client,
                normalized,
                prompt,
                baseline_model=baseline_model,
                candidate_model=candidate_model or client.model,
                options=options,
                thresholds=config.thresholds,
                prompt_dir=config.prompt_dir,
                runs_dir=context.directory / "runs",
                baseline_report=baseline_report,
                progress=_on_cell,
            )
            st.session_state["lab_deg_elapsed"] = progress.finish()
            report = _build_report(
                config,
                "degradation",
                normalized,
                prompt,
                options,
                result.candidate_summary,
                result.verdict,
                runs=result.runs,
                baseline=result.baseline,
                candidate=result.candidate,
                comparison=result.comparison,
            )
            _store_report(context, report, config)
            st.session_state["lab_deg_result"] = {
                "comparison": result.comparison,
                "verdict": result.verdict.model_dump(mode="json"),
            }
        except (
            OllamaUnavailable,
            ModelMissing,
            DegradationError,
            BridgeError,
            ReportError,
        ) as exc:
            st.error(str(exc))
        finally:
            client.close()

    stored = st.session_state.get("lab_deg_result")
    if stored:
        _elapsed_caption("lab_deg_elapsed")
        st.write(stored["comparison"])
        st.write(stored["verdict"])
        if stored["verdict"]["status"] == "regressed" and fail_on_regression:
            st.error("Обнаружена регрессия.")


def _tab_last_run(config: LabConfig) -> None:
    items = list_reports(config.output_dir, limit=1)
    if not items:
        st.info("Прогонов пока нет. Запустите любую проверку.")
        return
    item = items[0]
    st.subheader(f"{KIND_TITLES.get(item['kind'], item['kind'])} — {item['run_id']}")
    columns = st.columns(4)
    columns[0].metric("Вердикт", item["verdict"])
    columns[1].metric("Модель", item["model"] or "-")
    columns[2].metric("Прогонов", item["runs"])
    columns[3].metric("Тип", item["kind"])
    st.caption(f"Начат: {item['started_at']} | Завершён: {item['finished_at']}")
    try:
        report = load_report(Path(item["path"]))
    except ReportError as exc:
        st.error(str(exc))
        return
    if report.verdict.reasons:
        st.write({"reasons": report.verdict.reasons})
    if report.summary:
        st.json(report.summary, expanded=False)
    if report.runs:
        st.dataframe(
            [
                {
                    "run": run.index,
                    "cell": run.cell,
                    "validator": run.validator_pass,
                    "attempts": run.attempts,
                    "cases": run.metrics.cases_total,
                    "br_coverage": run.metrics.br_coverage,
                    "latency_ms": run.latency_ms,
                }
                for run in report.runs
            ],
            use_container_width=True,
            key="lab_last_table",
        )
    if report.comparison:
        st.write(report.comparison)
    report_path = Path(item["path"])
    markdown_path = report_path.with_name("report.md")
    st.download_button(
        "Скачать report.json",
        data=report_path.read_text(encoding="utf-8"),
        file_name=f"{item['run_id']}.json",
        mime="application/json",
        key="lab_last_download_json",
    )
    if markdown_path.is_file():
        st.download_button(
            "Скачать report.md",
            data=markdown_path.read_text(encoding="utf-8"),
            file_name=f"{item['run_id']}.md",
            mime="text/markdown",
            key="lab_last_download_md",
        )


def _tab_reports(config: LabConfig) -> None:
    items = list_reports(config.output_dir)
    if not items:
        st.info("Отчётов пока нет.")
        return
    labels = [f"{item['run_id']} — {item['kind']} — {item['verdict']}" for item in items]
    selected = st.selectbox("Прогон", labels, key="lab_reports_select")
    index = labels.index(selected)
    item = items[index]
    report_path = Path(item["path"])
    markdown_path = report_path.with_name("report.md")
    if markdown_path.is_file():
        st.markdown(markdown_path.read_text(encoding="utf-8"))
    st.download_button(
        "Скачать report.json",
        data=report_path.read_text(encoding="utf-8"),
        file_name=f"{item['run_id']}.json",
        mime="application/json",
        key="lab_reports_download_json",
    )
    if markdown_path.is_file():
        st.download_button(
            "Скачать report.md",
            data=markdown_path.read_text(encoding="utf-8"),
            file_name=f"{item['run_id']}.md",
            mime="text/markdown",
            key="lab_reports_download_md",
        )


def _baseline_candidates(config: LabConfig) -> list[str]:
    paths: list[str] = []
    baseline_dir = config.golden_dir / "baseline"
    if baseline_dir.is_dir():
        paths.extend(str(item) for item in sorted(baseline_dir.glob("*.json")))
    paths.extend(
        item["path"] for item in list_reports(config.output_dir) if item["kind"] == "consistency"
    )
    return paths


def _build_report(
    config: LabConfig,
    kind: str,
    normalized: Any,
    prompt: Any,
    options: SeriesOptions,
    summary: dict[str, Any],
    verdict: VerdictInfo,
    *,
    runs: list[Any] | None = None,
    baseline: Any = None,
    candidate: Any = None,
    comparison: dict[str, Any] | None = None,
    prompt_info: PromptInfo | None = None,
) -> ExperimentReport:
    input_info = InputInfo(
        path=str(normalized.source) if normalized is not None else "",
        source_sha256=normalized.source_sha256 if normalized is not None else "",
        text_sha256=normalized.text_sha256 if normalized is not None else "",
        encoding=normalized.encoding if normalized is not None else "utf-8",
        title=normalized.title if normalized is not None else "",
        url=normalized.url if normalized is not None else None,
        br_ids=list(normalized.br_ids) if normalized is not None else [],
        normalizer=normalizer_snapshot(normalized.settings) if normalized is not None else {},
    )
    if prompt_info is None and prompt is not None:
        prompt_info = PromptInfo(
            artifact_type=prompt.artifact_type.value,
            count=prompt.count,
            presets=list(prompt.presets),
            prompt_sha256=prompt.prompt_sha256,
        )
    if prompt_info is None:
        prompt_info = PromptInfo()
    return ExperimentReport(
        run_id="",
        kind=kind,
        started_at=iso_utc(),
        finished_at=iso_utc(),
        environment=EnvironmentInfo(
            base_url=str(st.session_state.get("lab_base_url", config.base_url)),
            model=str(st.session_state.get("lab_model", config.model)),
            temperature=options.temperature,
            seed=options.seed,
            num_ctx=options.num_ctx,
            num_predict=options.num_predict,
            max_retries=options.max_retries,
        ),
        input=input_info,
        prompt=prompt_info,
        params={"runs": options.runs},
        runs=runs or [],
        summary=summary,
        verdict=verdict,
        baseline=baseline,
        candidate=candidate,
        comparison=comparison,
    )


def _store_report(context: Any, report: ExperimentReport, config: LabConfig) -> None:
    final = report.model_copy(update={"run_id": context.run_id, "started_at": context.started_at})
    report_path, _markdown = write_report(
        context, final, root=Path.cwd(), keep_runs=config.keep_runs
    )
    st.session_state["lab_last_report"] = str(report_path)
    st.success(f"Отчёт: {report_path}")


def _stand_command() -> str:
    return "cd dz2\n.\\scripts\\up-cpu.ps1   # или up-gpu.ps1\ndocker ps"
