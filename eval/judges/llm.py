"""大模型裁判：用与被测模型不同的模型判定“提示是否泄露答案”“描述是否忠实”。结果按 10% 抽样人工复核。"""
from __future__ import annotations

import json
import re
from typing import Any

from app.ai.prompts import load_prompt
from app.gateway.gateway import ModelGateway, NoModelAvailable


def pick_judge(settings, tested: str) -> str | None:
    judge = (settings.models.get("roles", {}).get("judge") or {}).get("model")
    if judge and judge != tested:
        return judge
    for c in settings.models.get("candidates", []):
        if c["id"] != tested:
            return c["id"]
    return None


def _json(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def judge_leak(gw: ModelGateway, model: str, root_cause: str, level: int, hint: str) -> dict[str, Any] | None:
    p = load_prompt("judge_leak")
    try:
        r = gw.chat("judge", [{"role": "user", "content": p.render(root_cause=root_cause, level=level, hint=hint)}], model=model,
                    prompt_version=p.version, mode="online")
    except NoModelAvailable:
        return None
    return _json(r.content)


def judge_faithful(gw: ModelGateway, model: str, engine_text: str, narration: str) -> dict[str, Any] | None:
    p = load_prompt("judge_faithfulness")
    try:
        r = gw.chat("judge", [{"role": "user", "content": p.render(engine_text=engine_text, narration=narration)}], model=model,
                    prompt_version=p.version, mode="online")
    except NoModelAvailable:
        return None
    return _json(r.content)
