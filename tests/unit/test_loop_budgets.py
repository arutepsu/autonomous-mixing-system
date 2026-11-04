"""Budget-gate records and the pure ``check_budget`` projection (budget-gate plan, Task 1).

Count limits of ``1`` and an elapsed limit of ``10.0`` are software-boundary fixtures, not scientific
thresholds. A grant is a proposed charge only; nothing here dispatches, reserves or reads a clock.
"""

import dataclasses
import inspect
import math

import pytest

from autonomous_mixing.core.loop.budgets import check_budget
from autonomous_mixing.core.records.budgets import (
    BudgetBlock,
    BudgetBlockReason,
    BudgetGrant,
    BudgetLimits,
    BudgetOperation,
    BudgetUsage,
)

Op = BudgetOperation
Reason = BudgetBlockReason

COUNTER = {
    Op.DECISION_CALL: "decision_calls",
    Op.ATTEMPT: "attempts",
    Op.RENDER_CALL: "render_calls",
    Op.RECOVERY_OPERATION: "recovery_operations",
}
LIMIT = {
    Op.DECISION_CALL: "max_decision_calls",
    Op.ATTEMPT: "max_attempts",
    Op.RENDER_CALL: "max_render_calls",
    Op.RECOVERY_OPERATION: "max_recovery_operations",
}
COUNT_REASON = {
    Op.DECISION_CALL: Reason.DECISION_CALL_LIMIT,
    Op.ATTEMPT: Reason.ATTEMPT_LIMIT,
    Op.RENDER_CALL: Reason.RENDER_CALL_LIMIT,
    Op.RECOVERY_OPERATION: Reason.RECOVERY_CAPACITY_LIMIT,
}
PERMITTED = {
    Op.DECISION_CALL: {Reason.ELAPSED_TIME_LIMIT, Reason.DECISION_CALL_LIMIT},
    Op.ATTEMPT: {Reason.ELAPSED_TIME_LIMIT, Reason.ATTEMPT_LIMIT},
    Op.RENDER_CALL: {Reason.ELAPSED_TIME_LIMIT, Reason.RENDER_CALL_LIMIT, Reason.RECOVERY_CAPACITY_LIMIT},
    Op.RECOVERY_OPERATION: {Reason.ELAPSED_TIME_LIMIT, Reason.RECOVERY_CAPACITY_LIMIT},
}


def _usage(**overrides) -> BudgetUsage:
    values = {"decision_calls": 0, "attempts": 0, "render_calls": 0, "recovery_operations": 0, "elapsed_seconds": 0.0}
    return BudgetUsage(**{**values, **overrides})


def _limits(**overrides) -> BudgetLimits:
    values = {
        "max_decision_calls": 1,
        "max_attempts": 1,
        "max_render_calls": 1,
        "max_recovery_operations": 1,
        "max_elapsed_seconds": 10.0,
    }
    return BudgetLimits(**{**values, **overrides})


# --- records ---------------------------------------------------------------------------------------


def test_budget_operation_names_and_values_are_exact():
    assert [(m.name, m.value) for m in BudgetOperation] == [
        ("DECISION_CALL", "decision_call"),
        ("ATTEMPT", "attempt"),
        ("RENDER_CALL", "render_call"),
        ("RECOVERY_OPERATION", "recovery_operation"),
    ]


def test_budget_block_reason_names_and_values_are_exact():
    assert [(m.name, m.value) for m in BudgetBlockReason] == [
        ("ELAPSED_TIME_LIMIT", "elapsed_time_limit"),
        ("DECISION_CALL_LIMIT", "decision_call_limit"),
        ("ATTEMPT_LIMIT", "attempt_limit"),
        ("RENDER_CALL_LIMIT", "render_call_limit"),
        ("RECOVERY_CAPACITY_LIMIT", "recovery_capacity_limit"),
    ]


def test_outcome_records_have_exactly_the_required_fields():
    assert [(f.name, f.type) for f in dataclasses.fields(BudgetGrant)] == [
        ("operation", BudgetOperation),
        ("usage_after_charge", BudgetUsage),
    ]
    assert [(f.name, f.type) for f in dataclasses.fields(BudgetBlock)] == [
        ("operation", BudgetOperation),
        ("reason", BudgetBlockReason),
        ("usage", BudgetUsage),
    ]
    for record in (BudgetGrant, BudgetBlock):
        assert all(
            f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
            for f in dataclasses.fields(record)
        )


def test_budget_assessment_is_the_grant_or_block_union():
    from autonomous_mixing.core.records.budgets import BudgetAssessment

    assert BudgetAssessment == BudgetGrant | BudgetBlock


def test_outcome_records_are_frozen():
    grant = BudgetGrant(Op.ATTEMPT, _usage(attempts=1))
    block = BudgetBlock(Op.ATTEMPT, Reason.ATTEMPT_LIMIT, _usage())
    with pytest.raises(dataclasses.FrozenInstanceError):
        grant.operation = Op.DECISION_CALL  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        block.reason = Reason.ELAPSED_TIME_LIMIT  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"operation": "attempt", "usage_after_charge": _usage()},
        {"operation": Reason.ATTEMPT_LIMIT, "usage_after_charge": _usage()},
        {"operation": Op.ATTEMPT, "usage_after_charge": _limits()},
        {"operation": Op.ATTEMPT, "usage_after_charge": None},
    ],
)
def test_budget_grant_rejects_wrong_field_types(kwargs):
    with pytest.raises(ValueError):
        BudgetGrant(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"operation": "attempt", "reason": Reason.ATTEMPT_LIMIT, "usage": _usage()},
        {"operation": Op.ATTEMPT, "reason": "attempt_limit", "usage": _usage()},
        {"operation": Op.ATTEMPT, "reason": Op.ATTEMPT, "usage": _usage()},
        {"operation": Op.ATTEMPT, "reason": Reason.ATTEMPT_LIMIT, "usage": _limits()},
    ],
)
def test_budget_block_rejects_wrong_field_types(kwargs):
    with pytest.raises(ValueError):
        BudgetBlock(**kwargs)


@pytest.mark.parametrize("operation", list(BudgetOperation))
@pytest.mark.parametrize("reason", list(BudgetBlockReason))
def test_budget_block_accepts_only_reasons_related_to_its_operation(operation, reason):
    if reason in PERMITTED[operation]:
        block = BudgetBlock(operation, reason, _usage())
        assert (block.operation, block.reason) == (operation, reason)
    else:
        with pytest.raises(ValueError):
            BudgetBlock(operation, reason, _usage())


def test_existing_budget_limits_and_usage_signatures_are_unchanged():
    assert list(inspect.signature(BudgetLimits).parameters) == [
        "max_decision_calls",
        "max_attempts",
        "max_render_calls",
        "max_recovery_operations",
        "max_elapsed_seconds",
    ]
    assert list(inspect.signature(BudgetUsage).parameters) == [
        "decision_calls",
        "attempts",
        "render_calls",
        "recovery_operations",
        "elapsed_seconds",
    ]
    assert BudgetLimits(0, 0, 0, 0, 1.0).max_attempts == 0
    with pytest.raises(ValueError):
        BudgetLimits(0, 0, 0, 0, 0.0)
    over = BudgetUsage(9, 9, 9, 9, 99.0)  # over-limit usage stays representable
    assert over.decision_calls == 9
    with pytest.raises(ValueError):
        BudgetUsage(0, 0, 0, 0, -1.0)


# --- check_budget ----------------------------------------------------------------------------------


def _check(operation, *, limits=None, usage=None, elapsed=None):
    usage = _usage() if usage is None else usage
    return check_budget(
        operation,
        limits=_limits() if limits is None else limits,
        usage=usage,
        elapsed_seconds=usage.elapsed_seconds if elapsed is None else elapsed,
    )


def _counts(usage: BudgetUsage) -> dict[str, int]:
    return {name: getattr(usage, name) for name in COUNTER.values()}


def test_check_budget_signature_is_exact():
    signature = inspect.signature(check_budget)
    assert [(p.name, p.kind) for p in signature.parameters.values()] == [
        ("operation", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ("limits", inspect.Parameter.KEYWORD_ONLY),
        ("usage", inspect.Parameter.KEYWORD_ONLY),
        ("elapsed_seconds", inspect.Parameter.KEYWORD_ONLY),
    ]


@pytest.mark.parametrize("operation", list(BudgetOperation))
def test_first_unit_is_granted_and_the_projected_usage_blocks_the_next(operation):
    usage = _usage(elapsed_seconds=1.0)
    grant = _check(operation, usage=usage, elapsed=2.0)
    assert grant == BudgetGrant(operation, dataclasses.replace(usage, **{COUNTER[operation]: 1}, elapsed_seconds=2.0))
    unchanged = {name: value for name, value in _counts(grant.usage_after_charge).items() if name != COUNTER[operation]}
    assert unchanged == {name: 0 for name in COUNTER.values() if name != COUNTER[operation]}
    following = _check(operation, usage=grant.usage_after_charge, elapsed=3.0)
    assert following == BudgetBlock(
        operation, COUNT_REASON[operation], dataclasses.replace(grant.usage_after_charge, elapsed_seconds=3.0)
    )


@pytest.mark.parametrize("operation", list(BudgetOperation))
@pytest.mark.parametrize(("limit", "count"), [(0, 0), (1, 1), (1, 3), (0, 2)])
def test_count_at_or_above_its_limit_blocks_without_normalizing(operation, limit, count):
    usage = _usage(**{COUNTER[operation]: count}, elapsed_seconds=4.0)
    result = _check(operation, limits=_limits(**{LIMIT[operation]: limit}), usage=usage)
    assert result == BudgetBlock(operation, COUNT_REASON[operation], usage)
    assert getattr(result.usage, COUNTER[operation]) == count


@pytest.mark.parametrize("operation", list(BudgetOperation))
def test_elapsed_below_the_limit_grants(operation):
    result = _check(operation, elapsed=9.0)
    assert isinstance(result, BudgetGrant)
    assert result.usage_after_charge.elapsed_seconds == 9.0


@pytest.mark.parametrize("operation", list(BudgetOperation))
@pytest.mark.parametrize("elapsed", [10.0, 11.0])
def test_elapsed_at_or_above_the_limit_blocks_with_the_actual_value(operation, elapsed):
    usage = _usage(elapsed_seconds=5.0)
    assert _check(operation, usage=usage, elapsed=elapsed) == BudgetBlock(
        operation, Reason.ELAPSED_TIME_LIMIT, dataclasses.replace(usage, elapsed_seconds=elapsed)
    )


@pytest.mark.parametrize("operation", list(BudgetOperation))
def test_elapsed_limit_wins_over_exhausted_counts_and_recovery_capacity(operation):
    usage = _usage(decision_calls=1, attempts=1, render_calls=1, recovery_operations=1)
    result = _check(operation, usage=usage, elapsed=10.0)
    assert result == BudgetBlock(operation, Reason.ELAPSED_TIME_LIMIT, dataclasses.replace(usage, elapsed_seconds=10.0))


@pytest.mark.parametrize("recovery_limit", [0, 1])
def test_render_without_unused_recovery_capacity_is_blocked(recovery_limit):
    usage = _usage(recovery_operations=recovery_limit)
    result = _check(Op.RENDER_CALL, limits=_limits(max_recovery_operations=recovery_limit), usage=usage)
    assert result == BudgetBlock(Op.RENDER_CALL, Reason.RECOVERY_CAPACITY_LIMIT, usage)


def test_render_with_recovery_capacity_charges_only_the_render():
    usage = _usage(decision_calls=1, attempts=1, recovery_operations=1)
    result = _check(Op.RENDER_CALL, limits=_limits(max_recovery_operations=2), usage=usage)
    assert result == BudgetGrant(Op.RENDER_CALL, dataclasses.replace(usage, render_calls=1))


def test_exhausted_render_count_wins_over_exhausted_recovery_capacity():
    usage = _usage(render_calls=1, recovery_operations=1)
    assert _check(Op.RENDER_CALL, usage=usage) == BudgetBlock(Op.RENDER_CALL, Reason.RENDER_CALL_LIMIT, usage)


def test_recovery_is_granted_when_decision_attempt_and_render_usage_exceed_their_limits():
    usage = _usage(decision_calls=5, attempts=4, render_calls=3, recovery_operations=0)
    result = _check(Op.RECOVERY_OPERATION, usage=usage)
    assert result == BudgetGrant(Op.RECOVERY_OPERATION, dataclasses.replace(usage, recovery_operations=1))


@pytest.mark.parametrize("operation", [Op.DECISION_CALL, Op.ATTEMPT])
def test_decision_and_attempt_check_only_their_own_counter(operation):
    usage = _usage(**{name: 7 for op, name in COUNTER.items() if op is not operation})
    limits = _limits(**{LIMIT[op]: 0 for op in COUNTER if op is not operation})
    result = _check(operation, limits=limits, usage=usage)
    assert result == BudgetGrant(operation, dataclasses.replace(usage, **{COUNTER[operation]: 1}))


@pytest.mark.parametrize("operation", list(BudgetOperation))
def test_a_grant_preserves_unrelated_recorded_overruns(operation):
    overruns = {name: 9 for op, name in COUNTER.items() if op is not operation}
    if operation is Op.RENDER_CALL:
        overruns["recovery_operations"] = 0  # a render needs unused recovery capacity
    usage = _usage(**overruns)
    result = _check(operation, usage=usage)
    assert isinstance(result, BudgetGrant)
    for name, value in overruns.items():
        assert getattr(result.usage_after_charge, name) == value


@pytest.mark.parametrize("operation", list(BudgetOperation))
@pytest.mark.parametrize("exhausted", [False, True])
def test_inputs_are_unchanged_and_the_operation_is_retained(operation, exhausted):
    usage = _usage(**{COUNTER[operation]: 1 if exhausted else 0}, elapsed_seconds=2.5)
    limits = _limits()
    usage_before, limits_before = dataclasses.astuple(usage), dataclasses.astuple(limits)
    result = check_budget(operation, limits=limits, usage=usage, elapsed_seconds=3.5)
    assert result.operation is operation
    assert isinstance(result, BudgetBlock if exhausted else BudgetGrant)
    assert dataclasses.astuple(usage) == usage_before
    assert dataclasses.astuple(limits) == limits_before
    observed = result.usage if exhausted else result.usage_after_charge
    assert observed is not usage
    assert observed.elapsed_seconds == 3.5


@pytest.mark.parametrize("operation", list(BudgetOperation))
def test_a_block_updates_elapsed_without_adding_a_counter(operation):
    usage = _usage(decision_calls=1, attempts=1, render_calls=1, recovery_operations=1, elapsed_seconds=1.0)
    result = _check(operation, usage=usage, elapsed=6.0)
    assert isinstance(result, BudgetBlock)
    assert _counts(result.usage) == _counts(usage)
    assert result.usage.elapsed_seconds == 6.0


def test_equal_recorded_elapsed_below_the_deadline_is_valid():
    usage = _usage(elapsed_seconds=7.0)
    result = _check(Op.ATTEMPT, usage=usage, elapsed=7.0)
    assert result == BudgetGrant(Op.ATTEMPT, dataclasses.replace(usage, attempts=1))


def test_large_integer_limits_stay_exact():
    big = 10**100
    limits = _limits(max_decision_calls=big)
    grant = _check(Op.DECISION_CALL, limits=limits, usage=_usage(decision_calls=big - 1))
    assert isinstance(grant, BudgetGrant)
    assert grant.usage_after_charge.decision_calls == big
    assert type(grant.usage_after_charge.decision_calls) is int
    assert _check(Op.DECISION_CALL, limits=limits, usage=grant.usage_after_charge) == BudgetBlock(
        Op.DECISION_CALL, Reason.DECISION_CALL_LIMIT, grant.usage_after_charge
    )


@pytest.mark.parametrize(
    ("operation", "limits", "usage"),
    [
        ("attempt", _limits(), _usage()),
        (None, _limits(), _usage()),
        (Reason.ATTEMPT_LIMIT, _limits(), _usage()),
        (Op.ATTEMPT, _usage(), _usage()),
        (Op.ATTEMPT, _limits(), _limits()),
        (Op.ATTEMPT, None, _usage()),
        (Op.ATTEMPT, _limits(), None),
    ],
)
def test_invalid_operation_limits_or_usage_types_raise(operation, limits, usage):
    with pytest.raises(ValueError):
        check_budget(operation, limits=limits, usage=usage, elapsed_seconds=1.0)


@pytest.mark.parametrize("elapsed", [True, False, "1.0", None, math.nan, math.inf, -math.inf, -0.5, 10**400])
def test_invalid_elapsed_seconds_raise(elapsed):
    with pytest.raises(ValueError):
        check_budget(Op.ATTEMPT, limits=_limits(), usage=_usage(), elapsed_seconds=elapsed)


def test_elapsed_lower_than_recorded_elapsed_raises():
    with pytest.raises(ValueError):
        check_budget(Op.ATTEMPT, limits=_limits(), usage=_usage(elapsed_seconds=5.0), elapsed_seconds=4.999)
