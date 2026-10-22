from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    return str(value).replace("|", "\\|")


def _ms(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):.0f}"


def _latency(summary: dict[str, Any]) -> str:
    latency = summary.get("latency_ms") or {}
    return f"{_ms(latency.get('p50'))} / {_ms(latency.get('p95'))}"


def _verdict_cell(summary: dict[str, Any] | None) -> str:
    if summary is None:
        return "unknown"
    return f"{summary.get('verdict', 'unknown')} (n={summary.get('n', 0)})"


def _table(summaries: Iterable[dict[str, Any]]) -> list[str]:
    rows = [
        "| Scenario | OWASP | n | blocked | bypass | mixed | unknown | error | p50/p95 ms | verdict |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for summary in summaries:
        counts = summary.get("counts") or {}
        rows.append(
            "| {scenario} | {owasp} | {n} | {blocked} | {bypass} | {mixed} | "
            "{unknown} | {error} | {latency} | {verdict} |".format(
                scenario=_cell(summary.get("scenario")),
                owasp=_cell(summary.get("owasp")),
                n=_cell(summary.get("n")),
                blocked=_cell(counts.get("blocked", 0)),
                bypass=_cell(counts.get("bypass", 0)),
                mixed=_cell(counts.get("mixed", 0)),
                unknown=_cell(counts.get("unknown", 0)),
                error=_cell(counts.get("error", 0)),
                latency=_latency(summary),
                verdict=_cell(summary.get("verdict")),
            )
        )
    return rows


def render_run_md(
    manifest: dict[str, Any],
    summaries: list[dict[str, Any]],
    *,
    title: str,
    complete: bool,
) -> str:
    lines = [
        f"# {title}",
        "",
        f"- Run: `{manifest.get('run_id', 'unknown')}`",
        f"- Preset: `P{manifest.get('preset', '?')}`",
        f"- Target: `{manifest.get('target', 'unknown')}`",
        f"- Model: `{manifest.get('model', 'unknown')}`",
        f"- Commit: `{manifest.get('git_commit', 'unknown')}`",
        f"- Runs per scenario: `{manifest.get('runs', '?')}`",
        f"- Started: `{manifest.get('started_at', 'unknown')}`",
        f"- Finished: `{manifest.get('finished_at', 'unknown')}`",
        f"- Complete: `{str(complete).lower()}`",
        "",
    ]
    if not complete:
        lines.extend(
            [
                "> Внимание: прогон неполный. Его нельзя публиковать как latest.md.",
                "",
            ]
        )
    lines.extend(_table(summaries))
    lines.append("")
    attention = [
        summary
        for summary in summaries
        if summary.get("verdict") in {"mixed", "unknown", "error"}
    ]
    lines.append("## Требуют внимания")
    lines.append("")
    if attention:
        for summary in attention:
            lines.append(
                f"- `{summary.get('scenario')}`: `{summary.get('verdict')}` "
                f"(n={summary.get('n')}, counts={summary.get('counts')})"
            )
    else:
        lines.append("Нет сценариев с verdict `mixed`, `unknown` или `error`.")
    lines.extend(
        [
            "",
            "## Details",
            "",
            f"Подробные запросы и ответы: `details/`.",
            "",
        ]
    )
    return "\n".join(lines)


def _latest_runs(runs: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    latest: dict[int, dict[str, Any]] = {}
    for run in runs:
        preset = run["manifest"].get("preset")
        if not isinstance(preset, int):
            continue
        key = (str(run["manifest"].get("started_at", "")), bool(run["manifest"].get("complete")))
        current = latest.get(preset)
        if current is None or key > (
            str(current["manifest"].get("started_at", "")),
            bool(current["manifest"].get("complete")),
        ):
            latest[preset] = run
    return latest


def render_red_team_md(runs: Iterable[dict[str, Any]]) -> str:
    latest = _latest_runs(runs)
    lines = [
        "# Red-Team Report — dz5 Security Stand",
        "",
        "Сгенерировано из `reports/<run-id>/summary.json`. Пустая ячейка не означает",
        "защиту: отсутствующие данные отображаются как `unknown`, а не `blocked`.",
        "",
    ]
    if not latest:
        lines.extend(["Нет завершённых прогонов.", ""])
        return "\n".join(lines)

    lines.extend(
        [
            "## Latest run per preset",
            "",
            "| Preset | Run | Complete | Started | Target |",
            "|---|---|---|---|---|",
        ]
    )
    for preset in sorted(latest):
        run = latest[preset]
        manifest = run["manifest"]
        lines.append(
            "| P{preset} | {run_id} | {complete} | {started} | {target} |".format(
                preset=preset,
                run_id=_cell(manifest.get("run_id")),
                complete="yes" if manifest.get("complete") else "no",
                started=_cell(manifest.get("started_at")),
                target=_cell(manifest.get("target")),
            )
        )

    scenario_ids: set[str] = set()
    by_preset: dict[int, dict[str, dict[str, Any]]] = {}
    for preset, run in latest.items():
        entries = {
            str(summary.get("scenario")): summary
            for summary in run.get("summaries", [])
            if summary.get("scenario")
        }
        by_preset[preset] = entries
        scenario_ids.update(entries)

    lines.extend(
        [
            "",
            "## Attack matrix",
            "",
            "| Scenario | OWASP | P1 | P2 | P3 | P4 | P5 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for scenario_id in sorted(
        scenario_ids,
        key=lambda item: (
            str(by_preset.get(1, {}).get(item, {}).get("owasp", "")),
            item,
        ),
    ):
        owasp = next(
            (
                by_preset[preset][scenario_id].get("owasp", "unknown")
                for preset in sorted(by_preset)
                if scenario_id in by_preset[preset]
            ),
            "unknown",
        )
        cells = [
            _verdict_cell(by_preset.get(preset, {}).get(scenario_id))
            for preset in range(1, 6)
        ]
        lines.append(
            f"| {_cell(scenario_id)} | {_cell(owasp)} | " + " | ".join(cells) + " |"
        )

    incomplete = [preset for preset, run in latest.items() if not run["manifest"].get("complete")]
    lines.extend(["", "## Notes", ""])
    if incomplete:
        lines.append(
            "Неполные последние прогоны: "
            + ", ".join(f"P{preset}" for preset in sorted(incomplete))
            + ". Их verdicts не являются red-team-вердиктом."
        )
    else:
        lines.append("Все последние прогоны по пресетам отмечены `complete=true`.")
    lines.extend(
        [
            "",
            "`n<3` даёт `unknown`; `error` не считается защитой; `mixed` означает",
            "наличие и `blocked`, и `bypass` в разных повторах.",
            "",
        ]
    )
    return "\n".join(lines)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
