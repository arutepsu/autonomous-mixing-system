"""Typed inputs and outputs of the coordinator's runtime collaborators (controlled-coordinator design, Task 1).

``RunRequest`` fixes everything one controlled run needs before any effect: the initial state, the existing
run configuration, admission policy, renderer descriptor, processing and mix definitions, stem and mix
selections, condition measurement bindings, the explicit analysis jobs, alignment declaration, logical artifact
roots and the decision retry policy. ``analysis_definitions`` lists every analysis job (each criterion's exact
definition once, plus any diagnostic-only job); it is never derived from the criteria. Construction checks
structure only: it cannot prove that a renderer matches its descriptor, that source bytes are intact or aligned,
or that a journal belongs to this run.

``DiagnosisRequest``, ``RunRenderRequest`` and ``ClockReading`` are the data the diagnosis source, outer
renderer and clock ports exchange. Records hold no callables, handles, credentials or hidden references.
"""

from dataclasses import dataclass
from datetime import datetime

from autonomous_mixing.core.records.actions import ExecutionIssue
from autonomous_mixing.core.records.admission import ActionAdmissionPolicy, StemBinding
from autonomous_mixing.core.records.analysis import AnalysisRequest
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.capabilities import RendererCapabilities
from autonomous_mixing.core.records.identifiers import ArtifactId, RunId, VersionRef
from autonomous_mixing.core.records.integrity import (
    ArtifactRootBinding,
    StateIntegrityRequest,
)
from autonomous_mixing.core.records.invariants import (
    require_count,
    require_finite,
    require_instance,
    require_text,
    require_tuple_of,
    require_utc,
)
from autonomous_mixing.core.records.mix import MixRecipe, ProcessingSpec
from autonomous_mixing.core.records.observation import (
    AnalysisScope,
    Observation,
    validate_observation_sources,
)
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.rendering import (
    ExecutionWorkspace,
    RenderedCandidate,
    RenderFailure,
    RenderPlan,
)
from autonomous_mixing.core.records.run import RunConfiguration
from autonomous_mixing.core.records.verification_assessment import (
    ConditionMeasurement,
    validate_condition_measurements,
)
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition


@dataclass(frozen=True)
class DecisionRetryPolicy:
    """Retries after the first decision call, per decision round (zero allowed).

    Only UNAVAILABLE failures marked retryable qualify, always subject to the run's time and call budgets.
    This record carries the policy; it implements no retry behavior.
    """

    reference: VersionRef
    max_retries: int

    def __post_init__(self) -> None:
        require_instance(self.reference, VersionRef, "DecisionRetryPolicy.reference")
        require_count(self.max_retries, "DecisionRetryPolicy.max_retries", minimum=0)


@dataclass(frozen=True)
class ClockReading:
    """One observation of elapsed monotonic time, wall-clock UTC time and cancellation.

    Progression across readings (no decrease) is checked by the coordinator, not here.
    """

    monotonic_seconds: float
    utc: datetime
    cancelled: bool

    def __post_init__(self) -> None:
        if require_finite(self.monotonic_seconds, "ClockReading.monotonic_seconds") < 0:
            raise ValueError("ClockReading.monotonic_seconds must be >= 0")
        require_utc(self.utc, "ClockReading.utc")
        if not isinstance(self.cancelled, bool):
            raise ValueError(f"ClockReading.cancelled must be a bool, got {self.cancelled!r}")


@dataclass(frozen=True)
class DiagnosisRequest:
    """The selected state, run scope and current observations a diagnosis source may use.

    Observations must have unique IDs and sources that are audio members of ``state`` (retained
    older-production members included). Observations broader than ``scope`` are permitted; scope
    containment of coverage and candidates is checked when the response arrives.
    """

    state: ProjectState
    scope: AnalysisScope
    observations: tuple[Observation, ...]

    def __post_init__(self) -> None:
        require_instance(self.state, ProjectState, "DiagnosisRequest.state")
        require_instance(self.scope, AnalysisScope, "DiagnosisRequest.scope")
        require_tuple_of(
            self.observations,
            Observation,
            "DiagnosisRequest.observations",
            nonempty=False,
            distinct=False,
        )
        self.scope.validate_against(self.state.snapshot)
        seen = set()
        for observation in self.observations:
            if observation.observation_id in seen:
                raise ValueError(f"duplicate observation ID in diagnosis request: {observation.observation_id}")
            seen.add(observation.observation_id)
            validate_observation_sources(
                observation,
                self.state.audio.artifacts,
                self.state.audio,
                self.state.snapshot,
            )


@dataclass(frozen=True)
class RunRenderRequest:
    """One outer render: a validated plan, the complete before state to protect and its protected artifacts.

    The plan's fixed input, current stems and current mix must resolve to exactly matching before-state
    metadata; every artifact the before state requires must be protected with equal metadata (further
    retained artifacts may be listed); candidate output IDs must not reuse a protected ID. Protected
    artifacts are runtime sources and outputs, never hidden reference audio. Structural checks only: no
    filesystem alias protection or action authorization.
    """

    plan: RenderPlan
    before: StateIntegrityRequest
    protected_artifacts: tuple[AudioArtifact, ...]
    protected_root_bindings: tuple[ArtifactRootBinding, ...]

    def __post_init__(self) -> None:
        require_instance(self.plan, RenderPlan, "RunRenderRequest.plan")
        require_instance(self.before, StateIntegrityRequest, "RunRenderRequest.before")
        require_tuple_of(
            self.protected_artifacts, AudioArtifact, "RunRenderRequest.protected_artifacts", nonempty=True,
            distinct=False,
        )  # fmt: skip
        protected: dict[ArtifactId, AudioArtifact] = {}
        for artifact in self.protected_artifacts:
            if artifact.artifact_id in protected:
                raise ValueError(f"duplicate protected artifact ID: {artifact.artifact_id}")
            protected[artifact.artifact_id] = artifact

        plan, state = self.plan, self.before.state
        if (state.project_id, state.revision) != (
            plan.project_id,
            plan.before_revision,
        ):
            raise ValueError(
                f"before state {state.project_id}@{state.revision} is not the plan's "
                f"{plan.project_id}@{plan.before_revision} revision"
            )
        required = {artifact.artifact_id: artifact for artifact in self.before.required_artifacts}
        if required.get(plan.fixed_target_input.artifact_id) != plan.fixed_target_input:
            raise ValueError("plan fixed target input does not match a required before-state artifact exactly")
        target_gain = next(
            (gain for gain in state.processing.track_gains if gain.track_id == plan.target_track_id),
            None,
        )
        if target_gain is None or target_gain.input_artifact != plan.fixed_target_input:
            raise ValueError("plan fixed target input must match the target's configured processing baseline")
        for stem in plan.current_stems:
            if state.audio.artifact_for(stem.artifact_id) != stem:
                raise ValueError(f"plan current stem {stem.artifact_id} does not match the before-state member exactly")
        mix = state.audio.artifact_for(plan.current_mix_artifact_id)
        if mix is None or mix.kind is not ArtifactKind.MIX or set(mix.source_track_ids) != set(plan.recipe.track_ids):
            raise ValueError(
                f"plan current mix {plan.current_mix_artifact_id} must be a current MIX of the recipe tracks"
            )
        unprotected = sorted(str(a) for a, artifact in required.items() if protected.get(a) != artifact)
        if unprotected:
            raise ValueError(f"required before-state artifacts are not protected with equal metadata: {unprotected}")
        outputs = {plan.candidate.target_artifact_id, plan.candidate.mix_artifact_id}
        reused = sorted(str(a) for a in outputs & protected.keys())
        if reused:
            raise ValueError(f"candidate output IDs reuse protected artifact IDs: {reused}")
        self._validate_protected_root_bindings(protected)

    def _validate_protected_root_bindings(self, protected: dict[ArtifactId, AudioArtifact]) -> None:
        """One explicit logical root per protected artifact; required artifacts keep their before-state root."""
        require_tuple_of(
            self.protected_root_bindings, ArtifactRootBinding, "RunRenderRequest.protected_root_bindings",
            nonempty=True, distinct=False,
        )  # fmt: skip
        keys: dict[ArtifactId, str] = {}
        for binding in self.protected_root_bindings:
            if binding.artifact_id in keys:
                raise ValueError(f"duplicate protected root binding for {binding.artifact_id}")
            keys[binding.artifact_id] = binding.root_key
        missing = sorted(str(a) for a in protected.keys() - keys.keys())
        extra = sorted(str(a) for a in keys.keys() - protected.keys())
        if missing or extra:
            raise ValueError(
                f"protected root bindings must name every protected artifact once (missing: {missing}, extra: {extra})"
            )
        before = {binding.artifact_id: binding.root_key for binding in self.before.bindings}
        conflicting = sorted(str(a) for a, key in before.items() if keys.get(a) != key)
        if conflicting:
            raise ValueError(f"protected root bindings differ from the before-state bindings for: {conflicting}")


@dataclass(frozen=True)
class RenderPreparationFailure:
    """Preparation failed before inner rendering began; ``created_workspace`` names a possibly retained allocation.

    No retry or cleanup is claimed. Failures after rendering began use ``RenderFailure``.
    """

    issue: ExecutionIssue
    created_workspace: ExecutionWorkspace | None

    def __post_init__(self) -> None:
        require_instance(self.issue, ExecutionIssue, "RenderPreparationFailure.issue")
        if self.created_workspace is not None:
            require_instance(
                self.created_workspace,
                ExecutionWorkspace,
                "RenderPreparationFailure.created_workspace",
            )


RunRenderOutcome = RenderedCandidate | RenderFailure | RenderPreparationFailure


@dataclass(frozen=True)
class RunRequest:
    """Everything one controlled run is fixed to before any effect. Structure only (see module docstring)."""

    run_id: RunId
    initial_state: ProjectState
    configuration: RunConfiguration
    policy: ActionAdmissionPolicy
    renderer_capabilities: RendererCapabilities
    processing_spec: ProcessingSpec
    recipe: MixRecipe
    stem_bindings: tuple[StemBinding, ...]
    mix_artifact_id: ArtifactId
    measurements: tuple[ConditionMeasurement, ...]
    analysis_definitions: tuple[MeasurementDefinition, ...]
    alignment: VersionRef
    root_bindings: tuple[ArtifactRootBinding, ...]
    output_root_key: str
    retry_policy: DecisionRetryPolicy

    def __post_init__(self) -> None:
        require_instance(self.run_id, RunId, "RunRequest.run_id")
        require_instance(self.initial_state, ProjectState, "RunRequest.initial_state")
        require_instance(self.configuration, RunConfiguration, "RunRequest.configuration")
        require_instance(self.policy, ActionAdmissionPolicy, "RunRequest.policy")
        require_instance(
            self.renderer_capabilities,
            RendererCapabilities,
            "RunRequest.renderer_capabilities",
        )
        require_instance(self.processing_spec, ProcessingSpec, "RunRequest.processing_spec")
        require_instance(self.recipe, MixRecipe, "RunRequest.recipe")
        require_tuple_of(
            self.stem_bindings,
            StemBinding,
            "RunRequest.stem_bindings",
            nonempty=True,
            distinct=False,
        )
        require_instance(self.mix_artifact_id, ArtifactId, "RunRequest.mix_artifact_id")
        require_tuple_of(
            self.analysis_definitions, MeasurementDefinition, "RunRequest.analysis_definitions", nonempty=True,
            distinct=False,
        )  # fmt: skip
        require_instance(self.alignment, VersionRef, "RunRequest.alignment")
        require_tuple_of(
            self.root_bindings,
            ArtifactRootBinding,
            "RunRequest.root_bindings",
            nonempty=False,
            distinct=False,
        )
        # A logical configured root key for every generated workspace and output, never a path or a
        # value inferred from an input binding; it may equal an input root key.
        require_text(self.output_root_key, "RunRequest.output_root_key")
        require_instance(self.retry_policy, DecisionRetryPolicy, "RunRequest.retry_policy")

        state = self.initial_state
        self.configuration.validate_against(state.snapshot)
        if self.policy.project_id != state.project_id:
            raise ValueError(f"policy names project {self.policy.project_id}, not {state.project_id}")
        self._validate_recipe_bindings()
        StateIntegrityRequest(state, self.root_bindings)  # current audio and fixed inputs, exactly once each
        validate_condition_measurements(self.configuration.criteria, self.measurements)
        self._validate_analysis_jobs()

    def _validate_analysis_jobs(self) -> None:
        """The initial analysis request must be constructible, and every criterion's definition must be a job.

        The initial request binds the current-audio subset of ``root_bindings``; extra (diagnostic-only) jobs
        are allowed, and malformed, conflicting or incomplete jobs fail here, before any run effect.
        """
        state, configuration = self.initial_state, self.configuration
        current = state.audio.artifact_ids
        AnalysisRequest(
            state,
            configuration.scope,
            configuration.analysis_spec,
            self.analysis_definitions,
            tuple(binding for binding in self.root_bindings if binding.artifact_id in current),
        )
        jobs = {(d.selector.metric, d.selector.scope): d for d in self.analysis_definitions}
        for measurement in self.measurements:
            selector = measurement.definition.selector
            if jobs.get((selector.metric, selector.scope)) != measurement.definition:
                raise ValueError(
                    f"definition for condition {measurement.condition_id} is not exactly one of the analysis definitions"
                )

    def _validate_recipe_bindings(self) -> None:
        state, recipe = self.initial_state, self.recipe
        recipe_tracks = set(recipe.track_ids)
        if recipe_tracks != {track.track_id for track in state.snapshot.tracks}:
            raise ValueError("recipe contributors must be exactly the initial snapshot's tracks")
        tracks = [binding.track_id for binding in self.stem_bindings]
        artifacts = [binding.artifact_id for binding in self.stem_bindings]
        if len(set(tracks)) != len(tracks) or len(set(artifacts)) != len(artifacts):
            raise ValueError("stem bindings must have unique track and artifact IDs")
        if set(tracks) != recipe_tracks:
            raise ValueError("stem bindings must bind exactly the recipe tracks")
        for binding in self.stem_bindings:
            stem = state.audio.artifact_for(binding.artifact_id)
            if stem is None or stem.kind is not ArtifactKind.STEM or stem.source_track_ids != (binding.track_id,):
                raise ValueError(
                    f"binding for {binding.track_id} must name a current stem of exactly that track, "
                    f"not {binding.artifact_id}"
                )
        mix = state.audio.artifact_for(self.mix_artifact_id)
        if mix is None or mix.kind is not ArtifactKind.MIX or set(mix.source_track_ids) != recipe_tracks:
            raise ValueError(f"selected mix {self.mix_artifact_id} must be a current MIX of exactly the recipe tracks")
