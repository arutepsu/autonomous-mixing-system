"""Declared renderer support and the derived view of available actions (attempt contract, sections 11 and 28).

These lower-level records import no admission, decision or run records, so a later ``Situation`` can carry
the view without a records cycle. A view describes declared available actions only: it is not a diagnosed
problem, a justified proposal, execution permission or proof that rendering is safe.
"""

from dataclasses import dataclass

from autonomous_mixing.core.records.identifiers import ContextId, ProjectId, Revision, TrackId, VersionRef
from autonomous_mixing.core.records.invariants import require_finite, require_instance, require_text, require_tuple_of
from autonomous_mixing.core.records.observation import AnalysisScope


@dataclass(frozen=True)
class GainBounds:
    """Inclusive absolute additional gain range in dB; equal endpoints permit one exact setting.

    Values are kept exactly as supplied. No default or scientifically motivated range is implied.
    """

    minimum_db: float
    maximum_db: float

    def __post_init__(self) -> None:
        minimum = require_finite(self.minimum_db, "GainBounds.minimum_db")
        maximum = require_finite(self.maximum_db, "GainBounds.maximum_db")
        if minimum > maximum:
            raise ValueError(f"GainBounds.minimum_db {minimum} exceeds maximum_db {maximum}")


@dataclass(frozen=True)
class RendererCapabilities:
    """Explicit, versioned description of what a configured renderer supports.

    ``gain_bounds`` of ``None`` declares no gain support. Declared bounds describe parameter support; they
    do not guarantee that every signal renders without overflow or that resources are ready. Nothing here
    inspects or binds an actual renderer implementation.
    """

    reference: VersionRef
    gain_bounds: GainBounds | None

    def __post_init__(self) -> None:
        require_instance(self.reference, VersionRef, "RendererCapabilities.reference")
        if self.gain_bounds is not None:
            require_instance(self.gain_bounds, GainBounds, "RendererCapabilities.gain_bounds")


@dataclass(frozen=True)
class AvailableTrackGain:
    """One potential ``SetTrackGain`` target and its available range; not an action with an identity."""

    track_id: TrackId
    bounds: GainBounds

    def __post_init__(self) -> None:
        require_instance(self.track_id, TrackId, "AvailableTrackGain.track_id")
        require_instance(self.bounds, GainBounds, "AvailableTrackGain.bounds")


@dataclass(frozen=True)
class ActionCapabilityView:
    """Actions declared available for one project revision, context version, policy, renderer and scope.

    Advertised tracks are unique and within ``scope``; an empty tuple means no gain action is available,
    not that the mix has no problem or that No Change is justified. The constructor does not check policy
    truth, protections, the actual renderer or snapshot membership; admission and dispatch still enforce
    the authoritative rules.
    """

    project_id: ProjectId
    revision: Revision
    context_id: ContextId
    context_version: str
    policy_reference: VersionRef
    renderer_reference: VersionRef
    scope: AnalysisScope
    gain_actions: tuple[AvailableTrackGain, ...]

    def __post_init__(self) -> None:
        require_instance(self.project_id, ProjectId, "ActionCapabilityView.project_id")
        require_instance(self.revision, Revision, "ActionCapabilityView.revision")
        require_instance(self.context_id, ContextId, "ActionCapabilityView.context_id")
        require_text(self.context_version, "ActionCapabilityView.context_version")
        require_instance(self.policy_reference, VersionRef, "ActionCapabilityView.policy_reference")
        require_instance(self.renderer_reference, VersionRef, "ActionCapabilityView.renderer_reference")
        require_instance(self.scope, AnalysisScope, "ActionCapabilityView.scope")
        require_tuple_of(
            self.gain_actions, AvailableTrackGain, "ActionCapabilityView.gain_actions", nonempty=False, distinct=False
        )
        tracks = [action.track_id for action in self.gain_actions]
        if len(set(tracks)) != len(tracks):
            raise ValueError("ActionCapabilityView.gain_actions must have at most one entry per track")
        outside = [str(t) for t in tracks if t not in self.scope.track_ids]
        if outside:
            raise ValueError(f"ActionCapabilityView.gain_actions names tracks outside its scope: {', '.join(outside)}")
