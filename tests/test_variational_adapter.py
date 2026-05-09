from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import Settings
from app.connectors.variational import VariationalAdapter


def test_variational_adapter_maps_snapshots_using_bps_normalization() -> None:
    payload = {
        "listings": [
            {
                "ticker": "RAVE",
                "name": "RaveDAO",
                "mark_price": "0.789816338061974",
                "volume_24h": "295014.179145",
                "open_interest": {
                    "long_open_interest": "12890.079876611979761742000000",
                    "short_open_interest": "20767.939918324691077623000000",
                },
                "funding_rate": "6.836009",
                "funding_interval_s": 3600,
                "quotes": {"updated_at": "2026-05-09T16:46:06.833000+00:00"},
            }
        ]
    }

    async def run_test() -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = VariationalAdapter(client, Settings(variational_normalization_mode="bps"))
            snapshots = await adapter.fetch_current_snapshots([])

        assert len(snapshots) == 1
        snapshot = snapshots[0]
        assert snapshot.exchange == "variational"
        assert snapshot.ticker == "RAVE"
        assert snapshot.funding_rate_raw == pytest.approx(6.836009)
        assert snapshot.funding_rate_decimal == pytest.approx(0.0006836009)
        assert snapshot.funding_rate_display_percent == pytest.approx(0.06836009)
        assert snapshot.funding_rate_1h_equiv == pytest.approx(0.0006836009)
        assert snapshot.open_interest == pytest.approx(33658.01979493667)

    asyncio.run(run_test())
