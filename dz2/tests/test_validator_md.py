from ai_testgen.prompt import TESTPLAN_SECTIONS
from ai_testgen.validator import validate_markdown


def make_plan(sections: dict[str, str]) -> str:
    return "\n\n".join(f"## {title}\n\n{body.strip()}" for title, body in sections.items())


PLAN_OK = make_plan({name: f"Content of {name}." for name in TESTPLAN_SECTIONS})

CHECKLIST_OK = (
    "# Чек-лист\n\n"
    "## Заголовок и мета\n\n"
    "- title соответствует странице\n"
    "- meta description заполнен\n\n"
    "## Пагинация\n\n"
    "- переключение страниц меняет выдачу\n"
)


class TestCodeFences:
    def test_fences_stripped(self) -> None:
        wrapped = f"```markdown\n{PLAN_OK}\n```"

        result = validate_markdown(wrapped, required_sections=TESTPLAN_SECTIONS)

        assert result.ok
        assert result.document is not None
        assert result.document.startswith("## ")

    def test_bare_fences_stripped(self) -> None:
        wrapped = f"```\n{CHECKLIST_OK}\n```"

        result = validate_markdown(wrapped, items_under_every_section=True)

        assert result.ok

    def test_text_without_fences_untouched(self) -> None:
        result = validate_markdown(CHECKLIST_OK, items_under_every_section=True)

        assert result.document == CHECKLIST_OK.strip()


class TestJsonDetection:
    def test_json_object_rejected(self) -> None:
        result = validate_markdown('{"areas": [{"name": "x"}]}')

        assert not result.ok
        assert "JSON" in result.issues[0]

    def test_json_array_rejected(self) -> None:
        result = validate_markdown("[1, 2, 3]")

        assert not result.ok
        assert "JSON" in result.issues[0]

    def test_plain_number_is_not_json_document(self) -> None:
        result = validate_markdown("42")

        assert not result.ok
        assert "headings" in result.issues[0]


class TestRequiredSections:
    def test_all_sections_present_and_filled(self) -> None:
        result = validate_markdown(PLAN_OK, required_sections=TESTPLAN_SECTIONS)

        assert result.ok

    def test_missing_section_reported_by_name(self) -> None:
        broken = make_plan({name: "content" for name in TESTPLAN_SECTIONS[:-1]})

        result = validate_markdown(broken, required_sections=TESTPLAN_SECTIONS)

        assert not result.ok
        assert any("Риски" in issue and "missing" in issue for issue in result.issues)

    def test_empty_section_reported(self) -> None:
        plan = make_plan({name: "content" for name in TESTPLAN_SECTIONS})
        plan += "\n\n## Риски\n"

        result = validate_markdown(plan, required_sections=TESTPLAN_SECTIONS)

        issues = [issue for issue in result.issues if "Риски" in issue]
        assert issues == ["section has no content: Риски"]

    def test_section_match_case_insensitive(self) -> None:
        plan = PLAN_OK.replace("## Цели тестирования", "## цели тестирования")

        result = validate_markdown(plan, required_sections=TESTPLAN_SECTIONS)

        assert result.ok

    def test_numbered_heading_matches(self) -> None:
        plan = PLAN_OK.replace("## Риски", "## 5. Риски")

        result = validate_markdown(plan, required_sections=TESTPLAN_SECTIONS)

        assert result.ok

    def test_extended_heading_with_clarification_matches(self) -> None:
        plan = PLAN_OK.replace(
            "## Объём тестирования",
            "## Объём тестирования (что проверяем и что не проверяем)",
        )

        result = validate_markdown(plan, required_sections=TESTPLAN_SECTIONS)

        assert result.ok

    def test_no_h2_headings_at_all(self) -> None:
        result = validate_markdown("# Title\n\njust text, no sections")

        assert not result.ok
        assert "headings" in result.issues[0]


class TestChecklistItems:
    def test_every_zone_has_items(self) -> None:
        result = validate_markdown(CHECKLIST_OK, items_under_every_section=True)

        assert result.ok

    def test_zone_without_items_reported(self) -> None:
        checklist = CHECKLIST_OK.replace("- переключение страниц меняет выдачу\n", "")

        result = validate_markdown(checklist, items_under_every_section=True)

        assert not result.ok
        assert result.issues == ["zone has no checklist items: Пагинация"]

    def test_h1_title_not_treated_as_zone(self) -> None:
        result = validate_markdown(CHECKLIST_OK, items_under_every_section=True)

        assert all("Чек-лист" not in issue for issue in result.issues)

    def test_numbered_items_count_as_items(self) -> None:
        checklist = CHECKLIST_OK.replace(
            "- переключение страниц меняет выдачу",
            "1. переключение страниц меняет выдачу",
        )

        result = validate_markdown(checklist, items_under_every_section=True)

        assert result.ok
