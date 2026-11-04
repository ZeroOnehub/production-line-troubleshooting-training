"""六个 AI 角色的编排：主持人、带班师傅、电话角色、考官、关卡编剧、安全守卫。

原则：AI 只做语言层的工作。所有判定（设备反应、违规、得分、到场时间）都由引擎计算；
主持人只能经工具调用改变状态；每个输出都过安全守卫；模型不可用时走规则兜底并如实标识。
"""
from __future__ import annotations

import json
import re
from typing import Any

import yaml

from ..engine.engine import ActResult, Engine
from ..engine.state import SessionState
from ..gateway.gateway import ModelGateway, NoModelAvailable
from .guard import SafetyGuard
from .prompts import load_prompt, load_yaml_prompt
from .rules_nlu import ALARM_KEYWORDS, RuleNLU
from .tools import action_table, alarm_table, examiner_tools, narrator_tools, phone_tools, target_table, tool_args_to_step

PERSONA = {"maintenance": "设备维修技术员", "quality": "质量员", "team_leader": "班组长"}


def visible_summary(engine: Engine, s: SessionState) -> str:
    """给主持人 / 电话角色看的“学员可见信息”。不含根因，也不含时钟（保证录制回放可匹配）。"""
    scn = engine.c.scenarios[s.scenario_id]
    h = s.hmi
    parts = [
        f"关卡：{scn.title}（{scn.type}）",
        f"训练局状态：{engine.c.session_states[s.state]['label']}",
        f"设备：{engine.c.modes[s.mode]['label']}",
        f"HMI 报警：{h['alarm'] + ' ' + h['alarm_text'] if h.get('alarm') else '无'}",
        f"急停：{'按下' if s.components.get('estop_button') == 'pressed' else '已复位'}",
        f"当前生产型号：{engine.c.variants[s.order_variant]['name']}",
    ]
    if s.changeover.get("active"):
        parts.append(f"换型中：切换到 {s.changeover.get('to_variant')} 型")
    if s.support is not None:
        parts.append(f"呼叫中：{PERSONA.get(s.support.target, s.support.target)}（{'已汇报' if s.support.reported else '待汇报'}）")
    if s.scenario_type == "checklist":
        parts.append("点检项：" + "、".join(f"{it.id} {it.name}" for it in scn.items))
    return "\n".join(parts)


def _result_view(r: ActResult) -> dict[str, Any]:
    return {
        "action": r.action, "params": r.params, "rejected": r.rejected, "terminated": r.terminated,
        "message": r.message, "observation": (r.observation or {}).get("text"), "state": r.state,
        "violations": [v.rule_id for v in r.violations], "seq": r.seq,
        "closeup": (r.observation or {}).get("closeup"),
    }


class Roles:
    def __init__(self, engine: Engine, gateway: ModelGateway):
        self.e = engine
        self.c = engine.c
        self.gw = gateway
        self.guard = SafetyGuard(engine.c)
        self.nlu = RuleNLU(engine.c)

    # ==================================================================
    # 主持人
    # ==================================================================
    def narrator_messages(self, s: SessionState, text: str) -> list[dict[str, Any]]:
        p = load_prompt("narrator")
        sys_prompt = p.render(state_summary=visible_summary(self.e, s), action_table=action_table(self.c),
                              target_table=target_table(self.c))
        return [{"role": "system", "content": sys_prompt}, {"role": "user", "content": text}]

    def narrate(self, s: SessionState, text: str, session_id: str | None = None, execute: bool = True) -> dict[str, Any]:
        p = load_prompt("narrator")
        tools = narrator_tools(self.c)
        msgs = self.narrator_messages(s, text)
        out: dict[str, Any] = {"input": text, "steps": [], "results": [], "reply": "", "clarify": None, "mode": "online",
                               "guard_blocked": False, "guard_hits": [], "calls": 0, "prompt_version": p.version}
        try:
            r1 = self.gw.chat("narrator", msgs, tools=tools, tool_choice="auto", session_id=session_id, prompt_version=p.version)
        except NoModelAvailable:
            return self._narrate_fallback(s, text, out, execute)
        out["mode"] = r1.mode
        out["calls"] = 1
        out["latency_ms"] = r1.latency_ms
        if not r1.tool_calls:
            # 模型没调工具。在线实测出现过：学员说“等维修来”，模型回“已执行等待操作”却没调用 wait，状态没变。
            # 先用规则解析兜底：规则能确定动作就照规则执行，回复用引擎原文；否则不允许模型声称“已执行”。
            if self._rule_backstop(s, text, out, execute):
                return self._guard_reply(out, s)
            reply = clean_reply(r1.content)
            if reply and _EXEC_CLAIM.search(reply):
                out["claim_blocked"] = True
                reply = "我没听明白你要做哪个操作，能说具体点吗？比如“等维修到场”“看看批头”。"
            out["reply"] = reply or "说说你要做什么操作。"
            return self._guard_reply(out, s)
        running = list(msgs)
        r = r1
        ended = False
        # 标准工具调用循环：有的模型一次只调一个工具（如 DeepSeek），执行后再问它，直到它给出文字回复，最多 3 轮
        for round_no in range(3):
            tool_msgs = []
            for tc in r.tool_calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                if name == "ask_clarification":
                    out["clarify"] = args.get("question") or "你具体想做什么？"
                    tool_msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps({"asked": True}, ensure_ascii=False)})
                    continue
                step = tool_args_to_step(args)
                out["steps"].append(step)
                if ended or not execute or step["action"] not in self.c.actions:
                    res_view = {"action": step["action"], "skipped": True, "message": "本局已结束" if ended else "未执行"}
                else:
                    res = self.e.act(s, step["action"], step["params"], actor="trainee", source="nl")
                    res_view = _result_view(res)
                    out["results"].append(res_view)
                    ended = res.ended
                tool_msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(
                    {k: res_view.get(k) for k in ("action", "rejected", "message", "observation", "skipped")}, ensure_ascii=False)})
            running = running + [r.message()] + tool_msgs
            if out["clarify"] and not out["steps"]:
                out["reply"] = out["clarify"]
                return self._guard_reply(out, s)
            last_round = round_no == 2 or ended
            try:
                r = self.gw.chat("narrator", running, tools=tools, tool_choice="none" if last_round else "auto",
                                 session_id=session_id, prompt_version=p.version)
            except NoModelAvailable:
                out["reply"] = self._template_narration(out["results"])
                out["mode"] = "partial_fallback"
                return self._guard_reply(out, s)
            out["calls"] += 1
            out["latency_ms"] = (out.get("latency_ms") or 0) + r.latency_ms
            if not r.tool_calls or last_round:
                break
        out["reply"] = clean_reply(r.content) or self._template_narration(out["results"])
        return self._guard_reply(out, s, regen=lambda note: self._regen_narration(running, tools, note, session_id, p.version))

    def _regen_narration(self, msgs2, tools, note, session_id, pv) -> str | None:
        try:
            r = self.gw.chat("narrator", msgs2 + [{"role": "system", "content": note}], tools=tools, tool_choice="none",
                             session_id=session_id, prompt_version=pv, tags={"regenerate": True})
            return clean_reply(r.content) or None
        except NoModelAvailable:
            return None

    def _template_narration(self, results: list[dict[str, Any]]) -> str:
        if not results:
            return "好的。"
        return " ".join(r.get("message") or "" for r in results).strip()

    def _rule_backstop(self, s: SessionState, text: str, out: dict[str, Any], execute: bool) -> bool:
        """模型没调工具时的规则兜底。只在规则解析得出明确动作（不需要反问）时生效。"""
        snap = {"indicators": {"estop": s.components.get("estop_button")}, "order_variant": s.order_variant,
                "changeover": dict(s.changeover)}
        parsed = self.nlu.parse(text, snap)
        if parsed.clarify or not parsed.steps:
            return False
        out["steps"] = parsed.steps
        out["backstop"] = "rules"
        if execute:
            for st in parsed.steps:
                r = self.e.act(s, st["action"], st["params"], source="nl")
                out["results"].append(_result_view(r))
                if r.ended:
                    break
        out["reply"] = self._template_narration(out["results"]) if execute else "好的。"
        return True

    def _narrate_fallback(self, s: SessionState, text: str, out: dict[str, Any], execute: bool) -> dict[str, Any]:
        out["mode"] = "fallback"
        snap = {"indicators": {"estop": s.components.get("estop_button")}, "order_variant": s.order_variant,
                "changeover": dict(s.changeover)}
        parsed = self.nlu.parse(text, snap)
        out["steps"] = parsed.steps
        if parsed.clarify:
            out["clarify"] = parsed.clarify
            out["reply"] = parsed.clarify
            return out
        if execute:
            for st in parsed.steps:
                r = self.e.act(s, st["action"], st["params"], source="nl")
                out["results"].append(_result_view(r))
                if r.ended:
                    break
        out["reply"] = self._template_narration(out["results"])
        return self._guard_reply(out, s)

    def _guard_reply(self, out: dict[str, Any], s: SessionState, regen=None) -> dict[str, Any]:
        g = self.guard.check(out["reply"])
        if g.blocked:
            out["guard_blocked"] = True
            out["guard_hits"] = g.hits
            new = regen("上一次回复里出现了越权或危险操作的建议，已被安全守卫拦截。重写：只复述工具返回的结果，不给任何建议。") if regen else None
            if new and not self.guard.check(new).blocked:
                out["reply"] = new
            else:
                # 引擎返回的事实是权威的（如“插头没有完全插到位”），守卫只拦 AI 自己加的建议，不替换引擎原文
                out["reply"] = self._template_narration(out["results"]) if out["results"] else SafetyGuard.SAFE_FALLBACK
        return out

    # ==================================================================
    # 带班师傅
    # ==================================================================
    def next_hint_level(self, s: SessionState) -> int:
        marker = self._progress_marker(s)
        lvl = 1
        for h in reversed(s.hints):
            if h.get("marker") == marker:
                lvl = min(3, int(h["level"]) + 1)
            break
        return lvl

    def _progress_marker(self, s: SessionState) -> str:
        issue = s.issue
        done = sum(issue.done) if issue else 0
        return f"{issue.id if issue else '-'}:{done}:{s.state}"

    def coach_hidden(self, s: SessionState) -> tuple[str, list[str], str]:
        cv = self.e.coach_view(s)
        rc = cv["root_cause"]
        leak: list[str] = []
        if rc:
            scope = "在操作工权限内，可以自己处理" if rc["in_operator_scope"] else f"不在操作工权限内，要叫{PERSONA.get(rc['andon_target'], rc['andon_target'])}"
            hidden = f"报警 {rc['alarm']}，真实原因：{rc['name']}（{scope}）。线索在：{'、'.join(self.c.target_name(x) for x in rc['clue_components']) or '屏幕信息'}。"
            leak = list(rc["leak_keywords"]) + [rc["name"]]
            direction = rc.get("direction") or ""
        elif cv["issue"]:
            iss = cv["issue"]
            hidden = f"当前要处理的问题：{iss['name']}（{'操作工可处理' if iss['in_scope'] else '需要叫' + PERSONA.get(iss.get('andon_target') or '', '人')}）。"
            if s.scenario_type == "checklist":
                faults = [self.e._fault(s, f).name for f in cv["checklist_faults"]]
                hidden += f"点检中埋的异常：{'、'.join(faults)}。"
                leak = faults
            direction = ""
        else:
            hidden, direction = "当前没有异常。", ""
        return hidden, leak, direction

    def hint(self, s: SessionState, question: str | None = None, level: int | None = None, session_id: str | None = None,
             record: bool = True) -> dict[str, Any]:
        level = level or self.next_hint_level(s)
        hidden, leak, direction = self.coach_hidden(s)
        nxt = self.e.next_standard_step(s, for_hint=True)
        next_text = self.e.step_demo_text(nxt) if nxt else "目前没有必须马上做的动作。"
        p = load_prompt("coach")
        sys_prompt = p.render(hidden=hidden, state_summary=visible_summary(self.e, s), level=level,
                              direction=direction or "（按根因线索所在的设备区域）", next_step=next_text)
        user = question.strip() if question else "师傅，我该怎么办？"
        msgs = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}]
        out: dict[str, Any] = {"level": level, "mode": "online", "leak_blocked": False, "guard_blocked": False, "prompt_version": p.version}
        text = None
        try:
            r = self.gw.chat("coach", msgs, session_id=session_id, prompt_version=p.version, tags={"level": level})
            out["mode"] = r.mode
            out["latency_ms"] = r.latency_ms
            text = (r.content or "").strip()
            if level < 3 and self.leaks(text, leak):
                out["leak_blocked"] = True
                try:
                    r2 = self.gw.chat("coach", msgs + [{"role": "assistant", "content": text},
                                                       {"role": "user", "content": "你刚才把原因说出来了。这一级不能说原因，按级别要求重新说一句。"}],
                                      session_id=session_id, prompt_version=p.version, tags={"regenerate": True})
                    text = (r2.content or "").strip()
                except NoModelAvailable:
                    text = None
                if text is None or self.leaks(text, leak):
                    text = self.coach_fallback(s, level)
                    out["mode"] = "fallback_after_leak"
        except NoModelAvailable:
            out["mode"] = "fallback"
            text = self.coach_fallback(s, level)
        g = self.guard.check(text)
        if g.blocked:
            out["guard_blocked"] = True
            out["guard_hits"] = g.hits
            text = self.coach_fallback(s, level)
        text = text[:80]
        out["text"] = text
        if record:
            s.hints.append({"level": level, "t": s.clock, "text": text, "mode": out["mode"], "marker": self._progress_marker(s),
                            "question": question})
        return out

    @staticmethod
    def leaks(text: str | None, keywords: list[str]) -> bool:
        if not text:
            return False
        t = text.replace(" ", "")
        return any(k and k.replace(" ", "") in t for k in keywords)

    def coach_fallback(self, s: SessionState, level: int) -> str:
        tpl = load_yaml_prompt("coach_fallback")
        cv = self.e.coach_view(s)
        nxt = self.e.next_standard_step(s, for_hint=True)
        ep = self.e._episode(s)
        if level >= 3:
            return tpl["level3_prefix"] + (self.e.step_demo_text(nxt) or "先停下来，处理不了就拉 Andon。") if nxt else "现在不用做什么，按节拍生产就行。"
        if level == 2:
            if s.scenario_type == "checklist":
                for it in self.c.scenarios[s.scenario_id].items:
                    if not it.sign_item and not s.checklist["items"][it.id]["checked"]:
                        return tpl["level2"]["checklist"].format(item=it.name)
                return tpl["level1"]["checklist"]
            if s.scenario_type == "tour":
                for e in self.c.scenarios[s.scenario_id].codex:
                    if e.id not in s.codex:
                        return tpl["level2"]["tour"].format(target=e.name)
            if s.issue is not None and s.issue.kind == "changeover":
                return tpl["level2"]["changeover"]
            rc = cv["root_cause"]
            if rc and rc.get("direction"):
                return rc["direction"]
            if s.issue is not None and not s.issue.in_scope:
                return tpl["level2"]["out_of_scope"]
            if rc and rc["clue_components"]:
                eq = self.c.components[rc["clue_components"][-1]]["equipment"]
                return tpl["level2"]["default"].format(equipment=self.c.equipment[eq]["name"])
            return tpl["level1"]["investigating"]
        l1 = tpl["level1"]
        if s.scenario_type == "tour":
            return l1["tour"]
        if s.scenario_type == "checklist":
            return l1["checklist"]
        if s.state in ("andon_called", "waiting_repair"):
            return l1["andon_waiting"]
        if s.state == "recovery_check":
            return l1["recovery"]
        if ep is not None and ep.ended_at is None:
            if not ep.alarm_viewed:
                return l1["no_alarm_view"]
            if nxt and self.c.actions[nxt["action"]].get("zone") in ("fixture", "tool", "curtain") and not self.c.modes[s.mode]["safe"]:
                return l1["unsafe_mode"]
            return l1["investigating"]
        if s.changeover.get("active"):
            return l1["changeover"]
        return l1["normal"]

    # ==================================================================
    # 电话角色
    # ==================================================================
    def phone_turn(self, s: SessionState, text: str, session_id: str | None = None) -> dict[str, Any]:
        sup = s.support
        if sup is None or sup.kind != "andon" or sup.reported:
            return {"ok": False, "reply": "现在没有 Andon 电话。", "done": False, "mode": "none"}
        cfg = self.c.support["andon"]
        max_follow = int(cfg.get("max_follow_up_rounds", 1))
        follow_left = max_follow - sup.follow_ups
        sup.transcript.append({"role": "user", "content": text})
        p = load_prompt("phone")
        sys_prompt = p.render(persona=PERSONA[sup.target], follow_ups_left=follow_left, alarm_table=alarm_table(self.c),
                              action_table=action_table(self.c, include_forbidden=False))
        msgs = [{"role": "system", "content": sys_prompt}] + [
            {"role": m["role"], "content": m["content"]} for m in sup.transcript]
        out: dict[str, Any] = {"mode": "online", "prompt_version": p.version, "guard_blocked": False}
        try:
            r = self.gw.chat("phone", msgs, tools=phone_tools(self.c),
                             tool_choice={"type": "function", "function": {"name": "submit_report_elements"}},
                             session_id=session_id, prompt_version=p.version)
            out["mode"] = r.mode
            out["latency_ms"] = r.latency_ms
            args = (r.tool_args("submit_report_elements") or [{}])[0]
            follow_text = args.pop("follow_up", None)
            elements = {k: (None if str(args.get(k)).strip().lower() in ("null", "none", "", "无", "未知") else args.get(k))
                        for k in ("station", "alarm_code", "ng_count")}
            acts = args.get("actions_done") or []
            elements["actions_done"] = [a for a in acts if isinstance(a, str) and a in self.c.actions] if isinstance(acts, list) else []
        except NoModelAvailable:
            out["mode"] = "fallback"
            elements = self.nlu.extract_report(text)
            follow_text = None
        # 合并多轮
        merged = dict(sup.partial)
        for k, v in elements.items():
            if v not in (None, [], ""):
                if k == "actions_done":
                    merged[k] = sorted(set(merged.get(k) or []) | set(v))
                else:
                    merged[k] = v
        for k in ("station", "alarm_code", "actions_done", "ng_count"):
            merged.setdefault(k, [] if k == "actions_done" else None)
        sup.partial = merged
        required = self.e.required_elements(s)
        missing = [k for k in required if merged.get(k) in (None, [], "")]
        out["elements"] = merged
        out["missing_by_extraction"] = missing
        if missing and follow_left > 0:
            names = self.c.support["andon"]["report_elements"]
            asks = follow_text and out["mode"] != "fallback" and any(x in follow_text for x in ("？", "?", "吗", "几", "什么", "哪", "说一下", "多少"))
            q = follow_text if asks else ("还有：" + "、".join(names[m] for m in missing) + "，说一下？")
            if self.guard.check(q).blocked:
                out["guard_blocked"] = True
                q = "还有几项没说清：" + "、".join(names[m] for m in missing) + "。"
            sup.follow_ups += 1
            sup.transcript.append({"role": "assistant", "content": q})
            out.update({"ok": True, "reply": q, "done": False})
            return out
        user_text = " / ".join(m["content"] for m in sup.transcript if m["role"] == "user")
        r = self.e.act(s, "andon_report", {"elements": merged, "follow_ups": sup.follow_ups, "text": user_text}, actor="phone", source="nl")
        closing = follow_text if (follow_text and out["mode"] != "fallback" and not self.guard.check(follow_text).blocked) else "知道了，马上过去。"
        rep = s.andon_reports[-1] if s.andon_reports else {}
        eta = s.support.transcript[-1]["content"] if (s.support is not None and s.support.transcript) else ""
        # 模型的结束语 + 系统算出的到场时间，拼成一句通顺的话（“知道了，马上过去。维修技术员大概 2 分钟到。”）
        closing = closing.strip()
        if closing and closing[-1] not in "。！？!?.":
            closing += "。"
        eta_part = eta[2:] if eta.startswith("好，") else eta
        out.update({"ok": not r.rejected, "reply": (closing + eta_part).strip(),
                    "done": True, "coverage": rep.get("coverage"), "report": rep, "result": _result_view(r)})
        return out

    # ==================================================================
    # 考官
    # ==================================================================
    def examiner_facts(self, review: dict[str, Any]) -> dict[str, Any]:
        sc = review["scores"]
        return {
            "通过": review["passed"],
            "总分": sc["total"],
            "五维": {sc["dimension_names"][k]: v for k, v in sc["dimensions"].items()},
            "失败原因": review["fail_reasons"],
            "根因": (review.get("root_cause") or {}).get("name"),
            "违规": [{"规则": v["rule_id"], "说明": v["message"]} for v in review["violations"]],
            "漏掉的标准步骤": [r["label"] for r in review["path"]["rows"] if r["status"] == "missed"],
            "多余或被拒绝的操作": [x["name"] for x in review["path"]["extras"]] + [x["name"] for x in review["path"]["rejected"]],
            "提示": [h["level"] for h in review["hints"]],
            "汇报覆盖率": [r["coverage"] for r in review["andon_reports"]],
            "停线分钟": round(review["downtime_s"] / 60, 1),
            "标准停线分钟": round((review.get("standard_downtime_s") or 0) / 60, 1),
            "后果": review["consequences"],
        }

    def examine(self, review: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
        p = load_prompt("examiner")
        facts = self.examiner_facts(review)
        msgs = [{"role": "system", "content": p.render(tags="、".join(self.c.weakness_tags),
                                                       facts=json.dumps(facts, ensure_ascii=False, indent=1))},
                {"role": "user", "content": "请写复盘点评。"}]
        out: dict[str, Any] = {"mode": "online", "prompt_version": p.version}
        try:
            r = self.gw.chat("examiner", msgs, tools=examiner_tools(self.c),
                             tool_choice={"type": "function", "function": {"name": "submit_review"}},
                             session_id=session_id, prompt_version=p.version)
            out["mode"] = r.mode
            args = (r.tool_args("submit_review") or [{}])[0]
            comment = str(args.get("comment") or "")[:200]
            tags = [t for t in (args.get("weakness_tags") or []) if t in self.c.weakness_tags][:3]
            if not comment or self.guard.check(comment).blocked:
                raise NoModelAvailable("empty or blocked")
        except NoModelAvailable:
            out["mode"] = "fallback"
            comment = self.examiner_fallback(review)
            tags = review["weakness_tags"][:3]
        out["comment"] = comment
        out["ai_tags"] = tags
        return out

    def examiner_fallback(self, review: dict[str, Any]) -> str:
        sc = review["scores"]
        dims = sc["dimensions"]
        names = sc["dimension_names"]
        best = max(dims, key=lambda k: dims[k])
        worst = min(dims, key=lambda k: dims[k])
        head = f"本局{'通过' if review['passed'] else '未通过'}，总分 {sc['total']}。"
        if review["terminated_by"]:
            head += f"触发一票否决的安全规则 {review['terminated_by']}，安全永远排第一。"
        elif review["fail_reasons"]:
            head += "主要问题：" + "；".join(review["fail_reasons"][:2]) + "。"
        good = f"{names[best]}做得最好（{dims[best]:.0f} 分）。"
        bad_items = [v["message"].split("：", 1)[-1] for v in review["violations"][:2]]
        missed = [r["label"] for r in review["path"]["rows"] if r["status"] == "missed" and not r["label"].startswith("查看")][:2]
        bad = ""
        if bad_items:
            bad += "要记住：" + "；".join(bad_items) + "。"
        if missed:
            bad += "漏了：" + "、".join(missed) + "。"
        if not bad:
            bad = f"{names[worst]}还有提升空间（{dims[worst]:.0f} 分）。"
        tip = "下次先看报警、再暂停、再动手，处理完打首件确认。" if review["scenario_type"] == "alarm" else "下次按清单一步一步来。"
        return (head + good + bad + tip)[:200]

    # ==================================================================
    # 关卡编剧
    # ==================================================================
    def scriptwriter_messages(self, oral: str, feedback: list[str] | None = None, example_id: str = "SCN-T104") -> list[dict[str, Any]]:
        p = load_prompt("scriptwriter")
        alarms = []
        for code, a in self.c.alarms.items():
            alarms.append(f"- {code} {a['text']}（阶段 {a['stage']}；触发条件：{json.dumps(a['trigger'], ensure_ascii=False)}）")
        comps = []
        for cid, comp in self.c.components.items():
            states = "/".join(f"{k}({v['label']})" for k, v in comp["states"].items())
            comps.append(f"- {cid} {comp['name']}：{states}；正常={comp['default']}")
        rules = "\n".join(f"- {r['id']}（{r['type']}）{r['quote']}" for r in self.c.rules)
        example = self.c.scenario_files[example_id].read_text(encoding="utf-8") if example_id in self.c.scenario_files else ""
        sys_prompt = p.render(alarm_table="\n".join(alarms), component_table="\n".join(comps),
                              action_table=action_table(self.c, include_forbidden=False), rule_table=rules, example=example)
        user = f"师傅口述：\n{oral}"
        if feedback:
            user += "\n\n上一版草稿没有通过校验，问题如下，请修正后重新输出完整剧本：\n" + "\n".join(f"- {f}" for f in feedback[:20])
        return [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}]

    def write_script(self, oral: str, feedback: list[str] | None = None, draft_id: str | None = None,
                     model: str | None = None) -> dict[str, Any]:
        p = load_prompt("scriptwriter")
        msgs = self.scriptwriter_messages(oral, feedback)
        try:
            r = self.gw.chat("scriptwriter", msgs, session_id=draft_id, prompt_version=p.version, model=model)
            text = r.content or ""
            yml, expl = split_yaml(text)
            return {"yaml": yml, "explanation": expl, "mode": r.mode, "prompt_version": p.version, "model": r.model,
                    "latency_ms": r.latency_ms, "cost_usd": r.cost_usd}
        except NoModelAvailable:
            yml, expl = rule_draft(self.c, self.nlu, oral)
            return {"yaml": yml, "explanation": expl, "mode": "fallback", "prompt_version": p.version, "model": "rule-baseline"}


# 没调工具却声称“已执行 / 已经做了”的说法（执行声明必须来自引擎结果）
_EXEC_CLAIM = re.compile(r"(已经?(帮你|为你|给你)?(执行|完成|处理|暂停|复位|等待|等了|更换|换上|换了|清理|清洁|擦|隔离|呼叫|拉下|拉了|放入|放进|打开|恢复|确认|签字|记录|提交)|帮你(做|执行|处理)了|执行了|操作已)")
_TOOL_MARKUP = re.compile(r"<[｜|][^>]*?(tool|invoke|parameter|DSML)[^>]*>.*", re.S)


def clean_reply(text: str | None) -> str:
    """去掉部分模型在不允许调用工具时吐出的工具调用标记（如 <｜DSML｜tool_calls>）。"""
    if not text:
        return ""
    return _TOOL_MARKUP.sub("", text).strip()


def split_yaml(text: str) -> tuple[str, str]:
    m = re.search(r"```(?:yaml|yml)?\s*\n(.*?)```", text, re.S)
    if not m:
        return text.strip(), ""
    return m.group(1).strip() + "\n", text[m.end():].strip()


# ---------------------------------------------------------------------------
# 规则降级模式的剧本草稿：从口述里找报警码和被提到的部件，按报警触发条件拼出根因。
# 只能处理“报警 + 部件状态”说得很清楚的口述；它的定位是“模型不可用时也能把工坊流程走通”。
# ---------------------------------------------------------------------------
IN_SCOPE_FIX = {
    ("bit", "worn"): [{"action": "pause_equipment"}, {"action": "isolate_ng_part"}, {"action": "replace_bit"}, {"action": "reset_alarm"}, {"action": "first_piece_check"}],
    ("socket", "loose"): [{"action": "pause_equipment"}, {"action": "isolate_ng_part"}, {"action": "tighten_socket"}, {"action": "reset_alarm"}, {"action": "first_piece_check"}],
    ("fixture_seat", "debris"): [{"action": "pause_equipment"}, {"action": "clean_fixture"}, {"action": "reset_alarm"}],
    ("fixture_seat", "misplaced"): [{"action": "pause_equipment"}, {"action": "reposition_workpiece"}, {"action": "reset_alarm"}],
    ("scan_window", "dirty"): [{"action": "clean_scanner_window"}, {"action": "rescan"}],
    ("curtain_zone", "blocked"): [{"action": "remove_obstruction"}, {"action": "reset_alarm"}],
    ("curtain_mirror", "dirty"): [{"action": "pause_equipment"}, {"action": "clean_curtain_mirror"}, {"action": "reset_alarm"}],
    ("hand_valve", "half"): [{"action": "open_hand_valve"}, {"action": "reset_alarm"}],
    ("bolt_seat", "cross_threaded"): [{"action": "pause_equipment"}, {"action": "isolate_ng_part"}, {"action": "reset_alarm"}],
}
QUALITY_COMPS = {"label", "thread_hole", "bolt_box"}
NG_ALARMS = {"T-101", "T-102"}


def rule_draft(c, nlu: RuleNLU, oral: str) -> tuple[str, str]:
    raw = oral.replace(" ", "")
    m = re.search(r"T-?(\d{3})", raw.upper())
    code = f"T-{m.group(1)}" if m else None
    if code not in c.alarms:
        for k, kws in ALARM_KEYWORDS.items():
            if any(w in raw for w in kws):
                code = k
                break
    if code not in c.alarms:
        return ("# 规则草稿：口述里没有识别出报警码，请补充或在线生成\n", "规则降级模式：没有识别出报警码。")
    trig = c.alarms[code]["trigger"]
    conds = trig.get("any", [trig])
    rcs = []
    for cond in conds:
        comp = cond.get("fact")
        if comp not in c.components:
            continue
        states = cond.get("in") or [cond.get("eq")]
        aliases = [c.components[comp]["name"], *c.components[comp].get("aliases", [])]
        if not any(a in raw for a in aliases):
            continue
        for st in states:
            sdef = c.components[comp]["states"].get(st)
            if not sdef:
                continue
            rc_id = f"RC-{code.replace('-', '')}W-{chr(65 + len(rcs))}"
            path = [{"action": "inspect", "target": "hmi"}, {"action": "inspect", "target": comp}]
            fix = IN_SCOPE_FIX.get((comp, st))
            rc: dict[str, Any] = {"id": rc_id, "name": f"{c.components[comp]['name']}{sdef['label']}", "weight": 0.0,
                                  "in_operator_scope": fix is not None, "setup": {comp: st},
                                  "clues": [{"component": comp, "state": st, "view": f"closeup/{sdef['closeup']}"}],
                                  "leak_keywords": [sdef["label"]]}
            if fix:
                path += fix
                if code in NG_ALARMS and {"action": "isolate_ng_part"} not in path:
                    path.insert(3, {"action": "isolate_ng_part"})
            else:
                target = "quality" if comp in QUALITY_COMPS else "maintenance"
                if code in NG_ALARMS:
                    path += [{"action": "pause_equipment"}, {"action": "isolate_ng_part"}]
                if comp == "bolt_box" and st == "mixed":
                    path.append({"action": "isolate_bolt_box"})      # R-QLT-03：混料整盒隔离
                if comp == "pressure_gauge" or code in ("T-107",):
                    path.insert(2, {"action": "inspect", "target": "hand_valve"})
                path += [{"action": "pull_andon", "target": target}, {"action": "andon_report", "elements": "all"}, {"action": "wait"}]
                rc.update({"andon_target": target, "repair": {comp: c.components[comp]["default"]}, "repair_first_piece": True})
                path.append({"action": "first_piece_check"})
            path.append({"action": "resume"})
            rc["correct_path"] = path
            rcs.append(rc)
    if not rcs:
        return (f"# 规则草稿：识别到报警 {code}，但口述里提到的部件与该报警的触发条件对不上，请在线生成或手工补充\n",
                "规则降级模式：没有匹配到可用的根因。")
    for rc in rcs:
        rc["weight"] = round(1 / len(rcs), 2)
    rcs[-1]["weight"] = round(1 - sum(r["weight"] for r in rcs[:-1]), 2)
    num = re.sub(r"\D", "", code)
    doc = {
        "id": f"SCN-W{num}", "version": 1, "type": "alarm", "layer": 3, "station": "OP30",
        "title": c.alarms[code]["text"] + "（工坊草稿）", "alarm": code,
        "summary": oral[:80], "story": "", "objective": "找到根因并正确处置，恢复生产",
        "event": {"at_piece": 3}, "root_causes": rcs,
        "distractors": [{"target": "pressure_gauge", "value": 0.52, "text": "压力表读数 0.52 MPa，指针在绿区，比平时略低一点。"},
                        {"target": "bit_counter", "text": "批头使用次数 4100 / 5000 次（示意值），还没到寿命。"}],
        "rules": ["R-SAF-01", "R-SAF-02", "R-QLT-02", "R-HDL-03"],
        "negative_cases": [], "author": "工坊口述", "reviewed_by": [],
    }
    if len(rcs) < 2:
        return (yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                "规则降级模式：只识别出 1 个根因（异常关卡至少需要 2 个），请补充口述或手工添加后再校验。")
    return (yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
            f"规则降级模式生成（非大模型）：从口述中识别到报警 {code} 和 {len(rcs)} 个根因；干扰项为通用示例，请审核人重点确认处置路径与呼叫对象。")
