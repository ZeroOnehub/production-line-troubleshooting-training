"""运行配置：.env + config/models.yaml。API Key 只从环境变量读取，不写进任何文件。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


def repo_root() -> Path:
    env = os.environ.get("REPO_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "models.yaml").exists():
            return parent
    return Path("/app")


def _load_dotenv(path: Path) -> None:
    """极简 .env 读取（不覆盖已存在的环境变量）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0].strip().strip('"').strip("'")
        if v:   # 空值不写入环境变量，按未设置处理
            os.environ.setdefault(k.strip(), v)


@dataclass
class Settings:
    root: Path
    app_mode: str                     # online / offline / degraded
    record_calls: bool
    database_url: str
    content_dir: Path
    recordings_dir: Path
    logs_dir: Path
    models: dict[str, Any] = field(default_factory=dict)
    catalog: dict[str, Any] = field(default_factory=dict)
    think_time_cap_s: float = 30.0
    count_think_time: bool = True
    demo_unlock_all: bool = False

    def api_key(self, platform: str | None = None) -> str | None:
        platform = platform or self.models.get("provider", {}).get("default", "aihubmix")
        env = self.models.get("provider", {}).get("platforms", {}).get(platform, {}).get("api_key_env", "AIHUBMIX_API_KEY")
        v = os.environ.get(env)
        return v or None

    def base_url(self, platform: str | None = None) -> str:
        platform = platform or self.models.get("provider", {}).get("default", "aihubmix")
        return self.models["provider"]["platforms"][platform]["base_url"]

    def role_cfg(self, role: str) -> dict[str, Any]:
        return dict(self.models.get("roles", {}).get(role) or {})

    @property
    def effective_mode(self) -> str:
        """真实生效的模式：online 但没有 Key 时降级为规则模式。"""
        if self.app_mode == "online" and not self.api_key():
            return "degraded"
        return self.app_mode


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    root = repo_root()
    _load_dotenv(root / ".env")
    models = yaml.safe_load((root / "config" / "models.yaml").read_text(encoding="utf-8"))
    cat_path = root / models.get("pricing_catalog", "config/model_catalog.json")
    catalog = json.loads(cat_path.read_text(encoding="utf-8")) if cat_path.exists() else {"models": {}}
    data_dir = Path(os.environ.get("DATA_DIR", root / "data"))
    db_default = f"sqlite:///{data_dir / 'app.db'}"
    return Settings(
        root=root,
        app_mode=os.environ.get("APP_MODE", "online").strip().lower(),
        record_calls=os.environ.get("RECORD_CALLS", "false").lower() in ("1", "true", "yes"),
        database_url=os.environ.get("DATABASE_URL") or db_default,   # .env 里留空 = 用本地 SQLite
        content_dir=Path(os.environ.get("CONTENT_DIR", root / "content")),
        recordings_dir=Path(os.environ.get("RECORDINGS_DIR", data_dir / "recordings")),
        logs_dir=Path(os.environ.get("LOGS_DIR", data_dir / "logs")),
        models=models,
        catalog=catalog,
        think_time_cap_s=float(os.environ.get("THINK_TIME_CAP_S", "30")),
        count_think_time=os.environ.get("COUNT_THINK_TIME", "true").lower() in ("1", "true", "yes"),
        demo_unlock_all=os.environ.get("DEMO_UNLOCK_ALL", "false").lower() in ("1", "true", "yes"),
    )


def reset_settings() -> None:
    get_settings.cache_clear()
