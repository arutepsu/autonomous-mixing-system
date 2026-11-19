"""Increment 1 of the coordinator-integration plan (revision 3): pure contracts for the DAW execution path (ADR-035).

Records and pure rules only; no loop behaviour changes and no DAW call. Includes the rollback-evidence regressions
required by the owner's revision-3 amendment: resolution only, a one-ULP mismatch, the exact reading, and failed
retained-audio integrity.
"""

import math
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from daw_export_support import (
    BINDING,
    CHECKPOINT,
    FORMAT,
    PROFILE,
    READING,
    ROOT_KEY,
    T1,
    T2,
    T3,
    TIMELINE,
    TOKEN,
    anchor,
    awaiting,
    before_artifact,
    export_request,
    files_verified,
    outputs,
    routing,
    snapshot,
)
from daw_export_support import state as offline_state
from fl_runtime_support import make_request
from test_coordinator_runtime_contracts import STATE as RUN_STATE
from test_coordinator_runtime_contracts import _run_request
from test_execution_preflight import _decision_request
from test_execution_preflight import _request as _preflight_request
from test_execution_preflight import _state as _preflight_state

from autonomous_mixing.core.admission.daw_export import assess_daw_import
from autonomous_mixing.core.admission.preflight import preflight_execution
from autonomous_mixing.core.loop.budgets import check_budget
from autonomous_mixing.core.loop.daw_transitions import (
    daw_execution_receipt,
    derive_daw_candidate_state,
    validate_daw_execution,
)
from autonomous_mixing.core.loop.transitions import derive_candidate_project_state
from autonomous_mixing.core.records.acceptance import AcceptedReference
from autonomous_mixing.core.records.actions import (
    Action,
    DawRollbackEvidenceKind,
    DawRollbackFaderEvidence,
    ExecutionReceipt,
    ExecutionStatus,
    RollbackReceipt,
    RollbackScope,
    RollbackStatus,
    SetTrackGain,
    daw_rollback_evidence_problem,
    validate_rollback_references,
)
from autonomous_mixing.core.records.admission import PreflightCode, PreflightRejected, TrackGainPermission
from autonomous_mixing.core.records.artifacts import ArtifactKind
from autonomous_mixing.core.records.budgets import (
    BudgetBlock,
    BudgetBlockReason,
    BudgetGrant,
    BudgetLimits,
    BudgetOperation,
    BudgetUsage,
)
from autonomous_mixing.core.records.coordinator_progress import PreparedCorrection
from autonomous_mixing.core.records.daw_binding import DawFaderReading, DawTrackBinding
from autonomous_mixing.core.records.daw_control import DawWriteConfirmed
from autonomous_mixing.core.records.daw_correction import (
    DawCorrectionPlan,
    DawCorrectionSuspended,
    DawSessionConfig,
    DawStateQuery,
)
from autonomous_mixing.core.records.daw_export import (
    DawDependencyStatus,
    DawExportFormat,
    DawExportOutput,
    DawExportTimeline,
    RoutingUnknownError,
)
from autonomous_mixing.core.records.decision import ActionJustification, ControllerIdentity, Decision, ProposeAction
from autonomous_mixing.core.records.identifiers import (
    ActionId,
    ArtifactId,
    AttemptId,
    CheckpointId,
    ContextId,
    DecisionCallId,
    DecisionId,
    ExecutionId,
    ObservationId,
    ProblemCandidateId,
    ProjectId,
    Revision,
    RunEventId,
    RunId,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.project import ArtifactCheckpoint, DawParameterState, DawTrackParameter
from autonomous_mixing.core.records.rendering import ExecutionWorkspace
from autonomous_mixing.core.records.run import RunState
from autonomous_mixing.core.records.run_report import RunSuspended
from autonomous_mixing.core.records.run_trace import (
    DispatchIntent,
    OperationKind,
    OperationObserved,
    RunJournalSnapshot,
    RunTraceEvent,
)
from autonomous_mixing.core.records.termination import TerminationReason

UTC = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
BASE = BINDING.baseline_fader  # the confirmed (saved) fader of the before state
ACTION = Action(ActionId("a1"), ProjectId("p1"), Revision("r1"), SetTrackGain(T1, 1.0))
APPLY = make_request("apply_root_0001")  # the journaled apply request: ACTION, execution e1, a -1 dB cut
FL_EXPORT = VersionRef("fl-export", "1")
WORKSPACE = ExecutionWorkspace(ExecutionId("e1"), ROOT_KEY, TOKEN)


def params(fader=BASE, binding=BINDING, revision="r1", selected=2.0):
    return DawParameterState(ProjectId("p1"), Revision(revision), (DawTrackParameter(binding, selected, fader),))


def daw_state(**daw):
    return replace(offline_state(), daw=params(**daw))


def plan(**changes):
    base = DawCorrectionPlan(ExecutionId("e1"), "apply_root_0001", ProjectId("p1"), Revision("r1"), Revision("r2"), T1,
                             1.0, BINDING, PROFILE, routing(), outputs(), TIMELINE, FORMAT, FL_EXPORT, WORKSPACE)  # fmt: skip
    return replace(base, **changes)


def decision(action=ACTION):
    justification = ActionJustification("labelled fixture", "uncalibrated fixture text", (), ("contract fixture",))
    proposal = ProposeAction(action, "lower the bound track", (ProblemCandidateId("cand-1"),), justification)
    return Decision(DecisionId("d1"), ProjectId("p1"), Revision("r1"), ContextId("c1"), "1", (ObservationId("o1"),),
                    "structured rationale", ControllerIdentity("fixture", "0.1"), proposal)  # fmt: skip


def verified_import():
    record = awaiting()
    return assess_daw_import(record, files_verified(record), before_import=snapshot(), after_import=snapshot())


# --- Option A: explicit DAW parameter state inside ProjectState ---------------------------------------------------------


def test_offline_states_carry_no_daw_parameter_state_and_daw_states_validate():
    assert offline_state().daw is None
    state = daw_state()
    assert state.daw.track(T1).confirmed_fader == BASE and state.daw.track(T2) is None
    assert state.processing == offline_state().processing  # TrackGainState is a separate, unchanged record


@pytest.mark.parametrize(
    "build",
    [
        lambda: params(revision="r9"),  # another state revision
        lambda: params(binding=replace(BINDING, project_id=ProjectId("p2"))),
        lambda: DawParameterState(ProjectId("p1"), Revision("r1"),
                                  (DawTrackParameter(BINDING, 2.0, BASE), DawTrackParameter(BINDING, 1.0, BASE))),
        lambda: DawParameterState(ProjectId("p1"), Revision("r1"), ()),
    ],
)  # fmt: skip
def test_daw_parameter_state_must_match_the_project_state(build):
    with pytest.raises(ValueError):
        replace(offline_state(), daw=build())


def test_daw_parameter_tracks_must_be_known_to_the_snapshot():
    unknown = replace(BINDING, track_id=TrackId("t9"))
    with pytest.raises(ValueError):
        daw_state(binding=unknown)


def test_the_offline_derivation_refuses_a_daw_controlled_state():
    previous = daw_state()
    receipt = daw_execution_receipt(previous, verified_import(), APPLY)
    with pytest.raises(ValueError, match="DAW"):
        derive_candidate_project_state(previous, receipt, ACTION, CheckpointId("c2"), UTC)


def test_offline_preflight_refuses_a_daw_controlled_state():
    state = _preflight_state()
    binding = DawTrackBinding(VersionRef("binding", "1"), state.project_id, state.snapshot.tracks[0].track_id,
                              PROFILE.project_title, 2, "Bass", 0.0, BASE)  # fmt: skip
    controlled = replace(state, daw=DawParameterState(state.project_id, state.revision,
                                                      (DawTrackParameter(binding, 0.0, BASE),)))  # fmt: skip
    result = preflight_execution(_preflight_request(decision_request=_decision_request(controlled)))
    assert isinstance(result, PreflightRejected) and result.code is PreflightCode.DAW_CONTROLLED_STATE


# --- the correction plan and the session configuration ------------------------------------------------------------------


def test_a_daw_plan_validates_against_its_before_state():
    plan().validate_against(daw_state())
    prepared = PreparedCorrection(DecisionCallId("call-1"), daw_state(), decision(), AttemptId("at-1"), plan())
    assert prepared.execution_id == ExecutionId("e1") and prepared.candidate_revision == Revision("r2")


@pytest.mark.parametrize(
    "changes",
    [
        {"command_id": "short"},
        {"workspace": ExecutionWorkspace(ExecutionId("e2"), ROOT_KEY, TOKEN)},
        {"target_track_id": T2},
        {"candidate_revision": Revision("r1")},
        {"binding": replace(BINDING, project_id=ProjectId("p2"))},
        {"routing": routing(status=DawDependencyStatus.UNKNOWN)},
    ],
)
def test_daw_plan_invariants(changes):
    with pytest.raises(ValueError):
        plan(**changes)


def test_unknown_routing_is_its_own_error():
    with pytest.raises(RoutingUnknownError):
        plan(routing=routing(status=DawDependencyStatus.UNKNOWN))


def test_a_plan_is_refused_for_a_before_state_it_cannot_cover():
    group = before_artifact("g1", (T1, T2), ArtifactKind.GROUP)
    second_mix = before_artifact("m2", (T1, T3), ArtifactKind.MIX)
    for extra in (group, second_mix):
        checkpoint = ArtifactCheckpoint(
            CheckpointId("c1"), ProjectId("p1"), Revision("r1"), (*CHECKPOINT.artifacts, extra)
        )
        with pytest.raises(ValueError):
            plan().validate_against(replace(daw_state(), audio=checkpoint))
    with pytest.raises(ValueError, match="DAW parameter state"):
        plan().validate_against(offline_state())
    with pytest.raises(ValueError, match="binding"):
        plan().validate_against(daw_state(binding=replace(BINDING, baseline_gain_db=2.5)))


def test_the_export_request_refuses_a_group_of_the_target_too():
    group = before_artifact("g1", (T1, T2), ArtifactKind.GROUP)
    checkpoint = ArtifactCheckpoint(CheckpointId("c1"), ProjectId("p1"), Revision("r1"), (*CHECKPOINT.artifacts, group))
    with pytest.raises(ValueError, match="group"):
        export_request(before_checkpoint=checkpoint)


def test_a_prepared_daw_correction_must_match_its_action():
    other = Action(ActionId("a1"), ProjectId("p1"), Revision("r1"), SetTrackGain(T1, 0.5))
    with pytest.raises(ValueError):
        PreparedCorrection(DecisionCallId("call-1"), daw_state(), decision(other), AttemptId("at-1"), plan())


def session(**changes):
    base = DawSessionConfig(BINDING, PROFILE, TrackGainPermission(T1, -6.0, 6.0), routing(), FORMAT, TIMELINE,
                            FL_EXPORT, "exports")  # fmt: skip
    return replace(base, **changes)


def test_a_run_request_may_carry_a_daw_session_bound_to_its_initial_state():
    assert _run_request().daw_session is None  # the offline request is unchanged
    controlled = replace(RUN_STATE, daw=DawParameterState(RUN_STATE.project_id, RUN_STATE.revision,
                                                          (DawTrackParameter(BINDING, 2.0, BASE),)))  # fmt: skip
    assert _run_request(initial_state=controlled, daw_session=session()).daw_session == session()
    with pytest.raises(ValueError, match="DAW parameter state"):
        _run_request(daw_session=session())  # the initial state has no DAW parameter state
    with pytest.raises(ValueError):
        session(permission=TrackGainPermission(T2, -6.0, 6.0))
    with pytest.raises(RoutingUnknownError):
        session(routing=routing(status=DawDependencyStatus.UNKNOWN))


# --- the DAW receipt and the candidate state ------------------------------------------------------------------------------


def test_a_verified_import_becomes_a_candidate_ready_receipt_and_a_candidate_state():
    previous, verified = daw_state(), verified_import()
    receipt = daw_execution_receipt(previous, verified, APPLY)
    assert APPLY.admitted.action == ACTION
    assert receipt.status is ExecutionStatus.CANDIDATE_READY
    assert receipt.candidate_artifacts == verified.candidate_artifacts
    assert [a.value for a in receipt.superseded_artifact_ids] == ["s1", "s2", "m1"]
    candidate = derive_daw_candidate_state(previous, verified, APPLY, CheckpointId("c2"), UTC)
    assert candidate.revision == Revision("r2") and candidate.processing.track_gains == previous.processing.track_gains
    assert candidate.daw.track(T1) == DawTrackParameter(BINDING, 1.0, READING)  # the anchored reading
    assert {a.artifact_id.value for a in candidate.audio.artifacts} == {"s3", "x1", "x2", "xm"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: replace(r, superseded_artifact_ids=(*r.superseded_artifact_ids, ArtifactId("s3"))),
        lambda r: replace(r, superseded_artifact_ids=r.superseded_artifact_ids[:2]),
        lambda r: replace(
            r, candidate_artifacts=r.candidate_artifacts[:2], superseded_artifact_ids=r.superseded_artifact_ids[:2]
        ),  # fmt: skip
        lambda r: replace(r, action_id=ActionId("other")),
    ],
)
def test_the_daw_receipt_must_equal_the_verified_import(mutate):
    previous, verified = daw_state(), verified_import()
    receipt = mutate(daw_execution_receipt(previous, verified, APPLY))
    with pytest.raises(ValueError):
        validate_daw_execution(previous, receipt, APPLY, verified)


def test_the_daw_derivation_refuses_states_it_cannot_describe():
    verified = verified_import()
    with pytest.raises(ValueError):
        daw_execution_receipt(offline_state(), verified, APPLY)  # no DAW parameter state
    with pytest.raises(ValueError):
        daw_execution_receipt(daw_state(binding=replace(BINDING, baseline_gain_db=2.5)), verified, APPLY)


# Review fix 2026-11-19 (P2): the receipt and candidate are bound to the journaled apply request, so another action
# on the same track (another gain, command, execution or resolved state) cannot be substituted.
@pytest.mark.parametrize(
    "substitute",
    [
        lambda: make_request("apply_root_0001", change=-2.0),  # another gain on the same track, same command ID
        lambda: make_request("apply_other_0001"),  # the same gain under another command ID
        lambda: replace(APPLY, execution_id=ExecutionId("e2")),
        lambda: replace(APPLY, command_id="apply_other_0001"),
    ],
)
def test_another_apply_on_the_same_track_cannot_be_substituted(substitute):
    previous, verified, other = daw_state(), verified_import(), substitute()
    assert other.admitted.action.operation.track_id == T1
    with pytest.raises(ValueError):
        daw_execution_receipt(previous, verified, other)
    with pytest.raises(ValueError):
        derive_daw_candidate_state(previous, verified, other, CheckpointId("c2"), UTC)
    with pytest.raises(ValueError):
        validate_daw_execution(previous, daw_execution_receipt(previous, verified, APPLY), other, verified)


# Review fix 2026-11-19 (second P2): identity is exact agreement with the journaled apply carried by the anchor, not
# the success tolerance. A substitute 0.01 dB away under the same command ID lies inside the 0.05 dB tolerance.
def test_a_substitute_inside_the_success_tolerance_under_the_same_command_id_is_refused():
    substitute = make_request("apply_root_0001", change=-1.01)  # selected gain 0.99 dB instead of 1.00 dB
    assert substitute.command_id == APPLY.command_id and substitute != APPLY
    anchored = verified_import().request.anchor
    assert abs(anchored.reading.db - substitute.resolution.target_fader_db) <= PROFILE.success_db_tolerance
    previous, verified = daw_state(), verified_import()
    with pytest.raises(ValueError, match="exactly"):
        daw_execution_receipt(previous, verified, substitute)
    with pytest.raises(ValueError, match="exactly"):
        derive_daw_candidate_state(previous, verified, substitute, CheckpointId("c2"), UTC)
    with pytest.raises(ValueError):
        checkpoint(apply=substitute, decision=decision(substitute.admitted.action),
                   plan=plan(requested_gain_db=0.99))  # fmt: skip


def test_the_success_tolerance_is_still_checked_separately():
    off = replace(anchor(), reading=DawFaderReading(READING.normalized, READING.db - 0.06))
    request = export_request(anchor=off)
    from autonomous_mixing.core.records.daw_correction import daw_apply_export_problem

    assert daw_apply_export_problem(APPLY, request) is not None  # the exact apply, but not its confirmed result
    assert daw_apply_export_problem(APPLY, export_request()) is None


def test_the_apply_must_be_resolved_from_the_selected_state():
    with pytest.raises(ValueError, match="selected state"):
        daw_execution_receipt(daw_state(selected=1.5), verified_import(), APPLY)
    with pytest.raises(ValueError, match="selected state"):
        daw_execution_receipt(daw_state(fader=READING), verified_import(), APPLY)


def test_the_candidate_records_exactly_the_executed_gain():
    candidate = derive_daw_candidate_state(daw_state(), verified_import(), APPLY, CheckpointId("c2"), UTC)
    assert candidate.daw.track(T1).selected_gain_db == APPLY.admitted.action.operation.value_db == 1.0


# --- rollback: fader restoration versus complete project rollback (revision-3 regressions) ------------------------------


def evidence(**changes):
    base = DawRollbackFaderEvidence(
        BINDING, BASE, DawRollbackEvidenceKind.EXACT_RESTORE, "restore_cmd_0001", BASE, 5, 5
    )
    return replace(base, **changes)


def rollback(status=RollbackStatus.VERIFIED, fader=(None,), retained=RollbackStatus.VERIFIED, restored=None):
    items = (evidence(),) if fader == (None,) else fader
    state = (restored or daw_state()) if status is RollbackStatus.VERIFIED else None
    return RollbackReceipt(ExecutionId("e1"), CheckpointId("c1"), status, state,
                           "fader restored exactly; project-level restoration unverified",
                           RollbackScope.SELECTED_STATE_WITH_FADER, retained, items)  # fmt: skip


def test_exact_reading_evidence_with_verified_retained_audio_supports_verified_rollback():
    receipt = rollback()
    validate_rollback_references(receipt, daw_state())
    observed = evidence(kind=DawRollbackEvidenceKind.EXACT_OBSERVATION, reference="event-17", evidence_seq=7)
    assert rollback(fader=(observed,)).status is RollbackStatus.VERIFIED
    assert daw_rollback_evidence_problem(evidence()) is None


def test_resolution_only_cannot_establish_verified_rollback():
    resolution = evidence(kind=DawRollbackEvidenceKind.RESOLUTION, reference="resolve_0001")
    assert daw_rollback_evidence_problem(resolution) is not None  # even though its reading equals the target
    with pytest.raises(ValueError, match="resolution"):
        rollback(fader=(resolution,))
    assert rollback(RollbackStatus.UNCERTAIN, fader=(resolution,)).status is RollbackStatus.UNCERTAIN


@pytest.mark.parametrize(
    "reading",
    [
        DawFaderReading(math.nextafter(BASE.normalized, 1), BASE.db),
        DawFaderReading(math.nextafter(BASE.normalized, 0), BASE.db),
        DawFaderReading(BASE.normalized, math.nextafter(BASE.db, 0)),
        DawFaderReading(BASE.normalized, math.nextafter(BASE.db, -10)),
    ],
)
@pytest.mark.parametrize("kind", [DawRollbackEvidenceKind.EXACT_RESTORE, DawRollbackEvidenceKind.EXACT_OBSERVATION])
def test_a_one_ulp_mismatch_cannot_establish_verified_rollback(reading, kind):
    near = evidence(kind=kind, reading=reading)
    assert daw_rollback_evidence_problem(near) is not None  # well inside every tolerance, yet not exact
    with pytest.raises(ValueError, match="exactly"):
        rollback(fader=(near,))
    assert rollback(RollbackStatus.UNCERTAIN, fader=(near,)).status is RollbackStatus.UNCERTAIN


@pytest.mark.parametrize("retained", [RollbackStatus.FAILED, RollbackStatus.UNCERTAIN])
def test_failed_retained_audio_integrity_refuses_verified_rollback_despite_exact_fader_evidence(retained):
    with pytest.raises(ValueError, match="retained audio"):
        rollback(retained=retained)
    assert rollback(RollbackStatus.FAILED, retained=retained).status is RollbackStatus.FAILED


def test_exact_evidence_must_be_bound_to_the_same_fader_and_saved_target():
    other_contents = replace(BINDING, baseline_gain_db=2.5)  # the same reference with other contents
    assert other_contents.reference == BINDING.reference
    with pytest.raises(ValueError, match="fader"):
        rollback(fader=(evidence(binding=other_contents),))
    moved_target = evidence(saved_target=READING, reading=READING)  # exact, but to another target
    with pytest.raises(ValueError, match="saved target"):
        rollback(fader=(moved_target,))
    with pytest.raises(ValueError):
        rollback(fader=())
    with pytest.raises(ValueError):
        rollback(fader=(evidence(), evidence(reference="restore_cmd_0002")))


@pytest.mark.parametrize(
    "changes",
    [
        {"evidence_seq": 4},  # a restoration older than the latest write reservation
        {"evidence_seq": 6},  # not the latest write reservation itself
        {"kind": DawRollbackEvidenceKind.EXACT_OBSERVATION, "evidence_seq": 4},  # observed before the latest write
    ],
)
def test_exact_evidence_must_follow_the_latest_relevant_write_reservation(changes):
    stale = evidence(**changes)
    assert daw_rollback_evidence_problem(stale) is not None
    with pytest.raises(ValueError):
        rollback(fader=(stale,))


def test_a_daw_rollback_can_never_be_a_complete_project_rollback():
    with pytest.raises(ValueError, match="complete"):
        RollbackReceipt(ExecutionId("e1"), CheckpointId("c1"), RollbackStatus.VERIFIED, daw_state(), "restored")
    with pytest.raises(ValueError):
        RollbackReceipt(ExecutionId("e1"), CheckpointId("c1"), RollbackStatus.FAILED, None, "x",
                        RollbackScope.COMPLETE_SAVED_STATE, None, (evidence(),))  # fmt: skip
    with pytest.raises(ValueError, match="retained"):
        RollbackReceipt(ExecutionId("e1"), CheckpointId("c1"), RollbackStatus.FAILED, None, "x",
                        RollbackScope.SELECTED_STATE_WITH_FADER, None, ())  # fmt: skip
    offline = RollbackReceipt(ExecutionId("e1"), CheckpointId("c1"), RollbackStatus.VERIFIED, offline_state(), "ok")
    assert offline.scope is RollbackScope.COMPLETE_SAVED_STATE and offline.fader_evidence == ()


# --- budget, run state, termination and trace additions ------------------------------------------------------------------


LIMITS = BudgetLimits(3, 2, 2, 2, 60.0, max_daw_writes=1)


def test_daw_writes_have_their_own_budget():
    usage = BudgetUsage(0, 1, 0, 0, 1.0)
    grant = check_budget(BudgetOperation.DAW_WRITE, limits=LIMITS, usage=usage, elapsed_seconds=2.0)
    assert isinstance(grant, BudgetGrant) and grant.usage_after_charge.daw_writes == 1
    block = check_budget(BudgetOperation.DAW_WRITE, limits=LIMITS, usage=grant.usage_after_charge, elapsed_seconds=2.0)
    assert isinstance(block, BudgetBlock) and block.reason is BudgetBlockReason.DAW_WRITE_LIMIT
    assert TerminationReason(block.reason.value) is TerminationReason.DAW_WRITE_LIMIT
    offline = BudgetLimits(3, 2, 2, 2, 60.0)
    assert check_budget(BudgetOperation.DAW_WRITE, limits=offline, usage=usage, elapsed_seconds=2.0).reason is (
        BudgetBlockReason.DAW_WRITE_LIMIT
    )  # no DAW writes unless configured


def test_a_daw_write_needs_an_unused_recovery_slot_like_a_render():
    exhausted = BudgetUsage(0, 1, 0, 2, 1.0)  # both recovery slots used
    block = check_budget(BudgetOperation.DAW_WRITE, limits=LIMITS, usage=exhausted, elapsed_seconds=2.0)
    assert isinstance(block, BudgetBlock) and block.reason is BudgetBlockReason.RECOVERY_CAPACITY_LIMIT


def test_new_states_and_reasons_exist():
    assert RunState.AWAITING_OPERATOR_EXPORT.value == "awaiting_operator_export"
    for name in ("DAW_WRITE_LIMIT", "DAW_COMMAND_UNRESOLVED", "DAW_CONVERSION_BLOCKED", "DAW_CONTINUITY_FAILED",
                 "EXPORT_IMPORT_REJECTED"):  # fmt: skip
        assert TerminationReason[name].value == name.lower()


def test_daw_dispatch_intents_and_observations_are_typed():
    accepted = AcceptedReference(daw_state(), 0)
    apply = make_request("apply_root_0001")
    intent = DispatchIntent(OperationKind.DAW_APPLY, apply, accepted, DecisionCallId("call-1"), AttemptId("at-1"),
                            ExecutionId("e1"))  # fmt: skip
    assert intent.request is apply
    with pytest.raises(ValueError):
        DispatchIntent(OperationKind.DAW_APPLY, apply, accepted, DecisionCallId("call-1"), None, ExecutionId("e1"))
    with pytest.raises(ValueError):
        DispatchIntent(OperationKind.DAW_APPLY, apply, accepted, DecisionCallId("call-1"), AttemptId("at-1"),
                       ExecutionId("e2"))  # fmt: skip
    with pytest.raises(ValueError):
        DispatchIntent(OperationKind.RENDER, apply, accepted, DecisionCallId("call-1"), AttemptId("at-1"),
                       ExecutionId("e1"))  # fmt: skip
    write = DawWriteConfirmed("apply_root_0001", BASE, READING, READING.normalized, READING.db - BASE.db, False)
    assert OperationObserved(RunEventId("ev-1"), OperationKind.DAW_APPLY, write).outcome is write
    with pytest.raises(ValueError):
        OperationObserved(RunEventId("ev-1"), OperationKind.RENDER, write)
    query = DispatchIntent(OperationKind.DAW_STATE, DawStateQuery("apply_root_0001", True), accepted, None, None, None)
    assert OperationObserved(RunEventId("ev-2"), OperationKind.DAW_STATE, snapshot()).operation is query.operation
    prepare = DispatchIntent(OperationKind.EXPORT_PREPARE, export_request(), accepted, DecisionCallId("call-1"),
                             AttemptId("at-1"), ExecutionId("e1"))  # fmt: skip
    assert (
        OperationObserved(RunEventId("ev-3"), OperationKind.EXPORT_PREPARE, awaiting()).operation is prepare.operation
    )


# --- the suspension checkpoint and the suspended run ------------------------------------------------------------------


WRITE = DawWriteConfirmed("apply_root_0001", BASE, READING, READING.normalized, READING.db - BASE.db, False)


def checkpoint(**changes):
    base = DawCorrectionSuspended(RunId("run-1"), DecisionCallId("call-1"), decision(), AttemptId("at-1"), daw_state(),
                                  plan(), AcceptedReference(daw_state(), 0), frozenset({"token-1"}), (), (), APPLY,
                                  WRITE, awaiting(), UTC)  # fmt: skip
    return replace(base, **changes)


def test_a_suspension_checkpoint_binds_the_confirmed_write_its_anchor_and_the_awaiting_export():
    assert checkpoint().awaiting.request.anchor.reading == WRITE.after


@pytest.mark.parametrize(
    "changes",
    [
        {"write": replace(WRITE, command_id="apply_other_001")},
        {"write": replace(WRITE, after=BASE, achieved_change_db=0.0)},  # not the anchored reading
        {
            "awaiting": awaiting(
                export_request(anchor=anchor(source_id="apply_other_001", apply=make_request("apply_other_001")))
            )
        },
        {"awaiting": awaiting(export_request(candidate_revision=Revision("r3")))},
        {"plan": plan(command_id="apply_other_001")},
        {"accepted": AcceptedReference(offline_state(), 0)},
        {"tokens": frozenset()},
        {"paused_at_utc": datetime(2026, 11, 19, 12)},
        {"apply": make_request("apply_root_0001", change=-2.0)},  # another apply on the same track
        {"apply": make_request("apply_other_0001")},
        {"decision": decision(replace(ACTION, action_id=ActionId("a2")))},  # the apply executed another action
        # review fix 2026-11-19 (P2): every export setting of the plan must equal the awaiting request's
        {"plan": plan(timeline=DawExportTimeline(0.5, 44100, 8))},
        {"plan": plan(format=DawExportFormat("WAV", "PCM_16"))},
        {"plan": plan(render_spec=VersionRef("fl-export", "2"))},
    ],
)
def test_suspension_checkpoint_invariants(changes):
    with pytest.raises(ValueError):
        checkpoint(**changes)


def suspended_trace(payload):
    event = RunTraceEvent(RunEventId("ev-1"), 1, RunId("run-1"), UTC, BudgetUsage(0, 1, 0, 0, 1.0, 1), payload)
    initial = AcceptedReference(RUN_STATE, 0)
    return event, RunJournalSnapshot(RunId("run-1"), initial, (event,), ())


def test_a_suspended_run_ends_its_trace_with_the_checkpoint():
    controlled = replace(RUN_STATE, daw=DawParameterState(RUN_STATE.project_id, RUN_STATE.revision,
                                                          (DawTrackParameter(BINDING, 2.0, BASE),)))  # fmt: skip
    request = _run_request(initial_state=controlled, daw_session=session())
    event, trace = suspended_trace(checkpoint())
    assert RunSuspended(request, trace, event).checkpoint_event is event
    with pytest.raises(ValueError):
        RunSuspended(_run_request(), trace, event)  # an offline run cannot be suspended for an export
    other = RunTraceEvent(RunEventId("ev-2"), 1, RunId("run-1"), UTC, BudgetUsage(0, 1, 0, 0, 1.0, 1), request)
    with pytest.raises(ValueError):
        RunSuspended(request, RunJournalSnapshot(RunId("run-1"), AcceptedReference(RUN_STATE, 0), (other,), ()), other)


def test_the_execution_receipt_type_is_unchanged_for_the_offline_path():
    assert ExecutionReceipt.__dataclass_fields__.keys() == {
        "execution_id", "action_id", "project_id", "before_revision", "status", "candidate_revision",
        "candidate_artifacts", "superseded_artifact_ids", "issue",
    }  # fmt: skip


def test_export_outputs_are_unchanged():
    assert DawExportOutput.__dataclass_fields__.keys() == {
        "artifact_id", "replaces", "kind", "source_track_ids", "channel_count", "file_name",
    }  # fmt: skip
