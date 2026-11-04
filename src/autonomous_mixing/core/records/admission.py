"""Admission policy, admission and execution requests, admission and preflight results."""

from dataclasses import dataclass
from enum import Enum

from autonomous_mixing.core.records.actions import Action
from autonomous_mixing.core.records.decision import Decision, Situation
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    DecisionId,
    ExecutionId,
    ProjectId,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.invariants import require_finite, require_instance, require_text, require_tuple_of
from autonomous_mixing.core.records.mix import MixRecipe, ProcessingSpec
from autonomous_mixing.core.records.observation import RenderSpec
from autonomous_mixing.core.records.rendering import CandidateIdentity, ExecutionWorkspace, RenderPlan


@dataclass(frozen=True)
class TrackGainPermission:
    """Configured absolute gain range for one track, in dB, inclusive at both ends.

    No default or scientifically motivated limits are implied; equal bounds permit one exact setting.
    """

    track_id: TrackId
    minimum_db: float
    maximum_db: float

    def __post_init__(self) -> None:
        require_instance(self.track_id, TrackId, "TrackGainPermission.track_id")
        minimum = require_finite(self.minimum_db, "TrackGainPermission.minimum_db")
        maximum = require_finite(self.maximum_db, "TrackGainPermission.maximum_db")
        if minimum > maximum:
            raise ValueError(f"TrackGainPermission.minimum_db {minimum} exceeds maximum_db {maximum}")


@dataclass(frozen=True)
class ActionAdmissionPolicy:
    """Versioned admission configuration for one project. Empty permissions deny every gain action."""

    reference: VersionRef
    project_id: ProjectId
    gain_permissions: tuple[TrackGainPermission, ...]

    def __post_init__(self) -> None:
        require_instance(self.reference, VersionRef, "ActionAdmissionPolicy.reference")
        require_instance(self.project_id, ProjectId, "ActionAdmissionPolicy.project_id")
        require_tuple_of(
            self.gain_permissions,
            TrackGainPermission,
            "ActionAdmissionPolicy.gain_permissions",
            nonempty=False,
            distinct=False,
        )
        tracks = [permission.track_id for permission in self.gain_permissions]
        if len(set(tracks)) != len(tracks):
            raise ValueError("ActionAdmissionPolicy.gain_permissions must have at most one permission per track")


@dataclass(frozen=True)
class ActionAdmissionRequest:
    """Inputs for admission. Construction checks field types only; references are assessed by ``admit_action``."""

    decision_request: Situation
    decision: Decision
    policy: ActionAdmissionPolicy

    def __post_init__(self) -> None:
        require_instance(self.decision_request, Situation, "ActionAdmissionRequest.decision_request")
        require_instance(self.decision, Decision, "ActionAdmissionRequest.decision")
        require_instance(self.policy, ActionAdmissionPolicy, "ActionAdmissionRequest.policy")


class AdmissionRejectionCode(Enum):
    INVALID_REFERENCES = "invalid_references"
    POLICY_PROJECT_MISMATCH = "policy_project_mismatch"
    NOT_AN_ACTION = "not_an_action"  # e.g. a valid abstention: nothing to admit, not a run failure
    PROTECTED_TRACK = "protected_track"
    NO_GAIN_PERMISSION = "no_gain_permission"
    OUT_OF_BOUNDS = "out_of_bounds"


@dataclass(frozen=True)
class AdmittedAction:
    """A proposal that passed admission; not an execution receipt or security token."""

    action: Action
    policy_reference: VersionRef

    def __post_init__(self) -> None:
        require_instance(self.action, Action, "AdmittedAction.action")
        require_instance(self.policy_reference, VersionRef, "AdmittedAction.policy_reference")


@dataclass(frozen=True)
class RejectedAction:
    decision_id: DecisionId
    code: AdmissionRejectionCode
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.decision_id, DecisionId, "RejectedAction.decision_id")
        require_instance(self.code, AdmissionRejectionCode, "RejectedAction.code")
        require_text(self.explanation, "RejectedAction.explanation")


AdmissionResult = AdmittedAction | RejectedAction


@dataclass(frozen=True)
class StemBinding:
    """Explicit selection of the current stem artifact for one recipe contributor."""

    track_id: TrackId
    artifact_id: ArtifactId

    def __post_init__(self) -> None:
        require_instance(self.track_id, TrackId, "StemBinding.track_id")
        require_instance(self.artifact_id, ArtifactId, "StemBinding.artifact_id")


@dataclass(frozen=True)
class ExecutionRequest:
    """A requested execution of one decision's gain action, rebuilding the target stem and full mix.

    The current state is ``decision_request.state``. Construction checks field types and binding
    uniqueness only; all cross-record checks happen in ``preflight_execution``.
    """

    execution_id: ExecutionId
    decision_request: Situation
    decision: Decision
    policy: ActionAdmissionPolicy
    mix_recipe: MixRecipe
    stem_bindings: tuple[StemBinding, ...]
    current_mix_artifact_id: ArtifactId
    render_spec: RenderSpec
    processing_spec: ProcessingSpec
    workspace: ExecutionWorkspace
    candidate: CandidateIdentity

    def __post_init__(self) -> None:
        require_instance(self.execution_id, ExecutionId, "ExecutionRequest.execution_id")
        require_instance(self.decision_request, Situation, "ExecutionRequest.decision_request")
        require_instance(self.decision, Decision, "ExecutionRequest.decision")
        require_instance(self.policy, ActionAdmissionPolicy, "ExecutionRequest.policy")
        require_instance(self.mix_recipe, MixRecipe, "ExecutionRequest.mix_recipe")
        require_tuple_of(
            self.stem_bindings, StemBinding, "ExecutionRequest.stem_bindings", nonempty=True, distinct=False
        )
        tracks = [binding.track_id for binding in self.stem_bindings]
        artifacts = [binding.artifact_id for binding in self.stem_bindings]
        if len(set(tracks)) != len(tracks):
            raise ValueError("ExecutionRequest.stem_bindings must bind each track at most once")
        if len(set(artifacts)) != len(artifacts):
            raise ValueError("ExecutionRequest.stem_bindings must bind each artifact at most once")
        require_instance(self.current_mix_artifact_id, ArtifactId, "ExecutionRequest.current_mix_artifact_id")
        require_instance(self.render_spec, RenderSpec, "ExecutionRequest.render_spec")
        require_instance(self.processing_spec, ProcessingSpec, "ExecutionRequest.processing_spec")
        require_instance(self.workspace, ExecutionWorkspace, "ExecutionRequest.workspace")
        require_instance(self.candidate, CandidateIdentity, "ExecutionRequest.candidate")


class PreflightCode(Enum):
    ADMISSION_REJECTED = "admission_rejected"
    INVALID_WORKSPACE = "invalid_workspace"
    INVALID_CANDIDATE_IDENTITY = "invalid_candidate_identity"
    INVALID_RECIPE_BINDINGS = "invalid_recipe_bindings"
    INVALID_MIX = "invalid_mix"
    MISSING_GAIN_STATE = "missing_gain_state"
    INCOMPATIBLE_AUDIO = "incompatible_audio"
    UNSUPPORTED_DEPENDENCY = "unsupported_dependency"


@dataclass(frozen=True)
class PreflightRejected:
    """A typed preflight rejection; ``admission_rejection`` is present exactly for ADMISSION_REJECTED."""

    execution_id: ExecutionId
    code: PreflightCode
    explanation: str
    admission_rejection: RejectedAction | None

    def __post_init__(self) -> None:
        require_instance(self.execution_id, ExecutionId, "PreflightRejected.execution_id")
        require_instance(self.code, PreflightCode, "PreflightRejected.code")
        require_text(self.explanation, "PreflightRejected.explanation")
        if self.admission_rejection is not None:
            require_instance(self.admission_rejection, RejectedAction, "PreflightRejected.admission_rejection")
        if (self.code is PreflightCode.ADMISSION_REJECTED) != (self.admission_rejection is not None):
            raise ValueError("an admission rejection is required exactly for ADMISSION_REJECTED")


@dataclass(frozen=True)
class MetadataReady:
    """Metadata is consistent for processing; not executable authorization (see module docstring)."""

    plan: RenderPlan

    def __post_init__(self) -> None:
        require_instance(self.plan, RenderPlan, "MetadataReady.plan")


PreflightResult = MetadataReady | PreflightRejected
