from __future__ import annotations

import asyncio

import httpx
import pytest

from app.connectors.aster import AsterAdapter


def test_aster_adapter_maps_catalog_current_and_history(settings) -> None:
    settings.aster_history_limit = 100

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/fapi/v1/exchangeInfo"):
            return httpx.Response(
                200,
                json={
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "pair": "BTCUSDT",
                            "contractType": "PERPETUAL",
                            "status": "TRADING",
                            "baseAsset": "BTC",
                            "quoteAsset": "USDT",
                        },
                        {
                            "symbol": "TONCOINUSDT",
                            "pair": "TONCOINUSDT",
                            "contractType": "PERPETUAL",
                            "status": "TRADING",
                            "baseAsset": "TONCOIN",
                            "quoteAsset": "USDT",
                        },
                    ]
                },
            )
        if request.url.path.endswith("/fapi/v1/premiumIndex"):
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "BTCUSDT",
                        "markPrice": "63250.5",
                        "lastFundingRate": "0.0008",
                        "nextFundingTime": 1893459600000,
                        "time": 1893457200000,
                    },
                    {
                        "symbol": "TONCOINUSDT",
                        "markPrice": "6.12",
                        "lastFundingRate": "0.0004",
                        "nextFundingTime": 1893445200000,
                        "time": 1893442800000,
                    },
                ],
            )
        if request.url.path.endswith("/fapi/v1/ticker/24hr"):
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "BTCUSDT",
                        "quoteVolume": "123456789.5",
                    },
                    {
                        "symbol": "TONCOINUSDT",
                        "quoteVolume": "7654321",
                    },
                ],
            )
        if request.url.path.endswith("/fapi/v1/fundingRate"):
            if request.url.params["symbol"] == "BTCUSDT" and request.url.params.get("startTime"):
                return httpx.Response(
                    200,
                    json=[
                        {"symbol": "BTCUSDT", "fundingRate": "0.0007", "fundingTime": 1893456000000},
                        {"symbol": "BTCUSDT", "fundingRate": "0.0005", "fundingTime": 1893427200000},
                    ],
                )
            if request.url.params["symbol"] == "TONCOINUSDT" and request.url.params.get("startTime"):
                return httpx.Response(
                    200,
                    json=[{"symbol": "TONCOINUSDT", "fundingRate": "0.0003", "fundingTime": 1893456000000}],
                )
            if request.url.params["symbol"] == "BTCUSDT":
                return httpx.Response(
                    200,
                    json=[
                        {"symbol": "BTCUSDT", "fundingRate": "0.0007", "fundingTime": 1893456000000},
                        {"symbol": "BTCUSDT", "fundingRate": "0.0005", "fundingTime": 1893427200000},
                    ],
                )
            return httpx.Response(
                200,
                json=[
                    {"symbol": "TONCOINUSDT", "fundingRate": "0.0004", "fundingTime": 1893441600000},
                    {"symbol": "TONCOINUSDT", "fundingRate": "0.0002", "fundingTime": 1893438000000},
                ],
            )

        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = AsterAdapter(client, settings)
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
        assert snapshots[1].funding_interval_hours == pytest.approx(1.0)
        assert snapshots[1].funding_rate_1h_equiv == pytest.approx(0.0004)
        assert history[0].observation_source == "history_backfill"
        assert history[0].funding_rate_1h_equiv == pytest.approx(0.0000875)

    asyncio.run(run_test())
