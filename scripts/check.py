"""运行检查：数据库连接 → 模型 API 连通性 → 录制文件完整性 → 本体与关卡校验 → 前端产物。

    python scripts/check.py            # 本机直接运行（读取 .env）
每一项都打印“通过 / 未通过 / 跳过（原因）”，最后给出汇总。任何一项未通过返回非零退出码。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    mark = {"通过": "✓", "未通过": "✗", "跳过": "-"}[status]
    print(f"{mark} {name}：{status}{('（' + detail + '）') if detail else ''}")


def check_db() -> None:
    from sqlalchemy import text

    from app.models.db import get_engine, init_db
    from app.settings import get_settings

    url = get_settings().database_url
    try:
        init_db(url)
        with get_engine(url).connect() as conn:
            conn.execute(text("select 1"))
        record("数据库连接", "通过", url.split("@")[-1] if "@" in url else url)
    except Exception as exc:  # noqa: BLE001
        record("数据库连接", "未通过", f"{type(exc).__name__}: {exc}")


def check_models() -> None:
    from app.gateway.gateway import ModelGateway, NoModelAvailable
    from app.settings import get_settings

    s = get_settings()
    if not s.api_key():
        record("模型 API 连通性", "跳过", f"未配置 API Key，当前按 {s.effective_mode} 模式运行")
        return
    import httpx

    try:
        r = httpx.get(s.base_url() + "/models", headers={"Authorization": f"Bearer {s.api_key()}"}, timeout=15)
        r.raise_for_status()
        ids = {m["id"] for m in r.json().get("data", [])}
    except Exception as exc:  # noqa: BLE001
        record("模型 API 连通性", "未通过", f"拉取模型清单失败：{exc}")
        return
    missing = [m for m in {v.get("model") for v in s.models["roles"].values()} if m and m not in ids]
    gw = ModelGateway(s)
    t0 = time.perf_counter()
    try:
        res = gw.chat("narrator", [{"role": "user", "content": "只回复两个字：收到"}], max_tokens=10, mode="online")
        ms = (time.perf_counter() - t0) * 1000
        record("模型 API 连通性", "通过" if not missing else "未通过",
               f"主持人模型 {res.model} 响应 {ms:.0f} ms" + (f"；平台上找不到：{missing}" if missing else ""))
    except NoModelAvailable as exc:
        record("模型 API 连通性", "未通过", str(exc)[:200])


def check_recordings() -> None:
    from app.settings import get_settings

    sys.path.insert(0, str(ROOT / "scripts"))
    from record_demo import REQUIRED

    d = get_settings().recordings_dir
    problems = []
    total = 0
    for name in REQUIRED:
        p = d / f"{name}.jsonl"
        if not p.exists():
            problems.append(f"缺少 {name}")
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                assert rec["key"] and rec["role"] and "response" in rec
                total += 1
            except Exception:  # noqa: BLE001
                problems.append(f"{name} 第 {i} 行损坏")
    mode = get_settings().effective_mode
    if problems:
        status = "未通过" if mode == "offline" else "跳过"
        record("录制文件完整性", status, "；".join(problems) + ("" if mode == "offline" else "（只有离线回放需要，用 scripts/record_demo.py 生成）"))
    else:
        record("录制文件完整性", "通过", f"{len(REQUIRED)} 个演示、{total} 条调用")


def check_content() -> None:
    from app.engine.ontology import load_content
    from app.engine.validator import validate_all

    rep = validate_all(load_content())
    bad = [sid for sid, r in rep["scenarios"].items() if not r["ok"]]
    record("本体与关卡校验", "通过" if rep["ok"] else "未通过",
           f"{rep['scenario_count']} 个关卡、{rep['content']['rules']} 条规则" + (f"；未通过：{bad}" if bad else ""))


def check_frontend() -> None:
    dist = ROOT / "frontend" / "dist" / "index.html"
    if dist.exists():
        record("前端产物", "通过", "frontend/dist 已构建")
    else:
        record("前端产物", "跳过", "缺少 frontend/dist，请在 frontend 文件夹运行 npm run build")


def main() -> int:
    print("== 运行检查 ==")
    for fn in (check_db, check_models, check_recordings, check_content, check_frontend):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            record(fn.__name__, "未通过", f"{type(exc).__name__}: {exc}")
    bad = [r for r in results if r[1] == "未通过"]
    print(f"\n汇总：通过 {sum(r[1] == '通过' for r in results)}，跳过 {sum(r[1] == '跳过' for r in results)}，未通过 {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
