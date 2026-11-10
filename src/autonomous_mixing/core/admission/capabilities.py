"""Derivation of the declared action-capability view from renderer support, policy, scope and protections."""

from autonomous_mixing.core.records.admission import ActionAdmissionPolicy, TrackGainPermission
from autonomous_mixing.core.records.capabilities import (
    ActionCapabilityView,
    AvailableTrackGain,
    GainBounds,
    RendererCapabilities,
)
from autonomous_mixing.core.records.context import MusicalContext
from autonomous_mixing.core.records.identifiers import TrackId
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.observation import AnalysisScope
from autonomous_mixing.core.records.project import ProjectState


def build_action_capability_view(
    *,
    state: ProjectState,
    scope: AnalysisScope,
    context: MusicalContext,
    policy: ActionAdmissionPolicy,
    renderer: RendererCapabilities,
) -> ActionCapabilityView:
    """Derive which gain actions are declared available for ``state`` within ``scope``.

    Configuration is validated before filtering: scope and context against the snapshot, the policy's
    project, and every policy permission's track (also outside the scope and without renderer gain
    support), so an invalid configuration raises ``ValueError`` instead of yielding an empty view.

    Tracks are taken in ``scope.track_ids`` order. A track is omitted if it is protected, has no explicit
    gain permission, the renderer declares no gain support, or the permission and renderer ranges are
    disjoint; otherwise its range is their exact intersection (equal endpoints allowed; nothing is
    clamped, rounded or defaulted). The scope restricts targets only: gain still applies to the whole stem.

    The view describes declared availability, not permission: ``admit_action`` does not yet enforce the
    renderer descriptor or the advertised scope.
    """
    require_instance(state, ProjectState, "state")
    require_instance(scope, AnalysisScope, "scope")
    require_instance(context, MusicalContext, "context")
    require_instance(policy, ActionAdmissionPolicy, "policy")
    require_instance(renderer, RendererCapabilities, "renderer")

    snapshot = state.snapshot
    scope.validate_against(snapshot)
    context.validate_against(snapshot)
    if policy.project_id != state.project_id:
        raise ValueError(f"policy project {policy.project_id} does not match state project {state.project_id}")
    snapshot.require_known_tracks(
        tuple(permission.track_id for permission in policy.gain_permissions), "ActionAdmissionPolicy.gain_permissions"
    )

    permissions: dict[TrackId, TrackGainPermission] = {p.track_id: p for p in policy.gain_permissions}
    supported = renderer.gain_bounds
    actions: list[AvailableTrackGain] = []
    if supported is not None:
        for track_id in scope.track_ids:
            permission = permissions.get(track_id)
            if track_id in context.protected_track_ids or permission is None:
                continue
            low = max(permission.minimum_db, supported.minimum_db)
            high = min(permission.maximum_db, supported.maximum_db)
            if low > high:
                continue
            actions.append(AvailableTrackGain(track_id, GainBounds(low, high)))

    return ActionCapabilityView(
        project_id=state.project_id,
        revision=state.revision,
        context_id=context.context_id,
        context_version=context.version,
        policy_reference=policy.reference,
        renderer_reference=renderer.reference,
        scope=scope,
        gain_actions=tuple(actions),
    )
