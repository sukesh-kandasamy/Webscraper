"""Every test writes to a temporary data folder: never the real database, sessions or activity log."""
import pytest

from app import config
from app.fetching import fetcher
from app.security import sessions


@pytest.fixture(autouse=True)
def _isolated_data(tmp_path_factory, monkeypatch):
    data = tmp_path_factory.mktemp("data")
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", data / "test.db")
    monkeypatch.setattr(config, "SESSIONS_DIR", data / "sessions")
    fetcher._settings_cache.clear()
    sessions._mem.clear()
    yield
