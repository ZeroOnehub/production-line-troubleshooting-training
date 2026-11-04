"""每条安全规则（R-SAF-01 ~ R-SAF-08）一个专门测试；外加权限规则抽测。"""


def viol(s):
    return [v.rule_id for v in s.violations]


def test_saf01_hand_in_fixture_while_unsafe(engine, alarm_session):
    s = alarm_session("SCN-T104", "RC-T104-B")
    r = engine.act(s, "clean_fixture")
    assert r.terminated and "R-SAF-01" in viol(s) and s.terminated_by == "R-SAF-01"
    assert s.state == "review"


def test_saf01_allowed_after_pause(engine, alarm_session):
    s = alarm_session("SCN-T104", "RC-T104-B")
    engine.act(s, "pause_equipment")
    r = engine.act(s, "clean_fixture")
    assert r.ok and "R-SAF-01" not in viol(s)


def test_saf01_estop_counts_as_safe(engine, alarm_session):
    s = alarm_session("SCN-T104", "RC-T104-A")
    engine.act(s, "emergency_stop")
    r = engine.act(s, "reposition_workpiece")
    assert r.ok and "R-SAF-01" not in viol(s)


def test_saf02_tool_change_while_unsafe(engine, alarm_session):
    s = alarm_session("SCN-T101", "RC-T101-A")
    r = engine.act(s, "replace_bit")
    assert r.terminated and "R-SAF-02" in viol(s)


def test_saf03_bypass_safety(engine, alarm_session):
    s = alarm_session("SCN-T105", "RC-T105-C")
    r = engine.act(s, "bypass_safety")
    assert r.terminated and "R-SAF-03" in viol(s)


def test_saf04_release_estop_without_check(engine, alarm_session):
    s = alarm_session("SCN-T110", "RC-T110-A")
    r = engine.act(s, "release_estop")
    assert r.ok and "R-SAF-04" in viol(s)


def test_saf04_ok_after_safety_check(engine, alarm_session):
    s = alarm_session("SCN-T110", "RC-T110-A")
    engine.act(s, "safety_check")
    engine.act(s, "release_estop")
    assert "R-SAF-04" not in viol(s)


def test_saf05_release_estop_with_abnormal_equipment(engine, alarm_session):
    s = alarm_session("SCN-T110", "RC-T110-B")
    engine.act(s, "safety_check")
    r = engine.act(s, "release_estop")
    assert r.terminated and "R-SAF-05" in viol(s)


def test_saf06_start_with_broken_estop(engine):
    s = engine.new_session("SCN-CHECK", seed=1, faults=["F-CK03"], compute_standard=False)
    r = engine.act(s, "resume")
    assert r.terminated and "R-SAF-06" in viol(s)


def test_saf07_start_with_broken_curtain(engine):
    s = engine.new_session("SCN-CHECK", seed=1, faults=["F-CK04"], compute_standard=False)
    r = engine.act(s, "first_piece_check")
    assert r.terminated and "R-SAF-07" in viol(s)


def test_saf08_clean_curtain_while_unsafe(engine, alarm_session):
    s = alarm_session("SCN-T105", "RC-T105-B")
    r = engine.act(s, "clean_curtain_mirror")
    assert r.ok and "R-SAF-08" in viol(s)
    assert s.components["curtain_mirror"] == "clean"


def test_permission_rules_reject_and_record(engine, alarm_session):
    s = alarm_session("SCN-T108", "RC-T108-A")
    for action, rule in [("open_electrical_cabinet", "R-PRM-01"), ("modify_program_params", "R-PRM-02"),
                         ("adjust_sensor", "R-PRM-03"), ("disassemble_equipment", "R-PRM-04"),
                         ("reconnect_cable", "R-PRM-05"), ("adjust_regulator", "R-PRM-06"),
                         ("hmi_enter_maintenance", "R-PRM-07")]:
        r = engine.act(s, action)
        assert r.rejected and rule in viol(s), action
    # 越权尝试不改变设备状态
    assert s.components["controller_link"] == "cable_loose"
    assert s.state not in ("review", "safety_terminated")


def test_forbidden_attempt_recorded_while_waiting(engine, alarm_session):
    s = alarm_session("SCN-T108", "RC-T108-A")
    engine.act(s, "pull_andon", {"target": "maintenance"})
    engine.act(s, "andon_report", {"elements": "all"}, actor="phone")
    assert s.state == "waiting_repair"
    r = engine.act(s, "open_electrical_cabinet")
    assert r.rejected and "R-PRM-01" in viol(s)
    r = engine.act(s, "replace_bit")
    assert r.rejected and "等待维修" in r.message


def test_trainee_cannot_submit_report(engine, alarm_session):
    s = alarm_session("SCN-T108", "RC-T108-A")
    engine.act(s, "pull_andon", {"target": "maintenance"})
    r = engine.act(s, "andon_report", {"elements": "all"})
    assert r.rejected and "越权" in r.message


def test_guard_never_blocks_rule_quotes(content):
    """引擎拒绝时会原样说出规则原话（如“程序和参数谁让你改的”），守卫不能把它当成越权建议。"""
    from app.ai.guard import SafetyGuard

    g = SafetyGuard(content)
    for r in content.rules.values() if isinstance(content.rules, dict) else content.rules:
        quote = r["quote"] if isinstance(r, dict) else r.quote
        assert not g.check(quote).blocked, quote
        assert not g.check(f"权限规则：{quote}").blocked, quote
