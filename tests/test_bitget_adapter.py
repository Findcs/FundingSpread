from __future__ import annotations

import asyncio

import httpx
import pytest

from app.connectors.bitget import BitgetAdapter


def test_bitget_adapter_maps_catalog_current_and_history(settings) -> None:
    settings.bitget_history_page_size = 100

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v2/mix/market/contracts"):
            return httpx.Response(
                200,
                json={
                    "code": "00000",
                    "msg": "success",
                    "data": [
                        {
                            "symbol": "BTCUSDT",
                            "baseCoin": "BTC",
                            "quoteCoin": "USDT",
                            "symbolType": "perpetual",
                            "symbolStatus": "normal",
                            "fundInterval": "8",
                        },
                        {
                            "symbol": "TONCOINUSDT",
                            "baseCoin": "TONCOIN",
                            "quoteCoin": "USDT",
                            "symbolType": "perpetual",
                            "symbolStatus": "listed",
                            "fundInterval": "4",
                        },
                    ],
                },
            )
        if request.url.path.endswith("/api/v2/mix/market/tickers"):
            return httpx.Response(
                200,
                json={
                    "code": "00000",
                    "msg": "success",
                    "data": [
                        {
                            "symbol": "BTCUSDT",
                            "ts": "1893456000000",
                            "fundingRate": "0.0008",
                            "markPrice": "63250.5",
                            "usdtVolume": "123456789.5",
                            "holdingAmount": "987654.2",
                        },
                        {
                            "symbol": "TONCOINUSDT",
                            "ts": "1893456000000",
                            "fundingRate": "0.0004",
                            "markPrice": "6.12",
                            "usdtVolume": "7654321",
                            "holdingAmount": "222222",
                        },
                    ],
                },
            )
        if request.url.path.endswith("/api/v2/mix/market/current-fund-rate"):
            return httpx.Response(
                200,
                json={
                    "code": "00000",
                    "msg": "success",
                    "data": [
                        {
                            "symbol": "BTCUSDT",
                            "fundingRate": "0.0008",
                            "fundingRateInterval": "8",
                            "nextUpdate": "1893459600000",
                        },
                        {
                            "symbol": "TONCOINUSDT",
                            "fundingRate": "0.0004",
                            "fundingRateInterval": "4",
                            "nextUpdate": "1893457800000",
                        },
                    ],
                },
            )
        if request.url.path.endswith("/api/v2/mix/market/history-fund-rate"):
            if request.url.params["symbol"] == "BTCUSDT":
                return httpx.Response(
                    200,
                    json={
                        "code": "00000",
                        "msg": "success",
                        "data": [
                            {"symbol": "BTCUSDT", "fundingRate": "0.0007", "fundingTime": "1893456000000"},
                            {"symbol": "BTCUSDT", "fundingRate": "0.0005", "fundingTime": "1893427200000"},
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "code": "00000",
                    "msg": "success",
                    "data": [
                        {"symbol": "TONCOINUSDT", "fundingRate": "0.0003", "fundingTime": "1893456000000"},
                    ],
                },
            )

        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = BitgetAdapter(client, settings)
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
