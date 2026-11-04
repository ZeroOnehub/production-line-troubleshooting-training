"""安全守卫：检查所有 AI 输出是否建议了越权或违反安全规则的操作。

以规则检查为主：对照权限表（越权动作的名称与口语别名）和安全规则的危险表述；
命中且不在否定语境（“别、不要、不能、禁止……”）里即拦截，调用方重新生成或换成安全兜底回复。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..engine.ontology import Content

POST_NEGATIONS = ("被拒绝", "被拒", "拒绝", "越权", "不允许", "不行", "不可以", "不归", "不在你", "不在操作工", "没有权限", "无权", "不能做", "做不了", "不是你")
NEGATIONS = ("不要", "别", "不能", "不可以", "不准", "禁止", "严禁", "不得", "千万别", "绝不", "绝对不", "不许", "不该", "不用", "没让你", "谁让你", "不是你", "不归你", "无权", "拒绝", "越权", "不在操作工权限",
             # 批评或复述错误的语境（考官点评、师傅讲为什么）："违规调整传感器""擅自打开电气柜""应上报而非自行调试"
             "违规", "违反", "擅自", "私自", "试图", "而非", "不应", "避免", "防止", "不该你", "严禁")
# 带否定字但不是否定动作的安慰语（“别怕，你自己插一下”仍是越权建议）
NOT_NEGATION = ("别怕", "别急", "别慌", "别担心", "不要怕", "不要急", "不用怕", "不用急", "不用慌", "不用担心")
# 规则之外的危险表述（运行中伸手、带电作业等）
EXTRA_PATTERNS = [
    (r"(不用|不必|不需要)(先)?(暂停|停机|停线|拍急停)", "建议不停机作业"),
    (r"(伸手|用手|手伸).{0,6}(掏|扒|抠|拿|取|弄|清)", "建议伸手进夹具（未先停机）"),
    (r"(把|将)?(光栅|急停).{0,4}(屏蔽|短接|旁路|关掉|关了)", "建议屏蔽安全装置"),
    (r"(继续|接着)(复位|按复位).{0,6}(几次|试试|多按)", "建议反复复位"),
    (r"NG.{0,4}(件)?.{0,4}(先)?(放行|流下去|放过去)", "建议放行 NG 件"),
]


@dataclass
class GuardResult:
    blocked: bool
    hits: list[dict[str, str]] = field(default_factory=list)


class SafetyGuard:
    def __init__(self, content: Content):
        self.c = content
        self.terms: list[tuple[str, str]] = []
        for a in content.actions.values():
            if a["category"] != "forbidden":
                continue
            for t in {a["name"], *a.get("aliases", [])}:
                if len(t) >= 2:
                    self.terms.append((t, a["id"]))
        self.terms.sort(key=lambda x: -len(x[0]))

    @staticmethod
    def _negated(text: str, start: int, end: int | None = None, inner: bool = False) -> bool:
        window = text[max(0, start - 10):start]
        for x in NOT_NEGATION:
            window = window.replace(x, "")
        # 句子边界之前的否定词不算
        for sep in "。！？!?；;\n":
            if sep in window:
                window = window.split(sep)[-1]
        if any(n in window for n in NEGATIONS):
            return True
        if end is not None:
            # 否定词夹在对象和动词之间：“程序和参数谁让你改的”（规则 R-PRM-02 的原话）、“电气柜别开”。
            # 只用于越权动作；“不用停机”这类危险表述本身就含否定词，不适用
            # 只认紧挨在动词前的否定（“参数谁让你改”“电气柜别开”）；“插头别怕，自己插一下”不算否定
            span = text[start:end]
            if inner and any(span[:-k].endswith(n) for k in (1, 2) if len(span) > k for n in NEGATIONS):
                return True
            after = text[end:end + 12]
            for sep in "。！？!?；;\n":
                if sep in after:
                    after = after.split(sep)[0]
            if any(n in after for n in POST_NEGATIONS):
                return True
        return False

    def check(self, text: str | None) -> GuardResult:
        if not text:
            return GuardResult(False)
        t = text.replace(" ", "")
        hits: list[dict[str, str]] = []
        # 复用口语解析器识别“动词 + 对象”式的越权说法（如“扫码枪插头你自己插一下”“把扭矩下限调低”）
        from .rules_nlu import RuleNLU, normalize

        if not hasattr(self, "_nlu"):
            self._nlu = RuleNLU(self.c)
        nt = normalize(text).lower()
        for (a, b, label) in self._nlu.parse(text).spans:
            if label in self.c.actions and self.c.actions[label]["category"] == "forbidden" and not self._negated(nt, a, b, inner=True):
                hits.append({"term": nt[a:b], "action": label, "kind": "越权动作"})
        for term, aid in self.terms:
            for m in re.finditer(re.escape(term), t):
                if not self._negated(t, m.start(), m.end(), inner=True):
                    hits.append({"term": term, "action": aid, "kind": "越权动作"})
                    break
        for pat, why in EXTRA_PATTERNS:
            for m in re.finditer(pat, t):
                sentence = re.split(r"[。！？!?；;\n]", t[:m.start()])[-1]
                if why.startswith("建议伸手") and re.search(r"暂停|急停|停机|停稳|停下", sentence):
                    continue
                if not self._negated(t, m.start(), m.end()):
                    hits.append({"term": m.group(0), "action": "", "kind": why})
                    break
        return GuardResult(bool(hits), hits)

    SAFE_FALLBACK = "这个不在操作工权限内，也不安全。先暂停设备，处理不了就拉 Andon 叫人。"
