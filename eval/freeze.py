"""冻结用例集：人工逐条审核完成后运行，manifest 标记为 frozen，版本号改为 v1（之后 build_cases 不再覆盖）。

    python -m eval.freeze --reviewer 你的名字
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json

from .common import CASES_DIR


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reviewer", default="")
    ap.add_argument("--waived", action="store_true", help="未逐条人工审核，直接冻结（如实记录）")
    ap.add_argument("--version", default="v1")
    args = ap.parse_args()
    p = CASES_DIR / "manifest.json"
    m = json.loads(p.read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    for f in sorted(CASES_DIR.glob("*.jsonl")):
        digest.update(f.read_bytes())
    status = (f"未逐条人工审核，直接冻结（{dt.date.today().isoformat()}）" if args.waived
              else f"已人工审核（{args.reviewer}，{dt.date.today().isoformat()}）")
    m.update({"version": args.version, "frozen": True, "review_status": status,
              "sha256": digest.hexdigest()})
    p.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"用例集冻结为 {args.version}，sha256={m['sha256'][:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
