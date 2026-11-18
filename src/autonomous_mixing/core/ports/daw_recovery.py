"""The DAW recovery/reconciliation port and the structural checks on its responses.

Beside ``DawParameterController`` (unchanged). Obligations on every implementation, in addition to the apply
rules (pending intent before dispatch; identical retries replay without another write, written outcomes with
``replayed=True``, refused and uncertain ones unchanged; conflicting reuse refused; unknown after dispatch is
uncertain):

- the recovery budget charge is committed in the same transaction as the restoration's pending intent, before
  any byte is sent; a replay never charges again and an uncertain outcome is never refunded;
- ``restore_target`` returns only a target recomputed from the adapter's own journal under the basis rule
  (outcome ``before`` when recorded, else the root request's inspected reading);
- ``reconcile`` performs its own fresh read and appends its finding; it never clears anything;
- ``resolve`` accepts only subject-specific, latest relevant evidence with no reservation after it;
- conversion blocks are never cleared by this version (no method exists).

Restoring one fader proves only that fader's readings, never routing, plugins, the project or audio.
"""

from typing import Protocol

from autonomous_mixing.core.records.daw_control import DawInspectRequest
from autonomous_mixing.core.records.daw_recovery import (
    DawControlState,
    DawReconcileRequest,
    DawReconciliation,
    DawResolutionOutcome,
    DawResolutionRecorded,
    DawResolutionRefused,
    DawResolutionRequest,
    DawRestoreConfirmed,
    DawRestoreMismatch,
    DawRestoreOutcome,
    DawRestoreRefused,
    DawRestoreRequest,
    DawRestoreTarget,
    DawRestoreUncertain,
)
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.results import PortFailure, PortResult, Success


class DawRecoveryController(Protocol):
    """Exact-target recovery and explicit reconciliation for the bound insert; no audio or acceptance authority."""

    def control_state(self) -> PortResult[DawControlState]: ...

    def restore_target(self, source_command_id: str) -> PortResult[DawRestoreTarget]: ...

    def reconcile(self, request: DawReconcileRequest) -> PortResult[DawReconciliation]: ...

    def restore(self, request: DawRestoreRequest) -> DawRestoreOutcome: ...

    def resolve(self, request: DawResolutionRequest) -> DawResolutionOutcome: ...


def validate_daw_restore_outcome(request: DawRestoreRequest, outcome: DawRestoreOutcome) -> None:
    """Structural consistency of a restore outcome with its request (raises ``ValueError``)."""
    require_instance(request, DawRestoreRequest, "request")
    if not isinstance(outcome, (DawRestoreConfirmed, DawRestoreMismatch, DawRestoreRefused, DawRestoreUncertain)):
        raise ValueError(f"unknown DAW restore outcome {outcome!r}")
    if outcome.recovery_command_id != request.recovery_command_id:
        raise ValueError("the outcome answers another recovery command")
    if isinstance(outcome, (DawRestoreRefused, DawRestoreUncertain)):
        return
    if outcome.target != request.target:
        raise ValueError("the outcome reports another target than the request's")
    profile, expected = request.profile, request.inspection.fader
    if (
        abs(outcome.before.normalized - expected.normalized) > profile.start_normalized_tolerance
        or abs(outcome.before.db - expected.db) > profile.start_db_tolerance
    ):
        raise ValueError("the written restoration does not start where the request expected")


def validate_daw_reconciliation(request: DawReconcileRequest, result: PortResult[DawReconciliation]) -> None:
    require_instance(request, DawReconcileRequest, "request")
    if isinstance(result, PortFailure):
        return
    if not isinstance(result, Success) or not isinstance(result.value, DawReconciliation):
        raise ValueError("a reconciliation response must be Success(DawReconciliation) or a PortFailure")
    value = result.value
    if (value.reconciliation_id, value.subject_command_id) != (request.reconciliation_id, request.subject_command_id):
        raise ValueError("the reconciliation answers another request")
    if value.inspection is not None and value.inspection.request != DawInspectRequest(request.binding, request.profile):
        raise ValueError("the reconciliation's reading answers another binding or profile")


def validate_daw_resolution(request: DawResolutionRequest, outcome: DawResolutionOutcome) -> None:
    require_instance(request, DawResolutionRequest, "request")
    if isinstance(outcome, DawResolutionRefused):
        if outcome.resolution_id != request.resolution_id:
            raise ValueError("the refusal answers another resolution")
        return
    if not isinstance(outcome, DawResolutionRecorded):
        raise ValueError("a resolution response must be DawResolutionRecorded or DawResolutionRefused")
    if (outcome.resolution_id, outcome.subject_command_id, outcome.kind, outcome.evidence_id) != (
        request.resolution_id,
        request.subject_command_id,
        request.kind,
        request.evidence_id,
    ):
        raise ValueError("the recorded resolution differs from its request")


def validate_daw_control_state(result: PortResult[DawControlState]) -> None:
    if isinstance(result, PortFailure):
        return
    if not isinstance(result, Success) or not isinstance(result.value, DawControlState):
        raise ValueError("a control-state response must be Success(DawControlState) or a PortFailure")
