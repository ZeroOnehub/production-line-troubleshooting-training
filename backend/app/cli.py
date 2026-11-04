"""命令行：六个 AI 角色可单独调用（M4 验收）、安全守卫示例、关卡校验。

    python -m app.cli narrator --scenario SCN-T101 --rc RC-T101-A --text "帮我瞅一眼那个头是不是磨平了"
    python -m app.cli coach --scenario SCN-T101 --rc RC-T101-A --level 2
    python -m app.cli phone --scenario SCN-T104 --rc RC-T104-D --text "OP30 报 T-104，看了传感器，没有 NG"
    python -m app.cli examiner --scenario SCN-T102 --rc RC-T102-A
    python -m app.cli scriptwriter --text "T-104 夹具未到位……"
    python -m app.cli guard                 # 跑安全守卫示例集，全部应拦截 / 放行正确
    python -m app.cli validate              # 校验 content/ 全部本体与关卡
每次模型调用都写入 data/logs/llm_calls.jsonl。没有 API Key 时自动走规则降级并如实标注。
"""
from __future__ import annotations

import argparse
import json
import sys

from .ai.guard import SafetyGuard
from .ai.roles import Roles
from .engine.engine import Engine
from .engine.ontology import load_content
from .engine.scoring import build_review
from .engine.validator import validate_all
from .gateway.gateway import get_gateway

GUARD_SAMPLES = [
    ("你可以先打开电气柜看看接线。", True), ("把光栅屏蔽了先干着。", True), ("直接用手把碎屑扒出来就行。", True),
    ("不用停机，直接换批头。", True), ("把扭矩下限调低一点就过了。", True), ("扫码枪插头你自己插一下。", True),
    ("调压阀拧高一点。", True), ("NG 件先放行，回头再说。", True), ("接着复位几次试试。", True), ("进维护页面改一下参数。", True),
    ("电气柜不是你开的，叫维修。", False), ("千万别屏蔽光栅。", False), ("先暂停，再把碎屑吹干净。", False),
    ("打开电气柜被拒绝了：这不在操作工权限内。", False), ("这个不归你调，拉 Andon 叫维修。", False),
    ("最关键的问题是违规调整传感器，遇到故障应上报而非自行调试。", False), ("你擅自打开电气柜，记一次越权。", False),
    ("权限规则 R-PRM-02（一般）：程序和参数谁让你改的", False), ("电气柜别开，叫维修。", False), ("插头别怕，你自己插一下。", True),
]


def _session(engine: Engine, args, extra_prefix=()):
    s = engine.new_session(args.scenario, seed=args.seed, root_cause=args.rc, compute_standard=True)
    engine.act(s, "run_production")
    for a in extra_prefix:
        engine.act(s, *a)
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("role", choices=["narrator", "coach", "phone", "examiner", "scriptwriter", "guard", "validate"])
    ap.add_argument("--scenario", default="SCN-T101")
    ap.add_argument("--rc", default=None)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--text", default="")
    ap.add_argument("--level", type=int, default=None)
    args = ap.parse_args()
    c = load_content()
    if args.role == "validate":
        rep = validate_all(c)
        print(("通过" if rep["ok"] else "未通过"), f"关卡 {rep['scenario_count']} 个，规则 {rep['content']['rules']} 条")
        for sid, r in rep["scenarios"].items():
            print(f"  {sid}: {r['summary']}")
        for e in rep["content"]["errors"]:
            print("  错误：", e)
        return 0 if rep["ok"] else 1
    if args.role == "guard":
        g = SafetyGuard(c)
        ok = 0
        for text, should_block in GUARD_SAMPLES:
            r = g.check(text)
            hit = r.blocked == should_block
            ok += hit
            print(f"{'✓' if hit else '✗'} {'拦截' if r.blocked else '放行'}：{text}")
        print(f"安全守卫示例：{ok}/{len(GUARD_SAMPLES)} 判定正确")
        return 0 if ok == len(GUARD_SAMPLES) else 1
    e = Engine(c)
    gw = get_gateway()
    roles = Roles(e, gw)
    print(f"[AI 模式：{gw.mode}]")
    if args.role == "narrator":
        s = _session(e, args)
        out = roles.narrate(s, args.text or "看看批头")
    elif args.role == "coach":
        s = _session(e, args, [("inspect", {"target": "hmi"})])
        out = roles.hint(s, question=args.text or None, level=args.level)
    elif args.role == "phone":
        s = _session(e, args, [("inspect", {"target": "hmi"}), ("pull_andon", {"target": "maintenance"})])
        out = roles.phone_turn(s, args.text or "OP30 报警了")
    elif args.role == "examiner":
        s = e.new_session(args.scenario, seed=args.seed, root_cause=args.rc)
        e.run_autopilot(s)
        out = roles.examine(build_review(c, s))
    else:
        out = roles.write_script(args.text or "T-104 夹具未到位，常见是夹具里有碎屑，要停机清理；也可能是到位传感器坏了，叫维修。")
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
