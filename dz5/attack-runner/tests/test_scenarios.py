from pathlib import Path

from attack_runner.scenarios import load_scenarios, select_scenarios


SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"


def test_all_scenarios_load():
    scenarios = load_scenarios(SCENARIOS_DIR)
    assert len(scenarios) == 18
    categories = {scenario.owasp.split("/")[0] for scenario in scenarios}
    assert categories == {f"LLM{number:02d}" for number in range(1, 11)}


def test_poison_rag_scenarios_exist():
    scenarios = {scenario.id: scenario for scenario in load_scenarios(SCENARIOS_DIR)}
    assert scenarios["llm01-poison-rag-read"].setup
    assert scenarios["llm03-poison-rag-ddl"].steps
    assert scenarios["llm03-poison-rag-ddl"].restore_db


def test_selection_by_id():
    scenarios = load_scenarios(SCENARIOS_DIR)
    selected = select_scenarios(scenarios, ["llm01-ignore-previous"])
    assert [scenario.id for scenario in selected] == ["llm01-ignore-previous"]
