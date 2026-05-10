from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from app.connectors.base import ExchangeAdapter
from app.models import EXTENDED_EXCHANGE, FundingSnapshot, Market
from app.utils import canonicalize_ticker, funding_decimal_to_percent, to_1h_equivalent, utcnow


class ExtendedAdapter(ExchangeAdapter):
    exchange = EXTENDED_EXCHANGE
    base_url = "https://api.starknet.extended.exchange"

    def __init__(self, client: httpx.AsyncClient, stats_concurrency: int = 16):
        self.client = client
        self.stats_concurrency = max(1, int(stats_concurrency))

    async def refresh_market_catalog(self) -> list[Market]:
        payload = await self._fetch_markets()
        observed_at = utcnow()
        markets: list[Market] = []
        for item in payload.get("data", []):
            if str(item.get("type", "")).upper() != "PERPETUAL":
                continue
            if item.get("isActive") is False:
                continue

            external_symbol = str(item.get("market") or item.get("name") or "").upper()
            if not external_symbol:
                continue
            ticker = canonicalize_ticker(self._extract_base_ticker(item, external_symbol))
            markets.append(
                Market(
                    exchange=self.exchange,
                    ticker=ticker,
                    external_symbol=external_symbol,
                    base_asset=ticker,
                    quote_asset=self._extract_quote_asset(item, external_symbol),
                    funding_interval_hours=1.0,
                    metadata={
                        "market_type": item.get("type"),
                        "hourly_funding_rate_cap": self._to_float(
                            item.get("tradingConfig", {}).get("hourlyFundingRateCap")
                        ),
                    },
                    last_catalog_at=observed_at,
                )
            )
        return markets

    async def fetch_current_snapshots(self, active_markets: list[Market]) -> list[FundingSnapshot]:
        payload = await self._fetch_markets()
        markets_by_symbol = {market.external_symbol: market for market in active_markets}
        fallback_observed_at = utcnow()
        snapshots: list[FundingSnapshot] = []
        collected_symbols: set[str] = set()
        for item in payload.get("data", []):
            if str(item.get("type", "")).upper() != "PERPETUAL":
                continue
            external_symbol = str(item.get("market") or item.get("name") or "").upper()
            market = markets_by_symbol.get(external_symbol)
            if market is None:
                continue

            snapshot = self._snapshot_from_market_payload(
                market=market,
                item=item,
                fallback_observed_at=fallback_observed_at,
            )
            if snapshot is not None:
                snapshots.append(snapshot)
                collected_symbols.add(market.external_symbol)

        missing_markets = [
            market
            for market in active_markets
            if market.external_symbol not in collected_symbols
        ]
        if missing_markets:
            snapshots.extend(
                await self._fetch_missing_market_stats(
                    missing_markets,
                    fallback_observed_at,
                )
            )
        return snapshots

    async def fetch_recent_history(
        self,
        active_markets: list[Market],
        lookback_hours: int,
    ) -> list[FundingSnapshot]:
        if not active_markets:
            return []

        end_time_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        start_time_ms = end_time_ms - lookback_hours * 3600 * 1000
        snapshots: list[FundingSnapshot] = []

        for market in active_markets:
            response = await self.client.get(
                f"{self.base_url}/api/v1/info/{market.external_symbol}/funding",
                params={"startTime": start_time_ms, "endTime": end_time_ms},
            )
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("data", []):
                observed_at = self._parse_extended_timestamp(
                    item.get("timestamp") or item.get("time") or item.get("ts")
                )
                if observed_at is None:
                    continue
                funding_rate_decimal = self._to_float(
                    item.get("fundingRate") or item.get("rate") or item.get("f") or 0.0
                )
                snapshots.append(
                    FundingSnapshot(
                        exchange=self.exchange,
                        ticker=market.ticker,
                        external_symbol=market.external_symbol,
                        funding_rate_raw=funding_rate_decimal,
                        funding_rate_decimal=funding_rate_decimal,
                        funding_rate_display_percent=funding_decimal_to_percent(funding_rate_decimal),
                        funding_interval_hours=1.0,
                        funding_rate_1h_equiv=to_1h_equivalent(funding_rate_decimal, 1.0),
                        observed_at=observed_at,
                        source_exchange_timestamp=observed_at,
                        normalization_mode="identity",
                        observation_source="history_backfill",
                        raw_payload=item,
                    )
                )

        return snapshots

    async def _fetch_markets(self) -> dict:
        response = await self.client.get(f"{self.base_url}/api/v1/info/markets")
        response.raise_for_status()
        return response.json()

    async def _fetch_missing_market_stats(
        self,
        markets: list[Market],
        fallback_observed_at: datetime,
    ) -> list[FundingSnapshot]:
        semaphore = asyncio.Semaphore(self.stats_concurrency)

        async def fetch_one(market: Market) -> FundingSnapshot | None:
            async with semaphore:
                response = await self.client.get(
                    f"{self.base_url}/api/v1/info/markets/{market.external_symbol}/stats"
                )
                response.raise_for_status()
                payload = response.json()
            return self._snapshot_from_stats_payload(
                market=market,
                payload=payload,
                fallback_observed_at=fallback_observed_at,
            )

        snapshots = await asyncio.gather(*(fetch_one(market) for market in markets))
        return [snapshot for snapshot in snapshots if snapshot is not None]

    def _snapshot_from_market_payload(
        self,
        market: Market,
        item: dict,
        fallback_observed_at: datetime,
    ) -> FundingSnapshot | None:
        stats = item.get("marketStats", {})
        funding_rate_decimal = self._to_float(stats.get("fundingRate"))
        if funding_rate_decimal is None:
            return None

        observed_at = self._parse_extended_timestamp(
            stats.get("timestamp")
            or stats.get("ts")
            or stats.get("updatedTime")
            or stats.get("nextFundingRate")
        ) or fallback_observed_at

        next_settlement_at = None
        next_funding_ts = self._parse_extended_timestamp(stats.get("nextFundingRate"))
        if next_funding_ts is not None:
            next_settlement_at = next_funding_ts
        elif observed_at is not None:
            next_settlement_at = observed_at + timedelta(hours=1)

        return FundingSnapshot(
            exchange=self.exchange,
            ticker=market.ticker,
            external_symbol=market.external_symbol,
            funding_rate_raw=funding_rate_decimal,
            funding_rate_decimal=funding_rate_decimal,
            funding_rate_display_percent=funding_decimal_to_percent(funding_rate_decimal),
            funding_interval_hours=1.0,
            funding_rate_1h_equiv=to_1h_equivalent(funding_rate_decimal, 1.0),
            observed_at=observed_at,
            source_exchange_timestamp=observed_at,
            normalization_mode="identity",
            mark_price=self._to_float(stats.get("markPrice")),
            volume_24h=self._to_float(
                stats.get("dailyVolume") or stats.get("dailyVolumeQuote") or stats.get("dailyVolumeBase")
            ),
            open_interest=self._to_float(
                stats.get("openInterest") or stats.get("openInterestBase")
            ),
            next_settlement_at=next_settlement_at,
            raw_payload=item,
        )

    def _snapshot_from_stats_payload(
        self,
        market: Market,
        payload: dict,
        fallback_observed_at: datetime,
    ) -> FundingSnapshot | None:
        stats = payload.get("data", {})
        funding_rate_decimal = self._to_float(stats.get("fundingRate"))
        if funding_rate_decimal is None:
            return None

        observed_at = self._parse_extended_timestamp(
            stats.get("timestamp")
            or stats.get("ts")
            or stats.get("updatedTime")
        ) or fallback_observed_at

        next_settlement_at = None
        next_funding_ts = self._parse_extended_timestamp(stats.get("nextFundingRate"))
        if next_funding_ts is not None:
            next_settlement_at = next_funding_ts
        elif observed_at is not None:
            next_settlement_at = observed_at + timedelta(hours=1)

        return FundingSnapshot(
            exchange=self.exchange,
            ticker=market.ticker,
            external_symbol=market.external_symbol,
            funding_rate_raw=funding_rate_decimal,
            funding_rate_decimal=funding_rate_decimal,
            funding_rate_display_percent=funding_decimal_to_percent(funding_rate_decimal),
            funding_interval_hours=1.0,
            funding_rate_1h_equiv=to_1h_equivalent(funding_rate_decimal, 1.0),
            observed_at=observed_at,
            source_exchange_timestamp=observed_at,
            normalization_mode="identity",
            mark_price=self._to_float(stats.get("markPrice")),
            volume_24h=self._to_float(
                stats.get("dailyVolume") or stats.get("dailyVolumeBase")
            ),
            open_interest=self._to_float(
                stats.get("openInterest") or stats.get("openInterestBase")
            ),
            next_settlement_at=next_settlement_at,
            raw_payload={
                "market": market.external_symbol,
                "marketStats": stats,
                "source": "market_stats_endpoint",
            },
        )

    @staticmethod
    def _extract_base_ticker(item: dict, external_symbol: str) -> str:
        for key in ("baseAsset", "baseCurrency", "base", "asset"):
            if item.get(key):
                return str(item[key]).upper()
        return external_symbol.split("-")[0].split("_")[0]

    @staticmethod
    def _extract_quote_asset(item: dict, external_symbol: str) -> str | None:
        for key in ("quoteAsset", "quoteCurrency", "quote", "settleAsset"):
            if item.get(key):
                return str(item[key]).upper()
        parts = external_symbol.replace("_", "-").split("-")
        if len(parts) > 1:
            return parts[-1].upper()
        return None

    @staticmethod
    def _parse_extended_timestamp(value: object) -> datetime | None:
        if value is None:
            return None
        try:
            raw_value = float(value)
        except (TypeError, ValueError):
            return None
        if raw_value > 10_000_000_000:
            raw_value = raw_value / 1000.0
        return datetime.fromtimestamp(raw_value, tz=UTC)

    @staticmethod
    def _to_float(value: object) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
