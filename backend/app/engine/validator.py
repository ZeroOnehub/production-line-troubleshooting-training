"""关卡校验器：格式校验 → 引用校验 → 引擎自动回放每条正确路径 → 错误路径用例。

关卡工坊的“自动校验”、开发期的 `make validate`、评测台的“剧本首次校验通过率”都调用这里。
"""
from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError

from .engine import Engine
from .ontology import Content, Scenario, Step
from .predicates import facts_used

EVENT_FACTS = {
    "piece_is_ng", "piece_nonconforming", "piece_no", "first_piece_mode", "target_correct", "coverage",
    "ng_count_wrong", "alarm_code", "item_checked", "item_had_fault", "param_variant_matches_order",
    "param_result", "param_item", "param_target", "param_variant", "param_program",
}
SPECIAL_SETUP_KEYS = {"hmi.variant", "hmi.program", "order_variant", "bolt_box_variant", "bit_count"}


def known_facts(c: Content) -> set[str]:
    e = Engine(c)
    out: set[str] = set(EVENT_FACTS)
    for scn_id in c.scenarios:
        s = e.new_session(scn_id, seed=1, compute_standard=False)
        out |= set(e.facts(s).keys())
    out |= set(c.components)
    return out


def validate_content(c: Content) -> dict[str, Any]:
    """本体自身的一致性检查。"""
    errors: list[str] = []
    warnings: list[str] = []
    facts = known_facts(c)
    for r in c.rules:
        trig = r.get("trigger") or {}
        if trig.get("kind") not in ("action", "event"):
            errors.append(f"{r['id']}：trigger.kind 必须是 action 或 event（注意 YAML 会把 on 解析成布尔值）")
        if trig.get("kind") == "action" and not any(k in trig for k in ("actions", "zone", "categories")):
            errors.append(f"{r['id']}：动作类规则必须写 actions / zone / categories 之一")
        if trig.get("kind") == "event" and not trig.get("event"):
            errors.append(f"{r['id']}：事件类规则必须写 event")
        for a in trig.get("actions", []):
            if a not in c.actions:
                errors.append(f"{r['id']}：触发动作不存在 {a}")
        for a in r.get("actions", []):
            if a not in c.actions:
                errors.append(f"{r['id']}：对应动作不存在 {a}")
        if r["tag"] not in c.weakness_tags:
            errors.append(f"{r['id']}：薄弱点标签不在固定枚举中 {r['tag']}")
        for f in facts_used(r.get("when")):
            if f not in facts:
                errors.append(f"{r['id']}：条件引用了未知事实 {f}")
        for key in ("type", "quote", "executable", "source", "confirmed_by", "severity", "outcome", "dimension"):
            if not r.get(key):
                errors.append(f"{r['id']}：缺少字段 {key}")
    if len(c.rules) < 30:
        errors.append(f"规则只有 {len(c.rules)} 条，M1 要求不少于 30 条")
    for code, a in c.alarms.items():
        for f in facts_used(a.get("trigger")):
            if f not in facts:
                errors.append(f"{code}：触发条件引用了未知事实 {f}")
        for rc in a.get("root_causes", []):
            if rc not in c.rc_index:
                errors.append(f"{code}：报警定义列出的根因 {rc} 在关卡中不存在")
    for cid, comp in c.components.items():
        if comp.get("default") not in comp["states"]:
            errors.append(f"部件 {cid}：默认状态 {comp.get('default')} 不在状态表中")
    for name, err in c.scenario_errors.items():
        errors.append(f"{name}：{err}")
    closeup_dir = c.root / "stations" / "OP30" / "closeup"
    if closeup_dir.is_dir():
        for cid, comp in c.components.items():
            for st, sd in comp["states"].items():
                if sd.get("closeup") and not (closeup_dir / sd["closeup"]).exists():
                    warnings.append(f"近景图缺失：{sd['closeup']}（{comp['name']}·{sd['label']}）")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "rules": len(c.rules)}


def _check_step(c: Content, st: Step, where: str, errors: list[str]) -> None:
    a = c.actions.get(st.action)
    if a is None:
        errors.append(f"{where}：动作不存在 {st.action}")
        return
    for k, v in st.params.items():
        spec = (a.get("params") or {}).get(k)
        if spec is None:
            errors.append(f"{where}：动作 {st.action} 没有参数 {k}")
            continue
        if spec.get("type") == "target" and v not in c.inspect_targets():
            errors.append(f"{where}：查看目标不存在 {v}")
        if spec.get("type") == "enum" and v not in spec.get("values", []):
            errors.append(f"{where}：参数 {k}={v} 不在 {spec.get('values')} 中")
    for k, spec in (a.get("params") or {}).items():
        if spec.get("required") and k not in st.params:
            errors.append(f"{where}：动作 {st.action} 缺少参数 {k}")


def _check_setup(c: Content, setup: dict[str, Any], where: str, errors: list[str]) -> None:
    for k, v in setup.items():
        if k in SPECIAL_SETUP_KEYS:
            continue
        if k not in c.components:
            errors.append(f"{where}：初始状态引用了不存在的部件 {k}")
        elif str(v) not in c.components[k]["states"]:
            errors.append(f"{where}：部件 {k} 没有状态 {v}（可选：{list(c.components[k]['states'])}）")


def check_references(c: Content, scn: Scenario) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if scn.station != c.station["id"]:
        errors.append(f"工位 {scn.station} 不存在（当前本体只有 {c.station['id']}）")
    for rid in scn.rules:
        if rid not in c.rules_by_id:
            errors.append(f"引用的规则不存在：{rid}")
    if scn.type == "alarm":
        if scn.alarm not in c.alarms:
            errors.append(f"报警码不存在：{scn.alarm}")
        listed = set(c.alarms.get(scn.alarm or "", {}).get("root_causes", []))
        for rc in scn.root_causes:
            where = f"根因 {rc.id}"
            _check_setup(c, rc.setup, where, errors)
            if rc.repair:
                _check_setup(c, rc.repair, where + " 的 repair", errors)
            for i, st in enumerate(rc.correct_path):
                _check_step(c, st, f"{where} 正确路径第 {i+1} 步", errors)
            for cl in rc.clues:
                if cl.component and cl.component not in c.components:
                    errors.append(f"{where}：线索引用了不存在的部件 {cl.component}")
                if cl.component and cl.state and cl.state not in c.components.get(cl.component, {}).get("states", {}):
                    errors.append(f"{where}：线索状态不存在 {cl.component}.{cl.state}")
            if rc.id not in listed:
                warnings.append(f"{where} 未列入 alarms.yaml 的 {scn.alarm} 根因清单（发布后请同步本体）")
            if not rc.leak_keywords:
                warnings.append(f"{where} 没有写 leak_keywords，评测无法检查师傅提示是否泄露答案")
    for d in scn.distractors:
        if d.target not in c.inspect_targets():
            errors.append(f"干扰项目标不存在：{d.target}")
        elif d.target in c.components and scn.type == "alarm":
            for rc in scn.root_causes:
                if d.target in rc.setup:
                    warnings.append(f"干扰项 {d.target} 与根因 {rc.id} 的线索是同一部件（抽中该根因时显示真实异常）")
    for it in scn.items:
        for st in it.check:
            _check_step(c, st, f"点检项 {it.id}", errors)
        for f in it.faults:
            _check_setup(c, f.setup, f"异常 {f.id}", errors)
            for st in f.fix_path:
                _check_step(c, st, f"异常 {f.id} 处置路径", errors)
    for st in scn.steps:
        _check_step(c, st.as_step(), f"换型步骤 {st.name}", errors)
    for e in scn.codex:
        for t in e.targets:
            if t not in c.inspect_targets():
                errors.append(f"图鉴 {e.id} 的目标不存在：{t}")
    if scn.type == "shift" and scn.events:
        for sid in scn.events.get("random_faults", {}).get("pool", []):
            if sid not in c.scenarios:
                errors.append(f"上一个班的随机事件池引用了不存在的关卡 {sid}")
    for nc in scn.negative_cases:
        for i, st in enumerate(nc.path):
            _check_step(c, st, f"错误路径“{nc.name}”第 {i+1} 步", errors)
        for rid in nc.expect.get("violations", []):
            if rid not in c.rules_by_id:
                errors.append(f"错误路径“{nc.name}”期望的规则不存在：{rid}")
    return errors, warnings


def _variants(scn: Scenario) -> list[dict[str, Any]]:
    if scn.type == "alarm":
        return [{"root_cause": rc.id, "label": f"{rc.id} {rc.name}"} for rc in scn.root_causes]
    if scn.type == "checklist":
        out = [{"faults": [f.id], "label": f"{f.id} {f.name}"} for it in scn.items for f in it.faults]
        all_f = [f.id for it in scn.items for f in it.faults]
        if len(all_f) >= 2:
            out.append({"faults": all_f[:2], "label": "两个异常组合：" + "+".join(all_f[:2])})
        return out
    if scn.type == "shift":
        return [{"seed": sd, "label": f"随机种子 {sd}"} for sd in (11, 22, 33, 44, 55)]
    return [{"seed": 1, "label": "标准路径"}]


def replay(c: Content, scn: Scenario) -> list[dict[str, Any]]:
    """引擎自动回放每条正确路径，必须能通关（无违规、目标完成）。"""
    e = Engine(c)
    out = []
    for v in _variants(scn):
        kw = {k: v[k] for k in ("root_cause", "faults", "seed") if k in v}
        kw.setdefault("seed", 7)
        try:
            s = e.new_session(scn.id, compute_standard=False, **kw)
            results = e.run_autopilot(s)
            rejected = [r.message for r in results if r.rejected]
            ok = s.objective_met and not s.violations and not rejected and s.state in ("review", "completed")
            out.append({
                "variant": v["label"], "passed": ok, "steps": len(results),
                "violations": [x.rule_id for x in s.violations], "rejected": rejected,
                "state": s.state, "downtime_s": s.downtime, "duration_s": s.clock,
                "path": [r.action + (f"({','.join(f'{k}={vv}' for k, vv in r.params.items() if k != 'elements')})" if r.params else "") for r in results],
            })
        except Exception as exc:  # noqa: BLE001 - 校验器需要把所有异常报告出来
            out.append({"variant": v["label"], "passed": False, "steps": 0, "violations": [], "rejected": [],
                        "state": "error", "error": f"{type(exc).__name__}: {exc}"})
    return out


def run_negative(c: Content, scn: Scenario) -> list[dict[str, Any]]:
    e = Engine(c)
    out = []
    for nc in scn.negative_cases:
        try:
            kw: dict[str, Any] = {"seed": 7, "compute_standard": False}
            if nc.root_cause:
                kw["root_cause"] = nc.root_cause
            if nc.faults is not None:
                kw["faults"] = nc.faults
            s = e.new_session(scn.id, **kw)
            # 先推进到事件发生（异常/换型关卡）
            if scn.type in ("alarm", "changeover"):
                e.act(s, "run_production", source="replay")
            for st in nc.path:
                actor = "phone" if st.action == "andon_report" else "trainee"
                e.act(s, st.action, dict(st.params), actor=actor, source="replay")
            got = [v.rule_id for v in s.violations]
            exp = nc.expect
            ok = all(r in got for r in exp.get("violations", []))
            if "state" in exp:
                st_now = "safety_terminated" if s.terminated_by else s.state
                ok = ok and st_now == exp["state"]
            if "alarm" in exp:
                seen = {ep.alarm for ep in s.episodes}
                ok = ok and exp["alarm"] in seen
            out.append({"name": nc.name, "passed": ok, "expected": exp, "got_violations": got,
                        "state": "safety_terminated" if s.terminated_by else s.state,
                        "alarms": [ep.alarm for ep in s.episodes]})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": nc.name, "passed": False, "expected": nc.expect, "error": f"{type(exc).__name__}: {exc}"})
    return out


def validate_scenario(c: Content, scn: Scenario) -> dict[str, Any]:
    c2 = c.with_scenario(scn)
    ref_errors, warnings = check_references(c2, scn)
    rep: list[dict[str, Any]] = []
    neg: list[dict[str, Any]] = []
    if not ref_errors:
        rep = replay(c2, scn)
        neg = run_negative(c2, scn)
    ok = not ref_errors and all(r["passed"] for r in rep) and all(n["passed"] for n in neg)
    return {
        "scenario_id": scn.id,
        "version": scn.version,
        "ok": ok,
        "format_errors": [],
        "reference_errors": ref_errors,
        "warnings": warnings,
        "replay": rep,
        "negative": neg,
        "summary": _summary(ref_errors, rep, neg),
    }


def _summary(ref_errors: list[str], rep: list[dict[str, Any]], neg: list[dict[str, Any]]) -> str:
    if ref_errors:
        return f"引用校验未通过（{len(ref_errors)} 处）"
    bad = [r for r in rep if not r["passed"]]
    nbad = [n for n in neg if not n["passed"]]
    if bad:
        return f"引擎回放未通过：{len(bad)}/{len(rep)} 条正确路径无法通关"
    if nbad:
        return f"错误路径用例未通过：{len(nbad)}/{len(neg)}"
    return f"全部通过：{len(rep)} 条正确路径可通关，{len(neg)} 条错误路径判定符合预期"


def validate_yaml_text(c: Content, text: str) -> dict[str, Any]:
    """工坊草稿校验入口：从 YAML 文本开始。"""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return {"ok": False, "format_errors": [f"YAML 语法错误：{exc}"], "reference_errors": [], "warnings": [],
                "replay": [], "negative": [], "summary": "YAML 语法错误"}
    if not isinstance(data, dict):
        return {"ok": False, "format_errors": ["剧本必须是一个 YAML 映射"], "reference_errors": [], "warnings": [],
                "replay": [], "negative": [], "summary": "格式错误"}
    try:
        scn = Scenario.model_validate(data)
    except ValidationError as exc:
        errs = [f"{'.'.join(str(x) for x in e['loc'])}：{e['msg']}" for e in exc.errors()]
        return {"ok": False, "format_errors": errs, "reference_errors": [], "warnings": [], "replay": [],
                "negative": [], "summary": f"格式校验未通过（{len(errs)} 处）"}
    out = validate_scenario(c, scn)
    out["scenario"] = scn
    return out


def validate_all(c: Content) -> dict[str, Any]:
    content_report = validate_content(c)
    scenarios = {sid: validate_scenario(c, scn) for sid, scn in sorted(c.scenarios.items())}
    ok = content_report["ok"] and all(r["ok"] for r in scenarios.values()) and len(c.scenarios) >= 14
    return {"ok": ok, "content": content_report, "scenarios": scenarios, "scenario_count": len(c.scenarios)}
