from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import Settings
from app.connectors.mexc import MexcAdapter


def test_mexc_adapter_maps_catalog_current_and_history(settings: Settings) -> None:
    settings.mexc_current_batch_size = 10
    settings.mexc_current_batch_pause_s = 0
    settings.mexc_history_page_size = 100

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/detail"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "symbol": "BTC_USDT",
                            "baseCoin": "BTC",
                            "quoteCoin": "USDT",
                            "settleCoin": "USDT",
                            "state": 0,
                            "displayNameEn": "BTC_USDT SWAP",
                            "apiAllowed": False,
                        }
                    ]
                },
            )
        if request.url.path.endswith("/ticker"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "symbol": "BTC_USDT",
                            "fairPrice": 6867.4,
                            "fundingRate": 0.0008,
                            "holdVol": 2284742,
                            "amount24": 164586129,
                            "timestamp": 1587442022003,
                        }
                    ]
                },
            )
        if request.url.path.endswith("/funding_rate/BTC_USDT"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "symbol": "BTC_USDT",
                        "fundingRate": 0.0008,
                        "collectCycle": 8,
                        "nextSettleTime": 1587470800000,
                        "timestamp": 1587442022003,
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "pageSize": 1,
                    "totalCount": 1,
                    "totalPage": 1,
                    "currentPage": 1,
                    "resultList": [
                        {
                            "symbol": "BTC_USDT",
                            "fundingRate": 0.000266,
                            "settleTime": 1893456000000,
                        }
                    ],
                }
            },
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = MexcAdapter(client, settings)
            markets = await adapter.refresh_market_catalog()
            snapshots = await adapter.fetch_current_snapshots(markets)
            history = await adapter.fetch_recent_history(markets, lookback_hours=24 * 365 * 10)

        assert len(markets) == 1
        assert markets[0].ticker == "BTC"
        assert markets[0].external_symbol == "BTC_USDT"
        assert snapshots[0].funding_rate_decimal == pytest.approx(0.0008)
        assert snapshots[0].funding_rate_display_percent == pytest.approx(0.08)
        assert snapshots[0].funding_rate_1h_equiv == pytest.approx(0.0001)
        assert history[0].funding_rate_1h_equiv == pytest.approx(0.00003325)
        assert history[0].observation_source == "history_backfill"

    asyncio.run(run_test())
