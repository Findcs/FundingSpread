from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models import FundingSnapshot
from app.services.aggregation import build_spread_rows


def test_build_spread_rows_uses_1h_equivalent_and_multi_exchange_filter() -> None:
    observed_at = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    snapshots = [
        FundingSnapshot(
            exchange="variational",
            ticker="BTC",
            external_symbol="BTC",
            funding_rate_raw=6.0,
            funding_rate_decimal=0.0006,
            funding_rate_display_percent=0.06,
            funding_interval_hours=1.0,
            funding_rate_1h_equiv=0.0006,
            observed_at=observed_at,
        ),
        FundingSnapshot(
            exchange="mexc",
            ticker="BTC",
            external_symbol="BTC_USDT",
            funding_rate_raw=0.0004,
            funding_rate_decimal=0.0004,
            funding_rate_display_percent=0.04,
            funding_interval_hours=8.0,
            funding_rate_1h_equiv=0.00005,
            observed_at=observed_at,
        ),
        FundingSnapshot(
            exchange="mexc",
            ticker="ETH",
            external_symbol="ETH_USDT",
            funding_rate_raw=-0.0002,
            funding_rate_decimal=-0.0002,
            funding_rate_display_percent=-0.02,
            funding_interval_hours=8.0,
            funding_rate_1h_equiv=-0.000025,
            observed_at=observed_at,
        ),
    ]

    rows = build_spread_rows(snapshots)

    assert len(rows) == 1
    assert rows[0].ticker == "BTC"
    assert rows[0].spread_1h_percent == pytest.approx(0.055)
    assert rows[0].min_exchange == "mexc"
    assert rows[0].max_exchange == "variational"
    assert rows[0].price_spread_percent is None
    assert rows[0].min_volume_24h is None


def test_build_spread_rows_groups_aliases_and_keeps_single_exchange_rows() -> None:
    observed_at = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    snapshots = [
        FundingSnapshot(
            exchange="variational",
            ticker="TON",
            external_symbol="TON",
            funding_rate_raw=1.2,
            funding_rate_decimal=0.00012,
            funding_rate_display_percent=0.012,
            funding_interval_hours=1.0,
            funding_rate_1h_equiv=0.00012,
            observed_at=observed_at,
            mark_price=5.0,
            volume_24h=12_000_000,
        ),
        FundingSnapshot(
            exchange="mexc",
            ticker="TONCOIN",
            external_symbol="TONCOIN_USDT",
            funding_rate_raw=0.0004,
            funding_rate_decimal=0.0004,
            funding_rate_display_percent=0.04,
            funding_interval_hours=4.0,
            funding_rate_1h_equiv=0.0001,
            observed_at=observed_at,
            mark_price=5.03,
            volume_24h=12_000,
        ),
        FundingSnapshot(
            exchange="mexc",
            ticker="DOGE",
            external_symbol="DOGE_USDT",
            funding_rate_raw=0.0002,
            funding_rate_decimal=0.0002,
            funding_rate_display_percent=0.02,
            funding_interval_hours=4.0,
            funding_rate_1h_equiv=0.00005,
            observed_at=observed_at,
        ),
    ]

    rows = build_spread_rows(snapshots, only_multi_exchange=False)

    assert [row.ticker for row in rows] == ["TON", "DOGE"]
    assert rows[0].exchanges_count == 2
    assert rows[1].exchanges_count == 1
    assert rows[0].price_spread_percent == pytest.approx(-0.5964214711729598)
    assert rows[0].min_price_exchange == "mexc"
    assert rows[0].max_price_exchange == "variational"
    assert rows[0].min_volume_24h == pytest.approx(12_000)
    assert rows[0].min_volume_exchange == "mexc"
