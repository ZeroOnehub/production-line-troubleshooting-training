"""服务层：训练局、关卡工坊、看板统计。接口层（api/）只做参数转换。"""
from __future__ import annotations

import csv
import datetime as dt
import difflib
import io
import json
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml
from sqlmodel import Session, select

from .ai.roles import Roles
from .engine.engine import Engine, EngineError
from .engine.ontology import Content, Scenario, load_content, stable_hash
from .engine.scoring import build_review, compute_scores
from .engine.state import SessionState
from .engine.validator import validate_yaml_text
from .gateway.gateway import ModelGateway, get_gateway
from .models.db import (ActionLogRow, AndonReportRow, DowntimeRecordRow, HintLogRow, LLMCallRow, ReviewRow, ScenarioDraftRow,
                        ScenarioVersionRow, Team, Trainee, TrainingSession, ViolationRow, aware, init_db, now, session_scope)
from .settings import Settings, get_settings
from .voice.adapters import VoiceService

_SESSION_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Runtime:
    def __init__(self, settings: Settings | None = None, gateway: ModelGateway | None = None):
        self.settings = settings or get_settings()
        self.gw = gateway or get_gateway()
        self.db_url = self.settings.database_url
        init_db(self.db_url)
        self.gw.sinks.append(self._llm_sink)
        self.voice = VoiceService(self.gw)
        self._content: Content | None = None
        self._engine: Engine | None = None
        self._roles: Roles | None = None
        self.refresh_content()
        self.ensure_initial_versions()

    # ------------------------------------------------------------------
    def refresh_content(self) -> None:
        self._content = load_content(self.settings.content_dir)
        self._engine = Engine(self._content)
        self._roles = Roles(self._engine, self.gw)

    @property
    def content(self) -> Content:
        latest = load_content(self.settings.content_dir)
        if latest is not self._content:
            self._content = latest
            self._engine = Engine(latest)
            self._roles = Roles(self._engine, self.gw)
        assert self._content is not None
        return self._content

    @property
    def engine(self) -> Engine:
        _ = self.content
        assert self._engine is not None
        return self._engine

    @property
    def roles(self) -> Roles:
        _ = self.content
        assert self._roles is not None
        return self._roles

    def db(self) -> Session:
        return session_scope(self.db_url)

    def _llm_sink(self, rec: dict[str, Any]) -> None:
        with self.db() as db:
            db.add(LLMCallRow(role=rec.get("role", ""), model=rec.get("model"), prompt_version=rec.get("prompt_version"),
                              session_id=rec.get("session_id"), mode=rec.get("mode", ""), ok=bool(rec.get("ok")),
                              error=rec.get("error"), input_tokens=int(rec.get("input_tokens") or 0),
                              output_tokens=int(rec.get("output_tokens") or 0), latency_ms=rec.get("latency_ms"),
                              cost_usd=rec.get("cost_usd"), match=rec.get("match"), tags=rec.get("tags")))
            db.commit()

    def meta(self) -> dict[str, Any]:
        c = self.content
        from .ai.prompts import prompt_versions

        mode = self.gw.mode
        label = {"online": "在线", "offline": "离线回放", "degraded": "降级·规则"}.get(mode, mode)
        if self.gw.online_unhealthy():
            # 断网或平台故障：各角色正在逐次走规则兜底，界面如实显示（下一次调用成功后自动恢复“在线”）
            mode, label = "degraded", "降级·规则（模型暂不可用）"
        return {
            "mode": mode, "mode_label": label, "configured_mode": self.settings.app_mode,
            "has_api_key": bool(self.settings.api_key()), "record_calls": self.settings.record_calls,
            "roles": {k: v.get("model") for k, v in self.settings.models.get("roles", {}).items()},
            "models_status": self.settings.models.get("status"),
            "prompt_versions": prompt_versions(),
            "content": {"scenarios": len(c.scenarios), "rules": len(c.rules), "actions": len(c.actions),
                        "components": len(c.components), "factory": c.objects.get("factory")},
            "recordings": self.gw.store.stats(),
            "demo_unlock_all": self.settings.demo_unlock_all,
        }

    # ==================================================================
    # 学员与关卡地图
    # ==================================================================
    def list_trainees(self, include_demo: bool = False) -> list[dict[str, Any]]:
        with self.db() as db:
            q = select(Trainee)
            if not include_demo:
                q = q.where(Trainee.is_demo == False)  # noqa: E712
            rows = db.exec(q.order_by(Trainee.id)).all()
            teams = {t.id: t.name for t in db.exec(select(Team)).all()}
            return [{"id": t.id, "name": t.name, "team": teams.get(t.team_id), "is_demo": t.is_demo,
                     "rank": self.progress(t.id)["rank"]["name"]} for t in rows]

    def create_trainee(self, name: str, team: str | None = None) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ServiceError("学员名称不能为空")
        with self.db() as db:
            team_id = None
            if team:
                t = db.exec(select(Team).where(Team.name == team, Team.is_demo == False)).first()  # noqa: E712
                if t is None:
                    t = Team(name=team)
                    db.add(t)
                    db.commit()
                    db.refresh(t)
                team_id = t.id
            tr = Trainee(name=name, team_id=team_id)
            db.add(tr)
            db.commit()
            db.refresh(tr)
            return {"id": tr.id, "name": tr.name, "team": team}

    def set_unlock_all(self, trainee_id: int, value: bool) -> None:
        with self.db() as db:
            tr = db.get(Trainee, trainee_id)
            if tr is None:
                raise ServiceError("学员不存在", 404)
            tr.unlock_all = value
            db.add(tr)
            db.commit()

    def _passed_set(self, db: Session, trainee_id: int) -> dict[str, dict[str, Any]]:
        rows = db.exec(select(TrainingSession).where(TrainingSession.trainee_id == trainee_id)).all()
        best: dict[str, dict[str, Any]] = {}
        for r in rows:
            b = best.setdefault(r.scenario_id, {"attempts": 0, "passed": False, "best": None, "stars": 0})
            b["attempts"] += 1
            if r.passed:
                b["passed"] = True
            if r.total_score is not None and (b["best"] is None or r.total_score > b["best"]):
                b["best"] = r.total_score
                b["stars"] = (r.scores or {}).get("stars", 0) if r.passed else b["stars"]
        return best

    def _meets(self, req: dict[str, Any] | None, passed: dict[str, dict[str, Any]], rank_idx: int, ranks: list[dict[str, Any]]) -> bool:
        if not req:
            return True
        if "passed_all" in req:
            return all(passed.get(s, {}).get("passed") for s in req["passed_all"])
        if "passed_count" in req:
            pc = req["passed_count"]
            layer = next(l for l in self.content.scoring["layers"] if l["layer"] == pc["layer"])
            return sum(1 for s in layer["scenarios"] if passed.get(s, {}).get("passed")) >= int(pc["min"])
        if "rank" in req:
            idx = [r["id"] for r in ranks].index(req["rank"])
            return rank_idx >= idx
        return False

    def progress(self, trainee_id: int) -> dict[str, Any]:
        c = self.content
        sc = c.scoring
        with self.db() as db:
            tr = db.get(Trainee, trainee_id)
            if tr is None:
                raise ServiceError("学员不存在", 404)
            passed = self._passed_set(db, trainee_id)
        ranks = sc["ranks"]
        rank_idx = 0
        for i, r in enumerate(ranks):
            if self._meets(r.get("requires"), passed, i, ranks):
                rank_idx = i
            else:
                break
        unlock_all = tr.unlock_all or self.settings.demo_unlock_all
        layers = []
        for layer in sc["layers"]:
            req = sc["unlock"].get(layer["layer"]) if isinstance(sc["unlock"], dict) else None
            req = sc["unlock"].get(str(layer["layer"]), req) if isinstance(sc["unlock"], dict) else req
            unlocked = unlock_all or self._meets(req, passed, rank_idx, ranks)
            items = []
            extra = sorted(sid for sid, scn in c.scenarios.items() if scn.layer == layer["layer"] and sid not in layer["scenarios"])
            for sid in list(layer["scenarios"]) + extra:   # 工坊新发布的关卡按 layer 字段并入
                scn = c.scenarios.get(sid)
                if scn is None:
                    continue
                p = passed.get(sid, {})
                items.append({"id": sid, "title": scn.title, "type": scn.type, "alarm": scn.alarm, "summary": scn.summary,
                              "version": scn.version, "unlocked": unlocked, "passed": p.get("passed", False),
                              "attempts": p.get("attempts", 0), "best": p.get("best"), "stars": p.get("stars", 0)})
            layers.append({"layer": layer["layer"], "name": layer["name"], "unlocked": unlocked, "scenarios": items})
        nxt = ranks[rank_idx + 1] if rank_idx + 1 < len(ranks) else None
        return {"trainee": {"id": tr.id, "name": tr.name, "unlock_all": tr.unlock_all},
                "rank": {"id": ranks[rank_idx]["id"], "name": ranks[rank_idx]["name"], "index": rank_idx},
                "next_rank": nxt, "ranks": [r["name"] for r in ranks], "layers": layers}

    # ==================================================================
    # 训练局
    # ==================================================================
    def _load(self, db: Session, sid: str) -> tuple[TrainingSession, SessionState]:
        row = db.get(TrainingSession, sid)
        if row is None or row.state_json is None:
            raise ServiceError("训练局不存在", 404)
        return row, SessionState.model_validate(row.state_json)

    def _save(self, db: Session, row: TrainingSession, s: SessionState, from_seq: int) -> None:
        row.state = s.state
        row.sim_clock_s = s.clock
        row.downtime_s = s.downtime
        row.loss_yuan = self.engine.loss_yuan(s)
        row.hints_used = len(s.hints)
        row.last_action_at = now()
        row.state_json = json.loads(s.model_dump_json())
        for e in s.log:
            if e.seq > from_seq and e.actor != "system":
                db.add(ActionLogRow(session_id=row.id, seq=e.seq, t_sim=e.t, action=e.action, params=e.params, actor=e.actor,
                                    source=e.source, result=e.result, message=e.message, violations=e.violations,
                                    state_before=e.state_before, state_after=e.state_after, is_demo=row.is_demo))
        for v in s.violations:
            if v.seq > from_seq:
                db.add(ViolationRow(session_id=row.id, trainee_id=row.trainee_id, scenario_id=row.scenario_id, rule_id=v.rule_id,
                                    rule_type=v.type, severity=v.severity, dimension=v.dimension, tag=v.tag, outcome=v.outcome,
                                    t_sim=v.t, is_demo=row.is_demo))
        if s.state in ("review", "completed") and row.ended_at is None:
            row.ended_at = now()
            sc = compute_scores(self.content, s)
            row.passed = sc["passed"]
            row.total_score = sc["total"]
            row.scores = sc
        db.add(row)
        db.commit()

    def create_session(self, trainee_id: int, scenario_id: str, seed: int | None = None, root_cause: str | None = None,
                       faults: list[str] | None = None, is_demo: bool = False, check_unlock: bool = True) -> dict[str, Any]:
        c = self.content
        if scenario_id not in c.scenarios:
            raise ServiceError(f"关卡不存在：{scenario_id}", 404)
        if check_unlock and not is_demo:
            prog = self.progress(trainee_id)
            unlocked = {s["id"] for l in prog["layers"] for s in l["scenarios"] if s["unlocked"]}
            if scenario_id not in unlocked:
                raise ServiceError("该关卡尚未解锁", 403)
        try:
            s = self.engine.new_session(scenario_id, seed=seed, root_cause=root_cause, faults=faults)
        except (EngineError, KeyError) as exc:
            raise ServiceError(str(exc)) from exc
        with self.db() as db:
            row = TrainingSession(trainee_id=trainee_id, scenario_id=scenario_id, scenario_version=s.scenario_version,
                                  scenario_type=s.scenario_type, seed=s.seed, root_cause=s.root_cause, ai_mode=self.gw.mode,
                                  is_demo=is_demo)
            db.add(row)
            db.commit()
            self._save(db, row, s, 0)
            return {"id": row.id, "snapshot": self.engine.snapshot(s)}

    def get_snapshot(self, sid: str) -> dict[str, Any]:
        with self.db() as db:
            row, s = self._load(db, sid)
            snap = self.engine.snapshot(s)
            snap["session_id"] = row.id
            snap["trainee_id"] = row.trainee_id
            snap["ai_mode"] = self.gw.mode
            return snap

    def _think_time(self, row: TrainingSession, s: SessionState) -> None:
        if not self.settings.count_think_time or s.downtime_since is None:
            return
        delta = (now() - aware(row.last_action_at)).total_seconds()
        self.engine.advance_time(s, min(max(0.0, delta), self.settings.think_time_cap_s), "思考")

    def act(self, sid: str, action: str, params: dict[str, Any] | None = None, source: str = "click") -> dict[str, Any]:
        with _SESSION_LOCKS[sid], self.db() as db:
            row, s = self._load(db, sid)
            seq0 = s.seq
            self._think_time(row, s)
            r = self.engine.act(s, action, params or {}, source=source)
            self._save(db, row, s, seq0)
            return {"result": r.model_dump(), "snapshot": self._snap(row, s)}

    def _snap(self, row: TrainingSession, s: SessionState) -> dict[str, Any]:
        snap = self.engine.snapshot(s)
        snap["session_id"] = row.id
        snap["trainee_id"] = row.trainee_id
        snap["ai_mode"] = self.gw.mode
        return snap

    def say(self, sid: str, text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ServiceError("请输入内容")
        with _SESSION_LOCKS[sid], self.db() as db:
            row, s = self._load(db, sid)
            seq0 = s.seq
            self._think_time(row, s)
            if s.support is not None and s.support.kind == "andon" and not s.support.reported:
                out = self.roles.phone_turn(s, text, session_id=sid)
                kind = "phone"
            else:
                out = self.roles.narrate(s, text, session_id=sid)
                kind = "narrator"
            self._save(db, row, s, seq0)
            if kind == "phone" and out.get("done") and s.andon_reports:
                rep = s.andon_reports[-1]
                db.add(AndonReportRow(session_id=sid, target=rep["target"], content=rep.get("text", ""), elements=rep.get("elements"),
                                      covered=rep.get("covered"), coverage=rep.get("coverage", 0), follow_ups=rep.get("follow_ups", 0),
                                      delay_s=rep.get("delay_s", 0), is_demo=row.is_demo))
                db.commit()
            return {"kind": kind, "ai": out, "snapshot": self._snap(row, s)}

    def andon(self, sid: str, text: str) -> dict[str, Any]:
        with self.db() as db:
            row, s = self._load(db, sid)
            if s.support is None or s.support.kind != "andon" or s.support.reported:
                raise ServiceError("现在没有 Andon 电话。")
        return self.say(sid, text)

    def hint(self, sid: str, question: str | None = None) -> dict[str, Any]:
        with _SESSION_LOCKS[sid], self.db() as db:
            row, s = self._load(db, sid)
            if s.state in ("review", "completed", "safety_terminated"):
                raise ServiceError("本局已经结束。")
            seq0 = s.seq
            self._think_time(row, s)
            out = self.roles.hint(s, question=question, session_id=sid)
            db.add(HintLogRow(session_id=sid, level=out["level"], text=out["text"], question=question, mode=out["mode"],
                              t_sim=s.clock, is_demo=row.is_demo))
            self._save(db, row, s, seq0)
            return {"hint": out, "snapshot": self._snap(row, s)}

    def end_session(self, sid: str) -> dict[str, Any]:
        with _SESSION_LOCKS[sid], self.db() as db:
            row, s = self._load(db, sid)
            seq0 = s.seq
            self.engine.end_session(s)
            self._save(db, row, s, seq0)
            return {"snapshot": self._snap(row, s)}

    def review(self, sid: str, regenerate: bool = False) -> dict[str, Any]:
        with _SESSION_LOCKS[sid], self.db() as db:
            row, s = self._load(db, sid)
            if s.state not in ("review", "completed"):
                raise ServiceError("本局还没结束，结束后才能复盘。")
            if row.review and not regenerate:
                return row.review
            rev = build_review(self.content, s)
            ex = self.roles.examine(rev, session_id=sid)
            rev["examiner"] = ex
            scn = self.content.scenarios.get(s.scenario_id)
            rev["scenario_title"] = scn.title if scn else s.scenario_id
            rev["standard_path"] = s.standard.get("steps", [])
            rev["llm"] = self.session_llm_stats(db, sid)
            rev["session_id"] = sid
            rev["trainee_id"] = row.trainee_id
            rev["started_at"] = row.started_at.isoformat()
            s.state = "completed"
            row.review = json.loads(json.dumps(rev, ensure_ascii=False, default=str))
            self._save(db, row, s, s.seq)
            return row.review

    def session_llm_stats(self, db: Session, sid: str) -> dict[str, Any]:
        rows = db.exec(select(LLMCallRow).where(LLMCallRow.session_id == sid)).all()
        ok = [r for r in rows if r.ok]
        return {"calls": len(rows), "ok": len(ok), "model_calls": sum(1 for r in ok if r.mode in ("online", "replay")),
                "fallback": sum(1 for r in rows if r.mode == "fallback" or not r.ok), "input_tokens": sum(r.input_tokens for r in ok),
                "output_tokens": sum(r.output_tokens for r in ok),
                "cost_usd": round(sum(r.cost_usd or 0 for r in ok), 6),
                "modes": dict(Counter(r.mode for r in rows))}

    def list_sessions(self, trainee_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.db() as db:
            q = select(TrainingSession)
            if trainee_id is not None:
                q = q.where(TrainingSession.trainee_id == trainee_id)
            rows = db.exec(q.order_by(TrainingSession.started_at.desc()).limit(limit)).all()  # type: ignore[attr-defined]
            out = []
            for r in rows:
                scn = self.content.scenarios.get(r.scenario_id)
                out.append({"id": r.id, "scenario_id": r.scenario_id, "title": scn.title if scn else r.scenario_id,
                            "state": r.state, "passed": r.passed, "total": r.total_score, "downtime_s": r.downtime_s,
                            "started_at": r.started_at.isoformat(), "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                            "hints": r.hints_used, "is_demo": r.is_demo, "ai_mode": r.ai_mode})
            return out

    # ==================================================================
    # 关卡工坊
    # ==================================================================
    def ensure_initial_versions(self) -> None:
        c = self.content
        with self.db() as db:
            for sid, scn in c.scenarios.items():
                exists = db.exec(select(ScenarioVersionRow).where(ScenarioVersionRow.scenario_id == sid,
                                                                  ScenarioVersionRow.version == scn.version)).first()
                if exists:
                    continue
                db.add(ScenarioVersionRow(scenario_id=sid, version=scn.version, title=scn.title,
                                          yaml_text=c.scenario_files[sid].read_text(encoding="utf-8"), status="active",
                                          source="初始内容（M1 本体与关卡）", rule_hashes=c.rule_hashes(scn.rules), author=scn.author))
            db.commit()

    def _timeline(self, d: ScenarioDraftRow, stage: str, note: str = "") -> None:
        tl = list(d.timeline or [])
        tl.append({"stage": stage, "at": now().isoformat(timespec="seconds"), "version": d.version, "note": note})
        d.timeline = tl
        d.updated_at = now()

    def _draft_view(self, d: ScenarioDraftRow, db: Session) -> dict[str, Any]:
        reviews = db.exec(select(ReviewRow).where(ReviewRow.draft_id == d.id).order_by(ReviewRow.id)).all()
        cur = [r for r in reviews if r.draft_version == d.version]
        roles_passed = {r.reviewer_role for r in cur if r.verdict == "pass"}
        rejected = any(r.verdict == "reject" for r in cur)
        elapsed = (aware(d.published_at or now()) - aware(d.created_at)).total_seconds()
        return {
            "id": d.id, "title": d.title, "status": d.status,
            "status_label": self.content.states_doc["draft_states"].get(d.status, {}).get("label", d.status),
            "version": d.version, "source_type": d.source_type, "source_text": d.source_text, "attachments": d.attachments or [],
            "target_scenario_id": d.target_scenario_id, "yaml": d.yaml_text, "explanation": d.explanation,
            "gen_mode": d.gen_mode, "gen_model": d.gen_model, "validation": d.validation, "first_validation_ok": d.first_validation_ok,
            "timeline": d.timeline or [], "created_at": d.created_at.isoformat(), "published_at": d.published_at.isoformat() if d.published_at else None,
            "elapsed_s": round(elapsed, 1), "published_scenario_id": d.published_scenario_id, "published_version": d.published_version,
            "reviews": [{"role": r.reviewer_role, "name": r.reviewer_name, "verdict": r.verdict, "comments": r.comments,
                         "version": r.draft_version, "at": r.created_at.isoformat()} for r in reviews],
            "reviews_passed": sorted(roles_passed), "review_rejected": rejected,
            "can_publish": d.status == "pending_review" and roles_passed >= {"equipment", "quality", "ehs"} and not rejected,
            "author": d.author, "is_demo": d.is_demo,
        }

    def list_drafts(self) -> list[dict[str, Any]]:
        with self.db() as db:
            return [self._draft_view(d, db) for d in db.exec(select(ScenarioDraftRow).order_by(ScenarioDraftRow.created_at.desc())).all()]  # type: ignore[attr-defined]

    def get_draft(self, did: str) -> dict[str, Any]:
        with self.db() as db:
            d = db.get(ScenarioDraftRow, did)
            if d is None:
                raise ServiceError("草稿不存在", 404)
            return self._draft_view(d, db)

    def create_draft(self, title: str, source_text: str, source_type: str = "text", attachments: list[str] | None = None,
                     target_scenario_id: str | None = None, author: str = "") -> dict[str, Any]:
        with self.db() as db:
            d = ScenarioDraftRow(title=title or "未命名关卡", source_text=source_text or "", source_type=source_type,
                                 attachments=attachments or [], target_scenario_id=target_scenario_id or None, author=author)
            self._timeline(d, "collecting", "创建草稿，开始采集口述")
            db.add(d)
            db.commit()
            return self._draft_view(d, db)

    def update_draft(self, did: str, **fields: Any) -> dict[str, Any]:
        with self.db() as db:
            d = db.get(ScenarioDraftRow, did)
            if d is None:
                raise ServiceError("草稿不存在", 404)
            if d.status in ("published", "retired"):
                raise ServiceError("已发布的草稿不能修改，请新建草稿")
            for k in ("title", "source_text", "attachments", "target_scenario_id", "author"):
                if k in fields and fields[k] is not None:
                    setattr(d, k, fields[k])
            if fields.get("yaml") is not None and fields["yaml"] != d.yaml_text:
                d.yaml_text = fields["yaml"]
                if d.status in ("pending_review", "validating", "collecting"):
                    d.status = "ai_draft"
                if d.version == 0:
                    d.version = 1
                d.gen_mode = (d.gen_mode or "") if "手工" in (d.gen_mode or "") else (d.gen_mode + "+手工修改" if d.gen_mode else "手工录入")
                self._timeline(d, "ai_draft", "手工修改 YAML")
            db.add(d)
            db.commit()
            return self._draft_view(d, db)

    def generate_draft(self, did: str, use_feedback: bool = False) -> dict[str, Any]:
        with self.db() as db:
            d = db.get(ScenarioDraftRow, did)
            if d is None:
                raise ServiceError("草稿不存在", 404)
            if not d.source_text.strip():
                raise ServiceError("请先粘贴师傅口述（或上传录音转写）")
            feedback = None
            if use_feedback and d.validation:
                v = d.validation
                feedback = (v.get("format_errors") or []) + (v.get("reference_errors") or []) + \
                    [f"回放未通过：{r['variant']} {r.get('rejected') or r.get('violations') or r.get('error')}" for r in v.get("replay", []) if not r.get("passed")]
        out = self.roles.write_script(d.source_text, feedback=feedback, draft_id=did)
        with self.db() as db:
            d = db.get(ScenarioDraftRow, did)
            assert d is not None
            d.version += 1
            d.yaml_text = out["yaml"]
            d.explanation = out.get("explanation", "")
            d.gen_mode = {"online": "AI 生成", "replay": "AI 生成（离线回放）", "fallback": "规则草稿（非大模型）"}.get(out["mode"], out["mode"])
            d.gen_model = out.get("model")
            d.status = "ai_draft"
            d.validation = None
            self._timeline(d, "ai_draft", f"{d.gen_mode}（{out.get('model')}）" + ("，按校验意见重写" if feedback else ""))
            db.add(d)
            db.commit()
            return self._draft_view(d, db)

    def validate_draft(self, did: str) -> dict[str, Any]:
        with self.db() as db:
            d = db.get(ScenarioDraftRow, did)
            if d is None:
                raise ServiceError("草稿不存在", 404)
            if not d.yaml_text.strip():
                raise ServiceError("草稿还没有 YAML")
            d.status = "validating"
            self._timeline(d, "validating", "提交结构校验与引擎回放")
            rep = validate_yaml_text(self.content, d.yaml_text)
            scn = rep.pop("scenario", None)
            rep_json = json.loads(json.dumps(rep, ensure_ascii=False, default=str))
            if scn is not None:
                rep_json["parsed"] = {"id": scn.id, "title": scn.title, "type": scn.type, "alarm": scn.alarm,
                                      "root_causes": [{"id": r.id, "name": r.name, "weight": r.weight, "in_scope": r.in_operator_scope,
                                                       "andon_target": r.andon_target} for r in scn.root_causes],
                                      "rules": scn.rules, "distractors": len(scn.distractors)}
            d.validation = rep_json
            if d.first_validation_ok is None:
                d.first_validation_ok = bool(rep["ok"])
            if rep["ok"]:
                d.status = "pending_review"
                self._timeline(d, "pending_review", rep["summary"])
            else:
                d.status = "ai_draft"
                self._timeline(d, "ai_draft", "校验未通过：" + rep["summary"])
            db.add(d)
            db.commit()
            return self._draft_view(d, db)

    def review_draft(self, did: str, role: str, verdict: str, comments: str = "", name: str = "") -> dict[str, Any]:
        if role not in ("equipment", "quality", "ehs"):
            raise ServiceError("审核角色只能是 equipment / quality / ehs")
        if verdict not in ("pass", "reject"):
            raise ServiceError("审核结论只能是 pass / reject")
        with self.db() as db:
            d = db.get(ScenarioDraftRow, did)
            if d is None:
                raise ServiceError("草稿不存在", 404)
            if d.status != "pending_review":
                raise ServiceError("只有“待审核”的草稿可以审核（先通过结构校验）")
            db.add(ReviewRow(draft_id=did, draft_version=d.version, reviewer_role=role, reviewer_name=name, verdict=verdict, comments=comments))
            label = {"equipment": "设备", "quality": "质量", "ehs": "EHS"}[role]
            if verdict == "reject":
                d.status = "ai_draft"
                d.version += 1
                self._timeline(d, "ai_draft", f"{label}审核不通过，退回并生成新版本 v{d.version}：{comments}")
            else:
                self._timeline(d, "pending_review", f"{label}审核通过")
            db.add(d)
            db.commit()
            return self._draft_view(d, db)

    def publish_draft(self, did: str) -> dict[str, Any]:
        with self.db() as db:
            d = db.get(ScenarioDraftRow, did)
            if d is None:
                raise ServiceError("草稿不存在", 404)
            view = self._draft_view(d, db)
            if not view["can_publish"]:
                raise ServiceError("发布前需要：结构校验通过，且设备、质量、EHS 三方审核全部通过")
            rep = validate_yaml_text(self.content, d.yaml_text)
            if not rep["ok"]:
                raise ServiceError("发布前复验未通过：" + rep["summary"])
            scn: Scenario = rep["scenario"]
            data = yaml.safe_load(d.yaml_text)
            prev = self.content.scenarios.get(scn.id)
            new_version = (prev.version + 1) if prev else 1
            data["version"] = new_version
            data["reviewed_by"] = ["equipment", "quality", "ehs"]
            data["source_draft"] = d.id
            text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
            self._write_scenario(scn.id, text, prev)
            for old in db.exec(select(ScenarioVersionRow).where(ScenarioVersionRow.scenario_id == scn.id,
                                                                ScenarioVersionRow.status == "active")).all():
                old.status = "archived"
                db.add(old)
            self.refresh_content()
            db.add(ScenarioVersionRow(scenario_id=scn.id, version=new_version, title=scn.title, yaml_text=text, status="active",
                                      source=f"工坊草稿 {d.id}", source_draft_id=d.id,
                                      rule_hashes=self.content.rule_hashes(scn.rules), author=d.author))
            d.status = "published"
            d.published_at = now()
            d.published_scenario_id = scn.id
            d.published_version = new_version
            self._timeline(d, "published", f"发布为 {scn.id} v{new_version}")
            db.add(d)
            db.commit()
            return self._draft_view(d, db)

    def _write_scenario(self, scn_id: str, text: str, prev: Scenario | None) -> None:
        sdir = Path(self.settings.content_dir) / "scenarios"
        target = sdir / f"{scn_id}.yaml"
        if prev is not None and target.exists():
            vdir = sdir / "_versions"
            vdir.mkdir(parents=True, exist_ok=True)
            (vdir / f"{scn_id}.v{prev.version}.yaml").write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        target.write_text(text, encoding="utf-8")

    def versions(self, scn_id: str) -> list[dict[str, Any]]:
        with self.db() as db:
            rows = db.exec(select(ScenarioVersionRow).where(ScenarioVersionRow.scenario_id == scn_id).order_by(ScenarioVersionRow.version)).all()
            return [{"version": r.version, "status": r.status, "published_at": r.published_at.isoformat(), "source": r.source,
                     "needs_recheck": r.needs_recheck, "recheck_reason": r.recheck_reason, "author": r.author, "title": r.title} for r in rows]

    def diff(self, scn_id: str, a: int, b: int) -> dict[str, Any]:
        with self.db() as db:
            ra = db.exec(select(ScenarioVersionRow).where(ScenarioVersionRow.scenario_id == scn_id, ScenarioVersionRow.version == a)).first()
            rb = db.exec(select(ScenarioVersionRow).where(ScenarioVersionRow.scenario_id == scn_id, ScenarioVersionRow.version == b)).first()
            if ra is None or rb is None:
                raise ServiceError("版本不存在", 404)
            lines = list(difflib.unified_diff(ra.yaml_text.splitlines(), rb.yaml_text.splitlines(), f"v{a}", f"v{b}", lineterm=""))
            return {"scenario_id": scn_id, "a": a, "b": b, "diff": lines,
                    "added": sum(1 for x in lines if x.startswith("+") and not x.startswith("+++")),
                    "removed": sum(1 for x in lines if x.startswith("-") and not x.startswith("---"))}

    def rollback(self, scn_id: str, version: int) -> dict[str, Any]:
        with self.db() as db:
            src = db.exec(select(ScenarioVersionRow).where(ScenarioVersionRow.scenario_id == scn_id, ScenarioVersionRow.version == version)).first()
            if src is None:
                raise ServiceError("版本不存在", 404)
            prev = self.content.scenarios.get(scn_id)
            new_version = (prev.version + 1) if prev else version
            data = yaml.safe_load(src.yaml_text)
            data["version"] = new_version
            data.setdefault("changelog", [])
            data["changelog"] = list(data["changelog"]) + [f"v{new_version}：回滚为 v{version} 的内容"]
            text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
            rep = validate_yaml_text(self.content, text)
            if not rep["ok"]:
                raise ServiceError("回滚内容未通过校验：" + rep["summary"])
            self._write_scenario(scn_id, text, prev)
            for old in db.exec(select(ScenarioVersionRow).where(ScenarioVersionRow.scenario_id == scn_id, ScenarioVersionRow.status == "active")).all():
                old.status = "archived"
                db.add(old)
            self.refresh_content()
            db.add(ScenarioVersionRow(scenario_id=scn_id, version=new_version, title=data.get("title", ""), yaml_text=text,
                                      status="active", source=f"回滚到 v{version}", rule_hashes=self.content.rule_hashes(data.get("rules", []))))
            db.commit()
        return {"scenario_id": scn_id, "version": new_version, "versions": self.versions(scn_id)}

    def recheck_scan(self) -> list[dict[str, Any]]:
        """规则变更 → 自动标记引用了该规则的关卡为“待复核”。"""
        c = self.content
        out = []
        with self.db() as db:
            for row in db.exec(select(ScenarioVersionRow).where(ScenarioVersionRow.status == "active")).all():
                scn = c.scenarios.get(row.scenario_id)
                if scn is None:
                    continue
                cur = c.rule_hashes(scn.rules)
                snap = row.rule_hashes or {}
                changed = sorted(r for r in set(cur) | set(snap) if cur.get(r) != snap.get(r))
                if changed and not row.needs_recheck:
                    row.needs_recheck = True
                    row.recheck_reason = "引用的规则已变更：" + "、".join(changed)
                    db.add(row)
                    for d in db.exec(select(ScenarioDraftRow).where(ScenarioDraftRow.published_scenario_id == row.scenario_id,
                                                                    ScenarioDraftRow.status == "published")).all():
                        d.status = "needs_recheck"
                        self._timeline(d, "needs_recheck", row.recheck_reason)
                        db.add(d)
                if row.needs_recheck:
                    out.append({"scenario_id": row.scenario_id, "version": row.version, "title": row.title,
                                "reason": row.recheck_reason, "changed_rules": changed})
            db.commit()
        return out

    def recheck_confirm(self, scn_id: str, retire: bool = False) -> dict[str, Any]:
        with self.db() as db:
            row = db.exec(select(ScenarioVersionRow).where(ScenarioVersionRow.scenario_id == scn_id, ScenarioVersionRow.status == "active")).first()
            if row is None:
                raise ServiceError("没有该关卡的有效版本", 404)
            scn = self.content.scenarios[scn_id]
            if retire:
                row.status = "retired"
            row.needs_recheck = False
            row.recheck_reason = None
            row.rule_hashes = self.content.rule_hashes(scn.rules)
            db.add(row)
            for d in db.exec(select(ScenarioDraftRow).where(ScenarioDraftRow.published_scenario_id == scn_id,
                                                            ScenarioDraftRow.status == "needs_recheck")).all():
                d.status = "retired" if retire else "published"
                self._timeline(d, d.status, "复核通过" if not retire else "下线")
                db.add(d)
            db.commit()
            return {"scenario_id": scn_id, "status": row.status}

    def import_downtime(self, csv_text: str | None = None) -> dict[str, Any]:
        if csv_text is None:
            path = Path(self.settings.content_dir) / "seed" / "downtime_records.csv"
            if not path.exists():
                raise ServiceError("没有找到合成停机记录，请先运行 make seed")
            csv_text = path.read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(csv_text))
        n = 0
        with self.db() as db:
            for old in db.exec(select(DowntimeRecordRow)).all():
                db.delete(old)
            for r in reader:
                db.add(DowntimeRecordRow(date=r["date"], station=r["station"], alarm_code=r["alarm_code"],
                                         duration_min=float(r["duration_min"]), description=r.get("description", ""), is_demo=True))
                n += 1
            db.commit()
        return {"imported": n, "demo": True}

    def topics(self) -> dict[str, Any]:
        c = self.content
        with self.db() as db:
            rows = db.exec(select(DowntimeRecordRow)).all()
        agg: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:
            a = agg.setdefault((r.station, r.alarm_code), {"station": r.station, "alarm_code": r.alarm_code, "count": 0, "minutes": 0.0, "samples": []})
            a["count"] += 1
            a["minutes"] += r.duration_min
            if len(a["samples"]) < 3 and r.description:
                a["samples"].append(r.description)
        covered = {scn.alarm: scn for scn in c.scenarios.values() if scn.type == "alarm"}
        out = []
        for a in agg.values():
            scn = covered.get(a["alarm_code"]) if a["station"] == "OP30" else None
            a["minutes"] = round(a["minutes"], 1)
            a["score"] = round(a["count"] * a["minutes"], 1)
            if scn is None:
                a["coverage"] = "未覆盖"
                a["recommendation"] = f"新建关卡：{a['station']} {a['alarm_code']} 停机 {a['count']} 次、共 {a['minutes']} 分钟，尚无训练关卡"
            else:
                a["coverage"] = f"已覆盖（{scn.id} v{scn.version}，{len(scn.root_causes)} 个根因）"
                a["scenario_id"] = scn.id
                a["recommendation"] = "已有关卡；如停机描述中出现新的原因，可补充根因并发布新版本"
            out.append(a)
        out.sort(key=lambda x: -x["score"])
        return {"demo": True, "records": len(rows), "topics": out}

    def workshop_catalog(self) -> dict[str, Any]:
        c = self.content
        return {
            "alarms": [{"code": k, "text": v["text"], "stage": v["stage"]} for k, v in c.alarms.items()],
            "components": [{"id": k, "name": v["name"], "states": {s: d["label"] for s, d in v["states"].items()}} for k, v in c.components.items()],
            "actions": [{"id": a["id"], "name": a["name"], "category": a["category"], "params": a.get("params") or {}} for a in c.actions.values()],
            "rules": [{"id": r["id"], "type": r["type"], "quote": r["quote"]} for r in c.rules],
            "targets": c.inspect_targets(),
            "scenarios": [{"id": s.id, "title": s.title, "version": s.version, "type": s.type} for s in c.scenarios.values()],
        }

    def scenario_yaml(self, scn_id: str) -> str:
        c = self.content
        if scn_id not in c.scenario_files:
            raise ServiceError("关卡不存在", 404)
        return c.scenario_files[scn_id].read_text(encoding="utf-8")

    def production_time(self) -> dict[str, Any]:
        """“关卡制作周期”实测：从创建草稿到发布的耗时。"""
        with self.db() as db:
            rows = db.exec(select(ScenarioDraftRow).where(ScenarioDraftRow.published_at != None)).all()  # noqa: E711
            items = []
            for d in rows:
                total = (aware(d.published_at) - aware(d.created_at)).total_seconds()
                stages: dict[str, float] = {}
                tl = d.timeline or []
                for i in range(len(tl) - 1):
                    a = aware(dt.datetime.fromisoformat(tl[i]["at"]))
                    b = aware(dt.datetime.fromisoformat(tl[i + 1]["at"]))
                    stages[tl[i]["stage"]] = stages.get(tl[i]["stage"], 0.0) + (b - a).total_seconds()
                items.append({"draft_id": d.id, "title": d.title, "scenario_id": d.published_scenario_id, "version": d.published_version,
                              "minutes": round(total / 60, 1), "first_validation_ok": d.first_validation_ok, "gen_mode": d.gen_mode,
                              "stages_min": {k: round(v / 60, 1) for k, v in stages.items()}, "is_demo": d.is_demo})
        mins = [i["minutes"] for i in items if not i["is_demo"]]
        return {"layer": "实测", "count": len(mins), "items": items,
                "avg_minutes": round(sum(mins) / len(mins), 1) if mins else None,
                "median_minutes": sorted(mins)[len(mins) // 2] if mins else None}

    # ==================================================================
    # 看板
    # ==================================================================
    def _sessions(self, db: Session, demo: bool) -> list[TrainingSession]:
        q = select(TrainingSession)
        if not demo:
            q = q.where(TrainingSession.is_demo == False)  # noqa: E712
        return list(db.exec(q).all())

    def stats_overview(self, demo: bool = False) -> dict[str, Any]:
        c = self.content
        with self.db() as db:
            sessions = self._sessions(db, demo)
            tq = select(Trainee) if demo else select(Trainee).where(Trainee.is_demo == False)  # noqa: E712
            trainees = list(db.exec(tq).all())
            vq = select(ViolationRow) if demo else select(ViolationRow).where(ViolationRow.is_demo == False)  # noqa: E712
            violations = list(db.exec(vq).all())
        ended = [s for s in sessions if s.ended_at is not None]
        passed_pairs = {(s.trainee_id, s.scenario_id) for s in ended if s.passed}
        n_scn = len(c.scenarios)
        by_scn = []
        for sid, scn in sorted(c.scenarios.items(), key=lambda x: (x[1].layer, x[0])):
            ss = [s for s in ended if s.scenario_id == sid]
            by_scn.append({"id": sid, "title": scn.title, "layer": scn.layer, "attempts": len(ss),
                           "pass_rate": round(sum(1 for s in ss if s.passed) / len(ss), 3) if ss else None,
                           "avg_score": round(sum(s.total_score or 0 for s in ss) / len(ss), 1) if ss else None})
        alarm_passed = [s for s in ended if s.scenario_type == "alarm" and s.passed]
        hinted = [s for s in ended if s.hints_used > 0]
        ranks = Counter()
        tr_progress = []
        for t in trainees:
            p = self.progress(t.id)
            ranks[p["rank"]["name"]] += 1
            n_passed = sum(1 for l in p["layers"] for s in l["scenarios"] if s["passed"])
            tr_progress.append({"id": t.id, "name": t.name, "rank": p["rank"]["name"], "passed": n_passed, "is_demo": t.is_demo})
        safety = [v for v in violations if v.rule_type == "安全"]
        return {
            "demo": demo,
            "trainees": len(trainees),
            "sessions": len(sessions),
            "ended_sessions": len(ended),
            "progress_avg": round(sum(x["passed"] for x in tr_progress) / len(tr_progress) / n_scn, 3) if tr_progress else None,
            "completion_rate": round(len(passed_pairs) / (len(trainees) * n_scn), 3) if trainees else None,
            "avg_handling_min": round(sum(s.downtime_s for s in alarm_passed) / len(alarm_passed) / 60, 2) if alarm_passed else None,
            "hint_usage_rate": round(len(hinted) / len(ended), 3) if ended else None,
            "hints_per_session": round(sum(s.hints_used for s in ended) / len(ended), 2) if ended else None,
            "safety_violations": len(safety),
            "safety_terminations": sum(1 for v in safety if v.outcome == "terminate"),
            "authority_attempts": sum(1 for v in violations if v.rule_type == "权限"),
            "rank_distribution": [{"rank": r, "count": ranks.get(r, 0)} for r in [x["name"] for x in c.scoring["ranks"]]],
            "by_scenario": by_scn,
            "trainee_progress": sorted(tr_progress, key=lambda x: -x["passed"]),
        }

    def stats_errors(self, demo: bool = False) -> dict[str, Any]:
        c = self.content
        with self.db() as db:
            vq = select(ViolationRow) if demo else select(ViolationRow).where(ViolationRow.is_demo == False)  # noqa: E712
            vs = list(db.exec(vq).all())
        by_type = Counter(v.rule_type for v in vs)
        by_rule = Counter(v.rule_id for v in vs)
        by_tag = Counter(v.tag for v in vs)
        matrix: dict[str, Counter] = defaultdict(Counter)
        for v in vs:
            matrix[v.scenario_id][v.rule_type] += 1
        types = ["安全", "处置", "质量", "权限", "沟通"]
        return {
            "demo": demo, "total": len(vs),
            "by_type": [{"type": t, "count": by_type.get(t, 0)} for t in types],
            "top_rules": [{"rule_id": r, "count": n, "quote": c.rules_by_id.get(r, {}).get("quote", ""), "type": c.rules_by_id.get(r, {}).get("type", "")}
                          for r, n in by_rule.most_common(12)],
            "by_tag": [{"tag": t, "count": n} for t, n in by_tag.most_common()],
            "matrix": [{"scenario_id": sid, "title": c.scenarios[sid].title if sid in c.scenarios else sid,
                        **{t: matrix[sid].get(t, 0) for t in types}} for sid in sorted(matrix)],
            "types": types,
        }

    def stats_trainee(self, trainee_id: int) -> dict[str, Any]:
        c = self.content
        prog = self.progress(trainee_id)
        with self.db() as db:
            vs = list(db.exec(select(ViolationRow).where(ViolationRow.trainee_id == trainee_id)).all())
            ss = list(db.exec(select(TrainingSession).where(TrainingSession.trainee_id == trainee_id)).all())
            tr = db.get(Trainee, trainee_id)
        tags = Counter(v.tag for v in vs)
        for s in ss:
            for t in ((s.review or {}).get("weakness_tags") or []):
                tags[t] += 0.5
        rule_by_tag: dict[str, set[str]] = defaultdict(set)
        for r in c.rules:
            rule_by_tag[r["tag"]].add(r["id"])
        passed = {s["id"]: s for l in prog["layers"] for s in l["scenarios"]}
        suggestions = []
        for tag, _n in tags.most_common(3):
            for scn in c.scenarios.values():
                if set(scn.rules) & rule_by_tag[tag]:
                    st = passed.get(scn.id, {})
                    if not st.get("passed") or (st.get("best") or 0) < 85:
                        suggestions.append({"tag": tag, "scenario_id": scn.id, "title": scn.title, "best": st.get("best")})
        seen = set()
        uniq = []
        for s in suggestions:
            if s["scenario_id"] not in seen:
                seen.add(s["scenario_id"])
                uniq.append(s)
        ended = [s for s in ss if s.ended_at]
        return {
            "trainee": prog["trainee"], "rank": prog["rank"], "is_demo": bool(tr and tr.is_demo),
            "sessions": len(ss), "passed_scenarios": sum(1 for s in passed.values() if s.get("passed")),
            "avg_score": round(sum(s.total_score or 0 for s in ended) / len(ended), 1) if ended else None,
            "weak_tags": [{"tag": t, "score": round(n, 1)} for t, n in tags.most_common(6)],
            "suggested": uniq[:5],
            "recent": [{"id": s.id, "scenario_id": s.scenario_id, "passed": s.passed, "total": s.total_score,
                        "started_at": s.started_at.isoformat()} for s in sorted(ss, key=lambda x: aware(x.started_at), reverse=True)[:10]],
        }

    def stats_teams(self, demo: bool = False) -> dict[str, Any]:
        with self.db() as db:
            teams = list(db.exec(select(Team) if demo else select(Team).where(Team.is_demo == False)).all())  # noqa: E712
            trs = list(db.exec(select(Trainee)).all())
        out = []
        for t in teams:
            members = [x for x in trs if x.team_id == t.id]
            if not members:
                continue
            progs = [self.progress(m.id) for m in members]
            passed = [sum(1 for l in p["layers"] for s in l["scenarios"] if s["passed"]) for p in progs]
            masters = sum(1 for p in progs if p["rank"]["id"] == "master")
            out.append({"team": t.name, "members": len(members), "avg_passed": round(sum(passed) / len(passed), 2),
                        "masters": masters, "is_demo": t.is_demo})
        out.sort(key=lambda x: (-x["avg_passed"], -x["masters"]))
        return {"demo": demo, "teams": out}

    def stats_llm(self) -> dict[str, Any]:
        with self.db() as db:
            rows = list(db.exec(select(LLMCallRow)).all())
            sess = list(db.exec(select(TrainingSession).where(TrainingSession.is_demo == False)).all())  # noqa: E712
        by_role: dict[str, dict[str, Any]] = {}
        for r in rows:
            b = by_role.setdefault(r.role, {"calls": 0, "ok": 0, "lat": [], "cost": 0.0, "modes": Counter()})
            b["calls"] += 1
            b["modes"][r.mode] += 1
            if r.ok:
                b["ok"] += 1
                if r.latency_ms is not None and r.mode == "online":
                    b["lat"].append(r.latency_ms)
                b["cost"] += r.cost_usd or 0
        out = {}
        for role, b in by_role.items():
            lat = sorted(b["lat"])
            out[role] = {"calls": b["calls"], "ok": b["ok"], "cost_usd": round(b["cost"], 6), "modes": dict(b["modes"]),
                         "p50_ms": lat[len(lat) // 2] if lat else None, "p95_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None}
        per_session = defaultdict(float)
        for r in rows:
            if r.session_id and r.ok:
                per_session[r.session_id] += r.cost_usd or 0
        played = [per_session[s.id] for s in sess if s.ended_at and s.id in per_session]
        return {"by_role": out, "sessions_with_calls": len(played),
                "avg_cost_per_session_usd": round(sum(played) / len(played), 6) if played else None,
                "note": "只统计在线调用的真实成本；离线回放与规则降级成本为 0"}


_RUNTIME: Runtime | None = None


def get_runtime() -> Runtime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = Runtime()
    return _RUNTIME


def set_runtime(rt: Runtime | None) -> None:
    global _RUNTIME
    _RUNTIME = rt
