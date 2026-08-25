from ai_testgen.models import ArtifactType, CaseType, TestCase

_DOCUMENT_TITLES: dict[ArtifactType, str] = {
    ArtifactType.CHECKLIST: "Чек-лист",
    ArtifactType.TESTPLAN: "Тест-план",
}


def render_document(
    artifact_type: ArtifactType,
    url: str | None,
    source_name: str,
    body: str,
) -> str:
    title = _DOCUMENT_TITLES[artifact_type]
    lines: list[str] = [f"# {title}: {source_name}", ""]
    if url:
        lines += [f"**Тестируемый сайт:** {url}", ""]
    lines += [body.rstrip(), ""]
    return "\n".join(lines)


def render(cases: list[TestCase], url: str | None, source_name: str) -> str:
    positives = [case for case in cases if case.type == CaseType.POSITIVE]
    negatives = [case for case in cases if case.type == CaseType.NEGATIVE]

    lines: list[str] = [f"# Тест-кейсы: {source_name}", ""]
    if url:
        lines += [f"**Тестируемый сайт:** {url}", ""]
    lines += [
        (
            f"**Всего кейсов:** {len(cases)} "
            f"(позитивных: {len(positives)}, негативных: {len(negatives)})"
        ),
        "",
    ]

    if positives:
        lines.append("## Позитивные сценарии")
        lines.append("")
        for case in positives:
            _render_case(lines, case)
    if negatives:
        lines.append("## Негативные сценарии")
        lines.append("")
        for case in negatives:
            _render_case(lines, case)

    return "\n".join(lines).rstrip() + "\n"


def _render_case(lines: list[str], case: TestCase) -> None:
    lines += [
        f"### {case.id}. {case.title}",
        "",
        f"**Тип:** {'позитивный' if case.type == CaseType.POSITIVE else 'негативный'}",
        "",
    ]
    if case.requirement:
        lines += [f"**Проверяет требование:** {case.requirement}", ""]
    if case.preconditions:
        lines.append("**Предусловия:**")
        lines.append("")
        lines += [f"- {item}" for item in case.preconditions]
        lines.append("")
    lines += ["**Шаги:**", "", "| № | Действие |", "|---|----------|"]
    for number, step in enumerate(case.steps, start=1):
        lines.append(f"| {number} | {step} |")
    lines += ["", f"**Ожидаемый результат:** {case.expected}", ""]
