"""Focused tests for artifact checkpoints, execution/rollback receipts and correction attempts (Task 2D).

All artifacts are metadata fixtures (no files are read or written); values are structural examples only.
"""

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from autonomous_mixing.core.loop.references import validate_attempt_references
from autonomous_mixing.core.loop.transitions import derive_candidate_project_state
from autonomous_mixing.core.records.actions import (
    Action,
    ExecutionIssue,
    ExecutionReceipt,
    ExecutionStatus,
    RollbackReceipt,
    RollbackStatus,
    SetTrackGain,
    derive_candidate_checkpoint,
    validate_rollback_references,
)
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.budgets import BudgetLimits, BudgetUsage
from autonomous_mixing.core.records.context import MusicalContext
from autonomous_mixing.core.records.decision import (
    Abstain,
    AbstentionReason,
    ControllerIdentity,
    Decision,
    ProposeAction,
)
from autonomous_mixing.core.records.identifiers import (
    ActionId,
    ArtifactId,
    AttemptId,
    CheckpointId,
    ConditionId,
    ContextId,
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
from autonomous_mixing.core.records.observation import (
    AnalysisScope,
    AnalysisSpec,
    MeasuredScalar,
    Observation,
    RenderSpec,
    TimeRange,
    validate_observation_sources,
)
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)
from autonomous_mixing.core.records.run import (
    AttemptDisposition,
    CorrectionAttempt,
    RunConfiguration,
    RunRecord,
    RunState,
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
from autonomous_mixing.core.verification.consistency import validate_verification_references

UTC_NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
HASH = "b" * 64
_D = object()
ISSUE = ExecutionIssue("render_error", "renderer reported an error")
CR, NS, FL, UN = (
    ExecutionStatus.CANDIDATE_READY,
    ExecutionStatus.NOT_STARTED,
    ExecutionStatus.FAILED,
    ExecutionStatus.UNCERTAIN,
)
D = AttemptDisposition
METRIC = VersionRef("metric-a", "1")


def _artifact(aid, tracks=("t1",), *, revision="r1", project="p1", kind=ArtifactKind.STEM) -> AudioArtifact:
    return AudioArtifact(
        artifact_id=ArtifactId(aid),
        project_id=ProjectId(project),
        revision=Revision(revision),
        source_track_ids=tuple(TrackId(t) for t in tracks),
        kind=kind,
        location=f"outputs/{aid}.wav",
        timeline_start_seconds=0.0,
        sample_rate_hz=48000,
        channel_count=2,
        frame_count=48000 * 60,
        content_hash=HASH,
        render_spec=VersionRef("render", "1"),
    )


def _checkpoint_artifacts(revision="r1", project="p1") -> tuple[AudioArtifact, ...]:
    return (
        _artifact("a-t1", ("t1",), revision=revision, project=project),
        _artifact("a-t2", ("t2",), revision=revision, project=project),
        _artifact("a-mix", ("t1", "t2"), revision=revision, project=project, kind=ArtifactKind.MIX),
    )


def _checkpoint(revision="r1", artifacts=_D, *, cid="cp1", project="p1") -> ArtifactCheckpoint:
    if artifacts is _D:
        artifacts = _checkpoint_artifacts(revision, project)
    return ArtifactCheckpoint(CheckpointId(cid), ProjectId(project), Revision(revision), artifacts)


def _receipt(
    status, revision=_D, artifacts=_D, issue=_D, *, superseded=_D, before="r1", action="x1", project="p1", eid="e1"
):
    ready = status is CR
    if superseded is _D:
        # The candidates explicitly replace the edited stem and the mix derived from it.
        superseded = (ArtifactId("a-t2"), ArtifactId("a-mix")) if ready else ()
    if revision is _D:
        revision = Revision("r2") if ready else None
    if artifacts is _D:
        # Only the edited stem and the mix are newly produced; the unchanged t1 stem stays in the checkpoint.
        artifacts = (
            (
                _artifact("c-t2", ("t2",), revision="r2", project=project),
                _artifact("c-mix", ("t1", "t2"), revision="r2", project=project, kind=ArtifactKind.MIX),
            )
            if ready
            else ()
        )
    if issue is _D:
        issue = ISSUE if status in (FL, UN) else None
    return ExecutionReceipt(
        ExecutionId(eid),
        ActionId(action),
        ProjectId(project),
        Revision(before),
        status,
        revision,
        artifacts,
        superseded,
        issue,
    )


def _snapshot(revision="r1", tracks=("t1", "t2"), project="p1") -> ProjectSnapshot:
    names = {"t1": "Room", "t2": "Voice", "t3": "Pad", "t4": "Spare"}
    tracks = tuple(Track(TrackId(t), names[t]) for t in tracks)
    return ProjectSnapshot(ProjectId(project), Revision(revision), UTC_NOW, tracks)


def _state(revision="r1", artifacts=_D, *, cid="cp1", gains=_D, tracks=("t1", "t2")) -> ProjectState:
    """Complete before state: snapshot, explicit 0 dB gains on the t1/t2 input stems, audio membership."""
    audio = _checkpoint(revision, artifacts, cid=cid)
    if gains is _D:
        inputs = {a.artifact_id: a for a in _checkpoint_artifacts(revision)}
        gains = tuple(TrackGainState(TrackId(t), inputs[ArtifactId(f"a-{t}")], 0.0) for t in ("t1", "t2"))
    processing = ProjectProcessingState(ProjectId("p1"), Revision(revision), gains)
    return ProjectState(_snapshot(revision, tracks), processing, audio)


def _action(revision="r1", *, action="x1", target="t2", value_db=-3.0) -> Action:
    return Action(ActionId(action), ProjectId("p1"), Revision(revision), SetTrackGain(TrackId(target), value_db))


def _candidate(state, receipt, action=None, cid="cp2") -> ProjectState:
    """Explicit test construction of the candidate state a receipt claims (there is no production default)."""
    action = action or _action(str(state.revision), action=str(receipt.action_id))
    return derive_candidate_project_state(state, receipt, action, CheckpointId(cid), UTC_NOW + timedelta(minutes=1))


def _rollback(status=RollbackStatus.VERIFIED, restored=_D, *, checkpoint="cp1", execution="e1") -> RollbackReceipt:
    if restored is _D:
        restored = _state() if status is RollbackStatus.VERIFIED else None
    return RollbackReceipt(ExecutionId(execution), CheckpointId(checkpoint), status, restored, "restoration checked")


def _criteria(version="1") -> AcceptanceCriteria:
    selector = MetricSelector(METRIC, AnalysisScope((TrackId("t1"), TrackId("t2")), TimeRange(0.0, 60.0)))
    return AcceptanceCriteria(
        CriteriaId("crit"),
        version,
        (TargetCondition(ConditionId("target"), selector, ComparisonOperator.LE, MeasuredScalar(-6.0, "unit")),),
        (
            ImprovementCondition(
                ConditionId("improve"), selector, ImprovementDirection.LOWER, MeasuredScalar(1, "unit")
            ),
        ),
        (),
    )


def _validation(verdict=Verdict.ACCEPTED, version="1") -> VerificationResult:
    def check(cid):
        return CheckResult(ConditionId(cid), CheckStatus.PASS, "passed", (ObservationId("o-after"),))

    return VerificationResult(
        VerificationResultId("v1"),
        CriteriaId("crit"),
        version,
        AssessmentMode.ATTEMPT,
        (check("target"), check("improve")),
        verdict,
        TargetStatus.SATISFIED,
    )


def _initial_validation() -> VerificationResult:
    # A consistent INITIAL assessment: target met before any edit; improvement is outside this mode.
    return VerificationResult(
        VerificationResultId("v0"),
        CriteriaId("crit"),
        "1",
        AssessmentMode.INITIAL,
        (
            CheckResult(ConditionId("target"), CheckStatus.PASS, "passed", (ObservationId("o-before"),)),
            CheckResult(ConditionId("improve"), CheckStatus.NOT_APPLICABLE, "not assessed initially", ()),
        ),
        Verdict.ACCEPTED,
        TargetStatus.SATISFIED,
    )


def _attempt(
    disposition, execution=None, validation=None, rollback=None, state=_D, candidate=_D, **ids
) -> CorrectionAttempt:
    before = _state() if state is _D else state
    if candidate is _D:
        ready = isinstance(execution, ExecutionReceipt) and execution.status is CR
        candidate = _candidate(before, execution) if ready else None
    return CorrectionAttempt(
        attempt_id=AttemptId(ids.get("attempt", "att1")),
        run_id=RunId(ids.get("run", "run-1")),
        decision_id=DecisionId(ids.get("decision", "d1")),
        action_id=ActionId(ids.get("action", "x1")),
        before_state=before,
        execution=execution,
        candidate_state=candidate,
        validation=validation,
        rollback=rollback,
        disposition=disposition,
    )


def _accepted(**kwargs) -> CorrectionAttempt:
    return _attempt(D.ACCEPTED, _receipt(CR), _validation(), **kwargs)


CONTEXT = MusicalContext(ContextId("c1"), "1", (), (), frozenset())
CONTROLLER = ControllerIdentity("deterministic-baseline", "0.1")


def _run(project="p1", run_id="run-1", context=CONTEXT, controller=CONTROLLER, criteria_version="1") -> RunRecord:
    configuration = RunConfiguration(
        scope=AnalysisScope((TrackId("t1"), TrackId("t2"), TrackId("t3")), TimeRange(0.0, 60.0)),
        analysis_spec=AnalysisSpec(VersionRef("spec", "1"), (METRIC,)),
        render_spec=RenderSpec(VersionRef("render", "1"), 48000, 2),
        context=context,
        criteria=_criteria(criteria_version),
        controller=controller,
        budget_limits=BudgetLimits(4, 2, 6, 1, 300.0),
    )
    usage = BudgetUsage(1, 1, 2, 0, 12.5)
    return RunRecord(
        RunId(run_id),
        ProjectId(project),
        configuration,
        Revision("r1"),
        UTC_NOW,
        RunState.VALIDATING,
        usage,
        None,
        None,
        None,
    )


def _decision(revision="r1", *, project="p1", action="x1", decision="d1", context=("c1", "1"), controller=CONTROLLER):
    action_record = Action(ActionId(action), ProjectId(project), Revision(revision), SetTrackGain(TrackId("t2"), -3.0))
    return Decision(
        DecisionId(decision),
        ProjectId(project),
        Revision(revision),
        ContextId(context[0]),
        context[1],
        (ObservationId("o1"),),
        "structured rationale",
        controller,
        ProposeAction(action_record, "lower track t2"),
    )


# --- identifiers -----------------------------------------------------------------


@pytest.mark.parametrize("cls", [AttemptId, ExecutionId, CheckpointId])
@pytest.mark.parametrize("raw", ["", " ", 1, None])
def test_new_identifiers_reject_blank_and_non_string(cls, raw):
    with pytest.raises(ValueError):
        cls(raw)


def test_new_identifiers_are_distinct_kinds():
    assert AttemptId("x") != ExecutionId("x")
    assert ExecutionId("x") != CheckpointId("x")
    assert CheckpointId("x") != ArtifactId("x")


@pytest.mark.parametrize(
    "enum, names",
    [
        (ExecutionStatus, {"NOT_STARTED", "FAILED", "CANDIDATE_READY", "UNCERTAIN"}),
        (RollbackStatus, {"VERIFIED", "FAILED", "UNCERTAIN"}),
        (AttemptDisposition, {"PENDING", "ACCEPTED", "ROLLED_BACK", "FAILED", "UNRESOLVED"}),
    ],
)
def test_enums_have_exactly_the_specified_members(enum, names):
    assert {member.name for member in enum} == names


# --- checkpoints -----------------------------------------------------------------


def test_checkpoint_accepts_stems_groups_and_mix_without_full_track_coverage():
    artifacts = (
        _artifact("a-t1"),
        _artifact("a-group", ("t1", "t2"), kind=ArtifactKind.GROUP),
        _artifact("a-mix", ("t1", "t2"), kind=ArtifactKind.MIX),
    )
    checkpoint = _checkpoint(artifacts=artifacts)
    assert {a.kind for a in checkpoint.artifacts} == set(ArtifactKind)


@pytest.mark.parametrize(
    "artifacts",
    [
        (),
        list(_checkpoint_artifacts()),
        (_artifact("a-t1"), _artifact("a-t1", ("t2",))),
        (_artifact("a-t1"), _artifact("a-t2", project="p2")),
        ("a-t1",),
    ],
)
def test_checkpoint_rejects_empty_mutable_duplicate_and_mislinked_artifacts(artifacts):
    with pytest.raises(ValueError):
        _checkpoint(artifacts=artifacts)


def test_checkpoint_rejects_untyped_identity():
    with pytest.raises(ValueError):
        ArtifactCheckpoint("cp1", ProjectId("p1"), Revision("r1"), _checkpoint_artifacts())
    with pytest.raises(ValueError):
        ArtifactCheckpoint(CheckpointId("cp1"), ProjectId("p1"), "r1", _checkpoint_artifacts())


# --- execution receipts ----------------------------------------------------------


@pytest.mark.parametrize("status", list(ExecutionStatus))
def test_every_execution_status_has_a_valid_shape(status):
    receipt = _receipt(status)
    assert receipt.status is status


def test_not_started_may_record_an_issue():
    assert _receipt(NS, issue=ISSUE).issue == ISSUE


def test_candidate_artifacts_cover_only_newly_produced_outputs():
    receipt = _receipt(CR)
    checkpoint = _checkpoint()
    produced = {a.artifact_id for a in receipt.candidate_artifacts}
    assert ArtifactId("a-t1") in {a.artifact_id for a in checkpoint.artifacts}
    assert not any(
        TrackId("t1") == a.source_track_ids[0] and a.kind is ArtifactKind.STEM for a in receipt.candidate_artifacts
    )
    assert produced == {ArtifactId("c-t2"), ArtifactId("c-mix")}


@pytest.mark.parametrize(
    "status, revision, artifacts, issue",
    [
        (CR, None, _D, _D),
        (CR, Revision("r1"), (_artifact("c-t2", revision="r1"),), _D),
        (CR, _D, (), _D),
        (CR, _D, _D, ISSUE),
        (NS, Revision("r2"), _D, _D),
        (NS, None, (_artifact("c-t2", revision="r2"),), _D),
        (FL, _D, _D, None),
        (FL, Revision("r2"), _D, _D),
        (FL, None, (_artifact("c-t2", revision="r2"),), _D),
        (UN, _D, _D, None),
        (UN, Revision("r2"), _D, _D),
        (UN, None, (_artifact("c-t2", revision="r2"),), _D),
    ],
)
def test_execution_receipt_rejects_contradictory_shapes(status, revision, artifacts, issue):
    with pytest.raises(ValueError):
        _receipt(status, revision, artifacts, issue)


@pytest.mark.parametrize(
    "artifacts",
    [
        (_artifact("c-t2", revision="r3"),),
        (_artifact("c-t2", revision="r2", project="p2"),),
        (_artifact("c-t2", revision="r2"), _artifact("c-t2", ("t1",), revision="r2")),
        [_artifact("c-t2", revision="r2")],
    ],
)
def test_candidate_artifacts_must_be_unique_and_match_project_and_candidate_revision(artifacts):
    with pytest.raises(ValueError):
        _receipt(CR, Revision("r2"), artifacts)


@pytest.mark.parametrize("code, message", [("", "m"), ("c", " "), (None, "m")])
def test_execution_issue_requires_code_and_message(code, message):
    with pytest.raises(ValueError):
        ExecutionIssue(code, message)


def test_execution_receipt_rejects_untyped_fields():
    with pytest.raises(ValueError):
        _receipt("CANDIDATE_READY")
    with pytest.raises(ValueError):
        _receipt(FL, issue="render failed")


# --- rollback receipts -----------------------------------------------------------


def test_verified_rollback_restores_exactly_the_saved_before_state():
    validate_rollback_references(_rollback(), _state())


@pytest.mark.parametrize(
    "status, restored",
    [
        (RollbackStatus.VERIFIED, None),
        (RollbackStatus.FAILED, _state()),
        (RollbackStatus.UNCERTAIN, _state()),
        (RollbackStatus.VERIFIED, _checkpoint()),
        (RollbackStatus.VERIFIED, (ArtifactId("a-t1"),)),
        ("VERIFIED", _state()),
    ],
    ids=["verified-without-state", "failed-with-state", "uncertain-with-state", "audio-only", "ids-only", "untyped"],
)
def test_rollback_receipt_rejects_contradictory_shapes(status, restored):
    with pytest.raises(ValueError):
        _rollback(status, restored)


def test_rollback_receipt_requires_explanation():
    with pytest.raises(ValueError):
        RollbackReceipt(ExecutionId("e1"), CheckpointId("cp1"), RollbackStatus.FAILED, None, " ")


def _with_gain(state, track, gain_db) -> ProjectState:
    gains = tuple(
        TrackGainState(g.track_id, g.input_artifact, gain_db) if g.track_id == TrackId(track) else g
        for g in state.processing.track_gains
    )
    return dataclasses.replace(state, processing=dataclasses.replace(state.processing, track_gains=gains))


OTHER_MIX = _artifact("c-mix", ("t1", "t2"), kind=ArtifactKind.MIX)
RECAPTURED = dataclasses.replace(_snapshot(), captured_at=UTC_NOW + timedelta(hours=1))


@pytest.mark.parametrize(
    "restored, match",
    [
        (_with_gain(_state(), "t2", -3.0), "processing"),
        (_state(artifacts=_checkpoint_artifacts()[:2]), "audio"),
        (_state(artifacts=(*_checkpoint_artifacts()[:2], OTHER_MIX)), "audio"),
        (dataclasses.replace(_state(), snapshot=RECAPTURED), "snapshot"),
    ],
    ids=[
        "matching-audio-wrong-settings",
        "matching-settings-missing-audio",
        "matching-settings-other-audio",
        "recaptured",
    ],
)
def test_verified_rollback_requires_the_complete_saved_state(restored, match):
    with pytest.raises(ValueError, match=match):
        validate_rollback_references(_rollback(restored=restored), _state())


def test_rollback_references_reject_mismatched_checkpoint():
    with pytest.raises(ValueError, match="checkpoint"):
        validate_rollback_references(_rollback(checkpoint="cp0"), _state())


@pytest.mark.parametrize("status", [RollbackStatus.FAILED, RollbackStatus.UNCERTAIN])
def test_unsuccessful_rollback_references_need_only_checkpoint_identity(status):
    validate_rollback_references(_rollback(status), _state())


# --- correction attempts ---------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        lambda: _attempt(D.PENDING),
        lambda: _attempt(D.PENDING, _receipt(CR)),
        lambda: _attempt(D.PENDING, _receipt(CR), _validation(Verdict.REJECTED)),
        lambda: _accepted(),
        lambda: _attempt(D.ROLLED_BACK, _receipt(CR), _validation(Verdict.REJECTED), _rollback()),
        lambda: _attempt(D.ROLLED_BACK, _receipt(UN), None, _rollback()),
        lambda: _attempt(D.FAILED, _receipt(NS)),
        lambda: _attempt(D.FAILED, _receipt(FL)),
        lambda: _attempt(D.UNRESOLVED, _receipt(UN)),
        lambda: _attempt(D.UNRESOLVED, _receipt(CR), _validation(Verdict.REJECTED), _rollback(RollbackStatus.FAILED)),
        lambda: _attempt(D.UNRESOLVED, _receipt(CR), None, _rollback(RollbackStatus.UNCERTAIN)),
    ],
    ids=[
        "pending-empty",
        "pending-candidate",
        "pending-validated",
        "accepted",
        "rolled-back-after-rejection",
        "rolled-back-after-uncertain-execution",
        "failed-not-started",
        "failed-execution",
        "unresolved-execution",
        "unresolved-failed-rollback",
        "unresolved-uncertain-rollback",
    ],
)
def test_every_disposition_has_valid_evidence_shapes(build):
    assert isinstance(build().disposition, AttemptDisposition)


@pytest.mark.parametrize(
    "build, match",
    [
        (lambda: _attempt(D.PENDING, _receipt(CR, action="x2"), candidate=None), "action"),
        (lambda: _attempt(D.PENDING, _receipt(CR, project="p2"), candidate=None), "project"),
        (lambda: _attempt(D.PENDING, _receipt(CR, before="r0"), candidate=None), "revision"),
    ],
    ids=["action", "project", "before-revision"],
)
def test_attempt_rejects_receipt_disagreeing_with_attempt_or_checkpoint(build, match):
    with pytest.raises(ValueError, match=match):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: _attempt(D.PENDING, None, _validation()),
        lambda: _attempt(D.PENDING, _receipt(FL), _validation(Verdict.REJECTED)),
        lambda: _attempt(D.PENDING, _receipt(UN), _validation(Verdict.INCONCLUSIVE)),
    ],
    ids=["no-execution", "failed-execution", "uncertain-execution"],
)
def test_validation_requires_candidate_ready_execution(build):
    with pytest.raises(ValueError, match="CANDIDATE_READY"):
        build()


def test_accepted_attempt_rejects_initial_assessment_as_validation():
    with pytest.raises(ValueError, match="ATTEMPT"):
        _attempt(D.ACCEPTED, _receipt(CR), _initial_validation())


@pytest.mark.parametrize(
    "build",
    [
        lambda: _attempt(D.PENDING, _receipt(CR), _initial_validation()),
        lambda: _attempt(D.ROLLED_BACK, _receipt(CR), _initial_validation(), _rollback()),
    ],
    ids=["pending", "rolled-back"],
)
def test_other_dispositions_reject_embedded_initial_assessment(build):
    with pytest.raises(ValueError, match="ATTEMPT"):
        build()


def test_attempt_mode_assessments_remain_supported():
    attempt = _accepted()
    assert attempt.validation.mode is AssessmentMode.ATTEMPT
    validate_attempt_references(attempt, _run(), _decision())


def test_standalone_initial_assessment_remains_valid_outside_attempts():
    validate_verification_references(_initial_validation(), _criteria())


@pytest.mark.parametrize(
    "build",
    [
        lambda: _attempt(D.ACCEPTED),
        lambda: _attempt(D.ACCEPTED, _receipt(CR)),
        lambda: _attempt(D.ACCEPTED, _receipt(CR), _validation(Verdict.REJECTED)),
        lambda: _attempt(D.ACCEPTED, _receipt(CR), _validation(Verdict.INCONCLUSIVE)),
        lambda: _attempt(D.ACCEPTED, _receipt(CR), _validation(), _rollback()),
    ],
    ids=["no-execution", "no-validation", "rejected", "inconclusive", "with-rollback"],
)
def test_accepted_requires_candidate_and_accepted_validation_without_rollback(build):
    with pytest.raises(ValueError, match="ACCEPTED"):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: _attempt(D.ROLLED_BACK, _receipt(CR)),
        lambda: _attempt(D.ROLLED_BACK, _receipt(CR), None, _rollback(RollbackStatus.FAILED)),
        lambda: _attempt(D.ROLLED_BACK, _receipt(CR), None, _rollback(RollbackStatus.UNCERTAIN)),
    ],
    ids=["no-rollback", "failed-rollback", "uncertain-rollback"],
)
def test_rolled_back_requires_verified_rollback(build):
    with pytest.raises(ValueError, match="ROLLED_BACK"):
        build()


@pytest.mark.parametrize(
    "build, match",
    [
        (lambda: _attempt(D.PENDING, None, None, _rollback(RollbackStatus.FAILED)), "execution"),
        (lambda: _attempt(D.ROLLED_BACK, _receipt(CR), None, _rollback(execution="e9")), "execution"),
        (lambda: _attempt(D.ROLLED_BACK, _receipt(CR), None, _rollback(checkpoint="cp0")), "checkpoint"),
        (
            lambda: _attempt(D.ROLLED_BACK, _receipt(CR), None, _rollback(restored=_with_gain(_state(), "t1", -1.0))),
            "processing",
        ),
    ],
    ids=["without-execution", "execution-mismatch", "checkpoint-mismatch", "incomplete-restoration"],
)
def test_attempt_rejects_mismatched_rollback_references(build, match):
    with pytest.raises(ValueError, match=match):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: _attempt(D.FAILED),
        lambda: _attempt(D.FAILED, _receipt(UN)),
        lambda: _attempt(D.FAILED, _receipt(CR)),
        lambda: _attempt(D.FAILED, _receipt(FL), None, _rollback(RollbackStatus.FAILED)),
        lambda: _attempt(D.FAILED, _receipt(CR), None, _rollback(RollbackStatus.UNCERTAIN)),
    ],
    ids=["no-execution", "uncertain-execution", "candidate-ready", "failed-rollback", "uncertain-rollback"],
)
def test_failed_requires_known_unsuccessful_execution_without_rollback(build):
    with pytest.raises(ValueError, match="FAILED"):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: _attempt(D.UNRESOLVED),
        lambda: _attempt(D.UNRESOLVED, _receipt(FL)),
        lambda: _attempt(D.UNRESOLVED, _receipt(CR), _validation(Verdict.REJECTED), _rollback()),
    ],
    ids=["no-evidence", "known-failure", "verified-rollback"],
)
def test_unresolved_requires_uncertain_execution_or_unsuccessful_rollback(build):
    with pytest.raises(ValueError, match="UNRESOLVED"):
        build()


def test_attempt_rejects_untyped_fields():
    with pytest.raises(ValueError):
        _attempt("ACCEPTED", _receipt(CR), _validation())
    with pytest.raises(ValueError):
        _attempt(D.PENDING, state=_checkpoint())
    with pytest.raises(ValueError):
        _attempt(D.PENDING, execution="e1")


def test_attempt_records_are_immutable():
    attempt = _attempt(D.ROLLED_BACK, _receipt(CR), _validation(Verdict.REJECTED), _rollback())
    assert isinstance(attempt.before_state.audio.artifacts, tuple)
    assert isinstance(attempt.execution.candidate_artifacts, tuple)
    assert attempt.rollback.restored_state == attempt.before_state
    with pytest.raises(dataclasses.FrozenInstanceError):
        attempt.disposition = D.ACCEPTED
    with pytest.raises(dataclasses.FrozenInstanceError):
        attempt.execution.status = ExecutionStatus.FAILED
    with pytest.raises(dataclasses.FrozenInstanceError):
        attempt.before_state.audio.artifacts[0].content_hash = "c" * 64
    with pytest.raises(dataclasses.FrozenInstanceError):
        attempt.rollback.status = RollbackStatus.FAILED


# --- cross-record references -----------------------------------------------------


def test_attempt_references_accept_matching_run_and_decision():
    validate_attempt_references(_accepted(), _run(), _decision())


def test_later_attempt_checkpoint_may_differ_from_initial_revision():
    attempt = _attempt(D.PENDING, _receipt(CR, before="r5"), state=_state("r5", cid="cp5"))
    run = _run()
    assert run.initial_revision == Revision("r1")
    validate_attempt_references(attempt, run, _decision("r5"))


def _abstain_decision() -> Decision:
    return dataclasses.replace(_decision(), outcome=Abstain(AbstentionReason.NO_JUSTIFIED_ACTION))


@pytest.mark.parametrize(
    "attempt, run, decision, match",
    [
        (lambda: _accepted(run="run-9"), _run, _decision, "run"),
        (lambda: _accepted(decision="d9"), _run, _decision, "decision"),
        (_accepted, _run, lambda: _decision(action="x9"), "action"),
        (_accepted, _run, _abstain_decision, "ProposeAction"),
        (_accepted, lambda: _run(project="p9"), _decision, "project"),
        (_accepted, _run, lambda: _decision(project="p9"), "project"),
        (_accepted, _run, lambda: _decision("r0"), "revision"),
        (_accepted, _run, lambda: _decision(context=("c9", "1")), "context"),
        (_accepted, _run, lambda: _decision(context=("c1", "2")), "context"),
        (_accepted, _run, lambda: _decision(controller=ControllerIdentity("llm", "1")), "controller"),
        (_accepted, lambda: _run(criteria_version="2"), _decision, "criteria"),
    ],
    ids=[
        "run-id",
        "decision-id",
        "action-id",
        "abstention",
        "run-project",
        "decision-project",
        "decision-revision",
        "context-id",
        "context-version",
        "controller",
        "validation-criteria",
    ],
)
def test_attempt_references_reject_mismatches(attempt, run, decision, match):
    with pytest.raises(ValueError, match=match):
        validate_attempt_references(attempt(), run(), decision())


def test_attempt_references_reject_checkpoint_tracks_outside_run_scope():
    artifacts = (_artifact("a-t1"), _artifact("a-t4", ("t4",)))
    attempt = _attempt(D.PENDING, state=_state(artifacts=artifacts, tracks=("t1", "t2", "t4")))
    with pytest.raises(ValueError, match="t4"):
        validate_attempt_references(attempt, _run(), _decision())


# --- provenance, explicit replacement and candidate membership -------------------


def test_checkpoint_membership_may_mix_production_revisions():
    artifacts = (_artifact("a-t1", revision="r1"), _artifact("c-t2", ("t2",), revision="r2"))
    checkpoint = _checkpoint("r2", artifacts)
    assert [a.revision for a in checkpoint.artifacts] == [Revision("r1"), Revision("r2")]


@pytest.mark.parametrize(
    "status, superseded",
    [
        (CR, ()),
        (CR, (ArtifactId("a-t2"), ArtifactId("a-t2"))),
        (CR, [ArtifactId("a-t2")]),
        (CR, ("a-t2",)),
        (CR, (ArtifactId("c-t2"),)),
        (NS, (ArtifactId("a-t2"),)),
        (FL, (ArtifactId("a-t2"),)),
        (UN, (ArtifactId("a-t2"),)),
    ],
    ids=[
        "ready-empty",
        "duplicate",
        "list",
        "untyped",
        "supersedes-own-candidate",
        "not-started",
        "failed",
        "uncertain",
    ],
)
def test_receipt_rejects_invalid_superseded_ids(status, superseded):
    with pytest.raises(ValueError):
        _receipt(status, superseded=superseded)


@pytest.mark.parametrize(
    "receipt, match",
    [
        (lambda: _receipt(CR, superseded=(ArtifactId("a-t2"), ArtifactId("a-gone"))), "a-gone"),
        (
            lambda: _receipt(
                CR, artifacts=(_artifact("a-t1", ("t1",), revision="r2"),), superseded=(ArtifactId("a-t2"),)
            ),
            "a-t1",
        ),
    ],
    ids=["missing-superseded", "reused-candidate-id"],
)
def test_attempt_rejects_superseded_ids_outside_checkpoint_and_reused_candidate_ids(receipt, match):
    with pytest.raises(ValueError, match=match):
        _attempt(D.PENDING, receipt(), candidate=None)
    with pytest.raises(ValueError, match=match):
        derive_candidate_checkpoint(_checkpoint(), receipt(), CheckpointId("cp2"))


def test_derivation_replaces_superseded_artifacts_deterministically_and_preserves_provenance():
    previous = _checkpoint()
    receipt = _receipt(CR)
    derived = derive_candidate_checkpoint(previous, receipt, CheckpointId("cp2"))
    assert derived.checkpoint_id == CheckpointId("cp2")
    assert derived.project_id == previous.project_id
    assert derived.revision == receipt.candidate_revision
    assert [str(a.artifact_id) for a in derived.artifacts] == ["a-t1", "c-t2", "c-mix"]
    assert derived.artifacts[0] is previous.artifacts[0]  # retained unchanged: same identity, hash, location
    assert derived.artifacts[0].revision == Revision("r1")  # original production revision, not retagged
    assert derive_candidate_checkpoint(previous, receipt, CheckpointId("cp2")) == derived
    assert previous == _checkpoint()


@pytest.mark.parametrize("status", [NS, FL, UN])
def test_derivation_requires_candidate_ready_execution(status):
    with pytest.raises(ValueError, match="CANDIDATE_READY"):
        derive_candidate_checkpoint(_checkpoint(), _receipt(status), CheckpointId("cp2"))


@pytest.mark.parametrize(
    "receipt",
    [lambda: _receipt(CR, before="r0"), lambda: _receipt(CR, project="p2", superseded=(ArtifactId("a-t2"),))],
    ids=["before-revision", "project"],
)
def test_derivation_requires_matching_project_and_before_revision(receipt):
    with pytest.raises(ValueError):
        derive_candidate_checkpoint(_checkpoint(), receipt(), CheckpointId("cp2"))


def test_derivation_does_not_accept_the_attempt():
    attempt = _attempt(D.PENDING, _receipt(CR))
    derived = derive_candidate_checkpoint(attempt.before_state.audio, attempt.execution, CheckpointId("cp2"))
    assert attempt.disposition is D.PENDING
    assert attempt.before_state.audio == _checkpoint()
    assert isinstance(derived, ArtifactCheckpoint) and not hasattr(derived, "accepted")


def test_two_successive_corrections_retain_unchanged_older_artifacts():
    first = _attempt(D.ACCEPTED, _receipt(CR), _validation())
    state2 = first.candidate_state
    cp2 = state2.audio

    second_receipt = ExecutionReceipt(
        ExecutionId("e2"),
        ActionId("x2"),
        ProjectId("p1"),
        Revision("r2"),
        CR,
        Revision("r3"),
        (
            _artifact("c2-t2", ("t2",), revision="r3"),
            _artifact("c2-mix", ("t1", "t2"), revision="r3", kind=ArtifactKind.MIX),
        ),
        (ArtifactId("c-t2"), ArtifactId("c-mix")),
        None,
    )
    second = _attempt(
        D.ACCEPTED, second_receipt, _validation(), state=state2, attempt="att2", action="x2", decision="d2"
    )
    validate_attempt_references(second, _run(), _decision("r2", action="x2", decision="d2"))
    cp3 = derive_candidate_checkpoint(cp2, second_receipt, CheckpointId("cp3"))
    assert second.candidate_state.audio.artifacts == cp3.artifacts
    # The t2 gain stays relative to the original fixed input stem, never to the superseded c-t2 output.
    assert second.candidate_state.processing.track_gains[1].input_artifact.artifact_id == ArtifactId("a-t2")

    assert cp3.revision == Revision("r3")
    assert [(str(a.artifact_id), str(a.revision)) for a in cp3.artifacts] == [
        ("a-t1", "r1"),
        ("c2-t2", "r3"),
        ("c2-mix", "r3"),
    ]
    assert cp3.artifacts[0] is first.before_state.audio.artifacts[0]


def test_candidate_observation_uses_mixed_production_revision_membership():
    candidate = derive_candidate_checkpoint(_checkpoint(), _receipt(CR), CheckpointId("cp2"))
    snapshot = ProjectSnapshot(
        ProjectId("p1"), Revision("r2"), UTC_NOW, (Track(TrackId("t1"), "Room"), Track(TrackId("t2"), "Voice"))
    )
    observation = Observation(
        ObservationId("o-cand"),
        METRIC,
        (ArtifactId("a-t1"), ArtifactId("c-t2")),
        AnalysisScope((TrackId("t1"), TrackId("t2")), TimeRange(0.0, 60.0)),
        MeasuredScalar(-14.0, "unit"),
        VersionRef("spec", "1"),
        VersionRef("analyzer", "0.1"),
        UTC_NOW,
    )
    validate_observation_sources(observation, candidate.artifacts, candidate, snapshot)
    # A superseded artifact of the previous state is not a member of the candidate state.
    stale = dataclasses.replace(observation, artifact_ids=(ArtifactId("a-t1"), ArtifactId("a-t2")))
    with pytest.raises(ValueError, match="not a member"):
        validate_observation_sources(stale, (_artifact("a-t1"), _artifact("a-t2", ("t2",))), candidate, snapshot)


# --- complete candidate states (processing-state migration, ADR-019) ---------------------


def _good_candidate() -> ProjectState:
    return _candidate(_state(), _receipt(CR))


def test_candidate_ready_execution_requires_candidate_state_even_when_pending():
    with pytest.raises(ValueError, match="candidate_state"):
        _attempt(D.PENDING, _receipt(CR), candidate=None)
    with pytest.raises(ValueError, match="candidate_state"):
        _attempt(D.ACCEPTED, _receipt(CR), _validation(), candidate=None)


@pytest.mark.parametrize(
    "execution",
    [None, _receipt(NS), _receipt(FL), _receipt(UN)],
    ids=["no-execution", "not-started", "failed", "uncertain"],
)
def test_other_execution_statuses_forbid_candidate_state(execution):
    with pytest.raises(ValueError, match="candidate_state"):
        _attempt(D.PENDING, execution, candidate=_good_candidate())


def _replace_t2_input(state: ProjectState, artifact: AudioArtifact) -> ProjectState:
    gains = tuple(
        TrackGainState(g.track_id, artifact, g.gain_db) if g.track_id == TrackId("t2") else g
        for g in state.processing.track_gains
    )
    return dataclasses.replace(state, processing=dataclasses.replace(state.processing, track_gains=gains))


R3_RECEIPT = _receipt(
    CR,
    Revision("r3"),
    (_artifact("c-t2", ("t2",), revision="r3"), _artifact("c-mix", ("t1", "t2"), revision="r3", kind=ArtifactKind.MIX)),
)


@pytest.mark.parametrize(
    "candidate, match",
    [
        (lambda: _candidate(_state(), R3_RECEIPT), "revision"),
        (
            lambda: dataclasses.replace(
                _good_candidate(),
                snapshot=dataclasses.replace(
                    _good_candidate().snapshot, tracks=_good_candidate().snapshot.tracks[::-1]
                ),
            ),
            "track",
        ),
        (
            lambda: dataclasses.replace(
                _good_candidate(),
                audio=ArtifactCheckpoint(
                    CheckpointId("cp2"), ProjectId("p1"), Revision("r2"), _good_candidate().audio.artifacts[:2]
                ),
            ),
            "audio",
        ),
        (lambda: _replace_t2_input(_good_candidate(), _artifact("b-t2", ("t2",), revision="r0")), "input"),
    ],
    ids=["candidate-revision", "track-inventory-order", "audio-membership", "changed-baseline"],
)
def test_attempt_rejects_candidate_inconsistent_with_receipt_and_before_state(candidate, match):
    with pytest.raises(ValueError, match=match):
        _attempt(D.PENDING, _receipt(CR), candidate=candidate())


@pytest.mark.parametrize(
    "candidate, match",
    [
        (lambda: _with_gain(_good_candidate(), "t1", -1.0), "t1"),
        (lambda: _with_gain(_good_candidate(), "t2", -6.0), "gain"),
    ],
    ids=["unrelated-track-changed", "wrong-target-gain"],
)
def test_attempt_references_require_exactly_the_requested_gain_change(candidate, match):
    attempt = _attempt(D.ACCEPTED, _receipt(CR), _validation(), candidate=candidate())  # structurally consistent
    with pytest.raises(ValueError, match=match):
        validate_attempt_references(attempt, _run(), _decision())


def test_attempt_references_require_an_explicit_target_gain_state():
    t1_input = _checkpoint_artifacts()[0]
    before = _state(gains=(TrackGainState(TrackId("t1"), t1_input, 0.0),))  # t2 unrepresented
    receipt = _receipt(CR)
    audio = derive_candidate_checkpoint(before.audio, receipt, CheckpointId("cp2"))
    processing = ProjectProcessingState(ProjectId("p1"), Revision("r2"), before.processing.track_gains)
    candidate = ProjectState(_snapshot("r2"), processing, audio)
    attempt = _attempt(D.PENDING, receipt, state=before, candidate=candidate)
    with pytest.raises(ValueError, match="t2"):
        validate_attempt_references(attempt, _run(), _decision())


def test_derived_candidate_passes_attempt_references():
    attempt = _attempt(D.PENDING, _receipt(CR), candidate=_good_candidate())
    validate_attempt_references(attempt, _run(), _decision())
    assert attempt.candidate_state.processing.track_gains[1].gain_db == -3.0


# --- audit F1/F5: manually assembled candidates cannot bypass derivation checks ------------


def _manual_candidate(before: ProjectState, receipt: ExecutionReceipt, value_db=-3.0, cid="cp2") -> ProjectState:
    """Assemble a candidate state by hand, without derive_candidate_project_state."""
    superseded = set(receipt.superseded_artifact_ids)
    retained = tuple(a for a in before.audio.artifacts if a.artifact_id not in superseded)
    revision = receipt.candidate_revision
    audio = ArtifactCheckpoint(CheckpointId(cid), ProjectId("p1"), revision, retained + receipt.candidate_artifacts)
    gains = tuple(
        TrackGainState(g.track_id, g.input_artifact, value_db) if g.track_id == TrackId("t2") else g
        for g in before.processing.track_gains
    )
    snapshot = dataclasses.replace(before.snapshot, revision=revision)
    return ProjectState(snapshot, ProjectProcessingState(ProjectId("p1"), revision, gains), audio)


def test_audit_f1_manually_accepted_attempt_with_stale_target_stem_is_rejected():
    # Audit reproduction F1: only the mix is replaced, yet the candidate records t2 at -3 dB.
    receipt = _receipt(
        CR,
        artifacts=(_artifact("c-mix", ("t1", "t2"), revision="r2", kind=ArtifactKind.MIX),),
        superseded=(ArtifactId("a-mix"),),
    )
    attempt = _attempt(D.ACCEPTED, receipt, _validation(), candidate=_manual_candidate(_state(), receipt))
    with pytest.raises(ValueError, match="a-t2"):
        validate_attempt_references(attempt, _run(), _decision())


def test_manual_candidate_reusing_a_baseline_production_revision_is_rejected():
    # Before state r5 whose audio and fixed inputs were produced at r1; the candidate claims revision r1.
    artifacts = _checkpoint_artifacts("r1")
    inputs = {a.artifact_id: a for a in artifacts}
    gains = tuple(TrackGainState(TrackId(t), inputs[ArtifactId(f"a-{t}")], 0.0) for t in ("t1", "t2"))
    audio = ArtifactCheckpoint(CheckpointId("cp5"), ProjectId("p1"), Revision("r5"), artifacts)
    before = ProjectState(_snapshot("r5"), ProjectProcessingState(ProjectId("p1"), Revision("r5"), gains), audio)
    receipt = _receipt(
        CR,
        Revision("r1"),
        (
            _artifact("n-t2", ("t2",), revision="r1"),
            _artifact("n-mix", ("t1", "t2"), revision="r1", kind=ArtifactKind.MIX),
        ),
        before="r5",
    )
    with pytest.raises(ValueError, match="revision"):
        _attempt(D.PENDING, receipt, state=before, candidate=_manual_candidate(before, receipt))


def test_manual_candidate_reusing_a_baseline_only_revision_is_rejected_at_construction():
    # The t2 baseline was produced at rb and is no longer an audio member, so generic checkpoint
    # derivation cannot see rb; the attempt's composite-state check must.
    baseline = _artifact("a-t2", ("t2",), revision="rb")
    rendered_t2 = _artifact("c-t2", ("t2",), revision="r5")
    mix = _artifact("a-mix", ("t1", "t2"), revision="r5", kind=ArtifactKind.MIX)
    t1 = _artifact("a-t1", ("t1",), revision="r5")
    gains = (TrackGainState(TrackId("t1"), t1, 0.0), TrackGainState(TrackId("t2"), baseline, -2.0))
    before = ProjectState(
        _snapshot("r5"),
        ProjectProcessingState(ProjectId("p1"), Revision("r5"), gains),
        ArtifactCheckpoint(CheckpointId("cp5"), ProjectId("p1"), Revision("r5"), (t1, rendered_t2, mix)),
    )
    receipt = _receipt(
        CR,
        Revision("rb"),
        (
            _artifact("n-t2", ("t2",), revision="rb"),
            _artifact("n-mix", ("t1", "t2"), revision="rb", kind=ArtifactKind.MIX),
        ),
        superseded=(ArtifactId("c-t2"), ArtifactId("a-mix")),
        before="r5",
    )
    assert Revision("rb") not in before.audio.known_revisions
    derive_candidate_checkpoint(before.audio, receipt, CheckpointId("cp6"))  # not visible to membership alone
    with pytest.raises(ValueError, match="revision"):
        _attempt(D.PENDING, receipt, state=before, candidate=_manual_candidate(before, receipt))


# --- dependency boundary ---------------------------------------------------------
