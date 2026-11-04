"""按当前口径重算已有评测结果中的描述忠实度（不重新调用模型，只用已存的规则与大模型裁判结论）。

    python -m eval.rejudge <run>
口径：有大模型裁判时以它为准，规则裁判只在没有裁判时兜底。原数值保留在 metrics.faithfulness.before_rejudge。
"""
from __future__ import annotations

import json
import sys

from .common import REPORTS_DIR, case_set_info, load_cases
from .judges.rules import f1_pairs
from .run import write_report


def main() -> int:
    run = sys.argv[1]
    p = REPORTS_DIR / run / "results.json"
    res = json.loads(p.read_text(encoding="utf-8"))
    for m, d in res["models"].items():
        fx = [c for c in d["cases"] if c["category"] == "faithfulness"]
        if not fx or "faithfulness" not in d["metrics"] or d.get("kind") == "rules" and not any(c.get("llm_judge") for c in fx):
            continue
        dis = 0
        for c in fx:
            llm = c.get("llm_judge")
            rule_ok = (c.get("rule") or {}).get("pass")
            new = bool(llm.get("faithful")) if llm is not None else bool(rule_ok)
            dis += int(llm is not None and bool(llm.get("faithful")) != bool(rule_ok))
            c["pass"], c["judge_basis"] = new, ("llm" if llm is not None else "rule")
        mt = d["metrics"]["faithfulness"]
        mt.setdefault("before_rejudge", mt["value"])
        mt["value"] = round(sum(1 for c in fx if c["pass"]) / len(fx), 4)
        mt["pass"] = mt["value"] >= 0.98
        mt["rule_llm_disagree"] = dis
        d["per_category"]["faithfulness"]["pass"] = sum(1 for c in fx if c["pass"])
        d["bad_cases"] = [{k: c.get(k) for k in ("case_id", "category", "input", "expected", "got", "error")} for c in d["cases"] if not c.get("pass")]
        print(f"{m}: 忠实度 {mt['before_rejudge']} → {mt['value']}（规则与大模型裁判分歧 {dis} 条）")
    # 汇报要素：按当前用例标注（v1.1）重算 F1，用已保存的抽取结果，不重新调用模型
    labels = {c["id"]: c["expected"] for c in load_cases(["report"], "all")}
    for m, d in res["models"].items():
        reps = [c for c in d["cases"] if c["category"] == "report"]
        if not reps or "report_f1" not in d["metrics"]:
            continue
        tp = fp = fn = 0
        for c in reps:
            exp = labels.get(c["case_id"], c.get("expected"))
            got = c.get("got") if isinstance(c.get("got"), dict) else {}
            if isinstance(got, dict) and str(got.get("station")).lower() == "null":
                got = {**got, "station": None}
            a, b, x = f1_pairs(exp, got or {})
            c.update({"tp": a, "fp": b, "fn": x, "expected": exp, "pass": b == 0 and x == 0})
            tp, fp, fn = tp + a, fp + b, fn + x
        pr = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        f1 = round(2 * pr * rc / (pr + rc), 4) if pr + rc else 0.0
        mt = d["metrics"]["report_f1"]
        mt.setdefault("before_rejudge", mt["value"])
        mt.update({"value": f1, "precision": round(pr, 4), "recall": round(rc, 4), "pass": f1 >= 0.9})
        d["per_category"]["report"]["pass"] = sum(1 for c in reps if c["pass"])
        d["bad_cases"] = [{k: c.get(k) for k in ("case_id", "category", "input", "expected", "got", "error")} for c in d["cases"] if not c.get("pass")]
        print(f"{m}: 要素 F1 {mt['before_rejudge']} → {f1}（按用例 v1.1 标注）")
    res["case_set"] = case_set_info()
    res.setdefault("notes", []).append("描述忠实度已按新口径重算（eval.rejudge）：以大模型裁判为准")
    res["notes"].append("汇报要素 F1 已按用例集 v1.1 的统一标注规则重算")
    from .selection import select_models

    res["selection"] = select_models(res)
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    write_report(REPORTS_DIR / run, res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
