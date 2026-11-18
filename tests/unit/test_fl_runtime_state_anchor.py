"""Journal-backed state anchors and coherent continuity snapshots of the FL runtime controller (export plan rev. 3).

Real runtime protocol, exchange, journal and controller against device script v2 on a fake host. Not live FL evidence.
"""

import json
import threading

import pytest
from fl_recovery_support import journal_apply
from fl_runtime_support import make_request
from test_fl_runtime_recovery_controller import ROOT, World

from autonomous_mixing.adapters.fl_studio.journal import SqliteCommandJournal
from autonomous_mixing.adapters.fl_studio.serialization import request_json
from autonomous_mixing.core.ports.daw_recovery import validate_daw_continuity_snapshot, validate_daw_state_anchor
from autonomous_mixing.core.records.daw_control import DawRefusalCode, DawWriteConfirmed, DawWriteRefused
from autonomous_mixing.core.records.daw_export import DawStateBasis, daw_continuity_rejection
from autonomous_mixing.core.records.daw_recovery import DawResolutionKind, DawResolutionRecorded, DawRestoreConfirmed
from autonomous_mixing.core.records.identifiers import ExecutionId
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success


@pytest.fixture
def journal(tmp_path):
    instance = SqliteCommandJournal(tmp_path / "journal.db", session_key="session-1")
    yield instance
    instance.close()


def anchor_of(world, source_id):
    result = world.controller.state_anchor(source_id)
    validate_daw_state_anchor(source_id, result)
    return result


def snapshot_of(world, source_id):
    result = world.controller.continuity_snapshot(source_id)
    validate_daw_continuity_snapshot(source_id, result)
    return result


def ledger_seqs(journal):
    return [seq for seq, *_ in journal._ledger()]


# --- anchors -------------------------------------------------------------------------------------------------------------


def test_a_confirmed_apply_anchors_its_after_reading_with_the_complete_journaled_request(journal):
    world = World(journal)
    applied = world.apply()
    assert isinstance(applied, DawWriteConfirmed)
    result = anchor_of(world, ROOT)
    assert isinstance(result, Success), result
    anchor, request = result.value, make_request(ROOT)
    assert (anchor.basis, anchor.source_seq, anchor.reading) == (DawStateBasis.CONFIRMED_WRITE, 1, applied.after)
    assert anchor.binding == request.resolution.state.binding and anchor.profile == request.profile
    assert anchor.execution_id == ExecutionId("e1")
    assert (anchor.reservation_watermark, anchor.resolution_watermark, anchor.ledger_watermark) == (1, 0, 1)


def test_a_confirmed_rollback_anchors_the_restoration_and_supersedes_the_apply(journal):
    world = World(journal)
    applied = world.apply()
    _, restored = world.restore()
    assert isinstance(restored, DawRestoreConfirmed)
    anchor = anchor_of(world, "restore_cmd_0001").value
    assert anchor.basis is DawStateBasis.CONFIRMED_RESTORE and anchor.reading == restored.after == applied.before
    assert anchor.source_seq == anchor.reservation_watermark == 2 and anchor.ledger_watermark == 3  # + its charge
    superseded = anchor_of(world, ROOT)
    assert isinstance(superseded, PortFailure) and superseded.code is PortFailureCode.INVALID_INPUT


def test_a_resolution_anchors_the_resolved_reading_with_the_roots_binding_and_profile(journal):
    world = World(journal)
    world.lose_next_reply = True
    world.apply()
    unresolved = anchor_of(world, ROOT)
    assert isinstance(unresolved, PortFailure) and "unresolved" in unresolved.message
    _, restored = world.restore()
    assert isinstance(anchor_of(world, "restore_cmd_0001"), PortFailure)  # the root is still unresolved
    recorded = world.resolve("resolve_0001", ROOT, DawResolutionKind.RESTORED_TO_RECORDED_START, "restore_cmd_0001")
    assert isinstance(recorded, DawResolutionRecorded)
    anchor = anchor_of(world, "resolve_0001").value
    assert anchor.basis is DawStateBasis.RESOLVED and anchor.reading == recorded.resolved_reading == restored.after
    assert anchor.source_seq == anchor.resolution_watermark == recorded.ledger_seq
    assert anchor.binding == make_request(ROOT).resolution.state.binding and anchor.profile == make_request().profile
    assert isinstance(anchor_of(world, "restore_cmd_0001"), PortFailure)  # superseded by the resolution


@pytest.mark.parametrize("kind", ["off_target", "refused", "uncertain"])
def test_non_confirmed_sources_are_refused(journal, kind):
    journal_apply(journal, "apply_source_01", kind)
    world = World(journal)
    result = anchor_of(world, "apply_source_01")
    assert isinstance(result, PortFailure) and result.code is PortFailureCode.INVALID_INPUT


def test_unknown_sources_ledger_entries_of_other_kinds_and_pending_commands_are_refused(journal):
    world = World(journal)
    for source in ("never_seen_0001", "inspect_00000001"):
        assert isinstance(anchor_of(world, source), PortFailure)
    journal_apply(journal, "apply_pending_1", pending=True)
    assert isinstance(anchor_of(world, "apply_pending_1"), PortFailure)


def test_a_later_write_supersedes_and_a_local_refusal_advances_only_the_ledger(journal):
    world = World(journal)
    world.apply()
    world.host.volumes[2] = 0.76  # back to the live start so a second gate-built cut is accepted
    assert isinstance(world.apply("apply_second_01"), DawWriteConfirmed)
    assert isinstance(anchor_of(world, ROOT), PortFailure)
    second = anchor_of(world, "apply_second_01").value
    assert (second.source_seq, second.reservation_watermark, second.ledger_watermark) == (2, 2, 2)
    world.host.playing = True  # the device refuses: reserved, so this supersedes as well (fails closed)
    assert isinstance(world.apply("apply_third_001"), DawWriteRefused)
    assert isinstance(anchor_of(world, "apply_second_01"), PortFailure)


def test_a_local_refusal_marker_moves_the_ledger_watermark_but_not_the_anchor_source(journal):
    world = World(journal)
    world.apply()
    before = anchor_of(world, ROOT).value
    world.controller._poisoned = True  # the next apply is refused locally and journaled with a marker only
    assert world.apply("apply_local_001").code is DawRefusalCode.UNAVAILABLE
    after = anchor_of(world, ROOT).value
    assert after.reservation_watermark == before.reservation_watermark
    assert after.ledger_watermark == before.ledger_watermark + 1 and after != before


# --- coherent snapshots ---------------------------------------------------------------------------------------------------


def test_a_coherent_snapshot_agrees_with_the_anchor(journal):
    world = World(journal)
    world.apply()
    anchor = anchor_of(world, ROOT).value
    result = snapshot_of(world, ROOT)
    assert isinstance(result, Success), result
    assert result.value.anchor == anchor and result.value.inspection.fader == anchor.reading
    assert daw_continuity_rejection(anchor, result.value) is None
    assert ledger_seqs(journal) == [1]  # an inspection appends no ledger entry


def hook_first_inspection(world, action):
    """Run ``action`` once, when the controller opens the transport for the snapshot's inspection."""
    original, fired = world.controller.transport_factory, []

    def factory():
        if not fired:
            fired.append(True)
            action()
        return original()

    world.controller.transport_factory = factory
    return fired


def test_a_reservation_between_the_first_anchor_query_and_the_inspection_returns_no_snapshot(journal):
    world = World(journal)
    world.apply()
    fired = hook_first_inspection(world, lambda: journal_apply(journal, "apply_intruder1", pending=True))
    result = snapshot_of(world, ROOT)
    assert fired and isinstance(result, PortFailure)
    assert ledger_seqs(journal) == [1, 2]


def test_a_ledger_only_entry_between_the_queries_returns_a_retryable_failure(journal):
    world = World(journal)
    world.apply()
    refused = make_request("apply_local_002")

    def intrude():
        descriptor = json.dumps({"id": "apply_local_002", "local_refusal": True, "type": "set_gain_db"}, sort_keys=True)
        outcome = DawWriteRefused("apply_local_002", DawRefusalCode.UNAVAILABLE, "refused locally", None)
        journal._remember_refusal("apply_local_002", request_json(refused), descriptor, outcome)

    fired = hook_first_inspection(world, intrude)
    result = snapshot_of(world, ROOT)
    assert fired and isinstance(result, PortFailure) and result.retryable
    assert "changed during" in result.message
    retried = snapshot_of(world, ROOT)
    assert isinstance(retried, Success) and retried.value.anchor.ledger_watermark == 2


def test_the_controller_lock_is_held_across_the_inspection(journal):
    world = World(journal)
    world.apply()
    observed = []

    def probe():
        thread = threading.Thread(target=lambda: observed.append(world.controller._lock.acquire(blocking=False)))
        thread.start()
        thread.join()

    hook_first_inspection(world, probe)
    assert isinstance(snapshot_of(world, ROOT), Success)
    assert observed == [False]


def test_no_snapshot_while_unresolved_or_when_the_inspection_fails(journal):
    world = World(journal)
    world.apply()
    world.lose_next_reply = True
    failed = snapshot_of(world, ROOT)
    assert isinstance(failed, PortFailure) and failed.code is PortFailureCode.UNAVAILABLE
    journal_apply(journal, "apply_pending_2", pending=True)
    assert isinstance(snapshot_of(world, ROOT), PortFailure)


def test_a_moved_fader_is_snapshotted_as_observed_and_rejected_by_the_continuity_rule(journal):
    world = World(journal)
    world.apply()
    anchor = anchor_of(world, ROOT).value
    world.host.volumes[2] += 1e-9  # a manual move the journal knows nothing about
    result = snapshot_of(world, ROOT)
    assert isinstance(result, Success) and result.value.inspection.fader != anchor.reading
    assert daw_continuity_rejection(anchor, result.value) is not None
