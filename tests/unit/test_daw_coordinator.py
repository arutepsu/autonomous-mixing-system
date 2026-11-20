"""Increment 2 of the coordinator-integration plan: the DAW write path up to suspension (``run_daw``).

Real FL runtime protocol, exchange, journal and controller against device script v2 on a fake host; the real
``FileExportHandoff`` on temporary directories; labelled controlled doubles for analysis, diagnosis, decisions and
integrity (from ``test_coordinator``). Orchestration evidence only: no live FL Studio, no audio, no export continuation.
"""

import os
from dataclasses import replace

import pytest
from daw_export_support import routing
from fl_recovery_support import journal_apply
from fl_runtime_support import make_request
from test_coordinator import (
    INITIAL_VALUE,
    Analyzer,
    Clock,
    Decider,
    Diagnosis,
    FaultyJournal,
    Identities,
    Inspector,
    no_change,
    propose,
)
from test_fl_runtime_recovery_controller import DeviceTransport, World, live_curve
from test_run_recording_contracts import (
    P1,
    RATE,
    RENDER_REF,
    REQUIRED,
    SECONDS,
    STATE,
    T1,
    _configuration,
    _run_request,
)

import autonomous_mixing.core.loop.daw_execution as daw_execution
from autonomous_mixing.adapters.audio_io.limits import AudioReadLimits
from autonomous_mixing.adapters.audio_io.locations import resolve_artifact_location
from autonomous_mixing.adapters.fl_studio import protocol
from autonomous_mixing.adapters.fl_studio.journal import SqliteCommandJournal
from autonomous_mixing.adapters.journal.run_memory import InMemoryRunJournal
from autonomous_mixing.adapters.rendering.export_handoff import FileExportHandoff
from autonomous_mixing.adapters.rendering.workspaces import WorkspaceRoot
from autonomous_mixing.core.loop.coordinator import run, run_daw
from autonomous_mixing.core.records.acceptance import AcceptanceCommitted, AcceptanceEvent, AcceptedReference
from autonomous_mixing.core.records.actions import ExecutionReceipt, ExecutionStatus, RollbackReceipt, RollbackStatus
from autonomous_mixing.core.records.admission import AdmissionRejectionCode, RejectedAction, TrackGainPermission
from autonomous_mixing.core.records.artifacts import ArtifactKind
from autonomous_mixing.core.records.budgets import BudgetLimits
from autonomous_mixing.core.records.daw_control import DawApplyRejected, DawGateCode, DawNoWrite
from autonomous_mixing.core.records.daw_correction import DawCorrectionSuspended, DawPlanReady, DawSessionConfig
from autonomous_mixing.core.records.daw_export import DawExportFormat, DawExportTimeline
from autonomous_mixing.core.records.daw_rollback import DawRollbackRecord
from autonomous_mixing.core.records.feedback import ActionFeedback, ActionFeedbackDisposition, DecisionFeedback
from autonomous_mixing.core.records.identifiers import Revision
from autonomous_mixing.core.records.project import DawParameterState, DawTrackParameter
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode
from autonomous_mixing.core.records.run import FinalProjectState
from autonomous_mixing.core.records.run_report import RunReport, RunSuspended
from autonomous_mixing.core.records.run_trace import DispatchIntent, OperationKind, OperationObserved, TerminalSnapshot
from autonomous_mixing.core.records.termination import TerminationReason as R

APPLY = make_request()
BINDING, PROFILE = APPLY.resolution.state.binding, APPLY.profile
DAW_STATE = replace(STATE, daw=DawParameterState(P1, Revision("r1"), (DawTrackParameter(BINDING, 2.0,
                                                                                         BINDING.baseline_fader),)))  # fmt: skip
SESSION = DawSessionConfig(
    BINDING,
    PROFILE,
    TrackGainPermission(T1, -6.0, 0.0),
    routing(dependents=()),
    DawExportFormat("WAV", "PCM_24"),
    DawExportTimeline(0.0, RATE, RATE * SECONDS),
    RENDER_REF,
    "daw-exports",
)  # fmt: skip  (Increment 3: the baselines' own render spec)
OK_CUT = 0.0  # application gain 0.0 dB: a 2 dB cut from the confirmed 2.0 dB, inside the calibrated window


def limits(**overrides):
    fields = dict(max_decision_calls=4, max_attempts=3, max_render_calls=3, max_recovery_operations=3,
                  max_elapsed_seconds=60.0, max_daw_writes=2)  # fmt: skip
    fields.update(overrides)
    return BudgetLimits(**fields)


class LoseApplyReply(DeviceTransport):
    """The device executes the write, but its reply never arrives (only for ``set_gain_db``)."""

    def send(self, raw):
        if protocol.decode(raw)["type"] == "set_gain_db":
            self.world.lose_next_reply = True
        super().send(raw)


class DawHarness:
    def __init__(self, tmp_path, *, decisions=(propose(OK_CUT),), budget=None, journal=None, recovery=None,
                 exports_exist=True):  # fmt: skip
        self.fl_journal = SqliteCommandJournal(tmp_path / "fl.db", session_key="session-1")
        self.world = World(self.fl_journal)
        inputs = tmp_path / "inputs"
        for artifact in REQUIRED:
            path = inputs / artifact.location
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"original " + artifact.artifact_id.value.encode())
        self.exports = tmp_path / "exports"
        if exports_exist:
            self.exports.mkdir()
        self.handoff = FileExportHandoff(
            root=WorkspaceRoot("daw-exports", str(self.exports)), state=DAW_STATE,
            protected_locations=tuple(resolve_artifact_location(a, root=str(inputs)) for a in REQUIRED),
            limits=AudioReadLimits(1 << 20, 1 << 20),
        )  # fmt: skip
        self.request = _run_request(initial_state=DAW_STATE, daw_session=SESSION,
                                    configuration=_configuration(budget_limits=budget or limits()))  # fmt: skip
        self.analyzer, self.decider = Analyzer((INITIAL_VALUE,)), Decider(*decisions)
        self.real_journal = InMemoryRunJournal(self.request.run_id, AcceptedReference(DAW_STATE, 0))
        self.journal = journal(self.real_journal) if journal else self.real_journal
        self.recovery = recovery
        self.identities = Identities()

    def run(self):
        controller = self.world.controller
        return run_daw(
            self.request, analyzer=self.analyzer, diagnosis_source=Diagnosis(), decision_maker=self.decider,
            integrity_inspector=Inspector(), journal=self.journal, clock=Clock(), identities=self.identities,
            daw_controller=controller, recovery_controller=self.recovery or controller, export_handoff=self.handoff,
        )  # fmt: skip

    def sent(self, kind):
        return [message for message in self.world.sent if message["type"] == kind]


def events(result):
    return result.trace.events


def payloads(result, kind):
    return [e.payload for e in events(result) if isinstance(e.payload, kind)]


def operations(result):
    return [p.operation for p in payloads(result, DispatchIntent)]


def action_feedback(result):
    return [f.outcome for f in payloads(result, DecisionFeedback) if isinstance(f.outcome, ActionFeedback)]


@pytest.fixture
def harness(tmp_path):
    instances = []

    def make(**kwargs):
        instances.append(DawHarness(tmp_path, **kwargs))
        return instances[-1]

    yield make
    for instance in instances:
        instance.fl_journal.close()


# --- a confirmed write suspends; nothing is candidate-ready ---------------------------------------------------------


def test_a_confirmed_write_prepares_the_export_and_suspends_without_any_candidate(harness):
    h = harness()
    result = h.run()
    assert isinstance(result, RunSuspended)
    checkpoint = result.checkpoint_event.payload
    assert isinstance(checkpoint, DawCorrectionSuspended)
    assert checkpoint.write.after == checkpoint.awaiting.request.anchor.reading
    assert checkpoint.awaiting.request.anchor.apply == checkpoint.apply
    assert checkpoint.apply.admitted.action == checkpoint.decision.outcome.action
    assert os.listdir(checkpoint.awaiting.workspace_directory) == ["export-request.json"]
    # no candidate receipt, analysis after the write, acceptance or render
    assert not payloads(result, ExecutionReceipt) and not payloads(result, AcceptanceEvent)
    assert not payloads(result, AcceptanceCommitted) and not result.trace.acceptances
    ops = operations(result)
    write = ops.index(OperationKind.DAW_APPLY)
    assert OperationKind.RENDER not in ops and OperationKind.ANALYZE not in ops[write:]
    assert ops[write - 1:] == [OperationKind.DAW_INSPECT, OperationKind.DAW_APPLY, OperationKind.DAW_STATE,
                               OperationKind.EXPORT_PREPARE]  # fmt: skip
    assert result.trace.events[-1] == result.checkpoint_event
    assert result.checkpoint_event.usage.daw_writes == 1 and len(h.sent("set_gain_db")) == 1
    assert h.world.host.volumes[2] != 0.76  # the fader moved; the selected state is unchanged


def test_the_apply_uses_one_stable_command_id_from_plan_to_journal(harness):
    h = harness()
    result = h.run()
    checkpoint = result.checkpoint_event.payload
    plan = payloads(result, DawPlanReady)[0].plan
    intent = next(p for p in payloads(result, DispatchIntent) if p.operation is OperationKind.DAW_APPLY)
    assert plan.command_id == intent.request.command_id == checkpoint.apply.command_id == h.sent("set_gain_db")[0]["id"]
    assert plan.command_id.startswith("apply-") and h.fl_journal.lookup(plan.command_id) is not None


def test_the_capability_view_is_the_calibrated_window_of_the_confirmed_state(harness):
    # The confirmed 2.0 dB allows cuts of 0.1-3 dB, i.e. [-1.0, 1.9] dB; with the policy [-6, 0] dB the advertised
    # range is [-1.0, 0.0] dB, so 0.5 dB is outside it and is refused at admission, before any DAW call.
    h = harness(decisions=(propose(0.5), no_change))
    result = h.run()
    bounds = h.decider.situations[0].capabilities.gain_actions[0].bounds
    assert (bounds.minimum_db, bounds.maximum_db) == pytest.approx((-1.0, 0.0))
    rejected = payloads(result, RejectedAction)
    assert rejected and rejected[0].code is AdmissionRejectionCode.OUT_OF_BOUNDS
    assert OperationKind.DAW_INSPECT not in operations(result) and not h.sent("set_gain_db")


# --- nothing written: the loop continues ------------------------------------------------------------------------


def test_a_pre_write_refusal_is_a_failed_attempt_and_the_loop_continues(harness):
    h = harness(decisions=(propose(OK_CUT), no_change))
    h.world.host.playing = True  # the device refuses before writing
    result = h.run()
    assert isinstance(result, RunReport) and result.final_run.termination_reason is R.NO_CHANGE_PROPOSED
    assert result.final_run.final_project_state is FinalProjectState.UNCHANGED
    feedback = action_feedback(result)[0]
    assert (feedback.disposition, feedback.execution_status) == (ActionFeedbackDisposition.FAILED,
                                                                 ExecutionStatus.NOT_STARTED)  # fmt: skip
    assert OperationKind.EXPORT_PREPARE not in operations(result) and h.world.host.volumes[2] == 0.76


def test_an_unresolved_command_before_the_run_refuses_its_start(harness):
    """Increment 5 (G14, ADR-039): unexplained FL state refuses the start before any decision or write."""
    h = harness(decisions=(propose(OK_CUT), no_change))
    journal_apply(h.fl_journal, "apply_pending_1", pending=True)  # an unresolved earlier command
    result = h.run()
    assert result.final_run.termination_reason is R.DAW_COMMAND_UNRESOLVED
    assert "apply_pending_1" in result.final_run.termination_explanation
    assert not h.decider.situations and not h.sent("set_gain_db")
    assert [c.command_id for c in h.fl_journal.control_state().unresolved] == ["apply_pending_1"]  # preserved


def test_a_gate_rejection_writes_nothing_and_the_loop_continues(harness):
    def decide_after_an_orphan_appears(situation, number):  # an unresolved command appears during the run
        journal_apply(h.fl_journal, "apply_pending_1", pending=True)
        return propose(OK_CUT)(situation, number)

    h = harness(decisions=(decide_after_an_orphan_appears, no_change))
    result = h.run()
    assert result.final_run.termination_reason is R.NO_CHANGE_PROPOSED
    rejected = payloads(result, DawApplyRejected)
    assert rejected and rejected[0].code is DawGateCode.UNRESOLVED_OPERATION
    assert action_feedback(result)[0].disposition is ActionFeedbackDisposition.REJECTED
    assert OperationKind.DAW_APPLY not in operations(result) and not h.sent("set_gain_db")
    assert [c.command_id for c in h.fl_journal.control_state().unresolved] == ["apply_pending_1"]  # preserved


def test_a_no_write_gate_result_dispatches_nothing(harness, monkeypatch):
    # The calibrated capability view never advertises a zero cut, so the gate's DawNoWrite branch is driven by a
    # controlled double of the gate (labelled): it resolves the action to exactly the confirmed fader.
    monkeypatch.setattr(daw_execution, "gate_daw_apply", lambda admitted, **kwargs: _no_write_for(admitted, kwargs))
    h = harness(decisions=(propose(OK_CUT), no_change))
    result = h.run()
    assert payloads(result, DawNoWrite)
    assert action_feedback(result)[0].disposition is ActionFeedbackDisposition.NOT_EXECUTED
    assert OperationKind.DAW_APPLY not in operations(result) and not h.sent("set_gain_db")
    assert result.final_run.final_project_state is FinalProjectState.UNCHANGED


def _no_write_for(admitted, kwargs):
    """A DawNoWrite for exactly the confirmed state: the action resolves to the confirmed fader (0 dB change)."""
    from autonomous_mixing.core.admission.daw import resolve_daw_gain

    state = kwargs["state"]
    action = replace(admitted.action, operation=replace(admitted.action.operation, value_db=state.selected_gain_db))
    resolution = resolve_daw_gain(action, state=state, observed_fader=state.confirmed_fader, normalized_tolerance=0.0,
                                  db_tolerance=0.0)  # fmt: skip
    return DawNoWrite(resolution)


def test_an_exhausted_daw_write_budget_blocks_before_any_dispatch(harness):
    h = harness(budget=limits(max_daw_writes=0))
    result = h.run()
    assert result.final_run.termination_reason is R.DAW_WRITE_LIMIT
    assert result.final_run.final_project_state is FinalProjectState.UNCHANGED and not h.sent("set_gain_db")


# --- written or possibly written: terminate safely, claim nothing (interim, before Increment 4) -------------------


def assert_recovered(result, reason):
    """Increment 4 (ADR-038) supersedes the interim stops: the write is restored exactly, rollback VERIFIED."""
    assert isinstance(result, RunReport) and result.final_run.termination_reason is reason
    assert result.final_run.final_project_state is FinalProjectState.RESTORED_TO_LAST_ACCEPTED
    assert result.verified == result.authoritative and not payloads(result, ExecutionReceipt)
    rollback = payloads(result, DawRollbackRecord)[-1].receipt
    assert rollback.status is RollbackStatus.VERIFIED and rollback.fader_evidence


def assert_safe_unresolved_termination(result, reason):
    assert isinstance(result, RunReport) and result.final_run.termination_reason is reason
    assert result.final_run.final_project_state is FinalProjectState.UNKNOWN and result.verified is None
    assert not payloads(result, RollbackReceipt) and not payloads(result, ExecutionReceipt)
    feedback = action_feedback(result)[-1]
    assert (feedback.disposition, feedback.execution_status, feedback.rollback_status) == (
        ActionFeedbackDisposition.UNRESOLVED, ExecutionStatus.UNCERTAIN, None)  # fmt: skip


def test_an_off_target_write_is_restored_and_terminates_with_the_conversion_blocked(harness):
    h = harness(decisions=(propose(OK_CUT), no_change))
    h.world.host.curve = lambda v: live_curve(v) + (0.0 if v == 0.76 else 0.2)  # lands, but misses the dB change
    result = h.run()
    assert_recovered(result, R.DAW_CONVERSION_BLOCKED)  # superseded the interim stop (Increment 4, ADR-038)
    assert PROFILE.conversion in h.fl_journal.control_state().blocked_conversions  # the block is never cleared
    assert len(h.sent("restore_gain")) == 1 and len(h.decider.situations) == 1  # no further action


def test_an_uncertain_write_is_reconciled_restored_and_resolved(harness):
    h = harness(decisions=(propose(OK_CUT), no_change))
    h.world.controller.transport_factory = lambda: LoseApplyReply(h.world)
    result = h.run()
    assert_recovered(result, R.RENDER_FAILED)  # D4: the render-stage reason, documented for the DAW write
    assert result.final_run.termination_explanation.startswith("DAW write:")
    assert h.fl_journal.control_state().unresolved == () and len(h.decider.situations) == 1


def test_a_failed_export_preparation_after_a_confirmed_write_is_restored(harness):
    h = harness(decisions=(propose(OK_CUT), no_change), exports_exist=False)
    result = h.run()
    assert_recovered(result, R.EXECUTION_PREPARATION_FAILED)
    assert "prepar" in result.final_run.termination_explanation
    assert not payloads(result, DawCorrectionSuspended) and len(h.sent("restore_gain")) == 1


class FailingAnchor:
    """Delegates to the real recovery controller, except that the anchor query fails (controlled double)."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def state_anchor(self, source_id):
        return PortFailure(PortFailureCode.UNAVAILABLE, "labelled anchor failure", False)


def test_a_failed_anchor_after_a_confirmed_write_is_restored(harness, tmp_path):
    h = harness(decisions=(propose(OK_CUT), no_change))
    h.recovery = FailingAnchor(h.world.controller)  # the recovery lineage, not the export anchor, serves recovery
    result = h.run()
    assert_recovered(result, R.EXECUTION_PREPARATION_FAILED)
    assert OperationKind.EXPORT_PREPARE not in operations(result)


class FailingLineage(FailingAnchor):
    """Also refuses the recovery lineage, so no recovery can be performed (controlled double)."""

    def recovery_lineage(self, root_command_id):
        return PortFailure(PortFailureCode.UNAVAILABLE, "labelled lineage failure", False)


def test_an_unresolved_reason_survives_a_lost_terminal_append(harness):
    def refuse_terminal(event):
        return "refuse" if isinstance(event.payload, TerminalSnapshot) else None

    h = harness(decisions=(propose(OK_CUT), no_change), journal=lambda inner: FaultyJournal(inner,
                                                                                           append_fault=refuse_terminal))  # fmt: skip
    h.world.controller.transport_factory = lambda: LoseApplyReply(h.world)
    h.recovery = FailingLineage(h.world.controller)
    result = h.run()
    assert result.final_run.termination_reason is R.DAW_COMMAND_UNRESOLVED
    assert not h.sent("restore_gain")


# --- the offline path is unchanged ------------------------------------------------------------------------------


def test_the_offline_and_daw_entry_points_refuse_each_others_requests(harness):
    h = harness()
    with pytest.raises(ValueError, match="DAW session"):
        run(h.request, analyzer=h.analyzer, diagnosis_source=Diagnosis(), decision_maker=h.decider,
            renderer=object(), integrity_inspector=Inspector(), journal=h.journal, clock=Clock(),
            identities=Identities())  # fmt: skip
    offline = _run_request()
    with pytest.raises(ValueError, match="DAW session"):
        run_daw(offline, analyzer=h.analyzer, diagnosis_source=Diagnosis(), decision_maker=h.decider,
                integrity_inspector=Inspector(), journal=InMemoryRunJournal(offline.run_id,
                                                                            AcceptedReference(offline.initial_state, 0)),
                clock=Clock(), identities=Identities(), daw_controller=h.world.controller,
                recovery_controller=h.world.controller, export_handoff=h.handoff)  # fmt: skip


class MovedAnchor(FailingAnchor):
    """Returns the real anchor with another reading (controlled double): a journal answer that disagrees."""

    def state_anchor(self, source_id):
        from autonomous_mixing.core.records.daw_binding import DawFaderReading
        from autonomous_mixing.core.records.results import Success

        real = self.inner.state_anchor(source_id).value
        moved = DawFaderReading(real.reading.normalized, real.reading.db - 0.5)
        return Success(replace(real, reading=moved))


def test_an_anchor_that_disagrees_with_the_confirmed_write_is_restored(harness):
    h = harness(decisions=(propose(OK_CUT), no_change))
    h.recovery = MovedAnchor(h.world.controller)
    result = h.run()
    assert_recovered(result, R.EXECUTION_PREPARATION_FAILED)
    assert "anchor" in result.final_run.termination_explanation
    assert OperationKind.EXPORT_PREPARE not in operations(result)  # refused before the operator is asked to export


def test_the_daw_preflight_refuses_a_group_of_the_target_before_any_daw_call(harness):
    from test_run_recording_contracts import _artifact, _roots

    from autonomous_mixing.core.records.admission import PreflightCode, PreflightRejected
    from autonomous_mixing.core.records.project import ArtifactCheckpoint

    group = _artifact("grp", "r1", ("t1", "t2"), ArtifactKind.GROUP)
    audio = DAW_STATE.audio
    grouped = replace(DAW_STATE, audio=ArtifactCheckpoint(audio.checkpoint_id, P1, audio.revision,
                                                          (*audio.artifacts, group)))  # fmt: skip
    h = harness(decisions=(propose(OK_CUT), no_change))
    h.request = replace(h.request, initial_state=grouped, root_bindings=_roots((*REQUIRED, group)))
    h.real_journal = h.journal = InMemoryRunJournal(h.request.run_id, AcceptedReference(grouped, 0))
    result = h.run()
    rejected = payloads(result, PreflightRejected)
    assert rejected and rejected[0].code is PreflightCode.UNSUPPORTED_DEPENDENCY
    assert OperationKind.DAW_INSPECT not in operations(result) and not h.sent("set_gain_db")


# --- unexpected exceptions keep the phase's classification (review fix 2026-11-19) -------------------------------


class RaisingAfterApply:
    """Delegates to the real controller; its apply dispatches the write, then raises (controlled double)."""

    def __init__(self, inner, error):
        self.inner, self.error = inner, error

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def apply(self, request):
        self.inner.apply(request)
        raise self.error


class RaisingPrepare:
    def __init__(self, inner, error):
        self.inner, self.error = inner, error

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def prepare(self, request):
        raise self.error


def terminal(h):
    snapshot = h.real_journal.snapshot().value
    return snapshot.events[-1].payload


def run_raising(h, error, *, controller=None, handoff=None):
    controller = controller or h.world.controller
    with pytest.raises(type(error)) as raised:
        run_daw(
            h.request, analyzer=h.analyzer, diagnosis_source=Diagnosis(), decision_maker=h.decider,
            integrity_inspector=Inspector(), journal=h.journal, clock=Clock(), identities=h.identities,
            daw_controller=controller, recovery_controller=h.world.controller,
            export_handoff=handoff or h.handoff,
        )  # fmt: skip
    return raised.value


def test_an_exception_after_the_apply_may_have_dispatched_keeps_the_command_unresolved(harness):
    h = harness()
    error = RuntimeError("labelled failure after dispatch")
    assert run_raising(h, error, controller=RaisingAfterApply(h.world.controller, error)) is error  # the original
    snapshot = terminal(h)
    assert isinstance(snapshot, TerminalSnapshot)
    assert snapshot.run.termination_reason is R.DAW_COMMAND_UNRESOLVED
    assert snapshot.run.final_project_state is FinalProjectState.UNKNOWN and snapshot.verified is None
    assert "RuntimeError" in snapshot.run.termination_explanation
    assert len(h.sent("set_gain_db")) == 1 and not h.sent("restore_gain")


def test_an_exception_during_preparation_after_a_confirmed_write_requires_recovery(harness):
    h = harness()
    error = OSError("labelled disk failure")
    assert run_raising(h, error, handoff=RaisingPrepare(h.handoff, error)) is error  # the original exception
    snapshot = terminal(h)
    assert snapshot.run.termination_reason is R.REQUIRED_RECOVERY_NOT_PERFORMED
    assert snapshot.run.final_project_state is FinalProjectState.UNKNOWN and snapshot.verified is None
    assert "OSError" in snapshot.run.termination_explanation
    assert not h.sent("restore_gain") and h.fl_journal.control_state().unresolved == ()  # nothing cleared or added


def test_an_exception_before_any_dispatch_stays_a_runtime_contract_failure(harness):
    h = harness()

    class RaisingInspect(RaisingPrepare):
        def inspect(self, request):
            raise self.error

    error = RuntimeError("labelled failure before the write")
    run_raising(h, error, controller=RaisingInspect(h.world.controller, error))
    snapshot = terminal(h)
    assert snapshot.run.termination_reason is R.RUNTIME_CONTRACT_FAILED and not h.sent("set_gain_db")
    assert snapshot.run.final_project_state is FinalProjectState.UNCHANGED  # nothing was written


def test_a_refused_write_clears_the_pending_effect_so_a_later_error_is_a_contract_failure(harness):
    h = harness(decisions=(propose(OK_CUT), propose(OK_CUT)))
    h.world.host.playing = True  # the first write is refused before writing

    class RaisingSecondInspect(RaisingPrepare):
        calls = 0

        def inspect(self, request):
            RaisingSecondInspect.calls += 1
            if RaisingSecondInspect.calls == 2:
                raise self.error
            return self.inner.inspect(request)

    error = RuntimeError("labelled failure in the second round")
    run_raising(h, error, controller=RaisingSecondInspect(h.world.controller, error))
    snapshot = terminal(h)
    assert snapshot.run.termination_reason is R.RUNTIME_CONTRACT_FAILED
    assert snapshot.run.final_project_state is FinalProjectState.UNCHANGED  # the refusal wrote nothing


class RaisingOnApplyObservation:
    """Forwards to the real journal, but appending the DAW apply observation raises (controlled double)."""

    def __init__(self, inner):
        self.inner = inner

    def append(self, event):
        payload = event.payload
        if isinstance(payload, OperationObserved) and payload.operation is OperationKind.DAW_APPLY:
            raise RuntimeError("labelled failure while recording the apply observation")
        return self.inner.append(event)

    def commit(self, event):
        return self.inner.commit(event)

    def query(self, request):
        return self.inner.query(request)

    @property
    def store_durability(self):
        return self.inner.store_durability

    def snapshot(self):
        return self.inner.snapshot()


def test_an_exception_recording_a_confirmed_reply_requires_recovery(harness):
    h = harness(journal=RaisingOnApplyObservation)
    run_raising(h, RuntimeError("x"))
    snapshot = terminal(h)
    assert snapshot.run.termination_reason is R.REQUIRED_RECOVERY_NOT_PERFORMED
    assert snapshot.run.final_project_state is FinalProjectState.UNKNOWN and not h.sent("restore_gain")


def test_an_exception_recording_an_off_target_reply_keeps_the_conversion_blocked(harness):
    h = harness(journal=RaisingOnApplyObservation)
    h.world.host.curve = lambda v: live_curve(v) + (0.0 if v == 0.76 else 0.2)
    run_raising(h, RuntimeError("x"))
    snapshot = terminal(h)
    assert snapshot.run.termination_reason is R.DAW_CONVERSION_BLOCKED
    assert snapshot.run.final_project_state is FinalProjectState.UNKNOWN
    assert PROFILE.conversion in h.fl_journal.control_state().blocked_conversions


def test_an_exception_recording_a_refused_reply_is_a_contract_failure_with_nothing_written(harness):
    h = harness(journal=RaisingOnApplyObservation)
    h.world.host.playing = True
    run_raising(h, RuntimeError("x"))
    snapshot = terminal(h)
    assert snapshot.run.termination_reason is R.RUNTIME_CONTRACT_FAILED
    assert snapshot.run.final_project_state is FinalProjectState.UNCHANGED  # the verified reference is re-established
