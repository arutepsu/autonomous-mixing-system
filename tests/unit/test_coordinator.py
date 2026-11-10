"""Connected lifecycle tests for the controlled coordinator (plan 2026-11-09 controlled-coordinator composition).

Every collaborator here is an explicitly labelled controlled double: metric values, gains, diagnosis
candidates and decisions are scripted metadata, not musical analysis, detection or LLM reasoning. The
journal is the real non-durable ``InMemoryRunJournal``; fault wrappers below are test-only. These tests
demonstrate orchestration (call order, recording, charging, authority and state selection), not
scientific diagnosis, perceptual improvement, durability or FL Studio integration.
"""

import dataclasses
from datetime import timedelta

import pytest
from test_run_recording_contracts import (
    ANALYZER,
    IN_T1,
    METRIC,
    P1,
    SCOPE,
    SPEC,
    STATE,
    T1,
    T2,
    UNIT,
    UTC_NOW,
    _configuration,
    _run_request,
)

from autonomous_mixing.adapters.journal.run_memory import InMemoryRunJournal
from autonomous_mixing.core.loop.coordinator import run
from autonomous_mixing.core.records.acceptance import (
    AcceptanceAuthorityUnknown,
    AcceptanceCommitUnknown,
    AcceptanceEvent,
    AcceptanceNotCommitted,
    AcceptedReference,
)
from autonomous_mixing.core.records.actions import Action, ExecutionIssue, ExecutionStatus, SetTrackGain
from autonomous_mixing.core.records.artifacts import ArtifactKind
from autonomous_mixing.core.records.budgets import BudgetBlock, BudgetLimits
from autonomous_mixing.core.records.coordinator_runtime import ClockReading, RenderPreparationFailure
from autonomous_mixing.core.records.decision import (
    Abstain,
    AbstentionReason,
    ActionJustification,
    Decision,
    DecisionResponse,
    NoChange,
    ProposeAction,
)
from autonomous_mixing.core.records.diagnosis import (
    DiagnosisCoverage,
    DiagnosisCoverageStatus,
    DiagnosisReport,
    DiagnosticEvidence,
    ProblemCandidate,
    ProblemFamily,
    UncertaintyRepresentation,
)
from autonomous_mixing.core.records.feedback import (
    ActionFeedbackDisposition,
    ContextRequestFeedback,
    DecisionFeedback,
    NoChangeFeedback,
    PortFailureFeedback,
)
from autonomous_mixing.core.records.identifiers import (
    ActionId,
    ArtifactId,
    DecisionId,
    DiagnosisCoverageId,
    ObservationId,
    ProblemCandidateId,
    RunId,
    VersionRef,
)
from autonomous_mixing.core.records.integrity import ArtifactIntegrityResult, IntegrityStatus, StateIntegrityReport
from autonomous_mixing.core.records.observation import MeasuredScalar, Observation, Unavailable
from autonomous_mixing.core.records.reconciliation import ReconciliationOutcome, ReconciliationResult
from autonomous_mixing.core.records.rendering import RenderedCandidate, RenderEvidence, RenderFailure
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success
from autonomous_mixing.core.records.run import FinalProjectState, RunOutcome
from autonomous_mixing.core.records.run_report import RunReport
from autonomous_mixing.core.records.run_trace import (
    DispatchIntent,
    DispatchSkipped,
    OperationKind,
    OperationObserved,
    TraceAppended,
    TraceAppendUnknown,
    TraceNotAppended,
)
from autonomous_mixing.core.records.termination import TerminationReason as R

# Criteria of the shared fixture (labelled values, no musical meaning): target metric-a <= 0.0; improvement
# LOWER by >= 1.0 from before; INITIAL delta guard |x - initial| <= 2.0. Initial value 1.5 is UNMET.
INITIAL_VALUE = 1.5
SATISFYING, INTERMEDIATE, REJECTED_VALUE = 0.0, 0.4, 1.4


# --- controlled collaborators ------------------------------------------------------------------------


class Clock:
    """Monotonic steps of ``step`` seconds per read; scripted overrides by read index (0-based)."""

    def __init__(self, step=0.001, *, cancel_from=None, values=None):
        self.step, self.cancel_from, self.values, self.reads = step, cancel_from, values or {}, 0

    def read(self):
        index = self.reads
        self.reads += 1
        monotonic = self.values.get(index, index * self.step)
        if not isinstance(monotonic, float | int):
            return monotonic  # deliberately invalid reading
        cancelled = self.cancel_from is not None and index >= self.cancel_from
        return ClockReading(monotonic, UTC_NOW + timedelta(seconds=max(monotonic, 0)), cancelled)


class Identities:
    def __init__(self, overrides=None):
        self.count, self.overrides = 0, overrides or {}

    def next_token(self):
        self.count += 1
        return self.overrides.get(self.count, f"id-{self.count}")


def _mix_of(state):
    return next(a for a in state.audio.artifacts if a.kind is ArtifactKind.MIX)


class Analyzer:
    """Returns one metric-a observation over the state's current mix; values scripted per call."""

    def __init__(self, values):
        self.values, self.requests = list(values), []

    def analyze(self, request):
        self.requests.append(request)
        value = self.values[len(self.requests) - 1]
        if callable(value):
            return value(request)
        if isinstance(value, PortFailure | str):
            return value
        result = value if isinstance(value, Unavailable) else MeasuredScalar(value, UNIT)
        state = request.state
        observation = Observation(
            ObservationId(f"obs-{state.revision}"), METRIC, (_mix_of(state).artifact_id,), SCOPE, result, SPEC,
            ANALYZER, UTC_NOW,
        )  # fmt: skip
        from autonomous_mixing.core.records.analysis import AnalysisResponse

        return Success(AnalysisResponse((observation,)))


class Diagnosis:
    """One labelled ASSESSED candidate citing the state's observations (no detection method is implied)."""

    def __init__(self, *, insufficient=False, failures=None):
        self.insufficient, self.failures, self.requests = insufficient, failures or {}, []

    def diagnose(self, request):
        self.requests.append(request)
        scripted = self.failures.get(len(self.requests))
        if scripted is not None:
            return scripted
        state, method = request.state, VersionRef("labelled-method", "0")
        coverage = DiagnosisCoverage(
            DiagnosisCoverageId("cov-1"), ProblemFamily.MASKING, request.scope, DiagnosisCoverageStatus.ASSESSED,
            method, "labelled fixture coverage",
        )  # fmt: skip
        evidence = DiagnosticEvidence(
            "labelled", "labelled", UncertaintyRepresentation.QUALITATIVE, self.insufficient, (),
            ("labelled missing information",) if self.insufficient else (),
        )  # fmt: skip
        candidate = ProblemCandidate(
            ProblemCandidateId(f"cand-{state.revision}"), coverage.coverage_id, state.project_id, state.revision,
            ProblemFamily.MASKING, request.scope, tuple(o.observation_id for o in request.observations), method,
            evidence,
        )  # fmt: skip
        return Success(DiagnosisReport(state.project_id, state.revision, (coverage,), (candidate,)))


def _decision(situation, outcome, number):
    candidates = situation.diagnosis.candidates
    return Decision(
        DecisionId(f"decision-{number}"), situation.state.project_id, situation.state.revision,
        situation.context.context_id, situation.context.version,
        tuple(o for c in candidates for o in c.evidence_ids) if isinstance(outcome, ProposeAction) else (),
        "labelled controlled response", situation.controller, outcome,
    )  # fmt: skip


def propose(gain_db, track=T1):
    def respond(situation, number):
        action = Action(ActionId(f"action-{number}"), P1, situation.state.revision, SetTrackGain(track, gain_db))
        candidates = tuple(c.candidate_id for c in situation.diagnosis.candidates)
        outcome = ProposeAction(action, "labelled expected effect", candidates,
                                ActionJustification("labelled", "labelled", (), ()))  # fmt: skip
        return Success(DecisionResponse(_decision(situation, outcome, number)))

    return respond


def no_change(situation, number):
    return Success(DecisionResponse(_decision(situation, NoChange("labelled keep"), number)))


def abstain(reason, questions=()):
    def respond(situation, number):
        return Success(DecisionResponse(_decision(situation, Abstain(reason, questions), number)))

    return respond


def fail(code=PortFailureCode.UNAVAILABLE, retryable=True):
    return lambda situation, number: PortFailure(code, "labelled port failure", retryable)


class Decider:
    def __init__(self, *responses):
        self.responses, self.situations = list(responses), []

    def decide(self, situation):
        self.situations.append(situation)
        return self.responses[len(self.situations) - 1](situation, len(self.situations))


class Renderer:
    """Builds the plan's outputs (metadata only) or returns a scripted outcome per call."""

    def __init__(self, outcomes=None):
        self.outcomes, self.requests = outcomes or {}, []

    def process(self, request):
        self.requests.append(request)
        scripted = self.outcomes.get(len(self.requests))
        if scripted is not None:
            return scripted
        plan = request.plan
        token = plan.workspace.directory_token

        def output(aid, tracks, kind):
            source = plan.fixed_target_input
            return dataclasses.replace(
                source, artifact_id=aid, revision=plan.candidate.revision, source_track_ids=tracks, kind=kind,
                location=f"renders/{token}/{aid}.wav", content_hash="c" * 64, render_spec=plan.render_spec.ref,
            )  # fmt: skip

        target = output(plan.candidate.target_artifact_id, (plan.target_track_id,), ArtifactKind.STEM)
        mix = output(plan.candidate.mix_artifact_id, plan.recipe.track_ids, ArtifactKind.MIX)
        others = tuple(s.artifact_id for s in plan.current_stems if s.artifact_id != plan.target_current_artifact_id)
        processor = VersionRef("labelled-processor", "0")
        evidence = (
            RenderEvidence(target.artifact_id, (plan.fixed_target_input.artifact_id,), True, True, 0.5, processor,
                           plan.processing_spec),
            RenderEvidence(mix.artifact_id, (target.artifact_id, *others), True, True, 0.5, processor,
                           plan.processing_spec),
        )  # fmt: skip
        return RenderedCandidate(target, mix, evidence)


class Inspector:
    def __init__(self, statuses=None):
        self.statuses, self.requests = list(statuses or []), []

    def inspect(self, request):
        self.requests.append(request)
        index = len(self.requests) - 1
        status = self.statuses[index] if index < len(self.statuses) else IntegrityStatus.VERIFIED
        if not isinstance(status, IntegrityStatus):
            return status
        results = tuple(
            ArtifactIntegrityResult(a, status, a.content_hash if status is IntegrityStatus.VERIFIED else None,
                                    f"labelled claim: {status.value}")
            for a in request.required_artifacts
        )  # fmt: skip
        return StateIntegrityReport(request, results)


class FaultyJournal:
    """Test-only wrapper over the real journal: scripted append/commit/snapshot faults by predicate."""

    def __init__(self, inner, *, append_fault=None, commit_fault=None, query_fault=None, snapshot_fault=None):
        self.inner, self.append_fault, self.commit_fault = inner, append_fault, commit_fault
        self.query_fault, self.snapshot_fault = query_fault, snapshot_fault
        self.appended = []

    def append(self, event):
        self.appended.append(event)
        fault = self.append_fault(event) if self.append_fault else None
        if fault == "refuse":
            return TraceNotAppended(event.event_id, "labelled refusal")
        if fault == "unknown-before-write":
            return TraceAppendUnknown(event.event_id, "labelled lost request")
        response = self.inner.append(event)
        if fault == "unknown-after-write":
            return TraceAppendUnknown(event.event_id, "labelled lost acknowledgement")
        return response

    def commit(self, event):
        fault = self.commit_fault
        if fault == "refuse":
            return AcceptanceNotCommitted(event.acceptance_id, "labelled refusal")
        if fault == "unknown-before-write":
            return AcceptanceCommitUnknown(event.acceptance_id, "labelled lost request")
        response = self.inner.commit(event)
        if fault == "unknown-after-write":
            return AcceptanceCommitUnknown(event.acceptance_id, "labelled lost acknowledgement")
        return response

    def query(self, request):
        if self.query_fault == "unknown":
            return AcceptanceAuthorityUnknown(request.run_id, request.acceptance_id, "labelled unknown authority")
        return self.inner.query(request)

    def snapshot(self):
        if self.snapshot_fault:
            return PortFailure(PortFailureCode.UNAVAILABLE, "labelled snapshot failure", False)
        return self.inner.snapshot()


class Probe:
    """Forwards to the journal under test and notes the clock read index after each first intent."""

    def __init__(self, inner, clock, read_after_intent):
        self.inner, self.clock, self.read_after_intent = inner, clock, read_after_intent

    def append(self, event):
        if isinstance(event.payload, DispatchIntent):
            self.read_after_intent.setdefault(event.payload.operation, self.clock.reads)
        return self.inner.append(event)

    def commit(self, event):
        return self.inner.commit(event)

    def query(self, request):
        return self.inner.query(request)

    def snapshot(self):
        return self.inner.snapshot()


def limits(**overrides):
    fields = dict(max_decision_calls=4, max_attempts=3, max_render_calls=3, max_recovery_operations=2,
                  max_elapsed_seconds=60.0)  # fmt: skip
    fields.update(overrides)
    return BudgetLimits(**fields)


def request_with(**limit_overrides):
    return _run_request(configuration=_configuration(budget_limits=limits(**limit_overrides)))


class Harness:
    def __init__(self, *, analysis=(INITIAL_VALUE,), decisions=(), request=None, renderer=None, inspector=None,
                 diagnosis=None, clock=None, identities=None, journal=None):  # fmt: skip
        self.request = request or request_with()
        self.analyzer = Analyzer(analysis)
        self.diagnosis = diagnosis or Diagnosis()
        self.decider = Decider(*decisions)
        self.renderer = renderer or Renderer()
        self.inspector = inspector or Inspector()
        self.clock = clock or Clock()
        self.identities = identities or Identities()
        self.real_journal = InMemoryRunJournal(self.request.run_id, AcceptedReference(self.request.initial_state, 0))
        self.journal = journal(self.real_journal) if journal else self.real_journal
        self.read_after_intent = {}

    def run(self) -> RunReport:
        journal = self.journal
        if not isinstance(journal, Probe):
            journal = Probe(journal, self.clock, self.read_after_intent)
        return run(
            self.request, analyzer=self.analyzer, diagnosis_source=self.diagnosis, decision_maker=self.decider,
            renderer=self.renderer, integrity_inspector=self.inspector, journal=journal, clock=self.clock,
            identities=self.identities,
        )  # fmt: skip


def payloads(report, kind):
    return [e.payload for e in report.trace.events if isinstance(e.payload, kind)]


def operations(report):
    return [p.operation for p in payloads(report, DispatchIntent)]


def outcome(report):
    return report.final_run.outcome, report.final_run.termination_reason


def usage_counts(report):
    u = report.final_run.usage
    return u.decision_calls, u.attempts, u.render_calls, u.recovery_operations


A, D, DC, RN, Q, INS = (OperationKind.ANALYZE, OperationKind.DIAGNOSE, OperationKind.DECIDE, OperationKind.RENDER,
                      OperationKind.QUERY_ACCEPTANCE, OperationKind.INSPECT_STATE)  # fmt: skip
CANDIDATE_LOOP = [INS, A, D, DC, RN, A]


# --- Task 1: entry point and initial success ---------------------------------------------------------


def test_initial_success_runs_no_decision_render_or_acceptance():
    harness = Harness(analysis=(-0.5,))
    report = harness.run()
    assert isinstance(report, RunReport)
    assert outcome(report) == (RunOutcome.SUCCEEDED, R.INITIAL_CRITERIA_MET)
    assert report.final_run.final_project_state is FinalProjectState.UNCHANGED
    assert operations(report) == [INS, A]
    assert len(harness.analyzer.requests) == 1 and harness.decider.situations == []
    assert harness.renderer.requests == [] and harness.diagnosis.requests == []
    assert report.authoritative == AcceptedReference(STATE, 0) == report.verified
    assert usage_counts(report) == (0, 0, 0, 0)
    assert isinstance(report.terminal_append, TraceAppended) and report.unacknowledged_events == ()
    assert report.trace.events[0].payload == harness.request
    assert report.trace.acceptances == () and report.durability == "non_durable"


def test_initial_integrity_failure_terminates_before_analysis():
    for status, reason in ((IntegrityStatus.FAILED, R.ACCEPTED_STATE_INTEGRITY_FAILED),
                           (IntegrityStatus.UNCERTAIN, R.ACCEPTED_STATE_INTEGRITY_UNCERTAIN)):  # fmt: skip
        harness = Harness(inspector=Inspector([status]))
        report = harness.run()
        assert outcome(report) == (RunOutcome.FAILED, reason)
        assert report.final_run.final_project_state is FinalProjectState.UNKNOWN
        assert report.authoritative == AcceptedReference(STATE, 0) and report.verified is None
        assert harness.analyzer.requests == []


def test_initial_assessment_inconclusive_terminates():
    report = Harness(analysis=(Unavailable("labelled unavailable"),)).run()
    assert outcome(report) == (RunOutcome.FAILED, R.INITIAL_ASSESSMENT_INCONCLUSIVE)
    assert report.final_run.final_project_state is FinalProjectState.UNCHANGED


@pytest.mark.parametrize(
    "journal_for",
    [
        lambda request: InMemoryRunJournal(RunId("other-run"), AcceptedReference(request.initial_state, 0)),
        lambda request: InMemoryRunJournal(
            request.run_id,
            AcceptedReference(
                dataclasses.replace(
                    request.initial_state,
                    snapshot=dataclasses.replace(request.initial_state.snapshot, captured_at=UTC_NOW - timedelta(1)),
                ),
                0,
            ),
        ),  # fmt: skip
    ],
    ids=["other-run", "other-initial-state"],
)
def test_a_journal_bound_to_another_run_or_state_is_rejected_before_any_operation(journal_for):
    harness = Harness()
    harness.journal = journal_for(harness.request)
    with pytest.raises(ValueError, match="journal"):
        harness.run()
    assert harness.inspector.requests == [] and harness.analyzer.requests == []


def test_a_journal_with_an_existing_trace_is_not_resumed():
    first = Harness(analysis=(-0.5,))
    first.run()
    second = Harness(analysis=(-0.5,))
    second.journal = first.journal
    with pytest.raises(ValueError, match="journal"):
        second.run()
    assert second.inspector.requests == []


# --- Task 2: connected decision and correction lifecycle ---------------------------------------------


def test_one_accepted_correction_satisfies_the_target():
    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),))
    report = harness.run()
    assert outcome(report) == (RunOutcome.SUCCEEDED, R.CANDIDATE_CRITERIA_MET)
    assert report.final_run.final_project_state is FinalProjectState.ACCEPTED_EDITS_RETAINED
    assert operations(report) == CANDIDATE_LOOP
    assert usage_counts(report) == (1, 1, 1, 0)
    (commit,) = report.trace.acceptances
    candidate = commit.accepted.state
    assert report.authoritative == commit.accepted == report.verified and commit.accepted.sequence == 1
    plan = harness.renderer.requests[0].plan
    assert plan.fixed_target_input == IN_T1 and plan.requested_gain_db == -3.0
    assert plan.workspace.root_key == "renders" and plan.before_revision == STATE.revision
    assert {a.artifact_id for a in candidate.audio.artifacts} == {
        plan.candidate.target_artifact_id, plan.candidate.mix_artifact_id, *[
            s.artifact_id for s in plan.current_stems if s.artifact_id != plan.target_current_artifact_id]}  # fmt: skip
    assert [g.gain_db for g in candidate.processing.track_gains if g.track_id == T1] == [-3.0]
    situation = harness.decider.situations[0]
    assert situation.state == STATE and situation.history.entries == () and situation.usage.decision_calls == 1
    (feedback,) = payloads(report, DecisionFeedback)
    assert feedback.outcome.disposition is ActionFeedbackDisposition.ACCEPTED
    assert (feedback.before_revision, feedback.selected_revision) == (STATE.revision, candidate.revision)


def test_intermediate_progress_then_no_change_keeps_the_accepted_edit_and_history():
    harness = Harness(analysis=(INITIAL_VALUE, INTERMEDIATE), decisions=(propose(-2.0), no_change))
    report = harness.run()
    assert outcome(report) == (RunOutcome.ABSTAINED, R.NO_CHANGE_PROPOSED)
    assert report.final_run.final_project_state is FinalProjectState.ACCEPTED_EDITS_RETAINED
    first, second = harness.decider.situations
    candidate = report.trace.acceptances[0].accepted.state
    assert second.state == candidate and second.usage.decision_calls == 2
    (entry,) = second.history.entries
    assert entry.outcome.disposition is ActionFeedbackDisposition.ACCEPTED
    assert (entry.before_revision, entry.selected_revision) == (STATE.revision, candidate.revision)
    assert [o.observation_id for o in second.observations] == [ObservationId(f"obs-{candidate.revision}")]
    revisions = {(i.state_revision, i.observation.observation_id) for i in entry.outcome.observations}
    assert (STATE.revision, ObservationId(f"obs-{STATE.revision}")) in revisions  # original state association
    assert len(harness.diagnosis.requests) == 2 and len(harness.analyzer.requests) == 2


def test_repeated_edits_use_the_fixed_input_and_the_latest_other_stems():
    harness = Harness(analysis=(INITIAL_VALUE, INTERMEDIATE, -0.1), decisions=(propose(-2.0), propose(-4.0)))
    report = harness.run()
    assert outcome(report) == (RunOutcome.SUCCEEDED, R.CANDIDATE_CRITERIA_MET)
    first, second = (r.plan for r in harness.renderer.requests)
    assert second.fixed_target_input == IN_T1 == first.fixed_target_input  # never the rendered target
    assert second.requested_gain_db == -4.0  # absolute value, applied once to the fixed input
    assert second.target_current_artifact_id == first.candidate.target_artifact_id
    assert second.current_mix_artifact_id == first.candidate.mix_artifact_id
    assert second.before_revision == first.candidate.revision
    roots = {b.artifact_id: b.root_key for b in harness.renderer.requests[1].before.bindings}
    assert roots[first.candidate.target_artifact_id] == "renders" and roots[IN_T1.artifact_id] == "exports"
    assert [c.accepted.sequence for c in report.trace.acceptances] == [1, 2]
    assert usage_counts(report) == (2, 2, 2, 0)


def test_rejected_candidate_is_recovered_before_another_decision():
    harness = Harness(analysis=(INITIAL_VALUE, REJECTED_VALUE, SATISFYING),
                      decisions=(propose(-1.0), propose(-3.0)))  # fmt: skip
    report = harness.run()
    assert outcome(report) == (RunOutcome.SUCCEEDED, R.CANDIDATE_CRITERIA_MET)
    assert operations(report) == [INS, A, D, DC, RN, A, INS, DC, RN, A]
    assert usage_counts(report) == (2, 2, 2, 1)
    recovery = harness.inspector.requests[1]
    assert recovery.state == STATE  # the saved accepted state, not the rejected candidate
    second = harness.decider.situations[1]
    assert second.state == STATE
    (entry,) = second.history.entries
    assert entry.outcome.disposition is ActionFeedbackDisposition.ROLLED_BACK
    assert entry.selected_revision == STATE.revision and entry.candidate_revision is not None
    assert len(report.trace.acceptances) == 1


# --- non-action outcomes and context -----------------------------------------------------------------


def test_missing_context_ends_the_run_with_recorded_questions():
    questions = ("Which track carries the lead role?",)
    harness = Harness(decisions=(abstain(AbstentionReason.MISSING_CONTEXT, questions),))
    report = harness.run()
    assert outcome(report) == (RunOutcome.ABSTAINED, R.MISSING_CONTEXT)
    assert questions[0] in report.final_run.termination_explanation
    feedback = payloads(report, DecisionFeedback)
    assert isinstance(feedback[-1].outcome, ContextRequestFeedback) and feedback[-1].outcome.questions == questions
    assert usage_counts(report) == (1, 0, 0, 0)


def test_context_differences_between_runs_reach_the_controlled_decision():
    def by_context(situation, number):
        roles = {r.track_id: r.role for r in situation.context.track_roles}
        return propose(-3.0)(situation, number) if roles[T1] == "role-a" else no_change(situation, number)

    from autonomous_mixing.core.records.context import AssertionSource, MusicalContext, TrackRole

    source = AssertionSource("labelled-fixture", UTC_NOW)
    other = MusicalContext(_configuration().context.context_id, "2",
                           (TrackRole(T1, "role-z", source), TrackRole(T2, "role-b", source)), (), frozenset())  # fmt: skip
    first = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(by_context,))
    second = Harness(decisions=(by_context,),
                     request=_run_request(run_id=RunId("run-2"),
                                          configuration=_configuration(budget_limits=limits(), context=other)))  # fmt: skip
    assert outcome(first.run()) == (RunOutcome.SUCCEEDED, R.CANDIDATE_CRITERIA_MET)
    assert outcome(second.run()) == (RunOutcome.ABSTAINED, R.NO_CHANGE_PROPOSED)
    assert second.decider.situations[0].context.version == "2"


@pytest.mark.parametrize(
    "reason, expected",
    [
        (AbstentionReason.INSUFFICIENT_EVIDENCE, R.INSUFFICIENT_EVIDENCE),
        (AbstentionReason.CONFLICTING_CONSTRAINTS, R.CONFLICTING_CONSTRAINTS),
        (AbstentionReason.NO_JUSTIFIED_ACTION, R.NO_JUSTIFIED_ACTION),
    ],
)
def test_abstentions_finish_with_their_reason(reason, expected):
    report = Harness(decisions=(abstain(reason),)).run()
    assert outcome(report) == (RunOutcome.ABSTAINED, expected)
    assert usage_counts(report) == (1, 0, 0, 0)


def test_no_change_finishes_without_charging_an_attempt():
    report = Harness(decisions=(no_change,)).run()
    assert outcome(report) == (RunOutcome.ABSTAINED, R.NO_CHANGE_PROPOSED)
    assert usage_counts(report) == (1, 0, 0, 0)
    assert isinstance(payloads(report, DecisionFeedback)[-1].outcome, NoChangeFeedback)


def test_evidence_blocked_proposal_charges_an_attempt_and_ends_as_insufficient_evidence():
    harness = Harness(decisions=(propose(-3.0),), diagnosis=Diagnosis(insufficient=True))
    report = harness.run()
    assert outcome(report) == (RunOutcome.ABSTAINED, R.INSUFFICIENT_EVIDENCE)
    assert usage_counts(report) == (1, 1, 0, 0) and harness.renderer.requests == []


def test_unavailable_capability_is_rejected_and_a_new_decision_follows():
    harness = Harness(decisions=(propose(-3.0, track=T2), no_change))
    report = harness.run()
    assert outcome(report) == (RunOutcome.ABSTAINED, R.NO_CHANGE_PROPOSED)
    assert usage_counts(report) == (2, 1, 0, 0) and harness.renderer.requests == []
    (entry,) = harness.decider.situations[1].history.entries
    assert entry.outcome.disposition is ActionFeedbackDisposition.REJECTED


def test_renderer_bound_narrower_than_policy_is_enforced_from_the_capability_view():
    from autonomous_mixing.core.records.capabilities import GainBounds, RendererCapabilities

    narrow = RendererCapabilities(VersionRef("renderer", "narrow"), GainBounds(-2.0, 0.0))
    harness = Harness(decisions=(propose(-3.0), no_change),
                      request=_run_request(configuration=_configuration(budget_limits=limits()),
                                           renderer_capabilities=narrow))  # fmt: skip
    report = harness.run()
    assert outcome(report) == (RunOutcome.ABSTAINED, R.NO_CHANGE_PROPOSED)
    assert harness.renderer.requests == []
    (entry,) = harness.decider.situations[1].history.entries
    assert entry.outcome.disposition is ActionFeedbackDisposition.REJECTED


# --- invalid responses, retries and limits -----------------------------------------------------------


def test_invalid_decision_response_terminates_without_an_attempt():
    def wrong_controller(situation, number):
        decision = dataclasses.replace(_decision(situation, NoChange("x"), number),
                                       controller=type(situation.controller)("other", "1"))  # fmt: skip
        return Success(DecisionResponse(decision))

    for respond in (wrong_controller, lambda s, n: "not a port result", lambda s, n: Success("not a response")):
        report = Harness(decisions=(respond,)).run()
        assert outcome(report) == (RunOutcome.FAILED, R.INVALID_DECISION_RESPONSE)
        assert usage_counts(report) == (1, 0, 0, 0)


def test_retryable_unavailable_failure_is_retried_with_a_new_call_and_recorded_feedback():
    harness = Harness(decisions=(fail(), no_change))
    report = harness.run()
    assert outcome(report) == (RunOutcome.ABSTAINED, R.NO_CHANGE_PROPOSED)
    first, second = harness.decider.situations
    (entry,) = second.history.entries
    assert isinstance(entry.outcome, PortFailureFeedback) and entry.selected_revision == STATE.revision
    assert second.diagnosis == first.diagnosis and second.usage.decision_calls == 2
    calls = [p.call_id for p in payloads(report, DispatchIntent) if p.operation is DC]
    assert len(set(calls)) == 2


def test_retry_cap_exhaustion_and_non_retryable_failures():
    exhausted = Harness(decisions=(fail(), fail())).run()  # retry policy allows one retry
    assert outcome(exhausted) == (RunOutcome.FAILED, R.DECISION_RETRIES_EXHAUSTED)
    assert usage_counts(exhausted) == (2, 0, 0, 0)
    for failure in (fail(retryable=False), fail(PortFailureCode.INTERNAL_ERROR, retryable=True)):
        report = Harness(decisions=(failure,)).run()
        assert outcome(report) == (RunOutcome.FAILED, R.DECISION_FAILED)
        assert usage_counts(report) == (1, 0, 0, 0)


@pytest.mark.parametrize(
    "overrides, decisions, reason, counts",
    [
        ({"max_decision_calls": 0}, (), R.DECISION_CALL_LIMIT, (0, 0, 0, 0)),
        ({"max_attempts": 0}, (propose(-3.0),), R.ATTEMPT_LIMIT, (1, 0, 0, 0)),
        ({"max_render_calls": 0}, (propose(-3.0),), R.RENDER_CALL_LIMIT, (1, 1, 0, 0)),
        ({"max_recovery_operations": 0}, (propose(-3.0),), R.RECOVERY_CAPACITY_LIMIT, (1, 1, 0, 0)),
        ({"max_decision_calls": 1}, (fail(),), R.DECISION_CALL_LIMIT, (1, 0, 0, 0)),
    ],
)
def test_count_limits_block_the_next_operation(overrides, decisions, reason, counts):
    harness = Harness(decisions=decisions, request=request_with(**overrides))
    report = harness.run()
    assert outcome(report) == (RunOutcome.LIMIT_REACHED, reason)
    assert usage_counts(report) == counts and harness.renderer.requests == []
    assert payloads(report, BudgetBlock)


def test_cancellation_before_the_first_decision_records_no_intent():
    probe = Harness(decisions=(no_change,))
    probe.run()
    harness = Harness(decisions=(no_change,), clock=Clock(cancel_from=probe.read_after_intent[DC] - 1))
    report = harness.run()
    assert outcome(report) == (RunOutcome.CANCELLED, R.USER_CANCELLED)
    assert harness.decider.situations == [] and DC not in operations(report)
    assert usage_counts(report) == (0, 0, 0, 0)


@pytest.mark.parametrize("operation", [DC, RN])
@pytest.mark.parametrize("kind", ["cancel", "deadline"])
def test_cancellation_or_deadline_after_an_acknowledged_intent_skips_dispatch_and_keeps_the_charge(operation, kind):
    probe = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),))
    probe.run()
    # Find the clock read that follows the intent of ``operation`` and block from there on.
    target = probe_read_after_intent(probe, operation)
    clock = Clock(cancel_from=target) if kind == "cancel" else Clock(values={target: 60.0, **{
        i: 60.0 + i for i in range(target + 1, target + 50)}})  # fmt: skip
    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),), clock=clock)
    report = harness.run()
    reason = R.USER_CANCELLED if kind == "cancel" else R.ELAPSED_TIME_LIMIT
    assert report.final_run.termination_reason is reason
    skipped = [p for p in payloads(report, OperationObserved) if isinstance(p.outcome, DispatchSkipped)]
    assert skipped and skipped[0].operation is operation
    if operation is DC:
        assert harness.decider.situations == [] and usage_counts(report)[0] == 1
    else:
        assert harness.renderer.requests == [] and usage_counts(report)[2] == 1


def probe_read_after_intent(harness, operation):
    """Index of the clock read immediately after the first intent of ``operation`` (from a probe run)."""
    return harness.read_after_intent[operation]


@pytest.mark.parametrize("value", [0.0, "not a reading"], ids=["decreasing", "invalid"])
def test_decreasing_or_invalid_clock_reading_is_a_runtime_contract_failure(value):
    report = Harness(decisions=(no_change,), clock=Clock(values={4: value})).run()
    assert report.final_run.termination_reason is R.RUNTIME_CONTRACT_FAILED


# --- stage failures ----------------------------------------------------------------------------------


def test_initial_analysis_failure_and_invalid_response():
    failed = Harness(analysis=(PortFailure(PortFailureCode.UNAVAILABLE, "x", False),)).run()
    assert outcome(failed) == (RunOutcome.FAILED, R.INITIAL_ANALYSIS_FAILED)
    invalid = Harness(analysis=("not a result",)).run()
    assert outcome(invalid) == (RunOutcome.FAILED, R.RUNTIME_CONTRACT_FAILED)


def test_diagnosis_failure_and_invalid_diagnosis():
    failed = Harness(diagnosis=Diagnosis(failures={1: PortFailure(PortFailureCode.UNAVAILABLE, "x", False)})).run()
    assert outcome(failed) == (RunOutcome.FAILED, R.DIAGNOSIS_FAILED)
    invalid = Harness(diagnosis=Diagnosis(failures={1: Success("not a report")})).run()
    assert outcome(invalid) == (RunOutcome.FAILED, R.RUNTIME_CONTRACT_FAILED)


@pytest.mark.parametrize(
    "render_outcome, reason",
    [
        (RenderPreparationFailure(ExecutionIssue("prepare", "labelled"), None), R.EXECUTION_PREPARATION_FAILED),
        (RenderFailure(ExecutionStatus.FAILED, ExecutionIssue("render", "labelled")), R.RENDER_FAILED),
        (RenderFailure(ExecutionStatus.UNCERTAIN, ExecutionIssue("render", "labelled")), R.RENDER_FAILED),
        ("not an outcome", R.INVALID_RENDER_RESPONSE),
    ],
)
def test_render_failures_are_recovered_then_terminate(render_outcome, reason):
    harness = Harness(decisions=(propose(-3.0),), renderer=Renderer({1: render_outcome}))
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, reason)
    assert harness.inspector.requests[-1].state == STATE and len(harness.inspector.requests) == 2
    assert usage_counts(report) == (1, 1, 1, 1)
    assert report.final_run.final_project_state is FinalProjectState.RESTORED_TO_LAST_ACCEPTED


def test_candidate_analysis_failure_and_inconclusive_verification_recover_then_terminate():
    failed = Harness(analysis=(INITIAL_VALUE, PortFailure(PortFailureCode.UNAVAILABLE, "x", False)),
                     decisions=(propose(-3.0),)).run()  # fmt: skip
    assert outcome(failed) == (RunOutcome.FAILED, R.CANDIDATE_ANALYSIS_FAILED)
    inconclusive = Harness(analysis=(INITIAL_VALUE, Unavailable("labelled")), decisions=(propose(-3.0),)).run()
    assert outcome(inconclusive) == (RunOutcome.FAILED, R.VERIFICATION_INCONCLUSIVE)
    assert usage_counts(inconclusive)[3] == 1


@pytest.mark.parametrize(
    "status, reason",
    [(IntegrityStatus.FAILED, R.RESTORATION_FAILED), (IntegrityStatus.UNCERTAIN, R.RESTORATION_UNCERTAIN)],
)
def test_failed_or_uncertain_restoration(status, reason):
    harness = Harness(analysis=(INITIAL_VALUE, REJECTED_VALUE), decisions=(propose(-1.0),),
                      inspector=Inspector([IntegrityStatus.VERIFIED, status]))  # fmt: skip
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, reason)
    assert report.final_run.final_project_state is FinalProjectState.UNKNOWN and report.verified is None
    assert report.authoritative == AcceptedReference(STATE, 0)


def test_deadline_exhausted_before_recovery_leaves_recovery_unperformed():
    probe = Harness(analysis=(INITIAL_VALUE, REJECTED_VALUE), decisions=(propose(-1.0), no_change))
    probe.run()
    target = probe.read_after_intent[RN] + 1  # the render returns, then time runs out
    clock = Clock(values={i: 60.0 + i for i in range(target, target + 60)})
    harness = Harness(analysis=(INITIAL_VALUE, REJECTED_VALUE), decisions=(propose(-1.0),), clock=clock)
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, R.REQUIRED_RECOVERY_NOT_PERFORMED)
    assert len(harness.inspector.requests) == 1 and report.final_run.final_project_state is FinalProjectState.UNKNOWN


# --- acceptance authority and reconciliation ---------------------------------------------------------


def test_definitely_refused_acceptance_recovers_before_and_reports_recording_failure():
    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),),
                      journal=lambda inner: FaultyJournal(inner, commit_fault="refuse"))  # fmt: skip
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, R.RECORDING_FAILED)
    assert report.trace.acceptances == () and report.authoritative == AcceptedReference(STATE, 0)
    assert harness.inspector.requests[-1].state == STATE and usage_counts(report)[3] == 1


def test_lost_acknowledgement_after_a_real_commit_is_confirmed_and_followed():
    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),),
                      journal=lambda inner: FaultyJournal(inner, commit_fault="unknown-after-write"))  # fmt: skip
    report = harness.run()
    assert outcome(report) == (RunOutcome.SUCCEEDED, R.CANDIDATE_CRITERIA_MET)
    (commit,) = report.trace.acceptances
    assert report.authoritative == commit.accepted == report.verified
    assert operations(report)[-2:] == [Q, INS] and harness.inspector.requests[-1].state == commit.accepted.state
    (result,) = payloads(report, ReconciliationResult)
    assert result.outcome is ReconciliationOutcome.CONFIRMED_ACCEPTANCE
    assert usage_counts(report)[3] == 1


def test_lost_request_before_commit_is_reconciled_as_absent_and_reports_recording_failure():
    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),),
                      journal=lambda inner: FaultyJournal(inner, commit_fault="unknown-before-write"))  # fmt: skip
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, R.RECORDING_FAILED)
    (result,) = payloads(report, ReconciliationResult)
    assert result.outcome is ReconciliationOutcome.VERIFIED_PREVIOUS_STATE
    assert report.final_run.final_project_state is FinalProjectState.RESTORED_TO_LAST_ACCEPTED


def test_unknown_authority_reports_acceptance_uncertainty():
    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),),
                      journal=lambda inner: FaultyJournal(inner, commit_fault="unknown-after-write",
                                                          query_fault="unknown"))  # fmt: skip
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, R.ACCEPTANCE_COMMIT_UNCERTAIN)
    assert report.authoritative is None and report.verified is None
    assert report.final_run.final_project_state is FinalProjectState.UNKNOWN
    (result,) = payloads(report, ReconciliationResult)
    assert result.outcome is ReconciliationOutcome.AUTHORITY_UNCERTAIN
    assert len(harness.inspector.requests) == 1  # no integrity check substitutes for unknown authority


def test_confirmed_candidate_with_failed_integrity_keeps_candidate_authority():
    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),),
                      inspector=Inspector([IntegrityStatus.VERIFIED, IntegrityStatus.FAILED]),
                      journal=lambda inner: FaultyJournal(inner, commit_fault="unknown-after-write"))  # fmt: skip
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, R.ACCEPTED_STATE_INTEGRITY_FAILED)
    (commit,) = report.trace.acceptances
    assert report.authoritative == commit.accepted and report.verified is None


# --- recording failures and finalization -------------------------------------------------------------


def _is_intent(kind):
    return lambda event: isinstance(event.payload, DispatchIntent) and event.payload.operation is kind


@pytest.mark.parametrize("fault, charged", [("refuse", 0), ("unknown-before-write", 1), ("unknown-after-write", 1)])
def test_unacknowledged_decision_intent_never_dispatches(fault, charged):
    harness = Harness(decisions=(no_change,),
                      journal=lambda inner: FaultyJournal(inner, append_fault=lambda e: fault if _is_intent(DC)(e)
                                                          else None))  # fmt: skip
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, R.RECORDING_FAILED)
    assert harness.decider.situations == [] and usage_counts(report)[0] == charged
    assert any(_is_intent(DC)(e) for e in report.unacknowledged_events)


def test_result_recording_failure_after_commit_retains_the_accepted_edit():
    def after_commit(event):
        return "refuse" if type(event.payload).__name__ == "AcceptanceCommitted" else None

    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),),
                      journal=lambda inner: FaultyJournal(inner, append_fault=after_commit))  # fmt: skip
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, R.RECORDING_FAILED)
    (commit,) = report.trace.acceptances
    assert report.authoritative == commit.accepted == report.verified
    assert report.final_run.final_project_state is FinalProjectState.ACCEPTED_EDITS_RETAINED


@pytest.mark.parametrize("fault", ["refuse", "unknown-before-write"])
def test_terminal_append_failure_keeps_accepted_edits_and_reports_recording_failure(fault):
    def terminal(event):
        return fault if type(event.payload).__name__ == "TerminalSnapshot" else None

    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),),
                      journal=lambda inner: FaultyJournal(inner, append_fault=terminal))  # fmt: skip
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, R.RECORDING_FAILED)
    assert report.terminal_event.payload.run.termination_reason is R.CANDIDATE_CRITERIA_MET
    assert report.final_run.final_project_state is FinalProjectState.ACCEPTED_EDITS_RETAINED
    assert report.terminal_event in report.unacknowledged_events


def test_reconciliation_recording_failure_terminates_for_recording_failure():
    def reconciliation(event):
        return "refuse" if isinstance(event.payload, ReconciliationResult) else None

    def journal(inner):
        return FaultyJournal(inner, commit_fault="unknown-after-write", append_fault=reconciliation)

    report = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),), journal=journal).run()
    assert outcome(report) == (RunOutcome.FAILED, R.RECORDING_FAILED)
    assert report.final_run.final_project_state is FinalProjectState.ACCEPTED_EDITS_RETAINED


def test_finalization_crossing_the_deadline_downgrades_success_but_keeps_the_edit():
    probe = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),))
    probe.run()
    last = probe.clock.reads - 1  # the completion reading
    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),),
                      clock=Clock(values={last: 61.0}))  # fmt: skip
    report = harness.run()
    assert report.terminal_event.payload.run.termination_reason is R.CANDIDATE_CRITERIA_MET
    assert outcome(report) == (RunOutcome.LIMIT_REACHED, R.ELAPSED_TIME_LIMIT)
    assert report.final_run.final_project_state is FinalProjectState.ACCEPTED_EDITS_RETAINED
    assert report.final_run.usage.elapsed_seconds == 61.0


def test_unexpected_programming_error_propagates_after_best_effort_finalization():
    def broken(situation, number):
        raise RuntimeError("labelled programming error")

    harness = Harness(decisions=(broken,))
    with pytest.raises(RuntimeError, match="labelled programming error"):
        harness.run()
    snapshot = harness.real_journal.snapshot().value
    terminal = snapshot.events[-1].payload
    assert type(terminal).__name__ == "TerminalSnapshot"
    assert terminal.run.termination_reason is R.RUNTIME_CONTRACT_FAILED


def test_duplicate_generated_identity_is_a_runtime_contract_failure():
    report = Harness(decisions=(no_change,), identities=Identities({4: "id-1"})).run()
    assert report.final_run.termination_reason is R.RUNTIME_CONTRACT_FAILED


# --- baseline readiness ------------------------------------------------------------------------------


def test_unavailable_comparison_baseline_ends_before_any_decision():
    from test_run_recording_contracts import CRITERIA, MEASUREMENTS, _measurement

    from autonomous_mixing.core.records.analysis import AnalysisResponse
    from autonomous_mixing.core.records.observation import AnalysisSpec
    from autonomous_mixing.core.records.verification import (
        AcceptanceCriteria,
        ImprovementCondition,
        ImprovementDirection,
        MetricSelector,
    )

    metric_b = VersionRef("metric-b", "1")
    improvement = ImprovementCondition(type(CRITERIA.targets[0].condition_id)("im-b"), MetricSelector(metric_b, SCOPE),
                                       ImprovementDirection.LOWER, MeasuredScalar(1.0, UNIT))  # fmt: skip
    criteria = AcceptanceCriteria(CRITERIA.criteria_id, "2", CRITERIA.targets, (improvement,), ())
    measurements = (MEASUREMENTS[0], _measurement("im-b", MetricSelector(metric_b, SCOPE)))
    configuration = _configuration(budget_limits=limits(), criteria=criteria,
                                   analysis_spec=AnalysisSpec(SPEC, (METRIC, metric_b)))  # fmt: skip

    def both_metrics(request):
        mix = _mix_of(request.state).artifact_id
        observations = tuple(
            Observation(ObservationId(f"{metric.name}@{request.state.revision}"), metric, (mix,), SCOPE, result,
                        SPEC, ANALYZER, UTC_NOW)
            for metric, result in ((METRIC, MeasuredScalar(INITIAL_VALUE, UNIT)), (metric_b, Unavailable("labelled")))
        )  # fmt: skip
        return Success(AnalysisResponse(observations))

    harness = Harness(analysis=(both_metrics,), decisions=(no_change,),
                      request=_run_request(configuration=configuration, measurements=measurements,
                                           analysis_definitions=tuple(m.definition for m in measurements)))  # fmt: skip
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, R.REQUIRED_BASELINE_UNAVAILABLE)
    assert "im-b" in report.final_run.termination_explanation
    assert harness.diagnosis.requests == [] and harness.decider.situations == []


# --- stale or invalid responses ----------------------------------------------------------------------


def test_stale_decision_for_another_revision_is_an_invalid_response():
    def stale(situation, number):
        decision = dataclasses.replace(_decision(situation, NoChange("x"), number),
                                       expected_revision=type(situation.state.revision)("r-old"))  # fmt: skip
        return Success(DecisionResponse(decision))

    report = Harness(decisions=(stale,)).run()
    assert outcome(report) == (RunOutcome.FAILED, R.INVALID_DECISION_RESPONSE)
    assert usage_counts(report) == (1, 0, 0, 0)


def test_stale_diagnosis_and_analysis_responses_are_contract_failures():
    def other_revision(request):
        report = Diagnosis().diagnose(request).value
        return Success(dataclasses.replace(report, revision=type(report.revision)("r-old"), candidates=()))

    diagnosis = Diagnosis()
    diagnosis.diagnose = other_revision
    assert outcome(Harness(diagnosis=diagnosis).run()) == (RunOutcome.FAILED, R.RUNTIME_CONTRACT_FAILED)

    def foreign_source(request):
        from autonomous_mixing.core.records.analysis import AnalysisResponse

        observation = Observation(ObservationId("obs-foreign"), METRIC, (IN_T1.artifact_id,), SCOPE,
                                  MeasuredScalar(0.0, UNIT), SPEC, ANALYZER, UTC_NOW)  # fmt: skip
        return Success(AnalysisResponse((observation,)))  # the superseded fixed input is not a current member

    assert outcome(Harness(analysis=(foreign_source,)).run()) == (RunOutcome.FAILED, R.RUNTIME_CONTRACT_FAILED)


def test_rendered_outputs_with_another_identity_are_an_invalid_render_response():
    class WrongIdentity(Renderer):
        def process(self, request):
            rendered = super().process(request)
            wrong = dataclasses.replace(rendered.target_artifact, revision=type(rendered.target_artifact.revision)("x"))
            return dataclasses.replace(rendered, target_artifact=wrong)

    harness = Harness(decisions=(propose(-3.0),), renderer=WrongIdentity())
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, R.INVALID_RENDER_RESPONSE)
    assert len(harness.inspector.requests) == 2 and usage_counts(report)[3] == 1
    assert harness.analyzer.requests[-1].state == STATE  # the candidate was never analyzed


def test_invalid_initial_integrity_report_is_uncertain_integrity():
    report = Harness(inspector=Inspector(["not a report"])).run()
    assert outcome(report) == (RunOutcome.FAILED, R.ACCEPTED_STATE_INTEGRITY_UNCERTAIN)
    assert report.verified is None


# --- trace invariants --------------------------------------------------------------------------------


SCENARIOS = {
    "accepted": dict(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),)),
    "intermediate": dict(analysis=(INITIAL_VALUE, INTERMEDIATE), decisions=(propose(-2.0), no_change)),
    "rejected": dict(analysis=(INITIAL_VALUE, REJECTED_VALUE, SATISFYING), decisions=(propose(-1.0), propose(-3.0))),
    "retried": dict(decisions=(fail(), no_change)),
    "admission": dict(decisions=(propose(-3.0, track=T2), no_change)),
    "render-failed": dict(decisions=(propose(-3.0),),
                          renderer=Renderer({1: RenderFailure(ExecutionStatus.FAILED, ExecutionIssue("r", "x"))})),
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_charged_decision_slot_has_exactly_one_feedback_and_usage_never_decreases(name):
    report = Harness(**SCENARIOS[name]).run()
    calls = [p.call_id for p in payloads(report, DispatchIntent) if p.operation is DC]
    feedback = [p.call_id for p in payloads(report, DecisionFeedback)]
    assert calls == feedback and len(set(calls)) == len(calls)
    assert report.final_run.usage.decision_calls == len(calls)
    situations = [p.request for p in payloads(report, DispatchIntent) if p.operation is DC]
    for index, situation in enumerate(situations):
        assert [e.call_id for e in situation.history.entries] == calls[:index]  # complete prior-call prefix
    observed = {p.intent_id for p in payloads(report, OperationObserved)}
    intents = [e.event_id for e in report.trace.events if isinstance(e.payload, DispatchIntent)]
    assert set(intents) == observed  # every dispatched or skipped intent has its outcome recorded


def test_every_render_dispatch_follows_a_recorded_intent_with_its_charge():
    report = Harness(**SCENARIOS["rejected"]).run()
    events = report.trace.events
    for position, event in enumerate(events):
        if isinstance(event.payload, OperationObserved) and event.payload.operation is RN:
            intent = next(e for e in events[:position] if e.event_id == event.payload.intent_id)
            assert intent.usage.render_calls == sum(
                1 for e in events[: position] if isinstance(e.payload, DispatchIntent) and e.payload.operation is RN
            )  # fmt: skip


# --- finalization edge cases -------------------------------------------------------------------------


def test_snapshot_failure_at_finalization_reports_the_acknowledged_prefix():
    calls = {"count": 0}

    def journal(inner):
        faulty = FaultyJournal(inner)
        original = faulty.snapshot

        def snapshot():
            calls["count"] += 1
            return original() if calls["count"] == 1 else PortFailure(PortFailureCode.UNAVAILABLE, "x", False)

        faulty.snapshot = snapshot
        return faulty

    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),), journal=journal)
    report = harness.run()
    assert outcome(report) == (RunOutcome.SUCCEEDED, R.CANDIDATE_CRITERIA_MET)
    assert report.trace == harness.real_journal.snapshot().value  # the acknowledged prefix equals the journal's


def test_terminal_acknowledgement_lost_after_write_is_a_recording_failure_with_the_event_recorded():
    def terminal(event):
        return "unknown-after-write" if type(event.payload).__name__ == "TerminalSnapshot" else None

    harness = Harness(analysis=(-0.5,), journal=lambda inner: FaultyJournal(inner, append_fault=terminal))
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, R.RECORDING_FAILED)
    assert report.terminal_event in report.trace.events and report.terminal_event in report.unacknowledged_events
    assert report.final_run.final_project_state is FinalProjectState.UNCHANGED


def test_cancellation_after_a_render_requires_recovery_and_cannot_claim_restoration():
    probe = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),))
    probe.run()
    clock = Clock(cancel_from=probe.read_after_intent[RN] + 1)  # cancelled once the render has returned
    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),), clock=clock)
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, R.REQUIRED_RECOVERY_NOT_PERFORMED)
    assert len(harness.renderer.requests) == 1 and report.trace.acceptances == ()
    assert report.final_run.final_project_state is FinalProjectState.UNKNOWN


# --- Codex review corrections (2026-11-10): regressions promoted from the review probes ----------------


def _expire_from(clock):
    """From the next clock read on, report elapsed time at or beyond the 60 s limit (monotonically)."""
    start = clock.reads
    clock.values.update({i: 60.0 + i for i in range(start, start + 80)})


@pytest.mark.parametrize("kind", ["cancel", "deadline"])
def test_cancellation_or_expiry_while_recording_the_acceptance_event_prevents_the_commit(kind):
    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),))

    def fault(event):
        if isinstance(event.payload, AcceptanceEvent):
            if kind == "cancel":
                harness.clock.cancel_from = harness.clock.reads
            else:
                _expire_from(harness.clock)
        return None

    harness.journal = FaultyJournal(harness.real_journal, append_fault=fault)
    report = harness.run()
    assert harness.real_journal.commits == () and report.trace.acceptances == ()  # no commit was dispatched
    assert outcome(report) == (RunOutcome.FAILED, R.REQUIRED_RECOVERY_NOT_PERFORMED)  # recovery is blocked as well
    assert report.authoritative == AcceptedReference(STATE, 0) and report.verified is None
    assert report.final_run.final_project_state is FinalProjectState.UNKNOWN
    assert usage_counts(report) == (1, 1, 1, 0)
    assert len(harness.renderer.requests) == 1 and len(harness.inspector.requests) == 1
    assert len(payloads(report, AcceptanceEvent)) == 1


def test_clock_failure_after_the_reconciliation_inspection_ends_without_success_and_keeps_authority():
    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),),
                      journal=lambda inner: FaultyJournal(inner, commit_fault="unknown-after-write"))  # fmt: skip

    class InvalidClockAfterInspection(Inspector):
        def inspect(self, request):
            response = super().inspect(request)
            if len(self.requests) == 2:
                harness.clock.values[harness.clock.reads] = "invalid"
            return response

    harness.inspector = InvalidClockAfterInspection()
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, R.RUNTIME_CONTRACT_FAILED)
    (commit,) = report.trace.acceptances
    assert report.authoritative == commit.accepted == report.verified  # established facts are retained
    assert report.final_run.final_project_state is FinalProjectState.ACCEPTED_EDITS_RETAINED
    (result,) = payloads(report, ReconciliationResult)
    assert result.outcome is ReconciliationOutcome.CONFIRMED_ACCEPTANCE
    assert len(harness.decider.situations) == 1 and usage_counts(report) == (1, 1, 1, 1)


def test_clock_failure_after_the_authority_query_skips_the_inspection_but_keeps_the_authority():
    class InvalidClockAfterQuery(FaultyJournal):
        def query(self, request):
            answer = super().query(request)
            harness.clock.values[harness.clock.reads] = "invalid"
            return answer

    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),),
                      journal=lambda inner: InvalidClockAfterQuery(inner, commit_fault="unknown-after-write"))  # fmt: skip
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, R.REQUIRED_RECOVERY_NOT_PERFORMED)
    (commit,) = report.trace.acceptances
    assert report.authoritative == commit.accepted and report.verified is None
    assert report.final_run.final_project_state is FinalProjectState.UNKNOWN
    assert len(harness.inspector.requests) == 1 and payloads(report, ReconciliationResult) == []
    assert "runtime_contract_failed" in report.final_run.termination_explanation


@pytest.mark.parametrize(
    "operation, reason, final_state",
    [
        (Q, R.REQUIRED_RECOVERY_NOT_PERFORMED, FinalProjectState.UNKNOWN),
        (INS, R.RECORDING_FAILED, FinalProjectState.ACCEPTED_EDITS_RETAINED),
    ],
    ids=["query", "inspection"],
)
def test_unrecorded_reconciliation_observations_are_not_ignored(operation, reason, final_state):
    seen = {"count": 0}

    def refuse_reconciliation_observation(event):
        if isinstance(event.payload, OperationObserved) and event.payload.operation is operation:
            seen["count"] += 1
            if operation is Q or seen["count"] == 2:  # the second inspection is the reconciliation's
                return "refuse"
        return None

    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),),
                      journal=lambda inner: FaultyJournal(inner, commit_fault="unknown-after-write",
                                                          append_fault=refuse_reconciliation_observation))  # fmt: skip
    report = harness.run()
    assert outcome(report) == (RunOutcome.FAILED, reason)
    (commit,) = report.trace.acceptances
    assert report.authoritative == commit.accepted
    assert report.final_run.final_project_state is final_state
    assert len(harness.decider.situations) == 1
    assert len(harness.inspector.requests) == (1 if operation is Q else 2)


@pytest.mark.parametrize("value", ["invalid", 0.0], ids=["invalid", "backward"])
@pytest.mark.parametrize("scenario", ["initial", "accepted"])
def test_invalid_or_backward_completion_reading_cannot_report_success(value, scenario):
    kwargs = (dict(analysis=(-0.5,)) if scenario == "initial"
              else dict(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),)))  # fmt: skip
    probe = Harness(**kwargs)
    probe.run()
    final_read = probe.clock.reads - 1
    harness = Harness(**kwargs, clock=Clock(values={final_read: value}))
    report = harness.run()
    proposed = report.terminal_event.payload.run
    assert proposed.outcome is RunOutcome.SUCCEEDED
    assert outcome(report) == (RunOutcome.FAILED, R.RUNTIME_CONTRACT_FAILED)
    assert report.final_run.final_project_state is proposed.final_project_state  # accepted-state facts retained
    assert report.verified == report.authoritative and report.verified is not None
    assert report.final_run.usage == proposed.usage  # the last valid elapsed observation
    assert report.completion.monotonic_seconds == proposed.usage.elapsed_seconds
    assert isinstance(report.terminal_append, TraceAppended)


def test_final_snapshot_duration_is_included_in_completion():
    class AdvancingClock(Clock):
        offset = 0.0

        def read(self):
            reading = super().read()
            return ClockReading(reading.monotonic_seconds + self.offset, reading.utc, reading.cancelled)

    clock = AdvancingClock()

    class SlowFinalSnapshot(FaultyJournal):
        calls = 0

        def snapshot(self):
            self.calls += 1
            if self.calls == 2:  # the final read-back takes 61 s
                clock.offset = 61.0
            return super().snapshot()

    harness = Harness(analysis=(-0.5,), clock=clock, journal=lambda inner: SlowFinalSnapshot(inner))
    report = harness.run()
    assert report.terminal_event.payload.run.termination_reason is R.INITIAL_CRITERIA_MET
    assert report.final_run.usage.elapsed_seconds >= 61.0
    assert outcome(report) == (RunOutcome.LIMIT_REACHED, R.ELAPSED_TIME_LIMIT)
    assert report.final_run.final_project_state is FinalProjectState.UNCHANGED


def test_global_call_limit_precedes_retry_exhaustion():
    harness = Harness(decisions=(fail(), fail()), request=request_with(max_decision_calls=2))
    report = harness.run()
    assert outcome(report) == (RunOutcome.LIMIT_REACHED, R.DECISION_CALL_LIMIT)
    assert len(harness.decider.situations) == 2 and usage_counts(report) == (2, 0, 0, 0)
    (block,) = payloads(report, BudgetBlock)
    assert block.reason.name == "DECISION_CALL_LIMIT"


def test_time_limit_precedes_the_call_limit_and_retry_exhaustion():
    harness = Harness(request=request_with(max_decision_calls=2))

    def late_failure(situation, number):
        _expire_from(harness.clock)
        return PortFailure(PortFailureCode.UNAVAILABLE, "labelled late failure", True)

    harness.decider = Decider(fail(), late_failure)
    report = harness.run()
    assert outcome(report) == (RunOutcome.LIMIT_REACHED, R.ELAPSED_TIME_LIMIT)
    assert len(harness.decider.situations) == 2 and usage_counts(report) == (2, 0, 0, 0)
    (block,) = payloads(report, BudgetBlock)
    assert block.reason.name == "ELAPSED_TIME_LIMIT"


def test_cancellation_after_a_retryable_failure_stops_before_another_call():
    harness = Harness()

    def cancelling_failure(situation, number):
        harness.clock.cancel_from = harness.clock.reads
        return PortFailure(PortFailureCode.UNAVAILABLE, "labelled failure", True)

    harness.decider = Decider(cancelling_failure, no_change)
    report = harness.run()
    assert outcome(report) == (RunOutcome.CANCELLED, R.USER_CANCELLED)
    assert len(harness.decider.situations) == 1 and usage_counts(report) == (1, 0, 0, 0)
    assert isinstance(payloads(report, DecisionFeedback)[-1].outcome, PortFailureFeedback)


# --- explicit analysis jobs and current-artifact roots (analysis-jobs plan, 2026-11-10) ----------------


def _bound(request):
    return [(str(b.artifact_id), b.root_key) for b in request.root_bindings]


def test_initial_and_candidate_analysis_receive_explicit_jobs_and_current_roots():
    harness = Harness(analysis=(INITIAL_VALUE, SATISFYING), decisions=(propose(-3.0),))
    report = harness.run()
    assert outcome(report) == (RunOutcome.SUCCEEDED, R.CANDIDATE_CRITERIA_MET)
    initial, candidate = harness.analyzer.requests
    jobs = harness.request.analysis_definitions
    assert initial.definitions is jobs and candidate.definitions is jobs
    assert len(jobs) == 1 and jobs[0] is harness.request.measurements[0].definition  # shared criterion job once
    # Initial: current members only, in checkpoint order; superseded fixed inputs are not analysis roots.
    assert _bound(initial) == [("s1", "exports"), ("s2", "exports"), ("mix", "exports")]
    assert IN_T1.artifact_id in {b.artifact_id for b in harness.request.root_bindings}
    # Candidate: retained members keep their known root; outputs use the assigned workspace root before acceptance.
    plan = harness.renderer.requests[0].plan
    outputs = {str(plan.candidate.target_artifact_id), str(plan.candidate.mix_artifact_id)}
    assert plan.workspace.root_key == harness.request.output_root_key == "renders" != "exports"
    assert [str(a.artifact_id) for a in candidate.state.audio.artifacts] == [a for a, _ in _bound(candidate)]
    assert {a: k for a, k in _bound(candidate) if a in outputs} == dict.fromkeys(outputs, "renders")
    assert [(a, k) for a, k in _bound(candidate) if a not in outputs] == [("s2", "exports")]
    retained = candidate.state.audio.artifact_for(ArtifactId("s2"))
    assert retained.revision != candidate.state.revision  # a retained member from an older production revision
    fixed = {g.input_artifact.artifact_id for g in candidate.state.processing.track_gains}
    assert IN_T1.artifact_id in fixed and not fixed & {b.artifact_id for b in candidate.root_bindings}
    # Metadata assembly adds no clock, identity, port or budget use (HEAD eb13065 recorded 22 reads, 37 IDs).
    assert (harness.clock.reads, harness.identities.count, usage_counts(report)) == (22, 37, (1, 1, 1, 0))
    assert operations(report) == CANDIDATE_LOOP


def test_initial_roots_include_a_retained_older_member_on_its_own_root():
    from test_run_recording_contracts import IN_T2, MIX, S1, _artifact, _roots, _state

    from autonomous_mixing.core.records.admission import StemBinding

    older = _artifact("s2-old", "r0", ("t2",))
    state = _state(members=(S1, older, MIX))
    roots = (*_roots((S1, MIX), "exports"), *_roots((older,), "archive"), *_roots((IN_T1, IN_T2), "inputs"))
    request = _run_request(initial_state=state, root_bindings=roots,
                           stem_bindings=(StemBinding(T1, S1.artifact_id), StemBinding(T2, older.artifact_id)))  # fmt: skip
    harness = Harness(analysis=(-0.5,), request=request)
    assert outcome(harness.run()) == (RunOutcome.SUCCEEDED, R.INITIAL_CRITERIA_MET)
    (initial,) = harness.analyzer.requests
    assert _bound(initial) == [("s1", "exports"), ("s2-old", "archive"), ("mix", "exports")]


METRIC_D = VersionRef("metric-d", "1")  # labelled diagnostic-only metric: no criterion names it


def test_diagnostic_only_and_separate_scope_jobs_reach_diagnosis_and_the_situation():
    from test_run_recording_contracts import ALIGNMENT, MEASUREMENTS, RATE, RENDER_REF, SECONDS

    from autonomous_mixing.core.records.analysis import AnalysisResponse
    from autonomous_mixing.core.records.observation import AnalysisScope, AnalysisSpec, TimeRange
    from autonomous_mixing.core.records.verification import MetricSelector
    from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition, SourceRequirement

    criterion = MEASUREMENTS[0].definition
    half = AnalysisScope((T1, T2), TimeRange(0.0, 30.0))
    source = SourceRequirement(ArtifactKind.MIX, (T1, T2), RENDER_REF, RATE, 2, 0.0, RATE * SECONDS)

    def job(metric, scope):
        return MeasurementDefinition(MetricSelector(metric, scope), UNIT, SPEC, ANALYZER, ALIGNMENT, (source,))

    jobs = (criterion, job(METRIC_D, SCOPE), job(METRIC, half))  # one diagnostic-only, one more METRIC scope
    configuration = _configuration(budget_limits=limits(), analysis_spec=AnalysisSpec(SPEC, (METRIC, METRIC_D)))
    request = _run_request(configuration=configuration, analysis_definitions=jobs)

    def every_job(analysis_request):
        mix = _mix_of(analysis_request.state).artifact_id
        values = {criterion: MeasuredScalar(INITIAL_VALUE, UNIT)}
        return Success(AnalysisResponse(tuple(
            Observation(ObservationId(f"obs-{i}"), d.selector.metric, (mix,), d.selector.scope,
                        values.get(d, Unavailable("labelled: no value")), SPEC, ANALYZER, UTC_NOW)
            for i, d in enumerate(reversed(analysis_request.definitions))
        )))  # fmt: skip

    harness = Harness(analysis=(every_job,), decisions=(no_change,), request=request)
    report = harness.run()
    assert outcome(report) == (RunOutcome.ABSTAINED, R.NO_CHANGE_PROPOSED)
    (analysis_request,) = harness.analyzer.requests
    assert analysis_request.definitions is jobs
    keys = {(o.metric, o.scope) for o in harness.diagnosis.requests[0].observations}
    assert keys == {(METRIC, SCOPE), (METRIC_D, SCOPE), (METRIC, half)}
    (situation,) = harness.decider.situations
    assert {(o.metric, o.scope) for o in situation.observations} == keys
    assert (harness.clock.reads, harness.identities.count, usage_counts(report)) == (14, 16, (1, 0, 0, 0))


def test_an_omitted_requested_scope_is_an_invalid_analysis_response():
    from test_run_recording_contracts import ALIGNMENT, MEASUREMENTS

    from autonomous_mixing.core.records.observation import AnalysisScope, TimeRange
    from autonomous_mixing.core.records.verification import MetricSelector

    criterion = MEASUREMENTS[0].definition
    half = dataclasses.replace(
        criterion, selector=MetricSelector(METRIC, AnalysisScope((T1, T2), TimeRange(0.0, 30.0)))
    )
    assert criterion.alignment == ALIGNMENT
    harness = Harness(request=_run_request(configuration=_configuration(budget_limits=limits()),
                                           analysis_definitions=(criterion, half)))  # fmt: skip
    report = harness.run()  # the scripted analyzer returns only the full-scope observation
    assert outcome(report) == (RunOutcome.FAILED, R.RUNTIME_CONTRACT_FAILED)
    assert "no observation" in report.final_run.termination_explanation
    assert harness.diagnosis.requests == [] and harness.decider.situations == []
