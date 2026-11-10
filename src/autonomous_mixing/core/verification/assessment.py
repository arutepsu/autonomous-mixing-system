"""Verification verdicts: compute every configured check and the INITIAL or ATTEMPT verdict.

Pure and deterministic; it reads only the supplied request. Each condition is matched with its bound
measurement definition through ``match_evidence``; numeric checks use the stored scalars exactly (no
rounding, epsilon, clipping, rescaling or conversion). The result is returned for a future loop: no
state is selected, restored or published as accepted here.
"""

import math
import operator
from typing import assert_never

from autonomous_mixing.core.records.identifiers import ObservationId
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.observation import Observation
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
    TargetCondition,
    TargetStatus,
    Verdict,
    VerificationResult,
)
from autonomous_mixing.core.records.verification_assessment import VerificationAssessment
from autonomous_mixing.core.records.verification_evidence import (
    EvidenceUnavailable,
    MatchedEvidence,
    MeasurementDefinition,
)
from autonomous_mixing.core.verification.consistency import validate_verification_references
from autonomous_mixing.core.verification.evidence import match_evidence

_OPERATIONS = {
    ComparisonOperator.LT: operator.lt,
    ComparisonOperator.LE: operator.le,
    ComparisonOperator.GT: operator.gt,
    ComparisonOperator.GE: operator.ge,
}


def assess_verification(request: VerificationAssessment) -> VerificationResult:
    """Return the verification result for ``request``: one check per condition in configuration order.

    Every check is computed even when an earlier one decides the verdict. Target status: any target FAIL
    is UNMET, otherwise any target UNAVAILABLE is UNKNOWN, otherwise SATISFIED. INITIAL: improvements and
    delta guards are NOT_APPLICABLE; any applicable FAIL rejects, otherwise any UNAVAILABLE is
    inconclusive. ATTEMPT, first rule wins: a guard FAIL rejects; UNMET targets with an improvement FAIL
    reject; any UNAVAILABLE is inconclusive; otherwise accepted (including SATISFIED targets with an
    available failing improvement and UNMET targets whose improvements and guards all pass).
    """
    require_instance(request, VerificationAssessment, "request")
    criteria = request.criteria
    definitions = {m.condition_id: m.definition for m in request.measurements}

    checks = []
    for condition in (*criteria.targets, *criteria.improvements, *criteria.guards):
        checks.append(_check(condition, definitions[condition.condition_id], request))
    by_id = {check.condition_id: check.status for check in checks}

    target_statuses = [by_id[t.condition_id] for t in criteria.targets]
    if CheckStatus.FAIL in target_statuses:
        target_status = TargetStatus.UNMET
    elif CheckStatus.UNAVAILABLE in target_statuses:
        target_status = TargetStatus.UNKNOWN
    else:
        target_status = TargetStatus.SATISFIED

    statuses = set(by_id.values())
    match request.mode:
        case AssessmentMode.INITIAL:
            if CheckStatus.FAIL in statuses:
                verdict = Verdict.REJECTED
            elif CheckStatus.UNAVAILABLE in statuses:
                verdict = Verdict.INCONCLUSIVE
            else:
                verdict = Verdict.ACCEPTED
        case AssessmentMode.ATTEMPT:
            guard_failed = any(by_id[g.condition_id] is CheckStatus.FAIL for g in criteria.guards)
            improvement_failed = any(by_id[i.condition_id] is CheckStatus.FAIL for i in criteria.improvements)
            if guard_failed or (target_status is TargetStatus.UNMET and improvement_failed):
                verdict = Verdict.REJECTED
            elif CheckStatus.UNAVAILABLE in statuses:
                verdict = Verdict.INCONCLUSIVE
            else:
                verdict = Verdict.ACCEPTED
        case _:
            assert_never(request.mode)

    result = VerificationResult(
        request.result_id, criteria.criteria_id, criteria.version, request.mode, tuple(checks), verdict, target_status
    )
    validate_verification_references(result, criteria)
    return result


def _check(condition, definition: MeasurementDefinition, request: VerificationAssessment) -> CheckResult:
    cid = condition.condition_id
    deferred = isinstance(condition, (ImprovementCondition, DeltaGuard))
    if request.mode is AssessmentMode.INITIAL and deferred:
        return CheckResult(
            cid, CheckStatus.NOT_APPLICABLE, "comparison deferred: INITIAL assessment has no baseline", ()
        )

    assessed = match_evidence(definition, request.assessed)
    if isinstance(condition, (TargetCondition, AbsoluteGuard)):
        if isinstance(assessed, EvidenceUnavailable):
            return CheckResult(cid, CheckStatus.UNAVAILABLE, _unavailable("assessed", assessed), ())
        x = assessed.observation.result.value
        t = condition.threshold
        passed = _OPERATIONS[condition.operator](x, t.value)
        explanation = f"assessed {x!r} {condition.operator.value} threshold {t.value!r} {t.unit}"
        return _numeric(cid, passed, explanation, (assessed.observation,))

    if isinstance(condition, ImprovementCondition):
        role, bundle = "before", request.before
    elif condition.baseline is BaselineReference.ATTEMPT:
        role, bundle = "before", request.before
    else:
        role, bundle = "initial", request.initial
    if bundle is None:  # excluded by VerificationAssessment for ATTEMPT
        raise ValueError(f"no {role} evidence for {cid}")
    baseline = match_evidence(definition, bundle)

    if isinstance(assessed, EvidenceUnavailable) or isinstance(baseline, EvidenceUnavailable):
        reasons = [
            _unavailable(name, match)
            for name, match in (("assessed", assessed), (role, baseline))
            if isinstance(match, EvidenceUnavailable)
        ]
        matched = tuple(m.observation for m in (assessed, baseline) if isinstance(m, MatchedEvidence))
        return CheckResult(cid, CheckStatus.UNAVAILABLE, "; ".join(reasons), _ids(matched))

    x = assessed.observation.result.value
    b = baseline.observation.result.value
    operands = (assessed.observation, baseline.observation)
    if isinstance(condition, ImprovementCondition):
        lower = condition.direction is ImprovementDirection.LOWER
        expression = f"{role} {b!r} - assessed {x!r}" if lower else f"assessed {x!r} - {role} {b!r}"
        change = _difference(b, x) if lower else _difference(x, b)
        if change is None:
            return _unrepresentable(cid, expression, operands)
        m = condition.minimum_delta
        explanation = f"improvement {expression} = {change!r} >= minimum {m.value!r} {m.unit}"
        return _numeric(cid, change >= m.value, explanation, operands)
    if isinstance(condition, DeltaGuard):
        expression = f"|assessed {x!r} - {role} {b!r}|"
        change = _difference(x, b)
        if change is None:
            return _unrepresentable(cid, expression, operands)
        d = condition.maximum_absolute_delta
        explanation = f"delta {expression} = {abs(change)!r} <= maximum {d.value!r} {d.unit}"
        return _numeric(cid, abs(change) <= d.value, explanation, operands)
    assert_never(condition)


def _difference(minuend: float, subtrahend: float) -> float | None:
    """Exact stored-scalar difference, or ``None`` if it overflows or is not finitely representable."""
    try:
        difference = minuend - subtrahend
        finite = math.isfinite(difference)
    except OverflowError:
        return None
    return difference if finite else None


def _numeric(cid, passed: bool, explanation: str, operands: tuple[Observation, ...]) -> CheckResult:
    status = CheckStatus.PASS if passed else CheckStatus.FAIL
    return CheckResult(cid, status, f"{explanation}: {status.value}", _ids(operands))


def _unrepresentable(cid, expression: str, operands: tuple[Observation, ...]) -> CheckResult:
    return CheckResult(
        cid, CheckStatus.UNAVAILABLE, f"difference {expression} is not finitely representable", _ids(operands)
    )


def _unavailable(role: str, match: EvidenceUnavailable) -> str:
    return f"{role} evidence unavailable ({match.code.value}): {match.explanation}"


def _ids(observations: tuple[Observation, ...]) -> tuple[ObservationId, ...]:
    """Observation IDs in operand order (assessed, then baseline), each identical ID once."""
    return tuple(dict.fromkeys(o.observation_id for o in observations))
