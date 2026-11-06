"""Contract tests for explicit decision outcomes and proposal references (increment 2E).

Spec: docs/superpowers/specs/2026-11-05-decision-outcomes-design.md. All records are metadata fixtures. The
diagnosis report is a labelled test double (no detector ran); its candidates cite the fixture's actual
current observations. A structurally valid response is a proposal, not permission to act: nothing here is a
semantic evidence gate, a success claim or evaluation data.
"""

import dataclasses
import inspect
import typing
from datetime import datetime, timezone

import pytest

from autonomous_mixing.core.admission.admission import admit_action
from autonomous_mixing.core.ports.decision import validate_decision_references, validate_decision_response
from autonomous_mixing.core.records.actions import Action, SetTrackGain
from autonomous_mixing.core.records.admission import (
    ActionAdmissionPolicy,
    ActionAdmissionRequest,
    AdmissionRejectionCode,
    RejectedAction,
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
from autonomous_mixing.core.records.diagnosis import (
    DiagnosisCoverage,
    DiagnosisCoverageStatus,
    DiagnosisReport,
    DiagnosticEvidence,
    ProblemCandidate,
    ProblemFamily,
    UncertaintyRepresentation,
)
from autonomous_mixing.core.records.feedback import DecisionHistory, NoChangeFeedback
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
    ComparisonOperator,
    ImprovementCondition,
    ImprovementDirection,
    MetricSelector,
    TargetCondition,
)

UTC_NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
METRIC = VersionRef("metric-a", "1")
METHOD = VersionRef("test-double-diagnosis", "0")  # labelled test double; no detection method exists
CONTROLLER = ControllerIdentity("deterministic-baseline", "0.1")


# --- request fixture ---------------------------------------------------------------------------------


def _artifact(aid, tracks, revision, *, kind=ArtifactKind.STEM) -> AudioArtifact:
    return AudioArtifact(
        ArtifactId(aid),
        ProjectId("p1"),
        Revision(revision),
        tuple(TrackId(t) for t in tracks),
        kind,
        f"outputs/{aid}.wav",
        0.0,
        48000,
        2,
        48000 * 60,
        "d" * 64,
        VersionRef("render", "1"),
    )


def _state() -> ProjectState:
    snapshot = ProjectSnapshot(
        ProjectId("p1"), Revision("r2"), UTC_NOW, tuple(Track(TrackId(f"t{i}"), f"track {i}") for i in (1, 2, 3))
    )
    members = (
        _artifact("a-t1", ("t1",), "r1"),
        _artifact("c-t2", ("t2",), "r2"),
        _artifact("c-mix", ("t1", "t2", "t3"), "r2", kind=ArtifactKind.MIX),
    )
    gains = (TrackGainState(TrackId("t1"), _artifact("a-t1", ("t1",), "r1"), 0.0),)
    return ProjectState(
        snapshot,
        ProjectProcessingState(ProjectId("p1"), Revision("r2"), gains),
        ArtifactCheckpoint(CheckpointId("cp"), ProjectId("p1"), Revision("r2"), members),
    )


def _context() -> MusicalContext:
    source = AssertionSource("producer", UTC_NOW)
    roles = tuple(TrackRole(TrackId(t), r, source) for t, r in [("t1", "ambience"), ("t2", "lead"), ("t3", "texture")])
    return MusicalContext(ContextId("c1"), "1", roles, (), frozenset())


SCOPE = AnalysisScope((TrackId("t1"), TrackId("t2"), TrackId("t3")), TimeRange(0.0, 60.0))


def _observation(oid, sources, tracks=None) -> Observation:
    scope = SCOPE if tracks is None else AnalysisScope(tuple(TrackId(t) for t in tracks), TimeRange(0.0, 60.0))
    return Observation(
        ObservationId(oid),
        METRIC,
        tuple(ArtifactId(a) for a in sources),
        scope,
        MeasuredScalar(-14.0, "unit-a"),
        VersionRef("spec", "1"),
        VersionRef("analyzer", "0.1"),
        UTC_NOW,
    )


OBSERVATIONS = (
    _observation("o-mix", ("c-mix",)),
    _observation("o-t2", ("c-t2",), ("t2",)),
    _observation("o-t1", ("a-t1",), ("t1",)),
)


def _candidate(cid, evidence) -> ProblemCandidate:
    statement = DiagnosticEvidence(
        "test-double strength", "uncalibrated", UncertaintyRepresentation.UNCALIBRATED, False, (), ()
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


# Two labelled test-double candidates citing the fixture's actual current observations.
DIAGNOSIS = DiagnosisReport(
    ProjectId("p1"),
    Revision("r2"),
    (
        DiagnosisCoverage(
            DiagnosisCoverageId("cov-1"),
            ProblemFamily.MASKING,
            SCOPE,
            DiagnosisCoverageStatus.ASSESSED,
            METHOD,
            "labelled test-double coverage",
        ),
    ),  # fmt: skip
    (_candidate("cand-1", ("o-mix",)), _candidate("cand-2", ("o-mix", "o-t2"))),
)


def _request() -> Situation:
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
    capabilities = ActionCapabilityView(
        ProjectId("p1"), Revision("r2"), ContextId("c1"), "1", VersionRef("policy", "1"), VersionRef("renderer", "1"),
        SCOPE, (),
    )  # fmt: skip
    return Situation(
        _state(),
        _context(),
        CONTROLLER,
        OBSERVATIONS,
        BudgetUsage(1, 0, 0, 0, 1.0),
        BudgetLimits(4, 2, 6, 1, 300.0),
        RunId("run-1"),
        SCOPE,
        criteria,
        DIAGNOSIS,
        capabilities,
        DecisionHistory(RunId("run-1"), ProjectId("p1"), ()),
    )


# --- outcome fixtures --------------------------------------------------------------------------------


def _justification(**overrides) -> ActionJustification:
    values = dict(
        reasoning="lowering t2 reduces its share of the mix level",
        uncertainty="uncalibrated; the cited measurement is a test double",
        assumptions=("t2 is the lead",),
        limitations=("no perceptual test",),
    )
    values.update(overrides)
    return ActionJustification(**values)


def _action(track="t2") -> Action:
    return Action(ActionId("x1"), ProjectId("p1"), Revision("r2"), SetTrackGain(TrackId(track), -2.0))


def _proposal(candidates=("cand-1",), **overrides) -> ProposeAction:
    values = dict(
        action=_action(),
        expected_effect="lower track t2",
        candidate_ids=tuple(ProblemCandidateId(c) for c in candidates),
        justification=_justification(),
    )
    values.update(overrides)
    return ProposeAction(**values)


def _decision(outcome=None, evidence=("o-mix",), controller=CONTROLLER) -> Decision:
    return Decision(
        DecisionId("d1"),
        ProjectId("p1"),
        Revision("r2"),
        ContextId("c1"),
        "1",
        tuple(ObservationId(e) for e in evidence),
        "recorded rationale",
        controller,
        _proposal() if outcome is None else outcome,
    )


def _respond(decision) -> None:
    validate_decision_response(_request(), DecisionResponse(decision))


# --- ActionJustification -----------------------------------------------------------------------------


def test_action_justification_fields_are_exact_and_required():
    fields = dataclasses.fields(ActionJustification)
    assert [f.name for f in fields] == ["reasoning", "uncertainty", "assumptions", "limitations"]
    assert all(f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING for f in fields)
    with pytest.raises(TypeError):
        ActionJustification("reasoning", "uncertainty", ())  # type: ignore[call-arg]


def test_action_justification_keeps_values_and_allows_explicit_empty_declarations():
    assumptions, limitations = ("a",), ("l",)
    kept = _justification(assumptions=assumptions, limitations=limitations)
    assert kept.assumptions is assumptions and kept.limitations is limitations
    empty = _justification(assumptions=(), limitations=())
    assert (empty.assumptions, empty.limitations) == ((), ())
    with pytest.raises(dataclasses.FrozenInstanceError):
        kept.reasoning = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"reasoning": " "},
        {"reasoning": None},
        {"uncertainty": ""},
        {"uncertainty": 0.8},  # no numeric confidence
        {"assumptions": ["a"]},
        {"assumptions": ("a", "a")},
        {"assumptions": (" ",)},
        {"limitations": ("x", 1)},
        {"limitations": None},
    ],
)
def test_action_justification_rejects_blank_untyped_and_duplicate_entries(overrides):
    with pytest.raises(ValueError):
        _justification(**overrides)


# --- ProposeAction and NoChange ----------------------------------------------------------------------


def test_propose_action_appends_two_required_fields():
    fields = dataclasses.fields(ProposeAction)
    assert [f.name for f in fields] == ["action", "expected_effect", "candidate_ids", "justification"]
    assert all(f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING for f in fields)
    with pytest.raises(TypeError):
        ProposeAction(_action(), "lower track t2")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "overrides",
    [
        {"candidate_ids": ()},
        {"candidate_ids": [ProblemCandidateId("cand-1")]},
        {"candidate_ids": ("cand-1",)},
        {"candidate_ids": (ProblemCandidateId("cand-1"), ProblemCandidateId("cand-1"))},
        {"justification": "because"},
        {"justification": None},
        {"expected_effect": " "},
        {"action": SetTrackGain(TrackId("t2"), -2.0)},
    ],
)
def test_propose_action_rejects_invalid_candidates_and_justification(overrides):
    with pytest.raises(ValueError):
        _proposal(**overrides)


def test_propose_action_keeps_supplied_values():
    candidate_ids, justification = (ProblemCandidateId("cand-2"), ProblemCandidateId("cand-1")), _justification()
    proposal = _proposal(candidate_ids=candidate_ids, justification=justification)
    assert proposal.candidate_ids is candidate_ids and proposal.justification is justification


def test_no_change_is_an_explicit_outcome_with_required_text_only():
    assert [f.name for f in dataclasses.fields(NoChange)] == ["explanation"]
    with pytest.raises(TypeError):
        NoChange()  # type: ignore[call-arg]
    for bad in (" ", "", None, 0):
        with pytest.raises(ValueError):
            NoChange(bad)  # type: ignore[arg-type]
    kept = NoChange("keep the accepted state; the remaining overlap is intended")
    assert kept.explanation == "keep the accepted state; the remaining overlap is intended"
    assert not isinstance(kept, (ProposeAction, NoChangeFeedback))  # not an action and not a feedback summary
    with pytest.raises(dataclasses.FrozenInstanceError):
        kept.explanation = "x"  # type: ignore[misc]


# --- Abstain and missing context ---------------------------------------------------------------------


def test_abstain_signature_and_reason_values_are_unchanged():
    assert [(f.name, f.default) for f in dataclasses.fields(Abstain)] == [
        ("reason", dataclasses.MISSING),
        ("requested_context", ()),
    ]
    assert [(r.name, r.value) for r in AbstentionReason] == [
        ("INSUFFICIENT_EVIDENCE", "insufficient_evidence"),
        ("MISSING_CONTEXT", "missing_context"),
        ("CONFLICTING_CONSTRAINTS", "conflicting_constraints"),
        ("NO_JUSTIFIED_ACTION", "no_justified_action"),
    ]


def test_missing_context_requires_at_least_one_question():
    with pytest.raises(ValueError):
        Abstain(AbstentionReason.MISSING_CONTEXT)
    with pytest.raises(ValueError):
        Abstain(AbstentionReason.MISSING_CONTEXT, ())
    questions = ("Which track is the low-end anchor, and should t2 keep its level for the chorus?",)
    assert Abstain(AbstentionReason.MISSING_CONTEXT, questions).requested_context is questions


@pytest.mark.parametrize("requested", [("",), ("q", "q"), ["q"], (1,)])
def test_missing_context_questions_keep_the_existing_entry_checks(requested):
    with pytest.raises(ValueError):
        Abstain(AbstentionReason.MISSING_CONTEXT, requested)


@pytest.mark.parametrize("reason", [r for r in AbstentionReason if r is not AbstentionReason.MISSING_CONTEXT])
def test_other_abstention_reasons_still_allow_empty_or_nonempty_requests(reason):
    assert Abstain(reason).requested_context == ()
    assert Abstain(reason, ("optional note",)).requested_context == ("optional note",)


# --- Decision union ----------------------------------------------------------------------------------


def test_decision_outcome_union_adds_no_change():
    hint = typing.get_type_hints(Decision)["outcome"]
    assert typing.get_args(hint) == (ProposeAction, NoChange, Abstain)


@pytest.mark.parametrize("evidence", [(), ("o-mix",)])
def test_no_change_decision_may_cite_evidence_or_none(evidence):
    decision = _decision(NoChange("keep the accepted state"), evidence=evidence)
    assert isinstance(decision.outcome, NoChange)
    _respond(decision)  # structural validity only; no success or criteria evaluation is implied


def test_decision_rejects_an_untyped_outcome_and_keeps_the_action_evidence_rule():
    with pytest.raises(ValueError):
        _decision(NoChangeFeedback("keep"))
    with pytest.raises(ValueError):
        _decision(_proposal(), evidence=())


# --- request-aware candidate and evidence references -------------------------------------------------


def test_proposal_citing_an_existing_candidate_and_its_evidence_is_valid():
    _respond(_decision(_proposal(("cand-1",)), evidence=("o-mix",)))


def test_multiple_selected_candidates_need_the_union_of_their_evidence():
    _respond(_decision(_proposal(("cand-1", "cand-2")), evidence=("o-mix", "o-t2")))
    with pytest.raises(ValueError, match="cand-2"):
        _respond(_decision(_proposal(("cand-1", "cand-2")), evidence=("o-mix",)))


def test_a_candidate_missing_from_the_diagnosis_is_rejected():
    with pytest.raises(ValueError, match="cand-9"):
        _respond(_decision(_proposal(("cand-1", "cand-9")), evidence=("o-mix",)))


def test_selected_candidate_evidence_must_be_cited_by_the_decision():
    with pytest.raises(ValueError, match="o-mix"):
        _respond(_decision(_proposal(("cand-1",)), evidence=("o-t2",)))


def test_additional_decision_evidence_is_allowed():
    _respond(_decision(_proposal(("cand-1",)), evidence=("o-mix", "o-t2", "o-t1")))


def test_unselected_candidates_impose_no_evidence_requirement():
    # cand-2 would need o-t2, but only cand-1 is selected.
    _respond(_decision(_proposal(("cand-1",)), evidence=("o-mix",)))


def test_abstentions_pass_structural_validation():
    questions = ("Which track carries the low end in this section?",)
    _respond(_decision(Abstain(AbstentionReason.MISSING_CONTEXT, questions), evidence=()))
    _respond(_decision(Abstain(AbstentionReason.NO_JUSTIFIED_ACTION), evidence=("o-t1",)))


def test_existing_controller_and_reference_checks_still_apply():
    with pytest.raises(ValueError, match="controller"):
        _respond(_decision(controller=ControllerIdentity("other", "1")))
    with pytest.raises(ValueError, match="missing referenced observations"):
        _respond(_decision(NoChange("keep"), evidence=("o-absent",)))


def test_lower_reference_validator_keeps_its_signature_and_does_not_check_candidates():
    assert list(inspect.signature(validate_decision_references).parameters) == [
        "decision",
        "snapshot",
        "context",
        "observations",
    ]
    # It has no diagnosis report, so a nonexistent candidate passes here; only the response validator rejects it.
    decision = _decision(_proposal(("cand-9",)), evidence=("o-mix",))
    validate_decision_references(decision, _state().snapshot, _context(), OBSERVATIONS)
    with pytest.raises(ValueError):
        _respond(decision)


# --- admission ---------------------------------------------------------------------------------------


def _policy(project="p1") -> ActionAdmissionPolicy:
    return ActionAdmissionPolicy(
        VersionRef("policy", "1"), ProjectId(project), (TrackGainPermission(TrackId("t2"), -6.0, 6.0),)
    )


def _admit(decision, policy=None):
    return admit_action(ActionAdmissionRequest(_request(), decision, _policy() if policy is None else policy))


def test_admission_rejects_a_valid_no_change_as_not_an_action():
    result = _admit(_decision(NoChange("keep the accepted state"), evidence=()))
    assert isinstance(result, RejectedAction) and result.code is AdmissionRejectionCode.NOT_AN_ACTION


def test_admission_keeps_its_failure_order_for_no_change():
    invalid = _admit(_decision(NoChange("keep"), evidence=("o-absent",)))
    assert invalid.code is AdmissionRejectionCode.INVALID_REFERENCES
    mismatch = _admit(_decision(NoChange("keep"), evidence=()), policy=_policy(project="p9"))
    assert mismatch.code is AdmissionRejectionCode.POLICY_PROJECT_MISMATCH


def test_admission_rejects_a_proposal_with_unknown_candidates_as_invalid_references():
    result = _admit(_decision(_proposal(("cand-9",)), evidence=("o-mix",)))
    assert isinstance(result, RejectedAction) and result.code is AdmissionRejectionCode.INVALID_REFERENCES
    assert "cand-9" in result.explanation
