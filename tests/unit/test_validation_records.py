"""Focused tests for validation criteria and result records (Task 2B).

Metric names, units and thresholds are arbitrary structural fixtures, not scientifically justified values.
"""

import dataclasses
import math

import pytest

from autonomous_mixing.core.records.identifiers import (
    ConditionId,
    CriteriaId,
    ObservationId,
    TrackId,
    ValidationResultId,
    VersionRef,
)
from autonomous_mixing.core.records.observation import AnalysisScope, MeasuredScalar, TimeRange
from autonomous_mixing.core.records.verification import (
    AbsoluteGuard,
    AssessmentMode,
    BaselineReference,
    CheckResult,
    CheckStatus,
    ComparisonOperator,
    DeltaGuard,
    ImprovementCondition,
    ImprovementDirection,
    MetricSelector,
    TargetCondition,
    TargetStatus,
    ValidationCriteria,
    ValidationResult,
    ValidationVerdict,
)
from autonomous_mixing.core.verification.consistency import validate_validation_references

PASS, FAIL, UNAVAILABLE, NA = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNAVAILABLE, CheckStatus.NOT_APPLICABLE


def _selector(metric: str = "metric-a", track: str = "t1") -> MetricSelector:
    return MetricSelector(VersionRef(metric, "1"), AnalysisScope((TrackId(track),), TimeRange(0.0, 8.0)))


def _target(cid: str = "target") -> TargetCondition:
    return TargetCondition(ConditionId(cid), _selector(), ComparisonOperator.LE, MeasuredScalar(-6.0, "unit-a"))


def _improvement(cid: str = "improve", delta: float = 1.0) -> ImprovementCondition:
    return ImprovementCondition(
        ConditionId(cid), _selector(), ImprovementDirection.LOWER, MeasuredScalar(delta, "unit-a")
    )


def _absolute(cid: str = "abs-guard") -> AbsoluteGuard:
    return AbsoluteGuard(
        ConditionId(cid), _selector("metric-b", "t2"), ComparisonOperator.LT, MeasuredScalar(0, "unit-b")
    )


def _delta(cid: str = "delta-guard", limit: float = 2.0, baseline=BaselineReference.ATTEMPT) -> DeltaGuard:
    return DeltaGuard(ConditionId(cid), _selector("metric-c", "t3"), baseline, MeasuredScalar(limit, "unit-c"))


def _criteria(**overrides) -> ValidationCriteria:
    fields = dict(
        criteria_id=CriteriaId("crit"),
        version="1",
        targets=(_target(),),
        improvements=(_improvement(),),
        guards=(_absolute(), _delta()),
    )
    fields.update(overrides)
    return ValidationCriteria(**fields)


def _check(cid: str, status: CheckStatus, evidence: tuple[str, ...] | None = None) -> CheckResult:
    if evidence is None:
        evidence = ("o1",) if status in (PASS, FAIL) else ()
    return CheckResult(ConditionId(cid), status, f"{status.name} for {cid}", tuple(ObservationId(o) for o in evidence))


def _result(statuses: dict[str, CheckStatus], mode, verdict, target_status, **overrides) -> ValidationResult:
    fields = dict(
        validation_result_id=ValidationResultId("v1"),
        criteria_id=CriteriaId("crit"),
        criteria_version="1",
        mode=mode,
        checks=tuple(_check(cid, status) for cid, status in statuses.items()),
        verdict=verdict,
        target_status=target_status,
    )
    fields.update(overrides)
    return ValidationResult(**fields)


def _attempt(target=PASS, improve=PASS, abs_guard=PASS, delta_guard=PASS) -> dict[str, CheckStatus]:
    return {"target": target, "improve": improve, "abs-guard": abs_guard, "delta-guard": delta_guard}


def _initial(target=PASS, abs_guard=PASS) -> dict[str, CheckStatus]:
    return {"target": target, "improve": NA, "abs-guard": abs_guard, "delta-guard": NA}


ATTEMPT, INITIAL = AssessmentMode.ATTEMPT, AssessmentMode.INITIAL
ACCEPTED, REJECTED, INCONCLUSIVE = (
    ValidationVerdict.ACCEPTED,
    ValidationVerdict.REJECTED,
    ValidationVerdict.INCONCLUSIVE,
)
SATISFIED, UNMET, UNKNOWN = TargetStatus.SATISFIED, TargetStatus.UNMET, TargetStatus.UNKNOWN


# --- identifiers and enums -------------------------------------------------------


@pytest.mark.parametrize("cls", [CriteriaId, ConditionId, ValidationResultId])
@pytest.mark.parametrize("raw", ["", " ", 3, None])
def test_new_identifiers_reject_blank_and_non_string(cls, raw):
    with pytest.raises(ValueError):
        cls(raw)


def test_new_identifiers_are_distinct_kinds():
    assert CriteriaId("x") != ConditionId("x")
    assert ConditionId("x") != ValidationResultId("x")


@pytest.mark.parametrize(
    "enum, names",
    [
        (ComparisonOperator, {"LT", "LE", "GT", "GE"}),
        (ImprovementDirection, {"LOWER", "HIGHER"}),
        (BaselineReference, {"ATTEMPT", "INITIAL"}),
        (AssessmentMode, {"INITIAL", "ATTEMPT"}),
        (CheckStatus, {"PASS", "FAIL", "UNAVAILABLE", "NOT_APPLICABLE"}),
        (ValidationVerdict, {"ACCEPTED", "REJECTED", "INCONCLUSIVE"}),
        (TargetStatus, {"SATISFIED", "UNMET", "UNKNOWN"}),
    ],
)
def test_enums_have_exactly_the_specified_members(enum, names):
    assert {member.name for member in enum} == names


# --- criteria --------------------------------------------------------------------


@pytest.mark.parametrize("operator", list(ComparisonOperator))
@pytest.mark.parametrize("direction", list(ImprovementDirection))
@pytest.mark.parametrize("baseline", list(BaselineReference))
def test_criteria_accept_every_operator_direction_and_baseline(operator, direction, baseline):
    target = TargetCondition(ConditionId("target"), _selector(), operator, MeasuredScalar(3, "unit-a"))
    improvement = ImprovementCondition(ConditionId("improve"), _selector(), direction, MeasuredScalar(0.5, "unit-a"))
    guard = AbsoluteGuard(ConditionId("abs-guard"), _selector(), operator, MeasuredScalar(-1.5, "unit-a"))
    criteria = _criteria(targets=(target,), improvements=(improvement,), guards=(guard, _delta(baseline=baseline)))
    assert criteria.guards[1].baseline is baseline


def test_criteria_allow_empty_guards():
    assert _criteria(guards=()).guards == ()


@pytest.mark.parametrize(
    "overrides",
    [
        {"targets": ()},
        {"improvements": ()},
        {"targets": [_target()]},
        {"improvements": [_improvement()]},
        {"guards": [_absolute()]},
        {"guards": frozenset({_absolute()})},
    ],
)
def test_criteria_reject_empty_required_and_mutable_collections(overrides):
    with pytest.raises(ValueError):
        _criteria(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"targets": (_improvement("t-x"),)},
        {"improvements": (_target("i-x"),)},
        {"guards": (_target("g-x"),)},
        {"guards": (_improvement("g-x"),)},
        {"guards": (None,)},
        {"criteria_id": "crit"},
        {"version": ""},
        {"version": 1},
    ],
)
def test_criteria_reject_wrong_field_and_element_types(overrides):
    with pytest.raises(ValueError):
        _criteria(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"targets": (_target("dup"), _target("dup"))},
        {"targets": (_target("dup"),), "improvements": (_improvement("dup"),)},
        {"improvements": (_improvement("dup"),), "guards": (_absolute("dup"),)},
        {"targets": (_target("dup"),), "guards": (_delta("dup"),)},
        {"guards": (_absolute("dup"), _delta("dup"))},
    ],
)
def test_criteria_reject_duplicate_condition_ids_across_collections(overrides):
    with pytest.raises(ValueError, match="dup"):
        _criteria(**overrides)


@pytest.mark.parametrize("value", [True, math.nan, math.inf, -math.inf, "1"])
def test_thresholds_reject_boolean_and_nonfinite_values(value):
    with pytest.raises(ValueError):
        TargetCondition(ConditionId("c"), _selector(), ComparisonOperator.LT, MeasuredScalar(value, "unit"))


def test_conditions_require_typed_fields_and_explicit_units():
    with pytest.raises(ValueError):
        MeasuredScalar(1.0, " ")
    with pytest.raises(ValueError):
        TargetCondition(ConditionId("c"), _selector(), "LT", MeasuredScalar(1.0, "unit"))
    with pytest.raises(ValueError):
        TargetCondition(ConditionId("c"), _selector(), ComparisonOperator.LT, 1.0)
    with pytest.raises(ValueError):
        TargetCondition("c", _selector(), ComparisonOperator.LT, MeasuredScalar(1.0, "unit"))
    with pytest.raises(ValueError):
        ImprovementCondition(ConditionId("c"), _selector(), "LOWER", MeasuredScalar(1.0, "unit"))
    with pytest.raises(ValueError):
        DeltaGuard(ConditionId("c"), _selector(), "ATTEMPT", MeasuredScalar(1.0, "unit"))
    with pytest.raises(ValueError):
        AbsoluteGuard(ConditionId("c"), "selector", ComparisonOperator.GT, MeasuredScalar(1.0, "unit"))


def test_metric_selector_requires_exact_metric_version_and_scope():
    with pytest.raises(ValueError):
        MetricSelector("metric-a@1", _selector().scope)
    with pytest.raises(ValueError):
        MetricSelector(VersionRef("metric-a", "1"), (TrackId("t1"),))


@pytest.mark.parametrize("delta", [0, 0.0, -0.5])
def test_improvement_minimum_delta_must_be_strictly_positive(delta):
    with pytest.raises(ValueError):
        _improvement(delta=delta)


def test_delta_guard_limit_may_be_zero_but_not_negative():
    assert _delta(limit=0).maximum_absolute_delta.value == 0
    with pytest.raises(ValueError):
        _delta(limit=-0.1)


# --- check results ---------------------------------------------------------------


@pytest.mark.parametrize("status", [PASS, FAIL])
def test_pass_and_fail_checks_require_evidence(status):
    assert _check("c", status).evidence_ids == (ObservationId("o1"),)
    with pytest.raises(ValueError):
        _check("c", status, evidence=())


@pytest.mark.parametrize("status", [UNAVAILABLE, NA])
def test_unavailable_and_not_applicable_checks_may_lack_evidence(status):
    assert _check("c", status).evidence_ids == ()
    assert _check("c", status, evidence=("o1",)).evidence_ids == (ObservationId("o1"),)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"explanation": ""},
        {"explanation": "  "},
        {"evidence_ids": (ObservationId("o1"), ObservationId("o1"))},
        {"evidence_ids": [ObservationId("o1")]},
        {"status": "PASS"},
        {"condition_id": "c"},
    ],
)
def test_check_result_rejects_invalid_fields(kwargs):
    fields = dict(condition_id=ConditionId("c"), status=PASS, explanation="ok", evidence_ids=(ObservationId("o1"),))
    fields.update(kwargs)
    with pytest.raises(ValueError):
        CheckResult(**fields)


# --- validation results ----------------------------------------------------------


def test_accepted_target_satisfaction_and_accepted_intermediate_improvement():
    criteria = _criteria()
    satisfied = _result(_attempt(), ATTEMPT, ACCEPTED, SATISFIED)
    intermediate = _result(_attempt(target=FAIL), ATTEMPT, ACCEPTED, UNMET)
    validate_validation_references(satisfied, criteria)
    validate_validation_references(intermediate, criteria)


def test_initial_assessment_defers_improvement_and_delta_guard_checks():
    criteria = _criteria()
    validate_validation_references(_result(_initial(), INITIAL, ACCEPTED, SATISFIED), criteria)
    validate_validation_references(_result(_initial(target=FAIL), INITIAL, REJECTED, UNMET), criteria)
    validate_validation_references(_result(_initial(target=UNAVAILABLE), INITIAL, INCONCLUSIVE, UNKNOWN), criteria)


@pytest.mark.parametrize(
    "statuses, mode, verdict, target_status",
    [
        (_attempt(target=UNAVAILABLE), ATTEMPT, ACCEPTED, UNKNOWN),
        (_attempt(delta_guard=UNAVAILABLE), ATTEMPT, ACCEPTED, SATISFIED),
        (_attempt(target=FAIL, improve=UNAVAILABLE), ATTEMPT, ACCEPTED, UNMET),
        (_initial(target=FAIL), INITIAL, ACCEPTED, UNMET),
        (_initial(target=FAIL), INITIAL, ACCEPTED, UNKNOWN),
        (_initial(abs_guard=FAIL), INITIAL, ACCEPTED, SATISFIED),
    ],
)
def test_result_rejects_intrinsically_contradictory_combinations(statuses, mode, verdict, target_status):
    with pytest.raises(ValueError):
        _result(statuses, mode, verdict, target_status)


def test_accepted_satisfied_attempt_may_have_failing_improvement_check():
    # Regression: improvement conditions only gate acceptance when targets are UNMET.
    result = _result(_attempt(improve=FAIL), ATTEMPT, ACCEPTED, SATISFIED)
    validate_validation_references(result, _criteria())


def test_valid_intermediate_improvement_remains_acceptable():
    result = _result(_attempt(target=FAIL), ATTEMPT, ACCEPTED, UNMET)
    validate_validation_references(result, _criteria())


@pytest.mark.parametrize("guard", ["abs-guard", "delta-guard"])
@pytest.mark.parametrize("target, target_status", [(PASS, SATISFIED), (FAIL, UNMET)])
def test_references_reject_accepted_attempt_with_failing_guard(guard, target, target_status):
    result = _result(_attempt(target=target, **{guard.replace("-", "_"): FAIL}), ATTEMPT, ACCEPTED, target_status)
    with pytest.raises(ValueError, match="ACCEPTED.*guard"):
        validate_validation_references(result, _criteria())


def test_references_reject_accepted_intermediate_improvement_with_failing_improvement():
    criteria = _criteria(improvements=(_improvement("improve"), _improvement("improve-2")))
    result = _result({**_attempt(target=FAIL), "improve-2": FAIL}, ATTEMPT, ACCEPTED, UNMET)
    with pytest.raises(ValueError, match="ACCEPTED.*improve-2"):
        validate_validation_references(result, criteria)


@pytest.mark.parametrize("condition", ["improve", "abs-guard", "delta-guard"])
@pytest.mark.parametrize("target, target_status", [(PASS, SATISFIED), (FAIL, UNMET)])
def test_accepted_result_still_rejects_unavailable_evidence(condition, target, target_status):
    statuses = {**_attempt(target=target), condition: UNAVAILABLE}
    with pytest.raises(ValueError, match="UNAVAILABLE"):
        _result(statuses, ATTEMPT, ACCEPTED, target_status)


@pytest.mark.parametrize(
    "overrides",
    [
        {"checks": ()},
        {"checks": [_check("target", PASS)]},
        {"checks": (_check("target", PASS), _check("target", FAIL))},
        {"checks": ("target",)},
        {"mode": "ATTEMPT"},
        {"verdict": "ACCEPTED"},
        {"target_status": None},
        {"criteria_version": " "},
        {"criteria_id": "crit"},
        {"validation_result_id": "v1"},
    ],
)
def test_result_rejects_invalid_fields_and_duplicate_checks(overrides):
    with pytest.raises(ValueError):
        _result(_attempt(), **{"mode": ATTEMPT, "verdict": REJECTED, "target_status": SATISFIED, **overrides})


def test_rejected_and_inconclusive_results_may_carry_any_check_status():
    _result(_attempt(abs_guard=FAIL), ATTEMPT, REJECTED, SATISFIED)
    _result(_attempt(delta_guard=UNAVAILABLE), ATTEMPT, INCONCLUSIVE, SATISFIED)


# --- reference validation --------------------------------------------------------


@pytest.mark.parametrize(
    "overrides, match",
    [({"criteria_id": CriteriaId("other")}, "criteria"), ({"criteria_version": "2"}, "criteria")],
)
def test_references_reject_criteria_identity_or_version_mismatch(overrides, match):
    result = _result(_attempt(), ATTEMPT, ACCEPTED, SATISFIED, **overrides)
    with pytest.raises(ValueError, match=match):
        validate_validation_references(result, _criteria())


def test_references_reject_missing_and_extra_checks():
    missing = {k: v for k, v in _attempt().items() if k != "delta-guard"}
    with pytest.raises(ValueError, match="delta-guard"):
        validate_validation_references(_result(missing, ATTEMPT, ACCEPTED, SATISFIED), _criteria())
    extra = {**_attempt(), "unknown": PASS}
    with pytest.raises(ValueError, match="unknown"):
        validate_validation_references(_result(extra, ATTEMPT, ACCEPTED, SATISFIED), _criteria())


@pytest.mark.parametrize(
    "statuses",
    [
        {**_initial(), "improve": PASS},
        {**_initial(), "delta-guard": UNAVAILABLE},
        {**_initial(), "target": NA},
        {**_initial(), "abs-guard": NA},
    ],
)
def test_references_enforce_initial_mode_applicability(statuses):
    result = _result(statuses, INITIAL, INCONCLUSIVE, UNKNOWN if statuses["target"] is NA else SATISFIED)
    with pytest.raises(ValueError, match="INITIAL"):
        validate_validation_references(result, _criteria())


@pytest.mark.parametrize("condition", ["target", "improve", "abs-guard", "delta-guard"])
def test_references_forbid_not_applicable_in_attempt_mode(condition):
    result = _result({**_attempt(), condition: NA}, ATTEMPT, INCONCLUSIVE, SATISFIED)
    with pytest.raises(ValueError, match="ATTEMPT"):
        validate_validation_references(result, _criteria())


def _two_targets() -> ValidationCriteria:
    return _criteria(targets=(_target("target"), _target("target-2")))


@pytest.mark.parametrize(
    "t1, t2, target_status, valid",
    [
        (PASS, PASS, SATISFIED, True),
        (PASS, FAIL, SATISFIED, False),
        (PASS, UNAVAILABLE, SATISFIED, False),
        (PASS, FAIL, UNMET, True),
        (FAIL, UNAVAILABLE, UNMET, True),
        (PASS, PASS, UNMET, False),
        (PASS, UNAVAILABLE, UNMET, False),
        (PASS, UNAVAILABLE, UNKNOWN, True),
        (UNAVAILABLE, UNAVAILABLE, UNKNOWN, True),
        (FAIL, UNAVAILABLE, UNKNOWN, False),
        (PASS, PASS, UNKNOWN, False),
    ],
)
def test_references_check_target_status_consistency(t1, t2, target_status, valid):
    result = _result({**_attempt(target=t1), "target-2": t2}, ATTEMPT, INCONCLUSIVE, target_status)
    if valid:
        validate_validation_references(result, _two_targets())
    else:
        with pytest.raises(ValueError, match="target"):
            validate_validation_references(result, _two_targets())


def test_nested_records_and_collections_are_immutable():
    criteria = _criteria()
    result = _result(_attempt(), ATTEMPT, ACCEPTED, SATISFIED)
    assert all(isinstance(c, tuple) for c in (criteria.targets, criteria.improvements, criteria.guards, result.checks))
    with pytest.raises(dataclasses.FrozenInstanceError):
        criteria.targets[0].threshold = MeasuredScalar(0.0, "unit-a")
    with pytest.raises(dataclasses.FrozenInstanceError):
        criteria.improvements[0].selector.metric = VersionRef("other", "1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.checks[0].status = FAIL
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.verdict = REJECTED


# --- dependency boundary ---------------------------------------------------------
