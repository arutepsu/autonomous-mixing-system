"""Coordinator Increment 4 (ADR-038): the recovery-lineage query, the recovery snapshot and the new recovery records.

Adapter level: the real runtime controller and SQLite journal against device script v2 on a fake host (not live FL
evidence). Record level: the lineage and snapshot invariants, the decision rule, the standalone rollback record, the
three-slot DAW write reserve and the trace additions.
"""

import json
from dataclasses import replace

import pytest
from fl_recovery_support import grant
from fl_runtime_support import make_request
from test_daw_coordinator_contracts import daw_state, evidence
from test_fl_runtime_recovery_controller import ROOT, World, live_curve

from autonomous_mixing.adapters.fl_studio.journal import SqliteCommandJournal
from autonomous_mixing.adapters.fl_studio.serialization import request_json
from autonomous_mixing.core.admission.daw import daw_recovery_decision
from autonomous_mixing.core.loop.budgets import DAW_WRITE_RECOVERY_RESERVE, check_budget
from autonomous_mixing.core.records.acceptance import AcceptedReference
from autonomous_mixing.core.records.actions import RollbackReceipt, RollbackScope, RollbackStatus
from autonomous_mixing.core.records.budgets import (
    BudgetBlock,
    BudgetBlockReason,
    BudgetGrant,
    BudgetLimits,
    BudgetOperation,
    BudgetUsage,
)
from autonomous_mixing.core.records.coordinator_progress import _PENDING_EFFECT_REASONS
from autonomous_mixing.core.records.daw_control import DawRefusalCode, DawWriteRefused
from autonomous_mixing.core.records.daw_correction import DawContinuationDisposition
from autonomous_mixing.core.records.daw_recovery import (
    DawLedgerKind,
    DawLineageQuery,
    DawReconcileFinding,
    DawRecoveryDecision,
    DawRecoveryEntry,
    DawRecoveryPlan,
    DawRecoverySnapshot,
    DawResolutionKind,
    DawRestoreConfirmed,
    DawRootStatus,
)
from autonomous_mixing.core.records.daw_rollback import DawRollbackRecord
from autonomous_mixing.core.records.identifiers import (
    AttemptId,
    CheckpointId,
    DecisionCallId,
    ExecutionId,
    RunEventId,
    RunId,
)
from autonomous_mixing.core.records.results import PortFailure, Success
from autonomous_mixing.core.records.run_trace import DispatchIntent, OperationKind
from autonomous_mixing.core.records.termination import TerminationReason


@pytest.fixture
def world(tmp_path):
    journal = SqliteCommandJournal(tmp_path / "journal.db", session_key="session-1")
    yield World(journal)
    journal.close()


def lineage(world, root=ROOT):
    result = world.controller.recovery_lineage(root)
    assert isinstance(result, Success), result
    return result.value


# --- the lineage query: one case per root status ----------------------------------------------------------------


def test_a_confirmed_root_reports_its_exact_request_outcome_start_and_watermarks(world):
    applied = world.apply()
    found = lineage(world)
    assert found.status is DawRootStatus.CONFIRMED and found.outcome == applied
    assert found.apply == make_request(ROOT) and found.recorded_start == applied.before
    assert found.restorations == found.findings == found.foreign_after == found.unresolved == ()
    assert found.own_latest() and not found.conversion_blocked
    assert found.reservation_watermark == found.root_seq == found.ledger_watermark


def test_an_off_target_root_has_a_lineage_although_it_has_no_anchor(world):
    world.host.curve = lambda v: live_curve(v) + (0.0 if v == 0.76 else 0.2)
    world.apply()
    found = lineage(world)
    assert found.status is DawRootStatus.OFF_TARGET and found.conversion_blocked
    assert found.own_latest()
    anchored = world.controller.state_anchor(ROOT)  # export anchors are unchanged: confirmed sources only
    assert isinstance(anchored, PortFailure) and "confirmed" in anchored.message


def test_an_uncertain_root_is_unresolved_in_its_lineage_until_resolved(world):
    world.lose_next_reply = True
    world.apply()
    found = lineage(world)
    assert found.status is DawRootStatus.UNCERTAIN and found.unresolved == (ROOT,) and found.own_latest()
    _, restored = world.restore()
    assert isinstance(restored, DawRestoreConfirmed)
    world.resolve("resolve_0001", ROOT, DawResolutionKind.RESTORED_TO_RECORDED_START, "restore_cmd_0001")
    after = lineage(world)
    assert [r.command_id for r in after.restorations] == ["restore_cmd_0001"] and after.unresolved == ()
    assert [(f.entry_id, f.kind) for f in after.findings] == [("resolve_0001", DawLedgerKind.RESOLVED)]
    assert after.restoration("restore_cmd_0001").outcome == restored and after.own_latest()


def test_a_pending_root_is_reported_and_a_refused_root_has_no_lineage(world):
    journal = world.journal
    request = make_request("apply_pending_1")
    from fl_recovery_support import journal_apply

    journal_apply(journal, "apply_pending_1", pending=True)
    found = lineage(world, "apply_pending_1")
    assert found.status is DawRootStatus.PENDING and found.unresolved == ("apply_pending_1",)
    assert found.recorded_start == request.inspection.fader
    journal._remember_refusal("apply_local_009", request_json(make_request("apply_local_009")),
                              json.dumps({"id": "apply_local_009", "local_refusal": True, "type": "set_gain_db"},
                                         sort_keys=True),
                              DawWriteRefused("apply_local_009", DawRefusalCode.UNAVAILABLE, "local", None))  # fmt: skip
    refused = world.controller.recovery_lineage("apply_local_009")
    assert isinstance(refused, PortFailure)
    assert isinstance(world.controller.recovery_lineage("apply_unknown_1"), PortFailure)


def test_a_foreign_reservation_after_the_root_is_reported_and_breaks_own_latest(world):
    world.apply()
    second = world.apply("apply_second_01", change=-0.5)  # reserved, then refused on the wire: still a reservation
    assert isinstance(second, DawWriteRefused) and second.code is DawRefusalCode.START_GAIN
    found = lineage(world)
    assert [(f.entry_id, f.kind) for f in found.foreign_after] == [("apply_second_01", DawLedgerKind.RESERVED)]
    assert not found.own_latest()
    assert isinstance(world.controller.recovery_lineage("apply_second_01"), PortFailure)  # refused: no lineage


def test_a_reconciliation_of_the_lineage_is_a_finding_not_a_foreign_entry(world):
    world.lose_next_reply = True
    world.apply()
    world.reconcile("reconcile_0001", ROOT)
    found = lineage(world)
    assert [(f.entry_id, f.kind) for f in found.findings] == [("reconcile_0001", DawLedgerKind.RECONCILED)]
    assert found.foreign_after == () and found.ledger_watermark > found.reservation_watermark


def test_the_recovery_snapshot_is_coherent_and_refuses_a_journal_change_during_the_capture(world, monkeypatch):
    world.apply()
    snapshot = world.controller.recovery_snapshot(ROOT)
    assert isinstance(snapshot, Success)
    assert snapshot.value.inspection.fader.normalized == world.host.volumes[2]
    controller = world.controller
    real = controller.inspect

    def inspect_then_write(request):
        read = real(request)
        world.journal._remember_refusal(
            "apply_local_008", request_json(make_request("apply_local_008")),
            json.dumps({"id": "apply_local_008", "local_refusal": True, "type": "set_gain_db"}, sort_keys=True),
            DawWriteRefused("apply_local_008", DawRefusalCode.UNAVAILABLE, "local", None))  # fmt: skip
        return read

    monkeypatch.setattr(controller, "inspect", inspect_then_write)
    changed = controller.recovery_snapshot(ROOT)
    assert isinstance(changed, PortFailure) and changed.retryable


# --- record invariants ----------------------------------------------------------------------------------------------


def test_lineage_invariants_refuse_inconsistent_reports(world):
    world.apply()
    found = lineage(world)
    with pytest.raises(ValueError, match="matching"):
        replace(found, status=DawRootStatus.OFF_TARGET)
    with pytest.raises(ValueError, match="basis rule"):
        replace(found, recorded_start=found.outcome.after)
    with pytest.raises(ValueError, match="unresolved"):
        replace(found, unresolved=(ROOT,))
    with pytest.raises(ValueError, match="watermark"):
        replace(found, reservation_watermark=0)


def test_a_snapshot_must_agree_with_its_lineage(world):
    world.apply()
    snapshot = world.controller.recovery_snapshot(ROOT).value
    blocked = replace(snapshot.control_state, blocked_conversions=frozenset({snapshot.lineage.profile.conversion}))
    with pytest.raises(ValueError, match="block"):
        DawRecoverySnapshot(snapshot.lineage, snapshot.inspection, blocked)


def test_the_decision_rule_maps_each_finding_and_known_writes_need_no_reconciliation(world):
    assert daw_recovery_decision(DawRecoveryEntry.OFF_TARGET, ROOT, None).plan is DawRecoveryPlan.RESTORE
    assert daw_recovery_decision(DawRecoveryEntry.CONFIRMED_WRITE, ROOT, None).plan is DawRecoveryPlan.RESTORE
    world.lose_next_reply = True
    world.apply()
    reconciliation = world.reconcile("reconcile_0001", ROOT).value
    decided = daw_recovery_decision(DawRecoveryEntry.UNCERTAIN_WRITE, ROOT, reconciliation)
    assert decided.finding is DawReconcileFinding.AT_COMMANDED and decided.plan is DawRecoveryPlan.RESTORE_THEN_RESOLVE
    with pytest.raises(ValueError):
        daw_recovery_decision(DawRecoveryEntry.UNCERTAIN_WRITE, "apply_other_001", reconciliation)
    with pytest.raises(ValueError):
        daw_recovery_decision(DawRecoveryEntry.CONFIRMED_WRITE, ROOT, reconciliation)
    expected = {
        DawReconcileFinding.AT_RECORDED_START: DawRecoveryPlan.RESOLVE_THEN_RESTORE,
        DawReconcileFinding.AT_COMMANDED: DawRecoveryPlan.RESTORE_THEN_RESOLVE,
        DawReconcileFinding.NEITHER: DawRecoveryPlan.RESTORE_THEN_RESOLVE,
        DawReconcileFinding.AMBIGUOUS: DawRecoveryPlan.RESTORE_THEN_RESOLVE,
        DawReconcileFinding.IDENTITY_UNVERIFIED: DawRecoveryPlan.NONE,
    }
    for finding, plan in expected.items():
        assert DawRecoveryDecision(DawRecoveryEntry.UNCERTAIN_WRITE, ROOT, plan, "reconcile_0001", finding).plan is plan
        with pytest.raises(ValueError):
            DawRecoveryDecision(DawRecoveryEntry.UNCERTAIN_WRITE, ROOT,
                                next(p for p in DawRecoveryPlan if p is not plan), "reconcile_0001", finding)  # fmt: skip


def receipt(status=RollbackStatus.UNCERTAIN, scope=RollbackScope.SELECTED_STATE_WITH_FADER):
    restored = daw_state() if status is RollbackStatus.VERIFIED else None
    fader = (evidence(),) if status is RollbackStatus.VERIFIED else ()
    retained = None if scope is RollbackScope.COMPLETE_SAVED_STATE else (
        RollbackStatus.VERIFIED if status is RollbackStatus.VERIFIED else RollbackStatus.UNCERTAIN)  # fmt: skip
    return RollbackReceipt(ExecutionId("e1"), CheckpointId("c1"), status, restored, "labelled", scope, retained, fader)


def test_the_standalone_rollback_record_is_a_daw_selected_state_rollback_of_its_before_state():
    record = DawRollbackRecord(RunId("run-1"), AttemptId("at-1"), ExecutionId("e1"), daw_state(),
                               receipt(RollbackStatus.VERIFIED))  # fmt: skip
    assert record.receipt.status is RollbackStatus.VERIFIED
    with pytest.raises(ValueError, match="execution"):
        DawRollbackRecord(RunId("run-1"), AttemptId("at-1"), ExecutionId("e2"), daw_state(), receipt())
    with pytest.raises(ValueError, match="DAW parameter state"):
        DawRollbackRecord(RunId("run-1"), AttemptId("at-1"), ExecutionId("e1"), replace(daw_state(), daw=None),
                          receipt())  # fmt: skip
    with pytest.raises(ValueError):
        DawRollbackRecord(RunId("run-1"), AttemptId("at-1"), ExecutionId("e1"), daw_state(selected=1.0),
                          receipt(RollbackStatus.VERIFIED))  # fmt: skip


def test_a_daw_write_needs_three_free_recovery_slots_and_a_render_one():
    assert DAW_WRITE_RECOVERY_RESERVE == 3
    limits = BudgetLimits(3, 3, 3, 3, 60.0, max_daw_writes=2)
    for used, ok in ((0, True), (1, False), (2, False)):
        usage = BudgetUsage(0, 1, 0, used, 1.0)
        result = check_budget(BudgetOperation.DAW_WRITE, limits=limits, usage=usage, elapsed_seconds=1.0)
        assert isinstance(result, BudgetGrant) is ok
        if not ok:
            assert isinstance(result, BudgetBlock) and result.reason is BudgetBlockReason.RECOVERY_CAPACITY_LIMIT
    render = check_budget(BudgetOperation.RENDER_CALL, limits=limits, usage=BudgetUsage(0, 1, 0, 2, 1.0),
                          elapsed_seconds=1.0)  # fmt: skip
    assert isinstance(render, BudgetGrant)  # the offline reserve stays one slot
    assert isinstance(check_budget(BudgetOperation.DAW_WRITE, limits=replace(limits, max_recovery_operations=2),
                                   usage=BudgetUsage(0, 1, 0, 0, 1.0), elapsed_seconds=1.0), BudgetBlock)  # fmt: skip


def test_new_markers_dispositions_and_trace_members():
    assert TerminationReason.RESTORATION_UNCERTAIN in _PENDING_EFFECT_REASONS
    assert DawContinuationDisposition.ROLLED_BACK.value == "rolled_back"
    accepted = AcceptedReference(daw_state(), 0)
    query = DispatchIntent(OperationKind.DAW_STATE, DawLineageQuery(ROOT, True), accepted, None, None, None)
    assert query.request.fresh_inspection
    with pytest.raises(ValueError, match="recovery intent"):
        from autonomous_mixing.core.records.daw_recovery import DawReconcileRequest

        request = make_request(ROOT)
        DispatchIntent(OperationKind.DAW_RECOVERY, DawReconcileRequest("reconcile_0001", ROOT,
                                                                       request.resolution.state.binding,
                                                                       request.profile), accepted, None, None, None)  # fmt: skip


def test_a_restoration_intent_names_its_authorizations_execution_and_charge(world):
    from datetime import datetime, timezone

    from autonomous_mixing.core.records.run_trace import RunTraceEvent

    world.apply()
    request = world.restore_request("restore_cmd_0001", "recovery_auth_0001")
    accepted = AcceptedReference(daw_state(), 0)
    ids = (DecisionCallId("call-1"), AttemptId("at-1"))
    intent = DispatchIntent(OperationKind.DAW_RECOVERY, request, accepted, *ids, ExecutionId("e1"))
    with pytest.raises(ValueError, match="execution"):
        DispatchIntent(OperationKind.DAW_RECOVERY, request, accepted, *ids, ExecutionId("e2"))
    usage = grant().usage_after_charge
    utc = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
    assert RunTraceEvent(RunEventId("ev-1"), 1, RunId("run-1"), utc, usage, intent).payload is intent
    with pytest.raises(ValueError, match="charge"):
        RunTraceEvent(RunEventId("ev-1"), 1, RunId("run-1"), utc, replace(usage, recovery_operations=2), intent)


def test_an_own_lineage_restoration_is_refused_atomically_after_foreign_history(world):
    """Review fix 2026-11-20: the journal decides the lineage rule inside the reservation transaction."""
    from dataclasses import replace as _replace

    from autonomous_mixing.core.records.daw_recovery import DawRestoreRefused

    world.apply()
    request = _replace(world.restore_request("restore_cmd_0001", "recovery_auth_0001"), own_lineage_only=True)
    world.apply("apply_second_01", change=-0.5)  # a foreign reservation after the request was gated
    outcome = world.controller.restore(request)
    assert isinstance(outcome, DawRestoreRefused) and "foreign_history" in outcome.explanation
    state = world.controller.control_state().value
    assert state.recovery_charges == () and state.unresolved == ()
    assert not [m for m in world.sent if m["type"] == "restore_gain"]
    plain = world.restore_request("restore_cmd_0002", "recovery_auth_0002")  # without the flag: ADR-032 unchanged
    assert not plain.own_lineage_only
    assert isinstance(world.controller.restore(plain), DawRestoreConfirmed)
