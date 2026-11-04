"""数据库模型（SQLModel）。PostgreSQL 为生产形态；本地无 Docker 时可用 SQLite（DATABASE_URL=sqlite:///...）。

训练局的完整引擎状态以 JSON 存在 TrainingSession.state_json 中（引擎状态可序列化、可复现）；
操作记录、违规、提示、汇报、调用日志另存成行，供看板和评测统计。
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Optional

from sqlalchemy import JSON, Column, Text
from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Session, SQLModel, create_engine


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def aware(t: dt.datetime) -> dt.datetime:
    """SQLite 读回来的时间可能不带时区，统一按 UTC 处理。"""
    return t if t.tzinfo is not None else t.replace(tzinfo=dt.timezone.utc)


def uid() -> str:
    return uuid.uuid4().hex[:12]


class Team(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    is_demo: bool = False


class Trainee(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    team_id: Optional[int] = Field(default=None, foreign_key="team.id")
    created_at: dt.datetime = Field(default_factory=now)
    unlock_all: bool = False
    is_demo: bool = Field(default=False, index=True)


class TrainingSession(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    trainee_id: int = Field(foreign_key="trainee.id", index=True)
    scenario_id: str = Field(index=True)
    scenario_version: int = 1
    scenario_type: str = ""
    seed: int = 0
    root_cause: Optional[str] = None
    state: str = "created"
    ai_mode: str = "online"
    started_at: dt.datetime = Field(default_factory=now)
    ended_at: Optional[dt.datetime] = None
    last_action_at: dt.datetime = Field(default_factory=now)
    sim_clock_s: float = 0.0
    downtime_s: float = 0.0
    loss_yuan: float = 0.0
    passed: Optional[bool] = None
    total_score: Optional[float] = None
    hints_used: int = 0
    scores: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    review: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    state_json: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    is_demo: bool = Field(default=False, index=True)


class ActionLogRow(SQLModel, table=True):
    __tablename__ = "action_log"
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    seq: int
    t_sim: float
    created_at: dt.datetime = Field(default_factory=now)
    action: str
    params: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    actor: str = "trainee"
    source: str = "click"
    result: str = "ok"
    message: str = Field(default="", sa_column=Column(Text))
    violations: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))
    state_before: str = ""
    state_after: str = ""
    is_demo: bool = False


class ViolationRow(SQLModel, table=True):
    __tablename__ = "violation"
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    trainee_id: int = Field(index=True)
    scenario_id: str = Field(index=True)
    rule_id: str = Field(index=True)
    rule_type: str = ""
    severity: str = ""
    dimension: str = ""
    tag: str = ""
    outcome: str = ""
    t_sim: float = 0.0
    created_at: dt.datetime = Field(default_factory=now)
    is_demo: bool = Field(default=False, index=True)


class AndonReportRow(SQLModel, table=True):
    __tablename__ = "andon_report"
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    target: str
    content: str = Field(default="", sa_column=Column(Text))
    elements: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    covered: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    coverage: float = 0.0
    follow_ups: int = 0
    delay_s: float = 0.0
    created_at: dt.datetime = Field(default_factory=now)
    is_demo: bool = False


class HintLogRow(SQLModel, table=True):
    __tablename__ = "hint_log"
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    level: int
    text: str = Field(default="", sa_column=Column(Text))
    question: Optional[str] = None
    mode: str = ""
    t_sim: float = 0.0
    created_at: dt.datetime = Field(default_factory=now)
    is_demo: bool = False


class LLMCallRow(SQLModel, table=True):
    __tablename__ = "llm_call"
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: dt.datetime = Field(default_factory=now, index=True)
    role: str = Field(index=True)
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    session_id: Optional[str] = Field(default=None, index=True)
    mode: str = ""
    ok: bool = True
    error: Optional[str] = Field(default=None, sa_column=Column(Text))
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: Optional[float] = None
    cost_usd: Optional[float] = None
    match: Optional[str] = None
    tags: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))


class ScenarioVersionRow(SQLModel, table=True):
    __tablename__ = "scenario_version"
    id: Optional[int] = Field(default=None, primary_key=True)
    scenario_id: str = Field(index=True)
    version: int
    title: str = ""
    yaml_text: str = Field(default="", sa_column=Column(Text))
    status: str = "active"            # active / archived / retired
    published_at: dt.datetime = Field(default_factory=now)
    source: str = ""                  # 初始内容 / 工坊草稿 DRAFT-xxx / 回滚
    source_draft_id: Optional[str] = None
    rule_hashes: Optional[dict[str, str]] = Field(default=None, sa_column=Column(JSON))
    needs_recheck: bool = False
    recheck_reason: Optional[str] = Field(default=None, sa_column=Column(Text))
    author: str = ""


class ScenarioDraftRow(SQLModel, table=True):
    __tablename__ = "scenario_draft"
    id: str = Field(default_factory=lambda: "DRAFT-" + uid()[:6].upper(), primary_key=True)
    title: str = ""
    source_type: str = "text"         # text / audio
    source_text: str = Field(default="", sa_column=Column(Text))
    attachments: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))
    target_scenario_id: Optional[str] = None
    status: str = "collecting"
    version: int = 0
    yaml_text: str = Field(default="", sa_column=Column(Text))
    explanation: str = Field(default="", sa_column=Column(Text))
    gen_mode: str = ""
    gen_model: Optional[str] = None
    validation: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    first_validation_ok: Optional[bool] = None
    timeline: Optional[list[dict[str, Any]]] = Field(default=None, sa_column=Column(JSON))
    created_at: dt.datetime = Field(default_factory=now)
    updated_at: dt.datetime = Field(default_factory=now)
    published_at: Optional[dt.datetime] = None
    published_scenario_id: Optional[str] = None
    published_version: Optional[int] = None
    author: str = ""
    is_demo: bool = False


class ReviewRow(SQLModel, table=True):
    __tablename__ = "review"
    id: Optional[int] = Field(default=None, primary_key=True)
    draft_id: str = Field(index=True)
    draft_version: int
    reviewer_role: str                # equipment / quality / ehs
    reviewer_name: str = ""
    verdict: str                      # pass / reject
    comments: str = Field(default="", sa_column=Column(Text))
    created_at: dt.datetime = Field(default_factory=now)


class DowntimeRecordRow(SQLModel, table=True):
    __tablename__ = "downtime_record"
    id: Optional[int] = Field(default=None, primary_key=True)
    date: str
    station: str
    alarm_code: str = Field(index=True)
    duration_min: float
    description: str = Field(default="", sa_column=Column(Text))
    is_demo: bool = True


_engines: dict[str, Any] = {}


def get_engine(url: str):
    if url not in _engines:
        if url.startswith("sqlite"):
            kw: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
            if url in ("sqlite://", "sqlite:///:memory:"):
                kw["poolclass"] = StaticPool
            _engines[url] = create_engine(url, **kw)
        else:
            _engines[url] = create_engine(url, pool_pre_ping=True)
    return _engines[url]


def init_db(url: str) -> None:
    if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
        from pathlib import Path

        Path(url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(get_engine(url))


def session_scope(url: str) -> Session:
    return Session(get_engine(url), expire_on_commit=False)
