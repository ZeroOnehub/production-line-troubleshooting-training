"""录制与回放：在线打“标准演示局”时把每次大模型调用的输入输出存下来，离线时按输入匹配回放。

匹配顺序：完全一致（角色 + 消息 + 工具）→ 同角色、最后一条用户消息相同 → 同角色、最后一条用户消息相似度 ≥ 0.85。
都匹配不到时返回 None，由调用方走规则兜底（界面标识“降级·规则”）。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def _canonical(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> str:
    def clean(m: dict[str, Any]) -> dict[str, Any]:
        out = {"role": m.get("role"), "content": m.get("content")}
        if m.get("tool_calls"):
            out["tool_calls"] = [{"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]} for tc in m["tool_calls"]]
        if m.get("name"):
            out["name"] = m["name"]
        return out

    tool_names = sorted(t["function"]["name"] for t in (tools or []))
    return json.dumps({"messages": [clean(m) for m in messages], "tools": tool_names}, ensure_ascii=False, sort_keys=True)


def request_key(role: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> str:
    raw = role + "|" + _canonical(messages, tools)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def last_user(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


class RecordingStore:
    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self._index: dict[str, dict[str, Any]] | None = None
        self._by_user: dict[tuple[str, str], dict[str, Any]] = {}

    def _load(self) -> None:
        if self._index is not None:
            return
        self._index = {}
        self._by_user = {}
        if not self.dir.exists():
            return
        for f in sorted(self.dir.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._index[rec["key"]] = rec
                self._by_user[(rec["role"], rec.get("last_user", ""))] = rec

    def reload(self) -> None:
        self._index = None
        self._load()

    def append(self, name: str, role: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
               response: dict[str, Any], model: str, usage: dict[str, int], latency_ms: float) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        rec = {
            "key": request_key(role, messages, tools), "role": role, "model": model, "last_user": last_user(messages),
            "messages": messages, "tools": [t["function"]["name"] for t in (tools or [])],
            "response": response, "usage": usage, "latency_ms": latency_ms,
        }
        with _LOCK:
            with (self.dir / f"{name}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if self._index is not None:
            self._index[rec["key"]] = rec
            self._by_user[(role, rec["last_user"])] = rec

    def lookup(self, role: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> tuple[dict[str, Any] | None, str]:
        self._load()
        assert self._index is not None
        key = request_key(role, messages, tools)
        if key in self._index:
            return self._index[key], "exact"
        lu = last_user(messages)
        if (role, lu) in self._by_user and lu:
            return self._by_user[(role, lu)], "same_user_text"
        best, best_ratio = None, 0.0
        for (r, text), rec in self._by_user.items():
            if r != role or not text or not lu:
                continue
            ratio = difflib.SequenceMatcher(None, lu, text).ratio()
            if ratio > best_ratio:
                best, best_ratio = rec, ratio
        if best is not None and best_ratio >= 0.85:
            return best, f"similar:{best_ratio:.2f}"
        return None, "miss"

    def stats(self) -> dict[str, Any]:
        self._load()
        assert self._index is not None
        roles: dict[str, int] = {}
        for rec in self._index.values():
            roles[rec["role"]] = roles.get(rec["role"], 0) + 1
        files = sorted(p.name for p in self.dir.glob("*.jsonl")) if self.dir.exists() else []
        return {"total": len(self._index), "by_role": roles, "files": files}
