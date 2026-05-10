from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.config import Settings
from app.connectors.base import ExchangeAdapter
from app.models import BITGET_EXCHANGE, FundingSnapshot, Market
from app.utils import canonicalize_ticker, funding_decimal_to_percent, to_1h_equivalent, utcnow


class BitgetAdapter(ExchangeAdapter):
    exchange = BITGET_EXCHANGE
    base_url = "https://api.bitget.com"
    product_type = "USDT-FUTURES"

    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

    async def refresh_market_catalog(self) -> list[Market]:
        contracts = await self._fetch_contracts()
        observed_at = utcnow()
        markets: list[Market] = []
        for item in contracts:
            symbol = str(item.get("symbol") or "").upper()
            if not self._is_supported_contract(item, symbol):
                continue

            ticker = canonicalize_ticker(str(item.get("baseCoin") or symbol.removesuffix("USDT")))
            quote_asset = str(item.get("quoteCoin") or "USDT").upper()
            markets.append(
                Market(
                    exchange=self.exchange,
                    ticker=ticker,
                    external_symbol=symbol,
                    base_asset=ticker,
                    quote_asset=quote_asset,
                    funding_interval_hours=self._interval_hours(
                        item.get("fundInterval") or item.get("fundingRateInterval")
                    ),
                    metadata={
                        "symbol_status": item.get("symbolStatus"),
                        "symbol_type": item.get("symbolType"),
                        "min_trade_usdt": self._to_float(item.get("minTradeUSDT")),
                    },
                    last_catalog_at=observed_at,
                )
            )
        return markets

    async def fetch_current_snapshots(self, active_markets: list[Market]) -> list[FundingSnapshot]:
        if not active_markets:
            return []

        contracts = await self._fetch_contracts()
        tickers = await self._fetch_tickers()
        current_funding = await self._fetch_current_funding_rates()

        contract_map = {str(item.get("symbol") or "").upper(): item for item in contracts}
        ticker_map = {str(item.get("symbol") or "").upper(): item for item in tickers}
        funding_map = {str(item.get("symbol") or "").upper(): item for item in current_funding}
        fallback_observed_at = utcnow()

        snapshots: list[FundingSnapshot] = []
        for market in active_markets:
            contract_item = contract_map.get(market.external_symbol)
            ticker_item = ticker_map.get(market.external_symbol)
            funding_item = funding_map.get(market.external_symbol)
            snapshot = self._snapshot_from_payload(
                market=market,
                contract_item=contract_item,
                ticker_item=ticker_item,
                funding_item=funding_item,
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

        cutoff_ts_ms = (datetime.now(tz=UTC).timestamp() - float(lookback_hours) * 3600.0) * 1000.0
        snapshots: list[FundingSnapshot] = []

        for market in active_markets:
            page_no = 1
            while True:
                response = await self.client.get(
                    f"{self.base_url}/api/v2/mix/market/history-fund-rate",
                    params={
                        "symbol": market.external_symbol,
                        "productType": self.product_type,
                        "pageNo": page_no,
                        "pageSize": max(1, min(self.settings.bitget_history_page_size, 100)),
                    },
                )
                response.raise_for_status()
                payload = response.json()
                items = payload.get("data", [])
                if not isinstance(items, list) or not items:
                    break

                stop = False
                for item in items:
                    observed_at = self._parse_timestamp(
                        item.get("fundingTime") or item.get("fundingRateTimestamp")
                    )
                    if observed_at is None:
                        continue
                    if observed_at.timestamp() * 1000.0 < cutoff_ts_ms:
                        stop = True
                        break

                    funding_rate_decimal = self._to_float(item.get("fundingRate"))
                    if funding_rate_decimal is None:
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
                            funding_interval_hours=market.funding_interval_hours,
                            funding_rate_1h_equiv=to_1h_equivalent(
                                funding_rate_decimal,
                                market.funding_interval_hours,
                            ),
                            observed_at=observed_at,
                            source_exchange_timestamp=observed_at,
                            normalization_mode="identity",
                            observation_source="history_backfill",
                            raw_payload=item,
                        )
                    )

                if stop or len(items) < max(1, min(self.settings.bitget_history_page_size, 100)):
                    break
                page_no += 1

        return snapshots

    async def _fetch_contracts(self) -> list[dict]:
        response = await self.client.get(
            f"{self.base_url}/api/v2/mix/market/contracts",
            params={"productType": self.product_type},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", [])
        return data if isinstance(data, list) else []

    async def _fetch_tickers(self) -> list[dict]:
        response = await self.client.get(
            f"{self.base_url}/api/v2/mix/market/tickers",
            params={"productType": self.product_type},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", [])
        return data if isinstance(data, list) else []

    async def _fetch_current_funding_rates(self) -> list[dict]:
        response = await self.client.get(
            f"{self.base_url}/api/v2/mix/market/current-fund-rate",
            params={"productType": self.product_type},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", [])
        return data if isinstance(data, list) else []

    def _snapshot_from_payload(
        self,
        market: Market,
        contract_item: dict | None,
        ticker_item: dict | None,
        funding_item: dict | None,
        fallback_observed_at: datetime,
        observation_source: str,
    ) -> FundingSnapshot | None:
        funding_rate_decimal = self._to_float(
            (funding_item or {}).get("fundingRate")
            or (ticker_item or {}).get("fundingRate")
        )
        if funding_rate_decimal is None:
            return None

        interval_source = (
            (funding_item or {}).get("fundingRateInterval")
            or (contract_item or {}).get("fundInterval")
        )
        funding_interval_hours = self._interval_hours(interval_source) or market.funding_interval_hours
        observed_at = self._parse_timestamp((ticker_item or {}).get("ts")) or fallback_observed_at
        next_settlement_at = self._parse_timestamp((funding_item or {}).get("nextUpdate"))

        raw_payload = {
            "contract": contract_item,
            "ticker": ticker_item,
            "funding": funding_item,
        }

        return FundingSnapshot(
            exchange=self.exchange,
            ticker=market.ticker,
            external_symbol=market.external_symbol,
            funding_rate_raw=funding_rate_decimal,
            funding_rate_decimal=funding_rate_decimal,
            funding_rate_display_percent=funding_decimal_to_percent(funding_rate_decimal),
            funding_interval_hours=funding_interval_hours,
            funding_rate_1h_equiv=to_1h_equivalent(funding_rate_decimal, funding_interval_hours),
            observed_at=observed_at,
            source_exchange_timestamp=observed_at,
            normalization_mode="identity",
            observation_source=observation_source,
            mark_price=self._to_float((ticker_item or {}).get("markPrice")),
            volume_24h=self._to_float(
                (ticker_item or {}).get("usdtVolume") or (ticker_item or {}).get("quoteVolume")
            ),
            open_interest=self._to_float((ticker_item or {}).get("holdingAmount")),
            next_settlement_at=next_settlement_at,
            raw_payload=raw_payload,
        )

    @staticmethod
    def _is_supported_contract(item: dict, symbol: str) -> bool:
        if not symbol or not symbol.endswith("USDT"):
            return False
        if str(item.get("symbolType") or "").lower() not in {"perpetual", ""}:
            return False
        return str(item.get("symbolStatus") or "").lower() in {"listed", "normal", "restrictedapi"}

    @staticmethod
    def _interval_hours(value: object) -> float | None:
        interval = BitgetAdapter._to_float(value)
        if interval is None or interval <= 0:
            return None
        return interval

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        raw_value = BitgetAdapter._to_float(value)
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
