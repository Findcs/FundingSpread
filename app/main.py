from __future__ import annotations

from contextlib import asynccontextmanager
import logging

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.connectors.mexc import MexcAdapter
from app.connectors.variational import VariationalAdapter
from app.services.collector import CollectionService, PollingCoordinator
from app.storage import SQLiteRepository
from app.web import register_routes

logging.basicConfig(level=logging.INFO)


def create_app(settings: Settings | None = None, start_collectors: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()
    repository = SQLiteRepository(settings.database_path)
    repository.initialize()
    repository.migrate_snapshot_metrics(settings.variational_normalization_mode)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        timeout = httpx.Timeout(settings.request_timeout_s)
        variational_client = httpx.AsyncClient(timeout=timeout)
        mexc_client = httpx.AsyncClient(timeout=timeout)
        adapters = {
            "variational": VariationalAdapter(variational_client, settings),
            "mexc": MexcAdapter(mexc_client, settings),
        }
        collector = CollectionService(repository, adapters, settings)
        coordinator = PollingCoordinator()

        app.state.settings = settings
        app.state.repository = repository
        app.state.collector = collector
        app.state.coordinator = coordinator

        if start_collectors:
            await collector.refresh_catalog("variational")
            await collector.refresh_catalog("mexc")
            await collector.collect_snapshots("variational")
            await collector.collect_snapshots("mexc")
            if settings.mexc_history_backfill_enabled:
                await collector.backfill_recent_history(
                    "mexc",
                    settings.mexc_history_lookback_hours,
                )
            await coordinator.start(
                [
                    (
                        "variational_snapshot_collect",
                        settings.variational_poll_interval_s,
                        lambda: collector.collect_snapshots("variational"),
                    ),
                    (
                        "mexc_catalog_refresh",
                        settings.mexc_catalog_refresh_interval_s,
                        lambda: collector.refresh_catalog("mexc"),
                    ),
                    (
                        "mexc_snapshot_collect",
                        settings.mexc_poll_interval_s,
                        lambda: collector.collect_snapshots("mexc"),
                    ),
                ]
            )

        try:
            yield
        finally:
            await coordinator.stop()
            await variational_client.aclose()
            await mexc_client.aclose()
            repository.close()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    register_routes(app, repository=repository, settings=settings)
    return app


app = create_app()
