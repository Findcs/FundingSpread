from __future__ import annotations

from datetime import timedelta

import httpx

from app.config import Settings
from app.connectors.base import ExchangeAdapter
from app.models import FundingSnapshot, Market, VARIATIONAL_EXCHANGE
from app.utils import (
    funding_decimal_to_percent,
    normalize_variational_rate,
    parse_datetime,
    to_1h_equivalent,
    utcnow,
)


class VariationalAdapter(ExchangeAdapter):
    exchange = VARIATIONAL_EXCHANGE
    base_url = "https://omni-client-api.prod.ap-northeast-1.variational.io"

    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

    async def refresh_market_catalog(self) -> list[Market]:
        payload = await self._fetch_stats()
        observed_at = utcnow()
        markets: list[Market] = []
        for listing in payload.get("listings", []):
            funding_interval_hours = float(listing.get("funding_interval_s", 28800)) / 3600.0
            ticker = str(listing["ticker"]).upper()
            markets.append(
                Market(
                    exchange=self.exchange,
                    ticker=ticker,
                    external_symbol=ticker,
                    base_asset=ticker,
                    quote_asset="USDC",
                    funding_interval_hours=funding_interval_hours,
                    metadata={
                        "name": listing.get("name"),
                        "base_spread_bps": listing.get("base_spread_bps"),
                    },
                    last_catalog_at=observed_at,
                )
            )
        return markets

    async def fetch_current_snapshots(self, active_markets: list[Market]) -> list[FundingSnapshot]:
        del active_markets
        payload = await self._fetch_stats()
        fallback_observed_at = utcnow()
        snapshots: list[FundingSnapshot] = []
        for listing in payload.get("listings", []):
            ticker = str(listing["ticker"]).upper()
            funding_interval_hours = float(listing.get("funding_interval_s", 28800)) / 3600.0
            observed_at = parse_datetime(listing.get("quotes", {}).get("updated_at")) or fallback_observed_at
            funding_rate_raw = float(listing["funding_rate"])
            funding_rate_decimal = normalize_variational_rate(
                funding_rate_raw,
                self.settings.variational_normalization_mode,
            )
            open_interest = None
            if listing.get("open_interest"):
                long_oi = float(listing["open_interest"].get("long_open_interest", 0.0))
                short_oi = float(listing["open_interest"].get("short_open_interest", 0.0))
                open_interest = long_oi + short_oi

            snapshots.append(
                FundingSnapshot(
                    exchange=self.exchange,
                    ticker=ticker,
                    external_symbol=ticker,
                    funding_rate_raw=funding_rate_raw,
                    funding_rate_decimal=funding_rate_decimal,
                    funding_rate_display_percent=funding_decimal_to_percent(
                        funding_rate_decimal
                    ),
                    funding_interval_hours=funding_interval_hours,
                    funding_rate_1h_equiv=to_1h_equivalent(
                        funding_rate_decimal, funding_interval_hours
                    ),
                    observed_at=observed_at,
                    source_exchange_timestamp=observed_at,
                    normalization_mode=(
                        f"variational_{self.settings.variational_normalization_mode}"
                    ),
                    mark_price=float(listing["mark_price"]),
                    volume_24h=float(listing["volume_24h"]),
                    open_interest=open_interest,
                    next_settlement_at=observed_at + timedelta(hours=funding_interval_hours),
                    raw_payload=listing,
                )
            )
        return snapshots

    async def _fetch_stats(self) -> dict:
        response = await self.client.get(f"{self.base_url}/metadata/stats")
        response.raise_for_status()
        return response.json()
