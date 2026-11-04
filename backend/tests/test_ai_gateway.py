"""AI 编排与模型网关：用一个假的 OpenAI 兼容客户端验证在线调用、工具调用执行、录制、离线回放、
安全守卫拦截后重新生成、师傅提示泄露拦截、电话要素抽取、考官点评。不调用任何真实模型。"""
import json
import types

import pytest

from app.ai.guard import SafetyGuard
from app.ai.roles import Roles, rule_draft, split_yaml
from app.ai.rules_nlu import RuleNLU
from app.engine.validator import validate_yaml_text
from app.gateway.gateway import ModelGateway, NoModelAvailable
from app.gateway.pricing import audio_cost_usd, cost_usd
from app.settings import Settings


def _msg(content=None, tool_calls=None):
    tcs = []
    for i, (name, args) in enumerate(tool_calls or []):
        tcs.append(types.SimpleNamespace(id=f"call_{i}", function=types.SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False))))
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content, tool_calls=tcs or None))],
        usage=types.SimpleNamespace(prompt_tokens=1000, completion_tokens=100),
    )


class FakeClient:
    """按调用顺序返回预设响应；记录收到的请求。"""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        if not self.script:
            raise RuntimeError("no more scripted responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture()
def settings(tmp_path, content):
    import yaml
    from app.settings import repo_root

    root = repo_root()
    models = yaml.safe_load((root / "config" / "models.yaml").read_text(encoding="utf-8"))
    catalog = json.loads((root / "config" / "model_catalog.json").read_text(encoding="utf-8"))
    return Settings(root=root, app_mode="online", record_calls=True, database_url="sqlite://", content_dir=root / "content",
                    recordings_dir=tmp_path / "rec", logs_dir=tmp_path / "logs", models=models, catalog=catalog)


def make(settings, script, monkeypatch):
    monkeypatch.setenv("AIHUBMIX_API_KEY", "test-key-not-real")
    fc = FakeClient(script)
    gw = ModelGateway(settings, client_factory=lambda base, key, timeout: fc)
    return gw, fc


def test_pricing(settings):
    c = cost_usd(settings.catalog, "qwen3.6-27b", 1_000_000, 1_000_000)
    assert c == pytest.approx(0.422 + 2.532)
    assert cost_usd(settings.catalog, "nope", 1, 1) is None
    assert audio_cost_usd(settings.catalog, "whisper-large-v3", seconds=10) > 0
    assert audio_cost_usd(settings.catalog, "tts-1", characters=100) > 0


def test_narrator_online_tool_calls_and_recording(settings, engine, monkeypatch, alarm_session):
    gw, fc = make(settings, [
        _msg(tool_calls=[("act", {"action": "inspect", "target": "bit"})]),
        _msg(content="你凑近看了批头，刀口明显磨圆了，边缘发亮。"),
    ], monkeypatch)
    roles = Roles(engine, gw)
    s = alarm_session("SCN-T101", "RC-T101-A")
    out = roles.narrate(s, "帮我瞅一眼那个头是不是磨平了", session_id="S1")
    assert out["mode"] == "online" and out["calls"] == 2
    assert out["steps"] == [{"action": "inspect", "params": {"target": "bit"}}]
    assert "磨圆" in out["results"][0]["observation"]
    assert out["reply"].startswith("你凑近")
    # 第一轮请求带了工具，主持人看不到根因
    req = fc.requests[0]
    assert req["tools"][0]["function"]["name"] == "act"
    assert "批头磨损" not in json.dumps(req["messages"], ensure_ascii=False)
    # 调用日志写入成本
    log = (settings.logs_dir / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()
    rec = json.loads(log[-1])
    assert rec["role"] == "narrator" and rec["cost_usd"] > 0
    # 录制文件 → 离线回放
    assert list((settings.recordings_dir).glob("*.jsonl"))
    gw2 = ModelGateway(settings, client_factory=lambda *a: (_ for _ in ()).throw(AssertionError("不应调用在线模型")))
    gw2.mode_override = "offline"
    roles2 = Roles(engine, gw2)
    s2 = alarm_session("SCN-T101", "RC-T101-A")
    out2 = roles2.narrate(s2, "帮我瞅一眼那个头是不是磨平了")
    assert out2["mode"] == "replay" and out2["reply"] == out["reply"]
    # 离线时没有录制的输入 → 规则兜底
    out3 = roles2.narrate(s2, "先暂停")
    assert out3["mode"] == "fallback" and s2.mode == "paused"


def test_narrator_guard_regenerates(settings, engine, monkeypatch, alarm_session):
    gw, fc = make(settings, [
        _msg(tool_calls=[("act", {"action": "open_electrical_cabinet"})]),
        _msg(content="柜门打不开的话，你可以先打开电气柜看看接线。"),
        _msg(content="打开电气柜被拒绝了：这不在操作工权限内。"),
    ], monkeypatch)
    roles = Roles(engine, gw)
    s = alarm_session("SCN-T108", "RC-T108-A")
    out = roles.narrate(s, "把电气柜打开看看接线")
    assert out["guard_blocked"] and "被拒绝" in out["reply"]
    assert "R-PRM-01" in [v.rule_id for v in s.violations]


def test_narrator_clarification_and_chitchat(settings, engine, monkeypatch, alarm_session):
    gw, _ = make(settings, [
        _msg(tool_calls=[("ask_clarification", {"question": "你想看哪儿？"})]),
        _msg(content="说说你要做什么操作。"),
    ], monkeypatch)
    roles = Roles(engine, gw)
    s = alarm_session("SCN-T101", "RC-T101-A")
    assert roles.narrate(s, "看看那个")["reply"] == "你想看哪儿？"
    assert roles.narrate(s, "今天好累")["reply"].startswith("说说")


def test_narrator_no_tool_call_backstop(settings, engine, monkeypatch, alarm_session):
    """模型没调工具却回“已执行等待操作”。规则能确定动作时按规则执行；确定不了时不许声称已执行。"""
    gw, _ = make(settings, [
        _msg(content="好的，已执行等待操作。请留意维修人员到达现场。"),
        _msg(content="好的，已经帮你暂停了设备。"),
    ], monkeypatch)
    roles = Roles(engine, gw)
    s = alarm_session("SCN-T104", "RC-T104-D")
    engine.act(s, "inspect", {"target": "pos_sensor"})
    engine.act(s, "pull_andon", {"target": "maintenance"})
    engine.act(s, "andon_report", {"elements": "all"}, actor="phone")
    assert s.state == "waiting_repair"
    out = roles.narrate(s, "等维修来")
    assert out["backstop"] == "rules" and [st["action"] for st in out["steps"]] == ["wait"]
    assert s.state != "waiting_repair" and "到场" in out["reply"]
    out2 = roles.narrate(s, "嗯嗯那个弄好")
    assert out2.get("claim_blocked") and "已经" not in out2["reply"] and not out2["results"]


def test_mode_badge_reflects_recent_failure(settings, engine, monkeypatch, alarm_session):
    """断网时右上角不能还显示“在线”：最近一次在线调用失败 → 不健康；下一次成功 → 恢复。"""
    gw, _ = make(settings, [RuntimeError("network down"), _msg(content="说说你要做什么操作。")], monkeypatch)
    roles = Roles(engine, gw)
    s = alarm_session("SCN-T101", "RC-T101-A")
    assert not gw.online_unhealthy()
    roles.narrate(s, "今天好累")
    assert gw.online_unhealthy()
    roles.narrate(s, "今天好累")
    assert not gw.online_unhealthy()


def test_online_failure_falls_back(settings, engine, monkeypatch, alarm_session):
    gw, _ = make(settings, [RuntimeError("timeout")], monkeypatch)
    roles = Roles(engine, gw)
    s = alarm_session("SCN-T101", "RC-T101-A")
    out = roles.narrate(s, "看看批头")
    assert out["mode"] == "fallback" and "磨圆" in out["reply"]


def test_coach_leak_blocked_and_regenerated(settings, engine, monkeypatch, alarm_session):
    gw, _ = make(settings, [
        _msg(content="批头磨损了，换批头就行。"),
        _msg(content="批头寿命到了吧。"),
    ], monkeypatch)
    roles = Roles(engine, gw)
    s = alarm_session("SCN-T101", "RC-T101-A")
    h = roles.hint(s)
    assert h["level"] == 1 and h["leak_blocked"] and h["mode"] == "fallback_after_leak"
    assert not roles.leaks(h["text"], ["磨损", "批头磨损", "寿命到了"])
    # 同一进度再要提示 → 升级
    assert roles.next_hint_level(s) == 2


def test_coach_level3_allows_answer(settings, engine, monkeypatch, alarm_session):
    gw, fc = make(settings, [_msg(content="先按暂停，把这件 NG 放进红箱子，再换批头。")], monkeypatch)
    roles = Roles(engine, gw)
    s = alarm_session("SCN-T101", "RC-T101-A")
    h = roles.hint(s, level=3)
    assert h["level"] == 3 and not h["leak_blocked"] and "暂停" in h["text"]
    sys_prompt = fc.requests[0]["messages"][0]["content"]
    assert "批头磨损" in sys_prompt  # 师傅能看到根因


def test_phone_extraction_followup_and_report(settings, engine, monkeypatch, alarm_session):
    gw, _ = make(settings, [
        _msg(tool_calls=[("submit_report_elements", {"station": "OP30", "alarm_code": "T-104", "actions_done": [], "ng_count": None,
                                                     "follow_up": "你做了什么处理？有没有 NG？"})]),
        _msg(tool_calls=[("submit_report_elements", {"station": None, "alarm_code": None, "actions_done": ["inspect"], "ng_count": 0,
                                                     "follow_up": "知道了，马上过去。"})]),
    ], monkeypatch)
    roles = Roles(engine, gw)
    s = alarm_session("SCN-T104", "RC-T104-D")
    engine.act(s, "inspect", {"target": "pos_sensor"})
    engine.act(s, "pull_andon", {"target": "maintenance"})
    o1 = roles.phone_turn(s, "OP30 报 T-104")
    assert not o1["done"] and "处理" in o1["reply"]
    o2 = roles.phone_turn(s, "看了传感器，没有 NG")
    assert o2["done"] and o2["coverage"] == 1.0 and s.state == "waiting_repair"
    assert s.andon_reports[-1]["follow_ups"] == 1


def test_examiner_tool_and_fallback(settings, engine, monkeypatch, alarm_session):
    from app.engine.scoring import build_review

    gw, _ = make(settings, [_msg(tool_calls=[("submit_review", {"comment": "通过。下次先看报警。", "weakness_tags": ["报警识别", "不存在的标签"]})])], monkeypatch)
    roles = Roles(engine, gw)
    s = engine.new_session("SCN-T102", seed=1, root_cause="RC-T102-A")
    engine.run_autopilot(s)
    rev = build_review(engine.c, s)
    ex = roles.examine(rev)
    assert ex["mode"] == "online" and ex["ai_tags"] == ["报警识别"]
    gw.mode_override = "degraded"
    ex2 = roles.examine(rev)
    assert ex2["mode"] == "fallback" and len(ex2["comment"]) <= 200


def test_scriptwriter_online_and_rule_draft(settings, engine, content, monkeypatch):
    example = content.scenario_files["SCN-T104"].read_text(encoding="utf-8").replace("SCN-T104", "SCN-W104")
    gw, fc = make(settings, [_msg(content=f"```yaml\n{example}```\n从口述提取了四个根因。")], monkeypatch)
    roles = Roles(engine, gw)
    out = roles.write_script("T-104 夹具没到位，有时候是工件没放正……")
    assert out["mode"] == "online" and "SCN-W104" in out["yaml"] and "四个根因" in out["explanation"]
    assert validate_yaml_text(content, out["yaml"])["ok"]
    assert "部件与状态" in fc.requests[0]["messages"][0]["content"]
    # 规则草稿（降级）：口述里说清报警和两个部件 → 能通过校验
    yml, expl = rule_draft(content, RuleNLU(content), "T-104 夹具未到位。常见是夹具里有碎屑，要停机清理；也可能是到位传感器坏了，那得叫维修。")
    rep = validate_yaml_text(content, yml)
    assert rep["ok"], rep
    assert "规则降级" in expl
    assert split_yaml("no fence")[0] == "no fence"


def test_degraded_mode_raises(settings, monkeypatch):
    gw, _ = make(settings, [], monkeypatch)
    gw.mode_override = "degraded"
    with pytest.raises(NoModelAvailable):
        gw.chat("narrator", [{"role": "user", "content": "x"}])


def test_guard_cases(content):
    g = SafetyGuard(content)
    assert g.check("你可以打开电气柜看看接线").blocked
    assert g.check("把光栅屏蔽了先干着").blocked
    assert g.check("直接用手把碎屑扒出来").blocked
    assert not g.check("千万别屏蔽光栅，这个归维修。").blocked
    assert not g.check("先暂停，再用手把碎屑取出来。").blocked
    assert not g.check("打开电气柜被拒绝了。").blocked
    assert not g.check("").blocked
