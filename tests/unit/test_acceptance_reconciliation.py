"""Contract tests for acceptance reconciliation (plan docs/superpowers/plans/2026-11-09-acceptance-reconciliation-plan.md).

All records are labelled metadata fixtures on generic project and track identities: no files are read.
Acceptance events come from the existing candidate derivation and the real verifier; integrity reports
are hand-built records whose statuses stand for an inspector's claims. Reconciliation interprets supplied
evidence only: it performs no journal, filesystem or clock calls, selection, recording or rollback.
"""

import dataclasses
import inspect
from datetime import datetime, timedelta, timezone

import pytest

import autonomous_mixing.core.loop.reconciliation as reconciliation_module
from autonomous_mixing.core.loop.reconciliation import reconcile_acceptance
from autonomous_mixing.core.loop.transitions import derive_candidate_project_state
from autonomous_mixing.core.records.acceptance import (
    AcceptanceAbsent,
    AcceptanceAuthorityUnknown,
    AcceptanceEvent,
    AcceptanceFound,
    AcceptedReference,
)
from autonomous_mixing.core.records.actions import Action, ExecutionReceipt, ExecutionStatus, SetTrackGain
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.identifiers import (
    AcceptanceId,
    ActionId,
    ArtifactId,
    AttemptId,
    CheckpointId,
    ConditionId,
    CriteriaId,
    DecisionId,
    ExecutionId,
    ObservationId,
    ProjectId,
    Revision,
    RunId,
    TrackId,
    VerificationResultId,
    VersionRef,
)
from autonomous_mixing.core.records.integrity import (
    ArtifactIntegrityResult,
    ArtifactRootBinding,
    IntegrityStatus,
    StateIntegrityReport,
    StateIntegrityRequest,
)
from autonomous_mixing.core.records.observation import AnalysisScope, MeasuredScalar, Observation, TimeRange
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)
from autonomous_mixing.core.records.reconciliation import ReconciliationOutcome, ReconciliationResult
from autonomous_mixing.core.records.verification import (
    AcceptanceCriteria,
    AssessmentMode,
    ComparisonOperator,
    ImprovementCondition,
    ImprovementDirection,
    MetricSelector,
    TargetCondition,
)
from autonomous_mixing.core.records.verification_assessment import ConditionMeasurement, VerificationAssessment
from autonomous_mixing.core.records.verification_evidence import (
    MeasurementDefinition,
    SourceRequirement,
    StateEvidence,
)
from autonomous_mixing.core.verification.assessment import assess_verification

UTC_NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
SPEC, ANALYZER = VersionRef("analysis-spec", "1"), VersionRef("analyzer", "0.1")
ALIGNMENT, RENDER = VersionRef("declared-alignment", "1"), VersionRef("render", "1")
RATE, SECONDS, UNIT = 48000, 60, "unit-a"
T1 = TrackId("t1")
SCOPE = AnalysisScope((T1,), TimeRange(0.0, 60.0))
RUN = RunId("run-1")
Outcome = ReconciliationOutcome
VERIFIED, FAILED, UNCERTAIN = IntegrityStatus.VERIFIED, IntegrityStatus.FAILED, IntegrityStatus.UNCERTAIN


# --- acceptance fixture (existing derivation and real verifier) --------------------------------------


def _artifact(aid, revision) -> AudioArtifact:
    return AudioArtifact(
        ArtifactId(aid), ProjectId("p1"), Revision(revision), (T1,), ArtifactKind.STEM, f"outputs/{aid}.wav",
        0.0, RATE, 2, RATE * SECONDS, "d" * 64, RENDER,
    )  # fmt: skip


def _before(captured_at=UTC_NOW) -> ProjectState:
    snapshot = ProjectSnapshot(ProjectId("p1"), Revision("r1"), captured_at, (Track(T1, "track 1"),))
    gains = (TrackGainState(T1, _artifact("in-t1", "r0"), 0.0),)
    return ProjectState(
        snapshot,
        ProjectProcessingState(ProjectId("p1"), Revision("r1"), gains),
        ArtifactCheckpoint(CheckpointId("cp-r1"), ProjectId("p1"), Revision("r1"), (_artifact("s1-r1", "r1"),)),
    )


def _selector(metric) -> MetricSelector:
    return MetricSelector(VersionRef(metric, "1"), SCOPE)


CRITERIA = AcceptanceCriteria(
    CriteriaId("criteria-a"),
    "1",
    (TargetCondition(ConditionId("tg"), _selector("tg"), ComparisonOperator.GE, MeasuredScalar(0.0, UNIT)),),
    (ImprovementCondition(ConditionId("im"), _selector("im"), ImprovementDirection.HIGHER, MeasuredScalar(1.0, UNIT)),),
    (),
)
_SOURCE = SourceRequirement(ArtifactKind.STEM, (T1,), RENDER, RATE, 2, 0.0, RATE * SECONDS)
MEASUREMENTS = tuple(
    ConditionMeasurement(c.condition_id, MeasurementDefinition(c.selector, UNIT, SPEC, ANALYZER, ALIGNMENT, (_SOURCE,)))
    for c in (*CRITERIA.targets, *CRITERIA.improvements)
)


def _bundle(state, values) -> StateEvidence:
    source = state.audio.artifacts[0].artifact_id
    observations = tuple(
        Observation(ObservationId(f"{m}@{state.revision}"), VersionRef(m, "1"), (source,), SCOPE,
                    MeasuredScalar(v, UNIT), SPEC, ANALYZER, UTC_NOW)
        for m, v in values.items()
    )  # fmt: skip
    return StateEvidence(state, observations, ALIGNMENT)


def make_event(*, attempt_id="attempt-1", run_id=RUN, acceptance_id="acceptance-1") -> AcceptanceEvent:
    before = _before()
    action = Action(ActionId("action-1"), ProjectId("p1"), Revision("r1"), SetTrackGain(T1, -3.0))
    receipt = ExecutionReceipt(
        ExecutionId("execution-1"), action.action_id, ProjectId("p1"), Revision("r1"), ExecutionStatus.CANDIDATE_READY,
        Revision("r2"), (_artifact("s1-r2", "r2"),), (ArtifactId("s1-r1"),), None,
    )  # fmt: skip
    candidate = derive_candidate_project_state(before, receipt, action, CheckpointId("cp-r2"), UTC_NOW + timedelta(1))
    assessment = VerificationAssessment(
        VerificationResultId("vr-1"), AssessmentMode.ATTEMPT, CRITERIA, MEASUREMENTS,
        _bundle(candidate, {"tg": 1.0, "im": 2.0}), _bundle(before, {"im": 0.0}), None,
    )  # fmt: skip
    return AcceptanceEvent(
        AcceptanceId(acceptance_id), run_id, AttemptId(attempt_id), DecisionId("decision-1"),
        AcceptedReference(before, 0), action, receipt, assessment, assess_verification(assessment),
    )  # fmt: skip


EVENT = make_event()
CANDIDATE = EVENT.assessment.assessed.state
EXPECTED = EVENT.expected
ACCEPTED = AcceptedReference(CANDIDATE, 1)
FOUND = AcceptanceFound(EVENT, ACCEPTED)
ABSENT = AcceptanceAbsent(RUN, EVENT.acceptance_id)
UNKNOWN = AcceptanceAuthorityUnknown(RUN, EVENT.acceptance_id, "journal unreachable")


# --- integrity fixtures (records only; statuses stand for inspector claims) --------------------------


def integrity_request(state, root="exports") -> StateIntegrityRequest:
    """Bind every required artifact (current audio and fixed inputs) to one logical root."""
    ids = dict.fromkeys(
        a.artifact_id for a in (*state.audio.artifacts, *(g.input_artifact for g in state.processing.track_gains))
    )
    return StateIntegrityRequest(state, tuple(ArtifactRootBinding(i, root) for i in ids))


def report(request, status=VERIFIED) -> StateIntegrityReport:
    results = tuple(
        ArtifactIntegrityResult(
            a, status, a.content_hash if status is VERIFIED else None, f"labelled fixture claim: {status.value}"
        )
        for a in request.required_artifacts
    )
    return StateIntegrityReport(request, results)


def mixed_report(request, statuses) -> StateIntegrityReport:
    results = tuple(
        ArtifactIntegrityResult(a, s, a.content_hash if s is VERIFIED else None, f"fixture: {s.value}")
        for a, s in zip(request.required_artifacts, statuses, strict=True)
    )
    return StateIntegrityReport(request, results)


CANDIDATE_REQUEST = integrity_request(CANDIDATE)
PREVIOUS_REQUEST = integrity_request(EXPECTED.state)


def reconcile(authority, request, integrity):
    return reconcile_acceptance(EVENT, authority, integrity_request=request, integrity_report=integrity)


# --- records -----------------------------------------------------------------------------------------


def test_outcome_members_and_values_are_exact():
    assert [(o.name, o.value) for o in ReconciliationOutcome] == [
        ("CONFIRMED_ACCEPTANCE", "confirmed_acceptance"),
        ("VERIFIED_PREVIOUS_STATE", "verified_previous_state"),
        ("ACCEPTED_STATE_FAILED", "accepted_state_failed"),
        ("ACCEPTED_STATE_UNCERTAIN", "accepted_state_uncertain"),
        ("PREVIOUS_STATE_FAILED", "previous_state_failed"),
        ("PREVIOUS_STATE_UNCERTAIN", "previous_state_uncertain"),
        ("AUTHORITY_UNCERTAIN", "authority_uncertain"),
    ]


def test_result_fields_are_exact_required_and_frozen():
    fields = dataclasses.fields(ReconciliationResult)
    assert [f.name for f in fields] == ["outcome", "authoritative_reference", "verified_reference"]
    assert all(f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING for f in fields)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ReconciliationResult(Outcome.AUTHORITY_UNCERTAIN, None, None).outcome = Outcome.CONFIRMED_ACCEPTANCE


@pytest.mark.parametrize(
    "outcome, authoritative, verified",
    [
        (Outcome.CONFIRMED_ACCEPTANCE, ACCEPTED, ACCEPTED),
        (Outcome.VERIFIED_PREVIOUS_STATE, EXPECTED, EXPECTED),
        (Outcome.ACCEPTED_STATE_FAILED, ACCEPTED, None),
        (Outcome.ACCEPTED_STATE_UNCERTAIN, ACCEPTED, None),
        (Outcome.PREVIOUS_STATE_FAILED, EXPECTED, None),
        (Outcome.PREVIOUS_STATE_UNCERTAIN, EXPECTED, None),
        (Outcome.AUTHORITY_UNCERTAIN, None, None),
    ],
)
def test_valid_result_shapes(outcome, authoritative, verified):
    assert ReconciliationResult(outcome, authoritative, verified).outcome is outcome


@pytest.mark.parametrize(
    "outcome, authoritative, verified",
    [
        (Outcome.AUTHORITY_UNCERTAIN, EXPECTED, None),
        (Outcome.AUTHORITY_UNCERTAIN, None, EXPECTED),
        (Outcome.CONFIRMED_ACCEPTANCE, None, None),
        (Outcome.CONFIRMED_ACCEPTANCE, ACCEPTED, None),
        (Outcome.CONFIRMED_ACCEPTANCE, ACCEPTED, EXPECTED),
        (Outcome.VERIFIED_PREVIOUS_STATE, EXPECTED, None),
        (Outcome.ACCEPTED_STATE_FAILED, ACCEPTED, ACCEPTED),
        (Outcome.ACCEPTED_STATE_UNCERTAIN, None, None),
        (Outcome.PREVIOUS_STATE_FAILED, EXPECTED, EXPECTED),
        (Outcome.PREVIOUS_STATE_UNCERTAIN, EXPECTED, EXPECTED),
        ("confirmed_acceptance", ACCEPTED, ACCEPTED),
        (Outcome.CONFIRMED_ACCEPTANCE, CANDIDATE, CANDIDATE),
        (Outcome.ACCEPTED_STATE_FAILED, ACCEPTED, "none"),
    ],
)
def test_invalid_result_shapes(outcome, authoritative, verified):
    with pytest.raises(ValueError):
        ReconciliationResult(outcome, authoritative, verified)


# --- the seven outcomes ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status, outcome",
    [
        (VERIFIED, Outcome.CONFIRMED_ACCEPTANCE),
        (FAILED, Outcome.ACCEPTED_STATE_FAILED),
        (UNCERTAIN, Outcome.ACCEPTED_STATE_UNCERTAIN),
    ],
)
def test_found_authority_reports_the_candidate_and_its_integrity(status, outcome):
    result = reconcile(FOUND, CANDIDATE_REQUEST, report(CANDIDATE_REQUEST, status))
    assert result.outcome is outcome
    assert result.authoritative_reference is FOUND.accepted  # the original object
    assert result.verified_reference is (FOUND.accepted if status is VERIFIED else None)


@pytest.mark.parametrize(
    "status, outcome",
    [
        (VERIFIED, Outcome.VERIFIED_PREVIOUS_STATE),
        (FAILED, Outcome.PREVIOUS_STATE_FAILED),
        (UNCERTAIN, Outcome.PREVIOUS_STATE_UNCERTAIN),
    ],
)
def test_absent_authority_reports_the_expected_state_and_its_integrity(status, outcome):
    result = reconcile(ABSENT, PREVIOUS_REQUEST, report(PREVIOUS_REQUEST, status))
    assert result.outcome is outcome
    assert result.authoritative_reference is EVENT.expected
    assert result.verified_reference is (EVENT.expected if status is VERIFIED else None)


def test_unknown_authority_is_authority_uncertain():
    result = reconcile(UNKNOWN, None, None)
    assert (result.outcome, result.authoritative_reference, result.verified_reference) == (
        Outcome.AUTHORITY_UNCERTAIN,
        None,
        None,
    )


def test_known_candidate_authority_with_damaged_files_keeps_the_authoritative_reference():
    statuses = [VERIFIED] * (len(CANDIDATE_REQUEST.required_artifacts) - 1) + [FAILED]
    result = reconcile(FOUND, CANDIDATE_REQUEST, mixed_report(CANDIDATE_REQUEST, statuses))
    assert result.outcome is Outcome.ACCEPTED_STATE_FAILED
    assert result.authoritative_reference == ACCEPTED and result.verified_reference is None


def test_confirmed_acceptance_and_verified_previous_state_stay_distinct():
    confirmed = reconcile(FOUND, CANDIDATE_REQUEST, report(CANDIDATE_REQUEST))
    previous = reconcile(ABSENT, PREVIOUS_REQUEST, report(PREVIOUS_REQUEST))
    assert confirmed.outcome is not previous.outcome
    assert confirmed.verified_reference.state == CANDIDATE and confirmed.verified_reference.sequence == 1
    assert previous.verified_reference.state == EXPECTED.state and previous.verified_reference.sequence == 0


# --- unknown authority cannot be replaced by integrity evidence --------------------------------------


@pytest.mark.parametrize(
    "request_, integrity",
    [
        (PREVIOUS_REQUEST, report(PREVIOUS_REQUEST)),  # a tempting verified previous state
        (CANDIDATE_REQUEST, report(CANDIDATE_REQUEST)),
        (PREVIOUS_REQUEST, None),
        (None, report(PREVIOUS_REQUEST)),
    ],
)
def test_unknown_authority_rejects_any_integrity_evidence(request_, integrity):
    with pytest.raises(ValueError):
        reconcile(UNKNOWN, request_, integrity)


# --- integrity evidence must belong to the authoritative state ---------------------------------------


@pytest.mark.parametrize("authority", [FOUND, ABSENT], ids=["found", "absent"])
@pytest.mark.parametrize(
    "request_, integrity",
    [(None, None), (None, "report"), ("request", None)],
    ids=["both-missing", "request-missing", "report-missing"],
)
def test_known_authority_requires_both_integrity_arguments(authority, request_, integrity):
    request_obj = CANDIDATE_REQUEST if authority is FOUND else PREVIOUS_REQUEST
    with pytest.raises(ValueError):
        reconcile(
            authority,
            request_obj if request_ == "request" else None,
            report(request_obj) if integrity == "report" else None,
        )


def test_found_authority_rejects_evidence_about_the_previous_state():
    with pytest.raises(ValueError, match="candidate"):
        reconcile(FOUND, PREVIOUS_REQUEST, report(PREVIOUS_REQUEST))


def test_absent_authority_rejects_evidence_about_the_candidate():
    with pytest.raises(ValueError, match="expected"):
        reconcile(ABSENT, CANDIDATE_REQUEST, report(CANDIDATE_REQUEST))


def test_same_revision_with_altered_metadata_is_rejected():
    altered = dataclasses.replace(
        EXPECTED.state, snapshot=dataclasses.replace(EXPECTED.state.snapshot, captured_at=UTC_NOW - timedelta(1))
    )
    assert altered.revision == EXPECTED.state.revision and altered != EXPECTED.state
    request = integrity_request(altered)
    with pytest.raises(ValueError, match="expected"):
        reconcile(ABSENT, request, report(request))


def test_report_for_other_root_bindings_is_rejected():
    other_roots = integrity_request(CANDIDATE, root="elsewhere")
    with pytest.raises(ValueError, match="request"):
        reconcile(FOUND, CANDIDATE_REQUEST, report(other_roots))


def test_supplied_request_may_use_any_roots_when_the_report_answers_it():
    request = integrity_request(CANDIDATE, root="archive")
    assert reconcile(FOUND, request, report(request)).outcome is Outcome.CONFIRMED_ACCEPTANCE


@pytest.mark.parametrize(
    "request_, integrity",
    [(CANDIDATE_REQUEST, "report"), ("request", report(CANDIDATE_REQUEST)), (CANDIDATE, report(CANDIDATE_REQUEST))],
)
def test_integrity_argument_types_are_checked(request_, integrity):
    with pytest.raises(ValueError):
        reconcile(FOUND, request_, integrity)


# --- authority identity ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "authority",
    [
        AcceptanceFound(make_event(run_id=RunId("run-2")), ACCEPTED),
        AcceptanceFound(make_event(acceptance_id="acceptance-2"), ACCEPTED),
        AcceptanceAbsent(RunId("run-2"), EVENT.acceptance_id),
        AcceptanceAbsent(RUN, AcceptanceId("acceptance-2")),
        AcceptanceAuthorityUnknown(RunId("run-2"), EVENT.acceptance_id, "offline"),
        AcceptanceAuthorityUnknown(RUN, AcceptanceId("acceptance-2"), "offline"),
    ],
)
def test_authority_must_name_the_submitted_run_and_acceptance(authority):
    request_ = CANDIDATE_REQUEST if isinstance(authority, AcceptanceFound) else PREVIOUS_REQUEST
    integrity = report(request_)
    if isinstance(authority, AcceptanceAuthorityUnknown):
        request_, integrity = None, None
    with pytest.raises(ValueError):
        reconcile(authority, request_, integrity)


def test_found_event_with_altered_contents_under_the_same_ids_is_rejected():
    altered = make_event(attempt_id="attempt-other")  # same run and acceptance ID, different contents
    assert (altered.run_id, altered.acceptance_id) == (EVENT.run_id, EVENT.acceptance_id) and altered != EVENT
    with pytest.raises(ValueError, match="event"):
        reconcile(AcceptanceFound(altered, ACCEPTED), CANDIDATE_REQUEST, report(CANDIDATE_REQUEST))


def test_an_equal_separately_built_found_event_is_accepted():
    rebuilt = make_event()
    assert rebuilt == EVENT and rebuilt is not EVENT
    result = reconcile(AcceptanceFound(rebuilt, ACCEPTED), CANDIDATE_REQUEST, report(CANDIDATE_REQUEST))
    assert result.outcome is Outcome.CONFIRMED_ACCEPTANCE


@pytest.mark.parametrize("authority", ["found", None, ACCEPTED, EVENT])
def test_authority_type_is_checked(authority):
    with pytest.raises(ValueError):
        reconcile(authority, CANDIDATE_REQUEST, report(CANDIDATE_REQUEST))


# --- the event itself --------------------------------------------------------------------------------


def test_the_event_is_validated_by_the_coordinator_precommit_check():
    tampered_checks = tuple(
        dataclasses.replace(c, explanation="tampered") if str(c.condition_id) == "im" else c
        for c in EVENT.verification.checks
    )
    tampered = dataclasses.replace(EVENT, verification=dataclasses.replace(EVENT.verification, checks=tampered_checks))
    with pytest.raises(ValueError, match="verification"):
        reconcile_acceptance(tampered, ABSENT, integrity_request=PREVIOUS_REQUEST,
                             integrity_report=report(PREVIOUS_REQUEST))  # fmt: skip


def test_event_type_is_checked():
    with pytest.raises(ValueError):
        reconcile_acceptance(ACCEPTED, ABSENT, integrity_request=PREVIOUS_REQUEST,
                             integrity_report=report(PREVIOUS_REQUEST))  # fmt: skip


def test_unexpected_errors_propagate(monkeypatch):
    def boom(event):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(reconciliation_module, "validate_acceptance_event", boom)
    with pytest.raises(RuntimeError):
        reconcile(ABSENT, PREVIOUS_REQUEST, report(PREVIOUS_REQUEST))


# --- signature ---------------------------------------------------------------------------------------


def test_integrity_arguments_are_required_keyword_only():
    parameters = inspect.signature(reconcile_acceptance).parameters
    assert list(parameters) == ["event", "authority", "integrity_request", "integrity_report"]
    for name in ("integrity_request", "integrity_report"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        reconcile_acceptance(EVENT, UNKNOWN)
