from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.services.aggregation import build_spread_rows, collect_exchange_columns
from app.services.collector import serialize_collector_status
from app.storage import SQLiteRepository


def register_routes(app: FastAPI, repository: SQLiteRepository, settings: Settings) -> None:
    templates = Jinja2Templates(directory="templates")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        rows = build_spread_rows(
            repository.list_latest_snapshots(),
            only_multi_exchange=settings.show_only_multi_exchange_default,
        )
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "request": request,
                "rows": rows,
                "exchange_columns": collect_exchange_columns(rows),
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
                "exchanges": collect_exchange_columns(rows),
                "rows": [
                    {
                        "ticker": row.ticker,
                        "spread_1h_percent": row.spread_1h_percent,
                        "spread_abs_1h_percent": row.spread_abs_1h_percent,
                        "exchanges_count": row.exchanges_count,
                        "min_exchange": row.min_exchange,
                        "min_rate_1h_percent": row.min_rate_1h_percent,
                        "max_exchange": row.max_exchange,
                        "max_rate_1h_percent": row.max_rate_1h_percent,
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
