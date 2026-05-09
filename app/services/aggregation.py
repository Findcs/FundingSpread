from __future__ import annotations

from collections import defaultdict

from app.models import FundingSnapshot, SpreadExchangeValue, SpreadRow


def build_spread_rows(
    snapshots: list[FundingSnapshot],
    ticker_query: str = "",
    only_multi_exchange: bool = True,
) -> list[SpreadRow]:
    grouped: dict[str, list[FundingSnapshot]] = defaultdict(list)
    normalized_query = ticker_query.strip().upper()

    for snapshot in snapshots:
        if normalized_query and normalized_query not in snapshot.ticker:
            continue
        grouped[snapshot.ticker].append(snapshot)

    rows: list[SpreadRow] = []
    for ticker, grouped_snapshots in grouped.items():
        if only_multi_exchange and len(grouped_snapshots) < 2:
            continue

        ordered = sorted(grouped_snapshots, key=lambda item: item.funding_rate_1h_equiv)
        min_item = ordered[0]
        max_item = ordered[-1]
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
                exchanges_count=len(grouped_snapshots),
                min_exchange=min_item.exchange,
                min_rate_1h_percent=min_item.funding_rate_1h_equiv * 100.0,
                max_exchange=max_item.exchange,
                max_rate_1h_percent=max_item.funding_rate_1h_equiv * 100.0,
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
