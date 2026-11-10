"""Contract tests for the standalone decision gate (plan docs/superpowers/plans/2026-11-06-decision-gate-plan.md).

All records are metadata fixtures. The diagnosis report is a labelled test double (no detector ran); its
candidates cite the fixture's actual current observations, some of which are explicitly Unavailable. A
ReadyForAdmission result hands off to admission; it is not permission, and nothing here is evaluation data.
"""

import dataclasses
import typing
from datetime import datetime, timezone

import pytest

import autonomous_mixing.core.admission.decision_gate as gate_module
from autonomous_mixing.core.admission.admission import admit_action
from autonomous_mixing.core.admission.decision_gate import assess_decision
from autonomous_mixing.core.ports.decision import validate_decision_response
from autonomous_mixing.core.records.actions import Action, SetTrackGain
from autonomous_mixing.core.records.admission import (
    ActionAdmissionPolicy,
    ActionAdmissionRequest,
    AdmissionRejectionCode,
    TrackGainPermission,
)
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.budgets import BudgetLimits, BudgetUsage
from autonomous_mixing.core.records.capabilities import ActionCapabilityView
from autonomous_mixing.core.records.context import AssertionSource, MusicalContext, TrackRole
from autonomous_mixing.core.records.decision import (
    Abstain,
    AbstentionReason,
    ActionJustification,
    ControllerIdentity,
    Decision,
    DecisionResponse,
    NoChange,
    ProposeAction,
    Situation,
)
from autonomous_mixing.core.records.decision_gate import (
    DecisionGateResult,
    DeclineAction,
    EvidenceBlocked,
    InvalidResponse,
    KeepState,
    ReadyForAdmission,
    RequestContext,
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
from autonomous_mixing.core.records.feedback import DecisionHistory
from autonomous_mixing.core.records.identifiers import (
    ActionId,
    ArtifactId,
    CheckpointId,
    ConditionId,
    ContextId,
    CriteriaId,
    DecisionId,
    DiagnosisCoverageId,
    ObservationId,
    ProblemCandidateId,
    ProjectId,
    Revision,
    RunId,
    TrackId,
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
    AcceptanceCriteria,
    ComparisonOperator,
    ImprovementCondition,
    ImprovementDirection,
    MetricSelector,
    TargetCondition,
)

UTC_NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
METRIC = VersionRef("metric-a", "1")
METHOD = VersionRef("test-double-diagnosis", "0")  # labelled test double; no detection method exists
CONTROLLER = ControllerIdentity("deterministic-baseline", "0.1")
SCOPE = AnalysisScope((TrackId("t1"), TrackId("t2"), TrackId("t3")), TimeRange(0.0, 60.0))


# --- request fixture ---------------------------------------------------------------------------------


def _state() -> ProjectState:
    snapshot = ProjectSnapshot(
        ProjectId("p1"), Revision("r2"), UTC_NOW, tuple(Track(TrackId(f"t{i}"), f"track {i}") for i in (1, 2, 3))
    )
    mix = AudioArtifact(
        ArtifactId("c-mix"),
        ProjectId("p1"),
        Revision("r2"),
        (TrackId("t1"), TrackId("t2"), TrackId("t3")),
        ArtifactKind.MIX,
        "outputs/c-mix.wav",
        0.0,
        48000,
        2,
        48000 * 60,
        "d" * 64,
        VersionRef("render", "1"),
    )
    stem = dataclasses.replace(
        mix, artifact_id=ArtifactId("in-t1"), kind=ArtifactKind.STEM, source_track_ids=(TrackId("t1"),)
    )
    return ProjectState(
        snapshot,
        ProjectProcessingState(ProjectId("p1"), Revision("r2"), (TrackGainState(TrackId("t1"), stem, 0.0),)),
        ArtifactCheckpoint(CheckpointId("cp"), ProjectId("p1"), Revision("r2"), (mix,)),
    )


def _context(protected=frozenset()) -> MusicalContext:
    source = AssertionSource("producer", UTC_NOW)
    roles = tuple(TrackRole(TrackId(t), r, source) for t, r in [("t1", "ambience"), ("t2", "lead"), ("t3", "texture")])
    return MusicalContext(ContextId("c1"), "1", roles, (), frozenset(TrackId(t) for t in protected))


def _observation(oid, result) -> Observation:
    return Observation(
        ObservationId(oid),
        METRIC,
        (ArtifactId("c-mix"),),
        SCOPE,
        result,
        VersionRef("spec", "1"),
        VersionRef("analyzer", "0.1"),
        UTC_NOW,
    )


OBSERVATIONS = (
    _observation("o-avail", MeasuredScalar(-14.0, "unit-a")),
    _observation("o-zero", MeasuredScalar(0.0, "unit-a")),  # a measured zero is available
    _observation("o-unavail", Unavailable("silent region")),
    _observation("o-unavail-2", Unavailable("analyzer could not run")),
)


def _candidate(cid, evidence, insufficient=False) -> ProblemCandidate:
    statement = DiagnosticEvidence(
        "test-double strength",
        "uncalibrated",
        UncertaintyRepresentation.UNCALIBRATED,
        insufficient,
        (),
        ("labelled missing information",) if insufficient else (),
    )
    return ProblemCandidate(
        ProblemCandidateId(cid),
        DiagnosisCoverageId("cov-1"),
        ProjectId("p1"),
        Revision("r2"),
        ProblemFamily.MASKING,
        SCOPE,
        tuple(ObservationId(o) for o in evidence),
        METHOD,
        statement,
    )


# Diagnosis order deliberately differs from the proposal orders used below.
CANDIDATES = (
    _candidate("c-unavail", ("o-unavail",), insufficient=True),  # all Unavailable: must be marked insufficient
    _candidate("c-partial", ("o-avail", "o-unavail")),  # partial availability, not flagged
    _candidate("c-zero", ("o-zero",)),
    _candidate("c-flagged", ("o-avail",), insufficient=True),  # available, but explicitly insufficient
    _candidate("c-other", ("o-unavail-2",), insufficient=True),
)


def _situation(protected=frozenset()) -> Situation:
    selector = MetricSelector(METRIC, SCOPE)
    criteria = AcceptanceCriteria(
        CriteriaId("crit"),
        "1",
        (TargetCondition(ConditionId("target"), selector, ComparisonOperator.LE, MeasuredScalar(-10.0, "unit-a")),),
        (
            ImprovementCondition(
                ConditionId("improve"), selector, ImprovementDirection.LOWER, MeasuredScalar(1.0, "unit-a")
            ),
        ),
        (),
    )
    diagnosis = DiagnosisReport(
        ProjectId("p1"),
        Revision("r2"),
        (
            DiagnosisCoverage(
                DiagnosisCoverageId("cov-1"), ProblemFamily.MASKING, SCOPE, DiagnosisCoverageStatus.ASSESSED, METHOD,
                "labelled test-double coverage",
            ),
        ),
        CANDIDATES,
    )  # fmt: skip
    capabilities = ActionCapabilityView(
        ProjectId("p1"), Revision("r2"), ContextId("c1"), "1", VersionRef("policy", "1"), VersionRef("renderer", "1"),
        SCOPE, (),
    )  # fmt: skip
    return Situation(
        _state(),
        _context(protected),
        CONTROLLER,
        OBSERVATIONS,
        BudgetUsage(1, 0, 0, 0, 1.0),
        BudgetLimits(4, 2, 6, 1, 300.0),
        RunId("run-1"),
        SCOPE,
        criteria,
        diagnosis,
        capabilities,
        DecisionHistory(RunId("run-1"), ProjectId("p1"), ()),
    )


def _proposal(*candidates) -> ProposeAction:
    justification = ActionJustification("labelled test-double reasoning", "uncalibrated fixture text", (), ())
    action = Action(ActionId("x1"), ProjectId("p1"), Revision("r2"), SetTrackGain(TrackId("t2"), -2.0))
    return ProposeAction(action, "lower track t2", tuple(ProblemCandidateId(c) for c in candidates), justification)


def _decision(outcome, evidence=(), controller=CONTROLLER) -> Decision:
    return Decision(
        DecisionId("d1"),
        ProjectId("p1"),
        Revision("r2"),
        ContextId("c1"),
        "1",
        tuple(ObservationId(e) for e in evidence),
        "recorded rationale",
        controller,
        outcome,
    )


ALL_EVIDENCE = ("o-avail", "o-zero", "o-unavail", "o-unavail-2")
QUESTIONS = ("Which track is the low-end anchor in this section, and should t2 keep its level?",)


def _assess(outcome, evidence=ALL_EVIDENCE, **kwargs):
    return assess_decision(_situation(), DecisionResponse(_decision(outcome, evidence, **kwargs)))


# --- result records ----------------------------------------------------------------------------------

RESULT_FIELDS = {
    InvalidResponse: ["explanation"],
    KeepState: ["decision"],
    RequestContext: ["decision"],
    DeclineAction: ["decision"],
    EvidenceBlocked: ["decision", "blocked_candidate_ids"],
    ReadyForAdmission: ["decision"],
}


@pytest.mark.parametrize("record, fields", RESULT_FIELDS.items(), ids=lambda v: getattr(v, "__name__", ""))
def test_result_records_have_exact_required_fields(record, fields):
    assert [f.name for f in dataclasses.fields(record)] == fields
    assert all(
        f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
        for f in dataclasses.fields(record)
    )
    with pytest.raises(TypeError):
        record()


def test_the_result_union_is_exactly_the_six_routes():
    assert typing.get_args(DecisionGateResult) == (
        InvalidResponse,
        KeepState,
        RequestContext,
        DeclineAction,
        EvidenceBlocked,
        ReadyForAdmission,
    )


def test_result_records_are_frozen():
    result = KeepState(_decision(NoChange("keep")))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.decision = _decision(NoChange("other"))  # type: ignore[misc]


@pytest.mark.parametrize("explanation", ["", " ", None, 3])
def test_invalid_response_needs_a_nonblank_explanation(explanation):
    with pytest.raises(ValueError):
        InvalidResponse(explanation)  # type: ignore[arg-type]


PROPOSAL = _decision(_proposal("c-partial", "c-flagged"), ("o-avail", "o-unavail"))
KEEP = _decision(NoChange("keep the accepted state"))
ASK = _decision(Abstain(AbstentionReason.MISSING_CONTEXT, QUESTIONS))
DECLINE = _decision(Abstain(AbstentionReason.NO_JUSTIFIED_ACTION))


@pytest.mark.parametrize(
    "record, wrong",
    [
        (KeepState, PROPOSAL),
        (KeepState, ASK),
        (RequestContext, KEEP),
        (RequestContext, DECLINE),
        (DeclineAction, ASK),
        (DeclineAction, PROPOSAL),
        (ReadyForAdmission, KEEP),
        (ReadyForAdmission, DECLINE),
        (KeepState, NoChange("keep")),
        (ReadyForAdmission, None),
    ],
    ids=lambda v: getattr(v, "__name__", type(v).__name__),
)
def test_route_records_require_a_decision_with_their_outcome(record, wrong):
    with pytest.raises(ValueError):
        record(wrong)


def test_route_records_keep_the_original_decision():
    assert KeepState(KEEP).decision is KEEP
    assert RequestContext(ASK).decision.outcome.requested_context is QUESTIONS
    assert DeclineAction(DECLINE).decision.outcome.reason is AbstentionReason.NO_JUSTIFIED_ACTION
    assert ReadyForAdmission(PROPOSAL).decision is PROPOSAL


@pytest.mark.parametrize(
    "decision, blocked",
    [
        (PROPOSAL, ()),  # empty
        (PROPOSAL, [ProblemCandidateId("c-flagged")]),  # not a tuple
        (PROPOSAL, ("c-flagged",)),  # untyped
        (PROPOSAL, (ProblemCandidateId("c-flagged"), ProblemCandidateId("c-flagged"))),  # duplicate
        (PROPOSAL, (ProblemCandidateId("c-zero"),)),  # not selected by the proposal
        (PROPOSAL, (ProblemCandidateId("c-flagged"), ProblemCandidateId("c-partial"))),  # not in proposal order
        (KEEP, (ProblemCandidateId("c-flagged"),)),  # not a proposal
    ],
    ids=["empty", "list", "untyped", "duplicate", "unselected", "reordered", "not-a-proposal"],
)
def test_evidence_blocked_ids_are_a_nonempty_unique_subsequence_of_the_proposal(decision, blocked):
    with pytest.raises(ValueError):
        EvidenceBlocked(decision, blocked)


def test_evidence_blocked_keeps_the_decision_and_ids():
    blocked = (ProblemCandidateId("c-partial"), ProblemCandidateId("c-flagged"))
    result = EvidenceBlocked(PROPOSAL, blocked)
    assert result.decision is PROPOSAL and result.blocked_candidate_ids is blocked


# --- structural validation comes first ---------------------------------------------------------------


def test_argument_types_are_checked_before_response_validation():
    response = DecisionResponse(KEEP)
    with pytest.raises(ValueError):
        assess_decision(None, response)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        assess_decision(_situation(), KEEP)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "decision",
    [
        _decision(NoChange("keep"), ("o-absent",)),  # an invalid NoChange is not kept
        _decision(Abstain(AbstentionReason.MISSING_CONTEXT, QUESTIONS), controller=ControllerIdentity("other", "1")),
        _decision(Abstain(AbstentionReason.NO_JUSTIFIED_ACTION), ("o-absent",)),
        _decision(_proposal("c-missing"), ALL_EVIDENCE),
        _decision(_proposal("c-partial"), ("o-unavail",)),  # selected candidate's evidence not cited
    ],
    ids=["no-change", "context-request", "decline", "unknown-candidate", "uncited-evidence"],
)
def test_structurally_invalid_responses_become_invalid_response_with_the_original_explanation(decision):
    situation, response = _situation(), DecisionResponse(decision)
    with pytest.raises(ValueError) as caught:
        validate_decision_response(situation, response)
    result = assess_decision(situation, response)
    assert isinstance(result, InvalidResponse)
    assert result.explanation == str(caught.value)


def test_response_validation_runs_exactly_once_for_every_route(monkeypatch):
    calls = []
    real = gate_module.validate_decision_response

    def counting(situation, response):
        calls.append(response)
        return real(situation, response)

    monkeypatch.setattr(gate_module, "validate_decision_response", counting)
    for decision in (KEEP, ASK, DECLINE, _decision(_proposal("c-partial"), ALL_EVIDENCE)):
        calls.clear()
        response = DecisionResponse(decision)
        assess_decision(_situation(), response)
        assert calls == [response]


def test_unexpected_validator_failures_propagate(monkeypatch):
    error = RuntimeError("validator bug")

    def broken(situation, response):
        raise error

    monkeypatch.setattr(gate_module, "validate_decision_response", broken)
    with pytest.raises(RuntimeError) as caught:
        assess_decision(_situation(), DecisionResponse(KEEP))
    assert caught.value is error


# --- non-action routes -------------------------------------------------------------------------------


def test_no_change_is_routed_to_keep_state_without_a_success_claim():
    decision = _decision(NoChange("keep the accepted state; criteria are not evaluated here"))
    result = assess_decision(_situation(), DecisionResponse(decision))
    assert isinstance(result, KeepState) and result.decision is decision


def test_missing_context_is_routed_to_request_context_with_the_original_questions():
    decision = _decision(Abstain(AbstentionReason.MISSING_CONTEXT, QUESTIONS))
    result = assess_decision(_situation(), DecisionResponse(decision))
    assert isinstance(result, RequestContext) and result.decision is decision
    assert result.decision.outcome.requested_context is QUESTIONS


@pytest.mark.parametrize("reason", [r for r in AbstentionReason if r is not AbstentionReason.MISSING_CONTEXT])
def test_other_abstentions_are_routed_to_decline_action_with_their_reason(reason):
    decision = _decision(Abstain(reason))
    result = assess_decision(_situation(), DecisionResponse(decision))
    assert isinstance(result, DeclineAction) and result.decision is decision
    assert result.decision.outcome.reason is reason


# --- proposals: per-candidate evidence ---------------------------------------------------------------


@pytest.mark.parametrize(
    "candidates",
    [("c-partial",), ("c-zero",), ("c-zero", "c-partial")],
    ids=["partial-availability", "measured-zero", "both"],
)
def test_candidates_with_available_unflagged_evidence_are_ready_for_admission(candidates):
    decision = _decision(_proposal(*candidates), ALL_EVIDENCE)
    result = assess_decision(_situation(), DecisionResponse(decision))
    assert isinstance(result, ReadyForAdmission) and result.decision is decision


def test_all_unavailable_evidence_blocks_the_candidate():
    result = _assess(_proposal("c-unavail"))
    assert isinstance(result, EvidenceBlocked)
    assert result.blocked_candidate_ids == (ProblemCandidateId("c-unavail"),)


def test_an_available_observation_does_not_override_an_explicit_insufficiency_flag():
    result = _assess(_proposal("c-flagged"))
    assert isinstance(result, EvidenceBlocked)
    assert result.blocked_candidate_ids == (ProblemCandidateId("c-flagged"),)


def test_available_evidence_of_another_candidate_cannot_rescue_a_candidate():
    # The decision cites o-avail (c-partial's evidence), but c-other's own evidence is all Unavailable.
    result = _assess(_proposal("c-partial", "c-other"))
    assert isinstance(result, EvidenceBlocked)
    assert result.blocked_candidate_ids == (ProblemCandidateId("c-other"),)


def test_all_blocked_candidates_are_reported_in_proposal_order():
    # Diagnosis order is c-unavail, c-partial, c-zero, c-flagged, c-other; the proposal order differs.
    decision = _decision(_proposal("c-other", "c-zero", "c-flagged", "c-partial", "c-unavail"), ALL_EVIDENCE)
    result = assess_decision(_situation(), DecisionResponse(decision))
    assert isinstance(result, EvidenceBlocked) and result.decision is decision
    assert result.blocked_candidate_ids == tuple(ProblemCandidateId(c) for c in ("c-other", "c-flagged", "c-unavail"))


def test_unselected_candidates_impose_no_restriction():
    # c-unavail, c-flagged and c-other would be blocked, but only c-zero is selected.
    assert isinstance(_assess(_proposal("c-zero"), ("o-zero",)), ReadyForAdmission)


def test_ready_for_admission_does_not_apply_admission_rules():
    # t2 is protected: the gate still hands the proposal on, and admission then rejects it.
    situation = _situation(protected={"t2"})
    decision = _decision(_proposal("c-partial"), ALL_EVIDENCE)
    assert isinstance(assess_decision(situation, DecisionResponse(decision)), ReadyForAdmission)
    policy = ActionAdmissionPolicy(
        VersionRef("policy", "1"), ProjectId("p1"), (TrackGainPermission(TrackId("t2"), -6.0, 6.0),)
    )
    admitted = admit_action(ActionAdmissionRequest(situation, decision, policy))
    assert admitted.code is AdmissionRejectionCode.PROTECTED_TRACK
