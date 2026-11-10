"""Run budget limits, usage and the typed outcome of a budget check."""

from dataclasses import dataclass
from enum import Enum

from autonomous_mixing.core.records.invariants import require_count, require_finite, require_instance


@dataclass(frozen=True)
class BudgetLimits:
    """Configured resource limits. Zero count limits are valid (e.g. observe without attempting)."""

    max_decision_calls: int
    max_attempts: int
    max_render_calls: int
    max_recovery_operations: int
    max_elapsed_seconds: float

    def __post_init__(self) -> None:
        require_count(self.max_decision_calls, "BudgetLimits.max_decision_calls", minimum=0)
        require_count(self.max_attempts, "BudgetLimits.max_attempts", minimum=0)
        require_count(self.max_render_calls, "BudgetLimits.max_render_calls", minimum=0)
        require_count(self.max_recovery_operations, "BudgetLimits.max_recovery_operations", minimum=0)
        if require_finite(self.max_elapsed_seconds, "BudgetLimits.max_elapsed_seconds") <= 0:
            raise ValueError("BudgetLimits.max_elapsed_seconds must be strictly positive")


@dataclass(frozen=True)
class BudgetUsage:
    """Recorded resource use. Values beyond the configured limits are representable, so overruns stay visible."""

    decision_calls: int
    attempts: int
    render_calls: int
    recovery_operations: int
    elapsed_seconds: float

    def __post_init__(self) -> None:
        require_count(self.decision_calls, "BudgetUsage.decision_calls", minimum=0)
        require_count(self.attempts, "BudgetUsage.attempts", minimum=0)
        require_count(self.render_calls, "BudgetUsage.render_calls", minimum=0)
        require_count(self.recovery_operations, "BudgetUsage.recovery_operations", minimum=0)
        if require_finite(self.elapsed_seconds, "BudgetUsage.elapsed_seconds") < 0:
            raise ValueError("BudgetUsage.elapsed_seconds must be non-negative")


class BudgetOperation(Enum):
    """A charged operation whose next unit a budget check assesses."""

    DECISION_CALL = "decision_call"
    ATTEMPT = "attempt"
    RENDER_CALL = "render_call"
    RECOVERY_OPERATION = "recovery_operation"


class BudgetBlockReason(Enum):
    """Why a budget check blocks the next unit of an operation."""

    ELAPSED_TIME_LIMIT = "elapsed_time_limit"
    DECISION_CALL_LIMIT = "decision_call_limit"
    ATTEMPT_LIMIT = "attempt_limit"
    RENDER_CALL_LIMIT = "render_call_limit"
    RECOVERY_CAPACITY_LIMIT = "recovery_capacity_limit"


_PERMITTED_BLOCK_REASONS = {
    BudgetOperation.DECISION_CALL: frozenset(
        {BudgetBlockReason.ELAPSED_TIME_LIMIT, BudgetBlockReason.DECISION_CALL_LIMIT}
    ),
    BudgetOperation.ATTEMPT: frozenset({BudgetBlockReason.ELAPSED_TIME_LIMIT, BudgetBlockReason.ATTEMPT_LIMIT}),
    BudgetOperation.RENDER_CALL: frozenset(
        {
            BudgetBlockReason.ELAPSED_TIME_LIMIT,
            BudgetBlockReason.RENDER_CALL_LIMIT,
            BudgetBlockReason.RECOVERY_CAPACITY_LIMIT,
        }
    ),
    BudgetOperation.RECOVERY_OPERATION: frozenset(
        {BudgetBlockReason.ELAPSED_TIME_LIMIT, BudgetBlockReason.RECOVERY_CAPACITY_LIMIT}
    ),
}


@dataclass(frozen=True)
class BudgetGrant:
    """A proposed charge: the usage that would follow one more unit of ``operation``.

    A grant is not authorization, a reservation or proof of dispatch; it carries no limits, so its
    construction alone does not show that the charge follows from any configured limits.
    """

    operation: BudgetOperation
    usage_after_charge: BudgetUsage

    def __post_init__(self) -> None:
        require_instance(self.operation, BudgetOperation, "BudgetGrant.operation")
        require_instance(self.usage_after_charge, BudgetUsage, "BudgetGrant.usage_after_charge")


@dataclass(frozen=True)
class BudgetBlock:
    """The next unit of ``operation`` is blocked for ``reason``; ``usage`` is the observed checkpoint."""

    operation: BudgetOperation
    reason: BudgetBlockReason
    usage: BudgetUsage

    def __post_init__(self) -> None:
        require_instance(self.operation, BudgetOperation, "BudgetBlock.operation")
        require_instance(self.reason, BudgetBlockReason, "BudgetBlock.reason")
        require_instance(self.usage, BudgetUsage, "BudgetBlock.usage")
        if self.reason not in _PERMITTED_BLOCK_REASONS[self.operation]:
            raise ValueError(f"BudgetBlock.reason {self.reason.name} does not apply to {self.operation.name}")


BudgetAssessment = BudgetGrant | BudgetBlock
