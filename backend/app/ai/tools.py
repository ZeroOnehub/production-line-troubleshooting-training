"""AI 工具定义：全部从本体（actions.yaml / objects.yaml）生成，代码里不另写一份动作清单。"""
from __future__ import annotations

from typing import Any

from ..engine.ontology import Content

ANDON_PARAM = "andon_target"   # 工具里把 pull_andon 的 target 改名，避免与“查看目标”混淆


def narrator_tools(c: Content) -> list[dict[str, Any]]:
    actions = [a["id"] for a in c.operator_actions()]
    targets = sorted(c.inspect_targets())
    return [
        {
            "type": "function",
            "function": {
                "name": "act",
                "description": "执行一个学员动作。动作 ID 与参数见系统提示中的动作表。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": actions, "description": "动作 ID"},
                        "target": {"type": "string", "enum": targets, "description": "inspect（查看）的目标 ID"},
                        "variant": {"type": "string", "enum": list(c.variants), "description": "型号（换型相关动作）"},
                        "program": {"type": "string", "enum": sorted({v['program'] for v in c.variants.values()}), "description": "拧紧程序号"},
                        ANDON_PARAM: {"type": "string", "enum": list(c.andon_targets), "description": "pull_andon 的呼叫对象"},
                        "item": {"type": "string", "description": "record_check 的点检项 ID，如 CK-01"},
                        "result": {"type": "string", "enum": ["ok", "ng"], "description": "record_check 的结果"},
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ask_clarification",
                "description": "学员说得含糊时反问，不执行任何动作。",
                "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
            },
        },
    ]


def tool_args_to_step(args: dict[str, Any]) -> dict[str, Any]:
    """把工具参数转成引擎动作 {action, params}。"""
    action = args.get("action")
    params: dict[str, Any] = {}
    for k in ("target", "variant", "program", "item", "result"):
        if args.get(k) not in (None, ""):
            params[k] = args[k]
    if action == "pull_andon":
        params.pop("target", None)
        if args.get(ANDON_PARAM):
            params["target"] = args[ANDON_PARAM]
    elif action != "inspect":
        params.pop("target", None)
    return {"action": action, "params": params}


def phone_tools(c: Content) -> list[dict[str, Any]]:
    acts = [a["id"] for a in c.operator_actions() if a["category"] != "forbidden" and a["id"] not in ("pull_andon", "wait")]
    return [{
        "type": "function",
        "function": {
            "name": "submit_report_elements",
            "description": "提交从操作工汇报中抽取的要素，以及你接下来要说的一句话。",
            "parameters": {
                "type": "object",
                "properties": {
                    "station": {"type": ["string", "null"]},
                    "alarm_code": {"type": ["string", "null"]},
                    "actions_done": {"type": "array", "items": {"type": "string", "enum": acts}},
                    "ng_count": {"type": ["integer", "null"]},
                    "follow_up": {"type": "string"},
                },
                "required": ["station", "alarm_code", "actions_done", "ng_count", "follow_up"],
            },
        },
    }]


def examiner_tools(c: Content) -> list[dict[str, Any]]:
    return [{
        "type": "function",
        "function": {
            "name": "submit_review",
            "description": "提交复盘点评与薄弱点标签。",
            "parameters": {
                "type": "object",
                "properties": {
                    "comment": {"type": "string", "description": "不超过 200 字的点评"},
                    "weakness_tags": {"type": "array", "items": {"type": "string", "enum": c.weakness_tags}, "maxItems": 3},
                },
                "required": ["comment", "weakness_tags"],
            },
        },
    }]


def action_table(c: Content, include_forbidden: bool = True) -> str:
    lines = []
    for a in c.operator_actions():
        if not include_forbidden and a["category"] == "forbidden":
            continue
        params = ",".join(a.get("params", {}) or {})
        al = "、".join(a.get("aliases", [])[:6])
        lines.append(f"- {a['id']}（{a['name']}{'；参数 ' + params if params else ''}）：{al}")
    return "\n".join(lines)


def target_table(c: Content) -> str:
    lines = []
    for tid, name in sorted(c.inspect_targets().items()):
        comp = c.components.get(tid) or c.indicators.get(tid) or c.composites.get(tid) or c.equipment.get(tid) or {}
        if comp.get("hidden"):
            continue
        al = "、".join([x for x in comp.get("aliases", []) if x != name][:6])
        lines.append(f"- {tid}：{name}" + (f"（{al}）" if al else ""))
    return "\n".join(lines)


def alarm_table(c: Content) -> str:
    return "\n".join(f"- {code} {a['text']}" for code, a in c.alarms.items())
