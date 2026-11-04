"""确定性状态机引擎。

- 读取 content/ 的本体与关卡运行；AI 只能通过 `Engine.act()` 改变训练局状态。
- 根因只改部件状态，报警由工作循环按 alarms.yaml 的触发条件判定产生。
- 规则（rules.yaml）在“动作执行前”与“引擎事件发生时”两个时机判定。
- 同一个种子 + 同一串动作 → 完全相同的结果（回放校验、评测、演示数据都依赖这一点）。
"""
from __future__ import annotations

import copy
import random
from typing import Any

from pydantic import BaseModel, Field

from .ontology import Content, RootCause, Scenario, Step
from .predicates import evaluate
from .state import Episode, Issue, LogEntry, Piece, SessionState, Support, Violation

TERMINAL = {"safety_terminated", "review", "completed"}
SAFE_ZONES_RULE = {"fixture", "tool", "curtain"}
TESTS_FOR_HIDDEN = {
    "estop_function": "test_estop",
    "curtain_function": "test_light_curtain",
    "tower_lamp": "test_tower_light",
}
REPORT_ELEMENTS = ("station", "alarm_code", "actions_done", "ng_count")


class EngineError(Exception):
    pass


def _as_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class ActResult(BaseModel):
    ok: bool
    rejected: bool = False
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    observation: dict[str, Any] | None = None
    violations: list[Violation] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    state: str = ""
    terminated: bool = False
    ended: bool = False
    seq: int = 0


class _Ctx:
    """一次动作执行过程中的上下文：收集消息、事件与违规。"""

    def __init__(self, action: str, params: dict[str, Any], actor: str, source: str):
        self.action = action
        self.params = params
        self.actor = actor
        self.source = source
        self.messages: list[str] = []
        self.events: list[dict[str, Any]] = []
        self.violations: list[Violation] = []
        self.observation: dict[str, Any] | None = None
        self.success = True        # 动作是否达成目的（用于标准路径进度）
        self.state_set = False


class Engine:
    def __init__(self, content: Content):
        self.c = content
        self.takt = float(content.station["takt_s"])
        self.loss_per_min = float(content.station["loss_per_min_yuan"])

    # ==================================================================
    # 开局
    # ==================================================================
    def new_session(
        self,
        scenario_id: str,
        seed: int | None = None,
        root_cause: str | None = None,
        faults: list[str] | None = None,
        compute_standard: bool = True,
    ) -> SessionState:
        if scenario_id not in self.c.scenarios:
            raise EngineError(f"关卡不存在：{scenario_id}")
        scn = self.c.scenarios[scenario_id]
        if seed is None:
            seed = random.randrange(1, 10**9)
        rng = random.Random(seed)
        dv = self.c.station.get("default_variant", "A")
        s = SessionState(
            scenario_id=scn.id,
            scenario_version=scn.version,
            scenario_type=scn.type,
            seed=seed,
            order_variant=dv,
            bolt_box_variant=dv,
        )
        s.components = {cid: str(c["default"]) for cid, c in self.c.components.items()}
        s.components["locating_block"] = dv
        s.hmi = {
            "variant": dv,
            "program": self.c.variants[dv]["program"],
            "alarm": None,
            "alarm_text": None,
            "torque_result": "—",
            "angle_result": "—",
            "scanned_variant": None,
            "ok_count": 0,
            "ng_count": 0,
            "bit_count": int(self.c.bit_life["normal_count"]),
            "cycle_step": "待机",
            "curve": "none",
        }
        for d in scn.distractors:
            s.distractors[d.target] = d.text
            if d.target == "pressure_gauge" and d.value is not None:
                s.pressure_override = float(d.value)

        if scn.type == "tour":
            s.state, s.mode = "normal_production", "running"
            plan = [{"action": "inspect", "params": {"target": e.targets[0]}} for e in scn.codex]
            plan += [{"action": "cycle_step", "params": {}} for _ in scn.cycle_steps]
            s.issue = Issue(id=scn.id, kind="tour", name=scn.title, path=plan, done=[False] * len(plan))
        elif scn.type == "checklist":
            s.mode = scn.initial.get("mode", "stopped")
            s.first_piece_pending = bool(scn.initial.get("first_piece_pending", True))
            all_faults = [f for it in scn.items for f in it.faults]
            if faults is None:
                lo = (scn.pick_faults or {}).get("min", 1)
                hi = (scn.pick_faults or {}).get("max", 2)
                k = rng.randint(lo, hi)
                chosen = rng.sample(all_faults, k=min(k, len(all_faults)))
                faults = [f.id for f in chosen]
            fault_ids = [f.id for f in all_faults if f.id in set(faults)]
            for f in all_faults:
                if f.id in fault_ids:
                    self._apply_setup(s, f.setup)
            s.checklist = {
                "items": {it.id: {"checked": False, "recorded": None} for it in scn.items if not it.sign_item},
                "faults": fault_ids,
                "signed": False,
            }
            s.state = "investigating"
            plan = self._checklist_plan(scn, fault_ids)
            s.issue = Issue(id=scn.id, kind="checklist", name=scn.title, path=plan, done=[False] * len(plan), strict_order=True)
        elif scn.type == "changeover":
            s.state, s.mode = "normal_production", "running"
            ev = scn.event or {"at_piece": 2}
            s.schedule = [{"at_piece": int(ev.get("at_piece", 2)), "type": "changeover_order",
                           "to_variant": scn.to_variant, "scenario": scn.id,
                           "notice": ev.get("notice", "班组长：下一件开始换型。"), "fired": False}]
        elif scn.type == "alarm":
            s.state, s.mode = "normal_production", "running"
            rc = scn.rc(root_cause) if root_cause else self._draw_rc(scn, rng)
            s.root_cause = rc.id
            at = int((scn.event or {}).get("at_piece", 3))
            s.schedule = [{"at_piece": at, "type": "fault", "scenario": scn.id, "root_cause": rc.id, "fired": False}]
        elif scn.type == "shift":
            s.state, s.mode = "normal_production", "running"
            s.target_pieces = int(scn.target_pieces or 16)
            s.schedule = self._shift_schedule(scn, rng)
        if compute_standard:
            s.standard = self._compute_standard(s)
        self._log_system(s, "start_session", f"开局：{scn.title}")
        return s

    def _draw_rc(self, scn: Scenario, rng: random.Random) -> RootCause:
        r = rng.random() * sum(rc.weight for rc in scn.root_causes)
        acc = 0.0
        for rc in scn.root_causes:
            acc += rc.weight
            if r <= acc:
                return rc
        return scn.root_causes[-1]

    def _shift_schedule(self, scn: Scenario, rng: random.Random) -> list[dict[str, Any]]:
        ev = scn.events or {}
        out: list[dict[str, Any]] = []
        rf = ev.get("random_faults", {})
        lo, hi = rf.get("count", [2, 3])
        n = rng.randint(int(lo), int(hi))
        pool = [p for p in rf.get("pool", []) if p in self.c.scenarios]
        picks = rng.sample(pool, k=min(n, len(pool)))
        pieces = list(rf.get("pieces", [3, 6, 12, 14]))
        for i, scn_id in enumerate(picks):
            rc = self._draw_rc(self.c.scenarios[scn_id], rng)
            out.append({"at_piece": int(pieces[i]), "type": "fault", "scenario": scn_id, "root_cause": rc.id, "fired": False})
        if ev.get("changeover"):
            co = ev["changeover"]
            cscn = self.c.scenarios.get(co.get("scenario", ""))
            out.append({"at_piece": int(co.get("at_piece", 8)), "type": "changeover_order",
                        "scenario": co.get("scenario"), "to_variant": cscn.to_variant if cscn else "B",
                        "notice": (cscn.event or {}).get("notice", "班组长：下一件开始换型。") if cscn else "班组长：下一件开始换型。",
                        "fired": False})
        if ev.get("material_shortage"):
            ms = ev["material_shortage"]
            out.append({"at_piece": int(ms.get("at_piece", 10)), "type": "fault", "scenario": ms.get("scenario"),
                        "root_cause": ms.get("root_cause"), "fired": False})
        out.sort(key=lambda e: e["at_piece"])
        return out

    def _checklist_plan(self, scn: Scenario, fault_ids: list[str]) -> list[dict[str, Any]]:
        plan: list[dict[str, Any]] = []
        for it in scn.items:
            if it.sign_item:
                plan.append({"action": "sign_checklist", "params": {}})
                continue
            for st in it.check:
                plan.append({"action": st.action, "params": dict(st.params)})
            faulty = [f for f in it.faults if f.id in fault_ids]
            for f in faulty:
                for st in f.fix_path:
                    plan.append({"action": st.action, "params": dict(st.params)})
            plan.append({"action": "record_check", "params": {"item": it.id, "result": "ng" if faulty else "ok"}})
        for st in scn.finish_path:
            plan.append({"action": st.action, "params": dict(st.params)})
        return plan

    # ==================================================================
    # 公共入口：执行动作
    # ==================================================================
    def act(
        self,
        s: SessionState,
        action: str,
        params: dict[str, Any] | None = None,
        actor: str = "trainee",
        source: str = "click",
    ) -> ActResult:
        params = dict(params or {})
        ctx = _Ctx(action, params, actor, source)
        before = s.state
        if s.state in TERMINAL:
            return self._finish(s, ctx, before, rejected=True, msg="本局已经结束。")
        adef = self.c.actions.get(action)
        if adef is None:
            return self._finish(s, ctx, before, rejected=True, msg=f"没有这个动作：{action}")
        err = self._check_params(adef, params)
        if err:
            return self._finish(s, ctx, before, rejected=True, msg=err)

        cat = adef["category"]
        role_ok = actor != "trainee" or self.c.role_can("operator", action)
        # 1) 状态是否允许（越权动作在进行中的状态都“允许尝试”，以便被权限规则拒绝并记录）
        if not self._state_allows(s.state, action, cat):
            label = self.c.session_states[s.state]["label"]
            return self._finish(s, ctx, before, rejected=True, msg=f"当前状态（{label}）不能执行“{adef['name']}”。")
        # 2) 引擎前置条件（物理上做不了的动作直接拒绝，不判规则）
        pre = self._precondition(s, action, params)
        if pre:
            return self._finish(s, ctx, before, rejected=True, msg=pre)
        # 3) 动作前规则（权限、安全、处置、质量）
        outcome = self._rules_on_action(s, ctx, adef)
        if outcome == "terminate":
            self._terminate(s, ctx)
            return self._finish(s, ctx, before)
        if outcome == "reject":
            msgs = "；".join(v.message for v in ctx.violations if v.outcome == "reject")
            return self._finish(s, ctx, before, rejected=True, msg=msgs or "该动作被拒绝。")
        if not role_ok:
            return self._finish(s, ctx, before, rejected=True, msg=f"越权：“{adef['name']}”不在操作工权限内。")
        # 4) 执行
        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            return self._finish(s, ctx, before, rejected=True, msg=f"引擎未实现动作：{action}")
        self._tick(s, float(adef.get("duration_s", 0)))
        handler(s, ctx)
        if s.state == "safety_terminated":
            self._terminate(s, ctx)
            return self._finish(s, ctx, before)
        # 5) 训练局状态流转与进度
        if not ctx.state_set:
            self._transition_by_category(s, cat)
        self._track(s, ctx, adef)
        self._check_objective(s, ctx)
        return self._finish(s, ctx, before)

    def advance_time(self, s: SessionState, seconds: float, reason: str = "思考") -> None:
        """在线游玩时由接口层调用：把两步之间的真实思考时间（有上限）计入时钟。回放与评测不调用。"""
        if s.state in TERMINAL or seconds <= 0:
            return
        self._tick(s, seconds)

    def end_session(self, s: SessionState, reason: str = "学员结束本局") -> None:
        if s.state in TERMINAL:
            return
        s.fail_reasons.append(reason)
        self._log_system(s, "end", reason)
        self.finalize(s)

    # ==================================================================
    # 规则
    # ==================================================================
    def _rule_matches_action(self, rule: dict[str, Any], adef: dict[str, Any]) -> bool:
        trig = rule.get("trigger") or {}
        if trig.get("kind") != "action":
            return False
        if "actions" in trig and adef["id"] in trig["actions"]:
            return True
        if "zone" in trig and adef.get("zone") == trig["zone"]:
            return True
        if "categories" in trig and adef.get("category") in trig["categories"]:
            return True
        return False

    def _rules_on_action(self, s: SessionState, ctx: _Ctx, adef: dict[str, Any]) -> str | None:
        facts = self.facts(s, ctx)
        worst: str | None = None
        for rule in self.c.rules:
            if not self._rule_matches_action(rule, adef):
                continue
            if not evaluate(rule.get("when"), facts):
                continue
            if not self._record_violation(s, ctx, rule):
                if rule["outcome"] in ("reject", "terminate"):
                    worst = worst or rule["outcome"]
                continue
            out = rule["outcome"]
            if out == "terminate":
                worst = "terminate"
            elif out == "reject" and worst != "terminate":
                worst = "reject"
        return worst

    def _emit(self, s: SessionState, ctx: _Ctx, event: str, extra: dict[str, Any]) -> None:
        ctx.events.append({"event": event, **{k: v for k, v in extra.items() if not isinstance(v, BaseModel)}})
        facts = self.facts(s, ctx, extra)
        for rule in self.c.rules:
            trig = rule.get("trigger") or {}
            if trig.get("kind") != "event" or trig.get("event") != event:
                continue
            if evaluate(rule.get("when"), facts):
                self._record_violation(s, ctx, rule)
                if rule["outcome"] == "terminate":
                    s.state = "safety_terminated"

    def _record_violation(self, s: SessionState, ctx: _Ctx, rule: dict[str, Any]) -> bool:
        ep = self._episode(s)
        once = rule.get("once", "every")
        if once == "episode" and ep is not None:
            if rule["id"] in ep.once_rules:
                return False
            ep.once_rules.append(rule["id"])
        if once == "session" and any(v.rule_id == rule["id"] for v in s.violations):
            return False
        sev_text = {"veto": "一票否决", "major": "严重", "minor": "一般"}[rule["severity"]]
        msg = f"{rule['type']}规则 {rule['id']}（{sev_text}）：{rule['quote']}"
        v = Violation(
            rule_id=rule["id"], t=round(s.clock, 1), seq=s.seq + 1, action=ctx.action,
            episode=ep.id if ep else None, outcome=rule["outcome"], severity=rule["severity"],
            dimension=rule["dimension"], deduction=float(rule.get("deduction", 0)), tag=rule["tag"],
            type=rule["type"], message=msg,
        )
        s.violations.append(v)
        ctx.violations.append(v)
        if rule["outcome"] == "fail":
            if rule["id"] == "R-QLT-02":
                s.ng_escaped += 1
                ctx.messages.append("后果：这件 NG 流到了下游，OP50 终检（EOL）发现不合格并整批追溯。")
            s.failed = True
        return True

    def _terminate(self, s: SessionState, ctx: _Ctx) -> None:
        s.state = "safety_terminated"
        vet = [v for v in ctx.violations if v.outcome == "terminate"]
        s.terminated_by = vet[0].rule_id if vet else "safety"
        s.failed = True
        ctx.messages.append(f"安全终止：{vet[0].message if vet else '触发安全规则'}。本局判定失败，进入复盘。")

    # ==================================================================
    # 事实
    # ==================================================================
    def pressure(self, s: SessionState) -> float:
        vals = []
        for key, val in self.c.air.get("effects", {}).items():
            comp, st = key.split(".")
            if s.components.get(comp) == st:
                vals.append(float(val))
        if vals:
            return min(vals)
        if s.pressure_override is not None:
            return s.pressure_override
        return float(self.c.air["normal"])

    def pressure_zone(self, p: float) -> str:
        if p < float(self.c.air["red_below"]):
            return "红区"
        if p < float(self.c.air["green_min"]):
            return "黄区"
        return "绿区"

    def next_variant(self, s: SessionState) -> str:
        if s.components.get("feed_material") == "wrong_variant":
            return self.c.other_variant(s.order_variant)
        return s.order_variant

    def _episode(self, s: SessionState) -> Episode | None:
        if s.current_episode is None:
            return None
        return s.episodes[s.current_episode - 1]

    def facts(self, s: SessionState, ctx: _Ctx | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        f: dict[str, Any] = {}
        f["mode"] = s.mode
        f["safe_mode"] = bool(self.c.modes[s.mode]["safe"])
        f["alarm"] = s.alarm
        f["state"] = s.state
        ep = self._episode(s)
        f["in_abnormal"] = ep is not None and ep.ended_at is None
        f["recurrence"] = ep.recurrence if ep else 0
        f["inspected_any"] = ep.inspected_any if ep else False
        f["alarm_viewed"] = ep.alarm_viewed if ep else False
        f["hand_valve_inspected"] = ep.hand_valve_inspected if ep else False
        f["andon_pulled"] = ep.andon_pulled if ep else False
        f["safety_checked"] = s.safety_checked
        f.update(s.components)
        if s.current_piece is not None:
            f.update(s.current_piece.defects)
        p = self.pressure(s)
        f["pressure"] = p
        f["pressure_low"] = p < float(self.c.air["green_min"])
        f["pressure_red"] = p < float(self.c.air["red_below"])
        f["clamp_pressure_low"] = p < float(self.c.air["green_min"])
        pv = s.current_piece.variant if s.current_piece is not None else self.next_variant(s)
        f["piece_variant"] = pv
        f["program_mismatch"] = s.hmi.get("program") != self.c.variants[pv]["program"]
        f["bolt_variant_mismatch"] = s.bolt_box_variant != pv
        f["block_mismatch"] = s.components.get("locating_block") != pv
        f["hmi_variant_mismatch"] = s.hmi.get("variant") != pv
        f["bolt_box_isolated"] = s.bolt_box_isolated
        f["first_piece_pending"] = s.first_piece_pending
        co = s.changeover
        f["changeover_active"] = bool(co.get("active"))
        f["changeover_verifying"] = bool(co.get("verifying"))
        f["changeover_quality_pending"] = bool(co.get("quality_pending"))
        f["is_checklist"] = s.scenario_type == "checklist"
        if s.scenario_type == "checklist":
            items = s.checklist["items"]
            f["checklist_signed"] = bool(s.checklist["signed"])
            f["checklist_all_recorded"] = all(v["recorded"] is not None for v in items.values())
            f["checklist_unresolved"] = len(self._unresolved_faults(s))
        f["rc_in_scope"] = s.issue.in_scope if (s.issue is not None and s.issue.kind in ("rc", "mismatch", "unknown", "self_estop")) else True
        if ctx is not None:
            for k, v in ctx.params.items():
                f[f"param_{k}"] = v
            if "variant" in ctx.params:
                f["param_variant_matches_order"] = ctx.params["variant"] == s.order_variant
            if ctx.action == "record_check":
                item = ctx.params.get("item")
                st = s.checklist["items"].get(item, {})
                f["item_checked"] = bool(st.get("checked"))
                f["item_had_fault"] = any(fid in s.checklist["faults"] for fid in self._item_fault_ids(s, item))
        if extra:
            f.update(extra)
        return f

    # ==================================================================
    # 前置条件与参数
    # ==================================================================
    def _check_params(self, adef: dict[str, Any], params: dict[str, Any]) -> str | None:
        for name, spec in (adef.get("params") or {}).items():
            if spec.get("required") and name not in params:
                return f"“{adef['name']}”缺少参数：{name}"
            if name not in params:
                continue
            v = params[name]
            t = spec.get("type")
            if t == "enum" and v not in spec.get("values", []):
                return f"“{adef['name']}”的参数 {name} 只能是 {spec.get('values')}"
            if t == "target" and v not in self.c.inspect_targets():
                return f"工位上找不到“{v}”。"
        return None

    def _state_allows(self, state: str, action: str, cat: str) -> bool:
        st = self.c.session_states.get(state, {})
        return action in st.get("allow", []) or cat in st.get("allow_categories", [])

    def _precondition(self, s: SessionState, action: str, p: dict[str, Any]) -> str | None:
        piece = s.current_piece
        estop = s.components.get("estop_button") == "pressed"
        if action in ("resume", "first_piece_check", "run_production"):
            if s.scenario_type == "tour" and action != "run_production":
                return "认识工位关卡不需要生产。"
            if estop:
                return "急停还按着，设备不能启动。"
            if s.alarm:
                return f"HMI 上还有报警（{s.alarm}），先处理并复位报警。"
            if action == "run_production" and s.state != "normal_production":
                return "现在不是正常生产状态，处理完后用“恢复生产”。"
            if s.support is not None:
                return "还在等支援到场。"
        if action == "reset_alarm":
            if not s.alarm:
                return "HMI 上没有需要复位的报警。"
            if estop:
                return "急停还按着，先确认安全、复位急停。"
        if action == "release_estop" and not estop:
            return "急停按钮本来就是弹起的。"
        if action == "rescan" and s.alarm != "T-103":
            return "现在没有扫码报警，不需要重新扫码。"
        if action in ("isolate_ng_part", "reposition_workpiece", "release_part") and piece is None:
            return "夹具上没有工件。"
        if action == "call_quality_confirm":
            co = s.changeover
            if not co.get("active") or not co.get("verified"):
                return "现在没有需要质量员确认的换型首件。"
            if co.get("quality_confirmed"):
                return "质量员已经确认过首件了。"
        if action in ("pull_andon", "call_material") and s.support is not None:
            return "已经在呼叫中，等对方到场。"
        if action == "andon_report":
            if s.support is None or s.support.kind != "andon" or s.support.reported:
                return "现在没有需要汇报的 Andon 电话。"
        if action == "wait":
            if s.support is None:
                return "现在没有在等谁。"
            if s.support.kind == "andon" and not s.support.reported:
                return "先在电话里把情况说清楚。"
        if action in ("test_estop", "test_light_curtain", "test_tower_light"):
            if s.mode not in ("stopped", "paused"):
                return "生产中不能做功能测试，先暂停设备。"
        if action in ("record_check", "sign_checklist") and s.scenario_type != "checklist":
            return "本关卡没有点检记录表。"
        if action == "record_check":
            item = p.get("item")
            if item not in s.checklist["items"]:
                return f"点检表上没有这一项：{item}"
        if action == "sign_checklist" and s.checklist.get("signed"):
            return "点检记录已经签过字了。"
        if action == "cycle_step" and s.scenario_type != "tour":
            return "只有认识工位关卡有工作循环演示。"
        if action == "cycle_step":
            scn = self.c.scenarios[s.scenario_id]
            if s.cycle_step_index >= len(scn.cycle_steps):
                return "工作循环已经看完了。"
        return None

    # ==================================================================
    # 时间、日志、收尾
    # ==================================================================
    def _tick(self, s: SessionState, seconds: float) -> None:
        if seconds <= 0:
            return
        s.clock = round(s.clock + seconds, 1)
        if s.downtime_since is not None:
            s.downtime = round(s.downtime + seconds, 1)

    def _start_downtime(self, s: SessionState) -> None:
        if s.downtime_since is None:
            s.downtime_since = s.clock

    def _end_downtime(self, s: SessionState) -> None:
        s.downtime_since = None

    def loss_yuan(self, s: SessionState) -> float:
        return round(s.downtime / 60.0 * self.loss_per_min, 0)

    def _say(self, s: SessionState, who: str, text: str) -> None:
        s.messages.append({"t": round(s.clock, 1), "from": who, "text": text})

    def _log_system(self, s: SessionState, action: str, msg: str) -> None:
        s.seq += 1
        s.log.append(LogEntry(seq=s.seq, t=round(s.clock, 1), action=action, actor="system", source="system",
                              message=msg, state_before=s.state, state_after=s.state, downtime=s.downtime))

    def _finish(self, s: SessionState, ctx: _Ctx, before: str, rejected: bool = False, msg: str | None = None) -> ActResult:
        if msg:
            ctx.messages.append(msg)
        terminated = s.state == "safety_terminated"
        if terminated:
            self.finalize(s)
        s.seq += 1
        result = "rejected" if rejected else ("terminated" if terminated else ("violation" if ctx.violations else "ok"))
        entry = LogEntry(
            seq=s.seq, t=round(s.clock, 1), action=ctx.action, params=ctx.params, actor=ctx.actor,
            source=ctx.source, result=result, message="；".join(ctx.messages),
            violations=[v.rule_id for v in ctx.violations], state_before=before,
            state_after="safety_terminated" if terminated else s.state, episode=s.current_episode,
            observation=ctx.observation, downtime=s.downtime,
        )
        s.log.append(entry)
        # 本动作触发了“进入复盘”时，系统记录排在动作之后
        if len(s.log) >= 2 and s.log[-2].actor == "system" and s.log[-2].action == "review":
            sys_entry = s.log[-2]
            s.log[-2], s.log[-1] = entry, sys_entry
            entry.seq, sys_entry.seq = sys_entry.seq, entry.seq
        return ActResult(
            ok=not rejected and not terminated, rejected=rejected, action=ctx.action, params=ctx.params,
            message=entry.message, observation=ctx.observation, violations=ctx.violations, events=ctx.events,
            state=s.state, terminated=terminated, ended=s.state in TERMINAL, seq=s.seq,
        )

    def finalize(self, s: SessionState) -> None:
        """进入复盘：结束计时、判定目标与失败原因。分数由 scoring 模块计算。"""
        if s.state in ("review", "completed"):
            return
        self._end_downtime(s)
        if s.terminated_by:
            s.fail_reasons.append(f"安全终止（{s.terminated_by}）")
        if s.ng_escaped:
            s.fail_reasons.append(f"NG 流出 {s.ng_escaped} 件")
        if not s.objective_met and not s.terminated_by:
            s.fail_reasons.append("关卡目标未完成")
        if s.scenario_type == "checklist":
            missed = self._unresolved_faults(s)
            if missed:
                names = [self._fault(s, fid).name for fid in missed]
                s.fail_reasons.append("漏检或未处理：" + "、".join(names))
        if s.scenario_type == "shift" and s.standard.get("downtime"):
            factor = float((self.c.scenarios[s.scenario_id].pass_ or {}).get("max_downtime_factor", 1.6))
            limit = s.standard["downtime"] * factor
            if s.downtime > limit:
                s.fail_reasons.append(f"停线总时长 {s.downtime/60:.1f} 分钟，超过阈值 {limit/60:.1f} 分钟")
        s.state = "review"
        self._log_system(s, "review", "进入复盘")

    # ==================================================================
    # 状态流转与进度跟踪
    # ==================================================================
    def _transition_by_category(self, s: SessionState, cat: str) -> None:
        if s.state in TERMINAL:
            return
        if cat == "observe" and s.state == "abnormal":
            s.state = "investigating"
        elif cat in ("control", "handle", "quality", "changeover") and s.state in ("abnormal", "investigating"):
            s.state = "handling"

    def _track(self, s: SessionState, ctx: _Ctx, adef: dict[str, Any]) -> None:
        ep = self._episode(s)
        if ep is not None and ep.ended_at is None and adef["category"] not in ("forbidden",):
            if ctx.action not in ep.actions_done and ctx.action not in ("andon_report", "wait"):
                ep.actions_done.append(ctx.action)
        if s.scenario_type == "checklist":
            done = s.checklist.setdefault("actions_done", [])
            if ctx.action not in done and ctx.action not in ("andon_report", "wait", "record_check", "sign_checklist"):
                done.append(ctx.action)
        if ctx.success:
            self._mark_progress(s, ctx.action, ctx.params)

    def _step_matches(self, step: dict[str, Any], action: str, params: dict[str, Any]) -> bool:
        if step["action"] != action:
            return False
        for k, v in step.get("params", {}).items():
            if k == "elements":
                continue
            if params.get(k) != v:
                return False
        return True

    def _mark_progress(self, s: SessionState, action: str, params: dict[str, Any]) -> None:
        issue = s.issue
        if issue is None:
            return
        for i, st in enumerate(issue.path):
            if not issue.done[i] and self._step_matches(st, action, params):
                issue.done[i] = True
                break
        self._maybe_pop_issue(s)

    def _maybe_pop_issue(self, s: SessionState) -> None:
        issue = s.issue
        while issue is not None and issue.kind in ("mismatch",) and all(issue.done):
            parent = issue.parent
            s.issue_history.append({"id": issue.id, "kind": issue.kind, "name": issue.name, "t": s.clock})
            if parent is not None:
                for i, st in enumerate(parent.path):
                    if not parent.done[i] and st["action"] == issue.id.split(":")[-1]:
                        parent.done[i] = True
                        break
            s.issue = parent
            issue = parent

    def _close_issue(self, s: SessionState) -> None:
        if s.issue is not None and s.issue.kind in ("rc", "unknown", "self_estop", "mismatch"):
            s.issue_history.append({"id": s.issue.id, "kind": s.issue.kind, "name": s.issue.name, "t": s.clock})
            s.issue = s.issue.parent

    def _check_objective(self, s: SessionState, ctx: _Ctx) -> None:
        if s.state in TERMINAL:
            return
        if s.scenario_type == "tour":
            scn = self.c.scenarios[s.scenario_id]
            if len(s.codex) >= len(scn.codex) and s.cycle_step_index >= len(scn.cycle_steps):
                s.objective_met = True
                ctx.messages.append("图鉴全部点亮，工作循环也看完了。认识工位完成。")
                self.finalize(s)

    # ==================================================================
    # 工作循环
    # ==================================================================
    def _fire_events(self, s: SessionState, ctx: _Ctx) -> bool:
        """在上下一件之前触发到点的事件。返回 True 表示需要停下（换型指令）。"""
        nxt = s.piece_counter + 1
        stop = False
        for ev in s.schedule:
            if ev["fired"] or ev["at_piece"] != nxt:
                continue
            ev["fired"] = True
            if ev["type"] == "fault":
                scn, rc = self.c.rc_index[ev["root_cause"]]
                s.active_rc = rc.id
                self._apply_setup(s, rc.setup)
                if rc.surroundings:
                    s.surroundings = rc.surroundings
                ctx.events.append({"event": "fault_injected", "scenario": scn.id})
            elif ev["type"] == "changeover_order":
                co = s.changeover
                co.update({"active": True, "ordered": True, "to_variant": ev["to_variant"], "verifying": True,
                           "verified": False, "quality_pending": False, "quality_confirmed": False, "done": False})
                s.order_variant = ev["to_variant"]
                cscn = self.c.scenarios.get(ev.get("scenario") or "")
                prior = s.issue
                if prior is not None and prior.kind != "changeover" and s.current_episode is None:
                    # 上一个异常已在首件/恢复时消除，换型指令到来即视为该问题处理结束
                    s.issue_history.append({"id": prior.id, "kind": prior.kind, "name": prior.name, "t": s.clock})
                    prior = prior.parent
                if cscn is not None:
                    path = [st.as_step().model_dump() for st in cscn.steps]
                    s.issue = Issue(id=f"changeover:{cscn.id}", kind="changeover", name=cscn.title, path=path,
                                    done=[False] * len(path), strict_order=True, parent=prior)
                self._say(s, "team_leader", ev.get("notice", "班组长：下一件开始换型。"))
                ctx.messages.append(ev.get("notice", "班组长：下一件开始换型。"))
                ctx.events.append({"event": "changeover_order", "to_variant": ev["to_variant"]})
                stop = True
        return stop

    def _load_piece(self, s: SessionState) -> Piece:
        s.piece_counter += 1
        v = self.next_variant(s)
        p = Piece(no=s.piece_counter, barcode=f"EX{v}{s.piece_counter:05d}", variant=v)
        if s.pending_piece_defects:
            p.defects = dict(s.pending_piece_defects)
            s.pending_piece_defects = {}
        s.current_piece = p
        s.hmi.update({"torque_result": "—", "angle_result": "—", "curve": "none", "scanned_variant": None, "cycle_step": "上件"})
        return p

    def _release_piece(self, s: SessionState, ctx: _Ctx, p: Piece) -> None:
        p.location = "downstream"
        s.pieces.append(p)
        s.current_piece = None
        is_ng = p.is_ng or p.result is None or p.result != "ok"
        self._emit(s, ctx, "piece_released", {"piece_is_ng": is_ng, "piece_nonconforming": p.nonconforming, "piece_no": p.no})

    def _check_alarms(self, s: SessionState) -> str | None:
        f = self.facts(s)
        order = self.c.alarms_doc.get("stage_order", [])
        pre_order = self.c.alarms_doc.get("pre_order", [])
        for stage in order:
            codes = [a["code"] for a in self.c.alarms_doc["alarms"] if a["stage"] == stage]
            if stage == "pre":
                codes = sorted(codes, key=lambda c: pre_order.index(c) if c in pre_order else 99)
            for code in codes:
                if evaluate(self.c.alarms[code].get("trigger"), f):
                    return code
        return None

    def _cycle(self, s: SessionState, ctx: _Ctx, first_piece_mode: bool) -> str:
        """跑一个工作循环。返回 ok / alarm / event / terminated。"""
        p = s.current_piece
        if p is not None and (p.is_ng or p.result is not None):
            self._release_piece(s, ctx, p)
            p = None
        if p is None:
            if self._fire_events(s, ctx):
                return "event"
            p = self._load_piece(s)
        self._emit(s, ctx, "cycle_start", {"first_piece_mode": first_piece_mode})
        if s.state == "safety_terminated":
            return "terminated"
        if s.first_piece_pending and not first_piece_mode:
            p.confirmed = False
            s.unconfirmed_count += 1
        s.mode = "running"
        code = self._check_alarms(s)
        if code:
            self._tick(s, self.takt / 2)
            self._raise_alarm(s, ctx, code)
            return "alarm"
        p.result = "ok"
        p.scanned = True
        s.hmi.update({"torque_result": "OK", "angle_result": "OK", "curve": "ok", "scanned_variant": p.variant,
                      "cycle_step": "下件", "ok_count": s.hmi["ok_count"] + 1, "bit_count": s.hmi["bit_count"] + 1})
        if first_piece_mode:
            p.first_piece = True
            s.first_piece_pending = False
            s.first_piece_count += 1
        p.location = "downstream"
        s.pieces.append(p)
        s.current_piece = None
        s.produced_ok += 1
        self._tick(s, self.takt)
        return "ok"

    def _raise_alarm(self, s: SessionState, ctx: _Ctx, code: str, self_pressed: bool = False) -> None:
        a = self.c.alarms[code]
        p = s.current_piece
        ep = self._episode(s)
        new_episode = ep is None or ep.ended_at is not None
        if new_episode:
            ep = Episode(id=len(s.episodes) + 1, kind="alarm", alarm=code, started_at=s.clock)
            s.episodes.append(ep)
            s.current_episode = ep.id
        else:
            assert ep is not None
            if ep.alarm == code and ep.reset_since_alarm:
                ep.recurrence += 1
            elif ep.alarm != code:
                ep.alarm = code
                ep.recurrence = 0
        ep.reset_since_alarm = False
        s.recurrence[code] = ep.recurrence
        s.alarm = code
        s.mode = "estopped" if code == "T-110" else "alarm_stop"
        if code == "T-110":
            s.safety_checked = False
        stage_cn = {"pre": "循环前检查", "load": "上件", "scan": "扫码", "verify": "防错", "clamp": "夹紧", "tighten": "拧紧", "judge": "判定"}
        s.hmi.update({"alarm": code, "alarm_text": a["text"], "cycle_step": f"报警停机（{stage_cn.get(a['stage'], a['stage'])}）"})
        if p is not None:
            p.episode = ep.id
            if a.get("makes_ng"):
                res = a.get("tighten_result", "ng")
                p.result = res
                if not p.is_ng:
                    p.is_ng = True
                    ep.ng_count += 1
                    s.hmi["ng_count"] += 1
                s.hmi["curve"] = res
                if res == "torque_low":
                    s.hmi.update({"torque_result": "NG（低于下限）", "angle_result": "OK"})
                else:
                    s.hmi.update({"torque_result": "OK", "angle_result": "NG（超上限）"})
            if a.get("nonconforming") and not p.nonconforming:
                p.nonconforming = True
                ep.ng_count += 1
            if a["stage"] in ("verify", "clamp", "tighten"):
                s.hmi["scanned_variant"] = p.variant
                p.scanned = True
        self._start_downtime(s)
        keep = False
        if not new_episode and s.issue is not None and s.issue.kind == "rc" and s.issue.id in self.c.rc_index:
            keep = self._rc_holds(s, self.c.rc_index[s.issue.id][1])
        if not keep and (new_episode or s.issue is None or s.issue.kind in ("changeover", "checklist")
                         or (s.issue.alarm and s.issue.alarm != code)):
            self._infer_issue(s, code, self_pressed=self_pressed)
        self._say(s, "hmi", f"报警 {code} {a['text']}")
        ctx.messages.append(f"HMI 报警：{code} {a['text']}，三色灯红灯。")
        self._emit(s, ctx, "alarm_triggered", {"alarm_code": code})

    # ------------------------------------------------------------------
    def _rc_holds(self, s: SessionState, rc: RootCause) -> bool:
        f = self.facts(s)
        if rc.match:
            return evaluate(rc.match, f)
        conds = []
        for k, v in rc.setup.items():
            if k in self.c.components:
                conds.append({"fact": k, "eq": v})
        return bool(conds) and evaluate({"all": conds}, f)

    def _issue_from_rc(self, rc: RootCause, alarm: str | None) -> Issue:
        path = [st.model_dump() for st in rc.correct_path]
        return Issue(id=rc.id, kind="rc", name=rc.name, alarm=alarm, in_scope=rc.in_operator_scope,
                     andon_target=rc.andon_target, support=rc.support,
                     repair=rc.repair or {k: self.c.components[k]["default"] for k in rc.setup if k in self.c.components},
                     repair_first_piece=rc.repair_first_piece, path=path, done=[False] * len(path))

    def _infer_issue(self, s: SessionState, code: str, self_pressed: bool = False) -> None:
        parent = s.issue if (s.issue is not None and s.issue.kind == "changeover") else None
        f = self.facts(s)
        if self_pressed and code == "T-110":
            path = [{"action": "safety_check", "params": {}}, {"action": "release_estop", "params": {}},
                    {"action": "reset_alarm", "params": {}}, {"action": "resume", "params": {}}]
            s.issue = Issue(id="self_estop", kind="self_estop", name="学员按下急停", alarm=code, in_scope=True,
                            path=path, done=[False] * len(path), parent=parent)
            return
        # 1) 当前注入的根因
        if s.active_rc and s.active_rc in self.c.rc_index:
            scn, rc = self.c.rc_index[s.active_rc]
            if scn.alarm == code and self._rc_holds(s, rc):
                issue = self._issue_from_rc(rc, code)
                issue.parent = parent
                s.issue = issue
                return
        # 2) 配置不匹配（换型漏步等）
        for mm in self.c.mismatch_catalog:
            if mm.get("alarm") == code and f.get(mm["fact"]):
                st: Any = mm["step"]
                step = st.as_step()
                step.params = {k: (s.order_variant if k == "variant" else self.c.variants[s.order_variant]["program"] if k == "program" else v)
                               for k, v in step.params.items()}
                if mm.get("in_operator_scope", True):
                    path = []
                    if not self.c.modes[s.mode]["safe"]:
                        path.append({"action": "pause_equipment", "params": {}})
                    path += [step.model_dump(), {"action": "reset_alarm", "params": {}}]
                    if parent is None:
                        path += [{"action": "first_piece_check", "params": {}}, {"action": "resume", "params": {}}]
                    issue = Issue(id=f"mismatch:{step.action}", kind="mismatch", name=f"漏做：{st.name}", alarm=code,
                                  in_scope=True, path=path, done=[False] * len(path), parent=parent,
                                  repair={}, repair_first_piece=True)
                else:
                    target = mm.get("andon_target", "team_leader")
                    path = [{"action": "pause_equipment", "params": {}}, {"action": "isolate_ng_part", "params": {}},
                            {"action": "pull_andon", "params": {"target": target}},
                            {"action": "andon_report", "params": {"elements": "all"}}, {"action": "wait", "params": {}}]
                    if parent is None:
                        path += [{"action": "first_piece_check", "params": {}}, {"action": "resume", "params": {}}]
                    repair = {k: (s.order_variant if v in ("B", "A") and k == "hmi.variant" else v) for k, v in (mm.get("repair") or {}).items()}
                    issue = Issue(id=f"mismatch:{step.action}", kind="mismatch", name=f"漏做：{st.name}", alarm=code,
                                  in_scope=False, andon_target=target, path=path, done=[False] * len(path), parent=parent,
                                  repair=repair, repair_first_piece=bool(mm.get("repair_first_piece", True)))
                s.issue = issue
                return
        # 3) 任一已知根因
        for rc_id, (scn, rc) in self.c.rc_index.items():
            if scn.alarm == code and self._rc_holds(s, rc):
                issue = self._issue_from_rc(rc, code)
                issue.parent = parent
                s.issue = issue
                return
        # 4) 学员自己拍的急停
        if code == "T-110":
            path = [{"action": "safety_check", "params": {}}, {"action": "release_estop", "params": {}},
                    {"action": "reset_alarm", "params": {}}, {"action": "resume", "params": {}}]
            s.issue = Issue(id="self_estop", kind="self_estop", name="急停被按下", alarm=code, in_scope=True,
                            path=path, done=[False] * len(path), parent=parent)
            return
        # 5) 未知：叫维修
        path = [{"action": "pull_andon", "params": {"target": "maintenance"}}, {"action": "andon_report", "params": {"elements": "all"}},
                {"action": "wait", "params": {}}, {"action": "first_piece_check", "params": {}}, {"action": "resume", "params": {}}]
        s.issue = Issue(id=f"unknown:{code}", kind="unknown", name="未识别的异常", alarm=code, in_scope=False,
                        andon_target="maintenance", path=path, done=[False] * len(path), parent=parent)

    # ------------------------------------------------------------------
    def _resolve_special(self, s: SessionState, value: Any) -> Any:
        if value == "@other_program":
            return self.c.variants[self.c.other_variant(s.order_variant)]["program"]
        if value == "@order_program":
            return self.c.variants[s.order_variant]["program"]
        if value == "@order_variant":
            return s.order_variant
        return value

    def _state_scope(self, comp: str, state: str) -> str:
        cdef = self.c.components[comp]
        st = cdef["states"].get(state, {})
        return st.get("scope", cdef.get("scope", "station"))

    def _apply_setup(self, s: SessionState, setup: dict[str, Any]) -> None:
        for k, v in setup.items():
            v = self._resolve_special(s, v)
            if k.startswith("hmi."):
                s.hmi[k.split(".", 1)[1]] = v
            elif k == "order_variant":
                s.order_variant = v
            elif k == "bolt_box_variant":
                s.bolt_box_variant = v
            elif k == "bit_count":
                s.hmi["bit_count"] = int(v)
            elif k in self.c.components:
                if self._state_scope(k, str(v)) == "piece":
                    if s.current_piece is not None:
                        s.current_piece.defects[k] = str(v)
                    else:
                        s.pending_piece_defects[k] = str(v)
                else:
                    s.components[k] = str(v)
                if k == "estop_button" and v == "pressed":
                    s.safety_checked = False
                if k == "bit" and v == "worn":
                    s.hmi["bit_count"] = int(self.c.bit_life["worn_count"])
            else:
                raise EngineError(f"未知的初始状态键：{k}")

    def _apply_repair(self, s: SessionState, repair: dict[str, Any]) -> list[str]:
        names = []
        for k, v in repair.items():
            v = self._resolve_special(s, v)
            if k.startswith("hmi."):
                s.hmi[k.split(".", 1)[1]] = v
                names.append("HMI " + {"variant": "型号", "program": "程序号"}.get(k.split(".", 1)[1], k))
            elif k in self.c.components:
                if s.current_piece is not None and k in s.current_piece.defects:
                    s.current_piece.defects.pop(k, None)
                else:
                    s.components[k] = str(v)
                if k == "bolt_box":
                    s.bolt_box_isolated = False
                names.append(self.c.components[k]["name"])
        return names

    # ==================================================================
    # 动作处理器
    # ==================================================================
    def _do_inspect(self, s: SessionState, ctx: _Ctx) -> None:
        target = ctx.params["target"]
        obs = self.observe(s, target)
        ctx.observation = obs
        ctx.messages.append(obs["text"])
        ep = self._episode(s)
        if ep is not None and ep.ended_at is None:
            ep.inspected_any = True
            if target == "hmi":
                ep.alarm_viewed = True
            if target == "hand_valve":
                ep.hand_valve_inspected = True
        if s.scenario_type == "tour":
            scn = self.c.scenarios[s.scenario_id]
            for e in scn.codex:
                if target in e.targets and e.id not in s.codex:
                    s.codex.append(e.id)
                    ctx.messages.append(f"图鉴点亮：{e.name}（{len(s.codex)}/{len(scn.codex)}）")
                    ctx.observation["codex"] = {"id": e.id, "name": e.name, "desc": e.desc}
                    # 进度：图鉴条目按 codex 顺序计划，查看任一目标都算完成该条目
                    if s.issue is not None:
                        for i, st in enumerate(s.issue.path):
                            if not s.issue.done[i] and st["action"] == "inspect" and st["params"].get("target") in e.targets:
                                s.issue.done[i] = True
                                break
        self._mark_checklist(s, "inspect", ctx.params)

    def observe(self, s: SessionState, target: str) -> dict[str, Any]:
        """查看一个目标，返回引擎事实（主持人只能复述这里的内容）。"""
        c = self.c
        out: dict[str, Any] = {"target": target, "name": c.target_name(target), "closeup": None}
        p = self.pressure(s)
        fmt = {
            "pressure": f"{p:.2f}",
            "pressure_zone": self.pressure_zone(p),
            "bolt_variant_name": c.variants[s.bolt_box_variant]["name"],
            "bolt_color": c.variants[s.bolt_box_variant]["color"],
            "ng_bin_count": s.ng_bin_count,
            "first_piece_count": s.first_piece_count,
            "order_variant_name": c.variants[s.order_variant]["name"],
            "other_variant_name": c.variants[c.other_variant(s.order_variant)]["name"],
        }
        if target in c.components:
            cdef = c.components[target]
            if cdef.get("hidden") and target in TESTS_FOR_HIDDEN:
                test = c.actions[TESTS_FOR_HIDDEN[target]]["name"]
                out["text"] = f"{cdef['name']}光看看不出来，需要做一次“{test}”。"
                return out
            if cdef.get("scope") == "piece" or target in ("label", "bolt_seat"):
                piece = s.current_piece
                if piece is None:
                    out["text"] = f"夹具上没有工件，看不了{cdef['name']}。"
                    return out
                state = piece.defects.get(target, cdef["default"])
                if target == "bolt_seat" and piece.result is None:
                    out["text"] = "这一件还没开始拧紧。"
                    return out
            elif target == "fixture_seat" and s.current_piece is not None and s.current_piece.defects.get("fixture_seat"):
                state = s.current_piece.defects["fixture_seat"]
            elif target == "pressure_gauge":
                state = "low" if p < float(c.air["green_min"]) else "normal"
            else:
                state = s.components.get(target, cdef["default"])
            sdef = cdef["states"].get(state) or cdef["states"][cdef["default"]]
            text = sdef["text"]
            if not sdef.get("abnormal") and target in s.distractors and target != "pressure_gauge":
                text = s.distractors[target]
            if target == "pressure_gauge" and state == "normal" and "pressure_gauge" in s.distractors and s.pressure_override is not None:
                text = s.distractors["pressure_gauge"]
            out["text"] = text.format(**fmt)
            out["closeup"] = f"closeup/{sdef['closeup']}" if sdef.get("closeup") else None
            if target == "pressure_gauge":
                out["values"] = {"pressure": round(p, 2), "zone": self.pressure_zone(p)}
            return out
        if target == "hmi":
            h = s.hmi
            vname = c.variants[h["variant"]]["name"]
            parts = []
            parts.append(f"报警 {h['alarm']} {h['alarm_text']}" if h.get("alarm") else "无报警")
            parts.append(f"拧紧结果 扭矩 {h['torque_result']}、角度 {h['angle_result']}")
            parts.append(f"当前型号 {vname}，程序号 {h['program']}")
            if h.get("scanned_variant"):
                parts.append(f"扫码型号 {c.variants[h['scanned_variant']]['name']}")
            parts.append(f"OK {h['ok_count']} 件 / NG {h['ng_count']} 件")
            out["text"] = "HMI：" + "；".join(parts) + "。"
            out["values"] = dict(h)
            return out
        if target == "tighten_curve":
            out["text"] = c.indicators["tighten_curve"]["values"].get(s.hmi.get("curve", "none"), "无数据")
            return out
        if target == "bit_counter":
            if s.components.get("bit") != "worn" and "bit_counter" in s.distractors:
                out["text"] = s.distractors["bit_counter"]
            else:
                out["text"] = c.indicators["bit_counter"]["text"].format(bit_count=s.hmi["bit_count"], bit_limit=c.bit_life["limit"])
            return out
        if target == "tower_light":
            color = self.tower_color(s)
            out["text"] = "三色灯：" + c.indicators["tower_light"]["values"][color] + "。"
            out["values"] = {"color": color}
            out["closeup"] = "closeup/tower_light.webp"
            return out
        if target == "curtain_status":
            out["text"] = "光栅状态：" + ("光栅中断" if s.alarm == "T-105" else "光栅正常") + "。"
            return out
        if target == "estop_status":
            out["text"] = "急停状态：" + ("急停按下" if s.components.get("estop_button") == "pressed" else "急停已复位") + "。"
            return out
        if target == "workpiece":
            piece = s.current_piece
            if piece is None:
                out["text"] = "夹具上现在没有工件。"
                return out
            res = {"ok": "合格", "torque_low": "NG（扭矩低）", "angle_out": "NG（角度超差）", None: "还没拧紧"}[piece.result]
            out["text"] = f"当前工件条码 {piece.barcode}，型号 {c.variants[piece.variant]['name']}，拧紧结果：{res}，在夹具上。"
            out["values"] = {"barcode": piece.barcode, "variant": piece.variant, "result": piece.result}
            return out
        if target == "surroundings":
            out["text"] = self._surroundings_text(s)
            return out
        if target in c.equipment:
            e = c.equipment[target]
            out["text"] = s.distractors.get(target) or e.get("text", e["name"])
            out["closeup"] = f"closeup/{e['closeup']}" if e.get("closeup") else None
            return out
        out["text"] = f"看不清“{target}”。"
        return out

    def _surroundings_text(self, s: SessionState) -> str:
        if s.surroundings:
            return s.surroundings
        if s.components.get("gun_motor") == "abnormal":
            return "周边没有人在危险区。拧紧枪附近能闻到焦糊味。"
        return "周边没有人在危险区，设备外观无异常，也没有异味。"

    def tower_color(self, s: SessionState) -> str:
        if s.alarm or s.mode == "estopped":
            return "red"
        if s.mode == "running":
            return "green"
        if s.mode == "stopped":
            return "off"
        return "yellow"

    def _do_safety_check(self, s: SessionState, ctx: _Ctx) -> None:
        s.safety_checked = True
        text = self._surroundings_text(s)
        ctx.observation = {"target": "surroundings", "name": "工位周边", "text": text, "closeup": None}
        ctx.messages.append(text)
        ep = self._episode(s)
        if ep is not None:
            ep.inspected_any = True

    def _do_run_production(self, s: SessionState, ctx: _Ctx) -> None:
        produced = 0
        for _ in range(60):
            if s.scenario_type == "shift" and s.target_pieces and s.produced_ok >= s.target_pieces:
                break
            if s.scenario_type == "tour" and produced >= 1:
                break
            r = self._cycle(s, ctx, first_piece_mode=False)
            if r != "ok":
                if produced:
                    ctx.messages.insert(0, f"按节拍生产了 {produced} 件合格品。")
                if r == "alarm":
                    s.state = "abnormal"
                    ctx.state_set = True
                return
            produced += 1
            self._end_downtime(s)
            if s.scenario_type in ("alarm", "changeover") and all(e["fired"] for e in s.schedule) and s.issue is None:
                break
        ctx.messages.append(f"按节拍生产了 {produced} 件，全部合格。")
        if s.scenario_type == "shift" and s.target_pieces and s.produced_ok >= s.target_pieces:
            self._end_shift(s, ctx)

    def _end_shift(self, s: SessionState, ctx: _Ctx) -> None:
        unfired = [e for e in s.schedule if not e["fired"]]
        s.shift_ended = True
        s.objective_met = not unfired and s.issue is None and not s.changeover.get("active")
        ctx.messages.append(f"班次结束：本班生产合格 {s.produced_ok} 件，停线 {s.downtime/60:.1f} 分钟。")
        self.finalize(s)
        ctx.state_set = True

    def _do_pause_equipment(self, s: SessionState, ctx: _Ctx) -> None:
        if s.mode in ("paused", "estopped", "stopped"):
            ctx.messages.append("设备已经处于安全停止状态。")
            return
        s.mode = "paused"
        self._start_downtime(s)
        ctx.messages.append("设备已暂停，三色灯黄灯。")

    def _do_emergency_stop(self, s: SessionState, ctx: _Ctx) -> None:
        s.components["estop_button"] = "pressed"
        s.safety_checked = False
        s.mode = "estopped"
        if not s.alarm:
            self._raise_alarm(s, ctx, "T-110", self_pressed=True)
        else:
            self._start_downtime(s)
        ctx.messages.append("急停已按下，设备停止。")

    def _do_release_estop(self, s: SessionState, ctx: _Ctx) -> None:
        s.components["estop_button"] = "released"
        s.mode = "alarm_stop" if s.alarm else "ready"
        ctx.messages.append("急停已复位。" + ("HMI 上的报警还需要复位。" if s.alarm else ""))

    def _do_reset_alarm(self, s: SessionState, ctx: _Ctx) -> None:
        code = s.alarm
        s.alarm = None
        s.hmi.update({"alarm": None, "alarm_text": None, "cycle_step": "待机"})
        if s.mode == "alarm_stop":
            s.mode = "ready"
        ep = self._episode(s)
        if ep is not None:
            ep.reset_since_alarm = True
        ctx.messages.append(f"报警 {code} 已复位，三色灯黄灯。")

    def _after_success_cycle(self, s: SessionState, ctx: _Ctx) -> None:
        ep = self._episode(s)
        if ep is not None and ep.ended_at is None:
            ep.ended_at = s.clock
            s.current_episode = None

    def _do_first_piece_check(self, s: SessionState, ctx: _Ctx) -> None:
        r = self._cycle(s, ctx, first_piece_mode=True)
        ctx.state_set = True
        if r == "terminated":
            return
        if r == "event":
            ctx.success = False
            s.state = "normal_production"
            return
        if r == "alarm":
            ctx.success = False
            s.state = "investigating" if not self._is_new_episode_alarm(s) else "abnormal"
            ctx.messages.append("首件不合格，回到排查。")
            return
        self._after_success_cycle(s, ctx)
        co = s.changeover
        if co.get("active") and co.get("verifying"):
            co.update({"verifying": False, "verified": True, "quality_pending": True})
        s.state = "recovery_check"
        ctx.messages.append("首件合格：扭矩、角度均在窗口内，已记入首件确认记录。")

    def _is_new_episode_alarm(self, s: SessionState) -> bool:
        ep = self._episode(s)
        return ep is not None and ep.recurrence == 0 and len(ep.actions_done) == 0

    def _do_resume(self, s: SessionState, ctx: _Ctx) -> None:
        r = self._cycle(s, ctx, first_piece_mode=False)
        ctx.state_set = True
        if r == "terminated":
            return
        if r == "event":
            ctx.success = False
            self._end_downtime(s)
            s.state = "normal_production"
            return
        if r == "alarm":
            ctx.success = False
            s.state = "abnormal" if self._is_new_episode_alarm(s) else "investigating"
            return
        self._after_success_cycle(s, ctx)
        self._end_downtime(s)
        co = s.changeover
        if co.get("active"):
            # 换型后第一次恢复批量生产即视为换型结束；漏首件（R-HDL-07）、漏质量确认（R-QLT-01）已在前面判定
            co.update({"active": False, "done": True, "verifying": False, "quality_pending": False})
        if s.issue is not None and s.issue.kind == "changeover" and co.get("done"):
            s.issue.done = [True] * len(s.issue.path)
            s.issue_history.append({"id": s.issue.id, "kind": "changeover", "name": s.issue.name, "t": s.clock})
            s.issue = s.issue.parent
        else:
            self._mark_progress(s, "resume", {})
            self._close_issue(s)
            ctx.success = False  # 已在此处标记进度，避免重复
        s.state = "normal_production"
        ctx.messages.append("设备恢复生产，这一件合格，三色灯绿灯。")
        t = s.scenario_type
        if t == "alarm":
            fired = all(e["fired"] for e in s.schedule)
            if fired and s.current_episode is None and s.issue is None:
                s.objective_met = True
                self.finalize(s)
        elif t == "changeover":
            if co.get("done"):
                s.objective_met = True
                self.finalize(s)
        elif t == "checklist":
            if s.checklist.get("signed") and not self._unresolved_faults(s) and s.first_piece_count >= 1:
                s.objective_met = True
            self.finalize(s)
        elif t == "shift":
            if s.target_pieces and s.produced_ok >= s.target_pieces:
                self._end_shift(s, ctx)

    def _do_replace_bit(self, s: SessionState, ctx: _Ctx) -> None:
        s.components["bit"] = "normal"
        s.hmi["bit_count"] = 0
        s.first_piece_pending = True
        ctx.messages.append("已从备件盒取新批头换上，批头计数清零。")
        self._mark_checklist_fix(s)

    def _do_tighten_socket(self, s: SessionState, ctx: _Ctx) -> None:
        was = s.components["socket"]
        s.components["socket"] = "normal"
        s.first_piece_pending = True
        ctx.messages.append("套筒已重新装到位，定位销卡紧。" if was == "loose" else "检查了一遍，套筒本来就装得很紧。")

    def _do_clean_fixture(self, s: SessionState, ctx: _Ctx) -> None:
        was = s.components["fixture_seat"]
        s.components["fixture_seat"] = "normal"
        if s.current_piece is not None:
            s.current_piece.defects.pop("fixture_seat", None)
        ctx.messages.append("已取出工件吹净定位面碎屑，工件重新放好。" if was == "debris" else "定位面本来就是干净的，工件重新放好了。")

    def _do_reposition_workpiece(self, s: SessionState, ctx: _Ctx) -> None:
        p = s.current_piece
        assert p is not None
        was = p.defects.pop("fixture_seat", None)
        ctx.messages.append("工件已重新放到定位销上，贴合到位。" if was == "misplaced" else "工件重新放了一遍，位置和之前一样。")

    def _do_clean_scanner_window(self, s: SessionState, ctx: _Ctx) -> None:
        was = s.components["scan_window"]
        s.components["scan_window"] = "clean"
        ctx.messages.append("扫码枪窗口已擦干净。" if was == "dirty" else "扫码枪窗口本来就是干净的。")

    def _do_rescan(self, s: SessionState, ctx: _Ctx) -> None:
        ep = self._episode(s)
        if ep is not None:
            ep.reset_since_alarm = True
        f = self.facts(s)
        if evaluate(self.c.alarms["T-103"]["trigger"], f):
            ctx.success = False
            self._raise_alarm(s, ctx, "T-103")
            s.state = "investigating"
            ctx.state_set = True
            ctx.messages.append("重新扫码，仍然读不出来。")
            return
        s.alarm = None
        s.hmi.update({"alarm": None, "alarm_text": None, "scanned_variant": s.current_piece.variant if s.current_piece else None,
                      "cycle_step": "扫码完成"})
        if s.current_piece is not None:
            s.current_piece.scanned = True
        s.mode = "ready"
        s.state = "recovery_check"
        ctx.state_set = True
        ctx.messages.append("扫码成功，报警已清除。")

    def _do_clean_curtain_mirror(self, s: SessionState, ctx: _Ctx) -> None:
        was = s.components["curtain_mirror"]
        if was == "dirty":
            s.components["curtain_mirror"] = "clean"
            ctx.messages.append("光栅两端镜面已擦干净，对位指示灯恢复绿色。")
        elif was == "misaligned":
            ctx.messages.append("镜面擦干净了，但两端对位指示灯仍然是红色。")
        else:
            ctx.messages.append("光栅镜面本来就是干净的。")

    def _do_remove_obstruction(self, s: SessionState, ctx: _Ctx) -> None:
        was = s.components["curtain_zone"]
        s.components["curtain_zone"] = "clear"
        ctx.messages.append("已把挡在光栅前的周转盒拿走。" if was == "blocked" else "光栅区域里没有遮挡物。")

    def _do_open_hand_valve(self, s: SessionState, ctx: _Ctx) -> None:
        was = s.components["hand_valve"]
        s.components["hand_valve"] = "open"
        p = self.pressure(s)
        ctx.messages.append(("手阀已全开。" if was == "half" else "手阀本来就是全开的。") + f"压力表读数 {p:.2f} MPa（{self.pressure_zone(p)}）。")
        self._mark_checklist_fix(s)

    def _do_isolate_ng_part(self, s: SessionState, ctx: _Ctx) -> None:
        p = s.current_piece
        assert p is not None
        p.location = "ng_bin"
        p.labeled = True
        s.pieces.append(p)
        s.current_piece = None
        s.ng_bin_count += 1
        if p.is_ng or p.nonconforming:
            ctx.messages.append(f"工件 {p.barcode} 已放入红色 NG 料箱并贴好 NG 标签。")
        else:
            ctx.messages.append(f"工件 {p.barcode} 作为可疑件放入 NG 料箱并贴了标签（这一件本身没有 NG 结果）。")

    def _do_isolate_bolt_box(self, s: SessionState, ctx: _Ctx) -> None:
        s.bolt_box_isolated = True
        ctx.messages.append("螺栓料盒已移到待判定区，挂上“待判定”牌。工位上暂时没有螺栓可用。")

    def _do_tidy_workstation(self, s: SessionState, ctx: _Ctx) -> None:
        ctx.messages.append("工位物品已归位。")

    def _do_release_part(self, s: SessionState, ctx: _Ctx) -> None:
        p = s.current_piece
        assert p is not None
        self._release_piece(s, ctx, p)
        ctx.messages.append(f"工件 {p.barcode} 已放行到下游。")

    def _do_hmi_select_variant(self, s: SessionState, ctx: _Ctx) -> None:
        v = ctx.params["variant"]
        s.hmi["variant"] = v
        ctx.messages.append(f"HMI 当前型号已切换为 {self.c.variants[v]['name']} 并确认。")

    def _do_hmi_select_program(self, s: SessionState, ctx: _Ctx) -> None:
        s.hmi["program"] = ctx.params["program"]
        ctx.messages.append(f"拧紧程序号已切换为 {ctx.params['program']}。")

    def _do_replace_locating_block(self, s: SessionState, ctx: _Ctx) -> None:
        v = ctx.params["variant"]
        s.components["locating_block"] = v
        s.first_piece_pending = True
        ctx.messages.append(f"夹具定位块已换成 {self.c.variants[v]['name']}（{self.c.variants[v]['color']}标识）。")

    def _do_replace_bolt_box(self, s: SessionState, ctx: _Ctx) -> None:
        v = ctx.params["variant"]
        s.bolt_box_variant = v
        s.components["bolt_box"] = "normal"
        s.bolt_box_isolated = False
        ctx.messages.append(f"螺栓料盒已换成 {self.c.variants[v]['name']}（{self.c.variants[v]['color']}）。")

    def _do_call_quality_confirm(self, s: SessionState, ctx: _Ctx) -> None:
        s.changeover.update({"quality_confirmed": True, "quality_pending": False})
        self._say(s, "quality", "质量员：首件我看过了，合格，签字了，可以批量生产。")
        ctx.messages.append("质量员到场确认首件合格并签字。")

    def _do_pull_andon(self, s: SessionState, ctx: _Ctx) -> None:
        target = ctx.params["target"]
        s.support = Support(kind="andon", target=target, called_at=s.clock)
        ep = self._episode(s)
        if ep is not None:
            ep.andon_pulled = True
        name = self.c.andon_targets[target]["name"]
        greet = {"maintenance": "维修，哪个工位？什么情况？", "quality": "质量，OP 几？出什么问题了？",
                 "team_leader": "我是班组长，怎么了？"}[target]
        s.support.transcript.append({"role": "assistant", "content": greet})
        self._say(s, target, greet)
        s.state = "andon_called"
        ctx.state_set = True
        ctx.messages.append(f"Andon 已拉下，{name}打来电话：“{greet}”")

    def report_truth(self, s: SessionState) -> dict[str, Any]:
        ep = self._episode(s)
        if ep is None and s.episodes and s.episodes[-1].ended_at is None:
            ep = s.episodes[-1]
        if s.scenario_type == "checklist" and ep is None:
            acts = [a for a in s.checklist.get("actions_done", []) if a != "pull_andon"]
            return {"station": self.c.station["id"], "alarm_code": None, "actions_done": acts, "ng_count": None}
        acts = list(ep.actions_done) if ep else []
        return {
            "station": self.c.station["id"],
            "alarm_code": ep.alarm if ep else s.alarm,
            "actions_done": [a for a in acts if a != "pull_andon"],
            "ng_count": ep.ng_count if ep else 0,
        }

    def required_elements(self, s: SessionState) -> list[str]:
        truth = self.report_truth(s)
        req = ["station", "actions_done"]
        if truth["alarm_code"]:
            req.insert(1, "alarm_code")
            req.append("ng_count")
        return req

    def score_report(self, s: SessionState, elements: dict[str, Any]) -> dict[str, Any]:
        """核对汇报要素。AI 只负责抽取学员说了什么；对不对、到场时间由这里决定。"""
        truth = self.report_truth(s)
        req = self.required_elements(s)
        covered: dict[str, bool] = {}
        station = str(elements.get("station") or "").upper().replace(" ", "")
        covered["station"] = station == truth["station"]
        covered["alarm_code"] = truth["alarm_code"] is None or (str(elements.get("alarm_code") or "").upper() == truth["alarm_code"])
        reported = set(elements.get("actions_done") or [])
        truth_acts = set(truth["actions_done"])
        false_claims = sorted(reported - truth_acts)
        covered["actions_done"] = bool(reported & truth_acts) and not false_claims
        ngv = _as_int(elements.get("ng_count"))
        covered["ng_count"] = truth["ng_count"] is None or (ngv is not None and ngv == int(truth["ng_count"]))
        ng_wrong = truth["ng_count"] is not None and ngv is not None and ngv != int(truth["ng_count"])
        hit = [k for k in req if covered[k]]
        coverage = round(len(hit) / len(req), 3) if req else 1.0
        return {"required": req, "covered": {k: covered[k] for k in req}, "coverage": coverage,
                "missing": [k for k in req if not covered[k]], "false_claims": false_claims,
                "ng_count_wrong": ng_wrong, "truth": truth}

    def _do_andon_report(self, s: SessionState, ctx: _Ctx) -> None:
        sup = s.support
        assert sup is not None
        el = ctx.params.get("elements")
        follow = int(ctx.params.get("follow_ups", 0))
        if el == "all" or el is None:
            el = self.report_truth(s)
        scored = self.score_report(s, el)
        cfg = self.c.support["andon"]
        delay = float(cfg["base_arrival_s"]) + float(cfg["per_missing_element_s"]) * len(scored["missing"]) + float(cfg["per_follow_up_s"]) * follow
        sup.reported = True
        sup.report = {"elements": el, **scored, "follow_ups": follow, "delay_s": delay, "text": ctx.params.get("text", "")}
        sup.coverage = scored["coverage"]
        sup.follow_ups = follow
        sup.arrival_at = s.clock + delay
        s.andon_reports.append({"t": s.clock, "target": sup.target, **sup.report})
        self._emit(s, ctx, "andon_reported", {"coverage": scored["coverage"], "ng_count_wrong": scored["ng_count_wrong"]})
        name = self.c.andon_targets[sup.target]["name"]
        mins = max(1, round(delay / 60))
        reply = f"好，{name}大概 {mins} 分钟到。"
        sup.transcript.append({"role": "assistant", "content": reply})
        self._say(s, sup.target, reply)
        s.state = "waiting_repair"
        ctx.state_set = True
        ctx.messages.append(f"汇报已记录（要素覆盖 {round(scored['coverage']*100)}%），{name}预计 {mins} 分钟后到场。")

    def _do_call_material(self, s: SessionState, ctx: _Ctx) -> None:
        s.support = Support(kind="material", target="material_handler", called_at=s.clock, reported=True,
                            arrival_at=s.clock + float(self.c.support["material"]["arrival_s"]))
        s.state = "waiting_repair"
        ctx.state_set = True
        ctx.messages.append(f"已呼叫物料员，预计 {round(float(self.c.support['material']['arrival_s'])/60, 1)} 分钟到。")

    def _do_wait(self, s: SessionState, ctx: _Ctx) -> None:
        sup = s.support
        assert sup is not None and sup.arrival_at is not None
        if s.clock < sup.arrival_at:
            self._tick(s, sup.arrival_at - s.clock)
        ctx.state_set = True
        s.support_history.append(sup)
        s.support = None
        if sup.kind == "material":
            if s.components.get("bolt_box") == "low":
                s.components["bolt_box"] = "normal"
            s.bolt_box_isolated = False
            self._say(s, "material_handler", "物料员：螺栓补满了。")
            ctx.messages.append("物料员到场，螺栓料盒已补满。")
            s.state = "handling"
            return
        target = sup.target
        name = self.c.andon_targets[target]["name"]
        if s.scenario_type == "checklist" and (s.issue is None or s.issue.kind == "checklist"):
            matched = [fid for fid in self._unresolved_faults(s) if self._fault(s, fid).andon_target == target]
            correct = bool(matched)
            self._emit(s, ctx, "andon_arrival", {"target_correct": correct})
            if correct:
                for fid in matched:
                    self._apply_repair(s, self._fault(s, fid).repair or {})
                names = "、".join(self._fault(s, fid).name for fid in matched)
                txt = f"{name}到场：{names}，已经处理好了。"
                self._say(s, target, txt)
                ctx.messages.append(txt)
                s.state = "handling"
            else:
                txt = f"{name}到场看了一圈：没发现需要我处理的问题。"
                self._say(s, target, txt)
                ctx.messages.append(txt)
                ctx.success = False
                s.state = "investigating"
            return
        issue = s.issue
        correct = False
        if issue is not None:
            correct = issue.andon_target == target or (
                issue.in_scope and target in ("maintenance", "team_leader") and issue.support != "material")
        self._emit(s, ctx, "andon_arrival", {"target_correct": correct})
        if not correct:
            ctx.success = False
            right = self.c.andon_targets.get(issue.andon_target or "", {}).get("name", "其他人") if issue else "其他人"
            if issue is not None and issue.support == "material":
                right = "物料员"
            txt = f"{name}到场看了看：这个不归我们管，你应该找{right}。"
            self._say(s, target, txt)
            ctx.messages.append(txt)
            s.state = "investigating"
            return
        assert issue is not None
        names = self._apply_repair(s, issue.repair)
        if issue.in_scope and issue.andon_target != target:
            extra = "这个其实你自己就能处理。"
        else:
            extra = ""
        s.alarm = None
        s.hmi.update({"alarm": None, "alarm_text": None, "cycle_step": "待机"})
        if s.components.get("estop_button") == "pressed" and "estop_button" in issue.repair:
            s.components["estop_button"] = "released"
        s.mode = "paused"
        ep = self._episode(s)
        if ep is not None:
            ep.reset_since_alarm = True
        if issue.repair_first_piece:
            s.first_piece_pending = True
        what = "、".join(names) if names else "问题"
        txt = f"{name}到场：{what}已处理，报警已复位。{extra}" + ("先打个首件确认一下。" if issue.repair_first_piece else "可以恢复生产了。")
        self._say(s, target, txt)
        ctx.messages.append(txt)
        s.state = "handling"

    def _do_test_estop(self, s: SessionState, ctx: _Ctx) -> None:
        st = s.components["estop_function"]
        text = self.c.components["estop_function"]["states"][st]["text"]
        ctx.observation = {"target": "estop_function", "name": "急停功能", "text": text, "closeup": "closeup/estop_pressed.webp"}
        ctx.messages.append(text)
        self._mark_checklist(s, "test_estop", {})

    def _do_test_light_curtain(self, s: SessionState, ctx: _Ctx) -> None:
        st = s.components["curtain_function"]
        text = self.c.components["curtain_function"]["states"][st]["text"]
        ctx.observation = {"target": "curtain_function", "name": "光栅功能", "text": text, "closeup": "closeup/curtain_mirror_clean.webp"}
        ctx.messages.append(text)
        self._mark_checklist(s, "test_light_curtain", {})

    def _do_test_tower_light(self, s: SessionState, ctx: _Ctx) -> None:
        st = s.components["tower_lamp"]
        text = self.c.components["tower_lamp"]["states"][st]["text"]
        ctx.observation = {"target": "tower_lamp", "name": "三色灯", "text": text, "closeup": "closeup/tower_light.webp"}
        ctx.messages.append(text)
        self._mark_checklist(s, "test_tower_light", {})

    def _do_record_check(self, s: SessionState, ctx: _Ctx) -> None:
        item = ctx.params["item"]
        res = ctx.params["result"]
        s.checklist["items"][item]["recorded"] = res
        name = self._item(s, item).name
        ctx.messages.append(f"点检记录：{item} {name} → {'正常' if res == 'ok' else '异常（已处理/已报修）'}。")

    def _do_sign_checklist(self, s: SessionState, ctx: _Ctx) -> None:
        s.checklist["signed"] = True
        ctx.messages.append("点检记录已签字。")

    def _do_cycle_step(self, s: SessionState, ctx: _Ctx) -> None:
        scn = self.c.scenarios[s.scenario_id]
        st = scn.cycle_steps[s.cycle_step_index]
        s.cycle_step_index += 1
        s.hmi["cycle_step"] = st.name
        ctx.observation = {"target": "cycle", "name": st.name, "text": st.text, "step": s.cycle_step_index, "total": len(scn.cycle_steps)}
        ctx.messages.append(f"工作循环第 {s.cycle_step_index}/{len(scn.cycle_steps)} 步 · {st.name}：{st.text}")

    # 越权动作没有处理器：永远在规则/权限阶段被拒绝

    # ------------------------------------------------------------------
    # 点检辅助
    # ------------------------------------------------------------------
    def _item(self, s: SessionState, item_id: str):
        for it in self.c.scenarios[s.scenario_id].items:
            if it.id == item_id:
                return it
        raise KeyError(item_id)

    def _item_fault_ids(self, s: SessionState, item_id: str | None) -> list[str]:
        if s.scenario_type != "checklist" or item_id is None:
            return []
        try:
            return [f.id for f in self._item(s, item_id).faults]
        except KeyError:
            return []

    def _fault(self, s: SessionState, fid: str):
        for it in self.c.scenarios[s.scenario_id].items:
            for f in it.faults:
                if f.id == fid:
                    return f
        raise KeyError(fid)

    def _unresolved_faults(self, s: SessionState) -> list[str]:
        if s.scenario_type != "checklist":
            return []
        out = []
        for fid in s.checklist.get("faults", []):
            f = self._fault(s, fid)
            for k, v in f.setup.items():
                if s.components.get(k) == v:
                    out.append(fid)
                    break
        return out

    def _mark_checklist(self, s: SessionState, action: str, params: dict[str, Any]) -> None:
        if s.scenario_type != "checklist":
            return
        for it in self.c.scenarios[s.scenario_id].items:
            if it.sign_item:
                continue
            for st in it.check:
                if st.action == action and all(params.get(k) == v for k, v in st.params.items()):
                    s.checklist["items"][it.id]["checked"] = True

    def _mark_checklist_fix(self, s: SessionState) -> None:
        return None

    # ==================================================================
    # 标准路径（自动回放）
    # ==================================================================
    def next_standard_step(self, s: SessionState, for_hint: bool = False) -> dict[str, Any] | None:
        if s.state in TERMINAL:
            return None
        issue = s.issue
        if issue is None:
            if s.state == "normal_production":
                return {"action": "run_production", "params": {}}
            return None
        cand = None
        if for_hint and not issue.strict_order:
            last = max([i for i, d in enumerate(issue.done) if d], default=-1)
            for i, st in enumerate(issue.path):
                if not issue.done[i] and (i > last or st["action"] != "inspect"):
                    cand = st
                    break
        else:
            for i, st in enumerate(issue.path):
                if not issue.done[i]:
                    cand = st
                    break
        if cand is None:
            if s.state == "normal_production":
                return {"action": "run_production", "params": {}}
            return None
        if for_hint:
            adef = self.c.actions[cand["action"]]
            if adef.get("zone") in SAFE_ZONES_RULE and not self.c.modes[s.mode]["safe"]:
                return {"action": "pause_equipment", "params": {}}
            if cand["action"] in ("resume", "first_piece_check") and s.alarm:
                return {"action": "rescan" if s.alarm == "T-103" else "reset_alarm", "params": {}}
            if cand["action"] == "release_estop" and not s.safety_checked:
                return {"action": "safety_check", "params": {}}
        return cand

    def run_autopilot(self, s: SessionState, max_steps: int = 400, source: str = "autopilot") -> list[ActResult]:
        out: list[ActResult] = []
        for _ in range(max_steps):
            st = self.next_standard_step(s)
            if st is None:
                break
            actor = "phone" if st["action"] == "andon_report" else "trainee"
            r = self.act(s, st["action"], st.get("params", {}), actor=actor, source=source)
            out.append(r)
            if r.rejected:
                # 标准路径被拒：说明剧本有问题，停止并交给校验器报告
                break
        return out

    def _compute_standard(self, s: SessionState) -> dict[str, Any]:
        clone = s.model_copy(deep=True)
        clone.standard = {}
        results = self.run_autopilot(clone)
        steps = [{"action": r.action, "params": {k: v for k, v in r.params.items() if k != "elements"}, "t": e.t}
                 for r, e in zip(results, [x for x in clone.log if x.source == "autopilot"])
                 if r.action != "run_production"]
        return {
            "downtime": clone.downtime,
            "duration": clone.clock,
            "steps": steps,
            "passed": clone.objective_met and not clone.violations,
            "violations": [v.rule_id for v in clone.violations],
            "rejected": [r.message for r in results if r.rejected],
        }

    # ==================================================================
    # 视图
    # ==================================================================
    def snapshot(self, s: SessionState, reveal: bool = False) -> dict[str, Any]:
        """训练端看到的状态。不含根因等隐藏信息（reveal=True 时用于复盘/师傅）。"""
        scn = self.c.scenarios.get(s.scenario_id)
        p = self.pressure(s)
        piece = s.current_piece
        actions = self.available_actions(s)
        snap: dict[str, Any] = {
            "scenario": {"id": s.scenario_id, "version": s.scenario_version, "type": s.scenario_type,
                         "title": scn.title if scn else s.scenario_id, "story": scn.story if scn else "",
                         "objective": scn.objective if scn else "", "layer": scn.layer if scn else None},
            "state": s.state,
            "state_label": self.c.session_states[s.state]["label"],
            "mode": s.mode,
            "mode_label": self.c.modes[s.mode]["label"],
            "clock": s.clock,
            "downtime": s.downtime,
            "downtime_running": s.downtime_since is not None,
            "loss_yuan": self.loss_yuan(s),
            "loss_per_min": self.loss_per_min,
            "hmi": {**s.hmi, "variant_name": self.c.variants[s.hmi["variant"]]["name"]},
            "indicators": {
                "tower_light": self.tower_color(s),
                "pressure": round(p, 2),
                "pressure_zone": self.pressure_zone(p),
                "curtain": "interrupted" if s.alarm == "T-105" else "ok",
                "estop": s.components.get("estop_button"),
                "controller_link": "ok" if s.components.get("controller_link") == "ok" else "lost",
            },
            "piece": None if piece is None else {"no": piece.no, "barcode": piece.barcode, "variant": piece.variant,
                                                 "result": piece.result, "is_ng": piece.is_ng},
            "order_variant": s.order_variant,
            "produced_ok": s.produced_ok,
            "target_pieces": s.target_pieces,
            "ng_bin_count": s.ng_bin_count,
            "changeover": {k: s.changeover.get(k) for k in ("active", "to_variant", "verified", "quality_confirmed", "done")},
            "support": None if s.support is None else {
                "kind": s.support.kind, "target": s.support.target, "reported": s.support.reported,
                "arrival_in": None if s.support.arrival_at is None else max(0.0, round(s.support.arrival_at - s.clock, 1)),
                "transcript": s.support.transcript},
            "messages": s.messages[-12:],
            "actions": actions,
            "log": [e.model_dump() for e in s.log if e.actor != "system" or e.action in ("start_session", "review")][-40:],
            "violations": [v.model_dump() for v in s.violations] if (s.scenario_type != "shift" or s.state in TERMINAL) else [],
            "hints_used": len(s.hints),
            "ended": s.state in TERMINAL,
            "objective_met": s.objective_met,
            "terminated_by": s.terminated_by,
            "fail_reasons": s.fail_reasons if s.state in TERMINAL else [],
        }
        if s.scenario_type == "checklist" and scn is not None:
            snap["checklist"] = {
                "items": [{"id": it.id, "name": it.name, "standard": it.standard,
                           "checked": s.checklist["items"].get(it.id, {}).get("checked", False) if not it.sign_item else s.checklist["signed"],
                           "recorded": s.checklist["items"].get(it.id, {}).get("recorded") if not it.sign_item else ("signed" if s.checklist["signed"] else None),
                           "sign_item": it.sign_item} for it in scn.items],
                "signed": s.checklist["signed"],
            }
        if s.scenario_type == "tour" and scn is not None:
            snap["tour"] = {"codex": [{"id": e.id, "name": e.name, "desc": e.desc, "targets": e.targets, "lit": e.id in s.codex} for e in scn.codex],
                            "cycle_steps": [{"id": c.id, "name": c.name, "text": c.text} for c in scn.cycle_steps],
                            "cycle_step_index": s.cycle_step_index}
        if reveal:
            snap["hidden"] = self.coach_view(s)
        return snap

    def available_actions(self, s: SessionState) -> list[dict[str, Any]]:
        out = []
        for a in self.c.actions.values():
            if a.get("panel") is None:
                continue
            if s.scenario_type != "tour" and a["category"] == "tour":
                continue
            if s.scenario_type != "checklist" and a["panel"] == "点检":
                continue
            if s.scenario_type == "tour" and a["category"] not in ("observe", "tour", "control"):
                continue
            allowed = self._state_allows(s.state, a["id"], a["category"])
            out.append({"id": a["id"], "name": a["name"], "panel": a["panel"], "params": a.get("params") or {},
                        "enabled": allowed and s.state not in TERMINAL, "requires_pause": a.get("requires_pause", False)})
        return out

    def coach_view(self, s: SessionState) -> dict[str, Any]:
        """带班师傅可见的信息：包含抽中的根因（主持人看不到）。"""
        rc_info = None
        issue = s.issue
        if issue is not None and issue.kind == "rc" and issue.id in self.c.rc_index:
            scn, rc = self.c.rc_index[issue.id]
            rc_info = {"id": rc.id, "name": rc.name, "in_operator_scope": rc.in_operator_scope,
                       "andon_target": rc.andon_target, "direction": rc.direction, "leak_keywords": rc.leak_keywords,
                       "alarm": scn.alarm, "clue_components": [c.component for c in rc.clues if c.component]}
        nxt = self.next_standard_step(s, for_hint=True)
        return {
            "root_cause": rc_info,
            "issue": None if issue is None else {"id": issue.id, "kind": issue.kind, "name": issue.name,
                                                  "in_scope": issue.in_scope, "andon_target": issue.andon_target,
                                                  "path": issue.path, "done": issue.done},
            "next_step": nxt,
            "next_step_text": self.step_demo_text(nxt) if nxt else None,
            "recent_actions": [e.action for e in s.log if e.actor == "trainee"][-8:],
            "checklist_faults": s.checklist.get("faults", []) if s.scenario_type == "checklist" else [],
        }

    def step_demo_text(self, step: dict[str, Any] | None) -> str | None:
        if not step:
            return None
        adef = self.c.actions[step["action"]]
        p = step.get("params", {})
        fmt = {"target_name": self.c.target_name(p.get("target", "")) if p.get("target") else "",
               "variant": p.get("variant", ""), "program": p.get("program", ""),
               "target_name_andon": ""}
        if step["action"] == "pull_andon":
            fmt["target_name"] = self.c.andon_targets.get(p.get("target", ""), {}).get("name", "")
        try:
            return adef.get("demo", adef["name"]).format(**fmt)
        except (KeyError, IndexError):
            return adef.get("demo", adef["name"])

    def clone(self, s: SessionState) -> SessionState:
        return copy.deepcopy(s)
