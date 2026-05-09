from __future__ import annotations

from app.models import FundingSnapshot, Market


class ExchangeAdapter:
    exchange: str

    async def refresh_market_catalog(self) -> list[Market]:
        raise NotImplementedError

    async def fetch_current_snapshots(self, active_markets: list[Market]) -> list[FundingSnapshot]:
        raise NotImplementedError

    async def fetch_recent_history(
        self,
        active_markets: list[Market],
        lookback_hours: int,
    ) -> list[FundingSnapshot]:
        return []
