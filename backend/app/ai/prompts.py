"""提示词加载。提示词放在 backend/app/prompts/，每个文件头部写版本号；调用日志与评测结果记录版本。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


class Prompt:
    def __init__(self, name: str, meta: dict[str, Any], body: str):
        self.name = name
        self.meta = meta
        self.body = body

    @property
    def version(self) -> str:
        return f"{self.name}@v{self.meta.get('version', 0)}"

    def render(self, **kw: Any) -> str:
        return self.body.format(**kw)


@lru_cache(maxsize=32)
def load_prompt(name: str) -> Prompt:
    raw = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    meta: dict[str, Any] = {}
    body = raw
    if raw.startswith("---"):
        _, fm, body = raw.split("---", 2)
        meta = yaml.safe_load(fm) or {}
    return Prompt(name, meta, body.strip())


@lru_cache(maxsize=4)
def load_yaml_prompt(name: str) -> dict[str, Any]:
    return yaml.safe_load((PROMPT_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


def prompt_versions() -> dict[str, str]:
    out = {}
    for p in sorted(PROMPT_DIR.glob("*.md")):
        out[p.stem] = load_prompt(p.stem).version
    return out
