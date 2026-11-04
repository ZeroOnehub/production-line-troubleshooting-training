"""接口与服务层：训练局全流程、关卡工坊（生成 → 校验 → 三方审核 → 发布 → 版本对比 → 回滚 → 规则变更待复核）、看板统计。
使用临时目录里的 content 副本与内存数据库，不改动仓库内容。"""
import shutil

import pytest
import yaml
from fastapi.testclient import TestClient

from app.gateway.gateway import ModelGateway
from app.settings import Settings, get_settings


@pytest.fixture()
def client(tmp_path, monkeypatch):
    base = get_settings()
    content = tmp_path / "content"
    shutil.copytree(base.content_dir, content)
    monkeypatch.delenv("AIHUBMIX_API_KEY", raising=False)
    s = Settings(root=base.root, app_mode="degraded", record_calls=False, database_url=f"sqlite:///{tmp_path / 'app.db'}",
                 content_dir=content, recordings_dir=tmp_path / "rec", logs_dir=tmp_path / "logs", models=base.models, catalog=base.catalog)
    from app import services
    from app.api import main

    rt = services.Runtime(settings=s, gateway=ModelGateway(s))
    services.set_runtime(rt)
    yield TestClient(main.app), rt, content
    services.set_runtime(None)


def test_training_flow(client):
    c, rt, _ = client
    assert c.get("/api/health").json()["ok"]
    meta = c.get("/api/meta").json()
    assert meta["mode"] == "degraded" and meta["content"]["rules"] >= 30
    st = c.get("/api/content/station").json()
    assert st["hotspots"]["hotspots"] and "bit" in st["components"]
    t = c.post("/api/trainees", json={"name": "甲", "team": "甲班"}).json()
    assert c.post("/api/sessions", json={"trainee_id": t["id"], "scenario_id": "SCN-T101"}).status_code == 403
    r = c.post("/api/sessions", json={"trainee_id": t["id"], "scenario_id": "SCN-TOUR"}).json()
    sid = r["id"]
    for e in rt.content.scenarios["SCN-TOUR"].codex:
        c.post(f"/api/sessions/{sid}/act", json={"action": "inspect", "params": {"target": e.targets[0]}})
    for _ in range(7):
        snap = c.post(f"/api/sessions/{sid}/act", json={"action": "cycle_step"}).json()["snapshot"]
    assert snap["ended"] and snap["objective_met"]
    rev = c.get(f"/api/sessions/{sid}/review").json()
    assert rev["passed"] and rev["examiner"]["mode"] == "fallback"
    prog = c.get(f"/api/trainees/{t['id']}/progress").json()
    assert prog["layers"][1]["unlocked"]
    # 解锁全部关卡后直接打异常关卡：Andon 电话、提示、结束本局
    c.patch(f"/api/trainees/{t['id']}", json={"unlock_all": True})
    sid2 = c.post("/api/sessions", json={"trainee_id": t["id"], "scenario_id": "SCN-T104", "root_cause": "RC-T104-D", "seed": 3}).json()["id"]
    c.post(f"/api/sessions/{sid2}/say", json={"text": "继续生产"})
    h = c.post(f"/api/sessions/{sid2}/hint", json={}).json()
    assert h["hint"]["level"] == 1
    c.post(f"/api/sessions/{sid2}/say", json={"text": "看看到位传感器"})
    c.post(f"/api/sessions/{sid2}/say", json={"text": "拉Andon叫维修"})
    o = c.post(f"/api/sessions/{sid2}/andon", json={"text": "OP30 报 T-104，看了传感器，没有 NG"}).json()
    assert o["kind"] == "phone" and o["ai"]["done"]
    assert c.get(f"/api/sessions/{sid2}").json()["state"] == "waiting_repair"
    assert c.get(f"/api/sessions/{sid2}/review").status_code == 400
    c.post(f"/api/sessions/{sid2}/end")
    assert not c.get(f"/api/sessions/{sid2}/review").json()["passed"]
    assert len(c.get(f"/api/sessions?trainee_id={t['id']}").json()) == 2
    ov = c.get("/api/stats/overview").json()
    assert ov["trainees"] == 1 and ov["ended_sessions"] == 2
    assert c.get("/api/stats/errors").json()["total"] >= 0
    assert c.get(f"/api/stats/trainees/{t['id']}").json()["sessions"] == 2
    assert c.get("/api/stats/teams").json()["teams"][0]["team"] == "甲班"
    assert "by_role" in c.get("/api/stats/llm").json()
    with c.websocket_connect(f"/ws/sessions/{sid2}") as ws:
        assert ws.receive_json()["type"] == "snapshot"


def test_workshop_flow(client):
    c, rt, content = client
    oral = "T-104 夹具未到位。常见是夹具里有碎屑，要停机清理；也可能是到位传感器坏了，那得叫维修。"
    d = c.post("/api/drafts", json={"title": "夹具未到位（工坊）", "source_text": oral, "author": "示例师傅"}).json()
    did = d["id"]
    assert d["status"] == "collecting"
    d = c.post(f"/api/drafts/{did}/generate", json={}).json()
    assert d["status"] == "ai_draft" and d["gen_mode"].startswith("规则草稿") and d["version"] == 1
    assert c.post(f"/api/drafts/{did}/reviews", json={"role": "ehs", "verdict": "pass"}).status_code == 400
    d = c.post(f"/api/drafts/{did}/validate").json()
    assert d["validation"]["ok"] and d["status"] == "pending_review" and d["first_validation_ok"]
    # 质量不通过 → 退回并生成新版本
    d = c.post(f"/api/drafts/{did}/reviews", json={"role": "quality", "verdict": "reject", "comments": "补一个干扰项说明"}).json()
    assert d["status"] == "ai_draft" and d["version"] == 2
    d = c.post(f"/api/drafts/{did}/validate").json()
    for role in ("equipment", "quality"):
        d = c.post(f"/api/drafts/{did}/reviews", json={"role": role, "verdict": "pass", "name": role}).json()
    assert not d["can_publish"]
    assert c.post(f"/api/drafts/{did}/publish").status_code == 400
    d = c.post(f"/api/drafts/{did}/reviews", json={"role": "ehs", "verdict": "pass"}).json()
    assert d["can_publish"]
    d = c.post(f"/api/drafts/{did}/publish").json()
    sid = d["published_scenario_id"]
    assert d["status"] == "published" and d["published_version"] == 1
    assert (content / "scenarios" / f"{sid}.yaml").exists() and sid in rt.content.scenarios
    pt = c.get("/api/workshop/production-time").json()
    assert pt["count"] == 1 and pt["layer"] == "实测"
    # 再发布一个修改版 → 版本 2，对比与回滚
    text = yaml.safe_load(c.get(f"/api/workshop/scenarios/{sid}/yaml").text)
    text["title"] = "夹具未到位（工坊 v2）"
    d2 = c.post("/api/drafts", json={"title": "v2", "source_text": oral}).json()
    c.patch(f"/api/drafts/{d2['id']}", json={"yaml": yaml.safe_dump(text, allow_unicode=True)})
    c.post(f"/api/drafts/{d2['id']}/validate")
    for role in ("equipment", "quality", "ehs"):
        c.post(f"/api/drafts/{d2['id']}/reviews", json={"role": role, "verdict": "pass"})
    assert c.post(f"/api/drafts/{d2['id']}/publish").json()["published_version"] == 2
    vs = c.get(f"/api/scenarios/{sid}/versions").json()
    assert [v["version"] for v in vs] == [1, 2] and vs[1]["status"] == "active"
    diff = c.get(f"/api/scenarios/{sid}/diff?a=1&b=2").json()
    assert diff["added"] >= 1
    rb = c.post(f"/api/scenarios/{sid}/rollback", json={"version": 1}).json()
    assert rb["version"] == 3 and rt.content.scenarios[sid].version == 3
    assert rt.content.scenarios[sid].title != "夹具未到位（工坊 v2）"   # 内容回到 v1
    prog = c.get(f"/api/trainees/{c.post('/api/trainees', json={'name': '乙'}).json()['id']}/progress").json()
    assert sid in {s["id"] for l in prog["layers"] for s in l["scenarios"]}   # 新关卡出现在关卡地图
    assert (content / "scenarios" / "_versions" / f"{sid}.v2.yaml").exists()
    # 规则变更 → 自动标记引用该规则的关卡待复核
    rules = yaml.safe_load((content / "ontology" / "rules.yaml").read_text(encoding="utf-8"))
    rules["rules"][0]["quote"] = "手别往夹具里伸（复核测试）"
    (content / "ontology" / "rules.yaml").write_text(yaml.safe_dump(rules, allow_unicode=True, sort_keys=False), encoding="utf-8")
    flagged = c.get("/api/workshop/recheck").json()
    ids = {f["scenario_id"] for f in flagged}
    assert "SCN-T101" in ids and all("R-SAF-01" in f["changed_rules"] for f in flagged)
    assert c.post("/api/workshop/recheck/SCN-T101", json={}).json()["status"] == "active"
    assert "SCN-T101" not in {f["scenario_id"] for f in c.get("/api/workshop/recheck").json()}
    # 停机记录选题
    csv_dir = content / "seed"
    (csv_dir / "downtime_records.csv").write_text("date,station,alarm_code,duration_min,description\n2026-11-01,OP30,T-101,5,扭矩低\n2026-11-02,OP20,P-201,8,压装力超差\n", encoding="utf-8")
    assert c.post("/api/workshop/downtime/import").json()["imported"] == 2
    tp = c.get("/api/workshop/topics").json()
    assert tp["demo"] and {t["coverage"] for t in tp["topics"]} >= {"未覆盖"}
    assert c.get("/api/workshop/catalog").json()["rules"]
    assert len(c.get("/api/drafts").json()) == 2


def test_draft_validation_failure_and_regenerate(client):
    c, _, _ = client
    d = c.post("/api/drafts", json={"title": "坏草稿", "source_text": "随便说说"}).json()
    c.patch(f"/api/drafts/{d['id']}", json={"yaml": "id: SCN-BAD\nversion: 1\ntype: alarm\n"})
    d = c.post(f"/api/drafts/{d['id']}/validate").json()
    assert not d["validation"]["ok"] and d["status"] == "ai_draft" and d["first_validation_ok"] is False
    d = c.post(f"/api/drafts/{d['id']}/generate", json={"use_feedback": True}).json()
    assert d["version"] == 2
    assert c.get("/api/drafts/NOPE").status_code == 404
