"""评分与复盘：五维得分（安全、质量、效率、沟通、提示）全部由引擎按 scoring.yaml 计算，AI 只写点评。"""
from __future__ import annotations

from typing import Any

from .ontology import Content
from .state import SessionState

# 标准路径中的动作 → 漏做时对应的薄弱点标签
STEP_TAGS = {
    "pause_equipment": "停机再动手",
    "isolate_ng_part": "NG 隔离",
    "isolate_bolt_box": "NG 隔离",
    "first_piece_check": "首件确认",
    "call_quality_confirm": "首件确认",
    "safety_check": "急停处置",
    "pull_andon": "升级判断",
    "inspect": "根因排查",
    "andon_report": "汇报完整",
    "hmi_select_variant": "换型规范",
    "hmi_select_program": "换型规范",
    "replace_locating_block": "换型规范",
    "replace_bolt_box": "换型规范",
    "record_check": "点检规范",
    "sign_checklist": "点检规范",
    "test_estop": "点检规范",
    "test_light_curtain": "点检规范",
    "test_tower_light": "点检规范",
}


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def compute_scores(c: Content, s: SessionState) -> dict[str, Any]:
    cfg = c.scoring
    dims = {k: 100.0 for k in cfg["dimensions"]}
    deductions: dict[str, list[dict[str, Any]]] = {k: [] for k in dims}
    veto = False
    for v in s.violations:
        if v.severity == "veto":
            veto = veto or v.dimension == "safety"
        if v.deduction:
            deductions[v.dimension].append({"rule": v.rule_id, "deduction": v.deduction})
    # 安全
    dims["safety"] = 0.0 if s.terminated_by else _clamp(100 - sum(d["deduction"] for d in deductions["safety"]))
    # 质量
    dims["quality"] = 0.0 if s.ng_escaped else _clamp(100 - sum(d["deduction"] for d in deductions["quality"]))
    # 效率
    eff = cfg["efficiency"]
    std = s.standard or {}
    if std.get("downtime"):
        ratio = s.downtime / max(1.0, float(std["downtime"]))
        basis = "downtime"
    elif std.get("duration"):
        ratio = s.clock / max(1.0, float(std["duration"]))
        basis = "duration"
    else:
        ratio, basis = 1.0, "none"
    base = 100.0 if ratio <= eff["full_ratio"] else 100.0 - (ratio - eff["full_ratio"]) * eff["slope_per_ratio"]
    base = max(float(eff["floor"]), base)
    if s.scenario_type == "tour":
        base = 100.0
    dims["efficiency"] = _clamp(base - sum(d["deduction"] for d in deductions["efficiency"]))
    # 沟通
    com = cfg["communication"]
    if s.andon_reports:
        vals = [r["coverage"] * 100 - com["follow_up_penalty"] * r.get("follow_ups", 0) for r in s.andon_reports]
        cbase = sum(vals) / len(vals)
    else:
        cbase = float(com["no_report_score"])
    dims["communication"] = _clamp(cbase - sum(d["deduction"] for d in deductions["communication"]))
    # 提示
    cost = {int(k): float(v) for k, v in cfg["hints"]["cost"].items()}
    dims["hints"] = _clamp(100 - sum(cost.get(int(h["level"]), 0) for h in s.hints))
    if s.terminated_by:
        # 安全终止：其余维度按已发生的过程计算，但效率不给满分
        dims["efficiency"] = min(dims["efficiency"], float(eff["floor"]))
    total = sum(dims[k] * float(cfg["dimensions"][k]["weight"]) for k in dims)
    total = round(total, 1)
    passed = (
        s.objective_met
        and not s.terminated_by
        and not s.ng_escaped
        and not veto
        and total >= float(cfg["pass"]["min_total"])
        and not any("超过阈值" in r for r in s.fail_reasons)
        and not any("漏检" in r for r in s.fail_reasons)
    )
    stars = 0
    if passed:
        stars = 3 if total >= cfg["stars"]["three"] else 2 if total >= cfg["stars"]["two"] else 1
    return {
        "dimensions": {k: round(v, 1) for k, v in dims.items()},
        "dimension_names": {k: cfg["dimensions"][k]["name"] for k in dims},
        "weights": {k: cfg["dimensions"][k]["weight"] for k in dims},
        "deductions": deductions,
        "efficiency_ratio": round(ratio, 2),
        "efficiency_basis": basis,
        "total": total,
        "passed": passed,
        "stars": stars,
    }


def compare_path(c: Content, s: SessionState) -> dict[str, Any]:
    """把学员的实际操作与标准路径对齐（按顺序贪心匹配）。"""
    std = (s.standard or {}).get("steps", [])
    acts = [e for e in s.log if e.actor in ("trainee", "phone") and e.result != "rejected" and e.action != "run_production"]
    used = [False] * len(acts)
    rows = []
    cursor = 0
    for st in std:
        hit = None
        for j in range(cursor, len(acts)):
            if used[j]:
                continue
            e = acts[j]
            if e.action == st["action"] and all(e.params.get(k) == v for k, v in st["params"].items()):
                hit = j
                break
        if hit is None:
            for j in range(len(acts)):
                e = acts[j]
                if not used[j] and e.action == st["action"] and all(e.params.get(k) == v for k, v in st["params"].items()):
                    hit = j
                    break
        name = c.actions[st["action"]]["name"]
        label = name
        if st["action"] == "pull_andon" and st["params"].get("target"):
            label += "：" + c.andon_targets[st["params"]["target"]]["name"]
        elif st["params"].get("target"):
            label += "：" + c.target_name(st["params"]["target"])
        elif st["params"].get("variant"):
            label += f"（{st['params']['variant']}）"
        elif st["params"].get("program"):
            label += f"（{st['params']['program']}）"
        if hit is not None:
            used[hit] = True
            cursor = hit + 1
            rows.append({"step": st, "label": label, "status": "done", "t": acts[hit].t, "seq": acts[hit].seq})
        else:
            rows.append({"step": st, "label": label, "status": "missed", "t": None, "seq": None,
                         "tag": STEP_TAGS.get(st["action"])})
    extras = []
    for j, e in enumerate(acts):
        if not used[j] and e.action not in ("inspect",):
            extras.append({"seq": e.seq, "t": e.t, "action": e.action, "name": c.actions.get(e.action, {}).get("name", e.action),
                           "params": e.params, "violations": e.violations})
    rejected = [{"seq": e.seq, "t": e.t, "action": e.action, "name": c.actions.get(e.action, {}).get("name", e.action),
                 "message": e.message, "violations": e.violations}
                for e in s.log if e.actor == "trainee" and e.result == "rejected"]
    done = sum(1 for r in rows if r["status"] == "done")
    return {"rows": rows, "extras": extras, "rejected": rejected, "match_rate": round(done / len(rows), 3) if rows else 1.0}


def weakness_tags(c: Content, s: SessionState, scores: dict[str, Any], path: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for v in s.violations:
        if v.tag not in tags:
            tags.append(v.tag)
    for r in path["rows"]:
        t = r.get("tag")
        if r["status"] == "missed" and t and t not in tags and r["step"]["action"] != "inspect":
            tags.append(t)
    if len(s.hints) >= 3 and "依赖提示" not in tags:
        tags.append("依赖提示")
    if scores["dimensions"]["efficiency"] < 60 and "处置效率" not in tags:
        tags.append("处置效率")
    return [t for t in tags if t in c.weakness_tags]


def build_review(c: Content, s: SessionState) -> dict[str, Any]:
    scores = compute_scores(c, s)
    path = compare_path(c, s)
    tags = weakness_tags(c, s, scores, path)
    rc = None
    if s.root_cause and s.root_cause in c.rc_index:
        scn, r = c.rc_index[s.root_cause]
        rc = {"id": r.id, "name": r.name, "in_operator_scope": r.in_operator_scope, "andon_target": r.andon_target}
    events = []
    for ev in s.schedule:
        item = {"at_piece": ev["at_piece"], "type": ev["type"], "fired": ev["fired"]}
        if ev.get("root_cause") and ev["root_cause"] in c.rc_index:
            scn, r = c.rc_index[ev["root_cause"]]
            item.update({"alarm": scn.alarm, "root_cause": r.name, "scenario": scn.id})
        if ev["type"] == "changeover_order":
            item["to_variant"] = ev.get("to_variant")
        events.append(item)
    consequences = []
    if s.ng_escaped:
        consequences.append(f"{s.ng_escaped} 件 NG 流到下游，在 OP50 终检（EOL）被发现，需要整批追溯。")
    if s.unconfirmed_count:
        consequences.append(f"{s.unconfirmed_count} 件在应做首件确认时未确认，记为未确认件。")
    if s.terminated_by:
        consequences.append(f"触发一票否决的安全规则 {s.terminated_by}，训练局安全终止。")
    timeline = [e.model_dump() for e in s.log]
    return {
        "scenario_id": s.scenario_id,
        "scenario_version": s.scenario_version,
        "scenario_type": s.scenario_type,
        "root_cause": rc,
        "events": events,
        "scores": scores,
        "passed": scores["passed"],
        "objective_met": s.objective_met,
        "fail_reasons": s.fail_reasons,
        "terminated_by": s.terminated_by,
        "downtime_s": s.downtime,
        "standard_downtime_s": (s.standard or {}).get("downtime"),
        "duration_s": s.clock,
        "loss_yuan": round(s.downtime / 60.0 * float(c.station["loss_per_min_yuan"]), 0),
        "violations": [v.model_dump() for v in s.violations],
        "hints": s.hints,
        "andon_reports": s.andon_reports,
        "path": path,
        "timeline": timeline,
        "weakness_tags": tags,
        "consequences": consequences,
        "produced_ok": s.produced_ok,
        "ng_escaped": s.ng_escaped,
    }
