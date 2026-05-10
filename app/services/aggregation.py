from __future__ import annotations

from collections import defaultdict

from app.models import FundingSnapshot, SpreadExchangeValue, SpreadRow
from app.utils import canonicalize_ticker


def build_spread_rows(
    snapshots: list[FundingSnapshot],
    ticker_query: str = "",
    only_multi_exchange: bool = True,
) -> list[SpreadRow]:
    grouped: dict[str, list[FundingSnapshot]] = defaultdict(list)
    normalized_query = ticker_query.strip().upper()

    for snapshot in snapshots:
        canonical_ticker = canonicalize_ticker(snapshot.ticker)
        if normalized_query and normalized_query not in canonical_ticker:
            continue
        grouped[canonical_ticker].append(snapshot)

    rows: list[SpreadRow] = []
    for ticker, grouped_snapshots in grouped.items():
        if only_multi_exchange and len(grouped_snapshots) < 2:
            continue

        ordered = sorted(grouped_snapshots, key=lambda item: item.funding_rate_1h_equiv)
        min_item = ordered[0]
        max_item = ordered[-1]
        min_price_item = min_item if min_item.mark_price is not None and min_item.mark_price > 0 else None
        max_price_item = max_item if max_item.mark_price is not None and max_item.mark_price > 0 else None
        price_spread_percent = None
        if min_price_item is not None and max_price_item is not None and min_price_item.mark_price:
            price_spread_percent = (
                (float(max_price_item.mark_price) - float(min_price_item.mark_price))
                / float(min_price_item.mark_price)
            ) * 100.0
        volume_candidates = [
            item for item in (min_item, max_item) if item.volume_24h is not None and item.volume_24h > 0
        ]
        min_volume_item = None
        if volume_candidates:
            min_volume_item = min(volume_candidates, key=lambda item: item.volume_24h or 0.0)
        exchange_values = [
            SpreadExchangeValue(
                exchange=item.exchange,
                funding_rate_raw=item.funding_rate_raw,
                funding_rate_decimal=item.funding_rate_decimal,
                funding_rate_display_percent=item.funding_rate_display_percent,
                funding_interval_hours=item.funding_interval_hours,
                funding_rate_1h_equiv=item.funding_rate_1h_equiv,
                funding_rate_1h_percent=item.funding_rate_1h_equiv * 100.0,
                observed_at=item.observed_at,
                mark_price=item.mark_price,
                volume_24h=item.volume_24h,
                open_interest=item.open_interest,
            )
            for item in sorted(
                grouped_snapshots,
                key=lambda snapshot: snapshot.exchange,
            )
        ]
        rows.append(
            SpreadRow(
                ticker=ticker,
                spread_1h_percent=(max_item.funding_rate_1h_equiv - min_item.funding_rate_1h_equiv)
                * 100.0,
                spread_abs_1h_percent=abs(
                    (max_item.funding_rate_1h_equiv - min_item.funding_rate_1h_equiv) * 100.0
                ),
                price_spread_percent=price_spread_percent,
                min_volume_24h=min_volume_item.volume_24h if min_volume_item else None,
                exchanges_count=len(grouped_snapshots),
                min_exchange=min_item.exchange,
                min_rate_1h_percent=min_item.funding_rate_1h_equiv * 100.0,
                max_exchange=max_item.exchange,
                max_rate_1h_percent=max_item.funding_rate_1h_equiv * 100.0,
                min_price_exchange=min_price_item.exchange if min_price_item else None,
                min_price=min_price_item.mark_price if min_price_item else None,
                max_price_exchange=max_price_item.exchange if max_price_item else None,
                max_price=max_price_item.mark_price if max_price_item else None,
                min_volume_exchange=min_volume_item.exchange if min_volume_item else None,
                updated_at=max(item.observed_at for item in grouped_snapshots),
                rates_by_exchange={value.exchange: value for value in exchange_values},
                exchange_values=exchange_values,
            )
        )

    rows.sort(key=lambda row: (row.spread_abs_1h_percent, row.ticker), reverse=True)
    return rows


def collect_exchange_columns(rows: list[SpreadRow]) -> list[str]:
    exchanges = {exchange for row in rows for exchange in row.rates_by_exchange}
    return sorted(exchanges)
