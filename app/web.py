from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.services.aggregation import build_spread_rows, collect_exchange_columns
from app.services.collector import serialize_collector_status
from app.storage import SQLiteRepository

EXCHANGE_DISPLAY_META: dict[str, dict[str, str]] = {
    "aster": {"label": "Aster", "monogram": "AS"},
    "bitget": {"label": "Bitget", "monogram": "BG"},
    "extended": {"label": "Extended", "monogram": "EX"},
    "gate": {"label": "Gate", "monogram": "GT"},
    "mexc": {"label": "MEXC", "monogram": "MX"},
    "variational": {"label": "Variational", "monogram": "VR"},
}


def _resolve_exchange_columns(repository: SQLiteRepository, rows) -> list[str]:
    known_exchanges = repository.list_known_exchanges()
    row_exchanges = collect_exchange_columns(rows)
    order = ["variational", "aster", "extended", "bitget", "gate", "mexc"]
    merged = sorted(set(known_exchanges) | set(row_exchanges))
    return sorted(merged, key=lambda exchange: (order.index(exchange) if exchange in order else 999, exchange))


def _dashboard_summary(rows, exchange_columns: list[str]) -> dict[str, object]:
    max_spread = max((row.spread_abs_1h_percent for row in rows), default=0.0)
    latest_update = max((row.updated_at for row in rows), default=None)
    return {
        "assets_count": len(rows),
        "exchanges_count": len(exchange_columns),
        "max_spread_percent": max_spread,
        "latest_update": latest_update,
    }


def _resolve_exchange_meta(exchange_columns: list[str]) -> dict[str, dict[str, str]]:
    meta = dict(EXCHANGE_DISPLAY_META)
    for exchange in exchange_columns:
        if exchange in meta:
            continue
        meta[exchange] = {
            "label": exchange.replace("_", " ").title(),
            "monogram": exchange[:2].upper(),
        }
    return meta


def _format_mark_price(value: float | None) -> str:
    if value is None:
        return "-"
    absolute_value = abs(value)
    if absolute_value >= 1000:
        return f"{value:,.2f}"
    if absolute_value >= 1:
        return f"{value:,.4f}"
    if absolute_value >= 0.01:
        return f"{value:,.5f}"
    if absolute_value >= 0.0001:
        return f"{value:,.6f}"
    return f"{value:,.8f}"


def _format_compact_number(value: float | None) -> str:
    if value is None:
        return "-"
    absolute_value = abs(value)
    suffixes = (
        (1_000_000_000_000, "T"),
        (1_000_000_000, "B"),
        (1_000_000, "M"),
        (1_000, "K"),
    )
    for threshold, suffix in suffixes:
        if absolute_value >= threshold:
            compact = value / threshold
            formatted = f"{compact:.1f}".rstrip("0").rstrip(".")
            return f"{formatted}{suffix}"
    return f"{value:.0f}"


def register_routes(app: FastAPI, repository: SQLiteRepository, settings: Settings) -> None:
    templates = Jinja2Templates(directory="templates")
    templates.env.filters["mark_price"] = _format_mark_price
    templates.env.filters["compact_number"] = _format_compact_number

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        rows = build_spread_rows(
            repository.list_latest_snapshots(),
            only_multi_exchange=settings.show_only_multi_exchange_default,
        )
        exchange_columns = _resolve_exchange_columns(repository, rows)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "request": request,
                "rows": rows,
                "exchange_columns": exchange_columns,
                "exchange_meta": _resolve_exchange_meta(exchange_columns),
                "summary": _dashboard_summary(rows, exchange_columns),
                "collector_runs": serialize_collector_status(repository.latest_collector_runs()),
            },
        )

    @app.get("/api/spreads")
    async def api_spreads(q: str = "", only_multi_exchange: bool = False) -> JSONResponse:
        rows = build_spread_rows(
            repository.list_latest_snapshots(),
            ticker_query=q,
            only_multi_exchange=only_multi_exchange,
        )
        return JSONResponse(
            {
                "exchanges": _resolve_exchange_columns(repository, rows),
                "rows": [
                    {
                        "ticker": row.ticker,
                        "spread_1h_percent": row.spread_1h_percent,
                        "spread_abs_1h_percent": row.spread_abs_1h_percent,
                        "price_spread_percent": row.price_spread_percent,
                        "min_volume_24h": row.min_volume_24h,
                        "exchanges_count": row.exchanges_count,
                        "min_exchange": row.min_exchange,
                        "min_rate_1h_percent": row.min_rate_1h_percent,
                        "max_exchange": row.max_exchange,
                        "max_rate_1h_percent": row.max_rate_1h_percent,
                        "min_price_exchange": row.min_price_exchange,
                        "min_price": row.min_price,
                        "max_price_exchange": row.max_price_exchange,
                        "max_price": row.max_price,
                        "min_volume_exchange": row.min_volume_exchange,
                        "updated_at": row.updated_at.isoformat(),
                        "funding_by_exchange": {
                            value.exchange: {
                                "funding_rate_raw": value.funding_rate_raw,
                                "funding_rate_decimal": value.funding_rate_decimal,
                                "funding_rate_percent": value.funding_rate_display_percent,
                                "funding_rate_1h_percent": value.funding_rate_1h_percent,
                                "funding_interval_hours": value.funding_interval_hours,
                                "observed_at": value.observed_at.isoformat(),
                                "mark_price": value.mark_price,
                                "volume_24h": value.volume_24h,
                                "open_interest": value.open_interest,
                            }
                            for value in row.exchange_values
                        },
                    }
                    for row in rows
                ],
            }
        )

    @app.get("/api/tickers/{ticker}/history")
    async def api_ticker_history(ticker: str, hours: int = 24) -> JSONResponse:
        snapshots = repository.list_snapshot_history(ticker, hours=hours)
        return JSONResponse(
            [
                {
                    "exchange": snapshot.exchange,
                    "ticker": snapshot.ticker,
                    "external_symbol": snapshot.external_symbol,
                    "funding_rate_raw": snapshot.funding_rate_raw,
                    "funding_rate_decimal": snapshot.funding_rate_decimal,
                    "funding_rate_percent": snapshot.funding_rate_display_percent,
                    "funding_interval_hours": snapshot.funding_interval_hours,
                    "funding_rate_1h_percent": snapshot.funding_rate_1h_equiv * 100.0,
                    "mark_price": snapshot.mark_price,
                    "volume_24h": snapshot.volume_24h,
                    "open_interest": snapshot.open_interest,
                    "observed_at": snapshot.observed_at.isoformat(),
                    "source_exchange_timestamp": (
                        snapshot.source_exchange_timestamp.isoformat()
                        if snapshot.source_exchange_timestamp
                        else None
                    ),
                    "observation_source": snapshot.observation_source,
                    "next_settlement_at": (
                        snapshot.next_settlement_at.isoformat()
                        if snapshot.next_settlement_at
                        else None
                    ),
                }
                for snapshot in snapshots
            ]
        )

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "collectors": serialize_collector_status(repository.latest_collector_runs()),
            }
        )
