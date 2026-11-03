"""Run budget limits and usage."""

from dataclasses import dataclass

from autonomous_mixing.core.records.invariants import require_count, require_finite


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
