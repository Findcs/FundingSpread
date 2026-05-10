from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from app.config import Settings
from app.connectors.base import ExchangeAdapter
from app.models import ASTER_EXCHANGE, FundingSnapshot, Market
from app.utils import canonicalize_ticker, funding_decimal_to_percent, to_1h_equivalent, utcnow


class AsterAdapter(ExchangeAdapter):
    exchange = ASTER_EXCHANGE
    base_url = "https://fapi.asterdex.com"
    interval_probe_concurrency = 12

    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

    async def refresh_market_catalog(self) -> list[Market]:
        payload = await self._fetch_exchange_info()
        observed_at = utcnow()
        markets: list[Market] = []

        for item in payload.get("symbols", []):
            symbol = str(item.get("symbol") or "").upper()
            if not self._is_supported_symbol(item, symbol):
                continue

            base_asset = canonicalize_ticker(str(item.get("baseAsset") or symbol.removesuffix("USDT")))
            quote_asset = str(item.get("quoteAsset") or "USDT").upper()
            markets.append(
                Market(
                    exchange=self.exchange,
                    ticker=base_asset,
                    external_symbol=symbol,
                    base_asset=base_asset,
                    quote_asset=quote_asset,
                    funding_interval_hours=8.0,
                    metadata={
                        "contract_type": item.get("contractType"),
                        "status": item.get("status"),
                        "pair": item.get("pair"),
                    },
                    last_catalog_at=observed_at,
                )
            )

        return markets

    async def fetch_current_snapshots(self, active_markets: list[Market]) -> list[FundingSnapshot]:
        if not active_markets:
            return []

        premium_items = await self._fetch_premium_index()
        ticker_items = await self._fetch_24h_tickers()
        inferred_intervals = await self._infer_market_intervals(active_markets, premium_items)
        premium_map = {str(item.get("symbol") or "").upper(): item for item in premium_items}
        ticker_map = {str(item.get("symbol") or "").upper(): item for item in ticker_items}
        fallback_observed_at = utcnow()

        snapshots: list[FundingSnapshot] = []
        for market in active_markets:
            premium_item = premium_map.get(market.external_symbol)
            snapshot = self._snapshot_from_payload(
                market=market,
                premium_item=premium_item,
                ticker_item=ticker_map.get(market.external_symbol),
                inferred_interval_hours=inferred_intervals.get(market.external_symbol),
                fallback_observed_at=fallback_observed_at,
                observation_source="live_poll",
            )
            if snapshot is not None:
                market.funding_interval_hours = snapshot.funding_interval_hours
                snapshots.append(snapshot)

        return snapshots

    async def fetch_recent_history(
        self,
        active_markets: list[Market],
        lookback_hours: int,
    ) -> list[FundingSnapshot]:
        if not active_markets:
            return []

        end_time_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        start_time_ms = end_time_ms - int(lookback_hours * 3600 * 1000)
        inferred_intervals = await self._infer_market_intervals(active_markets, premium_items=None)
        snapshots: list[FundingSnapshot] = []

        for market in active_markets:
            funding_interval_hours = inferred_intervals.get(
                market.external_symbol,
                market.funding_interval_hours,
            )
            market.funding_interval_hours = funding_interval_hours
            response = await self.client.get(
                f"{self.base_url}/fapi/v1/fundingRate",
                params={
                    "symbol": market.external_symbol,
                    "startTime": start_time_ms,
                    "endTime": end_time_ms,
                    "limit": max(1, min(self.settings.aster_history_limit, 1000)),
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                continue

            for item in payload:
                observed_at = self._parse_timestamp(item.get("fundingTime"))
                funding_rate_decimal = self._to_float(item.get("fundingRate"))
                if observed_at is None or funding_rate_decimal is None:
                    continue

                snapshots.append(
                    FundingSnapshot(
                        exchange=self.exchange,
                        ticker=market.ticker,
                        external_symbol=market.external_symbol,
                        funding_rate_raw=funding_rate_decimal,
                        funding_rate_decimal=funding_rate_decimal,
                        funding_rate_display_percent=funding_decimal_to_percent(
                            funding_rate_decimal
                        ),
                        funding_interval_hours=funding_interval_hours,
                        funding_rate_1h_equiv=to_1h_equivalent(
                            funding_rate_decimal,
                            funding_interval_hours,
                        ),
                        observed_at=observed_at,
                        source_exchange_timestamp=observed_at,
                        normalization_mode="identity",
                        observation_source="history_backfill",
                        raw_payload=item,
                    )
                )

        return snapshots

    async def _fetch_exchange_info(self) -> dict:
        response = await self.client.get(f"{self.base_url}/fapi/v1/exchangeInfo")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def _fetch_premium_index(self) -> list[dict]:
        response = await self.client.get(f"{self.base_url}/fapi/v1/premiumIndex")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return [payload]
        return []

    async def _fetch_24h_tickers(self) -> list[dict]:
        response = await self.client.get(f"{self.base_url}/fapi/v1/ticker/24hr")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return [payload]
        return []

    def _snapshot_from_payload(
        self,
        market: Market,
        premium_item: dict | None,
        ticker_item: dict | None,
        inferred_interval_hours: float | None,
        fallback_observed_at: datetime,
        observation_source: str,
    ) -> FundingSnapshot | None:
        if premium_item is None:
            return None

        funding_rate_decimal = self._to_float(
            premium_item.get("lastFundingRate") or premium_item.get("fundingRate")
        )
        if funding_rate_decimal is None:
            return None

        observed_at = self._parse_timestamp(premium_item.get("time")) or fallback_observed_at
        next_funding_at = self._parse_timestamp(premium_item.get("nextFundingTime"))
        funding_interval_hours = inferred_interval_hours or market.funding_interval_hours

        return FundingSnapshot(
            exchange=self.exchange,
            ticker=market.ticker,
            external_symbol=market.external_symbol,
            funding_rate_raw=funding_rate_decimal,
            funding_rate_decimal=funding_rate_decimal,
            funding_rate_display_percent=funding_decimal_to_percent(funding_rate_decimal),
            funding_interval_hours=funding_interval_hours,
            funding_rate_1h_equiv=to_1h_equivalent(
                funding_rate_decimal,
                funding_interval_hours,
            ),
            observed_at=observed_at,
            source_exchange_timestamp=observed_at,
            normalization_mode="identity",
            observation_source=observation_source,
            mark_price=self._to_float(premium_item.get("markPrice")),
            volume_24h=self._to_float((ticker_item or {}).get("quoteVolume")),
            open_interest=None,
            next_settlement_at=next_funding_at,
            raw_payload={
                "premium": premium_item,
                "ticker": ticker_item,
            },
        )

    async def _infer_market_intervals(
        self,
        active_markets: list[Market],
        premium_items: list[dict] | None,
    ) -> dict[str, float]:
        premium_map = {
            str(item.get("symbol") or "").upper(): item
            for item in (premium_items or [])
        }
        semaphore = asyncio.Semaphore(self.interval_probe_concurrency)

        async def infer_one(market: Market) -> tuple[str, float]:
            async with semaphore:
                response = await self.client.get(
                    f"{self.base_url}/fapi/v1/fundingRate",
                    params={
                        "symbol": market.external_symbol,
                        "limit": 2,
                    },
                )
                response.raise_for_status()
                payload = response.json()

            inferred = self._interval_from_funding_history(
                payload if isinstance(payload, list) else [],
                premium_map.get(market.external_symbol),
                fallback_hours=market.funding_interval_hours,
            )
            return market.external_symbol, inferred

        pairs = await asyncio.gather(*(infer_one(market) for market in active_markets))
        return dict(pairs)

    def _interval_from_funding_history(
        self,
        items: list[dict],
        premium_item: dict | None,
        fallback_hours: float,
    ) -> float:
        ordered_times = [
            timestamp
            for timestamp in (
                self._parse_timestamp(item.get("fundingTime"))
                for item in items
            )
            if timestamp is not None
        ]
        ordered_times.sort()
        if len(ordered_times) >= 2:
            diff_hours = (ordered_times[-1] - ordered_times[-2]).total_seconds() / 3600.0
            if diff_hours > 0:
                return round(diff_hours, 6)

        if len(ordered_times) == 1 and premium_item is not None:
            next_funding_at = self._parse_timestamp(premium_item.get("nextFundingTime"))
            if next_funding_at is not None:
                diff_hours = (next_funding_at - ordered_times[-1]).total_seconds() / 3600.0
                if diff_hours > 0:
                    return round(diff_hours, 6)

        return fallback_hours

    @staticmethod
    def _is_supported_symbol(item: dict, symbol: str) -> bool:
        if not symbol or not symbol.endswith("USDT"):
            return False
        if str(item.get("contractType") or "").upper() != "PERPETUAL":
            return False
        return str(item.get("status") or "").upper() == "TRADING"

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        raw_value = AsterAdapter._to_float(value)
        if raw_value is None:
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
