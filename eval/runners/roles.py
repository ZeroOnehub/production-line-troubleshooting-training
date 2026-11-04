"""各角色的评测 runner：直接调用训练端同一套 Roles 代码。"""
from __future__ import annotations

import time
from typing import Any

from app.engine.validator import validate_yaml_text

from ..common import System, prepare_session


def run_narrator(sys_: System, case: dict[str, Any]) -> dict[str, Any]:
    s = prepare_session(sys_.engine, case.get("context", {}))
    t0 = time.perf_counter()
    out = sys_.roles.narrate(s, case["input"], session_id=f"eval:{case['id']}")
    out["wall_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    out["engine_texts"] = [r.get("observation") or r.get("message") or "" for r in out.get("results", [])]
    return out


def run_coach(sys_: System, case: dict[str, Any]) -> dict[str, Any]:
    s = prepare_session(sys_.engine, case.get("context", {}))
    t0 = time.perf_counter()
    out = sys_.roles.hint(s, question=case.get("input") or None, level=case["level"], session_id=f"eval:{case['id']}", record=False)
    out["wall_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def run_phone(sys_: System, case: dict[str, Any]) -> dict[str, Any]:
    s = prepare_session(sys_.engine, case.get("context", {}))
    t0 = time.perf_counter()
    out = sys_.roles.phone_turn(s, case["input"], session_id=f"eval:{case['id']}")
    out["wall_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def run_scriptwriter(sys_: System, case: dict[str, Any]) -> dict[str, Any]:
    t0 = time.perf_counter()
    out = sys_.roles.write_script(case["input"], draft_id=f"eval:{case['id']}")
    out["wall_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    rep = validate_yaml_text(sys_.engine.c, out["yaml"])
    rep.pop("scenario", None)
    out["validation"] = {"ok": rep["ok"], "summary": rep["summary"], "format_errors": rep.get("format_errors", [])[:5],
                         "reference_errors": rep.get("reference_errors", [])[:5],
                         "replay_failed": [r["variant"] for r in rep.get("replay", []) if not r["passed"]]}
    return out


RUNNERS = {"narrator": run_narrator, "coach": run_coach, "phone": run_phone, "scriptwriter": run_scriptwriter}
