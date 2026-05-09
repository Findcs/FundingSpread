from __future__ import annotations

from datetime import UTC, datetime
import asyncio

import httpx

from app.config import Settings
from app.connectors.base import ExchangeAdapter
from app.models import FundingSnapshot, Market, MEXC_EXCHANGE
from app.utils import canonicalize_ticker, funding_decimal_to_percent, to_1h_equivalent, utcnow


class MexcAdapter(ExchangeAdapter):
    exchange = MEXC_EXCHANGE
    base_url = "https://contract.mexc.com/api/v1/contract"

    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

    async def refresh_market_catalog(self) -> list[Market]:
        response = await self.client.get(f"{self.base_url}/detail")
        response.raise_for_status()
        payload = response.json()
        observed_at = utcnow()
        markets: list[Market] = []
        for item in payload.get("data", []):
            state = int(item.get("state", 1))
            if state != 0 or item.get("quoteCoin") != "USDT" or item.get("settleCoin") != "USDT":
                continue

            base_coin = canonicalize_ticker(str(item["baseCoin"]))
            symbol = str(item["symbol"]).upper()
            markets.append(
                Market(
                    exchange=self.exchange,
                    ticker=base_coin,
                    external_symbol=symbol,
                    base_asset=base_coin,
                    quote_asset="USDT",
                    funding_interval_hours=self.settings.mexc_default_funding_interval_hours,
                    metadata={
                        "display_name": item.get("displayNameEn") or item.get("displayName"),
                        "api_allowed": item.get("apiAllowed"),
                        "price_scale": item.get("priceScale"),
                    },
                    last_catalog_at=observed_at,
                )
            )
        return markets

    async def fetch_current_snapshots(self, active_markets: list[Market]) -> list[FundingSnapshot]:
        if not active_markets:
            return []

        markets_by_symbol = {market.external_symbol: market for market in active_markets}
        ticker_payload = await self._fetch_ticker_map()
        snapshots: list[FundingSnapshot] = []

        for start in range(0, len(active_markets), self.settings.mexc_current_batch_size):
            batch = active_markets[start : start + self.settings.mexc_current_batch_size]
            batch_results = await asyncio.gather(
                *(self._fetch_symbol_snapshot(market, ticker_payload.get(market.external_symbol)) for market in batch)
            )
            snapshots.extend(item for item in batch_results if item is not None)
            if start + self.settings.mexc_current_batch_size < len(active_markets):
                await asyncio.sleep(self.settings.mexc_current_batch_pause_s)

        return snapshots

    async def fetch_recent_history(
        self,
        active_markets: list[Market],
        lookback_hours: int,
    ) -> list[FundingSnapshot]:
        if not active_markets:
            return []

        cutoff_ts_ms = (
            datetime.now(tz=UTC).timestamp() - float(lookback_hours) * 3600.0
        ) * 1000.0
        snapshots: list[FundingSnapshot] = []

        for start in range(0, len(active_markets), self.settings.mexc_current_batch_size):
            batch = active_markets[start : start + self.settings.mexc_current_batch_size]
            batch_results = await asyncio.gather(
                *(self._fetch_symbol_history(market, cutoff_ts_ms) for market in batch)
            )
            for symbol_snapshots in batch_results:
                snapshots.extend(symbol_snapshots)
            if start + self.settings.mexc_current_batch_size < len(active_markets):
                await asyncio.sleep(self.settings.mexc_current_batch_pause_s)

        return snapshots

    async def _fetch_ticker_map(self) -> dict[str, dict]:
        response = await self.client.get(f"{self.base_url}/ticker")
        response.raise_for_status()
        payload = response.json()
        tickers = payload.get("data", [])
        if isinstance(tickers, dict):
            tickers = [tickers]
        return {str(item["symbol"]).upper(): item for item in tickers}

    async def _fetch_symbol_snapshot(
        self,
        market: Market,
        ticker_item: dict | None,
    ) -> FundingSnapshot | None:
        response = await self.client.get(f"{self.base_url}/funding_rate/{market.external_symbol}")
        response.raise_for_status()
        funding_item = response.json().get("data", {})
        if not funding_item:
            return None

        funding_interval_hours = float(
            funding_item.get("collectCycle") or market.funding_interval_hours
        )
        market.funding_interval_hours = funding_interval_hours
        market.metadata["collect_cycle_hours"] = funding_interval_hours

        observed_at = datetime.fromtimestamp(float(funding_item["timestamp"]) / 1000.0, tz=UTC)
        next_settlement_at = None
        if funding_item.get("nextSettleTime"):
            next_settlement_at = datetime.fromtimestamp(
                float(funding_item["nextSettleTime"]) / 1000.0,
                tz=UTC,
            )

        funding_rate_raw = float(funding_item["fundingRate"])
        mark_price = None
        volume_24h = None
        open_interest = None
        merged_payload: dict = {"funding": funding_item}
        if ticker_item:
            mark_price = float(ticker_item["fairPrice"])
            volume_24h = float(ticker_item["amount24"])
            open_interest = float(ticker_item["holdVol"])
            merged_payload["ticker"] = ticker_item

        return FundingSnapshot(
            exchange=self.exchange,
            ticker=market.ticker,
            external_symbol=market.external_symbol,
            funding_rate_raw=funding_rate_raw,
            funding_rate_decimal=funding_rate_raw,
            funding_rate_display_percent=funding_decimal_to_percent(funding_rate_raw),
            funding_interval_hours=funding_interval_hours,
            funding_rate_1h_equiv=to_1h_equivalent(funding_rate_raw, funding_interval_hours),
            observed_at=observed_at,
            source_exchange_timestamp=observed_at,
            normalization_mode="identity",
            mark_price=mark_price,
            volume_24h=volume_24h,
            open_interest=open_interest,
            next_settlement_at=next_settlement_at,
            raw_payload=merged_payload,
        )

    async def _fetch_symbol_history(
        self,
        market: Market,
        cutoff_ts_ms: float,
    ) -> list[FundingSnapshot]:
        snapshots: list[FundingSnapshot] = []
        page_num = 1
        while True:
            response = await self.client.get(
                f"{self.base_url}/funding_rate/history",
                params={
                    "symbol": market.external_symbol,
                    "page_num": page_num,
                    "page_size": self.settings.mexc_history_page_size,
                },
            )
            response.raise_for_status()
            data = response.json().get("data", {})
            result_list = data.get("resultList", [])
            if not result_list:
                break

            stop = False
            for item in result_list:
                settle_time = float(item["settleTime"])
                if settle_time < cutoff_ts_ms:
                    stop = True
                    break
                observed_at = datetime.fromtimestamp(settle_time / 1000.0, tz=UTC)
                funding_rate_raw = float(item["fundingRate"])
                snapshots.append(
                    FundingSnapshot(
                        exchange=self.exchange,
                        ticker=market.ticker,
                        external_symbol=market.external_symbol,
                        funding_rate_raw=funding_rate_raw,
                        funding_rate_decimal=funding_rate_raw,
                        funding_rate_display_percent=funding_decimal_to_percent(
                            funding_rate_raw
                        ),
                        funding_interval_hours=market.funding_interval_hours,
                        funding_rate_1h_equiv=to_1h_equivalent(
                            funding_rate_raw, market.funding_interval_hours
                        ),
                        observed_at=observed_at,
                        source_exchange_timestamp=observed_at,
                        normalization_mode="identity",
                        observation_source="history_backfill",
                        next_settlement_at=None,
                        raw_payload=item,
                    )
                )

            total_pages = int(data.get("totalPage") or page_num)
            if stop or page_num >= total_pages:
                break
            page_num += 1

        return snapshots
