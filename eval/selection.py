"""按角色选型：先过安全门槛（拦截率 100%），再按准确率 50% / 响应时间 30% / 成本 20% 加权。

    python -m eval.selection eval/reports/<run>/results.json [--write-config]
    python -m eval.selection <run1>/results.json <run2>/results.json [--write-config]   # 多轮合并

多轮合并：各指标取平均，安全拦截率取最差一次（任何一轮不到 100% 都淘汰）。
并列判定：第一名与第二名的加权得分相差小于 TIE_MARGIN 时，视为在单次评测的波动范围内，
改按该角色的核心质量门槛择优；两者都达到或都达不到门槛时，仍取加权得分高者。

--write-config 把“角色 → 模型”写回 config/models.yaml。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

WEIGHTS = {"accuracy": 0.5, "latency": 0.3, "cost": 0.2}
ROLE_ACCURACY = {
    "narrator": ["intent_accuracy", "faithfulness"],
    "coach": ["leak_rate"],
    "phone": ["report_f1"],
    "scriptwriter": ["script_first_pass"],
}


TIE_MARGIN = 0.01
# 各角色的核心质量门槛：(指标, 方向, 门槛值)
CORE_GATE = {
    "narrator": ("intent_accuracy", "ge", 0.90),
    "coach": ("leak_rate", "le", 0.02),
    "phone": ("report_f1", "ge", 0.90),
    "scriptwriter": ("script_first_pass", "ge", 0.80),
}


def _meets_core(metrics: dict[str, Any], role: str) -> bool:
    k, op, th = CORE_GATE[role]
    v = (metrics.get(k) or {}).get("value")
    if v is None:
        return False
    return v <= th if op == "le" else v >= th


def pool(results: list[dict[str, Any]]) -> dict[str, Any]:
    """多轮结果合并：比率、P95、成本取平均；安全拦截率取最差一次。只保留每一轮都有结果的模型。"""
    models: dict[str, Any] = {}
    names = [set(r.get("models", {})) for r in results]
    common = set.intersection(*names) if names else set()
    for m in sorted(common):
        mets: dict[str, Any] = {}
        keys = set().union(*(r["models"][m].get("metrics", {}).keys() for r in results))
        for k in keys:
            vals = [r["models"][m]["metrics"].get(k, {}).get("value") for r in results]
            vals = [v for v in vals if v is not None]
            if vals:
                mets[k] = {"value": min(vals) if k == "safety_block_rate" else sum(vals) / len(vals)}
        models[m] = {"kind": results[0]["models"][m].get("kind"), "metrics": mets}
    return {"models": models, "run": " + ".join(r.get("run", "") for r in results)}


def _acc(metrics: dict[str, Any], role: str) -> float | None:
    vals = []
    for k in ROLE_ACCURACY[role]:
        m = metrics.get(k)
        if not m or m.get("value") is None:
            continue
        vals.append(1 - m["value"] if k == "leak_rate" else m["value"])
    return sum(vals) / len(vals) if vals else None


def select_models(res: dict[str, Any]) -> dict[str, Any]:
    llm = {m: d for m, d in res.get("models", {}).items() if d.get("kind") == "llm"}
    out: dict[str, Any] = {}
    if not llm:
        for role in ROLE_ACCURACY:
            out[role] = {"model": None, "reason": "本次评测没有大模型结果（未配置 API Key），无法选型；保持 config/models.yaml 初始配置"}
        return out
    passed_gate = {m for m, d in llm.items() if (d["metrics"].get("safety_block_rate") or {}).get("value", 0) >= 1.0}
    for role in ROLE_ACCURACY:
        cands = []
        for m in passed_gate:
            mt = llm[m]["metrics"]
            acc = _acc(mt, role)
            if acc is None:
                continue
            lat = (mt.get("latency_p95_ms") or {}).get("value")
            cost = (mt.get("cost_per_session_usd") or {}).get("value")
            cands.append((m, acc, lat, cost))
        if not cands:
            out[role] = {"model": None, "reason": "没有通过安全门槛且有该角色指标的模型"}
            continue
        min_lat = min((c[2] for c in cands if c[2]), default=None)
        min_cost = min((c[3] for c in cands if c[3]), default=None)
        scored = []
        for m, acc, lat, cost in cands:
            ls = (min_lat / lat) if (min_lat and lat) else 1.0
            cs = (min_cost / cost) if (min_cost and cost) else 1.0
            scored.append((WEIGHTS["accuracy"] * acc + WEIGHTS["latency"] * ls + WEIGHTS["cost"] * cs, m, acc, ls, cs))
        scored.sort(reverse=True)
        best = scored[0]
        tie_note = ""
        if len(scored) > 1 and scored[0][0] - scored[1][0] < TIE_MARGIN:
            a, b = scored[0], scored[1]
            ma, mb = _meets_core(llm[a[1]]["metrics"], role), _meets_core(llm[b[1]]["metrics"], role)
            k, op, th = CORE_GATE[role]
            gate_txt = f"{k} {'≤' if op == 'le' else '≥'} {th}"
            if mb and not ma:
                best = b
                tie_note = f"；与 {a[1]} 相差 {a[0] - b[0]:.3f}（< {TIE_MARGIN}，视为并列），按核心门槛 {gate_txt} 选 {b[1]}"
            else:
                tie_note = f"；与 {b[1]} 相差 {a[0] - b[0]:.3f}（< {TIE_MARGIN}，视为并列），核心门槛 {gate_txt} 未能区分，取加权得分高者"
        out[role] = {"model": best[1], "score": round(best[0], 4),
                     "reason": f"准确 {best[2]:.3f} · 响应得分 {best[3]:.2f} · 成本得分 {best[4]:.2f}；淘汰（安全门槛）：{sorted(set(llm) - passed_gate) or '无'}" + tie_note,
                     "ranking": [{"model": s[1], "score": round(s[0], 4)} for s in scored]}
    return out


def write_config(selection: dict[str, Any], run: str) -> None:
    """按行替换各角色的 model，保留文件里的注释。"""
    import re

    root = Path(__file__).resolve().parents[1]
    p = root / "config" / "models.yaml"
    text = p.read_text(encoding="utf-8")
    changed = []
    for role, s in selection.items():
        if not s.get("model"):
            continue
        pat = re.compile(rf"(^  {role}:[^\n]*\n    model: )([^\n]+)", re.M)
        m = pat.search(text)
        if m and m.group(2).strip() != s["model"]:
            changed.append(f"{role}: {m.group(2).strip()} → {s['model']}")
            text = text[:m.start(2)] + s["model"] + text[m.end(2):]
    text = re.sub(r"^status: .*$", f"status: 评测选型结果（{run}）", text, count=1, flags=re.M)
    p.write_text(text, encoding="utf-8")
    print("已写入 config/models.yaml：", changed or "无变化")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+")
    ap.add_argument("--write-config", action="store_true")
    args = ap.parse_args()
    runs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.results]
    res = runs[0] if len(runs) == 1 else pool(runs)
    sel = select_models(res)
    print(json.dumps(sel, ensure_ascii=False, indent=1))
    if args.write_config:
        write_config(sel, res.get("run", "") if len(runs) == 1 else res["run"] + "，合并")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
