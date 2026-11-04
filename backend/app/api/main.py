"""FastAPI 入口：训练端、关卡工坊、管理看板、评测台共用一个后端。"""
from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from ..services import Runtime, ServiceError, get_runtime
from ..voice.adapters import VoiceUnavailable

app = FastAPI(title="佳境 · 产线排障情景训练系统", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_subscribers: dict[str, set[WebSocket]] = defaultdict(set)


def rt() -> Runtime:
    return get_runtime()


@app.exception_handler(ServiceError)
async def _svc_error(_: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


async def broadcast(sid: str, snapshot: dict[str, Any]) -> None:
    dead = []
    for ws in list(_subscribers.get(sid, ())):
        try:
            await ws.send_text(json.dumps({"type": "snapshot", "snapshot": snapshot}, ensure_ascii=False, default=str))
        except Exception:  # noqa: BLE001
            dead.append(ws)
    for ws in dead:
        _subscribers[sid].discard(ws)


# ---------------------------------------------------------------------------
# 元信息与内容
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict[str, Any]:
    r = rt()
    return {"ok": True, "mode": r.gw.mode, "scenarios": len(r.content.scenarios)}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return rt().meta()


@app.get("/api/content/station")
def station() -> dict[str, Any]:
    r = rt()
    c = r.content
    sdir = Path(r.settings.content_dir) / "stations" / "OP30"
    hotspots = yaml.safe_load((sdir / "hotspots.yaml").read_text(encoding="utf-8")) if (sdir / "hotspots.yaml").exists() else {}
    panos = sorted(p.name for p in (sdir / "pano").glob("*.webp")) if (sdir / "pano").exists() else []
    man = sdir / "closeup" / "manifest.json"
    closeup_sources = {f"closeup/{k}.webp": "ai" for k in (json.loads(man.read_text(encoding="utf-8")) if man.exists() else {})}
    return {
        "factory": c.objects.get("factory"), "line": c.objects.get("line"), "station": c.station, "variants": c.variants,
        "equipment": list(c.equipment.values()),
        "components": {k: {"name": v["name"], "equipment": v.get("equipment"), "hidden": v.get("hidden", False),
                           "states": {s: {"label": d["label"], "closeup": d.get("closeup")} for s, d in v["states"].items()}}
                       for k, v in c.components.items()},
        "targets": c.inspect_targets(),
        "hotspots": hotspots, "panoramas": panos, "closeup_sources": closeup_sources,
        "andon_targets": c.andon_targets,
        "assets_base": "/assets/stations/OP30",
    }


@app.get("/api/content/ontology")
def ontology() -> dict[str, Any]:
    c = rt().content
    return {"objects": c.objects["object_types"], "relations": c.objects["relations"], "actions": list(c.actions.values()),
            "rules": c.rules, "roles": c.roles, "alarms": list(c.alarms.values()), "states": c.states_doc,
            "scoring": c.scoring}


# ---------------------------------------------------------------------------
# 学员与关卡
# ---------------------------------------------------------------------------
class TraineeIn(BaseModel):
    name: str
    team: str | None = None


@app.get("/api/trainees")
def trainees(include_demo: bool = False) -> list[dict[str, Any]]:
    return rt().list_trainees(include_demo)


@app.post("/api/trainees")
def create_trainee(body: TraineeIn) -> dict[str, Any]:
    return rt().create_trainee(body.name, body.team)


@app.get("/api/trainees/{tid}/progress")
def trainee_progress(tid: int) -> dict[str, Any]:
    return rt().progress(tid)


class UnlockIn(BaseModel):
    unlock_all: bool


@app.patch("/api/trainees/{tid}")
def patch_trainee(tid: int, body: UnlockIn) -> dict[str, Any]:
    rt().set_unlock_all(tid, body.unlock_all)
    return rt().progress(tid)


@app.get("/api/scenarios")
def scenarios() -> list[dict[str, Any]]:
    c = rt().content
    return [{"id": s.id, "title": s.title, "type": s.type, "layer": s.layer, "alarm": s.alarm, "version": s.version,
             "summary": s.summary, "root_causes": len(s.root_causes)} for s in sorted(c.scenarios.values(), key=lambda x: (x.layer, x.id))]


# ---------------------------------------------------------------------------
# 训练局
# ---------------------------------------------------------------------------
class SessionIn(BaseModel):
    trainee_id: int
    scenario_id: str
    seed: int | None = None
    root_cause: str | None = None
    faults: list[str] | None = None


class ActIn(BaseModel):
    action: str
    params: dict[str, Any] = {}


class SayIn(BaseModel):
    text: str


class HintIn(BaseModel):
    question: str | None = None


@app.post("/api/sessions")
async def create_session(body: SessionIn) -> dict[str, Any]:
    return await run_in_threadpool(rt().create_session, body.trainee_id, body.scenario_id, body.seed, body.root_cause, body.faults)


@app.get("/api/sessions")
def list_sessions(trainee_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    return rt().list_sessions(trainee_id, limit)


@app.get("/api/sessions/{sid}")
def get_session(sid: str) -> dict[str, Any]:
    return rt().get_snapshot(sid)


@app.post("/api/sessions/{sid}/act")
async def act(sid: str, body: ActIn) -> dict[str, Any]:
    out = await run_in_threadpool(rt().act, sid, body.action, body.params)
    await broadcast(sid, out["snapshot"])
    return out


@app.post("/api/sessions/{sid}/say")
async def say(sid: str, request: Request) -> dict[str, Any]:
    ctype = request.headers.get("content-type", "")
    transcript = None
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        f = form.get("audio")
        if f is None or not hasattr(f, "read"):
            raise HTTPException(400, "缺少音频")
        audio = await f.read()  # type: ignore[union-attr]
        try:
            asr = await run_in_threadpool(rt().voice.transcribe, audio, getattr(f, "filename", "speech.webm"), sid)
        except VoiceUnavailable as exc:
            return {"kind": "asr_failed", "error": str(exc), "snapshot": rt().get_snapshot(sid)}
        text = asr["text"]
        transcript = asr
    else:
        body = await request.json()
        text = body.get("text", "")
    out = await run_in_threadpool(rt().say, sid, text)
    if transcript:
        out["transcript"] = transcript
    await broadcast(sid, out["snapshot"])
    return out


@app.post("/api/sessions/{sid}/hint")
async def hint(sid: str, body: HintIn) -> dict[str, Any]:
    out = await run_in_threadpool(rt().hint, sid, body.question)
    await broadcast(sid, out["snapshot"])
    return out


@app.post("/api/sessions/{sid}/andon")
async def andon(sid: str, body: SayIn) -> dict[str, Any]:
    out = await run_in_threadpool(rt().andon, sid, body.text)
    await broadcast(sid, out["snapshot"])
    return out


@app.post("/api/sessions/{sid}/end")
async def end(sid: str) -> dict[str, Any]:
    out = await run_in_threadpool(rt().end_session, sid)
    await broadcast(sid, out["snapshot"])
    return out


@app.get("/api/sessions/{sid}/review")
async def review(sid: str, regenerate: bool = False) -> dict[str, Any]:
    return await run_in_threadpool(rt().review, sid, regenerate)


@app.websocket("/ws/sessions/{sid}")
async def ws_session(ws: WebSocket, sid: str) -> None:
    await ws.accept()
    _subscribers[sid].add(ws)
    try:
        snap = await run_in_threadpool(rt().get_snapshot, sid)
        await ws.send_text(json.dumps({"type": "snapshot", "snapshot": snap}, ensure_ascii=False, default=str))
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except (WebSocketDisconnect, ServiceError):
        pass
    finally:
        _subscribers[sid].discard(ws)


# ---------------------------------------------------------------------------
# 语音
# ---------------------------------------------------------------------------
class TTSIn(BaseModel):
    text: str
    speaker: str = "coach"
    session_id: str | None = None


@app.post("/api/voice/tts")
async def tts(body: TTSIn) -> Response:
    res = await run_in_threadpool(rt().voice.speak, body.text, body.speaker, body.session_id)
    if res is None:
        return Response(status_code=204, headers={"X-Voice": "unavailable"})
    data, mode = res
    return Response(content=data, media_type="audio/mpeg", headers={"X-Voice": mode})


@app.post("/api/voice/asr")
async def asr(audio: UploadFile = File(...)) -> dict[str, Any]:
    data = await audio.read()
    try:
        return await run_in_threadpool(rt().voice.transcribe, data, audio.filename or "speech.webm", None)
    except VoiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


# ---------------------------------------------------------------------------
# 关卡工坊
# ---------------------------------------------------------------------------
class DraftIn(BaseModel):
    title: str = ""
    source_text: str = ""
    source_type: str = "text"
    attachments: list[str] = []
    target_scenario_id: str | None = None
    author: str = ""


class DraftPatch(BaseModel):
    title: str | None = None
    source_text: str | None = None
    yaml: str | None = None
    attachments: list[str] | None = None
    target_scenario_id: str | None = None
    author: str | None = None


class GenerateIn(BaseModel):
    use_feedback: bool = False


class ReviewIn(BaseModel):
    role: str
    verdict: str
    comments: str = ""
    name: str = ""


@app.get("/api/drafts")
def drafts() -> list[dict[str, Any]]:
    return rt().list_drafts()


@app.post("/api/drafts")
def create_draft(body: DraftIn) -> dict[str, Any]:
    return rt().create_draft(body.title, body.source_text, body.source_type, body.attachments, body.target_scenario_id, body.author)


@app.post("/api/drafts/upload")
async def create_draft_audio(title: str = Form(""), author: str = Form(""), audio: UploadFile = File(...)) -> dict[str, Any]:
    data = await audio.read()
    try:
        asr_out = await run_in_threadpool(rt().voice.transcribe, data, audio.filename or "oral.webm", None)
    except VoiceUnavailable as exc:
        raise HTTPException(503, f"录音转写不可用：{exc}。请改为粘贴文字。") from exc
    return rt().create_draft(title, asr_out["text"], "audio", [], None, author)


@app.get("/api/drafts/{did}")
def get_draft(did: str) -> dict[str, Any]:
    return rt().get_draft(did)


@app.patch("/api/drafts/{did}")
def patch_draft(did: str, body: DraftPatch) -> dict[str, Any]:
    return rt().update_draft(did, **body.model_dump())


@app.post("/api/drafts/{did}/generate")
async def generate(did: str, body: GenerateIn) -> dict[str, Any]:
    return await run_in_threadpool(rt().generate_draft, did, body.use_feedback)


@app.post("/api/drafts/{did}/validate")
async def validate(did: str) -> dict[str, Any]:
    return await run_in_threadpool(rt().validate_draft, did)


@app.post("/api/drafts/{did}/reviews")
def submit_review(did: str, body: ReviewIn) -> dict[str, Any]:
    return rt().review_draft(did, body.role, body.verdict, body.comments, body.name)


@app.post("/api/drafts/{did}/publish")
async def publish(did: str) -> dict[str, Any]:
    return await run_in_threadpool(rt().publish_draft, did)


@app.get("/api/workshop/catalog")
def catalog() -> dict[str, Any]:
    return rt().workshop_catalog()


@app.get("/api/workshop/scenarios/{scn_id}/yaml", response_class=PlainTextResponse)
def scenario_yaml(scn_id: str) -> str:
    return rt().scenario_yaml(scn_id)


@app.get("/api/scenarios/{scn_id}/versions")
def versions(scn_id: str) -> list[dict[str, Any]]:
    return rt().versions(scn_id)


@app.get("/api/scenarios/{scn_id}/diff")
def diff(scn_id: str, a: int, b: int) -> dict[str, Any]:
    return rt().diff(scn_id, a, b)


class RollbackIn(BaseModel):
    version: int


@app.post("/api/scenarios/{scn_id}/rollback")
async def rollback(scn_id: str, body: RollbackIn) -> dict[str, Any]:
    return await run_in_threadpool(rt().rollback, scn_id, body.version)


@app.get("/api/workshop/recheck")
def recheck() -> list[dict[str, Any]]:
    return rt().recheck_scan()


class RecheckIn(BaseModel):
    retire: bool = False


@app.post("/api/workshop/recheck/{scn_id}")
def recheck_confirm(scn_id: str, body: RecheckIn) -> dict[str, Any]:
    return rt().recheck_confirm(scn_id, body.retire)


@app.post("/api/workshop/downtime/import")
def import_downtime() -> dict[str, Any]:
    return rt().import_downtime()


@app.get("/api/workshop/topics")
def topics() -> dict[str, Any]:
    return rt().topics()


@app.get("/api/workshop/production-time")
def production_time() -> dict[str, Any]:
    return rt().production_time()


# ---------------------------------------------------------------------------
# 看板
# ---------------------------------------------------------------------------
@app.get("/api/stats/overview")
def stats_overview(demo: bool = False) -> dict[str, Any]:
    return rt().stats_overview(demo)


@app.get("/api/stats/errors")
def stats_errors(demo: bool = False) -> dict[str, Any]:
    return rt().stats_errors(demo)


@app.get("/api/stats/trainees/{tid}")
def stats_trainee(tid: int) -> dict[str, Any]:
    return rt().stats_trainee(tid)


@app.get("/api/stats/teams")
def stats_teams(demo: bool = False) -> dict[str, Any]:
    return rt().stats_teams(demo)


@app.get("/api/stats/llm")
def stats_llm() -> dict[str, Any]:
    return rt().stats_llm()


# ---------------------------------------------------------------------------
# 评测台（网页查看：读取 eval/reports 下的结果文件）
# ---------------------------------------------------------------------------
def _reports_dir() -> Path:
    return Path(rt().settings.root) / "eval" / "reports"


@app.get("/api/eval/runs")
def eval_runs() -> list[dict[str, Any]]:
    out = []
    d = _reports_dir()
    if not d.exists():
        return out
    for p in d.glob("*/results.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({"run": p.parent.name, "created_at": data.get("created_at"), "split": data.get("split"),
                    "models": list((data.get("models") or {}).keys()), "case_set": data.get("case_set"),
                    "iteration": data.get("iteration"), "note": data.get("note")})
    # 按生成时间倒序；当前选型所依据的那次评测（config/models.yaml 的 status 里写明）排在最前，评测台默认打开它
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    basis: list[str] = []
    cfg = Path(rt().settings.root) / "config" / "models.yaml"
    if cfg.exists():
        m = re.search(r"^status:(.*)$", cfg.read_text(encoding="utf-8"), re.M)
        basis = re.findall(r"\d{8}-[\w.-]+", m.group(1)) if m else []
    for r in out:
        r["selection_basis"] = r["run"] in basis
    # 选型依据（可能是多轮合并）中最新的一轮排第一，评测台默认打开它
    out.sort(key=lambda r: not r["selection_basis"])
    return out


@app.get("/api/eval/runs/{run}")
def eval_run(run: str) -> dict[str, Any]:
    p = _reports_dir() / run / "results.json"
    if not p.exists() or ".." in run:
        raise HTTPException(404, "评测结果不存在")
    data = json.loads(p.read_text(encoding="utf-8"))
    note = _pooled_selection_note(run, data)
    if note:
        data["notes"] = list(data.get("notes") or []) + [note]
    return data


def _pooled_selection_note(run: str, data: dict[str, Any]) -> str | None:
    """本轮属于“多轮合并选型”的依据之一时，说明本轮单独选型与当前配置（合并选型）的差异。"""
    root = Path(rt().settings.root)
    cfg = root / "config" / "models.yaml"
    if not cfg.exists():
        return None
    m = re.search(r"^status:(.*)$", cfg.read_text(encoding="utf-8"), re.M)
    basis = re.findall(r"\d{8}-[\w.-]+", m.group(1)) if m else []
    if len(basis) < 2 or run not in basis:
        return None
    try:
        import sys

        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from eval.selection import pool, select_models

        runs = [json.loads((_reports_dir() / b / "results.json").read_text(encoding="utf-8")) for b in basis]
        pooled = select_models(pool(runs))
    except Exception:  # noqa: BLE001 - 说明文字失败不影响页面
        return None
    names = {"narrator": "主持人", "coach": "带班师傅", "phone": "电话", "scriptwriter": "编剧"}
    diff = [f"{names.get(r, r)}：本轮单独为 {(data.get('selection') or {}).get(r, {}).get('model')}，合并后为 {s.get('model')}"
            for r, s in pooled.items() if (data.get("selection") or {}).get(r, {}).get("model") != s.get("model")]
    head = f"当前配置的选型依据是 {' + '.join(basis)} 两轮合并（各指标取平均，安全取最差一次；并列时按角色核心门槛择优）"
    return head + ("。与本轮单独计算的差异——" + "；".join(diff) if diff else "，与本轮单独计算的结果一致")


@app.get("/api/eval/badcases")
def eval_badcases() -> list[dict[str, Any]]:
    p = Path(rt().settings.root) / "eval" / "badcases.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# 静态资源：工位素材（全景、近景）、前端打包产物（本地无 Nginx 时直接由后端托管）
# ---------------------------------------------------------------------------
def _mount_static() -> None:
    r = rt()
    stations = Path(r.settings.content_dir) / "stations"
    if stations.exists():
        app.mount("/assets/stations", StaticFiles(directory=str(stations)), name="stations")
    dist = Path(r.settings.root) / "frontend" / "dist"
    if dist.exists():
        if (dist / "app-assets").exists():
            app.mount("/app-assets", StaticFiles(directory=str(dist / "app-assets")), name="app-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> FileResponse:
            if full_path.startswith(("api/", "ws/", "assets/stations/")):
                raise HTTPException(404)
            f = dist / full_path
            if full_path and f.is_file():
                return FileResponse(f)
            return FileResponse(dist / "index.html")


_mount_static()
