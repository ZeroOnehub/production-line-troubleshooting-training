"""M1/M2 验收：本体完整、14 个关卡格式与引用正确、每条正确路径自动通关、错误路径触发预期判定。"""
import pytest

from app.engine.ontology import Scenario
from app.engine.validator import replay, run_negative, validate_all, validate_content, validate_scenario, validate_yaml_text


def test_content_consistency(content):
    rep = validate_content(content)
    assert rep["ok"], rep["errors"]
    assert rep["rules"] >= 30


def test_fourteen_scenarios(content):
    types = [s.type for s in content.scenarios.values()]
    assert len(content.scenarios) == 14
    assert types.count("alarm") == 10
    assert types.count("tour") == 1 and types.count("checklist") == 1
    assert types.count("changeover") == 1 and types.count("shift") == 1


def test_alarm_scenarios_have_two_rc_and_two_distractors(content):
    for scn in content.scenarios.values():
        if scn.type == "alarm":
            assert len(scn.root_causes) >= 2, scn.id
            assert len(scn.distractors) >= 2, scn.id


def test_every_rule_has_source_and_type(content):
    types = {r["type"] for r in content.rules}
    assert {"安全", "处置", "质量", "权限"} <= types
    for r in content.rules:
        assert r["source"] in ("师傅经验", "SOP", "安全规范")


@pytest.mark.parametrize("scn_id", sorted(["SCN-TOUR", "SCN-CHECK", "SCN-CHG-AB", "SCN-SHIFT"] + [f"SCN-T1{i:02d}" for i in range(1, 11)]))
def test_replay_and_negative(content, scn_id):
    scn = content.scenarios[scn_id]
    for r in replay(content, scn):
        assert r["passed"], r
    for n in run_negative(content, scn):
        assert n["passed"], n


def test_validate_all(content):
    rep = validate_all(content)
    assert rep["ok"]


def test_validate_yaml_text_errors(content):
    assert not validate_yaml_text(content, "a: [")["ok"]
    assert not validate_yaml_text(content, "- 1")["ok"]
    bad = validate_yaml_text(content, "id: X\nversion: 1\n")
    assert not bad["ok"] and bad["format_errors"]


def test_validate_yaml_text_reference_errors(content):
    data = content.scenarios["SCN-T103"].to_yaml_dict()
    data["id"] = "SCN-T103X"
    data["root_causes"][0]["correct_path"].append({"action": "fly_away"})
    data["root_causes"][0]["setup"] = {"scan_window": "melted"}
    data["rules"].append("R-NOPE-01")
    import yaml

    rep = validate_yaml_text(content, yaml.safe_dump(data, allow_unicode=True))
    assert not rep["ok"]
    joined = "；".join(rep["reference_errors"])
    assert "fly_away" in joined and "melted" in joined and "R-NOPE-01" in joined


def test_replay_detects_broken_path(content):
    data = content.scenarios["SCN-T101"].to_yaml_dict()
    data["id"] = "SCN-T101X"
    # 删掉“暂停”：换批头时设备未处于安全状态 → 引擎判安全终止 → 回放不通过
    rc0 = data["root_causes"][0]
    rc0["correct_path"] = [st for st in rc0["correct_path"] if st["action"] != "pause_equipment"]
    rep = validate_scenario(content, Scenario.model_validate(data))
    assert not rep["ok"]
    assert any(not r["passed"] for r in rep["replay"])


def test_scenario_model_rules(content):
    data = content.scenarios["SCN-T102"].to_yaml_dict()
    data["root_causes"] = data["root_causes"][:1]
    with pytest.raises(Exception):
        Scenario.model_validate(data)
    data = content.scenarios["SCN-T102"].to_yaml_dict()
    data["root_causes"][0]["weight"] = 0.9
    with pytest.raises(Exception):
        Scenario.model_validate(data)
    data = content.scenarios["SCN-T102"].to_yaml_dict()
    data["root_causes"][2]["andon_target"] = None
    with pytest.raises(Exception):
        Scenario.model_validate(data)
