from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(slots=True)
class Settings:
    app_name: str = "Funding Spread Monitor"
    database_path: Path = Path("data") / "funding_spread.sqlite3"
    request_timeout_s: float = 15.0
    variational_poll_interval_s: int = 60
    aster_catalog_refresh_interval_s: int = 1800
    aster_poll_interval_s: int = 60
    extended_catalog_refresh_interval_s: int = 1800
    extended_poll_interval_s: int = 60
    bitget_catalog_refresh_interval_s: int = 1800
    bitget_poll_interval_s: int = 60
    gate_catalog_refresh_interval_s: int = 1800
    gate_poll_interval_s: int = 60
    mexc_catalog_refresh_interval_s: int = 1800
    mexc_poll_interval_s: int = 60
    mexc_default_funding_interval_hours: float = 8.0
    variational_normalization_mode: str = "bps"
    aster_history_backfill_enabled: bool = False
    aster_history_lookback_hours: int = 24
    aster_history_limit: int = 100
    extended_history_backfill_enabled: bool = False
    extended_history_lookback_hours: int = 24
    bitget_history_backfill_enabled: bool = False
    bitget_history_lookback_hours: int = 24
    bitget_history_page_size: int = 100
    gate_history_backfill_enabled: bool = False
    gate_history_lookback_hours: int = 24
    gate_history_limit: int = 200
    mexc_collect_overlapping_only: bool = False
    mexc_current_batch_size: int = 10
    mexc_current_batch_pause_s: float = 1.0
    mexc_history_backfill_enabled: bool = True
    mexc_history_lookback_hours: int = 24
    mexc_history_page_size: int = 100
    show_only_multi_exchange_default: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        database_path = Path(
            os.getenv("FUNDING_SPREAD_DB_PATH", str(defaults.database_path))
        )
        return cls(
            app_name=os.getenv("FUNDING_SPREAD_APP_NAME", defaults.app_name),
            database_path=database_path,
            request_timeout_s=float(
                os.getenv("FUNDING_SPREAD_REQUEST_TIMEOUT_S", defaults.request_timeout_s)
            ),
            variational_poll_interval_s=int(
                os.getenv(
                    "FUNDING_SPREAD_VARIATIONAL_POLL_INTERVAL_S",
                    defaults.variational_poll_interval_s,
                )
            ),
            aster_poll_interval_s=int(
                os.getenv(
                    "FUNDING_SPREAD_ASTER_POLL_INTERVAL_S",
                    defaults.aster_poll_interval_s,
                )
            ),
            aster_catalog_refresh_interval_s=int(
                os.getenv(
                    "FUNDING_SPREAD_ASTER_CATALOG_REFRESH_INTERVAL_S",
                    defaults.aster_catalog_refresh_interval_s,
                )
            ),
            extended_poll_interval_s=int(
                os.getenv(
                    "FUNDING_SPREAD_EXTENDED_POLL_INTERVAL_S",
                    defaults.extended_poll_interval_s,
                )
            ),
            extended_catalog_refresh_interval_s=int(
                os.getenv(
                    "FUNDING_SPREAD_EXTENDED_CATALOG_REFRESH_INTERVAL_S",
                    defaults.extended_catalog_refresh_interval_s,
                )
            ),
            bitget_poll_interval_s=int(
                os.getenv(
                    "FUNDING_SPREAD_BITGET_POLL_INTERVAL_S",
                    defaults.bitget_poll_interval_s,
                )
            ),
            bitget_catalog_refresh_interval_s=int(
                os.getenv(
                    "FUNDING_SPREAD_BITGET_CATALOG_REFRESH_INTERVAL_S",
                    defaults.bitget_catalog_refresh_interval_s,
                )
            ),
            gate_poll_interval_s=int(
                os.getenv(
                    "FUNDING_SPREAD_GATE_POLL_INTERVAL_S",
                    defaults.gate_poll_interval_s,
                )
            ),
            gate_catalog_refresh_interval_s=int(
                os.getenv(
                    "FUNDING_SPREAD_GATE_CATALOG_REFRESH_INTERVAL_S",
                    defaults.gate_catalog_refresh_interval_s,
                )
            ),
            mexc_catalog_refresh_interval_s=int(
                os.getenv(
                    "FUNDING_SPREAD_MEXC_CATALOG_REFRESH_INTERVAL_S",
                    defaults.mexc_catalog_refresh_interval_s,
                )
            ),
            mexc_poll_interval_s=int(
                os.getenv("FUNDING_SPREAD_MEXC_POLL_INTERVAL_S", defaults.mexc_poll_interval_s)
            ),
            mexc_default_funding_interval_hours=float(
                os.getenv(
                    "FUNDING_SPREAD_MEXC_DEFAULT_FUNDING_INTERVAL_HOURS",
                    defaults.mexc_default_funding_interval_hours,
                )
            ),
            variational_normalization_mode=os.getenv(
                "FUNDING_SPREAD_VARIATIONAL_NORMALIZATION_MODE",
                defaults.variational_normalization_mode,
            ).lower(),
            aster_history_backfill_enabled=os.getenv(
                "FUNDING_SPREAD_ASTER_HISTORY_BACKFILL_ENABLED",
                str(defaults.aster_history_backfill_enabled),
            ).lower()
            in {"1", "true", "yes", "on"},
            aster_history_lookback_hours=int(
                os.getenv(
                    "FUNDING_SPREAD_ASTER_HISTORY_LOOKBACK_HOURS",
                    defaults.aster_history_lookback_hours,
                )
            ),
            aster_history_limit=int(
                os.getenv(
                    "FUNDING_SPREAD_ASTER_HISTORY_LIMIT",
                    defaults.aster_history_limit,
                )
            ),
            extended_history_backfill_enabled=os.getenv(
                "FUNDING_SPREAD_EXTENDED_HISTORY_BACKFILL_ENABLED",
                str(defaults.extended_history_backfill_enabled),
            ).lower()
            in {"1", "true", "yes", "on"},
            extended_history_lookback_hours=int(
                os.getenv(
                    "FUNDING_SPREAD_EXTENDED_HISTORY_LOOKBACK_HOURS",
                    defaults.extended_history_lookback_hours,
                )
            ),
            bitget_history_backfill_enabled=os.getenv(
                "FUNDING_SPREAD_BITGET_HISTORY_BACKFILL_ENABLED",
                str(defaults.bitget_history_backfill_enabled),
            ).lower()
            in {"1", "true", "yes", "on"},
            bitget_history_lookback_hours=int(
                os.getenv(
                    "FUNDING_SPREAD_BITGET_HISTORY_LOOKBACK_HOURS",
                    defaults.bitget_history_lookback_hours,
                )
            ),
            bitget_history_page_size=int(
                os.getenv(
                    "FUNDING_SPREAD_BITGET_HISTORY_PAGE_SIZE",
                    defaults.bitget_history_page_size,
                )
            ),
            gate_history_backfill_enabled=os.getenv(
                "FUNDING_SPREAD_GATE_HISTORY_BACKFILL_ENABLED",
                str(defaults.gate_history_backfill_enabled),
            ).lower()
            in {"1", "true", "yes", "on"},
            gate_history_lookback_hours=int(
                os.getenv(
                    "FUNDING_SPREAD_GATE_HISTORY_LOOKBACK_HOURS",
                    defaults.gate_history_lookback_hours,
                )
            ),
            gate_history_limit=int(
                os.getenv(
                    "FUNDING_SPREAD_GATE_HISTORY_LIMIT",
                    defaults.gate_history_limit,
                )
            ),
            mexc_collect_overlapping_only=os.getenv(
                "FUNDING_SPREAD_MEXC_COLLECT_OVERLAPPING_ONLY",
                str(defaults.mexc_collect_overlapping_only),
            ).lower()
            in {"1", "true", "yes", "on"},
            mexc_current_batch_size=int(
                os.getenv(
                    "FUNDING_SPREAD_MEXC_CURRENT_BATCH_SIZE",
                    defaults.mexc_current_batch_size,
                )
            ),
            mexc_current_batch_pause_s=float(
                os.getenv(
                    "FUNDING_SPREAD_MEXC_CURRENT_BATCH_PAUSE_S",
                    defaults.mexc_current_batch_pause_s,
                )
            ),
            mexc_history_backfill_enabled=os.getenv(
                "FUNDING_SPREAD_MEXC_HISTORY_BACKFILL_ENABLED",
                str(defaults.mexc_history_backfill_enabled),
            ).lower()
            in {"1", "true", "yes", "on"},
            mexc_history_lookback_hours=int(
                os.getenv(
                    "FUNDING_SPREAD_MEXC_HISTORY_LOOKBACK_HOURS",
                    defaults.mexc_history_lookback_hours,
                )
            ),
            mexc_history_page_size=int(
                os.getenv(
                    "FUNDING_SPREAD_MEXC_HISTORY_PAGE_SIZE",
                    defaults.mexc_history_page_size,
                )
            ),
            show_only_multi_exchange_default=os.getenv(
                "FUNDING_SPREAD_SHOW_ONLY_MULTI_EXCHANGE_DEFAULT",
                str(defaults.show_only_multi_exchange_default),
            ).lower()
            in {"1", "true", "yes", "on"},
        )
