"""规则降级模式的口语理解：模型不可用时，主持人和电话角色用它兜底。

词表全部来自本体（动作与目标的口语别名），这里只有匹配逻辑。它也作为评测台里的 rule-baseline，
用来说明“模型挂了”时系统还能做到什么程度。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..engine.ontology import Content

LOOK_VERBS = ["看一看", "看一下", "看一眼", "看看", "看下", "瞅一眼", "瞅瞅", "瞅一下", "瞅", "瞧一瞧", "瞧瞧", "瞧", "检查一下", "检查",
              "查看", "查一下", "观察", "确认一下", "瞄一眼", "瞄", "盯一下", "摸一下", "看了", "查了", "检查了", "看过", "看"]
QUESTION_TAILS = ["报的什么", "写的什么", "显示什么", "什么报警", "亮没亮", "亮不亮", "开没开", "松没松", "有没有东西", "坏没坏", "好没好", "怎么样", "什么情况", "啥情况", "正常吗", "正不正常", "有没有问题", "咋样", "怎么了", "是多少", "多少"]
ANDON_WORDS = {"maintenance": ["维修", "机修", "设备员", "电工"], "quality": ["质量", "品质", "检验", "QC", "qc"],
               "team_leader": ["班组长", "班长", "组长", "领班"]}
# “动词 + 对象”组合：动词与对象之间允许夹几个字（“换个起子头”“换了批头”“把定位块换成 B 型”）。对象用本体里的目标 ID。
COMBOS: list[tuple[list[str], list[str], str]] = [
    (["更换", "换上", "换掉", "换成", "换个", "换一个", "换了", "换"], ["bit", "bit_spare_box"], "replace_bit"),
    (["更换", "换成", "换了", "换"], ["locating_block"], "replace_locating_block"),
    (["更换", "换成", "换了", "换"], ["bolt_box"], "replace_bolt_box"),
    (["擦干净", "擦一擦", "擦一下", "擦了", "擦", "清洁", "清理", "弄干净", "清"], ["scan_window"], "clean_scanner_window"),
    (["清理", "清一下", "吹干净", "吹一下", "吹", "擦", "清", "弄干净", "扒", "抠"], ["fixture_seat"], "clean_fixture"),
    (["擦干净", "擦一下", "擦了", "擦", "清洁", "清理"], ["curtain_mirror"], "clean_curtain_mirror"),
    (["装紧", "拧紧", "紧一下", "紧了", "装好", "装到位", "重新装", "紧"], ["socket"], "tighten_socket"),
    (["打开", "开大", "开足", "开到底", "开了", "开"], ["hand_valve"], "open_hand_valve"),
    (["隔离", "隔开", "挪走"], ["bolt_box"], "isolate_bolt_box"),
    (["隔离", "放进NG", "放到NG", "放NG"], ["workpiece"], "isolate_ng_part"),
    (["放进", "放到", "放入", "扔进", "放"], ["ng_bin"], "isolate_ng_part"),
    (["拿走", "拿开", "移开", "挪开", "搬走", "移走"], ["curtain_zone"], "remove_obstruction"),
    (["放正", "重新放", "摆正", "放好", "重放"], ["workpiece", "fixture_seat"], "reposition_workpiece"),
    (["插紧", "插好", "重插", "重新插", "拔插", "插一下", "插上"], ["scanner_cable", "controller_link"], "reconnect_cable"),
    (["调整", "调一下", "调", "掰", "挪", "对一下", "对准"], ["pos_sensor", "curtain_mirror", "bolt_level_sensor"], "adjust_sensor"),
    (["打开", "开一下", "开"], ["electrical_cabinet"], "open_electrical_cabinet"),
    (["消一下", "消掉", "消", "清一下", "清掉", "复位一下"], ["hmi"], "reset_alarm"),
    (["确认了", "确认一下", "确认", "看了一圈", "看了", "看过"], ["surroundings"], "safety_check"),
    (["拆开", "拆下来", "拆了", "拆"], ["gun_motor", "controller_link", "fixture_seat", "scan_window", "pos_sensor"], "disassemble_equipment"),
    (["屏蔽", "短接", "旁路", "关掉", "关了"], ["curtain_mirror", "curtain_zone", "estop_button"], "bypass_safety"),
]
NEGATION_BEFORE = ("不用", "不要", "不必", "无需", "别", "不", "没", "甭", "没有", "没敢")
# 状态描述（不是动作）：如“手阀全开”“料盒是满的”——汇报里常见，不能当成“打开手阀”
OBSERVATION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(手阀|阀门|球阀)(是|都|已经)?全开(的|着)?"), "hand_valve"),
    (re.compile(r"(料盒|螺栓料盒)(是|都)?满的"), "bolt_box"),
    (re.compile(r"(压力表|气压表|气压)(是|在)?\d+(\.\d+)?"), "pressure_gauge"),
]
PARAM_RE = re.compile(r"(扭矩|角度|下限|上限|参数|设定值|拧紧参数).{0,4}(调|改)|(调|改).{0,3}(扭矩|角度|下限|上限|参数|设定值)")
# 疑问句：“工件放好了没”“传感器坏了吗”——里面的动作词不是指令，按“查看”处理
QUESTION_END = re.compile(r"(了)?(没有?|吗|么|没呢|不)$|[?？]$")
QUALITY_CONFIRM_RE = re.compile(r"(质量员?|品质).{0,4}(确认|签字|签)(一下)?(首件)?")
PROGRAM_RE = re.compile(r"(程序号?).{0,4}(切|选|换|改)|(切|选|换|改).{0,4}(程序号?)")
VARIANT_RE = re.compile(r"(型号).{0,4}(切|选|换|改)|(切|选|换|改|选择).{0,4}(型号|[AB]型)")
VAGUE = ["那个", "这个", "弄一下", "搞一下", "处理一下", "看着办", "怎么办", "咋办", "帮我弄"]
CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
ALARM_KEYWORDS = {
    "T-101": ["扭矩低", "扭矩不够", "扭矩小", "扭矩NG", "扭力低", "扭矩"],
    "T-102": ["角度超差", "角度超", "角度NG", "角度"],
    "T-103": ["扫码失败", "扫不上", "扫不出", "读不出码", "扫码"],
    "T-104": ["夹具未到位", "夹不到位", "没夹到位", "夹不紧", "夹具没到位", "未到位"],
    "T-105": ["光栅中断", "光栅", "光幕"],
    "T-106": ["型号不符", "防错", "型号不对"],
    "T-107": ["气源压力低", "气压低", "压力低", "气压不够"],
    "T-108": ["通讯中断", "通讯", "通信"],
    "T-109": ["缺料", "没螺栓了", "螺栓不够", "没料"],
    "T-110": ["急停被按", "急停按下", "急停"],
}


def normalize(text: str) -> str:
    t = text.strip()
    t = t.translate(str.maketrans("ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ０１２３４５６７８９，。！？；：（）",
                                  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789,.!?;:()"))
    t = re.sub(r"\s+", "", t)
    return t


def cn_to_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    if s in CN_NUM:
        return CN_NUM[s]
    if len(s) == 2 and s[0] == "十" and s[1] in CN_NUM:
        return 10 + CN_NUM[s[1]]
    if len(s) == 2 and s[1] == "十" and s[0] in CN_NUM:
        return CN_NUM[s[0]] * 10
    return None


@dataclass
class ParseResult:
    steps: list[dict[str, Any]] = field(default_factory=list)
    clarify: str | None = None
    spans: list[tuple[int, int, str]] = field(default_factory=list)


class RuleNLU:
    def __init__(self, content: Content):
        self.c = content
        self.targets = sorted(((a.lower(), t) for a, t in content.target_aliases.items() if len(a) >= 1), key=lambda x: -len(x[0]))
        acts = []
        for aid, a in content.actions.items():
            if aid in ("inspect", "andon_report"):
                continue
            for al in {a["name"], *a.get("aliases", [])}:
                acts.append((al.lower().replace(" ", ""), aid))
        self.forbidden = sorted([x for x in acts if content.actions[x[1]]["category"] == "forbidden"], key=lambda x: -len(x[0]))
        self.others = sorted([x for x in acts if content.actions[x[1]]["category"] != "forbidden"], key=lambda x: -len(x[0]))

    # ------------------------------------------------------------------
    @staticmethod
    def _negated(t: str, start: int, end: int) -> bool:
        """动作前面紧挨着否定词（“不暂停”“别复位”“没敢复位”），或后面紧跟“被”（“急停被按了”，描述状态）。"""
        before = t[max(0, start - 2):start]
        if any(before.endswith(n) for n in NEGATION_BEFORE):
            return True
        return t[end:end + 1] == "被" or t[max(0, start - 1):start] == "被"

    @staticmethod
    def _question_after(t: str, end: int) -> bool:
        tail = re.split(r"[,.;!，。；！]", t[end:], maxsplit=1)[0]
        return bool(QUESTION_END.search(tail)) if tail else False

    def _free(self, claimed: list[tuple[int, int]], s: int, e: int) -> bool:
        return all(e <= a or s >= b for a, b in claimed)

    def _find_target(self, t: str, start: int, claimed: list[tuple[int, int]], window: int = 14) -> tuple[int, int, str] | None:
        best = None
        for alias, tid in self.targets:
            idx = t.find(alias, start, start + window + len(alias))
            while idx != -1:
                if self._free(claimed, idx, idx + len(alias)):
                    if best is None or idx < best[0] or (idx == best[0] and len(alias) > best[1] - best[0]):
                        best = (idx, idx + len(alias), tid)
                    break
                idx = t.find(alias, idx + 1, start + window + len(alias))
        return best

    def parse(self, text: str, snap: dict[str, Any] | None = None) -> ParseResult:
        raw = normalize(text)
        t = raw.lower()
        snap = snap or {}
        found: list[tuple[int, dict[str, Any]]] = []
        claimed: list[tuple[int, int]] = []
        spans: list[tuple[int, int, str]] = []

        def claim(s: int, e: int, label: str) -> None:
            claimed.append((s, e))
            spans.append((s, e, label))

        # 1) 越权与危险动作优先
        for alias, aid in self.forbidden:
            for m in re.finditer(re.escape(alias), t):
                if self._free(claimed, m.start(), m.end()) and not self._negated(t, m.start(), m.end()):
                    claim(m.start(), m.end(), aid)
                    found.append((m.start(), {"action": aid, "params": {}}))
        for m in PARAM_RE.finditer(t):
            if self._free(claimed, m.start(), m.end()) and not self._negated(t, m.start(), m.end()):
                claim(m.start(), m.end(), "modify_program_params")
                found.append((m.start(), {"action": "modify_program_params", "params": {}}))
        # 1.2) 状态描述 → 查看（汇报里常见）
        for rx, tid in OBSERVATION_PATTERNS:
            for m in rx.finditer(t):
                if self._free(claimed, m.start(), m.end()):
                    claim(m.start(), m.end(), "inspect")
                    found.append((m.start(), {"action": "inspect", "params": {"target": tid}}))
        # 2) “看”类动词 + 目标
        for verb in LOOK_VERBS:
            for m in re.finditer(re.escape(verb), t):
                if not self._free(claimed, m.start(), m.end()):
                    continue
                tgt = self._find_target(t, m.end(), claimed)
                if tgt is None:
                    continue
                # 动词与目标之间如果夹着别的动作别名（例如“看看再换批头”），不算查看
                between = t[m.end():tgt[0]]
                if any(al in between for al, _ in self.others if len(al) >= 2):
                    continue
                claim(m.start(), m.end(), "inspect")
                claim(tgt[0], tgt[1], tgt[2])
                found.append((m.start(), {"action": "inspect", "params": {"target": tgt[2]}}))
        # 1.5) 动词 + 对象组合（越权组合先行）
        combos = sorted(COMBOS, key=lambda x: 0 if self.c.actions[x[2]]["category"] == "forbidden" else 1)
        for verbs, tids, aid in combos:
            for verb in sorted(verbs, key=len, reverse=True):
                for m in re.finditer(re.escape(verb.lower()), t):
                    if not self._free(claimed, m.start(), m.end()) or self._negated(t, m.start(), m.end()):
                        continue
                    # 对象可以在动词后（换批头），也可以在动词前（把批头换了 / 定位块换成）
                    hit = None
                    for alias, tid in self.targets:
                        if tid not in tids:
                            continue
                        a_idx = t.find(alias, m.end(), m.end() + 4 + len(alias))
                        b_idx = t.rfind(alias, max(0, m.start() - 6 - len(alias)), m.start())
                        for idx in (a_idx, b_idx):
                            if idx != -1 and self._free(claimed, idx, idx + len(alias)):
                                hit = (idx, idx + len(alias))
                                break
                        if hit:
                            break
                    if hit:
                        claim(m.start(), m.end(), aid)
                        claim(hit[0], hit[1], "obj")
                        if self._question_after(t, max(m.end(), hit[1])) and self.c.actions[aid]["category"] != "forbidden":
                            obj = next(tid for al, tid in self.targets if t[hit[0]:hit[1]] == al)
                            found.append((min(m.start(), hit[0]), {"action": "inspect", "params": {"target": obj}}))
                        else:
                            found.append((min(m.start(), hit[0]), {"action": aid, "params": {}}))
        for rx, aid in ((QUALITY_CONFIRM_RE, "call_quality_confirm"), (PROGRAM_RE, "hmi_select_program"), (VARIANT_RE, "hmi_select_variant")):
            for m in rx.finditer(t):
                if self._free(claimed, m.start(), m.end()):
                    claim(m.start(), m.end(), aid)
                    found.append((m.start(), {"action": aid, "params": {}}))
        # 3) 其他动作别名
        for alias, aid in self.others:
            for m in re.finditer(re.escape(alias), t):
                if not self._free(claimed, m.start(), m.end()):
                    continue
                if self._negated(t, m.start(), m.end()):
                    # 被否定的动作不执行，但占住位置，避免更短的别名（如“停”）再次命中
                    claim(m.start(), m.end(), "negated")
                    continue
                claim(m.start(), m.end(), aid)
                found.append((m.start(), {"action": aid, "params": {}}))
        # 4) “X 怎么样 / X 是多少”式提问 → 查看 X
        for tail in QUESTION_TAILS:
            for m in re.finditer(re.escape(tail), t):
                if not self._free(claimed, m.start(), m.end()):
                    continue
                cands = []
                for gap in range(0, 5):
                    for alias, tid in self.targets:
                        st0 = m.start() - gap - len(alias)
                        if st0 >= 0 and t[st0:st0 + len(alias)] == alias and self._free(claimed, st0, m.start()):
                            cands.append((-len(alias), gap, st0, tid))
                if cands:
                    _, _, st0, tid = min(cands)
                    claim(st0, m.end(), tid)
                    found.append((st0, {"action": "inspect", "params": {"target": tid}}))
        found.sort(key=lambda x: x[0])
        steps = [st for _, st in found]
        # 去掉相邻重复
        dedup: list[dict[str, Any]] = []
        for st in steps:
            if not dedup or dedup[-1] != st:
                dedup.append(st)
        steps = dedup
        clarify = None
        for st in steps:
            q = self._fill_params(st, raw, t, snap)
            if q and not clarify:
                clarify = q
        if not steps:
            if any(v in t for v in VAGUE) or any(t.startswith(v) for v in ("看", "瞅", "瞧", "查")):
                clarify = "你想看哪儿、做什么？说具体点，比如“看看批头”“先暂停”。"
            else:
                clarify = "没听明白你要做什么操作，能说具体一点吗？"
        return ParseResult(steps=steps if not clarify else steps, clarify=clarify, spans=spans)

    def _fill_params(self, st: dict[str, Any], raw: str, t: str, snap: dict[str, Any]) -> str | None:
        a = st["action"]
        p = st["params"]
        if a == "pull_andon":
            for target, words in ANDON_WORDS.items():
                if any(w.lower() in t for w in words):
                    p["target"] = target
                    break
            if "target" not in p:
                return "要叫谁？维修、质量员还是班组长？"
        if a in ("hmi_select_variant", "replace_locating_block", "replace_bolt_box"):
            m = re.search(r"([AB])(型|号)?", raw.upper().replace("ANDON", "").replace("NG", "").replace("HMI", ""))
            if m:
                p["variant"] = m.group(1)
            else:
                v = (snap.get("changeover") or {}).get("to_variant") or snap.get("order_variant")
                if v:
                    p["variant"] = v
                else:
                    return "换成哪个型号？A 型还是 B 型？"
        if a == "hmi_select_program":
            m = re.search(r"P0?([12])", raw.upper())
            if m:
                p["program"] = f"P0{m.group(1)}"
            else:
                m2 = re.search(r"([AB])型", raw.upper())
                v = m2.group(1) if m2 else ((snap.get("changeover") or {}).get("to_variant") or snap.get("order_variant"))
                if v:
                    p["program"] = self.c.variants[v]["program"]
                else:
                    return "切到哪个程序号？"
        if a == "record_check":
            m = re.search(r"CK-?0?(\d)", raw.upper())
            if m:
                p["item"] = f"CK-0{m.group(1)}"
            p["result"] = "ng" if any(w in t for w in ("异常", "有问题", "不正常", "ng", "坏")) else "ok"
            if "item" not in p:
                return "记哪一项？说点检项编号，比如 CK-01。"
        if a == "reset_alarm" and snap:
            ind = snap.get("indicators") or {}
            if ind.get("estop") == "pressed" and "急停" in t:
                st["action"] = "release_estop"
        if a == "emergency_stop" and ("复位" in t or "拔起" in t or "松开" in t):
            st["action"] = "release_estop"
        return None

    # ------------------------------------------------------------------
    # 电话汇报要素抽取（规则版）
    # ------------------------------------------------------------------
    def extract_report(self, text: str) -> dict[str, Any]:
        raw = normalize(text)
        up = raw.upper()
        station = None
        m = re.search(r"OP-?(\d{2})", up)
        if m:
            station = f"OP{m.group(1)}"
        else:
            m = re.search(r"(\d{2})(号)?工位", raw)
            if m:
                station = f"OP{m.group(1)}"
            elif "三十工位" in raw or "30工位" in raw:
                station = "OP30"
        alarm = None
        m = re.search(r"T-?(\d{3})", up)
        if m:
            alarm = f"T-{m.group(1)}"
        else:
            for code, kws in ALARM_KEYWORDS.items():
                if any(k.upper() in up for k in kws):
                    alarm = code
                    break
        ng = None
        if re.search(r"(没有|没|无|零|0)(件)?(NG|不良|废品)", up) or re.search(r"NG(件)?(是)?(0|零|没有)", up):
            ng = 0
        else:
            m = re.search(r"(\d+|[一二两三四五六七八九十]{1,2})(件|个)", raw)
            if m:
                ng = cn_to_int(m.group(1))
            else:
                m = re.search(r"NG(件)?(有)?(\d+|[一二两三四五六七八九十]{1,2})", up)
                if m:
                    ng = cn_to_int(m.group(3))
        parsed = self.parse(text)
        acts = []
        for st in parsed.steps:
            if st["action"] in ("pull_andon", "wait") or self.c.actions[st["action"]]["category"] == "forbidden":
                continue
            if st["action"] not in acts:
                acts.append(st["action"])
        return {"station": station, "alarm_code": alarm, "actions_done": acts, "ng_count": ng}
