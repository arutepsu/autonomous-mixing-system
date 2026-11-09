"""Contract tests for verification verdicts (plan docs/superpowers/plans/2026-11-06-verification-verdict-plan.md).

All records are labelled metadata fixtures: metric names, units, thresholds and values are structural
examples chosen to sit on or beside an exact comparison boundary. They are not evaluation data and not
musical defaults. State selection, rollback and acceptance belong to the future loop and are not tested here.
"""

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

import autonomous_mixing.core.verification.assessment as assessment_module
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    CheckpointId,
    ConditionId,
    CriteriaId,
    ObservationId,
    ProjectId,
    Revision,
    TrackId,
    VerificationResultId,
    VersionRef,
)
from autonomous_mixing.core.records.observation import (
    AnalysisScope,
    MeasuredScalar,
    Observation,
    TimeRange,
    Unavailable,
)
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)
from autonomous_mixing.core.records.verification import (
    AbsoluteGuard,
    AcceptanceCriteria,
    AssessmentMode,
    BaselineReference,
    CheckStatus,
    ComparisonOperator,
    DeltaGuard,
    ImprovementCondition,
    ImprovementDirection,
    MetricSelector,
    TargetCondition,
    TargetStatus,
    Verdict,
    VerificationResult,
)
from autonomous_mixing.core.records.verification_assessment import (
    ConditionMeasurement,
    VerificationAssessment,
    validate_condition_measurements,
)
from autonomous_mixing.core.records.verification_evidence import (
    MeasurementDefinition,
    SourceRequirement,
    StateEvidence,
)
from autonomous_mixing.core.verification.assessment import assess_verification

UTC_NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
SPEC = VersionRef("analysis-spec", "1")
ANALYZER = VersionRef("analyzer", "0.1")
ALIGNMENT = VersionRef("declared-alignment", "1")  # a caller declaration; physical alignment is not checked
RENDER = VersionRef("render", "1")
RATE, SECONDS = 48000, 60
T1 = TrackId("t1")
SCOPE = AnalysisScope((T1,), TimeRange(0.0, 60.0))
UNIT = "unit-a"

ATTEMPT, INITIAL = AssessmentMode.ATTEMPT, AssessmentMode.INITIAL
PASS, FAIL = CheckStatus.PASS, CheckStatus.FAIL
UNAVAILABLE, NA = CheckStatus.UNAVAILABLE, CheckStatus.NOT_APPLICABLE
LT, LE, GT, GE = ComparisonOperator.LT, ComparisonOperator.LE, ComparisonOperator.GT, ComparisonOperator.GE
LOWER, HIGHER = ImprovementDirection.LOWER, ImprovementDirection.HIGHER


# --- state fixtures ----------------------------------------------------------------------------------


def _artifact(aid, revision, project="p1") -> AudioArtifact:
    return AudioArtifact(
        ArtifactId(aid), ProjectId(project), Revision(revision), (T1,), ArtifactKind.STEM, f"outputs/{aid}.wav",
        0.0, RATE, 2, RATE * SECONDS, "d" * 64, RENDER,
    )  # fmt: skip


def _state(revision, member, *, project="p1", captured_at=UTC_NOW) -> ProjectState:
    snapshot = ProjectSnapshot(ProjectId(project), Revision(revision), captured_at, (Track(T1, "track 1"),))
    gains = (TrackGainState(T1, _artifact("in-t1", "r0", project), 0.0),)
    return ProjectState(
        snapshot,
        ProjectProcessingState(ProjectId(project), Revision(revision), gains),
        ArtifactCheckpoint(CheckpointId(f"cp-{revision}"), ProjectId(project), Revision(revision), (member,)),
    )


S1 = _artifact("s1", "r1")
INITIAL_STATE = _state("r1", S1)
BEFORE_STATE = _state("r2", S1)  # retains s1 produced at r1
CANDIDATE_STATE = _state("r3", _artifact("s1-r3", "r3"))  # fresh source ID for the same subject
CANDIDATE_RETAINING = _state("r3", S1)  # a candidate that retains s1 unchanged


def _observation(metric, value, oid, source) -> Observation:
    result = value if isinstance(value, Unavailable) else MeasuredScalar(value, UNIT)
    return Observation(ObservationId(oid), VersionRef(metric, "1"), (source,), SCOPE, result, SPEC, ANALYZER, UTC_NOW)


def _bundle(state, values=None, *extra) -> StateEvidence:
    """One observation per metric in ``values`` (``None`` values omitted), IDs ``<metric>@<revision>``."""
    source = state.audio.artifacts[0].artifact_id
    observations = [
        _observation(metric, value, f"{metric}@{state.revision}", source)
        for metric, value in (values or {}).items()
        if value is not None
    ]
    return StateEvidence(state, (*observations, *extra), ALIGNMENT)


def _oid(metric, revision) -> ObservationId:
    return ObservationId(f"{metric}@{revision}")


# --- criteria fixtures -------------------------------------------------------------------------------


def _selector(metric) -> MetricSelector:
    return MetricSelector(VersionRef(metric, "1"), SCOPE)


def _scalar(value, unit=UNIT) -> MeasuredScalar:
    return MeasuredScalar(value, unit)


def target(cid, op=GE, threshold=0.0, *, metric=None, unit=UNIT) -> TargetCondition:
    return TargetCondition(ConditionId(cid), _selector(metric or cid), op, _scalar(threshold, unit))


def improvement(cid, direction=HIGHER, minimum=1.0, *, metric=None, unit=UNIT) -> ImprovementCondition:
    return ImprovementCondition(ConditionId(cid), _selector(metric or cid), direction, _scalar(minimum, unit))


def absolute(cid, op=LE, threshold=10.0, *, metric=None, unit=UNIT) -> AbsoluteGuard:
    return AbsoluteGuard(ConditionId(cid), _selector(metric or cid), op, _scalar(threshold, unit))


def delta(cid, baseline=BaselineReference.ATTEMPT, maximum=1.0, *, metric=None, unit=UNIT) -> DeltaGuard:
    return DeltaGuard(ConditionId(cid), _selector(metric or cid), baseline, _scalar(maximum, unit))


def criteria(targets, improvements, guards=()) -> AcceptanceCriteria:
    return AcceptanceCriteria(CriteriaId("criteria-a"), "1", tuple(targets), tuple(improvements), tuple(guards))


def _definition(selector, unit=UNIT) -> MeasurementDefinition:
    source = SourceRequirement(ArtifactKind.STEM, (T1,), RENDER, RATE, 2, 0.0, RATE * SECONDS)
    return MeasurementDefinition(selector, unit, SPEC, ANALYZER, ALIGNMENT, (source,))


def bindings(acceptance) -> tuple[ConditionMeasurement, ...]:
    conditions = (*acceptance.targets, *acceptance.improvements, *acceptance.guards)
    return tuple(ConditionMeasurement(c.condition_id, _definition(c.selector)) for c in conditions)


def request(acceptance, mode, assessed, before=None, initial=None, *, measurements=None) -> VerificationAssessment:
    return VerificationAssessment(
        VerificationResultId("vr-1"),
        mode,
        acceptance,
        bindings(acceptance) if measurements is None else measurements,
        assessed,
        before,
        initial,
    )


def _checks(result) -> dict[str, object]:
    return {str(check.condition_id): check for check in result.checks}


SIMPLE = criteria([target("tg")], [improvement("im")])
SIMPLE_VALUES = {"tg": 1.0, "im": 0.0}


# --- record shape and type validation ----------------------------------------------------------------


@pytest.mark.parametrize(
    "record, fields",
    [
        (ConditionMeasurement, ["condition_id", "definition"]),
        (VerificationAssessment, ["result_id", "mode", "criteria", "measurements", "assessed", "before", "initial"]),
    ],
)
def test_record_fields_are_exact_and_required(record, fields):
    assert [f.name for f in dataclasses.fields(record)] == fields
    assert all(f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
               for f in dataclasses.fields(record))  # fmt: skip
    assert record.__dataclass_params__.frozen


def test_condition_measurement_validates_types():
    definition = _definition(_selector("tg"))
    with pytest.raises(ValueError):
        ConditionMeasurement("tg", definition)
    with pytest.raises(ValueError):
        ConditionMeasurement(ConditionId("tg"), "definition")


@pytest.mark.parametrize(
    "field, value",
    [
        ("result_id", "vr-1"),
        ("mode", "attempt"),
        ("criteria", None),
        ("measurements", []),
        ("assessed", None),
        ("before", "before"),
        ("initial", "initial"),
    ],
)
def test_assessment_validates_field_types(field, value):
    base = request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE), _bundle(BEFORE_STATE))
    with pytest.raises(ValueError):
        dataclasses.replace(base, **{field: value})


def test_assessment_rejects_a_non_measurement_element():
    with pytest.raises(ValueError):
        request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE), _bundle(BEFORE_STATE),
                measurements=(*bindings(SIMPLE)[:1], "im"))  # fmt: skip


# --- binding coverage, selectors and units -----------------------------------------------------------


def test_duplicate_measurement_binding_is_rejected():
    measurements = bindings(SIMPLE)
    with pytest.raises(ValueError, match="duplicate"):
        request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE), _bundle(BEFORE_STATE),
                measurements=(*measurements, measurements[0]))  # fmt: skip


def test_missing_measurement_binding_is_rejected():
    with pytest.raises(ValueError, match="im"):
        request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE), _bundle(BEFORE_STATE), measurements=bindings(SIMPLE)[:1])


def test_extra_measurement_binding_is_rejected():
    extra = ConditionMeasurement(ConditionId("other"), _definition(_selector("other")))
    with pytest.raises(ValueError, match="other"):
        request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE), _bundle(BEFORE_STATE),
                measurements=(*bindings(SIMPLE), extra))  # fmt: skip


@pytest.mark.parametrize(
    "selector",
    [
        MetricSelector(VersionRef("tg", "2"), SCOPE),  # metric version
        MetricSelector(VersionRef("im", "1"), SCOPE),  # a different metric
        MetricSelector(VersionRef("tg", "1"), AnalysisScope((T1,), TimeRange(0.0, 30.0))),  # scope
    ],
)
def test_definition_selector_must_equal_the_condition_selector(selector):
    measurements = (ConditionMeasurement(ConditionId("tg"), _definition(selector)), bindings(SIMPLE)[1])
    with pytest.raises(ValueError, match="selector"):
        request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE), _bundle(BEFORE_STATE), measurements=measurements)


@pytest.mark.parametrize(
    "condition",
    [
        target("c", unit="unit-b"),
        improvement("c", unit="unit-b"),
        absolute("c", unit="unit-b"),
        delta("c", unit="unit-b"),
    ],
    ids=["threshold", "minimum_delta", "absolute-threshold", "maximum_absolute_delta"],
)
@pytest.mark.parametrize("mode", [ATTEMPT, INITIAL])
def test_definition_unit_must_equal_the_condition_unit_even_when_deferred(condition, mode):
    targets = [condition] if isinstance(condition, TargetCondition) else [target("tg")]
    improvements = [condition] if isinstance(condition, ImprovementCondition) else [improvement("im")]
    guards = [condition] if isinstance(condition, (AbsoluteGuard, DeltaGuard)) else []
    acceptance = criteria(targets, improvements, guards)
    measurements = tuple(
        ConditionMeasurement(m.condition_id, _definition(m.definition.selector, UNIT)) for m in bindings(acceptance)
    )
    before = _bundle(BEFORE_STATE) if mode is ATTEMPT else None
    assessed = _bundle(CANDIDATE_STATE if mode is ATTEMPT else INITIAL_STATE)
    with pytest.raises(ValueError, match="unit"):
        request(acceptance, mode, assessed, before, measurements=measurements)


def test_matching_definition_units_are_accepted():
    acceptance = criteria([target("tg")], [improvement("im")], [absolute("ab"), delta("dl")])
    assert request(acceptance, ATTEMPT, _bundle(CANDIDATE_STATE), _bundle(BEFORE_STATE)).criteria is acceptance


# --- mode and baseline structure ---------------------------------------------------------------------


@pytest.mark.parametrize("before, initial", [(True, False), (False, True), (True, True)])
def test_initial_mode_requires_no_baselines(before, initial):
    with pytest.raises(ValueError, match="INITIAL"):
        request(SIMPLE, INITIAL, _bundle(INITIAL_STATE), _bundle(BEFORE_STATE) if before else None,
                _bundle(BEFORE_STATE) if initial else None)  # fmt: skip


def test_attempt_mode_requires_before():
    with pytest.raises(ValueError, match="before"):
        request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE))


def test_before_must_differ_in_revision_from_the_candidate():
    with pytest.raises(ValueError, match="revision"):
        request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE), _bundle(CANDIDATE_STATE))


def test_initial_delta_guard_requires_initial_in_attempt_mode():
    acceptance = criteria([target("tg")], [improvement("im")], [delta("dl", BaselineReference.INITIAL)])
    with pytest.raises(ValueError, match="initial"):
        request(acceptance, ATTEMPT, _bundle(CANDIDATE_STATE), _bundle(BEFORE_STATE))
    assert request(acceptance, ATTEMPT, _bundle(CANDIDATE_STATE), _bundle(BEFORE_STATE), _bundle(INITIAL_STATE))


def test_initial_delta_guard_needs_no_baseline_in_initial_mode():
    acceptance = criteria([target("tg")], [improvement("im")], [delta("dl", BaselineReference.INITIAL)])
    assert request(acceptance, INITIAL, _bundle(INITIAL_STATE)).initial is None


def test_initial_must_differ_in_revision_from_the_candidate():
    with pytest.raises(ValueError, match="revision"):
        request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE), _bundle(BEFORE_STATE), _bundle(CANDIDATE_STATE))


def test_initial_may_equal_before():
    assert request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE), _bundle(BEFORE_STATE), _bundle(BEFORE_STATE))


def test_optional_initial_is_accepted_without_an_initial_delta_guard():
    built = request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE), _bundle(BEFORE_STATE), _bundle(INITIAL_STATE))
    assert built.initial.state is INITIAL_STATE


@pytest.mark.parametrize("role", ["before", "initial"])
def test_bundles_must_share_one_project(role):
    other = _bundle(_state("r9", _artifact("s1", "r1", "p2"), project="p2"))
    before = other if role == "before" else _bundle(BEFORE_STATE)
    initial = other if role == "initial" else _bundle(INITIAL_STATE)
    with pytest.raises(ValueError, match="project"):
        request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE), before, initial)


def test_bundles_with_one_revision_must_carry_equal_states():
    conflicting = _state("r2", S1, captured_at=UTC_NOW + timedelta(seconds=1))
    with pytest.raises(ValueError, match="r2"):
        request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE), _bundle(BEFORE_STATE), _bundle(conflicting))


def test_bundles_with_one_revision_may_carry_different_observations():
    before = _bundle(BEFORE_STATE, {"tg": 0.0})
    initial = _bundle(_state("r2", S1), {"im": 0.0})  # equal state record, built separately
    assert request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE), before, initial)


def test_repeated_observation_id_with_different_contents_is_rejected():
    candidate = _bundle(CANDIDATE_RETAINING, None, _observation("tg", 1.0, "shared", S1.artifact_id))
    before = _bundle(BEFORE_STATE, None, _observation("tg", 2.0, "shared", S1.artifact_id))
    with pytest.raises(ValueError, match="shared"):
        request(SIMPLE, ATTEMPT, candidate, before)


def test_repeated_observation_id_across_before_and_initial_is_checked():
    before = _bundle(BEFORE_STATE, None, _observation("tg", 1.0, "shared", S1.artifact_id))
    initial = _bundle(INITIAL_STATE, None, _observation("tg", 2.0, "shared", S1.artifact_id))
    with pytest.raises(ValueError, match="shared"):
        request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE), before, initial)


def test_identical_observation_reuse_is_accepted():
    shared = _observation("tg", 1.0, "shared", S1.artifact_id)
    assert request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_RETAINING, None, shared), _bundle(BEFORE_STATE, None, shared))


# --- result shape, order and wiring ------------------------------------------------------------------


def test_result_carries_the_supplied_identity_and_configuration_order():
    acceptance = criteria([target("t-b"), target("t-a")], [improvement("i-b"), improvement("i-a")],
                          [delta("g-b"), absolute("g-a")])  # fmt: skip
    values = {"t-b": 1.0, "t-a": 1.0, "i-b": 2.0, "i-a": 2.0, "g-b": 0.0, "g-a": 0.0}
    reversed_bindings = tuple(reversed(bindings(acceptance)))
    result = assess_verification(
        request(acceptance, ATTEMPT, _bundle(CANDIDATE_STATE, values),
                _bundle(BEFORE_STATE, {**values, "i-b": 0.0, "i-a": 0.0}), measurements=reversed_bindings)
    )  # fmt: skip
    assert isinstance(result, VerificationResult)
    assert result.validation_result_id == VerificationResultId("vr-1")
    assert (result.criteria_id, result.criteria_version, result.mode) == (CriteriaId("criteria-a"), "1", ATTEMPT)
    assert [str(c.condition_id) for c in result.checks] == ["t-b", "t-a", "i-b", "i-a", "g-b", "g-a"]
    assert {c.status for c in result.checks} == {PASS}


def test_argument_type_is_checked():
    with pytest.raises(ValueError):
        assess_verification("request")


def test_result_is_validated_against_the_criteria(monkeypatch):
    calls = []
    monkeypatch.setattr(assessment_module, "validate_verification_references",
                        lambda result, acceptance: calls.append((result, acceptance)))  # fmt: skip
    result = assess_verification(request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE, SIMPLE_VALUES),
                                         _bundle(BEFORE_STATE, SIMPLE_VALUES)))  # fmt: skip
    assert calls == [(result, SIMPLE)]


@pytest.mark.parametrize("error", [RuntimeError("boom"), ValueError("boom")])
def test_unexpected_matcher_errors_propagate(monkeypatch, error):
    def raising(definition, evidence):
        raise error

    monkeypatch.setattr(assessment_module, "match_evidence", raising)
    with pytest.raises(type(error), match="boom"):
        assess_verification(request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE), _bundle(BEFORE_STATE)))


# --- targets and absolute guards: exact operators and boundaries -------------------------------------


OPERATOR_CASES = [
    (LT, -1.0, PASS), (LT, 0.0, FAIL), (LT, 1.0, FAIL),
    (LE, -1.0, PASS), (LE, 0.0, PASS), (LE, 1.0, FAIL),
    (GT, -1.0, FAIL), (GT, 0.0, FAIL), (GT, 1.0, PASS),
    (GE, -1.0, FAIL), (GE, 0.0, PASS), (GE, 1.0, PASS),
]  # fmt: skip


@pytest.mark.parametrize("op, value, expected", OPERATOR_CASES)
@pytest.mark.parametrize("kind", ["target", "absolute"])
def test_threshold_operators_are_exact(kind, op, value, expected):
    condition = target("c", op, 0.0) if kind == "target" else absolute("c", op, 0.0)
    acceptance = (criteria([condition], [improvement("im")]) if kind == "target"
                  else criteria([target("tg")], [improvement("im")], [condition]))  # fmt: skip
    values = {"c": value, "tg": 1.0, "im": 2.0}
    result = assess_verification(request(acceptance, ATTEMPT, _bundle(CANDIDATE_STATE, values),
                                         _bundle(BEFORE_STATE, {"im": 0.0})))  # fmt: skip
    check = _checks(result)["c"]
    assert check.status is expected
    assert check.evidence_ids == (_oid("c", "r3"),)
    assert repr(value) in check.explanation and op.value in check.explanation


def test_threshold_comparison_uses_stored_values_without_tolerance():
    # 0.1 + 0.2 is stored as 0.30000000000000004, which is not <= 0.3.
    acceptance = criteria([target("c", LE, 0.3)], [improvement("im")])
    result = assess_verification(request(acceptance, ATTEMPT, _bundle(CANDIDATE_STATE, {"c": 0.1 + 0.2, "im": 1.0}),
                                         _bundle(BEFORE_STATE, {"im": 0.0})))  # fmt: skip
    assert _checks(result)["c"].status is FAIL


# --- improvements: both directions and exact minimum -------------------------------------------------


@pytest.mark.parametrize(
    "direction, before, after, expected",
    [
        (LOWER, 5.0, 3.0, PASS),  # before - after == minimum
        (LOWER, 5.0, 2.0, PASS),
        (LOWER, 5.0, 3.5, FAIL),  # progress below the minimum
        (LOWER, 5.0, 5.0, FAIL),  # no change
        (LOWER, 5.0, 7.0, FAIL),  # moved the wrong way
        (HIGHER, 3.0, 5.0, PASS),  # after - before == minimum
        (HIGHER, 3.0, 6.0, PASS),
        (HIGHER, 3.0, 4.5, FAIL),
        (HIGHER, 3.0, 1.0, FAIL),
    ],
)
def test_improvement_direction_and_minimum_are_exact(direction, before, after, expected):
    acceptance = criteria([target("tg")], [improvement("im", direction, 2.0)])
    result = assess_verification(request(acceptance, ATTEMPT, _bundle(CANDIDATE_STATE, {"tg": 1.0, "im": after}),
                                         _bundle(BEFORE_STATE, {"im": before})))  # fmt: skip
    check = _checks(result)["im"]
    assert check.status is expected
    assert check.evidence_ids == (_oid("im", "r3"), _oid("im", "r2"))  # assessed first, then baseline
    assert repr(before) in check.explanation and repr(after) in check.explanation


def test_improvement_uses_stored_arithmetic_without_epsilon():
    # 0.3 - 0.1 is 0.19999999999999998, which is below a minimum of 0.2.
    acceptance = criteria([target("tg")], [improvement("im", LOWER, 0.2)])
    result = assess_verification(request(acceptance, ATTEMPT, _bundle(CANDIDATE_STATE, {"tg": 1.0, "im": 0.1}),
                                         _bundle(BEFORE_STATE, {"im": 0.3})))  # fmt: skip
    assert _checks(result)["im"].status is FAIL


def test_improvement_is_compared_with_before_not_initial():
    acceptance = criteria([target("tg")], [improvement("im", HIGHER, 2.0)])
    result = assess_verification(request(acceptance, ATTEMPT, _bundle(CANDIDATE_STATE, {"tg": 1.0, "im": 5.0}),
                                         _bundle(BEFORE_STATE, {"im": 4.0}), _bundle(INITIAL_STATE, {"im": 0.0})))  # fmt: skip
    check = _checks(result)["im"]
    assert check.status is FAIL
    assert check.evidence_ids == (_oid("im", "r3"), _oid("im", "r2"))


# --- delta guards: magnitude in both directions and baseline selection -------------------------------


@pytest.mark.parametrize(
    "baseline_value, assessed_value, expected",
    [(5.0, 6.0, PASS), (5.0, 4.0, PASS), (5.0, 5.0, PASS), (5.0, 6.5, FAIL), (5.0, 3.5, FAIL)],
)
def test_delta_guard_bounds_change_in_either_direction(baseline_value, assessed_value, expected):
    acceptance = criteria([target("tg")], [improvement("im")], [delta("dl", maximum=1.0)])
    candidate = _bundle(CANDIDATE_STATE, {"tg": 1.0, "im": 2.0, "dl": assessed_value})
    result = assess_verification(request(acceptance, ATTEMPT, candidate,
                                         _bundle(BEFORE_STATE, {"im": 0.0, "dl": baseline_value})))  # fmt: skip
    check = _checks(result)["dl"]
    assert check.status is expected
    assert check.evidence_ids == (_oid("dl", "r3"), _oid("dl", "r2"))


@pytest.mark.parametrize(
    "baseline, expected_status, expected_baseline",
    [(BaselineReference.ATTEMPT, PASS, "r2"), (BaselineReference.INITIAL, FAIL, "r1")],
)
def test_delta_guard_selects_before_or_initial(baseline, expected_status, expected_baseline):
    acceptance = criteria([target("tg")], [improvement("im")], [delta("dl", baseline, 1.0)])
    result = assess_verification(request(
        acceptance, ATTEMPT,
        _bundle(CANDIDATE_STATE, {"tg": 1.0, "im": 2.0, "dl": 10.0}),
        _bundle(BEFORE_STATE, {"im": 0.0, "dl": 10.0}),
        _bundle(INITIAL_STATE, {"dl": 0.0}),
    ))  # fmt: skip
    check = _checks(result)["dl"]
    assert check.status is expected_status
    assert check.evidence_ids == (_oid("dl", "r3"), _oid("dl", expected_baseline))


def test_identical_reused_observation_is_cited_once():
    shared = _observation("dl", 4.0, "dl-shared", S1.artifact_id)
    acceptance = criteria([target("tg")], [improvement("im")], [delta("dl", maximum=0.0)])
    result = assess_verification(request(acceptance, ATTEMPT,
                                         _bundle(CANDIDATE_RETAINING, {"tg": 1.0, "im": 2.0}, shared),
                                         _bundle(BEFORE_STATE, {"im": 0.0}, shared)))  # fmt: skip
    check = _checks(result)["dl"]
    assert check.status is PASS
    assert check.evidence_ids == (ObservationId("dl-shared"),)


def test_distinct_source_ids_on_corresponding_states_are_compared():
    acceptance = criteria([target("tg")], [improvement("im")])
    candidate = _bundle(CANDIDATE_STATE, {"tg": 1.0, "im": 2.0})  # source s1-r3
    result = assess_verification(request(acceptance, ATTEMPT, candidate, _bundle(BEFORE_STATE, {"im": 0.0})))  # s1
    assert _checks(result)["im"].status is PASS


# --- overflowing differences -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind, before, after",
    [
        ("improvement", 1.7e308, -1.7e308),  # float difference overflows to infinity
        ("improvement", -(2**1023), 2**1023),  # exact int difference not representable as a float
        ("delta", 1.7e308, -1.7e308),
        ("delta", -(2**1023), 2**1023),
    ],
)
def test_overflowing_difference_is_unavailable_with_both_ids(kind, before, after):
    guards = [delta("dl", maximum=1.0)] if kind == "delta" else []
    direction = LOWER if before > after else HIGHER
    acceptance = criteria([target("tg")], [improvement("im", direction, 1.0)], guards)
    metric = "dl" if kind == "delta" else "im"
    candidate = {"tg": 1.0, "im": 2.0, metric: after}
    baseline = {"im": 0.0, metric: before}
    result = assess_verification(request(acceptance, ATTEMPT, _bundle(CANDIDATE_STATE, candidate),
                                         _bundle(BEFORE_STATE, baseline)))  # fmt: skip
    check = _checks(result)[metric]
    assert check.status is UNAVAILABLE
    assert check.evidence_ids == (_oid(metric, "r3"), _oid(metric, "r2"))
    assert "difference" in check.explanation
    assert result.verdict is Verdict.INCONCLUSIVE


# --- unavailable operands ----------------------------------------------------------------------------


def test_missing_assessed_operand_is_unavailable_without_evidence():
    result = assess_verification(request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE, {"im": 2.0}),
                                         _bundle(BEFORE_STATE, {"im": 0.0})))  # fmt: skip
    check = _checks(result)["tg"]
    assert check.status is UNAVAILABLE
    assert check.evidence_ids == ()
    assert "assessed" in check.explanation and "missing" in check.explanation


def test_missing_baseline_operand_keeps_the_matched_assessed_id():
    result = assess_verification(request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE, {"tg": 1.0, "im": 2.0}),
                                         _bundle(BEFORE_STATE)))  # fmt: skip
    check = _checks(result)["im"]
    assert check.status is UNAVAILABLE
    assert check.evidence_ids == (_oid("im", "r3"),)
    assert "before" in check.explanation and "missing" in check.explanation


def test_missing_assessed_operand_keeps_the_matched_baseline_id():
    result = assess_verification(request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE, {"tg": 1.0}),
                                         _bundle(BEFORE_STATE, {"im": 0.0})))  # fmt: skip
    check = _checks(result)["im"]
    assert check.status is UNAVAILABLE
    assert check.evidence_ids == (_oid("im", "r2"),)


def test_both_operands_unavailable_explains_both():
    result = assess_verification(request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE, {"tg": 1.0, "im": Unavailable("clipped input")}),
                                         _bundle(BEFORE_STATE)))  # fmt: skip
    check = _checks(result)["im"]
    assert check.status is UNAVAILABLE
    assert check.evidence_ids == ()
    for text in ("assessed", "unavailable_result", "clipped input", "before", "missing"):
        assert text in check.explanation


def test_initial_baseline_role_is_named():
    acceptance = criteria([target("tg")], [improvement("im")], [delta("dl", BaselineReference.INITIAL)])
    result = assess_verification(request(acceptance, ATTEMPT, _bundle(CANDIDATE_STATE, {"tg": 1.0, "im": 2.0, "dl": 0.0}),
                                         _bundle(BEFORE_STATE, {"im": 0.0, "dl": 0.0}), _bundle(INITIAL_STATE)))  # fmt: skip
    check = _checks(result)["dl"]
    assert check.status is UNAVAILABLE
    assert check.evidence_ids == (_oid("dl", "r3"),)
    assert "initial" in check.explanation


def test_matcher_codes_reach_the_explanation():
    measurements = tuple(
        ConditionMeasurement(m.condition_id, dataclasses.replace(m.definition, analyzer=VersionRef("analyzer", "9")))
        if str(m.condition_id) == "tg" else m
        for m in bindings(SIMPLE)
    )  # fmt: skip
    result = assess_verification(request(SIMPLE, ATTEMPT, _bundle(CANDIDATE_STATE, SIMPLE_VALUES),
                                         _bundle(BEFORE_STATE, SIMPLE_VALUES), measurements=measurements))  # fmt: skip
    check = _checks(result)["tg"]
    assert check.status is UNAVAILABLE and "settings_mismatch" in check.explanation


# --- INITIAL mode ------------------------------------------------------------------------------------


def test_initial_mode_defers_improvements_and_delta_guards_without_matching(monkeypatch):
    acceptance = criteria([target("tg")], [improvement("im")],
                          [delta("dl"), absolute("ab"), delta("di", BaselineReference.INITIAL)])  # fmt: skip
    seen = []
    original = assessment_module.match_evidence

    def recording(definition, evidence):
        seen.append(definition.selector.metric.name)
        return original(definition, evidence)

    monkeypatch.setattr(assessment_module, "match_evidence", recording)
    result = assess_verification(request(acceptance, INITIAL, _bundle(INITIAL_STATE, {"tg": 1.0, "ab": 0.0})))
    checks = _checks(result)
    assert seen == ["tg", "ab"]
    for cid in ("im", "dl", "di"):
        assert checks[cid].status is NA and checks[cid].evidence_ids == () and checks[cid].explanation.strip()
    assert (checks["tg"].status, checks["ab"].status) == (PASS, PASS)
    assert [str(c.condition_id) for c in result.checks] == ["tg", "im", "dl", "ab", "di"]
    assert (result.mode, result.target_status, result.verdict) == (INITIAL, TargetStatus.SATISFIED, Verdict.ACCEPTED)


# --- verdict precedence: independent tables ----------------------------------------------------------

# Per-check status -> candidate value for its metric (None: no observation). Before values are fixed.
TARGET_VALUE = {PASS: 1.0, FAIL: -1.0, UNAVAILABLE: None}  # target GE 0
IMPROVEMENT_VALUE = {PASS: 2.0, FAIL: 0.0, UNAVAILABLE: None}  # HIGHER by >= 1 from 0
GUARD_VALUE = {PASS: 0.0, FAIL: 20.0, UNAVAILABLE: None}  # absolute LE 10
P, F, U = PASS, FAIL, UNAVAILABLE
SAT, UNMET, UNKNOWN = TargetStatus.SATISFIED, TargetStatus.UNMET, TargetStatus.UNKNOWN
ACC, REJ, INC = Verdict.ACCEPTED, Verdict.REJECTED, Verdict.INCONCLUSIVE
VERDICT_CRITERIA = criteria([target("t1"), target("t2")], [improvement("im")], [absolute("gd")])

ATTEMPT_TABLE = [
    # t1, t2, improvement, guard -> target status, verdict
    (P, P, P, P, SAT, ACC),
    (P, P, F, P, SAT, ACC),  # satisfied targets with an available failing improvement
    (P, P, U, P, SAT, INC),  # satisfied targets with an unavailable improvement
    (F, P, P, P, UNMET, ACC),  # intermediate progress
    (F, P, F, P, UNMET, REJ),
    (F, U, F, P, UNMET, REJ),  # unmet plus failed improvement despite a missing target
    (F, P, F, U, UNMET, REJ),  # unmet plus failed improvement despite a missing guard
    (F, P, U, P, UNMET, INC),
    (F, U, P, P, UNMET, INC),
    (U, P, F, P, UNKNOWN, INC),  # unknown targets with a failing improvement
    (U, P, P, P, UNKNOWN, INC),
    (P, P, P, U, SAT, INC),
    (P, P, P, F, SAT, REJ),
    (F, P, P, F, UNMET, REJ),
    (U, U, U, F, UNKNOWN, REJ),  # guard failure with missing evidence
]


@pytest.mark.parametrize("t1, t2, im, gd, target_status, verdict", ATTEMPT_TABLE)
def test_attempt_verdict_precedence(t1, t2, im, gd, target_status, verdict):
    values = {"t1": TARGET_VALUE[t1], "t2": TARGET_VALUE[t2], "im": IMPROVEMENT_VALUE[im], "gd": GUARD_VALUE[gd]}
    result = assess_verification(request(VERDICT_CRITERIA, ATTEMPT, _bundle(CANDIDATE_STATE, values),
                                         _bundle(BEFORE_STATE, {"im": 0.0})))  # fmt: skip
    assert [(str(c.condition_id), c.status) for c in result.checks] == [("t1", t1), ("t2", t2), ("im", im), ("gd", gd)]
    assert (result.target_status, result.verdict) == (target_status, verdict)


INITIAL_TABLE = [
    # t1, t2, absolute guard -> target status, verdict (improvement deferred)
    (P, P, P, SAT, ACC),
    (F, P, P, UNMET, REJ),
    (F, U, P, UNMET, REJ),
    (P, P, F, SAT, REJ),
    (U, P, F, UNKNOWN, REJ),
    (U, P, P, UNKNOWN, INC),
    (P, P, U, SAT, INC),
]


@pytest.mark.parametrize("t1, t2, gd, target_status, verdict", INITIAL_TABLE)
def test_initial_verdict_precedence(t1, t2, gd, target_status, verdict):
    values = {"t1": TARGET_VALUE[t1], "t2": TARGET_VALUE[t2], "gd": GUARD_VALUE[gd]}
    result = assess_verification(request(VERDICT_CRITERIA, INITIAL, _bundle(INITIAL_STATE, values)))
    assert [(str(c.condition_id), c.status) for c in result.checks] == [("t1", t1), ("t2", t2), ("im", NA), ("gd", gd)]
    assert (result.target_status, result.verdict) == (target_status, verdict)


def test_failing_and_unavailable_improvements_differ_when_targets_are_satisfied():
    def assess(improvement_value):
        values = {"t1": 1.0, "t2": 1.0, "im": improvement_value, "gd": 0.0}
        return assess_verification(request(VERDICT_CRITERIA, ATTEMPT, _bundle(CANDIDATE_STATE, values),
                                           _bundle(BEFORE_STATE, {"im": 0.0})))  # fmt: skip

    failing, unavailable = assess(0.0), assess(None)
    assert (_checks(failing)["im"].status, failing.target_status, failing.verdict) == (FAIL, SAT, ACC)
    assert (_checks(unavailable)["im"].status, unavailable.target_status, unavailable.verdict) == (U, SAT, INC)


def test_failing_guard_rejects_although_other_evidence_is_unavailable():
    result = assess_verification(request(VERDICT_CRITERIA, ATTEMPT, _bundle(CANDIDATE_STATE, {"gd": 20.0}),
                                         _bundle(BEFORE_STATE)))  # fmt: skip
    assert [c.status for c in result.checks] == [U, U, U, F]
    assert (result.target_status, result.verdict) == (UNKNOWN, REJ)


# --- the extracted binding validator (coordinator-runtime plan, 2026-11-09) --------------------------


def test_extracted_validator_accepts_exact_bindings_in_any_order():
    acceptance = criteria([target("tg")], [improvement("im")], [absolute("ab"), delta("dl")])
    assert validate_condition_measurements(acceptance, tuple(reversed(bindings(acceptance)))) is None


@pytest.mark.parametrize(
    "case",
    ["duplicate", "missing", "extra", "selector", "unit", "deferred-unit", "empty", "list", "element", "criteria"],
)
def test_extracted_validator_rejects_what_the_constructor_rejects(case):
    acceptance = criteria([target("tg")], [improvement("im")], [delta("dl")])
    exact = bindings(acceptance)
    wrong_unit = ConditionMeasurement(ConditionId("tg"), _definition(_selector("tg"), "unit-b"))
    deferred_unit = ConditionMeasurement(ConditionId("dl"), _definition(_selector("dl"), "unit-b"))
    supplied = {
        "duplicate": (*exact, exact[0]),
        "missing": exact[:-1],
        "extra": (*exact, ConditionMeasurement(ConditionId("other"), _definition(_selector("other")))),
        "selector": (ConditionMeasurement(ConditionId("tg"), _definition(_selector("im"))), *exact[1:]),
        "unit": (wrong_unit, *exact[1:]),
        "deferred-unit": (*exact[:-1], deferred_unit),
        "empty": (),
        "list": list(exact),
        "element": (*exact[:-1], "dl"),
        "criteria": exact,
    }[case]
    with pytest.raises(ValueError):
        validate_condition_measurements("criteria" if case == "criteria" else acceptance, supplied)
    if case not in ("list", "element", "criteria", "empty"):
        with pytest.raises(ValueError):  # the constructor applies the same rule
            request(acceptance, INITIAL, _bundle(INITIAL_STATE), measurements=supplied)
