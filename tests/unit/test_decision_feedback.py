"""Compact decision feedback records and the history reference check (feedback-records plan, 2C).

All records are metadata fixtures. Metric names, gain values, reason codes and explanations are structural
examples, not measurements, thresholds or a reason registry. This history is a compact descriptive prefix,
not the complete audit trail.
"""

import dataclasses
import typing
from datetime import datetime, timezone

import pytest

from autonomous_mixing.core.records.actions import Action, ExecutionStatus, RollbackStatus, SetTrackGain
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.budgets import BudgetUsage
from autonomous_mixing.core.records.feedback import (
    AbstentionFeedback,
    ActionFeedback,
    ActionFeedbackDisposition,
    ContextRequestFeedback,
    DecisionFeedback,
    DecisionHistory,
    FeedbackOutcome,
    FeedbackStage,
    InvalidResponseFeedback,
    NoChangeFeedback,
    NotDispatchedFeedback,
    ObservationFeedback,
    PortFailureFeedback,
    validate_decision_history,
)
from autonomous_mixing.core.records.identifiers import (
    ActionId,
    ArtifactId,
    AttemptId,
    CheckpointId,
    ConditionId,
    CriteriaId,
    DecisionCallId,
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
)
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode
from autonomous_mixing.core.records.verification import (
    AssessmentMode,
    CheckResult,
    CheckStatus,
    TargetStatus,
    Verdict,
    VerificationResult,
)

UTC_NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
METRIC = VersionRef("metric-a", "1")

Disp = ActionFeedbackDisposition
Stage = FeedbackStage
Exec = ExecutionStatus
Roll = RollbackStatus


# --- fixtures ----------------------------------------------------------------------------------------


def _observation(oid="o1", sources=("mix-r1",), value=-14.0) -> Observation:
    return Observation(
        ObservationId(oid),
        METRIC,
        tuple(ArtifactId(a) for a in sources),
        AnalysisScope((TrackId("t1"),), TimeRange(0.0, 60.0)),
        MeasuredScalar(value, "unit-a"),
        VersionRef("spec", "1"),
        VersionRef("analyzer", "0.1"),
        UTC_NOW,
    )


def _obs_fb(revision="r1", oid="o1", **kwargs) -> ObservationFeedback:
    return ObservationFeedback(Revision(revision), _observation(oid, **kwargs))


def _verification(verdict=Verdict.ACCEPTED, target=TargetStatus.UNMET, evidence=("o1", "o2"), mode=None):
    mode = AssessmentMode.ATTEMPT if mode is None else mode
    status = CheckStatus.PASS if verdict is Verdict.ACCEPTED else CheckStatus.FAIL
    check = CheckResult(ConditionId("c1"), status, "recorded check", tuple(ObservationId(e) for e in evidence))
    return VerificationResult(VerificationResultId("v1"), CriteriaId("crit"), "1", mode, (check,), verdict, target)


def _action(aid="a1", revision="r1", gain=-2.0, project="p1") -> Action:
    return Action(ActionId(aid), ProjectId(project), Revision(revision), SetTrackGain(TrackId("t1"), gain))


ACCEPTED_OBS = (_obs_fb("r1", "o1"), _obs_fb("r2", "o2", sources=("mix-r2",), value=-12.0))


def _action_fb(disposition=Disp.ACCEPTED, **overrides) -> ActionFeedback:
    """A constructor-valid ActionFeedback for each disposition; ``overrides`` replace single fields."""
    shapes = {
        Disp.NOT_EXECUTED: dict(attempt=None, execution=None, stage=Stage.ADMISSION, status=None),
        Disp.REJECTED: dict(stage=Stage.ADMISSION, status=None, execution=None),
        Disp.ACCEPTED: dict(stage=Stage.ACCEPTANCE, status=Exec.CANDIDATE_READY, verification=_verification()),
        Disp.ROLLED_BACK: dict(stage=Stage.RECOVERY, status=Exec.FAILED, rollback=Roll.VERIFIED),
        Disp.FAILED: dict(stage=Stage.RENDER, status=Exec.FAILED),
        Disp.UNRESOLVED: dict(stage=Stage.RENDER, status=Exec.UNCERTAIN),
    }
    shape = {"attempt": "att-1", "execution": "ex-1", "verification": None, "rollback": None, **shapes[disposition]}
    values = dict(
        action=_action(),
        attempt_id=None if shape["attempt"] is None else AttemptId(shape["attempt"]),
        execution_id=None if shape["execution"] is None else ExecutionId(shape["execution"]),
        disposition=disposition,
        stage=shape["stage"],
        execution_status=shape["status"],
        verification=shape["verification"],
        rollback_status=shape["rollback"],
        reason_code="recorded_code",
        explanation="recorded outcome",
        observations=ACCEPTED_OBS if shape["verification"] is not None else (),
    )
    return ActionFeedback(**{**values, **overrides})


def _usage(calls=1, attempts=0, renders=0, recoveries=0, elapsed=1.0) -> BudgetUsage:
    return BudgetUsage(calls, attempts, renders, recoveries, elapsed)


def _entry(outcome=None, *, sequence=1, before="r1", candidate=..., selected=..., decision="d1", call=None, **kw):
    """A DecisionFeedback whose default selection follows the disposition (candidate, before or unknown)."""
    outcome = _action_fb() if outcome is None else outcome
    if isinstance(outcome, ActionFeedback):
        ready = outcome.execution_status is Exec.CANDIDATE_READY
        candidate = ("r2" if ready else None) if candidate is ... else candidate
        if selected is ...:
            selected = {Disp.ACCEPTED: candidate, Disp.UNRESOLVED: None}.get(outcome.disposition, before)
    else:
        candidate = None if candidate is ... else candidate
        selected = before if selected is ... else selected
    values = dict(
        call_id=DecisionCallId(call or f"call-{sequence}"),
        sequence=sequence,
        run_id=RunId("run-1"),
        project_id=ProjectId("p1"),
        before_revision=Revision(before),
        candidate_revision=None if candidate is None else Revision(candidate),
        selected_revision=None if selected is None else Revision(selected),
        decision_id=None if decision is None else DecisionId(decision),
        usage=_usage(calls=sequence),
        outcome=outcome,
    )
    return DecisionFeedback(**{**values, **kw})


# --- identifier, enums, union, shapes ----------------------------------------------------------------


def test_decision_call_id_follows_the_identifier_pattern():
    assert str(DecisionCallId("call-1")) == "call-1"
    assert DecisionCallId("call-1") != DecisionId("call-1")
    for bad in ("", " ", None, 1):
        with pytest.raises(ValueError):
            DecisionCallId(bad)


def test_enum_names_and_values_are_exact():
    assert [(m.name, m.value) for m in ActionFeedbackDisposition] == [
        ("NOT_EXECUTED", "not_executed"),
        ("REJECTED", "rejected"),
        ("ACCEPTED", "accepted"),
        ("ROLLED_BACK", "rolled_back"),
        ("FAILED", "failed"),
        ("UNRESOLVED", "unresolved"),
    ]
    assert [(m.name, m.value) for m in FeedbackStage] == [
        ("EVIDENCE_GATE", "evidence_gate"),
        ("ADMISSION", "admission"),
        ("PREFLIGHT", "preflight"),
        ("PREPARATION", "preparation"),
        ("RENDER", "render"),
        ("VERIFICATION", "verification"),
        ("RECOVERY", "recovery"),
        ("ACCEPTANCE", "acceptance"),
        ("RECORDING", "recording"),
    ]


def test_feedback_outcome_union_members_are_exact():
    assert typing.get_args(FeedbackOutcome) == (
        ActionFeedback,
        NoChangeFeedback,
        AbstentionFeedback,
        ContextRequestFeedback,
        InvalidResponseFeedback,
        PortFailureFeedback,
        NotDispatchedFeedback,
    )


@pytest.mark.parametrize(
    ("record", "fields"),
    [
        (ObservationFeedback, [("state_revision", Revision), ("observation", Observation)]),
        (
            ActionFeedback,
            [
                ("action", Action),
                ("attempt_id", AttemptId | None),
                ("execution_id", ExecutionId | None),
                ("disposition", ActionFeedbackDisposition),
                ("stage", FeedbackStage),
                ("execution_status", ExecutionStatus | None),
                ("verification", VerificationResult | None),
                ("rollback_status", RollbackStatus | None),
                ("reason_code", str),
                ("explanation", str),
                ("observations", tuple[ObservationFeedback, ...]),
            ],
        ),
        (NoChangeFeedback, [("explanation", str)]),
        (AbstentionFeedback, [("reason_code", str), ("explanation", str)]),
        (ContextRequestFeedback, [("questions", tuple[str, ...]), ("explanation", str)]),
        (InvalidResponseFeedback, [("reason_code", str), ("explanation", str)]),
        (PortFailureFeedback, [("failure", PortFailure)]),
        (NotDispatchedFeedback, [("reason_code", str), ("explanation", str)]),
        (
            DecisionFeedback,
            [
                ("call_id", DecisionCallId),
                ("sequence", int),
                ("run_id", RunId),
                ("project_id", ProjectId),
                ("before_revision", Revision),
                ("candidate_revision", Revision | None),
                ("selected_revision", Revision | None),
                ("decision_id", DecisionId | None),
                ("usage", BudgetUsage),
                ("outcome", FeedbackOutcome),
            ],
        ),
    ],
)
def test_record_fields_are_exact_and_required(record, fields):
    assert [(f.name, f.type) for f in dataclasses.fields(record)] == fields
    assert all(
        f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
        for f in dataclasses.fields(record)
    )


@pytest.mark.parametrize(
    ("instance", "field"),
    [
        (_obs_fb(), "state_revision"),
        (_action_fb(), "disposition"),
        (NoChangeFeedback("keep"), "explanation"),
        (ContextRequestFeedback(("Which track leads?",), "role missing"), "questions"),
        (_entry(), "selected_revision"),
    ],
)
def test_records_are_frozen(instance, field):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field, None)


# --- simple payloads ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [
        lambda text: NoChangeFeedback(text),
        lambda text: AbstentionFeedback(text, "x"),
        lambda text: AbstentionFeedback("insufficient_evidence", text),
        lambda text: ContextRequestFeedback(("q",), text),
        lambda text: InvalidResponseFeedback(text, "x"),
        lambda text: InvalidResponseFeedback("schema", text),
        lambda text: NotDispatchedFeedback(text, "x"),
        lambda text: NotDispatchedFeedback("deadline", text),
    ],
)
@pytest.mark.parametrize("bad", ["", "   ", None, 1])
def test_payload_text_fields_must_be_non_blank_strings(factory, bad):
    with pytest.raises(ValueError):
        factory(bad)


def test_payload_text_is_preserved_exactly():
    assert NoChangeFeedback("  keep as is\n").explanation == "  keep as is\n"
    assert AbstentionFeedback("custom_recorded_code", " x").reason_code == "custom_recorded_code"


def test_missing_context_abstention_requires_a_context_request_with_questions():
    with pytest.raises(ValueError, match="ContextRequestFeedback"):
        AbstentionFeedback("missing_context", "role unknown")
    request = ContextRequestFeedback(("Which track should stay most prominent?",), "role unknown")
    assert request.questions == ("Which track should stay most prominent?",)


@pytest.mark.parametrize("questions", [(), ["q"], ("q", "q"), ("q", " "), ("q", 1), "q"])
def test_context_request_questions_must_be_a_nonempty_tuple_of_distinct_texts(questions):
    with pytest.raises(ValueError):
        ContextRequestFeedback(questions, "role unknown")


def test_port_failure_feedback_requires_a_port_failure():
    failure = PortFailure(PortFailureCode.UNAVAILABLE, "service unavailable", True)
    assert PortFailureFeedback(failure).failure is failure
    with pytest.raises(ValueError):
        PortFailureFeedback("unavailable")


def test_observation_feedback_type_checks_its_fields():
    assert _obs_fb("r0").state_revision == Revision("r0")
    with pytest.raises(ValueError):
        ObservationFeedback("r1", _observation())
    with pytest.raises(ValueError):
        ObservationFeedback(Revision("r1"), _observation().scope)


# --- ActionFeedback ------------------------------------------------------------------------------------


@pytest.mark.parametrize("disposition", list(ActionFeedbackDisposition))
def test_every_disposition_has_a_valid_shape(disposition):
    assert _action_fb(disposition).disposition is disposition


@pytest.mark.parametrize(
    "overrides",
    [
        {"action": "a1"},
        {"attempt_id": "att-1"},
        {"execution_id": "ex-1"},
        {"disposition": "accepted"},
        {"stage": "acceptance"},
        {"execution_status": "candidate_ready"},
        {"verification": "accepted"},
        {"rollback_status": "verified"},
        {"reason_code": ""},
        {"explanation": " "},
        {"observations": list(ACCEPTED_OBS)},
        {"observations": (_observation("o1"), _observation("o2"))},
    ],
)
def test_action_feedback_rejects_wrong_types(overrides):
    with pytest.raises(ValueError):
        dataclasses.replace(_action_fb(), **overrides)


def test_not_executed_may_lack_an_attempt_but_other_dispositions_may_not():
    assert _action_fb(Disp.NOT_EXECUTED).attempt_id is None
    for disposition in (Disp.REJECTED, Disp.ACCEPTED, Disp.ROLLED_BACK, Disp.FAILED, Disp.UNRESOLVED):
        with pytest.raises(ValueError, match="AttemptId"):
            _action_fb(disposition, attempt_id=None)


@pytest.mark.parametrize(
    "overrides",
    [
        {"execution_id": None},
        {"execution_status": None},
    ],
)
def test_execution_id_and_status_are_both_present_or_both_absent(overrides):
    with pytest.raises(ValueError, match="execution"):
        _action_fb(Disp.FAILED, **overrides)


def test_rollback_status_requires_an_execution_id():
    with pytest.raises(ValueError):
        _action_fb(Disp.ROLLED_BACK, execution_id=None, execution_status=None)


def test_verification_requires_attempt_mode_and_a_ready_candidate():
    initial = _verification(target=TargetStatus.SATISFIED, mode=AssessmentMode.INITIAL)
    with pytest.raises(ValueError, match="ATTEMPT"):
        _action_fb(verification=initial)
    with pytest.raises(ValueError, match="CANDIDATE_READY"):
        _action_fb(Disp.ROLLED_BACK, verification=_verification(), observations=ACCEPTED_OBS)


def test_verification_evidence_must_be_among_the_copied_observations():
    with pytest.raises(ValueError, match="o2"):
        _action_fb(observations=(ACCEPTED_OBS[0],))


def test_initial_baseline_observations_may_accompany_attempt_evidence():
    observations = (_obs_fb("r0", "o0", sources=("stem-r0",)), *ACCEPTED_OBS)
    assert _action_fb(observations=observations).observations == observations


def test_observation_pairs_are_unique_and_reused_ids_need_identical_contents():
    duplicate = (*ACCEPTED_OBS, ACCEPTED_OBS[0])
    with pytest.raises(ValueError, match="duplicate"):
        _action_fb(observations=duplicate)
    same_contents_other_state = (*ACCEPTED_OBS, _obs_fb("r2", "o1"))
    assert _action_fb(observations=same_contents_other_state)
    conflicting = (*ACCEPTED_OBS, _obs_fb("r0", "o1", value=-20.0))
    with pytest.raises(ValueError, match="o1"):
        _action_fb(observations=conflicting)


@pytest.mark.parametrize(
    ("disposition", "overrides"),
    [
        # NOT_EXECUTED: no execution beyond NOT_STARTED, no verification or rollback, permitted stages.
        (Disp.NOT_EXECUTED, {"stage": Stage.ACCEPTANCE}),
        (Disp.NOT_EXECUTED, {"stage": Stage.VERIFICATION}),
        (Disp.NOT_EXECUTED, {"stage": Stage.RECOVERY}),
        (
            Disp.NOT_EXECUTED,
            {"attempt_id": AttemptId("a"), "execution_id": ExecutionId("e"), "execution_status": Exec.FAILED},
        ),
        # REJECTED: before candidate execution only.
        (Disp.REJECTED, {"stage": Stage.RENDER}),
        (Disp.REJECTED, {"execution_id": ExecutionId("e"), "execution_status": Exec.CANDIDATE_READY}),
        (
            Disp.REJECTED,
            {"execution_id": ExecutionId("e"), "execution_status": Exec.NOT_STARTED, "rollback_status": Roll.VERIFIED},
        ),
        # ACCEPTED: acceptance of a ready candidate with an ACCEPTED verification and no rollback.
        (Disp.ACCEPTED, {"stage": Stage.VERIFICATION}),
        (Disp.ACCEPTED, {"verification": None, "observations": ()}),
        (Disp.ACCEPTED, {"verification": _verification(Verdict.REJECTED)}),
        (Disp.ACCEPTED, {"rollback_status": Roll.VERIFIED}),
        (Disp.ACCEPTED, {"execution_status": Exec.FAILED, "verification": None, "observations": ()}),
        # ROLLED_BACK: recovery with a VERIFIED rollback.
        (Disp.ROLLED_BACK, {"stage": Stage.RENDER}),
        (Disp.ROLLED_BACK, {"rollback_status": Roll.UNCERTAIN}),
        (Disp.ROLLED_BACK, {"rollback_status": None}),
        # FAILED: execution NOT_STARTED or FAILED, never labelled accepted or restored.
        (Disp.FAILED, {"execution_status": Exec.CANDIDATE_READY}),
        (Disp.FAILED, {"execution_status": Exec.UNCERTAIN}),
        (Disp.FAILED, {"rollback_status": Roll.VERIFIED}),
        (Disp.FAILED, {"stage": Stage.ACCEPTANCE}),
        (Disp.FAILED, {"execution_id": None, "execution_status": None}),
        # UNRESOLVED: uncertain execution or failed/uncertain rollback; never a verified restoration.
        (Disp.UNRESOLVED, {"execution_status": Exec.FAILED}),
        (Disp.UNRESOLVED, {"execution_status": Exec.FAILED, "rollback_status": Roll.VERIFIED}),
        (Disp.UNRESOLVED, {"execution_status": Exec.NOT_STARTED, "rollback_status": Roll.VERIFIED}),
        (Disp.UNRESOLVED, {"stage": Stage.ADMISSION}),
    ],
)
def test_contradictory_disposition_shapes_are_rejected(disposition, overrides):
    with pytest.raises(ValueError):
        _action_fb(disposition, **overrides)


@pytest.mark.parametrize(
    ("disposition", "overrides"),
    [
        (Disp.NOT_EXECUTED, {"stage": Stage.EVIDENCE_GATE}),
        (Disp.NOT_EXECUTED, {"stage": Stage.RECORDING}),
        (
            Disp.NOT_EXECUTED,
            {"attempt_id": AttemptId("a"), "execution_id": ExecutionId("e"), "execution_status": Exec.NOT_STARTED},
        ),
        (Disp.REJECTED, {"stage": Stage.PREFLIGHT}),
        (Disp.ACCEPTED, {"verification": _verification(target=TargetStatus.SATISFIED)}),
        (Disp.ROLLED_BACK, {"execution_status": Exec.UNCERTAIN}),
        (Disp.FAILED, {"execution_status": Exec.NOT_STARTED, "stage": Stage.PREPARATION}),
        (Disp.UNRESOLVED, {"execution_status": Exec.FAILED, "rollback_status": Roll.FAILED, "stage": Stage.RECOVERY}),
        (Disp.UNRESOLVED, {"execution_status": Exec.CANDIDATE_READY, "rollback_status": Roll.UNCERTAIN}),
    ],
)
def test_permitted_disposition_variants_construct(disposition, overrides):
    assert _action_fb(disposition, **overrides).disposition is disposition


@pytest.mark.parametrize(
    "stage",
    [Stage.PREPARATION, Stage.RENDER, Stage.VERIFICATION, Stage.RECOVERY, Stage.ACCEPTANCE, Stage.RECORDING],
)
def test_uncertain_execution_with_verified_rollback_remains_unresolved(stage):
    # Restoring the saved reference does not account for every effect of an uncertain execution.
    feedback = _action_fb(Disp.UNRESOLVED, stage=stage, execution_status=Exec.UNCERTAIN, rollback_status=Roll.VERIFIED)
    assert (feedback.disposition, feedback.execution_status, feedback.rollback_status) == (
        Disp.UNRESOLVED,
        Exec.UNCERTAIN,
        Roll.VERIFIED,
    )
    entry = _entry(feedback, selected="r1")
    assert (entry.candidate_revision, entry.selected_revision) == (None, Revision("r1"))
    assert entry.outcome.disposition is Disp.UNRESOLVED


def test_accepted_verification_followed_by_verified_rollback_after_recording_failure():
    feedback = _action_fb(
        Disp.ROLLED_BACK,
        execution_status=Exec.CANDIDATE_READY,
        verification=_verification(),
        observations=ACCEPTED_OBS,
        reason_code="recording_failed",
    )
    assert feedback.verification.verdict is Verdict.ACCEPTED and feedback.rollback_status is Roll.VERIFIED


# --- DecisionFeedback ----------------------------------------------------------------------------------


def test_accepted_action_with_unmet_targets_selects_its_candidate():
    entry = _entry()
    assert (entry.candidate_revision, entry.selected_revision) == (Revision("r2"), Revision("r2"))
    assert entry.outcome.verification.target_status is TargetStatus.UNMET


@pytest.mark.parametrize("sequence", [0, -1, True, 1.0, "1"])
def test_sequence_must_be_a_positive_integer(sequence):
    with pytest.raises(ValueError):
        _entry(sequence=sequence)


def test_usage_decision_calls_must_equal_the_sequence():
    with pytest.raises(ValueError, match="decision_calls"):
        _entry(sequence=2, usage=_usage(calls=1))
    assert _entry(sequence=2).usage.decision_calls == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"call_id": "call-1"},
        {"run_id": "run-1"},
        {"project_id": "p1"},
        {"before_revision": "r1"},
        {"candidate_revision": "r2"},
        {"selected_revision": "r2"},
        {"decision_id": "d1"},
        {"usage": (1, 0, 0, 0, 1.0)},
        {"outcome": "accepted"},
        {"outcome": _obs_fb()},
    ],
)
def test_entry_rejects_wrong_types(overrides):
    with pytest.raises(ValueError):
        _entry(**overrides)


def test_action_entry_requires_a_decision_and_matching_project_and_before_revision():
    with pytest.raises(ValueError, match="DecisionId"):
        _entry(decision=None)
    with pytest.raises(ValueError, match="project"):
        _entry(_action_fb(action=_action(project="p2")))
    with pytest.raises(ValueError, match="before"):
        _entry(_action_fb(action=_action(revision="r0")))


def test_candidate_revision_follows_the_execution_status():
    with pytest.raises(ValueError, match="candidate"):
        _entry(candidate=None, selected=None)
    with pytest.raises(ValueError, match="candidate"):
        _entry(candidate="r1", selected="r1")
    with pytest.raises(ValueError, match="candidate"):
        _entry(_action_fb(Disp.FAILED), candidate="r2")


@pytest.mark.parametrize(
    ("disposition", "selected"),
    [
        (Disp.ACCEPTED, "r1"),
        (Disp.ACCEPTED, None),
        (Disp.REJECTED, "r2"),
        (Disp.REJECTED, None),
        (Disp.NOT_EXECUTED, None),
        (Disp.FAILED, None),
        (Disp.ROLLED_BACK, None),
    ],
)
def test_selected_revision_must_follow_the_disposition(disposition, selected):
    with pytest.raises(ValueError, match="select"):
        _entry(_action_fb(disposition), selected=selected)


def test_rejected_candidate_never_becomes_the_selected_state():
    rolled_back = _action_fb(
        Disp.ROLLED_BACK,
        execution_status=Exec.CANDIDATE_READY,
        verification=_verification(Verdict.REJECTED),
        observations=ACCEPTED_OBS,
    )
    assert _entry(rolled_back).selected_revision == Revision("r1")
    with pytest.raises(ValueError, match="select"):
        _entry(rolled_back, selected="r2")


def test_unresolved_selects_the_known_before_state_or_nothing_but_never_a_candidate():
    unresolved = _action_fb(Disp.UNRESOLVED, execution_status=Exec.CANDIDATE_READY, rollback_status=Roll.UNCERTAIN)
    assert _entry(unresolved, selected=None).selected_revision is None
    assert _entry(unresolved, selected="r1").selected_revision == Revision("r1")
    with pytest.raises(ValueError, match="select"):
        _entry(unresolved, selected="r2")


@pytest.mark.parametrize(
    "outcome",
    [
        NoChangeFeedback("current balance kept"),
        AbstentionFeedback("insufficient_evidence", "diagnosis incomplete"),
        ContextRequestFeedback(("Which track should stay most prominent?",), "role missing"),
    ],
)
def test_parsed_non_action_outcomes_require_a_decision_id(outcome):
    entry = _entry(outcome)
    assert (entry.candidate_revision, entry.selected_revision) == (None, Revision("r1"))
    with pytest.raises(ValueError, match="DecisionId"):
        _entry(outcome, decision=None)


@pytest.mark.parametrize(
    "outcome",
    [
        InvalidResponseFeedback("schema_violation", "response did not parse"),
        PortFailureFeedback(PortFailure(PortFailureCode.UNAVAILABLE, "service unavailable", True)),
        NotDispatchedFeedback("deadline_reached", "slot charged; provider not invoked"),
    ],
)
def test_failed_or_undispatched_calls_have_no_decision_id(outcome):
    entry = _entry(outcome, decision=None)
    assert entry.decision_id is None and entry.selected_revision == Revision("r1")
    with pytest.raises(ValueError, match="DecisionId"):
        _entry(outcome)


@pytest.mark.parametrize(
    "outcome",
    [NoChangeFeedback("keep"), InvalidResponseFeedback("schema", "bad"), NotDispatchedFeedback("deadline", "late")],
)
def test_non_action_outcomes_keep_before_selected_and_no_candidate(outcome):
    decision = "d1" if isinstance(outcome, NoChangeFeedback) else None
    with pytest.raises(ValueError):
        _entry(outcome, decision=decision, candidate="r2")
    with pytest.raises(ValueError):
        _entry(outcome, decision=decision, selected="r2")
    with pytest.raises(ValueError):
        _entry(outcome, decision=decision, selected=None)


def test_no_change_is_distinct_from_a_zero_gain_action():
    no_change = _entry(NoChangeFeedback("current balance kept"))
    zero_gain = _entry(_action_fb(Disp.REJECTED, action=_action(gain=0.0)))
    assert isinstance(no_change.outcome, NoChangeFeedback)
    assert isinstance(zero_gain.outcome, ActionFeedback) and zero_gain.outcome.action.operation.value_db == 0.0


def test_not_executed_proposal_without_attempt_is_representable():
    entry = _entry(_action_fb(Disp.NOT_EXECUTED, stage=Stage.EVIDENCE_GATE))
    assert entry.outcome.attempt_id is None and entry.selected_revision == Revision("r1")


# --- DecisionHistory: scenario fixtures ----------------------------------------------------------------

FAILURE = PortFailureFeedback(PortFailure(PortFailureCode.UNAVAILABLE, "service unavailable", True))
# Call 3's rejected candidate r3 cites o2 at r2 again (unchanged contents) and a new candidate observation o3.
ROLLBACK_OBS = (_obs_fb("r2", "o2", sources=("mix-r2",), value=-12.0), _obs_fb("r3", "o3", sources=("mix-r3",)))


def _scenario() -> tuple[DecisionFeedback, ...]:
    """Transient call failure, then an accepted intermediate edit, then a rolled-back rejected candidate."""
    failed_call = _entry(FAILURE, sequence=1, decision=None, usage=_usage(1, elapsed=1.0))
    accepted = _entry(_action_fb(), sequence=2, decision="d2", usage=_usage(2, 1, 1, 0, 3.0))
    rejected = _action_fb(
        Disp.ROLLED_BACK,
        action=_action("a2", "r2", gain=-3.0),
        attempt_id=AttemptId("att-2"),
        execution_id=ExecutionId("ex-2"),
        execution_status=Exec.CANDIDATE_READY,
        verification=_verification(Verdict.REJECTED, evidence=("o2", "o3")),
        observations=ROLLBACK_OBS,
    )
    rolled_back = _entry(
        rejected, sequence=3, before="r2", candidate="r3", decision="d3", usage=_usage(3, 2, 2, 1, 5.0)
    )
    return failed_call, accepted, rolled_back


def _history(entries=None, *, run="run-1", project="p1") -> DecisionHistory:
    return DecisionHistory(RunId(run), ProjectId(project), _scenario() if entries is None else entries)


def _state(revision="r2", project="p1") -> ProjectState:
    snapshot = ProjectSnapshot(ProjectId(project), Revision(revision), UTC_NOW, (Track(TrackId("t1"), "Choir"),))
    processing = ProjectProcessingState(ProjectId(project), Revision(revision), ())
    # The current checkpoint holds only mix-c: historical sources (mix-r1, mix-r2, mix-r3) are not members.
    mix = AudioArtifact(
        ArtifactId("mix-c"),
        ProjectId(project),
        Revision(revision),
        (TrackId("t1"),),
        ArtifactKind.MIX,
        "outputs/mix-c.wav",
        0.0,
        48000,
        2,
        48000 * 60,
        "d" * 64,
        VersionRef("render", "1"),
    )
    audio = ArtifactCheckpoint(CheckpointId("cp"), ProjectId(project), Revision(revision), (mix,))
    return ProjectState(snapshot, processing, audio)


def _replace(entries, index, **changes):
    items = list(entries)
    items[index] = dataclasses.replace(items[index], **changes)
    return tuple(items)


# --- DecisionHistory -----------------------------------------------------------------------------------


def test_empty_history_is_valid():
    assert _history(()).entries == ()


def test_scenario_history_keeps_order_and_objects():
    entries = _scenario()
    history = _history(entries)
    assert history.entries is entries
    assert [e.selected_revision for e in history.entries] == [Revision("r1"), Revision("r2"), Revision("r2")]


def test_accepted_intermediate_state_becomes_the_next_before_state():
    entries = _scenario()
    assert entries[2].before_revision == entries[1].selected_revision == entries[1].candidate_revision


def test_rollback_preserves_the_previously_accepted_reference():
    later = _entry(NoChangeFeedback("keep"), sequence=4, before="r2", decision="d4", usage=_usage(4, 2, 2, 1, 6.0))
    assert _history((*_scenario(), later)).entries[-1].before_revision == Revision("r2")


@pytest.mark.parametrize(
    "overrides",
    [{"run": ""}, {"project": ""}],
)
def test_history_rejects_invalid_identity(overrides):
    with pytest.raises(ValueError):
        _history(**overrides)


def test_history_rejects_wrong_entry_collections():
    with pytest.raises(ValueError):
        DecisionHistory(RunId("run-1"), ProjectId("p1"), list(_scenario()))
    with pytest.raises(ValueError):
        DecisionHistory(RunId("run-1"), ProjectId("p1"), (_action_fb(),))
    with pytest.raises(ValueError):
        DecisionHistory("run-1", ProjectId("p1"), ())


def test_entries_must_match_the_history_run_and_project():
    with pytest.raises(ValueError, match="run"):
        _history(run="run-2")
    with pytest.raises(ValueError, match="project"):
        _history(project="p2")


def test_sequences_must_be_exactly_one_to_n():
    a, b, c = _scenario()
    with pytest.raises(ValueError, match="sequence"):
        _history((a, c, b))
    with pytest.raises(ValueError, match="sequence"):
        _history((b,))
    with pytest.raises(ValueError, match="sequence"):
        _history((a, a))


@pytest.mark.parametrize(
    ("index", "changes", "label"),
    [
        (1, {"call_id": DecisionCallId("call-1")}, "call"),
        (2, {"decision_id": DecisionId("d2")}, "decision"),
    ],
)
def test_duplicate_call_and_decision_ids_are_rejected(index, changes, label):
    with pytest.raises(ValueError, match=label):
        _history(_replace(_scenario(), index, **changes))


@pytest.mark.parametrize(
    ("field", "value", "label"),
    [
        ("action", _action("a1", "r2", gain=-3.0), "action"),
        ("attempt_id", AttemptId("att-1"), "attempt"),
        ("execution_id", ExecutionId("ex-1"), "execution"),
    ],
)
def test_duplicate_action_attempt_and_execution_ids_are_rejected(field, value, label):
    entries = _scenario()
    outcome = dataclasses.replace(entries[2].outcome, **{field: value})
    with pytest.raises(ValueError, match=label):
        _history(_replace(entries, 2, outcome=outcome))


def test_stale_before_revision_breaks_the_chain():
    entries = _scenario()
    stale_outcome = dataclasses.replace(entries[2].outcome, action=_action("a2", "r1", gain=-3.0))
    with pytest.raises(ValueError, match="before"):
        _history(
            _replace(
                entries, 2, before_revision=Revision("r1"), selected_revision=Revision("r1"), outcome=stale_outcome
            )
        )


def test_candidate_revision_reusing_an_earlier_before_revision_is_rejected():
    with pytest.raises(ValueError, match="candidate"):
        _history(_replace(_scenario(), 2, candidate_revision=Revision("r1")))


def test_candidate_revision_reusing_a_rejected_candidate_is_rejected():
    retry = _action_fb(
        Disp.ROLLED_BACK,
        action=_action("a4", "r2", gain=-4.0),
        attempt_id=AttemptId("att-4"),
        execution_id=ExecutionId("ex-4"),
        execution_status=Exec.CANDIDATE_READY,
    )
    later = _entry(retry, sequence=4, before="r2", candidate="r3", decision="d4", usage=_usage(4, 3, 3, 2, 7.0))
    with pytest.raises(ValueError, match="candidate"):
        _history((*_scenario(), later))


@pytest.mark.parametrize(
    ("index", "usage"),
    [
        (2, _usage(3, 0, 2, 1, 5.0)),
        (2, _usage(3, 2, 0, 1, 5.0)),
        (1, _usage(2, 1, 1, 2, 3.0)),
        (2, _usage(3, 2, 2, 1, 2.0)),
    ],
    ids=["attempts", "renders", "recoveries", "elapsed"],
)
def test_usage_must_not_decrease(index, usage):
    with pytest.raises(ValueError, match="usage"):
        _history(_replace(_scenario(), index, usage=usage))


def test_recorded_overruns_are_kept():
    entries = _replace(_scenario(), 2, usage=_usage(3, 9, 9, 9, 900.0))
    assert _history(entries).entries[2].usage.attempts == 9


def _unresolved_second_entry() -> DecisionFeedback:
    unresolved = _action_fb(Disp.UNRESOLVED, execution_status=Exec.CANDIDATE_READY, rollback_status=Roll.UNCERTAIN)
    return _entry(unresolved, sequence=2, decision="d2", selected=None, usage=_usage(2, 1, 1, 1, 3.0))


def test_unresolved_last_entry_is_representable():
    history = _history((_scenario()[0], _unresolved_second_entry()))
    assert history.entries[-1].selected_revision is None


def test_no_continuation_after_an_unknown_selected_state():
    later = _entry(NoChangeFeedback("keep"), sequence=3, before="r1", decision="d3", usage=_usage(3, 1, 1, 1, 4.0))
    with pytest.raises(ValueError, match="unknown"):
        _history((_scenario()[0], _unresolved_second_entry(), later))


def test_initial_before_and_candidate_observation_revisions_are_accepted():
    entries = _scenario()
    observations = (_obs_fb("r1", "o1"), *ROLLBACK_OBS)  # initial before (r1), before (r2) and candidate (r3)
    outcome = dataclasses.replace(entries[2].outcome, observations=observations)
    assert _history(_replace(entries, 2, outcome=outcome))


@pytest.mark.parametrize("revision", ["r3", "r9"], ids=["future", "unknown"])
def test_observation_revisions_must_be_visible_by_their_entry(revision):
    entries = _scenario()
    observations = (*ACCEPTED_OBS, _obs_fb(revision, "o-late", sources=("mix-late",)))
    outcome = dataclasses.replace(entries[1].outcome, observations=observations)
    with pytest.raises(ValueError, match=revision):
        _history(_replace(entries, 1, outcome=outcome))


def test_one_observation_id_denotes_identical_contents_across_the_history():
    entries = _scenario()
    conflicting = (_obs_fb("r2", "o2", sources=("mix-r2",), value=-99.0), ROLLBACK_OBS[1])
    outcome = dataclasses.replace(entries[2].outcome, observations=conflicting)
    with pytest.raises(ValueError, match="o2"):
        _history(_replace(entries, 2, outcome=outcome))


def test_identical_reuse_in_different_states_and_calls_is_allowed():
    entries = _scenario()
    observations = (_obs_fb("r1", "o1"), _obs_fb("r2", "o1"), *ROLLBACK_OBS)
    outcome = dataclasses.replace(entries[2].outcome, observations=observations)
    assert _history(_replace(entries, 2, outcome=outcome))


# --- validate_decision_history ------------------------------------------------------------------------


def _check(history=None, *, state=None, run="run-1", usage=None):
    return validate_decision_history(
        _history() if history is None else history,
        state=_state() if state is None else state,
        run_id=RunId(run),
        usage=_usage(4, 2, 2, 1, 6.0) if usage is None else usage,
    )


def test_empty_history_needs_exactly_one_charged_decision_slot():
    assert _check(_history(()), state=_state("r1"), usage=_usage(1)) is None
    for calls in (0, 2):
        with pytest.raises(ValueError, match="decision_calls"):
            _check(_history(()), state=_state("r1"), usage=_usage(calls))


def test_valid_history_links_to_the_next_decision_input():
    assert _check() is None


def test_historical_sources_are_not_checked_against_the_current_checkpoint():
    # Observations cite mix-r1/mix-r2/mix-r3 with older production revisions; the current state holds only mix-c.
    assert {str(a) for e in _scenario()[1:] for o in e.outcome.observations for a in o.observation.artifact_ids} == {
        "mix-r1",
        "mix-r2",
        "mix-r3",
    }
    assert _check() is None


@pytest.mark.parametrize(
    ("history", "state", "run_id", "usage"),
    [
        (_history().entries, _state(), RunId("run-1"), _usage(4, 2, 2, 1, 6.0)),
        (_history(), _state().snapshot, RunId("run-1"), _usage(4, 2, 2, 1, 6.0)),
        (_history(), _state(), "run-1", _usage(4, 2, 2, 1, 6.0)),
        (_history(), _state(), RunId("run-1"), (4, 2, 2, 1, 6.0)),
    ],
    ids=["history", "state", "run_id", "usage"],
)
def test_check_rejects_wrong_input_types(history, state, run_id, usage):
    with pytest.raises(ValueError):
        validate_decision_history(history, state=state, run_id=run_id, usage=usage)


def test_check_requires_the_expected_run_and_project():
    with pytest.raises(ValueError, match="run"):
        _check(run="run-2")
    with pytest.raises(ValueError, match="project"):
        _check(state=_state(project="p2"))


def test_check_rejects_a_stale_current_revision():
    with pytest.raises(ValueError, match="r1"):
        _check(state=_state("r1"))


def test_check_rejects_an_unknown_final_selection():
    with pytest.raises(ValueError, match="unknown"):
        _check(_history((_scenario()[0], _unresolved_second_entry())), usage=_usage(3, 1, 1, 1, 4.0))


@pytest.mark.parametrize("calls", [3, 5])
def test_check_requires_len_plus_one_charged_decision_calls(calls):
    with pytest.raises(ValueError, match="decision_calls"):
        _check(usage=_usage(calls, 2, 2, 1, 6.0))


@pytest.mark.parametrize(
    "usage",
    [_usage(4, 1, 2, 1, 6.0), _usage(4, 2, 1, 1, 6.0), _usage(4, 2, 2, 0, 6.0), _usage(4, 2, 2, 1, 4.0)],
    ids=["attempts", "renders", "recoveries", "elapsed"],
)
def test_check_rejects_current_usage_below_the_history(usage):
    with pytest.raises(ValueError, match="usage"):
        _check(usage=usage)
