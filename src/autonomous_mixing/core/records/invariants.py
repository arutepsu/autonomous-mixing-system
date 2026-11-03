"""Invariant helpers used by record constructors and input boundaries."""

import math
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any


def require_text(value: Any, field: str) -> str:
    """Return ``value`` if it is a string with at least one non-whitespace character."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string, got {value!r}")
    return value


def require_finite(value: Any, field: str) -> float:
    """Return ``value`` unchanged if it is a finite int or float; bool and strings are rejected."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number, got {value!r}")
    try:
        finite = math.isfinite(value)
    except OverflowError:  # an int beyond the float range
        finite = False
    if not finite:
        raise ValueError(f"{field} must be finite and representable as a float")
    return value


def require_finite_result(compute: Callable[[], float], field: str) -> float:
    """Return a derived float, raising ValueError if computing it overflows or yields a non-finite value."""
    try:
        value = compute()
    except OverflowError:
        raise ValueError(f"{field} must be finite and representable as a float") from None
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite and representable as a float")
    return value


def require_count(value: Any, field: str, minimum: int) -> int:
    """Return ``value`` if it is an int (not bool) of at least ``minimum``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer, got {value!r}")
    if value < minimum:
        raise ValueError(f"{field} must be >= {minimum}, got {value}")
    return value


def require_utc(value: Any, field: str) -> datetime:
    """Return ``value`` if it is a timezone-aware datetime with zero UTC offset."""
    if not isinstance(value, datetime):
        raise ValueError(f"{field} must be a datetime, got {value!r}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware UTC, got naive {value!r}")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must have a UTC offset of zero, got {value.utcoffset()}")
    return value


def require_instance(value: Any, cls: type | tuple[type, ...], field: str) -> None:
    if not isinstance(value, cls):
        raise ValueError(f"{field} has invalid type {type(value).__name__}")


def require_tuple_of(value: Any, cls: type, field: str, *, nonempty: bool, distinct: bool) -> tuple[Any, ...]:
    """Validate an immutable tuple of ``cls`` elements; mutable sequences are rejected, not copied."""
    if not isinstance(value, tuple):
        raise ValueError(f"{field} must be a tuple, got {type(value).__name__}")
    if nonempty and not value:
        raise ValueError(f"{field} must not be empty")
    for item in value:
        require_instance(item, cls, f"{field} element")
    if distinct and len(set(value)) != len(value):
        raise ValueError(f"{field} must not contain duplicates")
    return value
