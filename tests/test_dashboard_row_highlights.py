from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import FundingSnapshot, Market
from app.storage import SQLiteRepository


def test_dashboard_applies_tiered_row_highlights(tmp_path) -> None:
    settings = Settings(
        database_path=tmp_path / "highlights.sqlite3",
        aster_history_backfill_enabled=False,
        bitget_history_backfill_enabled=False,
        gate_history_backfill_enabled=False,
        mexc_history_backfill_enabled=False,
    )
    repository = SQLiteRepository(settings.database_path)
    repository.initialize()
    observed_at = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)

    symbols = [
        ("PURP", 100.0, 102.0, 600_000),
        ("GREEN", 100.0, 100.5, 1_500_000),
        ("BLUE", 100.0, 100.4, 250_000),
        ("NEG", 100.0, 99.0, 5_000_000),
    ]
    markets: list[Market] = []
    snapshots: list[FundingSnapshot] = []
    for ticker, gate_price, var_price, min_volume in symbols:
        markets.extend(
            [
                Market(
                    exchange="gate",
                    ticker=ticker,
                    external_symbol=f"{ticker}_USDT",
                    base_asset=ticker,
                    quote_asset="USDT",
                    funding_interval_hours=8.0,
                ),
                Market(
                    exchange="variational",
                    ticker=ticker,
                    external_symbol=ticker,
                    base_asset=ticker,
                    quote_asset="USDC",
                    funding_interval_hours=4.0,
                ),
            ]
        )
        snapshots.extend(
            [
                FundingSnapshot(
                    exchange="gate",
                    ticker=ticker,
                    external_symbol=f"{ticker}_USDT",
                    funding_rate_raw=0.0001,
                    funding_rate_decimal=0.0001,
                    funding_rate_display_percent=0.01,
                    funding_interval_hours=8.0,
                    funding_rate_1h_equiv=0.0000125,
                    observed_at=observed_at,
                    mark_price=gate_price,
                    volume_24h=min_volume,
                    raw_payload={},
                ),
                FundingSnapshot(
                    exchange="variational",
                    ticker=ticker,
                    external_symbol=ticker,
                    funding_rate_raw=0.0005,
                    funding_rate_decimal=0.0005,
                    funding_rate_display_percent=0.05,
                    funding_interval_hours=4.0,
                    funding_rate_1h_equiv=0.000125,
                    observed_at=observed_at,
                    mark_price=var_price,
                    volume_24h=min_volume * 2,
                    raw_payload={},
                ),
            ]
        )

    repository.upsert_markets(markets)
    repository.insert_snapshots(snapshots)
    repository.close()

    app = create_app(settings, start_collectors=False)
    with TestClient(app) as client:
        dashboard = client.get("/")

    html = dashboard.text
    assert re.search(r'<tr class="row-highlight-purple">.*?<span class="symbol-chip">PURP</span>', html, re.S)
    assert re.search(r'<tr class="row-highlight-green">.*?<span class="symbol-chip">GREEN</span>', html, re.S)
    assert re.search(r'<tr class="row-highlight-blue">.*?<span class="symbol-chip">BLUE</span>', html, re.S)
    assert re.search(r"<tr>.*?<span class=\"symbol-chip\">NEG</span>", html, re.S)
