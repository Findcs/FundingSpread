from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.storage import SQLiteRepository


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(database_path=tmp_path / "test.sqlite3")


@pytest.fixture()
def repository(settings: Settings) -> SQLiteRepository:
    repo = SQLiteRepository(settings.database_path)
    repo.initialize()
    yield repo
    repo.close()
