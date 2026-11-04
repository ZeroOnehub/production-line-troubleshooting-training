"""录制 / 校验标准演示局（离线演示模式）。

    APP_MODE=online RECORD_CALLS=true python scripts/record_demo.py           # 在线跑一遍并录制（需要 API Key）
    APP_MODE=offline python scripts/record_demo.py --verify                   # 离线回放校验：每一步都应命中录制
    APP_MODE=degraded python scripts/record_demo.py --dry-run                 # 不调用模型，只检查脚本能完整走通

录制文件：data/recordings/<演示名>.jsonl（每次模型调用的输入输出）。scripts/check.py 会检查录制文件是否齐全。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.roles import Roles  # noqa: E402
from app.engine.engine import Engine  # noqa: E402
from app.engine.ontology import load_content  # noqa: E402
from app.engine.scoring import build_review  # noqa: E402
from app.gateway.gateway import get_gateway  # noqa: E402

REQUIRED = ["demo1_self_handle", "demo2_andon", "demo3_shift", "demo_workshop"]


def run(verify: bool) -> int:
    cfg = yaml.safe_load((ROOT / "scripts" / "demo_script.yaml").read_text(encoding="utf-8"))
    c = load_content()
    e = Engine(c)
    gw = get_gateway()
    roles = Roles(e, gw)
    modes: Counter = Counter()
    calls: list[dict] = []
    gw.sinks.append(calls.append)
    print(f"AI 模式：{gw.mode}（录制：{gw.settings.record_calls}）")
    for sess in cfg["sessions"]:
        gw.record_name = sess["name"]
        s = e.new_session(sess["scenario"], seed=sess.get("seed"), root_cause=sess.get("root_cause"))
        print(f"\n== {sess['title']}")
        if sess.get("autopilot_with_hints"):
            hints = int(sess["autopilot_with_hints"])
            for _ in range(400):
                st = e.next_standard_step(s)
                if st is None:
                    break
                if hints and s.state == "abnormal":
                    h = roles.hint(s, session_id=sess["name"])
                    modes[h["mode"]] += 1
                    print(f"  [师傅 L{h['level']}·{h['mode']}] {h['text']}")
                    hints -= 1
                actor = "phone" if st["action"] == "andon_report" else "trainee"
                e.act(s, st["action"], st["params"], actor=actor)
        for step in sess["steps"]:
            if "say" in step:
                out = roles.narrate(s, step["say"], session_id=sess["name"])
                modes[out["mode"]] += 1
                print(f"  > {step['say']}\n    [{out['mode']}] {out['reply']}")
            elif "hint" in step:
                out = roles.hint(s, question=step["hint"], session_id=sess["name"])
                modes[out["mode"]] += 1
                print(f"  ? {step['hint']}\n    [师傅 L{out['level']}·{out['mode']}] {out['text']}")
            elif "phone" in step:
                out = roles.phone_turn(s, step["phone"], session_id=sess["name"])
                modes[out["mode"]] += 1
                print(f"  ☎ {step['phone']}\n    [电话·{out['mode']}] {out['reply']}")
            elif step.get("review"):
                if s.state not in ("review", "completed"):
                    e.end_session(s, "演示脚本结束")
                rev = build_review(c, s)
                ex = roles.examine(rev, session_id=sess["name"])
                modes[ex["mode"]] += 1
                print(f"  复盘：{'通过' if rev['passed'] else '未通过'} {rev['scores']['total']} 分；[考官·{ex['mode']}] {ex['comment']}")
    gw.record_name = "demo_workshop"
    w = cfg["workshop"]
    out = roles.write_script(w["oral"], draft_id="demo_workshop")
    modes[out["mode"]] += 1
    from app.engine.validator import validate_yaml_text

    rep = validate_yaml_text(c, out["yaml"])
    print(f"\n== 工坊：生成方式 {out['mode']}，校验：{rep['summary']}")
    print(f"\n调用模式统计：{dict(modes)}")
    if verify:
        bad = {k: v for k, v in modes.items() if k not in ("replay",)}
        if bad:
            print(f"离线校验未通过：有 {sum(bad.values())} 次没有命中录制（{bad}）")
            return 1
        print("离线校验通过：全部命中录制")
    return 0


def check_files() -> dict:
    from app.settings import get_settings

    d = get_settings().recordings_dir
    have = {p.stem for p in d.glob("*.jsonl")} if d.exists() else set()
    return {"dir": str(d), "required": REQUIRED, "missing": [r for r in REQUIRED if r not in have]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check-files", action="store_true")
    args = ap.parse_args()
    if args.check_files:
        print(json.dumps(check_files(), ensure_ascii=False))
        return 0
    return run(args.verify)


if __name__ == "__main__":
    sys.exit(main())
