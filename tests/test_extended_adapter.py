from __future__ import annotations

import asyncio

import httpx
import pytest

from app.connectors.extended import ExtendedAdapter


def test_extended_adapter_maps_catalog_current_and_history() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v1/info/markets"):
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "data": [
                        {
                            "market": "TON-USD",
                            "type": "PERPETUAL",
                            "isActive": True,
                            "marketStats": {
                                "fundingRate": "0.00032",
                                "markPrice": "6.45",
                                "openInterest": "123456.7",
                                "volume24h": "456789.1",
                                "timestamp": 1710000000000,
                                "nextFundingRate": 1710003600000,
                            },
                        },
                        {
                            "market": "BTC-USD",
                            "type": "SPOT",
                            "isActive": True,
                        },
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "data": [
                    {"timestamp": 1710000000000, "fundingRate": "0.00012"},
                    {"timestamp": 1709996400000, "fundingRate": "0.00010"},
                ],
            },
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = ExtendedAdapter(client)
            markets = await adapter.refresh_market_catalog()
            snapshots = await adapter.fetch_current_snapshots(markets)
            history = await adapter.fetch_recent_history(markets, lookback_hours=24)

        assert len(markets) == 1
        assert markets[0].ticker == "TON"
        assert markets[0].external_symbol == "TON-USD"
        assert snapshots[0].exchange == "extended"
        assert snapshots[0].funding_rate_decimal == pytest.approx(0.00032)
        assert snapshots[0].funding_rate_display_percent == pytest.approx(0.032)
        assert snapshots[0].funding_rate_1h_equiv == pytest.approx(0.00032)
        assert history[0].observation_source == "history_backfill"
        assert history[0].ticker == "TON"

    asyncio.run(run_test())


def test_extended_adapter_falls_back_to_market_stats_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v1/info/markets"):
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "data": [
                        {
                            "market": "RAVE-USD",
                            "type": "PERPETUAL",
                            "isActive": True,
                        }
                    ],
                },
            )
        if request.url.path.endswith("/api/v1/info/markets/RAVE-USD/stats"):
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "data": {
                        "fundingRate": "0.00047",
                        "markPrice": "0.7954",
                        "dailyVolume": "1200345.4",
                        "openInterest": "456789.0",
                        "nextFundingRate": 1710003600000,
                    },
                },
            )
        return httpx.Response(404, json={"status": "ERROR"})

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = ExtendedAdapter(client)
            markets = await adapter.refresh_market_catalog()
            snapshots = await adapter.fetch_current_snapshots(markets)

        assert len(markets) == 1
        assert len(snapshots) == 1
        assert snapshots[0].external_symbol == "RAVE-USD"
        assert snapshots[0].funding_rate_decimal == pytest.approx(0.00047)
        assert snapshots[0].raw_payload["source"] == "market_stats_endpoint"

    asyncio.run(run_test())
