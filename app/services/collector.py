from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import asyncio
import logging

from app.connectors.base import ExchangeAdapter
from app.models import CollectorRun
from app.storage import SQLiteRepository
from app.utils import utcnow

logger = logging.getLogger(__name__)


class CollectionService:
    def __init__(
        self,
        repository: SQLiteRepository,
        adapters: dict[str, ExchangeAdapter],
        settings,
    ):
        self.repository = repository
        self.adapters = adapters
        self.settings = settings

    async def refresh_catalog(self, exchange: str) -> int:
        adapter = self.adapters[exchange]
        started_at = utcnow()
        try:
            markets = await adapter.refresh_market_catalog()
            self.repository.upsert_markets(markets)
            self.repository.insert_collector_run(
                CollectorRun(
                    exchange=exchange,
                    task_name="catalog_refresh",
                    started_at=started_at,
                    finished_at=utcnow(),
                    status="success",
                    item_count=len(markets),
                )
            )
            return len(markets)
        except Exception as exc:
            self.repository.insert_collector_run(
                CollectorRun(
                    exchange=exchange,
                    task_name="catalog_refresh",
                    started_at=started_at,
                    finished_at=utcnow(),
                    status="error",
                    item_count=0,
                    error_message=str(exc),
                )
            )
            logger.exception("Catalog refresh failed for %s", exchange)
            raise

    async def collect_snapshots(self, exchange: str) -> int:
        adapter = self.adapters[exchange]
        if exchange == "mexc" and self.settings.mexc_collect_overlapping_only:
            active_markets = self.repository.get_overlapping_markets(exchange=exchange)
        else:
            active_markets = self.repository.get_active_markets(exchange=exchange)
        started_at = utcnow()
        try:
            snapshots = await adapter.fetch_current_snapshots(active_markets)
            if active_markets:
                self.repository.upsert_markets(active_markets)
            self.repository.insert_snapshots(snapshots)
            self.repository.insert_collector_run(
                CollectorRun(
                    exchange=exchange,
                    task_name="snapshot_collect",
                    started_at=started_at,
                    finished_at=utcnow(),
                    status="success",
                    item_count=len(snapshots),
                )
            )
            return len(snapshots)
        except Exception as exc:
            self.repository.insert_collector_run(
                CollectorRun(
                    exchange=exchange,
                    task_name="snapshot_collect",
                    started_at=started_at,
                    finished_at=utcnow(),
                    status="error",
                    item_count=0,
                    error_message=str(exc),
                )
            )
            logger.exception("Snapshot collection failed for %s", exchange)
            raise

    async def backfill_recent_history(self, exchange: str, lookback_hours: int) -> int:
        adapter = self.adapters[exchange]
        if exchange == "mexc" and self.settings.mexc_collect_overlapping_only:
            active_markets = self.repository.get_overlapping_markets(exchange=exchange)
        else:
            active_markets = self.repository.get_active_markets(exchange=exchange)
        started_at = utcnow()
        try:
            snapshots = await adapter.fetch_recent_history(active_markets, lookback_hours)
            self.repository.insert_snapshots(snapshots)
            self.repository.insert_collector_run(
                CollectorRun(
                    exchange=exchange,
                    task_name="history_backfill",
                    started_at=started_at,
                    finished_at=utcnow(),
                    status="success",
                    item_count=len(snapshots),
                )
            )
            return len(snapshots)
        except Exception as exc:
            self.repository.insert_collector_run(
                CollectorRun(
                    exchange=exchange,
                    task_name="history_backfill",
                    started_at=started_at,
                    finished_at=utcnow(),
                    status="error",
                    item_count=0,
                    error_message=str(exc),
                )
            )
            logger.exception("History backfill failed for %s", exchange)
            raise


class PollingCoordinator:
    def __init__(self):
        self._tasks: list[asyncio.Task] = []

    async def start(
        self,
        jobs: list[tuple[str, int, Callable[[], Awaitable[object]], bool]],
    ) -> None:
        for name, interval_s, job, run_immediately in jobs:
            self._tasks.append(
                asyncio.create_task(
                    self._run_loop(name, interval_s, job, run_immediately=run_immediately)
                )
            )

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run_loop(
        self,
        name: str,
        interval_s: int,
        job: Callable[[], Awaitable[object]],
        run_immediately: bool = False,
    ) -> None:
        logger.info("Starting polling task %s", name)
        if not run_immediately:
            await asyncio.sleep(interval_s)
        while True:
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Polling task %s failed", name)
            await asyncio.sleep(interval_s)


def serialize_collector_status(rows: list[dict[str, str | int | None]]) -> list[dict[str, str | int | None]]:
    result = []
    for row in rows:
        normalized = dict(row)
        for key in ("started_at", "finished_at"):
            value = normalized.get(key)
            if isinstance(value, str):
                normalized[key] = datetime.fromisoformat(value).astimezone(UTC).isoformat()
        result.append(normalized)
    return result
