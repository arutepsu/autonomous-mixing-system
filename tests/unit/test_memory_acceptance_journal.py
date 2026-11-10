"""Tests for the in-memory acceptance journal (plan docs/superpowers/plans/2026-11-09-memory-acceptance-journal-plan.md).

All records are labelled metadata fixtures: identities, gains, metric names and values are structural
examples, not evaluation data. Events are built with the existing candidate derivation and the real
verifier so that they satisfy the coordinator's precommit check. The journal is NON_DURABLE: history
lives in one Python object only. Lost acknowledgements are simulated by test-only wrappers.
"""

import threading
from datetime import datetime, timedelta, timezone

import pytest

from autonomous_mixing.adapters.journal.memory import InMemoryAcceptanceJournal
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
    AcceptanceCommitted,
    AcceptanceCommitUnknown,
    AcceptanceEvent,
    AcceptanceFound,
    AcceptanceNotCommitted,
    AcceptanceQuery,
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
SPEC = VersionRef("analysis-spec", "1")
ANALYZER = VersionRef("analyzer", "0.1")
ALIGNMENT = VersionRef("declared-alignment", "1")
RENDER = VersionRef("render", "1")
RATE, SECONDS = 48000, 60
T1 = TrackId("t1")
SCOPE = AnalysisScope((T1,), TimeRange(0.0, 60.0))
UNIT = "unit-a"
RUN = RunId("run-1")


# --- local fixtures (no other test module's helpers) -------------------------------------------------


def _artifact(aid, revision) -> AudioArtifact:
    return AudioArtifact(
        ArtifactId(aid), ProjectId("p1"), Revision(revision), (T1,), ArtifactKind.STEM, f"outputs/{aid}.wav",
        0.0, RATE, 2, RATE * SECONDS, "d" * 64, RENDER,
    )  # fmt: skip


def _initial_state(captured_at=UTC_NOW) -> ProjectState:
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


def make_event(expected: AcceptedReference, candidate_revision: str, *, acceptance_id=None, gain_db=-3.0,
               run_id=RUN, attempt_id="attempt-1") -> AcceptanceEvent:  # fmt: skip
    """A complete, validator-approved acceptance of a gain change on track t1 over ``expected``."""
    before = expected.state
    action = Action(
        ActionId(f"action-{candidate_revision}"), ProjectId("p1"), before.revision, SetTrackGain(T1, gain_db)
    )
    receipt = ExecutionReceipt(
        ExecutionId(f"execution-{candidate_revision}"), action.action_id, ProjectId("p1"), before.revision,
        ExecutionStatus.CANDIDATE_READY, Revision(candidate_revision),
        (_artifact(f"s1-{candidate_revision}", candidate_revision),), (before.audio.artifacts[0].artifact_id,), None,
    )  # fmt: skip
    candidate = derive_candidate_project_state(
        before, receipt, action, CheckpointId(f"cp-{candidate_revision}"), UTC_NOW + timedelta(minutes=1)
    )
    assessment = VerificationAssessment(
        VerificationResultId(f"vr-{candidate_revision}"), AssessmentMode.ATTEMPT, CRITERIA, MEASUREMENTS,
        _bundle(candidate, {"tg": 1.0, "im": 2.0}), _bundle(before, {"im": 0.0}), None,
    )  # fmt: skip
    event = AcceptanceEvent(
        AcceptanceId(acceptance_id or f"acceptance-{candidate_revision}"), run_id, AttemptId(attempt_id),
        DecisionId(f"decision-{candidate_revision}"), expected, action, receipt, assessment,
        assess_verification(assessment),
    )  # fmt: skip
    validate_acceptance_event(event)  # the coordinator's precommit obligation holds for every fixture
    return event


INITIAL = AcceptedReference(_initial_state(), 0)
EVENT_1 = make_event(INITIAL, "r2")
AFTER_1 = AcceptedReference(EVENT_1.assessment.assessed.state, 1)
EVENT_2 = make_event(AFTER_1, "r3")
AFTER_2 = AcceptedReference(EVENT_2.assessment.assessed.state, 2)


def journal() -> InMemoryAcceptanceJournal:
    return InMemoryAcceptanceJournal(RUN, INITIAL)


def committed(response) -> AcceptanceCommitted:
    assert isinstance(response, AcceptanceCommitted), response
    return response


def _query(j, event=EVENT_1, run_id=RUN):
    request = AcceptanceQuery(run_id, event.acceptance_id)
    response = j.query(request)
    validate_acceptance_query_response(request, response)
    return response


def _commit(j, event):
    response = j.commit(event)
    validate_acceptance_commit_response(event, response)
    return response


# --- construction and read-only properties -----------------------------------------------------------


def test_a_new_journal_is_empty_at_the_initial_reference():
    j = journal()
    assert (j.run_id, j.initial, j.accepted, j.commits, j.durability) == (RUN, INITIAL, INITIAL, (), "non_durable")
    assert j.initial is INITIAL and j.accepted is INITIAL


def test_the_journal_satisfies_the_port_signatures():
    j: AcceptanceJournal = journal()
    assert callable(j.commit) and callable(j.query)


@pytest.mark.parametrize(
    "run_id, initial",
    [("run-1", INITIAL), (RUN, INITIAL.state), (RUN, AcceptedReference(INITIAL.state, 1)), (RUN, AFTER_1)],
    ids=["run-type", "initial-type", "nonzero-sequence", "later-reference"],
)
def test_construction_validates_run_and_initial_sequence(run_id, initial):
    with pytest.raises(ValueError):
        InMemoryAcceptanceJournal(run_id, initial)


@pytest.mark.parametrize("name", ["run_id", "initial", "accepted", "commits", "durability"])
def test_properties_are_read_only(name):
    j = journal()
    with pytest.raises(AttributeError):
        setattr(j, name, getattr(j, name))


def test_commits_are_an_immutable_tuple_snapshot():
    j = journal()
    _commit(j, EVENT_1)
    snapshot = j.commits
    assert isinstance(snapshot, tuple)
    _commit(j, EVENT_2)
    assert len(snapshot) == 1 and len(j.commits) == 2


# --- commits -----------------------------------------------------------------------------------------


def test_first_commit_advances_to_the_candidate_at_sequence_one():
    j = journal()
    ack = committed(_commit(j, EVENT_1))
    assert ack.event is EVENT_1
    assert ack.accepted == AFTER_1
    assert j.accepted is ack.accepted and j.commits == (ack,)


def test_sequential_commits_chain_through_the_accepted_reference():
    j = journal()
    first, second = committed(_commit(j, EVENT_1)), committed(_commit(j, EVENT_2))
    assert j.commits == (first, second)
    assert (j.accepted, j.accepted.sequence) == (AFTER_2, 2)
    assert j.accepted.state == EVENT_2.assessment.assessed.state


def test_exact_duplicate_returns_the_original_acknowledgement_after_later_commits():
    j = journal()
    first = committed(_commit(j, EVENT_1))
    second = committed(_commit(j, EVENT_2))
    again = _commit(j, EVENT_1)  # its expected reference is no longer the head
    assert again is first
    assert _commit(j, EVENT_2) is second
    assert j.commits == (first, second) and j.accepted == AFTER_2


def test_an_equal_but_separately_built_event_is_an_exact_duplicate():
    j = journal()
    first = committed(_commit(j, EVENT_1))
    rebuilt = make_event(INITIAL, "r2")
    assert rebuilt == EVENT_1 and rebuilt is not EVENT_1
    assert _commit(j, rebuilt) is first


def test_same_id_with_a_different_event_is_not_committed_and_changes_nothing():
    j = journal()
    first = committed(_commit(j, EVENT_1))
    different = make_event(INITIAL, "r2", attempt_id="attempt-other")  # same acceptance ID, other contents
    response = _commit(j, different)
    assert isinstance(response, AcceptanceNotCommitted) and response.acceptance_id == EVENT_1.acceptance_id
    assert j.commits == (first,) and j.accepted == AFTER_1


def test_reused_id_is_reported_even_when_the_expected_reference_is_current():
    j = journal()
    _commit(j, EVENT_1)
    reused = make_event(AFTER_1, "r3", acceptance_id=str(EVENT_1.acceptance_id))
    assert isinstance(_commit(j, reused), AcceptanceNotCommitted)
    assert len(j.commits) == 1


@pytest.mark.parametrize(
    "expected",
    [
        AcceptedReference(INITIAL.state, 1),  # right state, wrong sequence
        AcceptedReference(_initial_state(UTC_NOW - timedelta(minutes=5)), 0),  # same revision, altered state
    ],
    ids=["wrong-sequence", "altered-state-same-revision"],
)
def test_stale_or_altered_expected_reference_is_not_committed(expected):
    j = journal()
    response = _commit(j, make_event(expected, "r2"))
    assert isinstance(response, AcceptanceNotCommitted) and response.explanation.strip()
    assert j.commits == () and j.accepted is INITIAL


def test_an_event_over_a_superseded_head_is_not_committed():
    j = journal()
    _commit(j, EVENT_1)
    stale = make_event(INITIAL, "r9", acceptance_id="acceptance-late")
    assert isinstance(_commit(j, stale), AcceptanceNotCommitted)
    assert j.accepted == AFTER_1 and len(j.commits) == 1


def test_wrong_run_commit_is_not_committed():
    j = journal()
    response = _commit(j, make_event(INITIAL, "r2", run_id=RunId("run-2")))
    assert isinstance(response, AcceptanceNotCommitted) and "run" in response.explanation
    assert j.commits == ()


def test_commit_checks_the_event_type_before_storage():
    j = journal()
    with pytest.raises(ValueError):
        j.commit(AcceptanceQuery(RUN, EVENT_1.acceptance_id))
    assert j.commits == ()


# --- queries -----------------------------------------------------------------------------------------


def test_query_finds_the_stored_event_and_its_historical_reference():
    j = journal()
    first = committed(_commit(j, EVENT_1))
    _commit(j, EVENT_2)
    found = _query(j, EVENT_1)
    assert isinstance(found, AcceptanceFound)
    assert found.event is first.event and found.accepted is first.accepted  # historical, not the head
    assert found.accepted == AFTER_1 != j.accepted


def test_query_reports_definite_absence_for_unknown_and_rejected_ids():
    j = journal()
    assert _query(j, EVENT_1) == AcceptanceAbsent(RUN, EVENT_1.acceptance_id)
    stale = make_event(AcceptedReference(INITIAL.state, 4), "r2", acceptance_id="acceptance-rejected")
    assert isinstance(_commit(j, stale), AcceptanceNotCommitted)
    assert isinstance(_query(j, stale), AcceptanceAbsent)


def test_wrong_run_query_cannot_establish_authority():
    j = journal()
    _commit(j, EVENT_1)
    response = _query(j, EVENT_1, run_id=RunId("run-2"))
    assert isinstance(response, AcceptanceAuthorityUnknown)
    assert (response.run_id, response.acceptance_id) == (RunId("run-2"), EVENT_1.acceptance_id)
    assert "run" in response.explanation


def test_query_checks_the_request_type():
    with pytest.raises(ValueError):
        journal().query(EVENT_1)


def test_a_fresh_instance_has_no_history_for_the_same_run():
    first = journal()
    _commit(first, EVENT_1)
    second = journal()
    assert second.commits == () and second.accepted is INITIAL
    assert isinstance(_query(second, EVENT_1), AcceptanceAbsent)
    assert second.durability == "non_durable"


# --- concurrency -------------------------------------------------------------------------------------


def test_competing_events_on_the_same_head_commit_exactly_once():
    j = journal()
    events = (make_event(INITIAL, "r2", gain_db=-3.0, acceptance_id="acceptance-a"),
              make_event(INITIAL, "r2", gain_db=-6.0, acceptance_id="acceptance-b"))  # fmt: skip
    barrier = threading.Barrier(len(events))
    responses = [None] * len(events)

    def submit(index):
        barrier.wait()
        responses[index] = j.commit(events[index])

    threads = [threading.Thread(target=submit, args=(i,)) for i in range(len(events))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for event, response in zip(events, responses, strict=True):
        validate_acceptance_commit_response(event, response)
    winners = [r for r in responses if isinstance(r, AcceptanceCommitted)]
    losers = [r for r in responses if isinstance(r, AcceptanceNotCommitted)]
    assert len(winners) == 1 and len(losers) == 1
    assert j.commits == (winners[0],)
    assert j.accepted == winners[0].accepted == AcceptedReference(winners[0].event.assessment.assessed.state, 1)


# --- lost acknowledgements (test-only wrappers) ------------------------------------------------------


class _AcknowledgementLostAfterWrite:
    """Writes through to the journal, then reports an unknown outcome to the caller."""

    def __init__(self, inner):
        self.inner = inner

    def commit(self, event):
        self.inner.commit(event)
        return AcceptanceCommitUnknown(event.acceptance_id, "acknowledgement lost after the write")

    def query(self, request):
        return self.inner.query(request)


class _UnknownWithoutWrite:
    """Reports an unknown outcome without writing anything."""

    def __init__(self, inner):
        self.inner = inner

    def commit(self, event):
        return AcceptanceCommitUnknown(event.acceptance_id, "request lost before the write")

    def query(self, request):
        return self.inner.query(request)


def test_a_lost_acknowledgement_is_resolved_as_committed_by_query():
    inner = journal()
    wrapped: AcceptanceJournal = _AcknowledgementLostAfterWrite(inner)
    unknown = _commit(wrapped, EVENT_1)
    assert isinstance(unknown, AcceptanceCommitUnknown)
    found = _query(wrapped, EVENT_1)
    assert isinstance(found, AcceptanceFound) and found.event is EVENT_1 and found.accepted == AFTER_1
    # Resubmitting the exact event returns the original acknowledgement instead of a second commit.
    assert _commit(inner, EVENT_1) is inner.commits[0] and len(inner.commits) == 1


def test_an_unknown_outcome_without_a_write_is_resolved_as_absent_by_query():
    inner = journal()
    wrapped: AcceptanceJournal = _UnknownWithoutWrite(inner)
    assert isinstance(_commit(wrapped, EVENT_1), AcceptanceCommitUnknown)
    assert _query(wrapped, EVENT_1) == AcceptanceAbsent(RUN, EVENT_1.acceptance_id)
    assert inner.commits == () and inner.accepted is INITIAL
