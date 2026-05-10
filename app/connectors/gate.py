from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.config import Settings
from app.connectors.base import ExchangeAdapter
from app.models import FundingSnapshot, GATE_EXCHANGE, Market
from app.utils import canonicalize_ticker, funding_decimal_to_percent, to_1h_equivalent, utcnow


class GateAdapter(ExchangeAdapter):
    exchange = GATE_EXCHANGE
    base_url = "https://api.gateio.ws/api/v4"
    settle_currency = "usdt"

    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

    async def refresh_market_catalog(self) -> list[Market]:
        contracts = await self._fetch_contracts()
        observed_at = utcnow()
        markets: list[Market] = []
        for item in contracts:
            contract_name = str(item.get("name") or "").upper()
            if not self._is_supported_contract(item, contract_name):
                continue

            base_asset, quote_asset = self._split_contract(contract_name)
            ticker = canonicalize_ticker(base_asset)
            markets.append(
                Market(
                    exchange=self.exchange,
                    ticker=ticker,
                    external_symbol=contract_name,
                    base_asset=ticker,
                    quote_asset=quote_asset,
                    funding_interval_hours=self._funding_interval_hours(item),
                    metadata={
                        "type": item.get("type"),
                        "in_delisting": bool(item.get("in_delisting")),
                        "last_price": self._to_float(item.get("last_price")),
                        "mark_type": item.get("mark_type"),
                    },
                    last_catalog_at=observed_at,
                )
            )
        return markets

    async def fetch_current_snapshots(self, active_markets: list[Market]) -> list[FundingSnapshot]:
        if not active_markets:
            return []

        markets_by_symbol = {market.external_symbol: market for market in active_markets}
        contracts = await self._fetch_contracts()
        observed_at = utcnow()
        snapshots: list[FundingSnapshot] = []

        for item in contracts:
            contract_name = str(item.get("name") or "").upper()
            market = markets_by_symbol.get(contract_name)
            if market is None:
                continue

            snapshot = self._snapshot_from_contract(
                market=market,
                item=item,
                observed_at=observed_at,
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

        cutoff_ts = datetime.now(tz=UTC).timestamp() - float(lookback_hours) * 3600.0
        snapshots: list[FundingSnapshot] = []

        for market in active_markets:
            response = await self.client.get(
                f"{self.base_url}/futures/{self.settle_currency}/funding_rate",
                params={
                    "contract": market.external_symbol,
                    "limit": max(1, self.settings.gate_history_limit),
                },
            )
            response.raise_for_status()
            for item in response.json():
                observed_at = self._parse_timestamp(item.get("t"))
                if observed_at is None or observed_at.timestamp() < cutoff_ts:
                    continue

                funding_rate_decimal = self._to_float(item.get("r"))
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

        return snapshots

    async def _fetch_contracts(self) -> list[dict]:
        response = await self.client.get(
            f"{self.base_url}/futures/{self.settle_currency}/contracts"
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return []
        return payload

    def _snapshot_from_contract(
        self,
        market: Market,
        item: dict,
        observed_at: datetime,
        observation_source: str,
    ) -> FundingSnapshot | None:
        funding_rate_decimal = self._to_float(item.get("funding_rate"))
        if funding_rate_decimal is None:
            return None

        funding_interval_hours = self._funding_interval_hours(item) or market.funding_interval_hours
        next_settlement_at = self._parse_timestamp(item.get("funding_next_apply"))

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
            mark_price=self._to_float(item.get("mark_price")),
            volume_24h=self._to_float(item.get("trade_size")),
            open_interest=self._to_float(item.get("position_size")),
            next_settlement_at=next_settlement_at,
            raw_payload=item,
        )

    @staticmethod
    def _is_supported_contract(item: dict, contract_name: str) -> bool:
        if not contract_name or "_" not in contract_name:
            return False
        if item.get("in_delisting") is True:
            return False
        _, quote_asset = GateAdapter._split_contract(contract_name)
        return quote_asset == "USDT"

    @staticmethod
    def _split_contract(contract_name: str) -> tuple[str, str]:
        base_asset, quote_asset = contract_name.split("_", 1)
        return base_asset.upper(), quote_asset.upper()

    @staticmethod
    def _funding_interval_hours(item: dict) -> float:
        funding_interval_s = GateAdapter._to_float(item.get("funding_interval")) or 28800.0
        return funding_interval_s / 3600.0

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        raw_value = GateAdapter._to_float(value)
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
