from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


VARIATIONAL_EXCHANGE = "variational"
MEXC_EXCHANGE = "mexc"
EXTENDED_EXCHANGE = "extended"
GATE_EXCHANGE = "gate"
BITGET_EXCHANGE = "bitget"
ASTER_EXCHANGE = "aster"


@dataclass(slots=True)
class Market:
    exchange: str
    ticker: str
    external_symbol: str
    base_asset: str
    quote_asset: str | None = None
    is_active: bool = True
    funding_interval_hours: float = 8.0
    metadata: dict[str, Any] = field(default_factory=dict)
    last_catalog_at: datetime | None = None


@dataclass(slots=True)
class FundingSnapshot:
    exchange: str
    ticker: str
    external_symbol: str
    funding_rate_raw: float
    funding_rate_decimal: float
    funding_rate_display_percent: float
    funding_interval_hours: float
    funding_rate_1h_equiv: float
    observed_at: datetime
    source_exchange_timestamp: datetime | None = None
    normalization_mode: str = "identity"
    observation_source: str = "live_poll"
    mark_price: float | None = None
    volume_24h: float | None = None
    open_interest: float | None = None
    next_settlement_at: datetime | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SpreadExchangeValue:
    exchange: str
    funding_rate_raw: float
    funding_rate_decimal: float
    funding_rate_display_percent: float
    funding_interval_hours: float
    funding_rate_1h_equiv: float
    funding_rate_1h_percent: float
    observed_at: datetime
    mark_price: float | None = None
    volume_24h: float | None = None
    open_interest: float | None = None


@dataclass(slots=True)
class SpreadRow:
    ticker: str
    spread_1h_percent: float
    spread_abs_1h_percent: float
    price_spread_percent: float | None
    min_volume_24h: float | None
    exchanges_count: int
    min_exchange: str
    min_rate_1h_percent: float
    max_exchange: str
    max_rate_1h_percent: float
    min_price_exchange: str | None
    min_price: float | None
    max_price_exchange: str | None
    max_price: float | None
    min_volume_exchange: str | None
    updated_at: datetime
    rates_by_exchange: dict[str, SpreadExchangeValue] = field(default_factory=dict)
    exchange_values: list[SpreadExchangeValue] = field(default_factory=list)


@dataclass(slots=True)
class CollectorRun:
    exchange: str
    task_name: str
    started_at: datetime
    finished_at: datetime
    status: str
    item_count: int
    error_message: str | None = None
