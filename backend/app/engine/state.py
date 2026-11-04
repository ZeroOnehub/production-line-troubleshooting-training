"""训练局状态（可序列化为 JSON 存入数据库，回放时完全可复现）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Piece(BaseModel):
    no: int
    barcode: str
    variant: str
    result: str | None = None          # ok / torque_low / angle_out
    is_ng: bool = False
    nonconforming: bool = False
    defects: dict[str, str] = Field(default_factory=dict)  # 只属于这一件的部件状态（标签、已拧螺栓、放置）
    first_piece: bool = False
    confirmed: bool = True
    scanned: bool = False
    location: str = "fixture"          # fixture / ng_bin / downstream
    labeled: bool = False
    episode: int | None = None


class Violation(BaseModel):
    rule_id: str
    t: float
    seq: int
    action: str | None
    episode: int | None
    outcome: str
    severity: str
    dimension: str
    deduction: float
    tag: str
    type: str
    message: str


class LogEntry(BaseModel):
    seq: int
    t: float
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    actor: str = "trainee"             # trainee / system / maintenance / quality / team_leader / material_handler
    source: str = "click"              # click / nl / autopilot / system / replay
    result: str = "ok"                 # ok / rejected / violation / terminated
    message: str = ""
    violations: list[str] = Field(default_factory=list)
    state_before: str = ""
    state_after: str = ""
    episode: int | None = None
    observation: dict[str, Any] | None = None
    downtime: float = 0.0


class Episode(BaseModel):
    id: int
    kind: str                          # alarm / changeover / checklist
    alarm: str | None = None
    issue_id: str | None = None
    started_at: float = 0.0
    ended_at: float | None = None
    recurrence: int = 0
    reset_since_alarm: bool = False
    inspected_any: bool = False
    alarm_viewed: bool = False
    hand_valve_inspected: bool = False
    andon_pulled: bool = False
    ng_count: int = 0
    actions_done: list[str] = Field(default_factory=list)
    once_rules: list[str] = Field(default_factory=list)


class Issue(BaseModel):
    """当前要处理的问题，以及它的标准处置路径（复盘对比、三级提示“示范”、自动回放都用它）。"""

    id: str
    kind: str                          # rc / mismatch / changeover / checklist / tour / self_estop / unknown
    name: str = ""
    alarm: str | None = None
    in_scope: bool = True
    andon_target: str | None = None
    support: str | None = None
    repair: dict[str, Any] = Field(default_factory=dict)
    repair_first_piece: bool = True
    path: list[dict[str, Any]] = Field(default_factory=list)   # [{action, params}]
    done: list[bool] = Field(default_factory=list)
    strict_order: bool = False
    parent: "Issue | None" = None


class Support(BaseModel):
    kind: str                          # andon / material
    target: str
    called_at: float
    reported: bool = False
    report: dict[str, Any] | None = None
    coverage: float | None = None
    follow_ups: int = 0
    arrival_at: float | None = None
    transcript: list[dict[str, str]] = Field(default_factory=list)
    partial: dict[str, Any] = Field(default_factory=dict)   # 电话里已经说清的要素（追问前的累计）


class SessionState(BaseModel):
    scenario_id: str
    scenario_version: int
    scenario_type: str
    seed: int
    state: str = "created"
    mode: str = "running"
    clock: float = 0.0
    downtime: float = 0.0
    downtime_since: float | None = None
    components: dict[str, str] = Field(default_factory=dict)
    hmi: dict[str, Any] = Field(default_factory=dict)
    order_variant: str = "A"
    bolt_box_variant: str = "A"
    piece_counter: int = 0
    current_piece: Piece | None = None
    pieces: list[Piece] = Field(default_factory=list)
    alarm: str | None = None
    recurrence: dict[str, int] = Field(default_factory=dict)
    episodes: list[Episode] = Field(default_factory=list)
    current_episode: int | None = None
    first_piece_pending: bool = False
    unconfirmed_count: int = 0
    ng_escaped: int = 0
    ng_bin_count: int = 0
    first_piece_count: int = 0
    safety_checked: bool = True
    bolt_box_isolated: bool = False
    pending_piece_defects: dict[str, str] = Field(default_factory=dict)
    changeover: dict[str, Any] = Field(default_factory=lambda: {
        "active": False, "to_variant": None, "verifying": False, "verified": False,
        "quality_pending": False, "quality_confirmed": False, "done": False, "ordered": False,
    })
    checklist: dict[str, Any] = Field(default_factory=lambda: {"items": {}, "faults": [], "signed": False})
    codex: list[str] = Field(default_factory=list)
    cycle_step_index: int = 0
    support: Support | None = None
    support_history: list[Support] = Field(default_factory=list)
    andon_reports: list[dict[str, Any]] = Field(default_factory=list)
    schedule: list[dict[str, Any]] = Field(default_factory=list)
    root_cause: str | None = None       # 单个异常关卡抽中的根因
    active_rc: str | None = None        # 当前注入的根因（上一个班中会变化）
    issue: Issue | None = None
    issue_history: list[dict[str, Any]] = Field(default_factory=list)
    distractors: dict[str, str] = Field(default_factory=dict)
    pressure_override: float | None = None
    surroundings: str | None = None
    log: list[LogEntry] = Field(default_factory=list)
    violations: list[Violation] = Field(default_factory=list)
    hints: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    objective_met: bool = False
    failed: bool = False
    fail_reasons: list[str] = Field(default_factory=list)
    terminated_by: str | None = None
    standard: dict[str, Any] = Field(default_factory=dict)
    target_pieces: int | None = None
    produced_ok: int = 0
    shift_ended: bool = False
    seq: int = 0


Issue.model_rebuild()
