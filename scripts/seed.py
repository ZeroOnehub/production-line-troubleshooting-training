"""建表并导入初始内容与演示数据（make seed）。

    python scripts/seed.py               # 建表、登记关卡初始版本、生成/导入合成停机记录、生成演示学员（已有则跳过）
    python scripts/seed.py --reset-demo  # 删除全部演示数据后重新生成

演示学员的每一局都由引擎真实运行：按“标准路径 + 按能力折算的失误概率”出招，所以违规、得分、错误分布都是引擎判出来的，
但它们仍然是“演示数据”，全部带 is_demo 标记。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import random
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlmodel import delete, select  # noqa: E402

from app.engine.scoring import build_review  # noqa: E402
from app.models.db import (ActionLogRow, AndonReportRow, HintLogRow, ScenarioDraftRow, Team, Trainee, TrainingSession,  # noqa: E402
                           ViolationRow)
from app.services import Runtime  # noqa: E402

ORDER = ["SCN-TOUR", "SCN-CHECK", "SCN-CHG-AB"] + [f"SCN-T1{i:02d}" for i in range(1, 11)] + ["SCN-SHIFT"]

DOWNTIME_TEMPLATES = {
    ("OP30", "T-101"): (26, ["扭矩低，换批头后恢复", "扭矩低，发现料盒混入长螺栓，质量隔离整盒", "换型后程序号没切，班长确认后恢复", "扭矩低，复位多次无效后叫维修"]),
    ("OP30", "T-102"): (18, ["角度超差，螺栓斜入，隔离 1 件", "套筒松动，重新装紧后首件合格", "螺纹孔有切屑，来料批次隔离"]),
    ("OP30", "T-103"): (22, ["扫码窗口油污，擦拭后恢复", "条码标签破损，质量补码", "扫码枪插头松动，维修处理"]),
    ("OP30", "T-104"): (20, ["夹具有铁屑，停机清理", "工件没放正，重新放置", "到位传感器故障，维修更换", "气压不足夹不紧"]),
    ("OP30", "T-105"): (12, ["周转盒挡住光栅", "光栅镜面脏，擦拭后恢复", "光栅被撞偏，维修对位"]),
    ("OP30", "T-106"): (8, ["上料区混入他型件", "换型后 HMI 未确认型号"]),
    ("OP30", "T-107"): (9, ["工位手阀未开全", "车间供气压力波动"]),
    ("OP30", "T-108"): (5, ["控制器通讯中断，维修重启模块"]),
    ("OP30", "T-109"): (14, ["料盒见底，物料补料", "料位传感器误报"]),
    ("OP30", "T-110"): (7, ["隔壁搬料误触急停", "拧紧枪异味，同事拍急停"]),
    ("OP30", "T-111"): (6, ["拧紧枪过热保护（示例：尚无训练关卡）"]),
    ("OP20", "P-201"): (11, ["压装力超差（示例：OP20 尚无训练关卡）", "压装曲线异常"]),
    ("OP20", "P-202"): (7, ["压装位移不足（示例）"]),
}


def gen_downtime(path: Path, cfg: dict, rng: random.Random) -> None:
    total = cfg["records"]
    weights = {k: v[0] for k, v in DOWNTIME_TEMPLATES.items()}
    keys = list(weights)
    today = dt.date(2026, 11, 3)
    rows = []
    for _ in range(total):
        k = rng.choices(keys, weights=[weights[x] for x in keys])[0]
        desc = rng.choice(DOWNTIME_TEMPLATES[k][1])
        day = today - dt.timedelta(days=rng.randrange(cfg["days"]))
        dur = round(rng.lognormvariate(1.6, 0.6), 1)
        rows.append({"date": day.isoformat(), "station": k[0], "alarm_code": k[1], "duration_min": dur, "description": desc})
    rows.sort(key=lambda r: r["date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "station", "alarm_code", "duration_min", "description"])
        w.writeheader()
        w.writerows(rows)


def _mark_skip(s, step) -> None:
    issue = s.issue
    if issue is None:
        return
    for i, st in enumerate(issue.path):
        if not issue.done[i] and st["action"] == step["action"] and st.get("params", {}) == step.get("params", {}):
            issue.done[i] = True
            return


def play_noisy(rt: Runtime, s, rng: random.Random, skill: float, cfg: dict) -> None:
    e = rt.engine
    roles = rt.roles
    m = {k: v * (1.2 - skill) for k, v in cfg["mistakes"].items()}
    think_lo, think_hi = cfg["think_time_s"]
    hinted_episodes: set = set()
    blind_done: set = set()
    forbidden_done: set = set()
    andon_done: set = set()
    for _ in range(500):
        if s.state in ("review", "completed"):
            break
        st = e.next_standard_step(s)
        if st is None:
            break
        a = st["action"]
        ep = s.current_episode
        if s.downtime_since is not None:
            e.advance_time(s, rng.uniform(think_lo, think_hi))
        # 每个异常开始时的随机行为
        if ep is not None and ep not in hinted_episodes:
            hinted_episodes.add(ep)
            if rng.random() < cfg["hints"]["probability"] * (1.2 - skill):
                for lvl in range(1, rng.randint(1, cfg["hints"]["max_per_session"]) + 1):
                    s.hints.append({"level": lvl, "t": s.clock, "text": roles.coach_fallback(s, lvl), "mode": "demo", "marker": ""})
        if ep is not None and ep not in blind_done and s.alarm and rng.random() < m["blind_reset"]:
            blind_done.add(ep)
            e.act(s, "rescan" if s.alarm == "T-103" else "reset_alarm", source="demo")
            continue
        if ep is not None and ep not in forbidden_done and rng.random() < m["forbidden_try"]:
            forbidden_done.add(ep)
            e.act(s, rng.choice(["open_electrical_cabinet", "adjust_sensor", "modify_program_params", "reconnect_cable"]), source="demo")
            continue
        if (ep is not None and ep not in andon_done and s.issue is not None and s.issue.in_scope and s.issue.kind == "rc"
                and s.support is None and s.state in ("abnormal", "investigating") and rng.random() < m["unnecessary_andon"]):
            andon_done.add(ep)
            e.act(s, "pull_andon", {"target": "maintenance"}, source="demo")
            e.act(s, "andon_report", {"elements": "all"}, actor="phone", source="demo")
            e.act(s, "wait", source="demo")
            continue
        params = dict(st.get("params", {}))
        if a == "inspect" and s.scenario_type != "tour" and rng.random() < m["skip_inspect"]:
            _mark_skip(s, st)
            continue
        if a == "pause_equipment" and rng.random() < m["skip_pause"]:
            _mark_skip(s, st)
            continue
        if a == "isolate_ng_part" and rng.random() < m["skip_isolate"]:
            _mark_skip(s, st)
            continue
        if a == "first_piece_check" and rng.random() < m["skip_first_piece"]:
            _mark_skip(s, st)
            continue
        if a == "pull_andon" and rng.random() < m["wrong_target"]:
            right = params.get("target")
            params["target"] = rng.choice([t for t in ("maintenance", "quality", "team_leader") if t != right])
            r = e.act(s, a, params, source="demo")
            e.act(s, "andon_report", {"elements": "all"}, actor="phone", source="demo")
            e.act(s, "wait", source="demo")
            # 叫错人被退回：标准路径里的呼叫三步重新来
            if s.issue is not None:
                for i, p in enumerate(s.issue.path):
                    if p["action"] in ("pull_andon", "andon_report", "wait"):
                        s.issue.done[i] = False
            continue
        actor = "trainee"
        if a == "andon_report":
            actor = "phone"
            truth = e.report_truth(s)
            el = dict(truth)
            if rng.random() < m["poor_report"]:
                for k in rng.sample(["station", "alarm_code", "actions_done", "ng_count"], rng.randint(1, 2)):
                    el[k] = [] if k == "actions_done" else None
            params = {"elements": el}
        r = e.act(s, a, params, actor=actor, source="demo")
        if r.rejected:
            _mark_skip(s, st)
    if s.state not in ("review", "completed"):
        e.end_session(s, "演示学员未完成本局")


def gen_demo(rt: Runtime, cfg: dict) -> dict:
    rng = random.Random(cfg["seed"])
    stats = {"trainees": 0, "sessions": 0}
    base = dt.datetime(2026, 11, 3, 8, 0, tzinfo=dt.timezone.utc) - dt.timedelta(days=cfg["days"])
    n = 0
    for team_cfg in cfg["teams"]:
        with rt.db() as db:
            team = Team(name=team_cfg["name"], is_demo=True)
            db.add(team)
            db.commit()
            db.refresh(team)
        for _ in range(team_cfg["trainees"]):
            n += 1
            with rt.db() as db:
                tr = Trainee(name=f"{cfg['trainee_name_prefix']}{n:02d}", team_id=team.id, is_demo=True)
                db.add(tr)
                db.commit()
                db.refresh(tr)
            stats["trainees"] += 1
            skill = rng.uniform(cfg["skill_min"], cfg["skill_max"])
            stop = rng.randint(*cfg["stop_after_scenarios"])
            t0 = base + dt.timedelta(days=rng.uniform(0, 5))
            for scn_id in ORDER[:stop]:
                passed = False
                for _attempt in range(cfg["max_attempts_per_scenario"]):
                    out = rt.create_session(tr.id, scn_id, seed=rng.randrange(1, 10**8), is_demo=True, check_unlock=False)
                    sid = out["id"]
                    with rt.db() as db:
                        row, s = rt._load(db, sid)
                        play_noisy(rt, s, rng, skill, cfg)
                        rt._save(db, row, s, 0)
                        rev = build_review(rt.content, s)
                        rev["examiner"] = {"mode": "demo", "comment": rt.roles.examiner_fallback(rev), "ai_tags": rev["weakness_tags"][:3]}
                        row.review = {"weakness_tags": rev["weakness_tags"], "examiner": rev["examiner"], "passed": rev["passed"],
                                      "scores": rev["scores"], "demo": True}
                        t0 += dt.timedelta(minutes=rng.uniform(20, 300))
                        row.started_at = t0
                        row.last_action_at = t0
                        row.ended_at = t0 + dt.timedelta(seconds=s.clock)
                        for h in s.hints:
                            db.add(HintLogRow(session_id=sid, level=h["level"], text=h["text"], mode="demo", t_sim=h["t"], is_demo=True))
                        for rep in s.andon_reports:
                            db.add(AndonReportRow(session_id=sid, target=rep["target"], elements=rep.get("elements"), covered=rep.get("covered"),
                                                  coverage=rep.get("coverage", 0), follow_ups=rep.get("follow_ups", 0), delay_s=rep.get("delay_s", 0), is_demo=True))
                        db.add(row)
                        db.commit()
                        passed = bool(row.passed)
                    stats["sessions"] += 1
                    if passed:
                        break
                    skill = min(0.98, skill + 0.05)
                if not passed:
                    break
    return stats


def reset_demo(rt: Runtime) -> None:
    with rt.db() as db:
        ids = [s.id for s in db.exec(select(TrainingSession).where(TrainingSession.is_demo == True)).all()]  # noqa: E712
        for model in (ActionLogRow, ViolationRow, HintLogRow, AndonReportRow):
            db.exec(delete(model).where(model.is_demo == True))  # type: ignore[attr-defined]  # noqa: E712
        db.exec(delete(TrainingSession).where(TrainingSession.is_demo == True))  # noqa: E712
        db.exec(delete(Trainee).where(Trainee.is_demo == True))  # noqa: E712
        db.exec(delete(Team).where(Team.is_demo == True))  # noqa: E712
        db.exec(delete(ScenarioDraftRow).where(ScenarioDraftRow.is_demo == True))  # noqa: E712
        db.commit()
    print(f"已删除演示数据（训练局 {len(ids)} 个）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset-demo", action="store_true")
    ap.add_argument("--regen-downtime", action="store_true")
    args = ap.parse_args()
    rt = Runtime()
    cfg = yaml.safe_load((ROOT / "content" / "seed" / "demo_config.yaml").read_text(encoding="utf-8"))
    rng = random.Random(cfg["seed"])
    dpath = ROOT / "content" / "seed" / cfg["downtime_records"]["file"]
    if args.regen_downtime or not dpath.exists():
        gen_downtime(dpath, cfg["downtime_records"], rng)
        print(f"生成合成停机记录：{dpath}")
    print("导入停机记录：", rt.import_downtime())
    if args.reset_demo:
        reset_demo(rt)
    with rt.db() as db:
        has_demo = db.exec(select(Trainee).where(Trainee.is_demo == True)).first() is not None  # noqa: E712
    if has_demo:
        print("演示学员已存在，跳过（需要重新生成请加 --reset-demo）")
    else:
        stats = gen_demo(rt, cfg)
        print(f"生成演示学员 {stats['trainees']} 人、训练局 {stats['sessions']} 局（全部标记为演示数据）")
    print("关卡版本：", sum(len(rt.versions(sid)) for sid in rt.content.scenarios), "条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
