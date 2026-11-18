"""The runtime controller's recovery and reconciliation methods, end to end through device script v2.

Bytes go through the real runtime protocol, exchange, journal and controller to device script v2 running on a fake
host (tests/unit/fl_device_support.py). This demonstrates the implementation's contract; it is not live FL evidence.
"""

from dataclasses import replace
from itertools import count

import pytest
from fl_device_support import FakeHost, h_db, load_device
from fl_recovery_support import CONTROLLER, grant
from fl_runtime_support import make_request

from autonomous_mixing.adapters.fl_studio import protocol
from autonomous_mixing.adapters.fl_studio.controller import FlStudioParameterController
from autonomous_mixing.adapters.fl_studio.journal import JournalError, SqliteCommandJournal
from autonomous_mixing.core.admission.daw import gate_daw_restore
from autonomous_mixing.core.ports.daw_recovery import validate_daw_reconciliation
from autonomous_mixing.core.records.daw_control import (
    DawInspectRequest,
    DawRefusalCode,
    DawUncertainReason,
    DawWriteConfirmed,
    DawWriteRefused,
    DawWriteUncertain,
)
from autonomous_mixing.core.records.daw_recovery import (
    DawReconcileFinding,
    DawReconcileRequest,
    DawRecoveryAuthorization,
    DawResolutionAuthorization,
    DawResolutionKind,
    DawResolutionRecorded,
    DawResolutionRefusalCode,
    DawResolutionRefused,
    DawResolutionRequest,
    DawRestoreConfirmed,
    DawRestoreMismatch,
    DawRestorePermitted,
    DawRestoreRefused,
    DawRestoreUncertain,
    DawTargetBasis,
)
from autonomous_mixing.core.records.identifiers import ExecutionId, VersionRef
from autonomous_mixing.core.records.results import PortFailure, Success

ROOT = "apply_root_0001"
LIVE_DB = -1.1527416706085205  # the live calibration start; make_request's inspected reading at normalized 0.76


def live_curve(v):
    """The calibrated curve, except that 0.76 reads exactly the live start (as the real fader did)."""
    return LIVE_DB if v == 0.76 else h_db(v)


class DeviceTransport:
    def __init__(self, world):
        self.world, self.reply = world, None

    def send(self, raw):
        world = self.world
        message = protocol.decode(raw)
        world.sent.append(message)
        if message["type"] in protocol.WRITE_TYPES:
            assert world.journal.lookup(message["id"]).outcome is None  # intent committed before the send
        answer = world.device.handle(world.device_protocol.encode(message), world.host, world.history)
        self.reply = None if world.lose_next_reply else answer
        world.lose_next_reply = False

    def receive(self, remaining):
        if self.reply is None:
            self.world.now += remaining
            return None
        reply, self.reply = self.reply, None
        self.world.now += 0.001
        return reply

    def close(self):
        pass


class World:
    def __init__(self, journal, host=None, device="v2"):
        self.journal, self.host = journal, host or FakeHost(normalized=0.76, curve=live_curve)
        self.device, self.device_protocol = load_device(device)
        self.history, self.sent, self.now, self.lose_next_reply = {}, [], 0.0, False
        ids = count(1)
        self.controller = FlStudioParameterController(
            profile=make_request().profile, journal=journal, transport_factory=lambda: DeviceTransport(self),
            timeout_s=1.0, clock=lambda: self.now, next_inspect_id=lambda: f"inspect_{next(ids):08d}",
        )  # fmt: skip

    def inspect(self):
        request = make_request(ROOT)
        result = self.controller.inspect(DawInspectRequest(request.resolution.state.binding, request.profile))
        assert isinstance(result, Success), result
        return result.value

    def apply(self, command_id=ROOT, change=-1.0):
        """A gate-built cut from the live start (0.76); the host's fader must be there for the device to accept it."""
        return self.controller.apply(make_request(command_id, change))

    def restore_request(self, recovery_id, authorization_id, root=ROOT):
        target = self.controller.restore_target(root)
        assert isinstance(target, Success), target
        authorization = DawRecoveryAuthorization(authorization_id, ExecutionId("e1"), CONTROLLER, target.value, grant())
        inspection = self.inspect()
        state = self.controller.control_state().value
        result = gate_daw_restore(authorization, binding=inspection.request.binding, inspection=inspection,
                                  profile=inspection.request.profile, recovery_command_id=recovery_id,
                                  control_state=state)  # fmt: skip
        assert isinstance(result, DawRestorePermitted), result
        return result.request

    def restore(self, recovery_id="restore_cmd_0001", authorization_id="recovery_auth_0001", root=ROOT):
        request = self.restore_request(recovery_id, authorization_id, root)
        return request, self.controller.restore(request)

    def reconcile(self, reconciliation_id, subject):
        request = make_request(ROOT)
        reconcile = DawReconcileRequest(reconciliation_id, subject, request.resolution.state.binding, request.profile)
        result = self.controller.reconcile(reconcile)
        validate_daw_reconciliation(reconcile, result)
        return result

    def resolve(self, resolution_id, subject, kind, evidence, authorization="resolve_auth_0001"):
        return self.controller.resolve(
            DawResolutionRequest(
                resolution_id, subject, kind, evidence, DawResolutionAuthorization(authorization, CONTROLLER)
            )
        )

    def unresolved(self):
        return [item.command_id for item in self.controller.control_state().value.unresolved]


@pytest.fixture
def journal(tmp_path):
    instance = SqliteCommandJournal(tmp_path / "journal.db", session_key="session-1")
    yield instance
    instance.close()


def sends(world, kind):
    return [message for message in world.sent if message["type"] == kind]


# --- recovery paths ---------------------------------------------------------------------------------------------------


def test_rollback_of_a_confirmed_apply_restores_upward_exactly(journal):
    world = World(journal)
    applied = world.apply()
    assert isinstance(applied, DawWriteConfirmed)
    target = world.controller.restore_target(ROOT).value
    assert target.basis is DawTargetBasis.OUTCOME_BEFORE and target.reading == applied.before
    request, outcome = world.restore()
    assert isinstance(outcome, DawRestoreConfirmed) and outcome.after == applied.before
    assert world.host.volumes[2] == 0.76 and live_curve(0.76) == applied.before.db
    assert world.unresolved() == [] and len(world.controller.control_state().value.recovery_charges) == 1
    assert isinstance(world.apply("apply_second_01"), DawWriteConfirmed)  # actions continue


def test_an_unresolved_apply_is_recovered_by_restoration_then_explicit_resolution(journal):
    world = World(journal)
    world.lose_next_reply = True  # the device writes, but its reply is lost
    lost = world.apply()
    assert isinstance(lost, DawWriteUncertain) and lost.reason is DawUncertainReason.TIMEOUT
    assert world.unresolved() == [ROOT]
    assert isinstance(world.apply("apply_second_01"), DawWriteRefused)  # blocked
    _, restored = world.restore()
    assert isinstance(restored, DawRestoreConfirmed)
    assert world.unresolved() == [ROOT]  # a confirmed restoration does not resolve its root by itself
    recorded = world.resolve("resolve_0001", ROOT, DawResolutionKind.RESTORED_TO_RECORDED_START, "restore_cmd_0001")
    assert isinstance(recorded, DawResolutionRecorded) and recorded.resolved_reading == restored.after
    assert world.unresolved() == []
    assert isinstance(world.apply("apply_third_001"), DawWriteConfirmed)


def test_an_uncertain_restoration_is_reconciled_and_resolved_then_its_root(journal):
    world = World(journal)
    world.lose_next_reply = True
    world.apply()
    request = world.restore_request("restore_cmd_0001", "recovery_auth_0001")
    world.lose_next_reply = True  # after the inspection that builds the request: the restoration reply is lost
    restored = world.controller.restore(request)
    assert isinstance(restored, DawRestoreUncertain)
    finding = world.reconcile("reconcile_0001", "restore_cmd_0001").value
    assert finding.finding is DawReconcileFinding.AT_COMMANDED  # the fader reads the restoration's target
    assert isinstance(world.resolve("resolve_0001", "restore_cmd_0001", DawResolutionKind.APPLIED_AS_COMMANDED,
                                    "reconcile_0001"), DawResolutionRecorded)  # fmt: skip
    assert world.unresolved() == [ROOT]
    root_finding = world.reconcile("reconcile_0002", ROOT).value
    assert root_finding.finding is DawReconcileFinding.AT_RECORDED_START
    assert isinstance(world.resolve("resolve_0002", ROOT, DawResolutionKind.NOT_APPLIED_OR_REVERTED,
                                    "reconcile_0002", "resolve_auth_0002"), DawResolutionRecorded)  # fmt: skip
    assert world.unresolved() == []


def test_a_mismatched_restoration_keeps_actions_blocked(journal):
    world = World(journal)
    world.apply()
    world.host.land_offset = 2e-10  # lands within tolerance, not exactly
    _, outcome = world.restore()
    assert isinstance(outcome, DawRestoreMismatch)
    assert "restore_cmd_0001" in world.unresolved()
    world.host.land_offset = 0.0
    assert isinstance(world.apply("apply_second_01"), DawWriteRefused)


def test_an_installed_v1_script_refuses_the_restoration_which_stays_charged_and_blocking(journal):
    world = World(journal, device="v1")
    world.apply()
    _, outcome = world.restore()
    assert isinstance(outcome, DawRestoreRefused) and outcome.code is DawRefusalCode.UNSUPPORTED
    state = world.controller.control_state().value
    assert [item.command_id for item in state.unresolved] == ["restore_cmd_0001"]
    assert len(state.recovery_charges) == 1  # refused after reservation: charged
    assert world.host.writes == [(2, sends(world, "set_gain_db")[0]["target_normalized"])]  # no restore write


def test_reconciliation_appends_a_finding_and_never_clears(journal):
    world = World(journal)
    world.lose_next_reply = True
    world.apply()
    before = world.unresolved()
    finding = world.reconcile("reconcile_0001", ROOT).value
    assert finding.finding is DawReconcileFinding.AT_COMMANDED and world.unresolved() == before
    world.inspect()  # inspection is observational
    assert world.unresolved() == before


# --- Amendment R through the controller ------------------------------------------------------------------------------


def test_a_childs_finding_cannot_resolve_its_root_through_the_controller(journal):
    # A goes from the live start toward -1 dB (reply lost); restoration R's reply is lost too, and the fader ends
    # where R started (as if R had no effect). Reconciling R then finds AT_RECORDED_START: R's own start.
    world = World(journal)
    world.lose_next_reply = True
    world.apply()
    request = world.restore_request("restore_cmd_0001", "recovery_auth_0001")
    world.lose_next_reply = True
    assert isinstance(world.controller.restore(request), DawRestoreUncertain)
    world.host.volumes[2] = request.inspection.fader.normalized  # fader back where the restoration started
    child = world.reconcile("reconcile_0001", "restore_cmd_0001").value
    assert child.finding is DawReconcileFinding.AT_RECORDED_START  # the restoration's own start
    refused = world.resolve("resolve_0001", ROOT, DawResolutionKind.NOT_APPLIED_OR_REVERTED, "reconcile_0001")
    assert isinstance(refused, DawResolutionRefused)
    assert refused.code is DawResolutionRefusalCode.EVIDENCE_NOT_FOR_SUBJECT
    assert ROOT in world.unresolved()


def test_a_newer_unverified_reading_blocks_resolution_with_an_older_favourable_one(journal):
    world = World(journal)
    world.lose_next_reply = True
    world.apply()
    assert world.reconcile("reconcile_0001", ROOT).value.finding is DawReconcileFinding.AT_COMMANDED
    world.host = FakeHost(normalized=world.host.volumes[2], title="Other Project")  # identity cannot be verified
    assert world.reconcile("reconcile_0002", ROOT).value.finding is DawReconcileFinding.IDENTITY_UNVERIFIED
    refused = world.resolve("resolve_0001", ROOT, DawResolutionKind.APPLIED_AS_COMMANDED, "reconcile_0001")
    assert isinstance(refused, DawResolutionRefused) and refused.code is DawResolutionRefusalCode.STALE_EVIDENCE


# --- replay, conflicts, charges, poisoning -------------------------------------------------------------------------


def test_identical_retries_replay_without_sends_or_charges(journal):
    world = World(journal)
    world.apply()
    request, first = world.restore()
    restore_sends = len(sends(world, "restore_gain"))
    again = world.controller.restore(request)
    assert again == replace(first, replayed=True) and len(sends(world, "restore_gain")) == restore_sends
    assert len(world.controller.control_state().value.recovery_charges) == 1
    other = replace(request, authorization=replace(request.authorization, authorization_id="recovery_auth_0002"))
    conflict = world.controller.restore(other)
    assert isinstance(conflict, DawRestoreRefused) and conflict.code is DawRefusalCode.DUPLICATE_ID_CONFLICT


def test_reconciliation_and_resolution_replay_and_refuse_conflicts(journal):
    world = World(journal)
    world.lose_next_reply = True
    world.apply()
    first = world.reconcile("reconcile_0001", ROOT).value
    again = world.reconcile("reconcile_0001", ROOT).value
    assert again == replace(first, replayed=True)
    recorded = world.resolve("resolve_0001", ROOT, DawResolutionKind.APPLIED_AS_COMMANDED, "reconcile_0001")
    replayed = world.resolve("resolve_0001", ROOT, DawResolutionKind.APPLIED_AS_COMMANDED, "reconcile_0001")
    assert replayed == replace(recorded, replayed=True)
    conflict = world.resolve("resolve_0001", ROOT, DawResolutionKind.NOT_APPLIED_OR_REVERTED, "reconcile_0001")
    assert isinstance(conflict, DawResolutionRefused)
    assert conflict.code is DawResolutionRefusalCode.DUPLICATE_ID_CONFLICT


def test_a_local_refusal_sends_nothing_charges_nothing_and_does_not_block(journal):
    world = World(journal)
    world.apply()
    request = world.restore_request("restore_cmd_0001", "recovery_auth_0001")
    other_profile = replace(request.profile, reference=VersionRef("fl-control-profile", "9"))
    inspection = replace(request.inspection, request=replace(request.inspection.request, profile=other_profile))
    outcome = world.controller.restore(replace(request, profile=other_profile, inspection=inspection))
    assert isinstance(outcome, DawRestoreRefused) and outcome.code is DawRefusalCode.UNSUPPORTED
    state = world.controller.control_state().value
    assert state.unresolved == () and state.recovery_charges == () and sends(world, "restore_gain") == []


def test_an_outcome_commit_failure_poisons_the_controller(journal, monkeypatch):
    world = World(journal)
    world.apply()
    request = world.restore_request("restore_cmd_0001", "recovery_auth_0001")

    def failing_finish(*args):
        raise JournalError("disk full")

    monkeypatch.setattr(journal, "finish", failing_finish)
    outcome = world.controller.restore(request)
    assert isinstance(outcome, DawRestoreUncertain) and outcome.reason is DawUncertainReason.DISPATCH_INTERRUPTED
    monkeypatch.undo()
    second_auth = replace(request.authorization, authorization_id="recovery_auth_0002")
    second = world.controller.restore(replace(request, recovery_command_id="restore_cmd_0002",
                                              authorization=second_auth))  # fmt: skip
    assert isinstance(second, DawRestoreRefused) and second.code is DawRefusalCode.UNAVAILABLE


def test_restore_target_and_control_state_failures_are_typed(journal):
    world = World(journal)
    assert isinstance(world.controller.restore_target("apply_unknown_01"), PortFailure)
    world.apply("apply_refused_01")
    assert isinstance(world.controller.control_state(), Success)


def test_reconciling_a_resolved_or_unknown_subject_is_refused(journal):
    world = World(journal)
    world.apply()
    result = world.reconcile("reconcile_0001", ROOT)
    assert isinstance(result, PortFailure)  # a confirmed apply is not unresolved


def test_a_retry_after_restart_replays_from_the_journal_without_a_send_or_charge(journal):
    # a fresh controller has no in-memory cache: the replay must come from the journal itself
    world = World(journal)
    world.apply()
    request, first = world.restore()
    restarted = World(journal, host=world.host)
    again = restarted.controller.restore(request)
    assert again == replace(first, replayed=True)
    assert sends(restarted, "restore_gain") == []
    assert len(restarted.controller.control_state().value.recovery_charges) == 1
