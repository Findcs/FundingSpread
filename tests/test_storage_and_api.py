from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import CollectorRun, FundingSnapshot, Market
from app.storage import SQLiteRepository


def test_repository_migration_and_api_endpoints(tmp_path) -> None:
    settings = Settings(
        database_path=tmp_path / "app.sqlite3",
        mexc_history_backfill_enabled=False,
    )
    repository = SQLiteRepository(settings.database_path)
    repository.initialize()
    observed_at = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    repository.upsert_markets(
        [
            Market(
                exchange="variational",
                ticker="RAVE",
                external_symbol="RAVE",
                base_asset="RAVE",
                quote_asset="USDC",
                funding_interval_hours=1.0,
            ),
            Market(
                exchange="mexc",
                ticker="RAVE",
                external_symbol="RAVE_USDT",
                base_asset="RAVE",
                quote_asset="USDT",
                funding_interval_hours=4.0,
                metadata={"collect_cycle_hours": 4.0},
            ),
        ]
    )
    repository.insert_snapshots(
        [
            FundingSnapshot(
                exchange="variational",
                ticker="RAVE",
                external_symbol="RAVE",
                funding_rate_raw=6.836009,
                funding_rate_decimal=0.0006836009,
                funding_rate_display_percent=0.06836009,
                funding_interval_hours=1.0,
                funding_rate_1h_equiv=0.0006836009,
                observed_at=observed_at,
                normalization_mode="variational_bps",
                raw_payload={
                    "ticker": "RAVE",
                    "funding_rate": "6.836009",
                    "funding_interval_s": 3600,
                    "quotes": {"updated_at": observed_at.isoformat()},
                },
            ),
            FundingSnapshot(
                exchange="mexc",
                ticker="RAVE",
                external_symbol="RAVE_USDT",
                funding_rate_raw=0.000462,
                funding_rate_decimal=0.000462,
                funding_rate_display_percent=0.0462,
                funding_interval_hours=8.0,
                funding_rate_1h_equiv=0.00005775,
                observed_at=observed_at,
                raw_payload={"timestamp": int(observed_at.timestamp() * 1000)},
            ),
        ]
    )
    repository.insert_collector_run(
        CollectorRun(
            exchange="variational",
            task_name="snapshot_collect",
            started_at=observed_at,
            finished_at=observed_at,
            status="success",
            item_count=2,
        )
    )
    repository.close()

    app = create_app(settings, start_collectors=False)
    with TestClient(app) as client:
        spreads = client.get("/api/spreads").json()
        health = client.get("/health").json()
        dashboard = client.get("/")
        history = client.get("/api/tickers/RAVE/history").json()

    assert spreads["rows"][0]["ticker"] == "RAVE"
    assert spreads["rows"][0]["spread_1h_percent"] == pytest.approx(0.05681009)
    assert spreads["rows"][0]["funding_by_exchange"]["mexc"]["funding_rate_percent"] == 0.0462
    assert spreads["rows"][0]["funding_by_exchange"]["mexc"]["funding_interval_hours"] == 4.0
    assert spreads["rows"][0]["funding_by_exchange"]["mexc"]["funding_rate_1h_percent"] == pytest.approx(0.01155)
    assert health["status"] == "ok"
    assert "Spread 1H" in dashboard.text
    assert history[0]["funding_rate_1h_percent"] > 0
