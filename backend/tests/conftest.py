import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("CONTENT_DIR", str(ROOT / "content"))
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("APP_MODE", "degraded")
os.environ.setdefault("RECORDINGS_DIR", str(ROOT / "data" / "test_recordings"))

from app.engine.engine import Engine  # noqa: E402
from app.engine.ontology import load_content  # noqa: E402


@pytest.fixture(scope="session")
def content():
    return load_content(ROOT / "content")


@pytest.fixture()
def engine(content):
    return Engine(content)


def start_alarm(engine, scn, rc, seed=7):
    s = engine.new_session(scn, seed=seed, root_cause=rc, compute_standard=False)
    engine.act(s, "run_production")
    return s


@pytest.fixture()
def alarm_session(engine):
    def _make(scn, rc, seed=7):
        return start_alarm(engine, scn, rc, seed)

    return _make
