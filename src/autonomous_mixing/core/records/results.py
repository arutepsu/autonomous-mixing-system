"""Typed port results."""

from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

from autonomous_mixing.core.records.invariants import require_instance, require_text

T = TypeVar("T")


@dataclass(frozen=True)
class Success(Generic[T]):
    value: T


class PortFailureCode(Enum):
    INVALID_INPUT = "invalid_input"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class PortFailure:
    code: PortFailureCode
    message: str
    retryable: bool

    def __post_init__(self) -> None:
        require_instance(self.code, PortFailureCode, "PortFailure.code")
        require_text(self.message, "PortFailure.message")
        if not isinstance(self.retryable, bool):
            raise ValueError(f"PortFailure.retryable must be a bool, got {self.retryable!r}")


PortResult = Success[T] | PortFailure
