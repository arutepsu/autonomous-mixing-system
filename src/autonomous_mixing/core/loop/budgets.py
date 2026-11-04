"""Pure budget check for the next charged loop operation (control-loop attempt contract, sections 25-26)."""

from dataclasses import replace
from typing import assert_never

from autonomous_mixing.core.records.budgets import (
    BudgetAssessment,
    BudgetBlock,
    BudgetBlockReason,
    BudgetGrant,
    BudgetLimits,
    BudgetOperation,
    BudgetUsage,
)
from autonomous_mixing.core.records.invariants import require_finite, require_instance


def check_budget(
    operation: BudgetOperation,
    *,
    limits: BudgetLimits,
    usage: BudgetUsage,
    elapsed_seconds: float,
) -> BudgetAssessment:
    """Propose the charge for one more unit of ``operation``, or return why it is blocked.

    ``elapsed_seconds`` is the caller's observed checkpoint; it must be finite, non-negative and not
    earlier than ``usage.elapsed_seconds``. Elapsed time at or above the limit blocks first; then the
    operation's own count blocks at equality with its limit (inclusive limits); a render additionally
    needs an unused recovery-operation slot. Only the relevant counter is checked, so exhausted
    decision/attempt/render counters do not block recovery. Recorded overruns are kept, not clamped.

    The result is a proposal: no counter is charged, no recovery slot is reserved and nothing is
    dispatched. Applying the grant's usage after an acknowledged dispatch intent, and rechecking time
    and cancellation before the effect, belong to the orchestrator.
    """
    require_instance(operation, BudgetOperation, "operation")
    require_instance(limits, BudgetLimits, "limits")
    require_instance(usage, BudgetUsage, "usage")
    if require_finite(elapsed_seconds, "elapsed_seconds") < 0:
        raise ValueError(f"elapsed_seconds must be non-negative, got {elapsed_seconds!r}")
    if elapsed_seconds < usage.elapsed_seconds:
        raise ValueError(f"elapsed_seconds {elapsed_seconds!r} is earlier than the recorded {usage.elapsed_seconds!r}")
    observed = replace(usage, elapsed_seconds=elapsed_seconds)

    if elapsed_seconds >= limits.max_elapsed_seconds:
        return BudgetBlock(operation, BudgetBlockReason.ELAPSED_TIME_LIMIT, observed)

    match operation:
        case BudgetOperation.DECISION_CALL:
            count, limit, reason = (
                usage.decision_calls,
                limits.max_decision_calls,
                BudgetBlockReason.DECISION_CALL_LIMIT,
            )
        case BudgetOperation.ATTEMPT:
            count, limit, reason = usage.attempts, limits.max_attempts, BudgetBlockReason.ATTEMPT_LIMIT
        case BudgetOperation.RENDER_CALL:
            count, limit, reason = usage.render_calls, limits.max_render_calls, BudgetBlockReason.RENDER_CALL_LIMIT
        case BudgetOperation.RECOVERY_OPERATION:
            count, limit = usage.recovery_operations, limits.max_recovery_operations
            reason = BudgetBlockReason.RECOVERY_CAPACITY_LIMIT
        case _:
            assert_never(operation)

    if count >= limit:
        return BudgetBlock(operation, reason, observed)
    if operation is BudgetOperation.RENDER_CALL and usage.recovery_operations >= limits.max_recovery_operations:
        return BudgetBlock(operation, BudgetBlockReason.RECOVERY_CAPACITY_LIMIT, observed)

    match operation:
        case BudgetOperation.DECISION_CALL:
            charged = replace(observed, decision_calls=count + 1)
        case BudgetOperation.ATTEMPT:
            charged = replace(observed, attempts=count + 1)
        case BudgetOperation.RENDER_CALL:
            charged = replace(observed, render_calls=count + 1)
        case BudgetOperation.RECOVERY_OPERATION:
            charged = replace(observed, recovery_operations=count + 1)
        case _:
            assert_never(operation)
    return BudgetGrant(operation, charged)
