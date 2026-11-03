"""The rendering port's identities, plans, evidence and outcomes (metadata only)."""

import re
from dataclasses import dataclass

from autonomous_mixing.core.records.actions import ExecutionIssue, ExecutionStatus
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.identifiers import ArtifactId, ExecutionId, ProjectId, Revision, TrackId, VersionRef
from autonomous_mixing.core.records.invariants import require_finite, require_instance, require_text, require_tuple_of
from autonomous_mixing.core.records.mix import MixRecipe, ProcessingSpec
from autonomous_mixing.core.records.observation import RenderSpec

_DIRECTORY_TOKEN = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class ExecutionWorkspace:
    """Logical, application-issued output workspace assignment for one execution.

    ``root_key`` names configured storage, not a path; ``directory_token`` is a restricted logical
    name. Physical allocation, existence, collision and alias checks are deferred to the file adapter.
    """

    execution_id: ExecutionId
    root_key: str
    directory_token: str

    def __post_init__(self) -> None:
        require_instance(self.execution_id, ExecutionId, "ExecutionWorkspace.execution_id")
        require_text(self.root_key, "ExecutionWorkspace.root_key")
        if not isinstance(self.directory_token, str) or not _DIRECTORY_TOKEN.fullmatch(self.directory_token):
            raise ValueError("ExecutionWorkspace.directory_token must match [A-Za-z0-9_-]+")


@dataclass(frozen=True)
class CandidateIdentity:
    """Application-assigned identities for the candidate state and its new target stem and mix."""

    revision: Revision
    target_artifact_id: ArtifactId
    mix_artifact_id: ArtifactId

    def __post_init__(self) -> None:
        require_instance(self.revision, Revision, "CandidateIdentity.revision")
        require_instance(self.target_artifact_id, ArtifactId, "CandidateIdentity.target_artifact_id")
        require_instance(self.mix_artifact_id, ArtifactId, "CandidateIdentity.mix_artifact_id")
        if self.target_artifact_id == self.mix_artifact_id:
            raise ValueError("CandidateIdentity target and mix artifact IDs must differ")


@dataclass(frozen=True)
class ProcessingPlan:
    """Bounded, resolved input for a future audio processor (data only; no controller context).

    The processor renders ``fixed_target_input`` at ``requested_gain_db`` (absolute additional gain)
    into the new target stem, then sums the mix in ``recipe`` order from ``current_stems``,
    substituting the new target stem for ``target_current_artifact_id``. Current non-target stems
    are already rendered: their gains must not be applied again, and the obsolete current target
    stem must be neither attenuated nor summed alongside the new one.
    """

    execution_id: ExecutionId
    project_id: ProjectId
    before_revision: Revision
    candidate: CandidateIdentity
    target_track_id: TrackId
    fixed_target_input: AudioArtifact
    requested_gain_db: float
    current_stems: tuple[AudioArtifact, ...]
    target_current_artifact_id: ArtifactId
    current_mix_artifact_id: ArtifactId
    recipe: MixRecipe
    render_spec: RenderSpec
    processing_spec: ProcessingSpec
    workspace: ExecutionWorkspace

    def __post_init__(self) -> None:
        require_instance(self.execution_id, ExecutionId, "ProcessingPlan.execution_id")
        require_instance(self.project_id, ProjectId, "ProcessingPlan.project_id")
        require_instance(self.before_revision, Revision, "ProcessingPlan.before_revision")
        require_instance(self.candidate, CandidateIdentity, "ProcessingPlan.candidate")
        require_instance(self.target_track_id, TrackId, "ProcessingPlan.target_track_id")
        require_instance(self.fixed_target_input, AudioArtifact, "ProcessingPlan.fixed_target_input")
        require_finite(self.requested_gain_db, "ProcessingPlan.requested_gain_db")
        require_tuple_of(
            self.current_stems, AudioArtifact, "ProcessingPlan.current_stems", nonempty=True, distinct=True
        )
        require_instance(self.target_current_artifact_id, ArtifactId, "ProcessingPlan.target_current_artifact_id")
        require_instance(self.current_mix_artifact_id, ArtifactId, "ProcessingPlan.current_mix_artifact_id")
        require_instance(self.recipe, MixRecipe, "ProcessingPlan.recipe")
        require_instance(self.render_spec, RenderSpec, "ProcessingPlan.render_spec")
        require_instance(self.processing_spec, ProcessingSpec, "ProcessingPlan.processing_spec")
        require_instance(self.workspace, ExecutionWorkspace, "ProcessingPlan.workspace")

        if self.workspace.execution_id != self.execution_id:
            raise ValueError("ProcessingPlan workspace belongs to another execution")
        if self.candidate.revision == self.before_revision:
            raise ValueError("ProcessingPlan candidate revision must differ from the before revision")
        stem_tracks = tuple(stem.source_track_ids for stem in self.current_stems)
        if stem_tracks != tuple((track,) for track in self.recipe.track_ids) or any(
            stem.kind is not ArtifactKind.STEM for stem in self.current_stems
        ):
            raise ValueError("ProcessingPlan.current_stems must be one STEM per recipe track, in recipe order")
        if self.target_track_id not in self.recipe.track_ids:
            raise ValueError("ProcessingPlan target track is not a recipe contributor")
        target_stem = self.current_stems[self.recipe.track_ids.index(self.target_track_id)]
        if target_stem.artifact_id != self.target_current_artifact_id:
            raise ValueError("ProcessingPlan.target_current_artifact_id must be the target's current stem")
        fixed = self.fixed_target_input
        if fixed.kind is not ArtifactKind.STEM or fixed.source_track_ids != (self.target_track_id,):
            raise ValueError("ProcessingPlan.fixed_target_input must be a STEM of exactly the target track")
        used = {stem.artifact_id for stem in self.current_stems} | {fixed.artifact_id, self.current_mix_artifact_id}
        if {self.candidate.target_artifact_id, self.candidate.mix_artifact_id} & used:
            raise ValueError("ProcessingPlan candidate artifact IDs must not reuse input artifact IDs")


@dataclass(frozen=True)
class ArtifactProcessingEvidence:
    """Adapter claims about one produced artifact; recorded claims, not independent proof.

    ``processor`` identifies the implementation; ``processing_spec`` records the numerical and encoding
    configuration the adapter claims to have used. They are separate, and both are claims until the
    adapter is implemented and tested.
    """

    artifact_id: ArtifactId
    verified_input_ids: tuple[ArtifactId, ...]
    input_hashes_verified: bool
    finite_samples: bool
    peak_linear: float
    processor: VersionRef
    processing_spec: ProcessingSpec

    def __post_init__(self) -> None:
        require_instance(self.artifact_id, ArtifactId, "ArtifactProcessingEvidence.artifact_id")
        require_tuple_of(
            self.verified_input_ids,
            ArtifactId,
            "ArtifactProcessingEvidence.verified_input_ids",
            nonempty=True,
            distinct=True,
        )
        for name in ("input_hashes_verified", "finite_samples"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"ArtifactProcessingEvidence.{name} must be a bool")
        if require_finite(self.peak_linear, "ArtifactProcessingEvidence.peak_linear") < 0:
            raise ValueError("ArtifactProcessingEvidence.peak_linear must be non-negative")
        require_instance(self.processor, VersionRef, "ArtifactProcessingEvidence.processor")
        require_instance(self.processing_spec, ProcessingSpec, "ArtifactProcessingEvidence.processing_spec")


@dataclass(frozen=True)
class ProcessedCandidate:
    """A processor's successful outputs: new target stem, new full mix and their evidence."""

    target_artifact: AudioArtifact
    mix_artifact: AudioArtifact
    evidence: tuple[ArtifactProcessingEvidence, ...]

    def __post_init__(self) -> None:
        require_instance(self.target_artifact, AudioArtifact, "ProcessedCandidate.target_artifact")
        require_instance(self.mix_artifact, AudioArtifact, "ProcessedCandidate.mix_artifact")
        if self.target_artifact.artifact_id == self.mix_artifact.artifact_id:
            raise ValueError("ProcessedCandidate outputs must have distinct artifact IDs")
        require_tuple_of(
            self.evidence, ArtifactProcessingEvidence, "ProcessedCandidate.evidence", nonempty=True, distinct=False
        )
        ids = [record.artifact_id for record in self.evidence]
        if len(set(ids)) != len(ids):
            raise ValueError("ProcessedCandidate.evidence must have one record per artifact")


@dataclass(frozen=True)
class ProcessingFailure:
    """A processor outcome without registered candidate outputs: FAILED (established) or UNCERTAIN.

    Unlike a generic port failure, this keeps known and uncertain outcomes distinct once processing
    may have started. Temporary files may remain; no cleanup is claimed.
    """

    status: ExecutionStatus
    issue: ExecutionIssue

    def __post_init__(self) -> None:
        require_instance(self.status, ExecutionStatus, "ProcessingFailure.status")
        if self.status not in (ExecutionStatus.FAILED, ExecutionStatus.UNCERTAIN):
            raise ValueError("ProcessingFailure.status must be FAILED or UNCERTAIN")
        require_instance(self.issue, ExecutionIssue, "ProcessingFailure.issue")


ProcessingOutcome = ProcessedCandidate | ProcessingFailure
