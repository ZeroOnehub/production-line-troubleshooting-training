"""人工抽检一致率：human_review.csv 填写 human_verdict（pass / fail）后运行，结果写回 results.json 与 report.md。

    python -m eval.agreement <run>
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

REPORTS = Path(__file__).resolve().parent / "reports"


def main() -> int:
    run = sys.argv[1]
    d = REPORTS / run
    rows = list(csv.DictReader((d / "human_review.csv").open(encoding="utf-8")))
    done = [r for r in rows if r["human_verdict"].strip() in ("pass", "fail")]
    if not done:
        print("human_review.csv 还没有人工填写 human_verdict（pass / fail）")
        return 1
    agree = sum(1 for r in done if r["human_verdict"].strip() == r["auto_verdict"])
    rate = round(agree / len(done), 4)
    res = json.loads((d / "results.json").read_text(encoding="utf-8"))
    res["judge"]["human_review"] = f"人工抽检 {len(done)} 条，与自动判定一致 {agree} 条，一致率 {rate * 100:.1f}%"
    (d / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    from .run import write_report

    write_report(d, res)
    print(res["judge"]["human_review"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
