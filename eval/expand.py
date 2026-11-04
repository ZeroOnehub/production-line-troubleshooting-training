"""用大模型扩写意图类种子用例的口语变体，输出到 cases/pending_expand.jsonl，人工审核后再并入正式用例。

    python -m eval.expand --category intent --n 3 --model qwen3.8-max
扩写结果不会自动进入评测（文件名不以类别命名，load_cases 只读 *.jsonl 中的正式用例；审核后手工合并）。
"""
from __future__ import annotations

import argparse
import json

from .common import CASES_DIR, load_cases, make_system

PROMPT = """下面是产线操作工对训练系统说的一句话，以及它对应的系统动作。请写出 {n} 种同一意图的不同说法：车间口语、俗称、
语序变化、语音转写常见错字各至少一种。只改说法，不改意图。只输出 JSON 数组，每个元素是一句话。
原句：{text}
动作：{expected}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="intent")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--model", default="qwen3.8-max")
    args = ap.parse_args()
    sys_ = make_system(args.model)
    out = []
    for c in load_cases([args.category], "dev"):
        if c["expected"] == "clarify":
            continue
        try:
            r = sys_.gw.chat("judge", [{"role": "user", "content": PROMPT.format(n=args.n, text=c["input"], expected=json.dumps(c["expected"], ensure_ascii=False))}],
                             model=args.model, mode="online")
            variants = json.loads((r.content or "[]").strip().strip("`").replace("json", "", 1))
        except Exception as exc:  # noqa: BLE001
            print(f"{c['id']} 扩写失败：{exc}")
            continue
        for i, v in enumerate(variants):
            out.append({**c, "id": f"{c['id']}-X{i + 1}", "input": v, "source": f"expand:{args.model}", "review_status": "pending"})
    (CASES_DIR / "pending_expand.jsonl.txt").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in out), encoding="utf-8")
    print(f"扩写 {len(out)} 条，待人工审核：cases/pending_expand.jsonl.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
