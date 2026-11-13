"""Workflow orchestration: generate -> assess -> visualize -> report.

The stages communicate through files inside the output directory:

* ``generation.json`` keeps the rendered text and the raw runs, so the
  assessment phase can recompute the metrics without calling Ollama again;
* ``quality-metrics.json`` is the source of truth for charts and the Markdown
  report;
* ``scenarios/*.md`` are the generated artifacts;
* ``charts/*.png`` are the visualizations.

Any subset of stages can be executed, but later stages require the files of the
earlier ones. Running the CLI without stage flags executes the four core stages.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from src.config import DEFAULT_MODEL, Settings
from src.generator.input_doc import InputError
from src.generator.models import ArtifactType
from src.generator.ollama_client import (
    OllamaClient,
    OllamaConnectionError,
    OllamaResponseError,
)
from src.generator.pipeline import GenerationRequest
from src.generator.presets import PresetError
from src.generator.prompt import PromptError
from src.generator.service import RetryNotice
from src.models import PipelineData
from src.reporting.report_generator import load_pipeline_data
from src.workflow import stages

PHASES: tuple[str, ...] = ("generate", "assess", "visualize", "report")
CORE_PHASES: tuple[str, ...] = ("generate", "assess", "visualize", "report")

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GENERATION = 3
EXIT_REPORT = 4
EXIT_QUALITY = 5


def _log(message: str) -> None:
    print(message, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="graduation-project",
        description=(
            "Генерация тестовой документации локальной LLM и оценка её "
            "качества: generate -> assess -> visualize -> report."
        ),
    )
    stages = parser.add_argument_group("фазы пайплайна")
    stages.add_argument(
        "--generate",
        action="store_true",
        help="сгенерировать артефакты и сохранить их в output/scenarios",
    )
    stages.add_argument(
        "--assess",
        action="store_true",
        help="пересчитать метрики по сохранённой генерации",
    )
    stages.add_argument(
        "--visualize",
        action="store_true",
        help="построить графики по quality-metrics.json",
    )
    stages.add_argument(
        "--report",
        action="store_true",
        help="записать report.md по quality-metrics.json",
    )

    generation = parser.add_argument_group("генерация")
    generation.add_argument(
        "--input-dir",
        default="input",
        help="папка с файлами требований .md/.txt (по умолчанию: input)",
    )
    generation.add_argument(
        "--type",
        dest="artifact_type",
        default=ArtifactType.TESTCASES.value,
        choices=[member.value for member in ArtifactType],
        help="тип артефакта (по умолчанию: testcases)",
    )
    generation.add_argument(
        "--count",
        type=int,
        default=10,
        help="кейсов на один файл требований (по умолчанию: 10)",
    )
    generation.add_argument(
        "--runs",
        type=int,
        default=1,
        help=(
            "сколько раз генерировать каждый сценарий; >1 даёт честную "
            "consistency (по умолчанию: 1)"
        ),
    )
    generation.add_argument(
        "--temperature",
        type=float,
        default=0.7,
        help="температура модели (по умолчанию: 0.7)",
    )
    generation.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="повторы при невалидном ответе модели (по умолчанию: 3)",
    )
    generation.add_argument(
        "--url",
        default=None,
        help="URL тестируемого сайта (переопределяет URL из файла)",
    )
    generation.add_argument(
        "--normalize",
        action="store_true",
        help="нормализовать требования перед сборкой промпта",
    )
    generation.add_argument(
        "--preset",
        action="append",
        default=[],
        help="id пресета; можно указывать несколько раз",
    )
    generation.add_argument(
        "--prompt-dir",
        default=None,
        help="своя папка шаблонов промптов",
    )
    generation.add_argument(
        "--continue-on-error",
        action="store_true",
        help=(
            "продолжать генерацию, если Ollama недоступна для отдельного файла"
        ),
    )

    runtime = parser.add_argument_group("подключение и вывод")
    runtime.add_argument(
        "--base-url",
        default=None,
        help="адрес Ollama (по умолчанию: OLLAMA_BASE_URL или localhost:11434)",
    )
    runtime.add_argument(
        "--model",
        default=None,
        help=f"имя модели (по умолчанию: OLLAMA_MODEL или {DEFAULT_MODEL})",
    )
    runtime.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="таймаут запроса к Ollama в секундах",
    )
    runtime.add_argument(
        "--output-dir",
        default="output",
        help="папка результатов (по умолчанию: output)",
    )
    runtime.add_argument(
        "--fail-under",
        type=float,
        default=None,
        metavar="SCORE",
        help="вернуть код 5, если средний overall ниже SCORE",
    )
    return parser


def selected_phases(args: argparse.Namespace) -> list[str]:
    """Return the requested stages in canonical order."""
    requested = [
        phase
        for phase in PHASES
        if getattr(args, phase.replace("-", "_"), False)
    ]
    return requested or list(CORE_PHASES)


def _check_model(client: OllamaClient, *, strict: bool = True) -> None:
    """Warn early when the configured model is not installed.

    With ``strict=False`` an unreachable Ollama is only a warning: the caller
    opted into per-file recovery, so connection errors are reported per file.
    """
    try:
        models = client.list_models()
    except (OllamaConnectionError, OllamaResponseError) as exc:
        if not strict:
            _log(f"предупреждение: {exc}; продолжаю по файлам")
            return
        if isinstance(exc, OllamaConnectionError):
            raise OllamaConnectionError(str(exc)) from exc
        raise
    if client.model not in models:
        _log(
            f"предупреждение: модель {client.model!r} не найдена в Ollama. "
            f"Доступные модели: {', '.join(models) or '—'}"
        )


def _run_generate(
    args: argparse.Namespace, settings: Settings, output_dir: Path
) -> PipelineData:
    request = GenerationRequest(
        input_dir=Path(args.input_dir),
        artifact_type=ArtifactType(args.artifact_type),
        count=args.count,
        runs=args.runs,
        temperature=args.temperature,
        max_retries=args.max_retries,
        url=args.url,
        normalize=args.normalize,
        presets=tuple(args.preset),
        prompt_dir=Path(args.prompt_dir) if args.prompt_dir else None,
        output_dir=output_dir,
        continue_on_error=args.continue_on_error,
    )
    client = OllamaClient(
        base_url=settings.base_url,
        model=settings.model,
        timeout=settings.timeout,
    )
    _check_model(client, strict=not args.continue_on_error)
    _log(
        f"генерация: {request.input_dir} -> {output_dir / 'scenarios'} "
        f"(тип={request.artifact_type.value}, кейсов={request.count}, "
        f"повторов={request.runs}, модель={client.model})"
    )

    def on_retry(notice: RetryNotice) -> None:
        _log(
            f"  повтор {notice.attempt}/{notice.total_attempts}: "
            f"{', '.join(notice.issues)}"
        )

    data = stages.generate(request, client, on_retry=on_retry)

    for scenario in data.scenarios:
        _log(
            f"  {scenario.filename}: overall={scenario.quality.overall} "
            f"(попыток={scenario.attempts})"
        )
    for error in data.errors:
        _log(f"  ошибка: {error}")
    if not data.scenarios:
        raise OllamaResponseError(
            "ни один сценарий не сгенерирован; смотрите ошибки выше"
        )
    return data


def _run_assess(data: PipelineData, output_dir: Path) -> PipelineData:
    """Recompute the metrics from the stored rendered text and runs."""
    stages.assess(data, output_dir)
    _log(
        "оценка: overall="
        f"{data.averages.get('overall')} "
        f"({data.successful}/{len(data.scenarios)} сценариев успешны)"
    )
    return data


def _run_visualize(data: PipelineData, output_dir: Path) -> PipelineData:
    stages.visualize(data, output_dir)
    if data.charts:
        _log(
            "визуализация: "
            + ", ".join(sorted(data.charts.values()))
        )
    else:
        _log("визуализация: нет метрик для построения графиков")
    return data


def _run_report(data: PipelineData, output_dir: Path) -> PipelineData:
    paths = stages.report(data, output_dir)
    _log(f"отчёт: {paths['markdown']}")
    _log(f"данные: {paths['json']}")
    return data


def main(argv: Sequence[str] | None = None) -> int:
    """Run the pipeline and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs должен быть >= 1")
    if args.count < 1:
        parser.error("--count должен быть >= 1")

    settings = Settings.from_env(
        base_url=args.base_url,
        model=args.model,
        timeout=args.timeout,
        output_dir=args.output_dir,
    )
    output_dir = Path(settings.output_dir)
    phases = selected_phases(args)
    _log(f"фазы: {' -> '.join(phases)}")

    data: PipelineData | None = None
    try:
        for phase in phases:
            if phase == "generate":
                data = _run_generate(args, settings, output_dir)
            elif phase == "assess":
                data = data or stages.load_snapshot(output_dir)
                data = _run_assess(data, output_dir)
            elif phase == "visualize":
                if data is None:
                    data = load_pipeline_data(output_dir)
                data = _run_visualize(data, output_dir)
            elif phase == "report":
                if data is None:
                    data = load_pipeline_data(output_dir)
                data = _run_report(data, output_dir)
    except (
        InputError,
        PromptError,
        PresetError,
        FileNotFoundError,
        ValueError,
    ) as exc:
        _log(f"ошибка входа: {exc}")
        return EXIT_USAGE
    except (OllamaConnectionError, OllamaResponseError) as exc:
        _log(f"ошибка генерации: {exc}")
        return EXIT_GENERATION
    except OSError as exc:
        _log(f"ошибка ввода-вывода: {exc}")
        return EXIT_REPORT
    except KeyboardInterrupt:
        _log("прервано пользователем")
        return 130

    if args.fail_under is not None and data is not None:
        overall = data.overall
        if overall is None:
            _log("порог качества: overall не измерим, проверка пропущена")
        elif overall < args.fail_under:
            _log(
                f"порог качества не достигнут: overall={overall} < "
                f"{args.fail_under}"
            )
            return EXIT_QUALITY
        else:
            _log(
                f"порог качества достигнут: overall={overall} >= "
                f"{args.fail_under}"
            )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
