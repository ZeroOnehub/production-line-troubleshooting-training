"""按公开价格折算成本（价格来自 config/model_catalog.json，不手填）。"""
from __future__ import annotations

from typing import Any


def cost_usd(catalog: dict[str, Any], model: str | None, input_tokens: int, output_tokens: int) -> float | None:
    if not model:
        return 0.0
    m = (catalog.get("models") or {}).get(model)
    if not m or m.get("input_per_mtok") is None or m.get("output_per_mtok") is None:
        return None
    return round(input_tokens / 1e6 * float(m["input_per_mtok"]) + output_tokens / 1e6 * float(m["output_per_mtok"]), 8)


def audio_cost_usd(catalog: dict[str, Any], model: str, seconds: float = 0.0, characters: int = 0) -> float | None:
    m = (catalog.get("models") or {}).get(model)
    if not m:
        return None
    if m.get("audio_input_per_second") is not None:
        return round(seconds * float(m["audio_input_per_second"]), 8)
    if m.get("per_input_character") is not None:
        return round(characters * float(m["per_input_character"]), 8)
    return None
