"""Contract tests for verification routing (plan docs/superpowers/plans/2026-11-06-verification-routing-plan.md).

Results are labelled metadata fixtures built to be structurally consistent with their criteria; they are
not evaluation data. A route names the coordinator's next obligation; routing itself performs no
transition, effect or budget charge, and the consistency validator does not authenticate a verdict.
"""

import copy

import pytest

import autonomous_mixing.core.loop.verification_routing as routing_module
from autonomous_mixing.core.loop.verification_routing import route_verification
from autonomous_mixing.core.records.identifiers import (
    ConditionId,
    CriteriaId,
    ObservationId,
    TrackId,
    VerificationResultId,
    VersionRef,
)
from autonomous_mixing.core.records.observation import AnalysisScope, MeasuredScalar, TimeRange
from autonomous_mixing.core.records.verification import (
    AbsoluteGuard,
    AcceptanceCriteria,
    AssessmentMode,
    CheckResult,
    CheckStatus,
    ComparisonOperator,
    ImprovementCondition,
    ImprovementDirection,
    MetricSelector,
    TargetCondition,
    TargetStatus,
    Verdict,
    VerificationResult,
)
from autonomous_mixing.core.records.verification_routing import VerificationRoute

SCOPE = AnalysisScope((TrackId("t1"),), TimeRange(0.0, 60.0))
UNIT = "unit-a"


def _selector(metric) -> MetricSelector:
    return MetricSelector(VersionRef(metric, "1"), SCOPE)


CRITERIA = AcceptanceCriteria(
    CriteriaId("criteria-a"),
    "1",
    (TargetCondition(ConditionId("tg"), _selector("tg"), ComparisonOperator.GE, MeasuredScalar(0.0, UNIT)),),
    (ImprovementCondition(ConditionId("im"), _selector("im"), ImprovementDirection.HIGHER, MeasuredScalar(1.0, UNIT)),),
    (AbsoluteGuard(ConditionId("gd"), _selector("gd"), ComparisonOperator.LE, MeasuredScalar(10.0, UNIT)),),
)

INITIAL, ATTEMPT = AssessmentMode.INITIAL, AssessmentMode.ATTEMPT
P, F, U, NA = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNAVAILABLE, CheckStatus.NOT_APPLICABLE
ACC, REJ, INC = Verdict.ACCEPTED, Verdict.REJECTED, Verdict.INCONCLUSIVE
SAT, UNMET, UNKNOWN = TargetStatus.SATISFIED, TargetStatus.UNMET, TargetStatus.UNKNOWN
R = VerificationRoute


def _check(cid, status) -> CheckResult:
    evidence = () if status in (U, NA) else (ObservationId(f"{cid}-obs"),)
    return CheckResult(ConditionId(cid), status, f"{cid} {status.value}", evidence)


def result(mode, tg, im, gd, verdict, target_status, *, criteria_id="criteria-a", version="1") -> VerificationResult:
    checks = (_check("tg", tg), _check("im", im), _check("gd", gd))
    return VerificationResult(
        VerificationResultId("vr-1"), CriteriaId(criteria_id), version, mode, checks, verdict, target_status
    )


# Independent table: (mode, target check, improvement check, guard check, verdict, target status) -> route.
ROUTE_TABLE = [
    (INITIAL, P, NA, P, ACC, SAT, R.FINISH_INITIAL_SUCCESS),
    (INITIAL, F, NA, P, REJ, UNMET, R.CHECK_BASELINE_READINESS),
    (INITIAL, P, NA, F, REJ, SAT, R.CHECK_BASELINE_READINESS),
    (INITIAL, U, NA, F, REJ, UNKNOWN, R.CHECK_BASELINE_READINESS),
    (INITIAL, U, NA, P, INC, UNKNOWN, R.FINISH_INITIAL_INCONCLUSIVE),
    (INITIAL, P, NA, U, INC, SAT, R.FINISH_INITIAL_INCONCLUSIVE),
    (ATTEMPT, P, P, P, ACC, SAT, R.COMMIT_THEN_FINISH),
    (ATTEMPT, P, F, P, ACC, SAT, R.COMMIT_THEN_FINISH),  # failing improvement with satisfied targets
    (ATTEMPT, F, P, P, ACC, UNMET, R.COMMIT_THEN_ITERATE),
    (ATTEMPT, F, F, P, REJ, UNMET, R.RESTORE_THEN_CONSIDER_ITERATION),
    (ATTEMPT, P, P, F, REJ, SAT, R.RESTORE_THEN_CONSIDER_ITERATION),
    (ATTEMPT, U, U, F, REJ, UNKNOWN, R.RESTORE_THEN_CONSIDER_ITERATION),
    (ATTEMPT, P, U, P, INC, SAT, R.RECONCILE_THEN_FINISH_INCONCLUSIVE),
    (ATTEMPT, F, U, P, INC, UNMET, R.RECONCILE_THEN_FINISH_INCONCLUSIVE),
    (ATTEMPT, U, F, P, INC, UNKNOWN, R.RECONCILE_THEN_FINISH_INCONCLUSIVE),
]


def test_route_members_and_values_are_exact():
    assert [(r.name, r.value) for r in VerificationRoute] == [
        ("FINISH_INITIAL_SUCCESS", "finish_initial_success"),
        ("CHECK_BASELINE_READINESS", "check_baseline_readiness"),
        ("FINISH_INITIAL_INCONCLUSIVE", "finish_initial_inconclusive"),
        ("COMMIT_THEN_FINISH", "commit_then_finish"),
        ("COMMIT_THEN_ITERATE", "commit_then_iterate"),
        ("RESTORE_THEN_CONSIDER_ITERATION", "restore_then_consider_iteration"),
        ("RECONCILE_THEN_FINISH_INCONCLUSIVE", "reconcile_then_finish_inconclusive"),
    ]


@pytest.mark.parametrize("mode, tg, im, gd, verdict, target_status, route", ROUTE_TABLE)
def test_route_table(mode, tg, im, gd, verdict, target_status, route):
    assert route_verification(result(mode, tg, im, gd, verdict, target_status), CRITERIA) is route


def test_every_route_is_reachable():
    assert {row[-1] for row in ROUTE_TABLE} == set(VerificationRoute)


@pytest.mark.parametrize(
    "mode, rejected, inconclusive",
    [
        # Same mode and target status, different verdict: the verdict alone separates the routes.
        (INITIAL, (P, NA, F, REJ, SAT), (P, NA, U, INC, SAT)),
        (INITIAL, (U, NA, F, REJ, UNKNOWN), (U, NA, P, INC, UNKNOWN)),
        (ATTEMPT, (P, P, F, REJ, SAT), (P, U, P, INC, SAT)),
        (ATTEMPT, (F, F, P, REJ, UNMET), (F, U, P, INC, UNMET)),
        (ATTEMPT, (U, U, F, REJ, UNKNOWN), (U, F, P, INC, UNKNOWN)),
    ],
)
def test_rejection_and_inconclusiveness_route_differently_with_equal_target_status(mode, rejected, inconclusive):
    rejected_route = route_verification(result(mode, *rejected), CRITERIA)
    inconclusive_route = route_verification(result(mode, *inconclusive), CRITERIA)
    expected = (
        (R.CHECK_BASELINE_READINESS, R.FINISH_INITIAL_INCONCLUSIVE)
        if mode is INITIAL
        else (R.RESTORE_THEN_CONSIDER_ITERATION, R.RECONCILE_THEN_FINISH_INCONCLUSIVE)
    )
    assert (rejected_route, inconclusive_route) == expected


@pytest.mark.parametrize("value", [None, "result", CRITERIA])
def test_result_type_is_checked(value):
    with pytest.raises(ValueError):
        route_verification(value, CRITERIA)


@pytest.mark.parametrize("value", [None, "criteria", result(ATTEMPT, P, P, P, ACC, SAT)])
def test_criteria_type_is_checked(value):
    with pytest.raises(ValueError):
        route_verification(result(ATTEMPT, P, P, P, ACC, SAT), value)


@pytest.mark.parametrize("overrides", [{"criteria_id": "criteria-b"}, {"version": "2"}])
def test_criteria_reference_mismatch_is_rejected(overrides):
    with pytest.raises(ValueError, match="criteria"):
        route_verification(result(ATTEMPT, P, P, P, ACC, SAT, **overrides), CRITERIA)


def test_checks_not_matching_the_criteria_are_rejected():
    checks = (_check("tg", P), _check("im", P), _check("other", P))
    mismatched = VerificationResult(VerificationResultId("vr-1"), CRITERIA.criteria_id, "1", ATTEMPT, checks, ACC, SAT)
    with pytest.raises(ValueError, match="other"):
        route_verification(mismatched, CRITERIA)


@pytest.mark.parametrize(
    "inconsistent",
    [
        (ATTEMPT, F, F, P, ACC, UNMET),  # accepted unmet targets with a failing improvement
        (ATTEMPT, P, P, F, ACC, SAT),  # accepted with a failing guard
        (ATTEMPT, P, NA, P, REJ, SAT),  # NOT_APPLICABLE in ATTEMPT mode
        (INITIAL, P, P, P, INC, SAT),  # INITIAL improvement not deferred
        (ATTEMPT, F, P, P, REJ, SAT),  # target status contradicts the target check
    ],
)
def test_structurally_inconsistent_results_are_not_routed(inconsistent):
    with pytest.raises(ValueError):
        route_verification(result(*inconsistent), CRITERIA)


def test_routing_runs_the_consistency_validator_first(monkeypatch):
    calls = []

    def failing(result_, criteria_):
        calls.append((result_, criteria_))
        raise ValueError("inconsistent")

    monkeypatch.setattr(routing_module, "validate_verification_references", failing)
    accepted = result(ATTEMPT, P, P, P, ACC, SAT)
    with pytest.raises(ValueError, match="inconsistent"):
        route_verification(accepted, CRITERIA)
    assert calls == [(accepted, CRITERIA)]


def test_result_and_criteria_remain_unchanged():
    routed = result(ATTEMPT, F, P, P, ACC, UNMET)
    result_before, criteria_before = copy.deepcopy(routed), copy.deepcopy(CRITERIA)
    checks = routed.checks
    assert route_verification(routed, CRITERIA) is R.COMMIT_THEN_ITERATE
    assert routed == result_before and CRITERIA == criteria_before
    assert routed.checks is checks
