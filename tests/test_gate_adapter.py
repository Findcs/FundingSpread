from __future__ import annotations

import asyncio

import httpx
import pytest

from app.connectors.gate import GateAdapter


def test_gate_adapter_maps_catalog_current_and_history(settings) -> None:
    settings.gate_history_limit = 50

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/futures/usdt/contracts"):
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "BTC_USDT",
                        "type": "direct",
                        "mark_type": "index",
                        "mark_price": "63250.5",
                        "funding_rate": "0.0008",
                        "funding_interval": 28800,
                        "funding_next_apply": 1893459600,
                        "trade_size": "123456789.5",
                        "position_size": "987654.2",
                        "in_delisting": False,
                    },
                    {
                        "name": "TONCOIN_USDT",
                        "type": "direct",
                        "mark_type": "index",
                        "mark_price": "6.12",
                        "funding_rate": "0.0004",
                        "funding_interval": 14400,
                        "funding_next_apply": 1893456000,
                        "trade_size": "7654321",
                        "position_size": "222222",
                        "in_delisting": False,
                    },
                    {
                        "name": "BTC_USD",
                        "type": "inverse",
                        "funding_rate": "0.001",
                        "funding_interval": 28800,
                        "in_delisting": False,
                    },
                ],
            )

        if request.url.path.endswith("/futures/usdt/funding_rate"):
            contract = request.url.params["contract"]
            if contract == "BTC_USDT":
                return httpx.Response(
                    200,
                    json=[
                        {"t": 1893456000, "r": "0.0007"},
                        {"t": 1893427200, "r": "0.0005"},
                    ],
                )
            return httpx.Response(
                200,
                json=[{"t": 1893456000, "r": "0.0003"}],
            )

        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = GateAdapter(client, settings)
            markets = await adapter.refresh_market_catalog()
            snapshots = await adapter.fetch_current_snapshots(markets)
            history = await adapter.fetch_recent_history(markets, lookback_hours=24 * 365 * 10)

        assert len(markets) == 2
        assert markets[0].ticker == "BTC"
        assert any(market.ticker == "TON" for market in markets)
        assert snapshots[0].funding_rate_decimal == pytest.approx(0.0008)
        assert snapshots[0].funding_rate_display_percent == pytest.approx(0.08)
        assert snapshots[0].funding_rate_1h_equiv == pytest.approx(0.0001)
        assert snapshots[0].mark_price == pytest.approx(63250.5)
        assert history[0].observation_source == "history_backfill"
        assert history[0].funding_rate_1h_equiv == pytest.approx(0.0000875)

    asyncio.run(run_test())
