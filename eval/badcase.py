"""bad case 库（eval/badcases.jsonl）：记录 → 归因 → 修复 → 回归 → 留痕。

    python -m eval.badcase sync <前一轮 run> <后一轮 run>   # 前一轮的失败用例入库；后一轮通过的标记为 fixed
    python -m eval.badcase attribute <case_id> <归因> [--note ...] [--status wontfix|open|fixed]
归因只能是：提示词问题 / 规则缺失 / 本体缺对象或动作 / 模型能力不足 / 用例标注问题
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

LIB = Path(__file__).resolve().parent / "badcases.jsonl"
REPORTS = Path(__file__).resolve().parent / "reports"
ATTRIBUTIONS = ["提示词问题", "规则缺失", "本体缺对象或动作", "模型能力不足", "用例标注问题"]


def load() -> list[dict]:
    if not LIB.exists():
        return []
    return [json.loads(l) for l in LIB.read_text(encoding="utf-8").splitlines() if l.strip()]


def save(rows: list[dict]) -> None:
    LIB.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def sync(prev: str, nxt: str) -> None:
    a = json.loads((REPORTS / prev / "results.json").read_text(encoding="utf-8"))
    b = json.loads((REPORTS / nxt / "results.json").read_text(encoding="utf-8"))
    rows = load()
    known = {(r["model"], r["case_id"]) for r in rows}
    for m, d in a["models"].items():
        passed_next = {c["case_id"] for c in b["models"].get(m, {}).get("cases", []) if c.get("pass")}
        for bc in d["bad_cases"]:
            key = (m, bc["case_id"])
            if key not in known:
                rows.append({**bc, "model": m, "found_in": prev, "split": a["split"], "status": "open", "attribution": None, "fix": None})
                known.add(key)
        for r in rows:
            if r["model"] == m and r["status"] == "open" and r["case_id"] in passed_next:
                r["status"] = "fixed"
                r["verified_in"] = nxt
    save(rows)
    print(f"bad case 库：{len(rows)} 条（open {sum(r['status'] == 'open' for r in rows)}，fixed {sum(r['status'] == 'fixed' for r in rows)}）")


def attribute(case_id: str, attribution: str, note: str | None, status: str | None, model: str | None) -> None:
    if attribution not in ATTRIBUTIONS:
        raise SystemExit(f"归因只能是：{ATTRIBUTIONS}")
    rows = load()
    n = 0
    for r in rows:
        if r["case_id"] == case_id and (model is None or r["model"] == model):
            r["attribution"] = attribution
            if note:
                r["fix"] = note
            if status:
                r["status"] = status
            n += 1
    save(rows)
    print(f"更新 {n} 条")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("sync")
    s1.add_argument("prev")
    s1.add_argument("next")
    s2 = sub.add_parser("attribute")
    s2.add_argument("case_id")
    s2.add_argument("attribution")
    s2.add_argument("--note")
    s2.add_argument("--status", choices=["open", "fixed", "wontfix"])
    s2.add_argument("--model")
    args = ap.parse_args()
    if args.cmd == "sync":
        sync(args.prev, args.next)
    else:
        attribute(args.case_id, args.attribution, args.note, args.status, args.model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
