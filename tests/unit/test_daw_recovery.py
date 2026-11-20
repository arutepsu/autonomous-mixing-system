"""Coordinator Increment 4 (ADR-038): authorized DAW recovery routing (``recover_daw``).

Real adapters throughout: the FL controller and SQLite journal against device script v2 (or v1) on a fake host, the
real export handoff, file analyzer and integrity inspector over temporary synthetic WAVs; labelled doubles only for
decisions and diagnosis. Orchestration evidence only, never live FL evidence.
"""

import shutil
from dataclasses import replace
from types import SimpleNamespace

import pytest
from daw_continuation_support import ACCEPT, NO_IMPROVEMENT, SESSION, SILENT, Continuation, limits
from fl_device_support import load_device
from fl_runtime_support import make_request
from test_coordinator import Clock, no_change, propose
from test_daw_coordinator import LoseApplyReply
from test_fl_runtime_recovery_controller import DeviceTransport, live_curve

import autonomous_mixing.core.loop.daw_continuation as daw_continuation
import autonomous_mixing.core.loop.daw_execution as daw_execution
from autonomous_mixing.adapters.fl_studio import protocol
from autonomous_mixing.adapters.fl_studio.profile import make_apply_command
from autonomous_mixing.core.loop.daw_recovery import recovery_ids, require_recovery_authority
from autonomous_mixing.core.records.acceptance import AcceptanceCommitUnknown, AcceptanceNotCommitted, AcceptedReference
from autonomous_mixing.core.records.actions import DawRollbackEvidenceKind, ExecutionStatus, RollbackStatus
from autonomous_mixing.core.records.coordinator_runtime import ClockReading
from autonomous_mixing.core.records.daw_binding import DawFaderReading
from autonomous_mixing.core.records.daw_correction import DawContinuationDisposition, DawContinuationFinished
from autonomous_mixing.core.records.daw_recovery import (
    DawReconcileFinding,
    DawRecoveryDecision,
    DawRecoveryEntry,
    DawResolutionKind,
    DawRestoreRequest,
    DawRestoreTarget,
    classify_reconciliation,
)
from autonomous_mixing.core.records.daw_rollback import DawRollbackRecord
from autonomous_mixing.core.records.feedback import ActionFeedback, ActionFeedbackDisposition, DecisionFeedback
from autonomous_mixing.core.records.run import AttemptDisposition, CorrectionAttempt, FinalProjectState
from autonomous_mixing.core.records.run_report import RunReport, RunSuspended
from autonomous_mixing.core.records.run_trace import (
    DispatchIntent,
    OperationKind,
    OperationObserved,
    TerminalSnapshot,
    TraceNotAppended,
)
from autonomous_mixing.core.records.termination import TerminationReason as R

START = 0.76  # the live start and the initial state's confirmed fader (normalized)
OFFSET = 0.0005  # inside the start tolerances (0.001 normalized; about 0.0145 dB on the curve), but not zero


@pytest.fixture
def harness(tmp_path):
    made = []

    def make(**kwargs):
        made.append(Continuation(tmp_path / f"h{len(made)}", **kwargs))
        return made[-1]

    yield make
    for item in made:
        item.close()


def payloads(result, kind):
    return [e.payload for e in result.trace.events if isinstance(e.payload, kind)]


def trace(h):
    return h.real_journal.snapshot().value.events


def terminal(h):
    return trace(h)[-1].payload


def feedback(result):
    return [p.outcome for p in payloads(result, DecisionFeedback) if isinstance(p.outcome, ActionFeedback)]


def rollback_of(result):
    attempts = payloads(result, CorrectionAttempt)
    if attempts and attempts[-1].rollback is not None:
        return attempts[-1].rollback
    return payloads(result, DawRollbackRecord)[-1].receipt


def suspend(h):
    first = h.start()
    assert isinstance(first, RunSuspended), first
    return first


def resumed(h, master=ACCEPT, **kwargs):
    suspended = suspend(h)
    return h.resume(suspended, h.operator_export(suspended, master, **kwargs))


def assert_restored(report, reason, kind=DawRollbackEvidenceKind.EXACT_RESTORE):
    assert isinstance(report, RunReport), report
    run = report.final_run
    assert (run.termination_reason, run.final_project_state) == (reason, FinalProjectState.RESTORED_TO_LAST_ACCEPTED)
    rollback = rollback_of(report)
    assert rollback.status is RollbackStatus.VERIFIED and rollback.retained_audio is RollbackStatus.VERIFIED
    assert [item.kind for item in rollback.fader_evidence] == [kind]
    assert report.verified == report.authoritative and report.authoritative.sequence == 0


def assert_not_verified(report, reason, status):
    assert isinstance(report, RunReport), report
    assert report.final_run.termination_reason is reason
    assert report.final_run.final_project_state is FinalProjectState.UNKNOWN and report.verified is None
    assert rollback_of(report).status is status


class DropApply(DeviceTransport):
    """The write never reaches the device and no reply arrives; optionally the fader is moved meanwhile."""

    def __init__(self, world, move_to=None):
        super().__init__(world)
        self.move_to = move_to

    def send(self, raw):
        message = protocol.decode(raw)
        if message["type"] != "set_gain_db":
            return super().send(raw)
        self.world.sent.append(message)
        if self.move_to is not None:
            self.world.host.volumes[2] = self.move_to
        self.reply = None


class LoseApplyReplyAndMove(LoseApplyReply):
    def __init__(self, world, move_to):
        super().__init__(world)
        self.move_to = move_to

    def send(self, raw):
        super().send(raw)
        if protocol.decode(raw)["type"] == "set_gain_db":
            self.world.host.volumes[2] = self.move_to


class Wrapped:
    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)


def recovery_with(h, **methods):
    """A recovery controller delegating to the real one, with labelled overrides (controlled doubles)."""
    wrapper = Wrapped(h.world.controller)
    for name, method in methods.items():
        setattr(wrapper, name, method)
    collaborators = h.collaborators

    def patched(clock=None):
        values = collaborators(clock)
        values["recovery_controller"] = wrapper
        return values

    h.collaborators = patched
    return wrapper


# --- 1. a rejected candidate is restored exactly and the loop continues --------------------------------------------


def test_a_rejected_candidate_is_restored_exactly_and_the_next_round_starts_from_the_restored_fader(harness):
    h = harness(decisions=(propose(0.0), propose(0.0)), budget=limits(max_recovery_operations=5))
    second = resumed(h, NO_IMPROVEMENT)
    assert isinstance(second, RunSuspended), second  # the next round wrote again and suspended
    events = second.trace.events
    attempt = [e.payload for e in events if isinstance(e.payload, CorrectionAttempt)][-1]
    assert attempt.disposition is AttemptDisposition.ROLLED_BACK
    rollback = attempt.rollback
    assert rollback.status is RollbackStatus.VERIFIED and rollback.retained_audio is RollbackStatus.VERIFIED
    (item,) = rollback.fader_evidence
    assert item.kind is DawRollbackEvidenceKind.EXACT_RESTORE and item.reading == item.saved_target
    finished = [e.payload for e in events if isinstance(e.payload, DawContinuationFinished)]
    assert finished[-1].disposition is DawContinuationDisposition.ROLLED_BACK
    applies = h.sent("set_gain_db")
    assert len(applies) == 2 and len(h.sent("restore_gain")) == 1
    second_apply = second.checkpoint_event.payload.apply
    assert second_apply.inspection.fader == SESSION.binding.baseline_fader  # started from the restored fader
    assert h.world.controller.control_state().value.unresolved == ()


# --- 2. unaccepted candidates that terminate after a verified rollback -----------------------------------------------


def test_an_inconclusive_or_failed_candidate_is_restored_then_ends_with_its_own_reason(harness):
    h = harness(decisions=(propose(0.0),))
    report = resumed(h, SILENT)
    assert_restored(report, report.final_run.termination_reason)
    assert report.final_run.termination_reason in (R.VERIFICATION_INCONCLUSIVE, R.CANDIDATE_ANALYSIS_FAILED)
    assert payloads(report, CorrectionAttempt)[-1].disposition is AttemptDisposition.ROLLED_BACK


class NotCommitted(Wrapped):
    def commit(self, event):
        return AcceptanceNotCommitted(event.acceptance_id, "labelled refusal")


class AbsentAfterUnknown(Wrapped):
    def commit(self, event):
        return AcceptanceCommitUnknown(event.acceptance_id, "labelled lost acknowledgement")


def test_an_acceptance_that_was_not_committed_is_restored_and_ends(harness):
    h = harness(decisions=(propose(0.0),), journal=NotCommitted)
    report = resumed(h)
    assert_restored(report, R.RECORDING_FAILED)


def test_the_minimum_budget_covers_the_authority_query_restoration_and_integrity(harness):
    """Test 15: max_recovery_operations = 3, an unknown commit confirmed absent, then the complete recovery."""
    h = harness(decisions=(propose(0.0),), journal=AbsentAfterUnknown, budget=limits(max_recovery_operations=3))
    report = resumed(h)
    assert_restored(report, R.RECORDING_FAILED)
    assert report.final_run.usage.recovery_operations == 3
    charges = h.world.controller.control_state().value.recovery_charges
    intent = next(p for p in payloads(report, DispatchIntent) if isinstance(p.request, DawRestoreRequest))
    assert [c.budget_grant for c in charges] == [intent.request.authorization.budget_grant]


def test_two_free_recovery_slots_refuse_the_write_before_any_dispatch(harness):
    h = harness(decisions=(propose(0.0),), budget=limits(max_recovery_operations=2))
    report = h.start()
    assert isinstance(report, RunReport) and report.final_run.termination_reason is R.RECOVERY_CAPACITY_LIMIT
    assert not h.sent("set_gain_db") and h.world.controller.control_state().value.recovery_charges == ()


def test_after_a_rolled_back_rejection_the_next_write_needs_three_more_free_slots(harness):
    h = harness(decisions=(propose(0.0), propose(0.0)), budget=limits(max_recovery_operations=4))
    report = resumed(h, NO_IMPROVEMENT)
    assert isinstance(report, RunReport) and report.final_run.termination_reason is R.RECOVERY_CAPACITY_LIMIT
    assert len(h.sent("set_gain_db")) == 1 and report.final_run.usage.recovery_operations == 2


# --- 3. before an execution receipt ----------------------------------------------------------------------------------


def test_a_failed_export_preparation_is_restored_with_a_standalone_receipt(harness):
    h = harness(decisions=(propose(0.0),))
    shutil.rmtree(h.exports)
    report = h.start()
    assert_restored(report, R.EXECUTION_PREPARATION_FAILED)
    assert payloads(report, DawRollbackRecord) and not payloads(report, CorrectionAttempt)
    last = feedback(report)[-1]
    assert (last.disposition, last.execution_status, last.rollback_status) == (
        ActionFeedbackDisposition.ROLLED_BACK, ExecutionStatus.FAILED, RollbackStatus.VERIFIED)  # fmt: skip


def test_a_file_rejection_is_restored_and_ends_with_the_import_reason(harness):
    h = harness(decisions=(propose(0.0),))
    report = resumed(h, extra="notes.txt")
    assert_restored(report, R.EXPORT_IMPORT_REJECTED)


def test_a_fader_moved_during_the_pause_is_restored_and_ends_with_the_continuity_reason(harness):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    h.world.host.volumes[2] += 1e-6
    report = h.resume(suspended, h.operator_export(suspended, ACCEPT))
    assert_restored(report, R.DAW_CONTINUITY_FAILED)
    assert h.world.host.volumes[2] == START


def test_a_foreign_reservation_during_the_pause_withholds_recovery(harness):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    h.world.apply("apply_foreign_01", change=-0.5)  # reserved, then refused on the wire: a foreign reservation
    report = h.resume(suspended, h.operator_export(suspended, ACCEPT))
    assert_not_verified(report, R.DAW_CONTINUITY_FAILED, RollbackStatus.UNCERTAIN)
    assert "withheld" in rollback_of(report).explanation and not h.sent("restore_gain")
    # withheld at the lineage check, before any recovery read or effect (the reservation check is the backstop)
    assert not payloads(report, DawRestoreTarget)
    assert not [p for p in payloads(report, DispatchIntent) if p.operation is OperationKind.DAW_RECOVERY]


# --- 5. off-target writes --------------------------------------------------------------------------------------------


def off_target(h):
    h.world.host.curve = lambda v: live_curve(v) + (0.0 if v == START else 0.2)


def test_an_off_target_write_is_restored_exactly_and_the_block_persists(harness):
    h = harness(decisions=(propose(0.0), no_change))
    off_target(h)
    report = h.start()
    assert isinstance(report, RunReport) and report.final_run.termination_reason is R.DAW_CONVERSION_BLOCKED
    assert report.final_run.final_project_state is FinalProjectState.RESTORED_TO_LAST_ACCEPTED
    assert rollback_of(report).status is RollbackStatus.VERIFIED and h.world.host.volumes[2] == START
    assert SESSION.profile.conversion in h.fl_journal.control_state().blocked_conversions
    assert len(h.decider.situations) == 1


class LoseRestoreReply(DeviceTransport):
    def send(self, raw):
        if protocol.decode(raw)["type"] == "restore_gain":
            self.world.lose_next_reply = True
        super().send(raw)


def test_an_off_target_write_whose_restoration_reply_is_lost_claims_no_rollback(harness):
    h = harness(decisions=(propose(0.0), no_change))
    off_target(h)
    h.world.controller.transport_factory = lambda: LoseRestoreReply(h.world)
    report = h.start()
    assert_not_verified(report, R.DAW_CONVERSION_BLOCKED, RollbackStatus.UNCERTAIN)
    assert h.fl_journal.control_state().unresolved == ()  # the restoration was reconciled and resolved
    assert rollback_of(report).fader_evidence == ()  # an unconfirmed restoration is never fader evidence


# --- 6. uncertain writes, one test per finding --------------------------------------------------------------------


def decision_of(report):
    return payloads(report, DawRecoveryDecision)[-1]


def test_a_lost_apply_reply_at_the_commanded_value_is_restored_resolved_and_verified(harness):
    h = harness(decisions=(propose(0.0), no_change))
    h.world.controller.transport_factory = lambda: LoseApplyReply(h.world)
    report = h.start()
    assert_restored(report, R.RENDER_FAILED)
    assert report.final_run.termination_explanation.startswith("DAW write:")
    assert decision_of(report).finding is DawReconcileFinding.AT_COMMANDED
    last = feedback(report)[-1]
    assert (last.disposition, last.execution_status, last.rollback_status) == (
        ActionFeedbackDisposition.UNRESOLVED, ExecutionStatus.UNCERTAIN, RollbackStatus.VERIFIED)  # fmt: skip
    assert h.fl_journal.control_state().unresolved == () and len(h.decider.situations) == 1


def test_a_lost_apply_reply_at_neither_reference_is_restored_and_resolved(harness):
    h = harness(decisions=(propose(0.0), no_change))
    h.world.controller.transport_factory = lambda: LoseApplyReplyAndMove(h.world, 0.5)
    report = h.start()
    assert_restored(report, R.RENDER_FAILED)
    assert decision_of(report).finding is DawReconcileFinding.NEITHER


def test_a_dropped_write_at_the_exact_start_is_resolved_and_observed_without_a_restoration(harness):
    h = harness(decisions=(propose(0.0), no_change))
    h.world.controller.transport_factory = lambda: DropApply(h.world)
    report = h.start()
    assert_restored(report, R.RENDER_FAILED, DawRollbackEvidenceKind.EXACT_OBSERVATION)
    assert decision_of(report).finding is DawReconcileFinding.AT_RECORDED_START and not h.sent("restore_gain")


def test_an_ambiguous_finding_cannot_arise_from_an_admissible_write_under_the_calibrated_profile():
    """The smallest advertised cut (0.1 dB) exceeds the start dB tolerance (0.05 dB): a lost reply of an admissible
    write reads at the start, at the commanded value or at neither, never ambiguously (so the AMBIGUOUS branch is
    pinned by the decision-rule contract tests, and its fail-closed edge by the next test)."""
    request = make_request("apply_small_001", change=-0.1)
    command = make_apply_command(request)
    start = request.inspection.fader
    commanded = DawFaderReading(command["target_normalized"], start.db - 0.1)
    profile = request.profile
    for reading, finding in ((start, DawReconcileFinding.AT_RECORDED_START),
                             (commanded, DawReconcileFinding.AT_COMMANDED),
                             (DawFaderReading((start.normalized + commanded.normalized) / 2, start.db - 0.05),
                              DawReconcileFinding.NEITHER)):  # fmt: skip
        assert classify_reconciliation(profile, start, commanded, reading)[0] is finding


def test_a_restore_then_resolve_plan_whose_gate_finds_the_exact_start_stays_unresolved(harness):
    """The fail-closed edge: no restoration is written (``NoWrite``), so no resolution kind is supported."""
    h = harness(decisions=(propose(0.0), no_change))
    h.world.controller.transport_factory = lambda: LoseApplyReply(h.world)
    real = h.world.controller.reconcile

    def reconcile_then_move_back(request):
        found = real(request)
        h.world.host.volumes[2] = START  # moved back exactly before the restore gate's inspection
        return found

    recovery_with(h, reconcile=reconcile_then_move_back)
    report = h.start()
    assert decision_of(report).finding is DawReconcileFinding.AT_COMMANDED
    assert_not_verified(report, R.DAW_COMMAND_UNRESOLVED, RollbackStatus.UNCERTAIN)
    assert not h.sent("restore_gain")
    assert [c.command_id for c in h.fl_journal.control_state().unresolved] == [h.sent("set_gain_db")[0]["id"]]


def test_an_unverified_identity_restores_and_resolves_nothing(harness):
    h = harness(decisions=(propose(0.0), no_change))

    class LoseAndRename(LoseApplyReply):
        def send(self, raw):
            super().send(raw)
            if protocol.decode(raw)["type"] == "set_gain_db":
                self.world.host.mixer.getTrackName = lambda index: "Renamed"

    h.world.controller.transport_factory = lambda: LoseAndRename(h.world)
    report = h.start()
    assert decision_of(report).finding is DawReconcileFinding.IDENTITY_UNVERIFIED
    assert_not_verified(report, R.DAW_COMMAND_UNRESOLVED, RollbackStatus.UNCERTAIN)
    assert not h.sent("restore_gain")


def test_an_invalid_write_reply_is_recovered_from_the_journal_and_reported_as_invalid(harness, monkeypatch):
    h = harness(decisions=(propose(0.0), no_change))

    def refuse(request, outcome):
        raise ValueError("labelled invalid reply")

    monkeypatch.setattr(daw_execution, "validate_daw_apply_outcome", refuse)
    report = h.start()
    assert_restored(report, R.INVALID_RENDER_RESPONSE)
    assert report.final_run.termination_explanation.startswith("DAW write:")
    assert decision_of(report).entry is DawRecoveryEntry.CONFIRMED_WRITE  # the journal holds a confirmed write


# --- 7. the "Rollback evidence" regression ----------------------------------------------------------------------


class LateAfter(Clock):
    """A clock whose readings jump past the elapsed limit once ``late`` is set."""

    late = False

    def read(self):
        reading = super().read()
        if not self.late:
            return reading
        return ClockReading(reading.monotonic_seconds + 1000.0, reading.utc, False)


def offset_after_dropped_write(h):
    h.world.controller.transport_factory = lambda: DropApply(h.world, move_to=START + OFFSET)


def test_a_resolution_inside_tolerance_is_never_verified_rollback_when_time_runs_out(harness):
    h = harness(decisions=(propose(0.0), no_change))
    offset_after_dropped_write(h)
    clock = LateAfter()
    real = h.world.controller.resolve

    def resolve_then_late(request):
        outcome = real(request)
        clock.late = True
        return outcome

    recovery_with(h, resolve=resolve_then_late)
    report = run_with_clock(h, clock)
    assert decision_of(report).finding is DawReconcileFinding.AT_RECORDED_START
    assert_not_verified(report, R.RESTORATION_UNCERTAIN, RollbackStatus.UNCERTAIN)
    assert h.fl_journal.control_state().unresolved == () and not h.sent("restore_gain")


def run_with_clock(h, clock):
    from autonomous_mixing.core.loop.coordinator import run_daw

    values = h.collaborators(clock)
    return run_daw(h.request, **values)


def test_a_resolution_inside_tolerance_is_never_verified_rollback_when_v1_refuses_the_restoration(harness):
    h = harness(decisions=(propose(0.0), no_change))
    offset_after_dropped_write(h)
    h.world.device, h.world.device_protocol = load_device("v1")
    report = h.start()
    assert decision_of(report).finding is DawReconcileFinding.AT_RECORDED_START
    assert_not_verified(report, R.DAW_COMMAND_UNRESOLVED, RollbackStatus.UNCERTAIN)
    unresolved = [c.command_id for c in h.fl_journal.control_state().unresolved]
    assert unresolved == [recovery_ids(h.sent("set_gain_db")[0]["id"][len("apply-") :])["restore"]]


def test_the_control_case_restores_exactly_and_is_verified(harness):
    h = harness(decisions=(propose(0.0), no_change))
    offset_after_dropped_write(h)
    report = h.start()
    assert_restored(report, R.RENDER_FAILED)
    resolutions = [p.outcome for p in payloads(report, OperationObserved)
                   if type(p.outcome).__name__ == "DawResolutionRecorded"]  # fmt: skip
    assert [r.kind for r in resolutions] == [DawResolutionKind.NOT_APPLIED_OR_REVERTED]


# --- 8 and 9. inexact restorations are never verified -------------------------------------------------------------


def test_a_drifted_start_is_restored_to_the_journal_but_is_not_the_saved_target(harness):
    h = harness(decisions=(propose(0.0),))
    h.world.host.volumes[2] = START + OFFSET  # admitted inside the start tolerance, so the journaled start differs
    report = resumed(h, NO_IMPROVEMENT)
    assert_not_verified(report, R.RESTORATION_FAILED, RollbackStatus.FAILED)
    assert h.world.host.volumes[2] == START + OFFSET and h.sent("restore_gain")


def test_a_mismatched_restoration_is_reconciled_and_never_verified(harness):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    h.world.host.land_offset = 2e-10
    report = h.resume(suspended, h.operator_export(suspended, NO_IMPROVEMENT))
    assert_not_verified(report, R.RESTORATION_FAILED, RollbackStatus.FAILED)
    assert h.fl_journal.control_state().unresolved == ()


# --- 16. the fader moved after a confirmed restoration -----------------------------------------------------------


def test_a_fader_moved_after_the_confirmed_restoration_is_never_verified(harness):
    h = harness(decisions=(propose(0.0),))
    real = h.world.controller.recovery_snapshot

    def moved(root):
        h.world.host.volumes[2] = START - 0.01
        return real(root)

    recovery_with(h, recovery_snapshot=moved)
    report = resumed(h, NO_IMPROVEMENT)
    assert_not_verified(report, R.RESTORATION_FAILED, RollbackStatus.FAILED)
    assert rollback_of(report).fader_evidence == () and report.final_run.usage.recovery_operations == 1


def test_a_fader_moved_away_and_back_exactly_is_judged_by_the_fresh_reading(harness):
    h = harness(decisions=(propose(0.0),), budget=limits(max_recovery_operations=3))
    real = h.world.controller.recovery_snapshot

    def away_and_back(root):
        h.world.host.volumes[2] = START - 0.01
        h.world.host.volumes[2] = START
        return real(root)

    recovery_with(h, recovery_snapshot=away_and_back)
    report = resumed(h, SILENT)
    assert_restored(report, report.final_run.termination_reason)


# --- 10 and 11. exceptions and recording failures ------------------------------------------------------------------


def test_an_exception_from_the_restoration_call_keeps_the_command_unresolved(harness):
    h = harness(decisions=(propose(0.0),))

    def raise_(request):
        raise RuntimeError("labelled restoration failure")

    recovery_with(h, restore=raise_)
    suspended = suspend(h)
    with pytest.raises(RuntimeError, match="labelled restoration"):
        h.resume(suspended, h.operator_export(suspended, NO_IMPROVEMENT))
    snapshot = terminal(h)
    assert isinstance(snapshot, TerminalSnapshot) and snapshot.run.termination_reason is R.DAW_COMMAND_UNRESOLVED


class RaiseOn(Wrapped):
    def __init__(self, inner, predicate):
        super().__init__(inner)
        self.predicate = predicate

    def append(self, event):
        if self.predicate(event):
            raise RuntimeError("labelled recording exception")
        return self.inner.append(event)


def observed_kind(name):
    return lambda e: isinstance(e.payload, OperationObserved) and type(e.payload.outcome).__name__ == name


def test_an_exception_recording_a_confirmed_restoration_leaves_restoration_uncertain(harness):
    h = harness(decisions=(propose(0.0),), journal=lambda inner: RaiseOn(inner, observed_kind("DawRestoreConfirmed")))
    suspended = suspend(h)
    with pytest.raises(RuntimeError, match="labelled recording"):
        h.resume(suspended, h.operator_export(suspended, NO_IMPROVEMENT))
    assert terminal(h).run.termination_reason is R.RESTORATION_UNCERTAIN


def test_an_exception_from_resolve_keeps_the_root_unresolved(harness):
    h = harness(decisions=(propose(0.0), no_change))
    h.world.controller.transport_factory = lambda: LoseApplyReply(h.world)

    def raise_(request):
        raise RuntimeError("labelled resolution failure")

    recovery_with(h, resolve=raise_)
    with pytest.raises(RuntimeError, match="labelled resolution"):
        h.start()
    assert terminal(h).run.termination_reason is R.DAW_COMMAND_UNRESOLVED


def test_an_exception_recording_the_roots_resolution_leaves_restoration_uncertain(harness):
    h = harness(decisions=(propose(0.0), no_change), journal=lambda inner: RaiseOn(inner,
                                                                                   observed_kind("DawResolutionRecorded")))  # fmt: skip
    h.world.controller.transport_factory = lambda: DropApply(h.world)
    with pytest.raises(RuntimeError, match="labelled recording"):
        h.start()
    assert terminal(h).run.termination_reason is R.RESTORATION_UNCERTAIN


def test_an_exception_from_the_integrity_inspector_leaves_restoration_uncertain(harness):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    real = h.inspector.inspect
    calls = []

    def inspect(request):
        calls.append(request)
        raise RuntimeError("labelled integrity failure")

    h.inspector.inspect = inspect
    with pytest.raises(RuntimeError, match="labelled integrity"):
        h.resume(suspended, h.operator_export(suspended, NO_IMPROVEMENT))
    assert terminal(h).run.termination_reason is R.RESTORATION_UNCERTAIN and calls
    h.inspector.inspect = real


def test_an_unrecorded_restoration_intent_dispatches_and_charges_nothing(harness):
    def refuse(event):
        return isinstance(event.payload, DispatchIntent) and isinstance(event.payload.request, DawRestoreRequest)

    class Refuse(Wrapped):
        def append(self, event):
            if refuse(event):
                return TraceNotAppended(event.event_id, "labelled refusal")
            return self.inner.append(event)

    h = harness(decisions=(propose(0.0),), journal=Refuse)
    report = resumed(h, NO_IMPROVEMENT)
    assert report.final_run.termination_reason is R.REQUIRED_RECOVERY_NOT_PERFORMED
    assert not h.sent("restore_gain") and h.world.controller.control_state().value.recovery_charges == ()


def test_an_unrecorded_restoration_outcome_stops_the_recovery_without_a_claim(harness):
    class Refuse(Wrapped):
        def append(self, event):
            if observed_kind("DawRestoreConfirmed")(event):
                return TraceNotAppended(event.event_id, "labelled refusal")
            return self.inner.append(event)

    h = harness(decisions=(propose(0.0),), journal=Refuse)
    report = resumed(h, NO_IMPROVEMENT)
    assert report.final_run.termination_reason is R.RESTORATION_UNCERTAIN
    assert report.verified is None and report.final_run.usage.recovery_operations == 1  # no integrity inspection


# --- 12. IDs ---------------------------------------------------------------------------------------------------


def test_recovery_ids_are_stable_and_inside_the_id_rule(harness):
    ids = recovery_ids("id-7")
    assert ids["restore"] == "rst-id-7-1" and len(set(ids.values())) == len(ids)
    h = harness(decisions=(propose(0.0), no_change))
    report = resumed(h, NO_IMPROVEMENT)
    token = h.sent("set_gain_db")[0]["id"][len("apply-") :]
    assert h.sent("restore_gain")[0]["id"] == recovery_ids(token)["restore"]
    long_token = "t" * 57  # fits export-<token> but not rauth-<token>-1
    with pytest.raises(ValueError):
        recovery_ids(long_token)
    assert report.final_run.termination_reason is not None


def test_a_token_too_long_for_the_recovery_ids_is_refused_before_any_write(harness):
    h = harness(decisions=(propose(0.0),))
    counter = iter(range(10_000_000))
    h.identities.next_token = lambda: f"{'t' * 50}{next(counter):07d}"  # 57 characters: fits export-<token> only
    report = h.start()
    assert isinstance(report, RunReport) and not h.sent("set_gain_db")
    assert report.final_run.termination_reason is R.RUNTIME_CONTRACT_FAILED


# --- 13. established acceptance is never undone ---------------------------------------------------------------


def test_a_promotion_failure_after_a_committed_acceptance_never_enters_recovery(harness, monkeypatch):
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)

    def fail(*args, **kwargs):
        raise RuntimeError("labelled promotion failure")

    monkeypatch.setattr(daw_continuation, "promote_daw", fail)
    with pytest.raises(RuntimeError, match="labelled promotion"):
        h.resume(suspended, h.operator_export(suspended, ACCEPT))
    assert not h.sent("restore_gain") and not [e for e in trace(h) if isinstance(e.payload, DawRecoveryDecision)]
    snapshot = terminal(h)
    assert snapshot.authoritative is not None and snapshot.authoritative.sequence == 1


def test_recovery_refuses_to_run_against_an_established_new_reference(harness):
    h = harness(decisions=(propose(0.0),))
    record = suspend(h).checkpoint_event.payload
    require_recovery_authority(SimpleNamespace(authority_known=True, accepted=record.accepted), record.accepted)
    with pytest.raises(ValueError, match="authoritative"):
        require_recovery_authority(SimpleNamespace(authority_known=True, accepted=AcceptedReference(record.before, 1)),
                                   record.accepted)  # fmt: skip
    with pytest.raises(ValueError, match="authoritative"):
        require_recovery_authority(SimpleNamespace(authority_known=False, accepted=record.accepted), record.accepted)


def test_a_controller_level_replay_of_the_restoration_is_charged_exactly_once(harness):
    h = harness(decisions=(propose(0.0),))
    real = h.world.controller.restore
    outcomes = []

    def retried(request):  # a transport-level retry: the identical request again, replayed by the FL journal
        outcomes.append(real(request))
        outcomes.append(real(request))
        return outcomes[-1]

    recovery_with(h, restore=retried)
    report = resumed(h, SILENT)
    assert_restored(report, report.final_run.termination_reason)
    assert outcomes[1].replayed and len(h.sent("restore_gain")) == 1
    charges = h.world.controller.control_state().value.recovery_charges
    assert len(charges) == 1 and report.final_run.usage.recovery_operations == 2  # restoration + integrity


def test_a_journal_charge_that_disagrees_with_the_run_is_a_contract_failure(harness):
    h = harness(decisions=(propose(0.0),))
    real = h.world.controller.control_state
    calls = []

    def without_charges_after_the_restoration():
        calls.append(1)
        state = real()
        if h.sent("restore_gain"):
            return type(state)(replace(state.value, recovery_charges=()))
        return state

    recovery_with(h, control_state=without_charges_after_the_restoration)
    report = resumed(h, SILENT)
    assert report.final_run.termination_reason is R.RUNTIME_CONTRACT_FAILED
    assert report.verified is None and rollback_of(report).status is RollbackStatus.UNCERTAIN


class LoseRestoreReplyAndMove(DeviceTransport):
    def send(self, raw):
        restore = protocol.decode(raw)["type"] == "restore_gain"
        if restore:
            self.world.lose_next_reply = True
        super().send(raw)
        if restore:
            self.world.host.volumes[2] = 0.5  # moved to neither reference while the reply is lost


def test_an_unresolved_restoration_of_an_off_target_write_reports_the_unresolved_command_first(harness):
    """Precedence rows 1 and 2: both hold, and the unresolved command is reported (the block persists)."""
    h = harness(decisions=(propose(0.0), no_change))
    off_target(h)
    h.world.controller.transport_factory = lambda: LoseRestoreReplyAndMove(h.world)
    report = h.start()
    assert_not_verified(report, R.DAW_COMMAND_UNRESOLVED, RollbackStatus.UNCERTAIN)
    state = h.fl_journal.control_state()
    assert [c.kind.value for c in state.unresolved] == ["restore"]
    assert SESSION.profile.conversion in state.blocked_conversions


def test_an_integrity_exception_after_exact_observed_evidence_leaves_restoration_uncertain(harness):
    """A confirmed write whose fader was moved back exactly during the pause needs no restoration (``NoWrite``);
    the exact final reading still counts as a performed recovery step, so the marker is not "not performed"."""
    h = harness(decisions=(propose(0.0),))
    suspended = suspend(h)
    h.world.host.volumes[2] = START  # moved back exactly: a continuity rejection, then NoWrite at the restore gate

    def inspect(request):
        raise RuntimeError("labelled integrity failure")

    h.inspector.inspect = inspect
    with pytest.raises(RuntimeError, match="labelled integrity"):
        h.resume(suspended, h.operator_export(suspended, ACCEPT))
    assert terminal(h).run.termination_reason is R.RESTORATION_UNCERTAIN and not h.sent("restore_gain")


# --- review fixes (2026-11-20) -------------------------------------------------------------------------------------


def test_foreign_history_during_restoration_preparation_is_refused_atomically_without_dispatch_or_charge(harness):
    """Review fix 1: a foreign reservation after the lineage check must stop the restoration at its reservation."""
    h = harness(decisions=(propose(0.0),))
    real = h.world.controller.restore_target

    def target_then_foreign(root):
        found = real(root)
        h.world.apply("apply_foreign_02", change=-0.5)  # reserved (then refused on the wire): foreign history
        return found

    recovery_with(h, restore_target=target_then_foreign)
    report = resumed(h, NO_IMPROVEMENT)
    assert not h.sent("restore_gain")
    assert h.world.controller.control_state().value.recovery_charges == ()
    assert_not_verified(report, R.REQUIRED_RECOVERY_NOT_PERFORMED, RollbackStatus.UNCERTAIN)
    assert "withheld" in rollback_of(report).explanation
    assert report.final_run.usage.recovery_operations == 0 or not h.sent("restore_gain")


def test_an_exception_recording_a_restorations_resolution_reports_the_settled_facts(harness):
    """Review fix 2: off target, lost restoration reply, the restoration resolved, then its recording raises."""
    h = harness(decisions=(propose(0.0), no_change),
                journal=lambda inner: RaiseOn(inner, observed_kind("DawResolutionRecorded")))  # fmt: skip
    off_target(h)
    h.world.controller.transport_factory = lambda: LoseRestoreReply(h.world)
    with pytest.raises(RuntimeError, match="labelled recording"):
        h.start()
    state = h.fl_journal.control_state()
    assert state.unresolved == () and SESSION.profile.conversion in state.blocked_conversions
    assert terminal(h).run.termination_reason is R.DAW_CONVERSION_BLOCKED
