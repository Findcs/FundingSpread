from __future__ import annotations

from datetime import UTC, datetime
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import CollectorRun, FundingSnapshot, Market
from app.storage import SQLiteRepository


def test_repository_migration_and_api_endpoints(tmp_path) -> None:
    settings = Settings(
        database_path=tmp_path / "app.sqlite3",
        aster_history_backfill_enabled=False,
        bitget_history_backfill_enabled=False,
        gate_history_backfill_enabled=False,
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
            Market(
                exchange="aster",
                ticker="RAVE",
                external_symbol="RAVEUSDT",
                base_asset="RAVE",
                quote_asset="USDT",
                funding_interval_hours=8.0,
            ),
            Market(
                exchange="extended",
                ticker="RAVE",
                external_symbol="RAVE-USD",
                base_asset="RAVE",
                quote_asset="USD",
                funding_interval_hours=1.0,
            ),
            Market(
                exchange="bitget",
                ticker="RAVE",
                external_symbol="RAVEUSDT",
                base_asset="RAVE",
                quote_asset="USDT",
                funding_interval_hours=8.0,
            ),
            Market(
                exchange="gate",
                ticker="RAVE",
                external_symbol="RAVE_USDT",
                base_asset="RAVE",
                quote_asset="USDT",
                funding_interval_hours=8.0,
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
                mark_price=100.4,
                volume_24h=12_000_000,
                raw_payload={
                    "ticker": "RAVE",
                    "funding_rate": "6.836009",
                    "funding_interval_s": 3600,
                    "quotes": {"updated_at": observed_at.isoformat()},
                },
            ),
            FundingSnapshot(
                exchange="aster",
                ticker="RAVE",
                external_symbol="RAVEUSDT",
                funding_rate_raw=0.00036,
                funding_rate_decimal=0.00036,
                funding_rate_display_percent=0.036,
                funding_interval_hours=8.0,
                funding_rate_1h_equiv=0.000045,
                observed_at=observed_at,
                mark_price=1.2345,
                volume_24h=3_000_000,
                raw_payload={"symbol": "RAVEUSDT"},
            ),
            FundingSnapshot(
                exchange="bitget",
                ticker="RAVE",
                external_symbol="RAVEUSDT",
                funding_rate_raw=0.0004,
                funding_rate_decimal=0.0004,
                funding_rate_display_percent=0.04,
                funding_interval_hours=8.0,
                funding_rate_1h_equiv=0.00005,
                observed_at=observed_at,
                mark_price=12345.67,
                volume_24h=5_000_000,
                raw_payload={"symbol": "RAVEUSDT"},
            ),
            FundingSnapshot(
                exchange="gate",
                ticker="RAVE",
                external_symbol="RAVE_USDT",
                funding_rate_raw=0.000288,
                funding_rate_decimal=0.000288,
                funding_rate_display_percent=0.0288,
                funding_interval_hours=8.0,
                funding_rate_1h_equiv=0.000036,
                observed_at=observed_at,
                mark_price=100.0,
                volume_24h=12_000,
                raw_payload={"name": "RAVE_USDT"},
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
                mark_price=0.00012345,
                volume_24h=18_000_000,
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
        history = client.get("/api/tickers/RAVE/history?hours=8760").json()

    assert spreads["rows"][0]["ticker"] == "RAVE"
    assert spreads["rows"][0]["spread_1h_percent"] == pytest.approx(0.06476009)
    assert spreads["rows"][0]["price_spread_percent"] == pytest.approx(0.4)
    assert spreads["rows"][0]["min_volume_24h"] == pytest.approx(12_000)
    assert spreads["exchanges"] == ["variational", "aster", "extended", "bitget", "gate", "mexc"]
    assert spreads["rows"][0]["funding_by_exchange"]["aster"]["funding_rate_percent"] == pytest.approx(0.036)
    assert spreads["rows"][0]["funding_by_exchange"]["bitget"]["funding_rate_percent"] == 0.04
    assert spreads["rows"][0]["funding_by_exchange"]["gate"]["funding_rate_percent"] == 0.0288
    assert spreads["rows"][0]["funding_by_exchange"]["mexc"]["funding_rate_percent"] == 0.0462
    assert spreads["rows"][0]["funding_by_exchange"]["mexc"]["funding_interval_hours"] == 4.0
    assert spreads["rows"][0]["funding_by_exchange"]["mexc"]["funding_rate_1h_percent"] == pytest.approx(0.01155)
    assert health["status"] == "ok"
    assert "Aster" in dashboard.text
    assert "Extended" in dashboard.text
    assert "Bitget" in dashboard.text
    assert "Best Rate" in dashboard.text
    assert "Gate" in dashboard.text
    assert "Price Spread" in dashboard.text
    assert "Min Volume" in dashboard.text
    assert 'data-sticky-header-wrap' in dashboard.text
    assert 'data-sticky-header-scroll' in dashboard.text
    assert "Long Gate vs Short Variational" in dashboard.text
    assert "12K" in dashboard.text
    assert "price 1.2345 | 8.0h" in dashboard.text
    assert "price 12,345.67 | 8.0h" in dashboard.text
    assert "price 0.000123 | 4.0h" in dashboard.text
    assert "raw " not in dashboard.text
    assert history[0]["funding_rate_1h_percent"] > 0


def test_repository_prunes_snapshots_and_collector_runs_to_retention_limits(tmp_path) -> None:
    settings = Settings(
        database_path=tmp_path / "retention.sqlite3",
        snapshot_retention_per_exchange_ticker=2,
        collector_run_retention_per_task=2,
    )
    repository = SQLiteRepository(settings.database_path)
    repository.initialize()

    repository.upsert_markets(
        [
            Market(
                exchange="gate",
                ticker="RAVE",
                external_symbol="RAVE_USDT",
                base_asset="RAVE",
                quote_asset="USDT",
                funding_interval_hours=8.0,
            )
        ]
    )
    base_time = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    for minute in range(4):
        repository.insert_snapshots(
            [
                FundingSnapshot(
                    exchange="gate",
                    ticker="RAVE",
                    external_symbol="RAVE_USDT",
                    funding_rate_raw=0.0001 + minute,
                    funding_rate_decimal=0.0001 + minute,
                    funding_rate_display_percent=(0.0001 + minute) * 100.0,
                    funding_interval_hours=8.0,
                    funding_rate_1h_equiv=(0.0001 + minute) / 8.0,
                    observed_at=base_time.replace(minute=minute),
                    raw_payload={"ignored": True},
                )
            ],
            keep_limit_per_exchange_ticker=2,
        )
        repository.insert_collector_run(
            CollectorRun(
                exchange="gate",
                task_name="snapshot_collect",
                started_at=base_time.replace(minute=minute),
                finished_at=base_time.replace(minute=minute),
                status="success",
                item_count=1,
            ),
            keep_limit_per_exchange_task=2,
        )

    latest = repository.list_latest_snapshots()
    history = repository.list_snapshot_history("RAVE", hours=24 * 365)
    collector_runs = repository.latest_collector_runs()

    assert repository.count_snapshots("gate") == 2
    assert len(history) == 2
    assert latest[0].observed_at.minute == 3
    assert collector_runs[0]["item_count"] == 1
    repository.close()


def test_repository_compact_storage_prunes_existing_rows(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "compact.sqlite3")
    repository = SQLiteRepository(settings.database_path)
    repository.initialize()
    observed_at = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)

    repository.upsert_markets(
        [
            Market(
                exchange="mexc",
                ticker="BTC",
                external_symbol="BTC_USDT",
                base_asset="BTC",
                quote_asset="USDT",
                funding_interval_hours=8.0,
            )
        ]
    )
    with repository._cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO funding_snapshots (
                exchange, ticker, external_symbol, funding_rate_raw, funding_rate_decimal,
                funding_rate_display_percent, funding_interval_hours, funding_rate_1h_equiv,
                funding_rate_8h_equiv, normalization_mode, observation_source,
                source_exchange_timestamp, mark_price, volume_24h, open_interest,
                observed_at, next_settlement_at, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "mexc",
                    "BTC",
                    "BTC_USDT",
                    0.0001 + idx,
                    0.0001 + idx,
                    (0.0001 + idx) * 100.0,
                    8.0,
                    (0.0001 + idx) / 8.0,
                    0.0001 + idx,
                    "identity",
                    "live_poll",
                    observed_at.isoformat(),
                    None,
                    None,
                    None,
                    observed_at.replace(minute=idx).isoformat(),
                    None,
                    "{}",
                )
                for idx in range(5)
            ],
        )
        cursor.executemany(
            """
            INSERT INTO collector_runs (
                exchange, task_name, started_at, finished_at, status, item_count, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "mexc",
                    "snapshot_collect",
                    observed_at.replace(minute=idx).isoformat(),
                    observed_at.replace(minute=idx).isoformat(),
                    "success",
                    idx,
                    None,
                )
                for idx in range(5)
            ],
        )

    result = repository.compact_storage(
        snapshot_retention_per_exchange_ticker=2,
        collector_run_retention_per_task=2,
    )

    assert result["snapshots_remaining"] == 2
    assert result["collector_runs_remaining"] == 2
    assert repository.count_snapshots("mexc") == 2
    repository.close()


def test_repository_inserts_snapshots_into_legacy_schema_with_required_8h_field(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE funding_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange TEXT NOT NULL,
            ticker TEXT NOT NULL,
            external_symbol TEXT NOT NULL,
            funding_rate_raw REAL NOT NULL,
            funding_interval_hours REAL NOT NULL,
            funding_rate_8h_equiv REAL NOT NULL,
            mark_price REAL,
            volume_24h REAL,
            open_interest REAL,
            observed_at TEXT NOT NULL,
            next_settlement_at TEXT,
            raw_payload_json TEXT NOT NULL
        );

        CREATE TABLE markets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange TEXT NOT NULL,
            ticker TEXT NOT NULL,
            external_symbol TEXT NOT NULL,
            base_asset TEXT NOT NULL,
            quote_asset TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            funding_interval_hours REAL NOT NULL,
            metadata_json TEXT NOT NULL,
            last_catalog_at TEXT,
            UNIQUE(exchange, external_symbol)
        );

        CREATE TABLE collector_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange TEXT NOT NULL,
            task_name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            status TEXT NOT NULL,
            item_count INTEGER NOT NULL,
            error_message TEXT
        );
        """
    )
    connection.commit()
    connection.close()

    repository = SQLiteRepository(database_path)
    repository.initialize()
    inserted = repository.insert_snapshots(
        [
            FundingSnapshot(
                exchange="extended",
                ticker="BTC",
                external_symbol="BTC-USD",
                funding_rate_raw=0.0001,
                funding_rate_decimal=0.0001,
                funding_rate_display_percent=0.01,
                funding_interval_hours=1.0,
                funding_rate_1h_equiv=0.0001,
                observed_at=datetime(2026, 5, 9, 12, 0, tzinfo=UTC),
                raw_payload={"market": "BTC-USD"},
            )
        ]
    )

    assert inserted == 1
    assert repository.count_snapshots("extended") == 1
    repository.close()
