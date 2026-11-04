"""评测台公共部分：加载用例、建立被测系统（与训练端同一套 AI 编排和网关）。"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.roles import Roles  # noqa: E402
from app.engine.engine import Engine  # noqa: E402
from app.engine.ontology import load_content  # noqa: E402
from app.gateway.gateway import ModelGateway  # noqa: E402
from app.settings import Settings, get_settings  # noqa: E402

CASES_DIR = ROOT / "eval" / "cases"
REPORTS_DIR = ROOT / "eval" / "reports"
RULE_BASELINE = "rule-baseline"

CATEGORY_ROLE = {
    "intent": "narrator", "colloquial": "narrator", "safety": "narrator", "faithfulness": "narrator",
    "hint": "coach", "report": "phone", "script": "scriptwriter",
}
CATEGORY_NAME = {
    "intent": "意图解析", "colloquial": "口语与转写变体", "safety": "越权与安全拦截", "hint": "提示合规",
    "faithfulness": "描述忠实度", "report": "汇报要素抽取", "script": "剧本生成",
}


def split_of(case_id: str) -> str:
    """开发集 / 测试集按用例 ID 的哈希固定划分（约 4:6）。提示词只在开发集上调，报告只报测试集。"""
    h = int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % 10
    return "dev" if h < 4 else "test"


def load_cases(categories: list[str] | None = None, split: str = "test") -> list[dict[str, Any]]:
    out = []
    for f in sorted(CASES_DIR.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            if categories and c["category"] not in categories:
                continue
            c.setdefault("split", split_of(c["id"]))
            if split != "all" and c["split"] != split:
                continue
            out.append(c)
    return out


def case_set_info() -> dict[str, Any]:
    p = CASES_DIR / "manifest.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"version": "unversioned", "review_status": "unknown"}


@dataclass
class System:
    model: str
    gw: ModelGateway
    roles: Roles
    engine: Engine

    @property
    def kind(self) -> str:
        return "rules" if self.model == RULE_BASELINE else "llm"


def make_system(model: str, settings: Settings | None = None, judge: bool = False) -> System:
    """被测系统：与训练端完全相同的 Roles + Engine，只是把所有角色的模型换成被测模型。"""
    base = settings or get_settings()
    content = load_content(base.content_dir)
    models = json.loads(json.dumps(base.models))
    if model != RULE_BASELINE:
        for role in ("narrator", "coach", "phone", "examiner", "scriptwriter"):
            if models["roles"].get(role) is not None:
                models["roles"][role]["model"] = model
    s = Settings(root=base.root, app_mode="degraded" if model == RULE_BASELINE else "online", record_calls=False,
                 database_url="sqlite://", content_dir=base.content_dir, recordings_dir=base.recordings_dir,
                 logs_dir=base.root / "eval" / "reports" / "_logs", models=models, catalog=base.catalog)
    gw = ModelGateway(s)
    engine = Engine(content)
    return System(model=model, gw=gw, roles=Roles(engine, gw), engine=engine)


def prepare_session(engine: Engine, ctx: dict[str, Any]):
    """按用例上下文开局并推进到指定状态。"""
    s = engine.new_session(ctx.get("scenario", "SCN-T101"), seed=int(ctx.get("seed", 7)),
                           root_cause=ctx.get("root_cause"), faults=ctx.get("faults"), compute_standard=False)
    for st in ctx.get("prefix", [["run_production"]]):
        action, params = st[0], (st[1] if len(st) > 1 else {})
        actor = "phone" if action == "andon_report" else "trainee"
        engine.act(s, action, params, actor=actor, source="eval")
    return s
