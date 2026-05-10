from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3

from app.models import CollectorRun, FundingSnapshot, Market
from app.utils import (
    canonicalize_ticker,
    funding_decimal_to_percent,
    normalize_variational_rate,
    parse_datetime,
    to_1h_equivalent,
    to_iso,
)


class SQLiteRepository:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row

    @contextmanager
    def _cursor(self):
        cursor = self._connection.cursor()
        try:
            yield cursor
            self._connection.commit()
        finally:
            cursor.close()

    def close(self) -> None:
        self._connection.close()

    def initialize(self) -> None:
        with self._cursor() as cursor:
            cursor.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS markets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    external_symbol TEXT NOT NULL,
                    base_asset TEXT NOT NULL,
                    quote_asset TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    funding_interval_hours REAL NOT NULL,
                    metadata_json TEXT NOT NULL,
                    last_catalog_at TEXT,
                    UNIQUE(exchange, external_symbol)
                );

                CREATE INDEX IF NOT EXISTS idx_markets_exchange_ticker
                    ON markets (exchange, ticker);

                CREATE TABLE IF NOT EXISTS funding_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    external_symbol TEXT NOT NULL,
                    funding_rate_raw REAL NOT NULL,
                    funding_rate_decimal REAL,
                    funding_rate_display_percent REAL,
                    funding_interval_hours REAL NOT NULL,
                    funding_rate_1h_equiv REAL,
                    funding_rate_8h_equiv REAL,
                    normalization_mode TEXT,
                    observation_source TEXT,
                    source_exchange_timestamp TEXT,
                    mark_price REAL,
                    volume_24h REAL,
                    open_interest REAL,
                    observed_at TEXT NOT NULL,
                    next_settlement_at TEXT,
                    raw_payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_funding_snapshots_lookup
                    ON funding_snapshots (ticker, exchange, observed_at DESC);

                CREATE TABLE IF NOT EXISTS collector_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    task_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    item_count INTEGER NOT NULL,
                    error_message TEXT
                );
                """
            )
        self._ensure_snapshot_columns()
        self._ensure_snapshot_unique_index()

    def _ensure_snapshot_columns(self) -> None:
        with self._cursor() as cursor:
            columns = {
                row["name"] for row in cursor.execute("PRAGMA table_info(funding_snapshots)").fetchall()
            }
            migrations = {
                "funding_rate_decimal": "ALTER TABLE funding_snapshots ADD COLUMN funding_rate_decimal REAL",
                "funding_rate_display_percent": "ALTER TABLE funding_snapshots ADD COLUMN funding_rate_display_percent REAL",
                "funding_rate_1h_equiv": "ALTER TABLE funding_snapshots ADD COLUMN funding_rate_1h_equiv REAL",
                "normalization_mode": "ALTER TABLE funding_snapshots ADD COLUMN normalization_mode TEXT",
                "observation_source": "ALTER TABLE funding_snapshots ADD COLUMN observation_source TEXT",
                "source_exchange_timestamp": "ALTER TABLE funding_snapshots ADD COLUMN source_exchange_timestamp TEXT",
            }
            for column, statement in migrations.items():
                if column not in columns:
                    cursor.execute(statement)

    def _deduplicate_funding_snapshots(self) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM funding_snapshots
                WHERE id NOT IN (
                    SELECT MAX(id)
                    FROM funding_snapshots
                    GROUP BY exchange, external_symbol, observed_at
                )
                """
            )

    def _ensure_snapshot_unique_index(self) -> None:
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_funding_snapshots_unique
                        ON funding_snapshots (exchange, external_symbol, observed_at)
                    """
                )
        except sqlite3.IntegrityError:
            self._deduplicate_funding_snapshots()
            with self._cursor() as cursor:
                cursor.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_funding_snapshots_unique
                        ON funding_snapshots (exchange, external_symbol, observed_at)
                    """
                )

    def _canonicalize_tickers(self) -> None:
        with self._cursor() as cursor:
            market_rows = cursor.execute(
                "SELECT id, ticker, base_asset FROM markets"
            ).fetchall()
            market_updates = []
            for row in market_rows:
                canonical_ticker = canonicalize_ticker(row["ticker"])
                canonical_base_asset = canonicalize_ticker(row["base_asset"])
                if canonical_ticker != row["ticker"] or canonical_base_asset != row["base_asset"]:
                    market_updates.append((canonical_ticker, canonical_base_asset, row["id"]))
            if market_updates:
                cursor.executemany(
                    "UPDATE markets SET ticker = ?, base_asset = ? WHERE id = ?",
                    market_updates,
                )

            snapshot_rows = cursor.execute(
                "SELECT id, ticker FROM funding_snapshots"
            ).fetchall()
            snapshot_updates = []
            for row in snapshot_rows:
                canonical_ticker = canonicalize_ticker(row["ticker"])
                if canonical_ticker != row["ticker"]:
                    snapshot_updates.append((canonical_ticker, row["id"]))
            if snapshot_updates:
                cursor.executemany(
                    "UPDATE funding_snapshots SET ticker = ? WHERE id = ?",
                    snapshot_updates,
                )

    def migrate_snapshot_metrics(self, variational_normalization_mode: str) -> None:
        with self._cursor() as cursor:
            rows = cursor.execute(
                """
                SELECT id, exchange, funding_rate_raw, funding_interval_hours,
                       funding_rate_decimal, funding_rate_1h_equiv, normalization_mode,
                       source_exchange_timestamp, observed_at, raw_payload_json
                FROM funding_snapshots
                """
            ).fetchall()

            updates = []
            for row in rows:
                payload = json.loads(row["raw_payload_json"])
                exchange = row["exchange"]
                interval_hours = float(row["funding_interval_hours"] or 8.0)
                raw_rate = float(row["funding_rate_raw"])
                source_exchange_timestamp = parse_datetime(row["source_exchange_timestamp"])
                observed_at = parse_datetime(row["observed_at"])

                if exchange == "variational":
                    if "funding_interval_s" in payload:
                        interval_hours = float(payload["funding_interval_s"]) / 3600.0
                    decimal_rate = normalize_variational_rate(
                        float(payload.get("funding_rate", raw_rate)),
                        variational_normalization_mode,
                    )
                    normalization_mode = f"variational_{variational_normalization_mode}"
                    if payload.get("quotes", {}).get("updated_at"):
                        source_exchange_timestamp = parse_datetime(payload["quotes"]["updated_at"])
                else:
                    if payload.get("collectCycle"):
                        interval_hours = float(payload["collectCycle"])
                    decimal_rate = raw_rate
                    normalization_mode = "identity"
                    if payload.get("timestamp"):
                        source_exchange_timestamp = parse_datetime(
                            _timestamp_ms_to_iso_string(payload["timestamp"])
                        )

                updates.append(
                    (
                        decimal_rate,
                        funding_decimal_to_percent(decimal_rate),
                        interval_hours,
                        to_1h_equivalent(decimal_rate, interval_hours),
                        normalization_mode,
                        to_iso(source_exchange_timestamp or observed_at),
                        row["id"],
                    )
                )

            cursor.executemany(
                """
                UPDATE funding_snapshots
                SET funding_rate_decimal = ?,
                    funding_rate_display_percent = ?,
                    funding_interval_hours = ?,
                    funding_rate_1h_equiv = ?,
                    normalization_mode = ?,
                    source_exchange_timestamp = ?
                WHERE id = ?
                """,
                updates,
            )

    def upsert_markets(self, markets: list[Market]) -> None:
        if not markets:
            return
        with self._cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO markets (
                    exchange, ticker, external_symbol, base_asset, quote_asset,
                    is_active, funding_interval_hours, metadata_json, last_catalog_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exchange, external_symbol)
                DO UPDATE SET
                    ticker = excluded.ticker,
                    base_asset = excluded.base_asset,
                    quote_asset = excluded.quote_asset,
                    is_active = excluded.is_active,
                    funding_interval_hours = excluded.funding_interval_hours,
                    metadata_json = excluded.metadata_json,
                    last_catalog_at = excluded.last_catalog_at
                """,
                [
                    (
                        market.exchange,
                        canonicalize_ticker(market.ticker),
                        market.external_symbol,
                        canonicalize_ticker(market.base_asset),
                        market.quote_asset,
                        int(market.is_active),
                        market.funding_interval_hours,
                        json.dumps(market.metadata, separators=(",", ":"), sort_keys=True),
                        to_iso(market.last_catalog_at),
                    )
                    for market in markets
                ],
            )

    def get_active_markets(self, exchange: str | None = None) -> list[Market]:
        query = """
            SELECT exchange, ticker, external_symbol, base_asset, quote_asset,
                   is_active, funding_interval_hours, metadata_json, last_catalog_at
            FROM markets
            WHERE is_active = 1
        """
        params: list[str] = []
        if exchange:
            query += " AND exchange = ?"
            params.append(exchange)
        query += " ORDER BY exchange, ticker, external_symbol"
        with self._cursor() as cursor:
            rows = cursor.execute(query, params).fetchall()
        return [self._market_from_row(row) for row in rows]

    def get_overlapping_markets(self, exchange: str) -> list[Market]:
        with self._cursor() as cursor:
            rows = cursor.execute(
                """
                SELECT m.exchange, m.ticker, m.external_symbol, m.base_asset, m.quote_asset,
                       m.is_active, m.funding_interval_hours, m.metadata_json, m.last_catalog_at
                FROM markets AS m
                WHERE m.exchange = ?
                  AND m.is_active = 1
                  AND EXISTS (
                      SELECT 1
                      FROM markets AS other
                      WHERE other.ticker = m.ticker
                        AND other.exchange != m.exchange
                        AND other.is_active = 1
                  )
                ORDER BY m.ticker, m.external_symbol
                """,
                (exchange,),
            ).fetchall()
        return [self._market_from_row(row) for row in rows]

    def count_snapshots(self, exchange: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM funding_snapshots"
        params: list[str] = []
        if exchange:
            query += " WHERE exchange = ?"
            params.append(exchange)
        with self._cursor() as cursor:
            return int(cursor.execute(query, params).fetchone()[0])

    def list_known_exchanges(self) -> list[str]:
        with self._cursor() as cursor:
            rows = cursor.execute(
                """
                SELECT exchange
                FROM (
                    SELECT exchange FROM markets
                    UNION
                    SELECT exchange FROM funding_snapshots
                )
                ORDER BY exchange
                """
            ).fetchall()
        return [str(row["exchange"]) for row in rows]

    def insert_snapshots(
        self,
        snapshots: list[FundingSnapshot],
        keep_limit_per_exchange_ticker: int = 2,
    ) -> int:
        if not snapshots:
            return 0
        touched_groups = sorted(
            {
                (snapshot.exchange, canonicalize_ticker(snapshot.ticker))
                for snapshot in snapshots
            }
        )
        with self._cursor() as cursor:
            before_changes = self._connection.total_changes
            cursor.executemany(
                """
                INSERT OR IGNORE INTO funding_snapshots (
                    exchange, ticker, external_symbol, funding_rate_raw,
                    funding_rate_decimal, funding_rate_display_percent,
                    funding_interval_hours, funding_rate_1h_equiv, funding_rate_8h_equiv,
                    normalization_mode, observation_source, source_exchange_timestamp,
                    mark_price, volume_24h, open_interest, observed_at,
                    next_settlement_at, raw_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot.exchange,
                        canonicalize_ticker(snapshot.ticker),
                        snapshot.external_symbol,
                        snapshot.funding_rate_raw,
                        snapshot.funding_rate_decimal,
                        snapshot.funding_rate_display_percent,
                        snapshot.funding_interval_hours,
                        snapshot.funding_rate_1h_equiv,
                        snapshot.funding_rate_1h_equiv * 8.0,
                        snapshot.normalization_mode,
                        snapshot.observation_source,
                        to_iso(snapshot.source_exchange_timestamp),
                        snapshot.mark_price,
                        snapshot.volume_24h,
                        snapshot.open_interest,
                        to_iso(snapshot.observed_at),
                        to_iso(snapshot.next_settlement_at),
                        "{}",
                    )
                    for snapshot in snapshots
                ],
            )
            inserted_count = self._connection.total_changes - before_changes
            self._prune_snapshot_groups(
                cursor=cursor,
                groups=touched_groups,
                keep_limit=max(1, int(keep_limit_per_exchange_ticker)),
            )
            return inserted_count

    def list_latest_snapshots(self) -> list[FundingSnapshot]:
        with self._cursor() as cursor:
            rows = cursor.execute(
                """
                WITH ranked AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY exchange, ticker
                               ORDER BY observed_at DESC, id DESC
                           ) AS rn
                    FROM funding_snapshots
                )
                SELECT ranked.*,
                       markets.metadata_json AS market_metadata_json,
                       markets.funding_interval_hours AS market_funding_interval_hours
                FROM ranked
                LEFT JOIN markets
                  ON markets.exchange = ranked.exchange
                 AND markets.external_symbol = ranked.external_symbol
                WHERE rn = 1
                ORDER BY ticker, exchange
                """
            ).fetchall()
        return [self._snapshot_from_row(row) for row in rows]

    def list_snapshot_history(self, ticker: str, hours: int = 24) -> list[FundingSnapshot]:
        with self._cursor() as cursor:
            cutoff = parse_datetime(to_iso_from_hours_ago(hours))
            rows = cursor.execute(
                """
                SELECT funding_snapshots.*,
                       markets.metadata_json AS market_metadata_json,
                       markets.funding_interval_hours AS market_funding_interval_hours
                FROM funding_snapshots
                LEFT JOIN markets
                  ON markets.exchange = funding_snapshots.exchange
                 AND markets.external_symbol = funding_snapshots.external_symbol
                WHERE funding_snapshots.ticker = ?
                  AND funding_snapshots.observed_at >= ?
                ORDER BY funding_snapshots.observed_at DESC, funding_snapshots.exchange
                """,
                (canonicalize_ticker(ticker), to_iso(cutoff)),
            ).fetchall()
        return [self._snapshot_from_row(row) for row in rows]

    def insert_collector_run(
        self,
        run: CollectorRun,
        keep_limit_per_exchange_task: int = 20,
    ) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO collector_runs (
                    exchange, task_name, started_at, finished_at,
                    status, item_count, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.exchange,
                    run.task_name,
                    to_iso(run.started_at),
                    to_iso(run.finished_at),
                    run.status,
                    run.item_count,
                    run.error_message,
                ),
            )
            self._prune_collector_run_groups(
                cursor=cursor,
                groups=[(run.exchange, run.task_name)],
                keep_limit=max(1, int(keep_limit_per_exchange_task)),
            )

    def latest_collector_runs(self) -> list[dict[str, str | int | None]]:
        with self._cursor() as cursor:
            rows = cursor.execute(
                """
                WITH ranked AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY exchange, task_name
                               ORDER BY finished_at DESC, id DESC
                           ) AS rn
                    FROM collector_runs
                )
                SELECT exchange, task_name, started_at, finished_at, status,
                       item_count, error_message
                FROM ranked
                WHERE rn = 1
                ORDER BY exchange, task_name
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def compact_storage(
        self,
        snapshot_retention_per_exchange_ticker: int,
        collector_run_retention_per_task: int,
    ) -> dict[str, int]:
        snapshot_keep_limit = max(1, int(snapshot_retention_per_exchange_ticker))
        collector_keep_limit = max(1, int(collector_run_retention_per_task))

        before_snapshot_count = self.count_snapshots()
        before_collector_count = self._count_collector_runs()

        with self._cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM funding_snapshots
                WHERE id IN (
                    SELECT id
                    FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY exchange, ticker
                                   ORDER BY observed_at DESC, id DESC
                               ) AS rn
                        FROM funding_snapshots
                    )
                    WHERE rn > ?
                )
                """,
                (snapshot_keep_limit,),
            )
            cursor.execute(
                """
                DELETE FROM collector_runs
                WHERE id IN (
                    SELECT id
                    FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY exchange, task_name
                                   ORDER BY finished_at DESC, id DESC
                               ) AS rn
                        FROM collector_runs
                    )
                    WHERE rn > ?
                )
                """,
                (collector_keep_limit,),
            )

        self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._connection.execute("VACUUM")

        after_snapshot_count = self.count_snapshots()
        after_collector_count = self._count_collector_runs()
        return {
            "snapshots_removed": before_snapshot_count - after_snapshot_count,
            "snapshots_remaining": after_snapshot_count,
            "collector_runs_removed": before_collector_count - after_collector_count,
            "collector_runs_remaining": after_collector_count,
        }

    def _count_collector_runs(self) -> int:
        with self._cursor() as cursor:
            return int(cursor.execute("SELECT COUNT(*) FROM collector_runs").fetchone()[0])

    @staticmethod
    def _prune_snapshot_groups(
        cursor: sqlite3.Cursor,
        groups: list[tuple[str, str]],
        keep_limit: int,
    ) -> None:
        if not groups:
            return
        values_sql = ", ".join(["(?, ?)"] * len(groups))
        params: list[str | int] = []
        for exchange, ticker in groups:
            params.extend((exchange, ticker))
        params.append(max(1, int(keep_limit)))
        cursor.execute(
            f"""
            WITH targets(exchange, ticker) AS (
                VALUES {values_sql}
            ),
            ranked AS (
                SELECT funding_snapshots.id,
                       ROW_NUMBER() OVER (
                           PARTITION BY funding_snapshots.exchange, funding_snapshots.ticker
                           ORDER BY funding_snapshots.observed_at DESC, funding_snapshots.id DESC
                       ) AS rn
                FROM funding_snapshots
                JOIN targets
                  ON targets.exchange = funding_snapshots.exchange
                 AND targets.ticker = funding_snapshots.ticker
            )
            DELETE FROM funding_snapshots
            WHERE id IN (
                SELECT id
                FROM ranked
                WHERE rn > ?
            )
            """,
            params,
        )

    @staticmethod
    def _prune_collector_run_groups(
        cursor: sqlite3.Cursor,
        groups: list[tuple[str, str]],
        keep_limit: int,
    ) -> None:
        if not groups:
            return
        values_sql = ", ".join(["(?, ?)"] * len(groups))
        params: list[str | int] = []
        for exchange, task_name in groups:
            params.extend((exchange, task_name))
        params.append(max(1, int(keep_limit)))
        cursor.execute(
            f"""
            WITH targets(exchange, task_name) AS (
                VALUES {values_sql}
            ),
            ranked AS (
                SELECT collector_runs.id,
                       ROW_NUMBER() OVER (
                           PARTITION BY collector_runs.exchange, collector_runs.task_name
                           ORDER BY collector_runs.finished_at DESC, collector_runs.id DESC
                       ) AS rn
                FROM collector_runs
                JOIN targets
                  ON targets.exchange = collector_runs.exchange
                 AND targets.task_name = collector_runs.task_name
            )
            DELETE FROM collector_runs
            WHERE id IN (
                SELECT id
                FROM ranked
                WHERE rn > ?
            )
            """,
            params,
        )

    @staticmethod
    def _market_from_row(row: sqlite3.Row) -> Market:
        return Market(
            exchange=row["exchange"],
            ticker=canonicalize_ticker(row["ticker"]),
            external_symbol=row["external_symbol"],
            base_asset=canonicalize_ticker(row["base_asset"]),
            quote_asset=row["quote_asset"],
            is_active=bool(row["is_active"]),
            funding_interval_hours=row["funding_interval_hours"],
            metadata=json.loads(row["metadata_json"]),
            last_catalog_at=parse_datetime(row["last_catalog_at"]),
        )

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row) -> FundingSnapshot:
        raw_payload = json.loads(row["raw_payload_json"])
        market_metadata = {}
        if "market_metadata_json" in row.keys() and row["market_metadata_json"]:
            market_metadata = json.loads(row["market_metadata_json"])
        funding_rate_decimal = row["funding_rate_decimal"]
        funding_rate_display_percent = row["funding_rate_display_percent"]
        funding_rate_1h_equiv = row["funding_rate_1h_equiv"]
        interval_hours = row["funding_interval_hours"]
        inferred_interval_hours = interval_hours

        if row["exchange"] == "mexc":
            inferred_interval_hours = _infer_mexc_interval_hours(
                raw_payload=raw_payload,
                market_metadata=market_metadata,
                stored_interval_hours=interval_hours,
                market_interval_hours=row["market_funding_interval_hours"]
                if "market_funding_interval_hours" in row.keys()
                else None,
            )

        if funding_rate_decimal is None:
            if row["exchange"] == "variational":
                funding_rate_decimal = normalize_variational_rate(
                    float(raw_payload.get("funding_rate", row["funding_rate_raw"])),
                    "bps",
                )
                if raw_payload.get("funding_interval_s"):
                    inferred_interval_hours = float(raw_payload["funding_interval_s"]) / 3600.0
            else:
                funding_rate_decimal = row["funding_rate_raw"]
        if funding_rate_display_percent is None:
            funding_rate_display_percent = funding_decimal_to_percent(funding_rate_decimal)
        if (
            funding_rate_1h_equiv is None
            or abs(float(inferred_interval_hours) - float(interval_hours)) > 1e-9
        ):
            funding_rate_1h_equiv = to_1h_equivalent(
                funding_rate_decimal,
                inferred_interval_hours,
            )
        interval_hours = inferred_interval_hours

        return FundingSnapshot(
            exchange=row["exchange"],
            ticker=canonicalize_ticker(row["ticker"]),
            external_symbol=row["external_symbol"],
            funding_rate_raw=row["funding_rate_raw"],
            funding_rate_decimal=funding_rate_decimal,
            funding_rate_display_percent=funding_rate_display_percent,
            funding_interval_hours=interval_hours,
            funding_rate_1h_equiv=funding_rate_1h_equiv,
            observed_at=parse_datetime(row["observed_at"]),
            source_exchange_timestamp=parse_datetime(row["source_exchange_timestamp"]),
            normalization_mode=row["normalization_mode"]
            or ("variational_bps" if row["exchange"] == "variational" else "identity"),
            observation_source=row["observation_source"] or "live_poll",
            mark_price=row["mark_price"],
            volume_24h=row["volume_24h"],
            open_interest=row["open_interest"],
            next_settlement_at=parse_datetime(row["next_settlement_at"]),
            raw_payload=raw_payload,
        )


def _timestamp_ms_to_iso_string(timestamp_ms: int | float | str) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(float(timestamp_ms) / 1000.0, tz=UTC).isoformat()


def to_iso_from_hours_ago(hours: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(tz=UTC) - timedelta(hours=hours)).isoformat()


def _infer_mexc_interval_hours(
    raw_payload: dict,
    market_metadata: dict,
    stored_interval_hours: float | int | None,
    market_interval_hours: float | int | None,
) -> float:
    funding_payload = raw_payload.get("funding", {})
    if isinstance(funding_payload, dict) and funding_payload.get("collectCycle"):
        return float(funding_payload["collectCycle"])
    if raw_payload.get("collectCycle"):
        return float(raw_payload["collectCycle"])
    if market_metadata.get("collect_cycle_hours"):
        return float(market_metadata["collect_cycle_hours"])
    if market_interval_hours:
        return float(market_interval_hours)
    return float(stored_interval_hours or 8.0)
