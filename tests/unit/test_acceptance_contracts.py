"""Contract tests for acceptance events and the journal port (plan docs/superpowers/plans/2026-11-09-acceptance-contract-plan.md).

All records are labelled metadata fixtures: identities, gains, metric names and values are structural
examples, not evaluation data. Candidates come from the existing derivation and verdicts from the real
pure verifier. Journal test doubles establish response contracts only; idempotent storage and
compare-and-set behaviour belong to the in-memory adapter of the next task.
"""

import dataclasses
import typing
from datetime import datetime, timedelta, timezone

import pytest

from autonomous_mixing.core.loop.acceptance import validate_acceptance_event
from autonomous_mixing.core.loop.transitions import derive_candidate_project_state
from autonomous_mixing.core.ports.acceptance import (
    AcceptanceJournal,
    validate_acceptance_commit_response,
    validate_acceptance_query_response,
)
from autonomous_mixing.core.records.acceptance import (
    AcceptanceAbsent,
    AcceptanceAuthorityUnknown,
    AcceptanceCommitResult,
    AcceptanceCommitted,
    AcceptanceCommitUnknown,
    AcceptanceEvent,
    AcceptanceFound,
    AcceptanceNotCommitted,
    AcceptanceQuery,
    AcceptanceQueryResult,
    AcceptedReference,
)
from autonomous_mixing.core.records.actions import (
    Action,
    ExecutionIssue,
    ExecutionReceipt,
    ExecutionStatus,
    SetTrackGain,
)
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
from autonomous_mixing.core.records.observation import AnalysisScope, MeasuredScalar, Observation, TimeRange
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)
from autonomous_mixing.core.records.verification import (
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
from autonomous_mixing.core.records.verification_assessment import ConditionMeasurement, VerificationAssessment
from autonomous_mixing.core.records.verification_evidence import (
    MeasurementDefinition,
    SourceRequirement,
    StateEvidence,
)
from autonomous_mixing.core.verification.assessment import assess_verification

UTC_NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
CANDIDATE_AT = UTC_NOW + timedelta(minutes=1)
SPEC = VersionRef("analysis-spec", "1")
ANALYZER = VersionRef("analyzer", "0.1")
ALIGNMENT = VersionRef("declared-alignment", "1")
RENDER = VersionRef("render", "1")
RATE, SECONDS = 48000, 60
T1 = TrackId("t1")
SCOPE = AnalysisScope((T1,), TimeRange(0.0, 60.0))
UNIT = "unit-a"
RUN, ATTEMPT_ID, DECISION = RunId("run-1"), AttemptId("attempt-1"), DecisionId("decision-1")
ACC_ID = AcceptanceId("acceptance-1")


# --- state, action and receipt fixtures --------------------------------------------------------------


def _artifact(aid, revision, project="p1") -> AudioArtifact:
    return AudioArtifact(
        ArtifactId(aid), ProjectId(project), Revision(revision), (T1,), ArtifactKind.STEM, f"outputs/{aid}.wav",
        0.0, RATE, 2, RATE * SECONDS, "d" * 64, RENDER,
    )  # fmt: skip


def _before(project="p1") -> ProjectState:
    snapshot = ProjectSnapshot(ProjectId(project), Revision("r1"), UTC_NOW, (Track(T1, "track 1"),))
    gains = (TrackGainState(T1, _artifact("in-t1", "r0", project), 0.0),)
    return ProjectState(
        snapshot,
        ProjectProcessingState(ProjectId(project), Revision("r1"), gains),
        ArtifactCheckpoint(
            CheckpointId("cp-r1"), ProjectId(project), Revision("r1"), (_artifact("s1", "r1", project),)
        ),
    )


BEFORE = _before()


def _action(value_db=-3.0, *, revision="r1", project="p1", action_id="action-1") -> Action:
    return Action(ActionId(action_id), ProjectId(project), Revision(revision), SetTrackGain(T1, value_db))


def _receipt(*, action_id="action-1", before_revision="r1", candidate_revision="r2", project="p1") -> ExecutionReceipt:
    return ExecutionReceipt(
        ExecutionId("execution-1"), ActionId(action_id), ProjectId(project), Revision(before_revision),
        ExecutionStatus.CANDIDATE_READY, Revision(candidate_revision),
        (_artifact(f"s1-{candidate_revision}", candidate_revision, project),), (ArtifactId("s1"),), None,
    )  # fmt: skip


ACTION, RECEIPT = _action(), _receipt()
CANDIDATE = derive_candidate_project_state(BEFORE, RECEIPT, ACTION, CheckpointId("cp-r2"), CANDIDATE_AT)


# --- verification fixtures ---------------------------------------------------------------------------


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


def _observation(metric, value, state, oid=None) -> Observation:
    source = state.audio.artifacts[0].artifact_id
    return Observation(
        ObservationId(oid or f"{metric}@{state.revision}"), VersionRef(metric, "1"), (source,), SCOPE,
        MeasuredScalar(value, UNIT), SPEC, ANALYZER, UTC_NOW,
    )  # fmt: skip


def _bundle(state, values, *extra) -> StateEvidence:
    return StateEvidence(state, (*(_observation(m, v, state) for m, v in values.items()), *extra), ALIGNMENT)


def _assessment(candidate=CANDIDATE, before=BEFORE, *, candidate_values=None, initial=None) -> VerificationAssessment:
    values = {"tg": 1.0, "im": 2.0} if candidate_values is None else candidate_values
    return VerificationAssessment(
        VerificationResultId("vr-1"),
        AssessmentMode.ATTEMPT,
        CRITERIA,
        MEASUREMENTS,
        _bundle(candidate, values),
        _bundle(before, {"im": 0.0}),
        initial,
    )


ASSESSMENT = _assessment()
VERIFICATION = assess_verification(ASSESSMENT)


def _event(**overrides) -> AcceptanceEvent:
    values = dict(
        acceptance_id=ACC_ID,
        run_id=RUN,
        attempt_id=ATTEMPT_ID,
        decision_id=DECISION,
        expected=AcceptedReference(BEFORE, 0),
        action=ACTION,
        execution=RECEIPT,
        assessment=ASSESSMENT,
        verification=VERIFICATION,
    )
    values.update(overrides)
    return AcceptanceEvent(**values)


EVENT = _event()
ACCEPTED = AcceptedReference(CANDIDATE, 1)


def test_fixture_verdict_is_a_real_attempt_acceptance():
    assert (VERIFICATION.mode, VERIFICATION.verdict, VERIFICATION.target_status) == (
        AssessmentMode.ATTEMPT,
        Verdict.ACCEPTED,
        TargetStatus.SATISFIED,
    )


# --- record shapes -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "record, fields",
    [
        (AcceptedReference, ["state", "sequence"]),
        (AcceptanceEvent, ["acceptance_id", "run_id", "attempt_id", "decision_id", "expected", "action", "execution",
                           "assessment", "verification"]),
        (AcceptanceCommitted, ["event", "accepted"]),
        (AcceptanceNotCommitted, ["acceptance_id", "explanation"]),
        (AcceptanceCommitUnknown, ["acceptance_id", "explanation"]),
        (AcceptanceQuery, ["run_id", "acceptance_id"]),
        (AcceptanceFound, ["event", "accepted"]),
        (AcceptanceAbsent, ["run_id", "acceptance_id"]),
        (AcceptanceAuthorityUnknown, ["run_id", "acceptance_id", "explanation"]),
    ],
)  # fmt: skip
def test_record_fields_are_exact_required_and_frozen(record, fields):
    assert [f.name for f in dataclasses.fields(record)] == fields
    assert all(f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
               for f in dataclasses.fields(record))  # fmt: skip
    assert record.__dataclass_params__.frozen


def test_result_unions_are_exact():
    assert typing.get_args(AcceptanceCommitResult) == (
        AcceptanceCommitted,
        AcceptanceNotCommitted,
        AcceptanceCommitUnknown,
    )
    assert typing.get_args(AcceptanceQueryResult) == (AcceptanceFound, AcceptanceAbsent, AcceptanceAuthorityUnknown)


def test_acceptance_id_follows_the_identifier_pattern():
    assert str(AcceptanceId("a-1")) == "a-1"
    with pytest.raises(ValueError):
        AcceptanceId(" ")


@pytest.mark.parametrize("sequence", [0, 1, 7])
def test_accepted_reference_accepts_counts_from_zero(sequence):
    assert AcceptedReference(BEFORE, sequence).sequence == sequence


@pytest.mark.parametrize("sequence", [-1, True, 1.0, "1", None])
def test_accepted_reference_rejects_invalid_sequences(sequence):
    with pytest.raises(ValueError):
        AcceptedReference(BEFORE, sequence)


def test_accepted_reference_requires_a_state():
    with pytest.raises(ValueError):
        AcceptedReference(BEFORE.snapshot, 0)


@pytest.mark.parametrize(
    "record, args",
    [
        (AcceptanceNotCommitted, ("acceptance-1", "stale")),
        (AcceptanceNotCommitted, (ACC_ID, " ")),
        (AcceptanceCommitUnknown, (ACC_ID, "")),
        (AcceptanceCommitUnknown, (ACC_ID, None)),
        (AcceptanceQuery, ("run-1", ACC_ID)),
        (AcceptanceQuery, (RUN, "acceptance-1")),
        (AcceptanceAbsent, (RUN, "acceptance-1")),
        (AcceptanceAuthorityUnknown, (RUN, ACC_ID, " ")),
        (AcceptanceAuthorityUnknown, ("run-1", ACC_ID, "store offline")),
        (AcceptanceCommitted, ("event", ACCEPTED)),
        (AcceptanceCommitted, (EVENT, CANDIDATE)),
        (AcceptanceFound, (EVENT, "accepted")),
    ],
)
def test_outcome_records_validate_types_and_explanations(record, args):
    with pytest.raises(ValueError):
        record(*args)


# --- committed and found references ------------------------------------------------------------------


@pytest.mark.parametrize("record", [AcceptanceCommitted, AcceptanceFound])
def test_committed_reference_is_the_candidate_at_the_next_sequence(record):
    assert record(EVENT, ACCEPTED).accepted == AcceptedReference(CANDIDATE, 1)
    later = _event(expected=AcceptedReference(BEFORE, 4))
    assert record(later, AcceptedReference(CANDIDATE, 5)).accepted.sequence == 5


@pytest.mark.parametrize("record", [AcceptanceCommitted, AcceptanceFound])
@pytest.mark.parametrize(
    "accepted",
    [AcceptedReference(CANDIDATE, 0), AcceptedReference(CANDIDATE, 2), AcceptedReference(BEFORE, 1)],
    ids=["same-sequence", "skipped-sequence", "before-state"],
)
def test_committed_reference_must_match_candidate_and_increment(record, accepted):
    with pytest.raises(ValueError):
        record(EVENT, accepted)


# --- event cross-links -------------------------------------------------------------------------------


def test_a_consistent_event_is_constructed():
    assert EVENT.assessment.assessed.state == CANDIDATE
    assert EVENT.expected == AcceptedReference(BEFORE, 0)


@pytest.mark.parametrize(
    "field, value",
    [
        ("acceptance_id", "acceptance-1"),
        ("run_id", "run-1"),
        ("attempt_id", "attempt-1"),
        ("decision_id", "decision-1"),
        ("expected", BEFORE),
        ("action", ACTION.operation),
        ("execution", None),
        ("assessment", VERIFICATION),
        ("verification", ASSESSMENT),
    ],
)
def test_event_fields_are_type_checked(field, value):
    with pytest.raises(ValueError):
        _event(**{field: value})


def _initial_assessment() -> VerificationAssessment:
    return VerificationAssessment(
        VerificationResultId("vr-1"), AssessmentMode.INITIAL, CRITERIA, MEASUREMENTS,
        _bundle(CANDIDATE, {"tg": 1.0}), None, None,
    )  # fmt: skip


def _tampered_verification(**changes) -> VerificationResult:
    return dataclasses.replace(VERIFICATION, **changes)


OTHER_BEFORE_SAME_REVISION = dataclasses.replace(
    BEFORE, snapshot=dataclasses.replace(BEFORE.snapshot, captured_at=UTC_NOW - timedelta(minutes=1))
)


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"assessment": _initial_assessment(), "verification": assess_verification(_initial_assessment())},
         "ATTEMPT"),
        ({"expected": AcceptedReference(OTHER_BEFORE_SAME_REVISION, 0)}, "before"),  # stale expected reference
        ({"verification": _tampered_verification(verdict=Verdict.INCONCLUSIVE)}, "ACCEPTED"),
        ({"verification": _tampered_verification(validation_result_id=VerificationResultId("vr-2"))}, "result"),
        ({"verification": _tampered_verification(criteria_id=CriteriaId("criteria-b"))}, "criteria"),
        ({"verification": _tampered_verification(criteria_version="2")}, "criteria"),
        ({"action": _action(revision="r0")}, "revision"),
        ({"action": _action(project="p2")}, "project"),
        ({"execution": _receipt(before_revision="r0")}, "revision"),
        ({"execution": _receipt(project="p2")}, "project"),
        ({"execution": _receipt(action_id="action-2")}, "action"),
        ({"execution": _receipt(candidate_revision="r5")}, "candidate"),
    ],
)  # fmt: skip
def test_event_cross_links_are_checked(overrides, message):
    with pytest.raises(ValueError, match=message):
        _event(**overrides)


@pytest.mark.parametrize("status", [ExecutionStatus.FAILED, ExecutionStatus.UNCERTAIN, ExecutionStatus.NOT_STARTED])
def test_event_requires_a_candidate_ready_execution(status):
    issue = None if status is ExecutionStatus.NOT_STARTED else ExecutionIssue("code", "message")
    receipt = ExecutionReceipt(
        ExecutionId("execution-1"), ActionId("action-1"), ProjectId("p1"), Revision("r1"), status, None, (), (), issue
    )
    with pytest.raises(ValueError, match="CANDIDATE_READY"):
        _event(execution=receipt)


def test_event_requires_a_shared_project_for_expected_and_candidate():
    other = _before("p2")
    other_candidate = derive_candidate_project_state(
        other, _receipt(project="p2"), _action(project="p2"), CheckpointId("cp-r2"), CANDIDATE_AT
    )
    assessment = _assessment(other_candidate, other)
    with pytest.raises(ValueError, match="project"):
        _event(
            expected=AcceptedReference(other, 0), assessment=assessment, verification=assess_verification(assessment)
        )


# --- coordinator-side event validation ---------------------------------------------------------------


def test_validator_accepts_the_derived_candidate_and_recomputed_verdict():
    assert validate_acceptance_event(EVENT) is None


def test_validator_checks_its_argument_type():
    with pytest.raises(ValueError):
        validate_acceptance_event(ASSESSMENT)


def _event_for_candidate(candidate) -> AcceptanceEvent:
    assessment = _assessment(candidate)
    return _event(assessment=assessment, verification=assess_verification(assessment))


def test_validator_rejects_a_candidate_with_a_different_gain():
    # The record-level links hold, but the candidate state does not follow from the action.
    other = derive_candidate_project_state(BEFORE, RECEIPT, _action(-6.0), CheckpointId("cp-r2"), CANDIDATE_AT)
    with pytest.raises(ValueError, match="candidate"):
        validate_acceptance_event(_event_for_candidate(other))


def test_validator_rejects_a_candidate_with_altered_audio_membership():
    altered_member = dataclasses.replace(CANDIDATE.audio.artifacts[0], content_hash="e" * 64)
    altered = dataclasses.replace(CANDIDATE, audio=dataclasses.replace(CANDIDATE.audio, artifacts=(altered_member,)))
    with pytest.raises(ValueError, match="candidate"):
        validate_acceptance_event(_event_for_candidate(altered))


def test_validator_rejects_a_candidate_that_keeps_the_superseded_stem():
    stale = dataclasses.replace(
        CANDIDATE,
        audio=dataclasses.replace(CANDIDATE.audio, artifacts=(*CANDIDATE.audio.artifacts, BEFORE.audio.artifacts[0])),
    )
    with pytest.raises(ValueError, match="candidate"):
        validate_acceptance_event(_event_for_candidate(stale))


def test_validator_propagates_derivation_errors_for_inconsistent_executions():
    # The receipt supersedes nothing the gain affects; the existing derivation rejects it.
    receipt = dataclasses.replace(RECEIPT, superseded_artifact_ids=(ArtifactId("unknown"),))
    with pytest.raises(ValueError):
        validate_acceptance_event(_event(execution=receipt))


def test_validator_rejects_a_tampered_but_structurally_valid_verdict():
    checks = tuple(
        CheckResult(c.condition_id, CheckStatus.FAIL, "tampered", c.evidence_ids) if str(c.condition_id) == "im" else c
        for c in VERIFICATION.checks
    )
    tampered = _tampered_verification(checks=checks)  # still ACCEPTED / SATISFIED and record-consistent
    event = _event(verification=tampered)
    with pytest.raises(ValueError, match="verification"):
        validate_acceptance_event(event)


def test_validator_rejects_an_accepted_verdict_the_evidence_does_not_support():
    assessment = _assessment(candidate_values={"tg": -1.0, "im": 2.0})  # target fails: real verdict is not SATISFIED
    computed = assess_verification(assessment)
    claimed = dataclasses.replace(computed, target_status=TargetStatus.SATISFIED, checks=VERIFICATION.checks)
    with pytest.raises(ValueError, match="verification"):
        validate_acceptance_event(_event(assessment=assessment, verification=claimed))


def test_identical_duplicate_evidence_is_consistent_and_conflicting_evidence_is_rejected():
    shared = _observation("im", 0.0, BEFORE)
    initial = StateEvidence(BEFORE, (shared,), ALIGNMENT)  # initial may equal before; same ID, same contents
    assessment = _assessment(initial=initial)
    validate_acceptance_event(_event(assessment=assessment, verification=assess_verification(assessment)))

    conflicting = StateEvidence(BEFORE, (_observation("im", 5.0, BEFORE),), ALIGNMENT)
    with pytest.raises(ValueError, match="conflicting"):
        _assessment(initial=conflicting)


# --- journal protocol and response checks ------------------------------------------------------------


def test_journal_protocol_shape():
    assert typing.Protocol in AcceptanceJournal.__mro__
    assert not getattr(AcceptanceJournal, "_is_runtime_protocol", False)
    hints = {name: typing.get_type_hints(getattr(AcceptanceJournal, name)) for name in ("commit", "query")}
    assert hints["commit"] == {"event": AcceptanceEvent, "return": AcceptanceCommitResult}
    assert hints["query"] == {"request": AcceptanceQuery, "return": AcceptanceQueryResult}


class _ScriptedJournal:
    """A test double returning prepared responses; it establishes response contracts only."""

    def __init__(self, commit_response, query_response):
        self._commit, self._query = commit_response, query_response

    def commit(self, event):
        return self._commit

    def query(self, request):
        return self._query


QUERY = AcceptanceQuery(RUN, ACC_ID)


@pytest.mark.parametrize(
    "response",
    [
        AcceptanceCommitted(EVENT, ACCEPTED),
        AcceptanceNotCommitted(ACC_ID, "stale expected reference"),
        AcceptanceCommitUnknown(ACC_ID, "store did not confirm"),
    ],
)
def test_valid_commit_responses(response):
    journal: AcceptanceJournal = _ScriptedJournal(response, None)
    assert validate_acceptance_commit_response(EVENT, journal.commit(EVENT)) is None


OTHER_EVENT = _event(acceptance_id=AcceptanceId("acceptance-2"))


@pytest.mark.parametrize(
    "response",
    [
        AcceptanceCommitted(OTHER_EVENT, ACCEPTED),  # a different event
        AcceptanceCommitted(_event(attempt_id=AttemptId("attempt-2")), ACCEPTED),  # same ID, different contents
        AcceptanceCommitted(_event(expected=AcceptedReference(BEFORE, 3)), AcceptedReference(CANDIDATE, 4)),
        AcceptanceNotCommitted(AcceptanceId("acceptance-2"), "stale"),
        AcceptanceCommitUnknown(AcceptanceId("acceptance-2"), "unknown"),
    ],
    ids=["other-event", "altered-event", "other-sequence", "not-committed-other-id", "unknown-other-id"],
)
def test_commit_responses_must_name_the_submitted_event(response):
    with pytest.raises(ValueError):
        validate_acceptance_commit_response(EVENT, response)


@pytest.mark.parametrize(
    "event, response",
    [
        ("event", AcceptanceCommitUnknown(ACC_ID, "unknown")),
        (EVENT, "committed"),
        (EVENT, AcceptanceFound(EVENT, ACCEPTED)),  # a query result is not a commit result
        (EVENT, None),
    ],
)
def test_commit_response_types_are_checked(event, response):
    with pytest.raises(ValueError):
        validate_acceptance_commit_response(event, response)


@pytest.mark.parametrize(
    "response",
    [
        AcceptanceFound(EVENT, ACCEPTED),
        AcceptanceAbsent(RUN, ACC_ID),
        AcceptanceAuthorityUnknown(RUN, ACC_ID, "store offline"),
    ],
)
def test_valid_query_responses(response):
    journal: AcceptanceJournal = _ScriptedJournal(None, response)
    assert validate_acceptance_query_response(QUERY, journal.query(QUERY)) is None


@pytest.mark.parametrize(
    "response",
    [
        AcceptanceFound(_event(run_id=RunId("run-2")), ACCEPTED),  # wrong run
        AcceptanceFound(OTHER_EVENT, ACCEPTED),  # wrong acceptance
        AcceptanceAbsent(RunId("run-2"), ACC_ID),
        AcceptanceAbsent(RUN, AcceptanceId("acceptance-2")),
        AcceptanceAuthorityUnknown(RunId("run-2"), ACC_ID, "offline"),
        AcceptanceAuthorityUnknown(RUN, AcceptanceId("acceptance-2"), "offline"),
    ],
)
def test_query_responses_must_name_the_queried_run_and_acceptance(response):
    with pytest.raises(ValueError):
        validate_acceptance_query_response(QUERY, response)


@pytest.mark.parametrize(
    "request_, response",
    [
        ("query", AcceptanceAbsent(RUN, ACC_ID)),
        (QUERY, "absent"),
        (QUERY, AcceptanceCommitted(EVENT, ACCEPTED)),  # a commit result is not a query result
        (QUERY, None),
    ],
)
def test_query_response_types_are_checked(request_, response):
    with pytest.raises(ValueError):
        validate_acceptance_query_response(request_, response)


def test_an_invalid_query_response_is_never_turned_into_absence():
    wrong_run = AcceptanceFound(_event(run_id=RunId("run-2")), ACCEPTED)
    with pytest.raises(ValueError):
        validate_acceptance_query_response(QUERY, wrong_run)
