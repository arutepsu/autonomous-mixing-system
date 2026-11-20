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
- conversion blocks are never cleared by this version (no method exists);
- ``state_anchor`` returns only the latest state-establishing ledger event (a confirmed write or restoration, or a
  resolution) with the complete binding and profile journaled with it, refuses while anything is unresolved, and
  reads every watermark in one journal pass (export plan 2026-11-18, revision 3);
- ``continuity_snapshot`` holds the adapter's lock across the whole capture, queries the anchor and the control
  state before and after one fresh inspection of the anchor's binding and profile, and returns the snapshot only
  when both queries agree; otherwise a ``PortFailure`` (never a partially consistent snapshot);
- ``recovery_lineage`` (coordinator Increment 4, ADR-038) describes one root apply whatever its outcome, from one
  locked journal pass: its exact journaled request and outcome, its restorations, reconciliations and resolutions,
  every reservation or resolution outside the lineage after it, the unresolved commands and the watermarks. It
  refuses a refused root (nothing was written) and never changes the journal. Export anchors are unaffected;
- ``recovery_snapshot`` holds the lock across the lineage and control state read before and after one fresh
  inspection of the lineage's binding and profile, returning the snapshot only when both reads agree.

Restoring one fader proves only that fader's readings, never routing, plugins, the project or audio.
"""

from typing import Protocol

from autonomous_mixing.core.records.daw_control import DawInspectRequest
from autonomous_mixing.core.records.daw_export import DawContinuitySnapshot, DawStateAnchor
from autonomous_mixing.core.records.daw_recovery import (
    DawControlState,
    DawReconcileRequest,
    DawReconciliation,
    DawRecoveryLineage,
    DawRecoverySnapshot,
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

    def state_anchor(self, source_id: str) -> PortResult[DawStateAnchor]: ...

    def continuity_snapshot(self, source_id: str) -> PortResult[DawContinuitySnapshot]: ...

    def recovery_lineage(self, root_command_id: str) -> PortResult[DawRecoveryLineage]: ...

    def recovery_snapshot(self, root_command_id: str) -> PortResult[DawRecoverySnapshot]: ...


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


def validate_daw_state_anchor(source_id: str, result: PortResult[DawStateAnchor]) -> None:
    if isinstance(result, PortFailure):
        return
    if not isinstance(result, Success) or not isinstance(result.value, DawStateAnchor):
        raise ValueError("a state-anchor response must be Success(DawStateAnchor) or a PortFailure")
    if result.value.source_id != source_id:
        raise ValueError("the anchor answers another source")


def validate_daw_continuity_snapshot(source_id: str, result: PortResult[DawContinuitySnapshot]) -> None:
    if isinstance(result, PortFailure):
        return
    if not isinstance(result, Success) or not isinstance(result.value, DawContinuitySnapshot):
        raise ValueError("a continuity response must be Success(DawContinuitySnapshot) or a PortFailure")
    if result.value.anchor.source_id != source_id:
        raise ValueError("the snapshot answers another source")


def validate_daw_recovery_lineage(root_command_id: str, result: PortResult[DawRecoveryLineage]) -> None:
    if isinstance(result, PortFailure):
        return
    if not isinstance(result, Success) or not isinstance(result.value, DawRecoveryLineage):
        raise ValueError("a recovery-lineage response must be Success(DawRecoveryLineage) or a PortFailure")
    if result.value.root_command_id != root_command_id:
        raise ValueError("the lineage answers another root")


def validate_daw_recovery_snapshot(root_command_id: str, result: PortResult[DawRecoverySnapshot]) -> None:
    if isinstance(result, PortFailure):
        return
    if not isinstance(result, Success) or not isinstance(result.value, DawRecoverySnapshot):
        raise ValueError("a recovery-snapshot response must be Success(DawRecoverySnapshot) or a PortFailure")
    if result.value.lineage.root_command_id != root_command_id:
        raise ValueError("the recovery snapshot answers another root")
