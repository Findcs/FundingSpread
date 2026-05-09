from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def normalize_funding_rate(rate: float, funding_interval_hours: float) -> float:
    if funding_interval_hours <= 0:
        raise ValueError("funding_interval_hours must be positive")
    return rate * (8.0 / funding_interval_hours)


def funding_decimal_to_percent(rate_decimal: float) -> float:
    return rate_decimal * 100.0


def to_1h_equivalent(rate_decimal: float, funding_interval_hours: float) -> float:
    if funding_interval_hours <= 0:
        raise ValueError("funding_interval_hours must be positive")
    return rate_decimal / funding_interval_hours


def normalize_variational_rate(raw_rate: float, mode: str) -> float:
    normalized_mode = mode.lower()
    if normalized_mode == "bps":
        return raw_rate / 10000.0
    if normalized_mode == "percent":
        return raw_rate / 100.0
    if normalized_mode == "decimal":
        return raw_rate
    raise ValueError(f"Unsupported Variational normalization mode: {mode}")
