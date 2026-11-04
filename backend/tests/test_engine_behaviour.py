"""引擎行为：确定性、报警复现、Andon 汇报与到场、首件、NG 流出、换型漏步、点检、认识工位、上一个班、评分与复盘。"""
from app.engine.scoring import build_review, compare_path, compute_scores


def viol(s):
    return [v.rule_id for v in s.violations]


def test_determinism(engine):
    a = engine.new_session("SCN-SHIFT", seed=123)
    b = engine.new_session("SCN-SHIFT", seed=123)
    assert a.schedule == b.schedule
    assert a.standard == b.standard
    ra = engine.run_autopilot(a)
    rb = engine.run_autopilot(b)
    assert [r.message for r in ra] == [r.message for r in rb]
    assert a.clock == b.clock and a.downtime == b.downtime


def test_root_cause_draw_respects_weights(engine):
    seen = {engine.new_session("SCN-T101", seed=i, compute_standard=False).root_cause for i in range(200)}
    assert seen == {"RC-T101-A", "RC-T101-B", "RC-T101-C"}


def test_alarm_emerges_from_state(engine, alarm_session):
    s = alarm_session("SCN-T101", "RC-T101-A")
    assert s.alarm == "T-101" and s.state == "abnormal" and s.mode == "alarm_stop"
    assert s.current_piece.is_ng and s.hmi["ng_count"] == 1
    assert s.produced_ok == 2 and s.downtime_since is not None


def test_recurrence_and_hdl03(engine, alarm_session):
    s = alarm_session("SCN-T105", "RC-T105-C")
    engine.act(s, "inspect", {"target": "hmi"})
    for _ in range(2):
        engine.act(s, "reset_alarm")
        r = engine.act(s, "resume")
        assert not r.ok or s.alarm == "T-105"
    assert s.episodes[-1].recurrence == 2
    engine.act(s, "reset_alarm")
    assert "R-HDL-03" in viol(s)
    # 拉了 Andon 之后再复位不再记 R-HDL-03
    n = viol(s).count("R-HDL-03")
    engine.act(s, "pull_andon", {"target": "maintenance"})
    assert s.state == "andon_called"


def test_hdl02_reset_without_looking(engine, alarm_session):
    s = alarm_session("SCN-T104", "RC-T104-A")
    engine.act(s, "reset_alarm")
    assert "R-HDL-02" in viol(s)


def test_hdl01_handle_before_reading_alarm(engine, alarm_session):
    s = alarm_session("SCN-T103", "RC-T103-A")
    engine.act(s, "clean_scanner_window")
    assert "R-HDL-01" in viol(s)


def test_andon_report_coverage_drives_arrival(engine, alarm_session):
    full = alarm_session("SCN-T104", "RC-T104-D")
    engine.act(full, "inspect", {"target": "pos_sensor"})
    engine.act(full, "pull_andon", {"target": "maintenance"})
    engine.act(full, "andon_report", {"elements": "all"}, actor="phone")
    part = alarm_session("SCN-T104", "RC-T104-D")
    engine.act(part, "inspect", {"target": "pos_sensor"})
    engine.act(part, "pull_andon", {"target": "maintenance"})
    engine.act(part, "andon_report", {"elements": {"station": "OP30", "alarm_code": None, "actions_done": [], "ng_count": 3}}, actor="phone")
    rf, rp = full.andon_reports[-1], part.andon_reports[-1]
    assert rf["coverage"] == 1.0 and rp["coverage"] == 0.25
    assert rp["delay_s"] - rf["delay_s"] == 3 * 90
    assert "R-COM-01" in viol(part) and "R-COM-02" in viol(part)


def test_score_report_false_claims(engine, alarm_session):
    s = alarm_session("SCN-T101", "RC-T101-A")
    engine.act(s, "inspect", {"target": "hmi"})
    engine.act(s, "pull_andon", {"target": "maintenance"})
    sc = engine.score_report(s, {"station": "op30", "alarm_code": "t-101", "actions_done": ["replace_bit"], "ng_count": "1"})
    assert sc["covered"]["station"] and sc["covered"]["alarm_code"] and sc["covered"]["ng_count"]
    assert not sc["covered"]["actions_done"] and sc["false_claims"] == ["replace_bit"]


def test_wrong_target_is_redirected(engine, alarm_session):
    s = alarm_session("SCN-T102", "RC-T102-C")
    engine.act(s, "pause_equipment")
    engine.act(s, "isolate_ng_part")
    engine.act(s, "pull_andon", {"target": "maintenance"})
    engine.act(s, "andon_report", {"elements": "all"}, actor="phone")
    r = engine.act(s, "wait")
    assert "R-HDL-05" in viol(s) and s.state == "investigating" and "质量员" in r.message
    assert s.components["thread_hole"] == "debris"


def test_unnecessary_escalation_still_fixed(engine, alarm_session):
    s = alarm_session("SCN-T101", "RC-T101-A")
    engine.act(s, "inspect", {"target": "hmi"})
    engine.act(s, "pause_equipment")
    engine.act(s, "isolate_ng_part")
    engine.act(s, "pull_andon", {"target": "maintenance"})
    assert "R-HDL-04" in viol(s)
    engine.act(s, "andon_report", {"elements": "all"}, actor="phone")
    r = engine.act(s, "wait")
    assert s.components["bit"] == "normal" and "自己就能处理" in r.message
    engine.act(s, "first_piece_check")
    engine.act(s, "resume")
    assert s.objective_met and s.state == "review"


def test_ng_escape_and_first_piece(engine, alarm_session):
    s = alarm_session("SCN-T101", "RC-T101-A")
    engine.act(s, "inspect", {"target": "hmi"})
    engine.act(s, "pause_equipment")
    engine.act(s, "replace_bit")
    engine.act(s, "reset_alarm")
    engine.act(s, "resume")
    assert "R-QLT-02" in viol(s) and "R-HDL-07" in viol(s)
    assert s.ng_escaped == 1 and s.unconfirmed_count >= 1
    rev = build_review(engine.c, s)
    assert not rev["passed"] and any("OP50" in c for c in rev["consequences"])


def test_material_call_flow(engine, alarm_session):
    s = alarm_session("SCN-T109", "RC-T109-A")
    engine.act(s, "inspect", {"target": "bolt_box"})
    engine.act(s, "call_material")
    assert s.state == "waiting_repair"
    assert engine.act(s, "resume").rejected
    engine.act(s, "wait")
    assert s.components["bolt_box"] == "normal"
    engine.act(s, "reset_alarm")
    engine.act(s, "resume")
    assert s.objective_met


def test_rescan_paths(engine, alarm_session):
    s = alarm_session("SCN-T103", "RC-T103-A")
    engine.act(s, "inspect", {"target": "scan_window"})
    r = engine.act(s, "rescan")
    assert s.alarm == "T-103" and s.episodes[-1].recurrence == 1 and not r.rejected
    engine.act(s, "clean_scanner_window")
    engine.act(s, "rescan")
    assert s.alarm is None and s.state == "recovery_check"
    engine.act(s, "resume")
    assert s.objective_met


def test_self_estop_issue(engine):
    s = engine.new_session("SCN-T107", seed=3, root_cause="RC-T107-A", compute_standard=False)
    engine.act(s, "emergency_stop")
    assert s.alarm == "T-110" and s.issue.kind == "self_estop"
    assert engine.next_standard_step(s, for_hint=True)["action"] == "safety_check"
    assert engine.act(s, "reset_alarm").rejected
    engine.act(s, "safety_check")
    engine.act(s, "release_estop")
    engine.act(s, "reset_alarm")
    engine.act(s, "resume")
    assert s.state == "normal_production" and s.issue is None


def test_preconditions(engine, alarm_session):
    s = alarm_session("SCN-T104", "RC-T104-A")
    assert engine.act(s, "resume").rejected
    assert engine.act(s, "release_estop").rejected
    assert engine.act(s, "rescan").rejected
    assert engine.act(s, "wait").rejected
    assert engine.act(s, "call_quality_confirm").rejected
    assert engine.act(s, "test_estop").rejected
    assert engine.act(s, "record_check", {"item": "CK-01", "result": "ok"}).rejected
    assert engine.act(s, "cycle_step").rejected
    assert engine.act(s, "inspect", {"target": "nothing"}).rejected
    assert engine.act(s, "pull_andon", {"target": "boss"}).rejected
    assert engine.act(s, "pull_andon").rejected
    assert engine.act(s, "no_such_action").rejected
    assert engine.act(s, "andon_report", {"elements": "all"}, actor="phone").rejected


def test_observe_texts(engine, alarm_session):
    s = alarm_session("SCN-T101", "RC-T101-B")
    assert "混有" in engine.observe(s, "bolt_box")["text"]
    assert "0.52" in engine.observe(s, "pressure_gauge")["text"]
    assert "划痕" in engine.observe(s, "socket")["text"]
    assert "T-101" in engine.observe(s, "hmi")["text"]
    assert "测试" in engine.observe(s, "estop_function")["text"]
    assert engine.observe(s, "tighten_curve")["text"]
    assert "条码" in engine.observe(s, "workpiece")["text"]
    assert engine.observe(s, "surroundings")["text"]
    assert engine.observe(s, "tower_light")["values"]["color"] == "red"
    assert "上锁" in engine.observe(s, "electrical_cabinet")["text"]
    assert engine.observe(s, "curtain_status")["text"] and engine.observe(s, "estop_status")["text"]
    assert "4100" in engine.observe(s, "bit_counter")["text"] or "次" in engine.observe(s, "bit_counter")["text"]
    engine.act(s, "pause_equipment")
    engine.act(s, "isolate_ng_part")
    assert "没有工件" in engine.observe(s, "label")["text"]
    assert "没有工件" in engine.observe(s, "workpiece")["text"]


def test_pressure_zones(engine):
    s = engine.new_session("SCN-T107", seed=1, root_cause="RC-T107-B", compute_standard=False)
    assert engine.pressure_zone(engine.pressure(s)) == "绿区"
    engine.act(s, "run_production")
    assert s.alarm == "T-107" and engine.pressure_zone(engine.pressure(s)) == "红区"
    s2 = engine.new_session("SCN-T104", seed=1, root_cause="RC-T104-C", compute_standard=False)
    engine.act(s2, "run_production")
    assert s2.alarm == "T-104" and engine.pressure_zone(engine.pressure(s2)) == "黄区"
    engine.act(s2, "pull_andon", {"target": "maintenance"})
    assert "R-HDL-08" in viol(s2)


def test_changeover_miss_subissue_and_hint(engine):
    s = engine.new_session("SCN-CHG-AB", seed=1, compute_standard=False)
    engine.act(s, "run_production")
    assert s.changeover["active"] and s.issue.kind == "changeover"
    assert engine.act(s, "hmi_select_variant", {"variant": "B"}).ok
    assert "R-HDL-06" in viol(s)  # 运行中换型
    engine.act(s, "pause_equipment")
    engine.act(s, "hmi_select_program", {"program": "P02"})
    engine.act(s, "replace_bolt_box", {"variant": "B"})
    engine.act(s, "first_piece_check")
    assert s.alarm == "T-104" and s.issue.kind == "mismatch" and "R-HDL-11" in viol(s)
    nxt = engine.next_standard_step(s, for_hint=True)
    assert nxt["action"] == "pause_equipment"
    engine.act(s, "pause_equipment")
    assert engine.next_standard_step(s, for_hint=True)["action"] == "replace_locating_block"
    engine.act(s, "replace_locating_block", {"variant": "B"})
    engine.act(s, "reset_alarm")
    assert s.issue.kind == "changeover"
    engine.act(s, "first_piece_check")
    engine.act(s, "call_quality_confirm")
    engine.act(s, "resume")
    assert s.objective_met and s.state == "review"


def test_changeover_t106_needs_team_leader(engine):
    s = engine.new_session("SCN-CHG-AB", seed=1, compute_standard=False)
    engine.act(s, "run_production")
    engine.act(s, "pause_equipment")
    engine.act(s, "replace_locating_block", {"variant": "B"})
    engine.act(s, "replace_bolt_box", {"variant": "B"})
    engine.act(s, "hmi_select_program", {"program": "P02"})
    engine.act(s, "first_piece_check")
    assert s.alarm == "T-106" and not s.issue.in_scope
    r = engine.act(s, "hmi_select_variant", {"variant": "B"})
    assert r.rejected and "R-PRM-08" in viol(s)
    for st in ["pause_equipment", "isolate_ng_part"]:
        engine.act(s, st)
    engine.act(s, "pull_andon", {"target": "team_leader"})
    engine.act(s, "andon_report", {"elements": "all"}, actor="phone")
    engine.act(s, "wait")
    assert s.hmi["variant"] == "B" and s.issue.kind == "changeover"
    engine.act(s, "first_piece_check")
    engine.act(s, "call_quality_confirm")
    engine.act(s, "resume")
    assert s.objective_met


def test_checklist_flow_and_records(engine):
    s = engine.new_session("SCN-CHECK", seed=1, faults=["F-CK02", "F-CK05"], compute_standard=False)
    assert s.state == "investigating" and s.mode == "stopped"
    assert engine.observe(s, "bit")["text"].startswith("批头刀口明显磨圆")
    engine.act(s, "test_tower_light")
    engine.act(s, "record_check", {"item": "CK-02", "result": "ok"})
    assert "R-QLT-06" in viol(s)
    engine.act(s, "pull_andon", {"target": "maintenance"})
    engine.act(s, "andon_report", {"elements": {"station": "OP30", "actions_done": ["test_tower_light"]}}, actor="phone")
    assert s.andon_reports[-1]["coverage"] == 1.0
    engine.act(s, "wait")
    assert s.components["tower_lamp"] == "normal"
    snap = engine.snapshot(s)
    assert snap["checklist"]["items"][1]["recorded"] == "ok"
    engine.act(s, "resume")
    assert "R-QLT-07" in viol(s)


def test_checklist_wrong_andon(engine):
    s = engine.new_session("SCN-CHECK", seed=1, faults=["F-CK05"], compute_standard=False)
    engine.act(s, "pull_andon", {"target": "quality"})
    engine.act(s, "andon_report", {"elements": "all"}, actor="phone")
    engine.act(s, "wait")
    assert "R-HDL-05" in viol(s) and s.state == "investigating"


def test_checklist_missed_fault_fails(engine):
    s = engine.new_session("SCN-CHECK", seed=1, faults=["F-CK02"], compute_standard=False)
    for it in engine.c.scenarios["SCN-CHECK"].items:
        if it.sign_item:
            continue
        for st in it.check:
            engine.act(s, st.action, dict(st.params))
        engine.act(s, "record_check", {"item": it.id, "result": "ok"})
    engine.act(s, "sign_checklist")
    engine.act(s, "first_piece_check")
    engine.act(s, "resume")
    assert s.state == "review" and not s.objective_met
    assert any("漏检" in r for r in s.fail_reasons)
    assert not compute_scores(engine.c, s)["passed"]


def test_tour(engine):
    s = engine.new_session("SCN-TOUR", seed=1)
    snap = engine.snapshot(s)
    assert all(a["panel"] in ("观察", "设备", "认识工位") for a in snap["actions"])
    engine.run_autopilot(s)
    assert s.objective_met and len(s.codex) == 12 and s.cycle_step_index == 7
    sc = compute_scores(engine.c, s)
    assert sc["passed"] and sc["stars"] == 3


def test_shift_pass_and_threshold(engine):
    s = engine.new_session("SCN-SHIFT", seed=11)
    engine.run_autopilot(s)
    assert s.objective_met and s.state == "review" and s.produced_ok >= 16
    rev = build_review(engine.c, s)
    assert rev["passed"] and len([e for e in rev["events"] if e["type"] == "fault"]) >= 3
    # 人为加长停线：超过阈值即不通过
    s2 = engine.new_session("SCN-SHIFT", seed=11)
    while s2.state not in ("review",):
        st = engine.next_standard_step(s2)
        if st is None:
            break
        if s2.state == "abnormal":
            engine.advance_time(s2, 900)
        actor = "phone" if st["action"] == "andon_report" else "trainee"
        engine.act(s2, st["action"], st["params"], actor=actor)
    assert any("超过阈值" in r for r in s2.fail_reasons)
    assert not compute_scores(engine.c, s2)["passed"]


def test_shift_hides_violations_until_review(engine):
    s = engine.new_session("SCN-SHIFT", seed=11, compute_standard=False)
    engine.act(s, "run_production")
    engine.act(s, "open_electrical_cabinet")
    assert s.violations and engine.snapshot(s)["violations"] == []


def test_snapshot_hides_root_cause(engine, alarm_session):
    s = alarm_session("SCN-T101", "RC-T101-A")
    snap = engine.snapshot(s)
    text = str(snap)
    assert "RC-T101-A" not in text and "批头磨损" not in text
    coach = engine.coach_view(s)
    assert coach["root_cause"]["name"] == "批头磨损"
    assert coach["next_step"]["action"] in ("inspect", "pause_equipment")
    assert engine.snapshot(s, reveal=True)["hidden"]["root_cause"]["id"] == "RC-T101-A"


def test_scores_and_path_compare(engine):
    s = engine.new_session("SCN-T101", seed=5, root_cause="RC-T101-A")
    engine.act(s, "run_production")
    engine.act(s, "pause_equipment")
    engine.act(s, "isolate_ng_part")
    engine.act(s, "replace_bit")
    engine.act(s, "reset_alarm")
    engine.act(s, "first_piece_check")
    s.hints.append({"level": 2, "t": s.clock, "text": "x"})
    engine.act(s, "resume")
    sc = compute_scores(engine.c, s)
    assert sc["dimensions"]["hints"] == 80 and sc["dimensions"]["safety"] == 100
    assert sc["dimensions"]["efficiency"] < 100  # R-HDL-01 扣效率
    path = compare_path(engine.c, s)
    missed = [r for r in path["rows"] if r["status"] == "missed"]
    assert {r["step"]["action"] for r in missed} == {"inspect"}
    rev = build_review(engine.c, s)
    assert "报警识别" in rev["weakness_tags"] and rev["root_cause"]["name"] == "批头磨损"


def test_end_session_and_terminal(engine, alarm_session):
    s = alarm_session("SCN-T102", "RC-T102-A")
    engine.advance_time(s, 30)
    engine.end_session(s)
    assert s.state == "review" and "关卡目标未完成" in s.fail_reasons
    assert engine.act(s, "inspect", {"target": "hmi"}).rejected
    engine.end_session(s)


def test_available_actions_follow_state(engine, alarm_session):
    s = alarm_session("SCN-T108", "RC-T108-A")
    engine.act(s, "pull_andon", {"target": "maintenance"})
    engine.act(s, "andon_report", {"elements": "all"}, actor="phone")
    enabled = {a["id"] for a in engine.available_actions(s) if a["enabled"]}
    assert enabled == {"isolate_ng_part", "isolate_bolt_box", "tidy_workstation", "wait", "emergency_stop"}


def test_step_demo_text(engine):
    assert "维修" in engine.step_demo_text({"action": "pull_andon", "params": {"target": "maintenance"}})
    assert "批头" in engine.step_demo_text({"action": "inspect", "params": {"target": "bit"}})
    assert engine.step_demo_text(None) is None
