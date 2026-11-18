"""Test-only construction of journaled applies and restorations against the real runtime records and journal."""

import json
from dataclasses import replace

from fl_device_support import h_db
from fl_runtime_support import make_request

from autonomous_mixing.adapters.fl_studio import protocol
from autonomous_mixing.adapters.fl_studio.profile import make_apply_command
from autonomous_mixing.adapters.fl_studio.serialization import grant_json, request_json, restore_request_json
from autonomous_mixing.core.admission.daw import gate_daw_restore
from autonomous_mixing.core.records.budgets import BudgetGrant, BudgetOperation, BudgetUsage
from autonomous_mixing.core.records.daw import DawFaderReading
from autonomous_mixing.core.records.daw_control import (
    DawInspection,
    DawInspectRequest,
    DawRefusalCode,
    DawUncertainReason,
    DawWriteConfirmed,
    DawWriteOffTarget,
    DawWriteRefused,
    DawWriteUncertain,
)
from autonomous_mixing.core.records.daw_recovery import (
    DawControlState,
    DawRecoveryAuthorization,
    DawRestorePermitted,
    DawRestoreTarget,
    DawTargetBasis,
)
from autonomous_mixing.core.records.identifiers import ExecutionId, VersionRef

CONTROLLER = VersionRef("controller", "1")


def grant(n=1):
    return BudgetGrant(BudgetOperation.RECOVERY_OPERATION, BudgetUsage(1, 1, 0, n, 1.0))


def apply_written(request, kind="confirmed"):
    command = make_apply_command(request)
    before = request.inspection.fader
    after = DawFaderReading(command["target_normalized"], before.db + request.requested_change_db)
    if kind == "confirmed":
        return DawWriteConfirmed(request.command_id, before, after, command["target_normalized"],
                                 after.db - before.db, False)  # fmt: skip
    if kind == "off_target":
        after = DawFaderReading(command["target_normalized"], before.db + request.requested_change_db + 0.1)
        return DawWriteOffTarget(request.command_id, before, after, command["target_normalized"],
                                 after.db - before.db, False)  # fmt: skip
    if kind == "uncertain":
        return DawWriteUncertain(request.command_id, DawUncertainReason.TIMEOUT, before, None, "no reply")
    if kind == "uncertain_no_before":
        return DawWriteUncertain(request.command_id, DawUncertainReason.DISPATCH_INTERRUPTED, None, None, "lost")
    return DawWriteRefused(request.command_id, DawRefusalCode.TRANSPORT_PLAYING, "playing", None)


def journal_apply(journal, command_id, kind="confirmed", pending=False):
    """Reserve one real apply and, unless pending, finish it with an outcome of ``kind``."""
    request = make_request(command_id)
    journal.reserve(command_id, request_json(request), json.dumps(make_apply_command(request), sort_keys=True))
    if not pending:
        journal.finish(command_id, apply_written(request, kind))
    return request


def target_for(journal, root):
    basis, reading, conversion = journal.restore_target(root)
    return DawRestoreTarget(root, basis, reading, conversion)


def restore_request(journal, root, recovery_id, authorization_id, current=(0.7257362319041692, None), target=None,
                    request=None):  # fmt: skip
    """A gate-permitted DawRestoreRequest for ``root``, from the journal's own target unless ``target`` is given."""
    request = request or make_request(root)
    binding, profile = request.resolution.state.binding, request.profile
    normalized, db = current
    reading = DawFaderReading(normalized, h_db(normalized) if db is None else db)
    inspection = DawInspection(DawInspectRequest(binding, profile), profile.build, binding.project_title,
                               binding.insert_index, binding.insert_name, reading)  # fmt: skip
    authorization = DawRecoveryAuthorization(authorization_id, ExecutionId("e1"), CONTROLLER,
                                             target or target_for(journal, root), grant())  # fmt: skip
    result = gate_daw_restore(authorization, binding=binding, inspection=inspection, profile=profile,
                              recovery_command_id=recovery_id,
                              control_state=DawControlState((), frozenset(), ()))  # fmt: skip
    assert isinstance(result, DawRestorePermitted), result
    return result.request


def restore_wire(request):
    current, target = request.inspection.fader, request.target
    return protocol.restore_gain_command(request.recovery_command_id, request.profile.project_title,
                                         request.profile.insert_index, request.profile.insert_name, current.normalized,
                                         current.db, target.normalized, target.db)  # fmt: skip


def journal_restore(journal, root, recovery_id, authorization_id, outcome=None, **kwargs):
    """Reserve (and charge) one restoration of ``root``; finish it with ``outcome`` (a callable of the request)."""
    request = restore_request(journal, root, recovery_id, authorization_id, **kwargs)
    journal.reserve_restore(recovery_id, restore_request_json(request), json.dumps(restore_wire(request),
                            sort_keys=True), authorization_id=authorization_id,
                            grant_json=grant_json(request.authorization.budget_grant))  # fmt: skip
    if outcome is not None:
        journal.finish(recovery_id, outcome(request))
    return request


def with_target(request, reading, basis=DawTargetBasis.OUTCOME_BEFORE):
    target = replace(request.authorization.target, reading=reading, basis=basis)
    return replace(request, authorization=replace(request.authorization, target=target))
