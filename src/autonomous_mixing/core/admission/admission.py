"""Explicit gain-action admission (ADR-018)."""

from autonomous_mixing.core.ports.decision import validate_decision_response
from autonomous_mixing.core.records.admission import (
    ActionAdmissionPolicy,
    ActionAdmissionRequest,
    AdmissionRejectionCode,
    AdmissionResult,
    AdmittedAction,
    RejectedAction,
    TrackGainPermission,
)
from autonomous_mixing.core.records.decision import DecisionResponse, ProposeAction
from autonomous_mixing.core.records.identifiers import TrackId
from autonomous_mixing.core.records.invariants import require_instance


def _gain_permission_for(policy: ActionAdmissionPolicy, track_id: TrackId) -> TrackGainPermission | None:
    for permission in policy.gain_permissions:
        if permission.track_id == track_id:
            return permission
    return None


def admit_action(request: ActionAdmissionRequest) -> AdmissionResult:
    """Admit or reject a decision's proposed action. Checks run in a fixed order; the first failure wins.

    1. decision references and controller (via ``validate_decision_response``), 2. policy project,
    3. a ProposeAction outcome, 4. producer protection, 5. an explicit gain permission, 6. inclusive bounds.
    """
    require_instance(request, ActionAdmissionRequest, "request")
    decision, decision_request, policy = request.decision, request.decision_request, request.policy

    def reject(code: AdmissionRejectionCode, explanation: str) -> RejectedAction:
        return RejectedAction(decision.decision_id, code, explanation)

    try:
        validate_decision_response(decision_request, DecisionResponse(decision))
    except ValueError as error:  # expected reference failures only; other exceptions propagate
        return reject(AdmissionRejectionCode.INVALID_REFERENCES, f"decision references are invalid: {error}")

    snapshot_project = decision_request.state.snapshot.project_id
    if policy.project_id != snapshot_project:
        return reject(
            AdmissionRejectionCode.POLICY_PROJECT_MISMATCH,
            f"policy {policy.reference.name}@{policy.reference.version} is for project {policy.project_id}, "
            f"not {snapshot_project}",
        )

    if not isinstance(decision.outcome, ProposeAction):
        return reject(AdmissionRejectionCode.NOT_AN_ACTION, "the decision does not propose an action")
    action = decision.outcome.action
    operation = action.operation  # SetTrackGain is the only operation type (enforced by Action)

    target = operation.track_id
    if target in decision_request.context.protected_track_ids:
        return reject(AdmissionRejectionCode.PROTECTED_TRACK, f"track {target} is protected by the producer context")

    permission = _gain_permission_for(policy, target)
    if permission is None:
        return reject(AdmissionRejectionCode.NO_GAIN_PERMISSION, f"policy grants no gain permission for track {target}")

    if not permission.minimum_db <= operation.value_db <= permission.maximum_db:
        return reject(
            AdmissionRejectionCode.OUT_OF_BOUNDS,
            f"requested gain {operation.value_db} dB for track {target} is outside the permitted range "
            f"[{permission.minimum_db}, {permission.maximum_db}] dB",
        )

    return AdmittedAction(action, policy.reference)
