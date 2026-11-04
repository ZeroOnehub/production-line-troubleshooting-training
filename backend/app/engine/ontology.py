"""本体与关卡加载。`content/` 是唯一事实来源：引擎、AI 工具定义、文档表格都从这里读取。

Pydantic 模型定义关卡剧本结构（关卡工坊生成的 YAML 用同一套模型校验）。
"""
from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCENARIO_TYPES = ("tour", "checklist", "changeover", "alarm", "shift")
REVIEWER_ROLES = ("equipment", "quality", "ehs")


def default_content_dir() -> Path:
    env = os.environ.get("CONTENT_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "content" / "ontology").is_dir():
            return parent / "content"
    return Path("/app/content")


# ---------------------------------------------------------------------------
# 剧本结构
# ---------------------------------------------------------------------------
class Step(BaseModel):
    """路径中的一步：动作 ID + 参数。YAML 写作 {action: inspect, target: bit}。"""

    model_config = ConfigDict(extra="forbid")
    action: str
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, v: Any) -> Any:
        if isinstance(v, str):
            return {"action": v, "params": {}}
        if isinstance(v, dict) and "params" not in v:
            v = dict(v)
            action = v.pop("action", None)
            return {"action": action, "params": v}
        return v

    def to_yaml(self) -> dict[str, Any]:
        return {"action": self.action, **self.params}

    def key(self) -> str:
        if not self.params:
            return self.action
        inner = ",".join(f"{k}={v}" for k, v in sorted(self.params.items()) if k != "elements")
        return f"{self.action}({inner})" if inner else self.action


class Clue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indicator: str | None = None
    component: str | None = None
    composite: str | None = None
    state: str | None = None
    value: str | None = None
    view: str | None = None


class RootCause(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    weight: float = Field(gt=0, le=1)
    in_operator_scope: bool
    setup: dict[str, Any]
    match: dict[str, Any] | None = None
    clues: list[Clue] = Field(default_factory=list)
    andon_target: Literal["maintenance", "quality", "team_leader"] | None = None
    repair: dict[str, Any] | None = None
    repair_first_piece: bool = True
    support: Literal["material"] | None = None
    surroundings: str | None = None
    direction: str | None = None
    leak_keywords: list[str] = Field(default_factory=list)
    correct_path: list[Step]

    @model_validator(mode="after")
    def _scope(self) -> "RootCause":
        if not self.in_operator_scope and not self.andon_target:
            raise ValueError(f"{self.id}：不在操作工权限内的根因必须写 andon_target")
        return self


class Distractor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str
    text: str
    value: float | None = None


class NegativeCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    root_cause: str | None = None
    faults: list[str] | None = None
    path: list[Step]
    expect: dict[str, Any]


class CodexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    targets: list[str]
    desc: str


class CycleStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    text: str


class Fault(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    setup: dict[str, Any]
    in_operator_scope: bool
    andon_target: Literal["maintenance", "quality", "team_leader"] | None = None
    repair: dict[str, Any] | None = None
    fix_path: list[Step]


class ChecklistItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    standard: str
    check: list[Step]
    faults: list[Fault] = Field(default_factory=list)
    sign_item: bool = False


class ChangeoverStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    name: str
    variant: str | None = None
    program: str | None = None
    miss: dict[str, Any] | None = None

    def as_step(self) -> Step:
        params = {}
        if self.variant:
            params["variant"] = self.variant
        if self.program:
            params["program"] = self.program
        return Step(action=self.action, params=params)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    version: int = Field(ge=1)
    type: Literal["tour", "checklist", "changeover", "alarm", "shift"]
    layer: int = Field(ge=1, le=4)
    station: str
    title: str
    summary: str = ""
    story: str = ""
    objective: str = ""
    alarm: str | None = None
    event: dict[str, Any] | None = None
    root_causes: list[RootCause] = Field(default_factory=list)
    distractors: list[Distractor] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    negative_cases: list[NegativeCase] = Field(default_factory=list)
    author: str = ""
    reviewed_by: list[str] = Field(default_factory=list)
    # tour
    codex: list[CodexEntry] = Field(default_factory=list)
    cycle_steps: list[CycleStep] = Field(default_factory=list)
    # checklist
    initial: dict[str, Any] = Field(default_factory=dict)
    pick_faults: dict[str, int] | None = None
    items: list[ChecklistItem] = Field(default_factory=list)
    finish_path: list[Step] = Field(default_factory=list)
    # changeover
    from_variant: str | None = None
    to_variant: str | None = None
    steps: list[ChangeoverStep] = Field(default_factory=list)
    # shift
    real_time_limit_s: int | None = None
    target_pieces: int | None = None
    events: dict[str, Any] | None = None
    pass_: dict[str, Any] | None = Field(default=None, alias="pass")
    # 工坊附加信息（可选）
    source_draft: str | None = None
    changelog: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not v.startswith("SCN-"):
            raise ValueError("关卡 ID 必须以 SCN- 开头")
        return v

    @model_validator(mode="after")
    def _type_fields(self) -> "Scenario":
        if self.type == "alarm":
            if not self.alarm:
                raise ValueError("异常关卡必须写 alarm")
            if len(self.root_causes) < 2:
                raise ValueError("异常关卡至少 2 个根因")
            if len(self.distractors) < 2:
                raise ValueError("异常关卡至少 2 个干扰项")
            total = sum(rc.weight for rc in self.root_causes)
            if abs(total - 1.0) > 0.01:
                raise ValueError(f"根因权重之和应为 1，当前 {total:.2f}")
            ids = [rc.id for rc in self.root_causes]
            if len(set(ids)) != len(ids):
                raise ValueError("根因 ID 重复")
        if self.type == "checklist" and not self.items:
            raise ValueError("点检关卡必须写 items")
        if self.type == "changeover" and not self.steps:
            raise ValueError("换型关卡必须写 steps")
        if self.type == "tour" and not self.codex:
            raise ValueError("认识工位关卡必须写 codex")
        if self.type == "shift" and not self.events:
            raise ValueError("上一个班必须写 events")
        return self

    def rc(self, rc_id: str) -> RootCause:
        for rc in self.root_causes:
            if rc.id == rc_id:
                return rc
        raise KeyError(rc_id)

    def to_yaml_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_defaults=False, mode="json")


# ---------------------------------------------------------------------------
# 内容总表
# ---------------------------------------------------------------------------
def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def stable_hash(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class Content:
    """本体 + 关卡。所有索引在加载时建立。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        onto = self.root / "ontology"
        self.objects: dict[str, Any] = _load_yaml(onto / "objects.yaml")
        self.actions_doc: dict[str, Any] = _load_yaml(onto / "actions.yaml")
        self.rules_doc: dict[str, Any] = _load_yaml(onto / "rules.yaml")
        self.roles_doc: dict[str, Any] = _load_yaml(onto / "roles.yaml")
        self.alarms_doc: dict[str, Any] = _load_yaml(onto / "alarms.yaml")
        self.states_doc: dict[str, Any] = _load_yaml(onto / "states.yaml")
        self.scoring: dict[str, Any] = _load_yaml(onto / "scoring.yaml")

        self.station: dict[str, Any] = self.objects["station"]
        self.variants: dict[str, dict[str, Any]] = self.objects["variants"]
        self.air: dict[str, Any] = self.objects["air_pressure"]
        self.bit_life: dict[str, Any] = self.objects["bit_life"]
        self.components: dict[str, dict[str, Any]] = self.objects["components"]
        self.indicators: dict[str, dict[str, Any]] = self.objects["indicators"]
        self.composites: dict[str, dict[str, Any]] = self.objects["composite_targets"]
        self.equipment: dict[str, dict[str, Any]] = {e["id"]: e for e in self.objects["equipment"]}
        for eq in self.equipment.values():
            for cid in eq.get("components", []):
                self.components[cid].setdefault("equipment", eq["id"])

        self.actions: dict[str, dict[str, Any]] = {a["id"]: a for a in self.actions_doc["actions"]}
        self.rules: list[dict[str, Any]] = self.rules_doc["rules"]
        self.rules_by_id: dict[str, dict[str, Any]] = {r["id"]: r for r in self.rules}
        self.roles: dict[str, dict[str, Any]] = self.roles_doc["roles"]
        self.support: dict[str, Any] = self.roles_doc["support"]
        self.andon_targets: dict[str, dict[str, Any]] = self.roles_doc["andon_targets"]
        self.alarms: dict[str, dict[str, Any]] = {a["code"]: a for a in self.alarms_doc["alarms"]}
        self.session_states: dict[str, dict[str, Any]] = self.states_doc["session_states"]
        self.modes: dict[str, dict[str, Any]] = self.states_doc["equipment_modes"]
        self.weakness_tags: list[str] = self.scoring["weakness_tags"]

        self.scenarios: dict[str, Scenario] = {}
        self.scenario_errors: dict[str, str] = {}
        self.scenario_files: dict[str, Path] = {}
        for path in sorted((self.root / "scenarios").glob("SCN-*.yaml")):
            try:
                scn = Scenario.model_validate(_load_yaml(path))
                self.scenarios[scn.id] = scn
                self.scenario_files[scn.id] = path
            except Exception as exc:  # 记录错误，交给校验器报告
                self.scenario_errors[path.name] = str(exc)

        self.rc_index: dict[str, tuple[Scenario, RootCause]] = {}
        for scn in self.scenarios.values():
            for rc in scn.root_causes:
                self.rc_index[rc.id] = (scn, rc)
        # 配置不匹配目录（来自换型关卡的“漏步后果”）：引擎用它识别换型漏步类报警
        self.mismatch_catalog: list[dict[str, Any]] = []
        for scn in self.scenarios.values():
            if scn.type == "changeover":
                for st in scn.steps:
                    if st.miss and st.miss.get("fact"):
                        self.mismatch_catalog.append({"step": st, **st.miss})
        self._build_alias_index()

    # ------------------------------------------------------------------
    def _build_alias_index(self) -> None:
        """口语别名 → 目标 ID / 动作 ID。规则降级模式的意图解析与评测标注共用。"""
        self.target_aliases: dict[str, str] = {}
        for cid, c in self.components.items():
            if c.get("hidden"):
                continue
            for a in [c["name"], *c.get("aliases", [])]:
                self.target_aliases.setdefault(a, cid)
        for iid, ind in self.indicators.items():
            for a in [ind["name"], *ind.get("aliases", [])]:
                self.target_aliases.setdefault(a, iid)
        for tid, t in self.composites.items():
            for a in [t["name"], *t.get("aliases", [])]:
                self.target_aliases.setdefault(a, tid)
        for eid, e in self.equipment.items():
            for a in e.get("aliases", []):
                self.target_aliases.setdefault(a, eid)
        self.action_aliases: dict[str, str] = {}
        for aid, a in self.actions.items():
            for al in [a["name"], *a.get("aliases", [])]:
                self.action_aliases.setdefault(al, aid)

    # ------------------------------------------------------------------
    def inspect_targets(self) -> dict[str, str]:
        """可被“查看”的全部目标：ID → 名称。"""
        out: dict[str, str] = {}
        for cid, c in self.components.items():
            out[cid] = c["name"]
        for iid, i in self.indicators.items():
            out[iid] = i["name"]
        for tid, t in self.composites.items():
            out[tid] = t["name"]
        for eid, e in self.equipment.items():
            if not e.get("components") and e.get("text"):
                out[eid] = e["name"]
        return out

    def target_name(self, target: str) -> str:
        return self.inspect_targets().get(target, target)

    def operator_actions(self) -> list[dict[str, Any]]:
        """学员可尝试的动作（含越权动作，以便被识别并拒绝），不含系统动作。"""
        return [a for a in self.actions.values() if a["id"] != "andon_report"]

    def role_can(self, role: str, action_id: str) -> bool:
        r = self.roles[role]
        if action_id in r.get("deny", []):
            return False
        if action_id in r.get("allow", []):
            return True
        cat = self.actions[action_id]["category"]
        return cat in r.get("allow_categories", [])

    def rule_hashes(self, rule_ids: list[str] | None = None) -> dict[str, str]:
        ids = rule_ids if rule_ids is not None else list(self.rules_by_id)
        return {rid: stable_hash(self.rules_by_id[rid]) for rid in ids if rid in self.rules_by_id}

    def other_variant(self, v: str) -> str:
        others = [k for k in self.variants if k != v]
        return others[0] if others else v

    def scenario_layer(self, scn_id: str) -> int:
        return self.scenarios[scn_id].layer

    def with_scenario(self, scn: Scenario) -> "Content":
        """返回一个浅拷贝，其中 scn 替换（或新增）进关卡表。工坊校验草稿时用，不写盘。"""
        import copy as _copy

        clone = _copy.copy(self)
        clone.scenarios = dict(self.scenarios)
        clone.scenarios[scn.id] = scn
        clone.rc_index = {}
        for sc in clone.scenarios.values():
            for rc in sc.root_causes:
                clone.rc_index[rc.id] = (sc, rc)
        clone.mismatch_catalog = []
        for sc in clone.scenarios.values():
            if sc.type == "changeover":
                for st in sc.steps:
                    if st.miss and st.miss.get("fact"):
                        clone.mismatch_catalog.append({"step": st, **st.miss})
        return clone


@lru_cache(maxsize=4)
def _cached(root: str, stamp: float) -> Content:
    return Content(Path(root))


def content_stamp(root: Path) -> float:
    latest = 0.0
    for p in root.rglob("*.yaml"):
        try:
            latest = max(latest, p.stat().st_mtime)
        except FileNotFoundError:
            pass
    return latest


def load_content(root: Path | None = None) -> Content:
    """按文件修改时间缓存；工坊发布新版本后自动重新加载。"""
    root = Path(root or default_content_dir())
    return _cached(str(root), content_stamp(root))
