"""条件求值：rules.yaml / alarms.yaml 中 `when` / `trigger` 的结构化条件。

写法：
    {fact: <名>, eq|ne|in|not_in|lt|lte|gt|gte: <值>}
    {all: [...]} / {any: [...]} / {not: {...}}
未知事实视为 None；与 None 比较大小一律为 False。
"""
from __future__ import annotations

from typing import Any, Mapping

_OPS = ("eq", "ne", "in", "not_in", "lt", "lte", "gt", "gte")


class ConditionError(ValueError):
    pass


def evaluate(cond: Mapping[str, Any] | None, facts: Mapping[str, Any]) -> bool:
    if cond is None:
        return True
    if not isinstance(cond, Mapping):
        raise ConditionError(f"条件必须是映射：{cond!r}")
    if "all" in cond:
        return all(evaluate(c, facts) for c in cond["all"])
    if "any" in cond:
        return any(evaluate(c, facts) for c in cond["any"])
    if "not" in cond:
        return not evaluate(cond["not"], facts)
    if "fact" not in cond:
        raise ConditionError(f"条件缺少 fact：{cond!r}")
    value = facts.get(cond["fact"])
    ops = [op for op in _OPS if op in cond]
    if len(ops) != 1:
        raise ConditionError(f"条件必须且只能有一个比较符：{cond!r}")
    op = ops[0]
    target = cond[op]
    if op == "eq":
        return value == target
    if op == "ne":
        return value != target
    if op == "in":
        return value in target
    if op == "not_in":
        return value not in target
    if value is None:
        return False
    try:
        if op == "lt":
            return value < target
        if op == "lte":
            return value <= target
        if op == "gt":
            return value > target
        return value >= target
    except TypeError:
        return False


def facts_used(cond: Mapping[str, Any] | None) -> set[str]:
    """列出条件引用的全部事实名（用于内容校验与文档生成）。"""
    if not cond:
        return set()
    out: set[str] = set()
    for key in ("all", "any"):
        if key in cond:
            for c in cond[key]:
                out |= facts_used(c)
    if "not" in cond:
        out |= facts_used(cond["not"])
    if "fact" in cond:
        out.add(cond["fact"])
    return out
