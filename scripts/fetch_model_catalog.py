"""从 AIHubMix 公开接口拉取模型清单与价格，写入 config/model_catalog.json。

    python scripts/fetch_model_catalog.py            # 只保留 models.yaml 中用到的模型
    python scripts/fetch_model_catalog.py --all      # 保留全部

价格单位：美元 / 百万 token（语音按秒或按字符）。成本统计（单局成本、评测报告）只读这个文件，不手填。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
LIST_URL = "https://aihubmix.com/v1/models"
META_URL = "https://aihubmix.com/api/v1/models"


def fetch(url: str) -> dict:
    resp = httpx.get(url, headers={"User-Agent": "production-line-training/1.0"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def wanted_ids(cfg: dict) -> set[str]:
    ids = {c["id"] for c in cfg.get("candidates", [])}
    for r in cfg.get("roles", {}).values():
        if r.get("model"):
            ids.add(r["model"])
    v = cfg.get("voice", {})
    for key in ("asr", "tts"):
        if v.get(key, {}).get("model"):
            ids.add(v[key]["model"])
        if v.get(key, {}).get("fallback_model"):
            ids.add(v[key]["fallback_model"])
    return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "config" / "model_catalog.json"))
    args = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
    listed = {m["id"] for m in fetch(LIST_URL).get("data", [])}
    meta = fetch(META_URL).get("data", [])
    want = wanted_ids(cfg)
    models = {}
    for m in meta:
        mid = m.get("model_id")
        if not args.all and mid not in want:
            continue
        lines = {ln["billable"]: ln["cost_usd"] for ln in (m.get("pricing_lines") or [])}
        pricing = m.get("pricing") or {}
        models[mid] = {
            "name": m.get("model_name"),
            "types": m.get("types"),
            "listed_in_v1_models": mid in listed,
            "open_weights": m.get("open_weights"),
            "tool_call": m.get("tool_call"),
            "context_length": m.get("context_length"),
            "input_per_mtok": pricing.get("input", lines.get("prompt_tokens")),
            "output_per_mtok": pricing.get("output", lines.get("completion_tokens")),
            "audio_input_per_second": lines.get("audio_input_seconds"),
            "per_input_character": lines.get("input_characters"),
            "pricing_source": m.get("pricing_source"),
            "release_date": m.get("release_date"),
        }
    missing = sorted(want - set(models))
    out = {
        "source": META_URL,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "currency": "USD",
        "unit": "per million tokens (audio: per second; tts: per character)",
        "models": models,
        "missing_from_platform": missing,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写入 {args.out}：{len(models)} 个模型；平台上缺少：{missing or '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
