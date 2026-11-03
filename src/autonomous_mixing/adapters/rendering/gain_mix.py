"""Gain/full-mix processor: the first ``AudioProcessor`` implementation (ADR-020 §5, ADR-026).

One instance is bound to one pre-allocated workspace. The caller (a future execution service) holds
the project state and the protected-location inventory, allocates the workspace and constructs the
processor; the plan's inputs are then looked up in ``workspace.request.protected_locations``, which
allocation requires to cover every current audio member and fixed baseline.

``process(plan)`` verifies every input it reads before anything is written, renders the fixed target
input at the requested absolute gain, sums the mix in recipe order with the new target stem in place
of the obsolete one, writes ``target.wav`` and then ``mix.wav`` through the exclusive WAV writer and
describes both as a ``ProcessedCandidate``. It composes existing components and adds no numerical
behaviour. The obsolete current target stem and the current mix are never opened.

Wiring errors (types, a plan that does not belong to the bound workspace or state, missing or
differing input locations, inputs incompatible with the render settings, an input location string equal
to a planned output location) raise plain ``ValueError``
before any file access. Expected failures return ``ProcessingFailure(FAILED, ...)``; files already
created are retained and named in the message. Unexpected exceptions propagate unchanged, and the
caller records the outcome as uncertain. Nothing is deleted, renamed, retried, normalized, clipped or
limited. Contributes to FR-08, FR-17, QR-01, QR-02 and OP-02 without completing them; no perceptual
claim follows.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np

from autonomous_mixing.adapters.rendering.dsp import apply_absolute_gain, sum_stems
from autonomous_mixing.adapters.rendering.integrity import (
    ArtifactVerificationError,
    VerifiedAudio,
    verify_resolved_wav_artifact,
)
from autonomous_mixing.adapters.rendering.locations import LocationResolutionError, ResolvedArtifactLocation
from autonomous_mixing.adapters.rendering.wav_output import WavOutputError, WrittenWav, write_wav_output
from autonomous_mixing.adapters.rendering.workspaces import AllocatedWorkspace
from autonomous_mixing.core.records.actions import ExecutionIssue, ExecutionStatus
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.identifiers import ArtifactId, ProjectId, VersionRef
from autonomous_mixing.core.records.rendering import (
    ArtifactProcessingEvidence,
    ProcessedCandidate,
    ProcessingFailure,
    ProcessingOutcome,
    ProcessingPlan,
)

# Fixed identity of this implementation; increase the version whenever numerical or file behaviour changes.
GAIN_MIX_PROCESSOR = VersionRef("autonomous_mixing.gain_mix_processor", "1")
# One fresh workspace per execution makes fixed names unique; they satisfy the writer's filename rule.
TARGET_FILENAME = "target.wav"
MIX_FILENAME = "mix.wav"

INPUT_VERIFICATION_FAILED = "input_verification_failed"
PROCESSING_FAILED = "processing_failed"
OUTPUT_FAILED = "output_failed"


def _require_limit(value: Any, field: str) -> int:
    # Exact built-in int: rejects bool, NumPy scalars and int subclasses before any comparison.
    if type(value) is not int or value < 1:
        raise ValueError(f"{field} must be a positive built-in int, got {value!r}")
    return value


@dataclass(frozen=True)
class ProcessingLimits:
    """Per-artifact size limits passed unchanged to every input verification and both writes.

    Both are required, with no defaults; they are resource bounds, not scientific thresholds. They do
    not bound peak memory, which grows with the number of stems held at once.
    """

    max_file_bytes: int
    max_decoded_bytes: int

    def __post_init__(self) -> None:
        _require_limit(self.max_file_bytes, "ProcessingLimits.max_file_bytes")
        _require_limit(self.max_decoded_bytes, "ProcessingLimits.max_decoded_bytes")


class _Failed(Exception):
    """Internal signal carrying the expected failure to return; never escapes ``process``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.issue = ExecutionIssue(code, message)


class GainMixProcessor:
    """Renders one plan's target stem and full mix into the bound workspace (structural ``AudioProcessor``)."""

    def __init__(self, workspace: AllocatedWorkspace, limits: ProcessingLimits) -> None:
        if not isinstance(workspace, AllocatedWorkspace):
            raise ValueError(f"workspace must be an AllocatedWorkspace, got {type(workspace).__name__}")
        if not isinstance(limits, ProcessingLimits):
            raise ValueError(f"limits must be a ProcessingLimits, got {type(limits).__name__}")
        self._workspace = workspace
        self._limits = limits

    @property
    def workspace(self) -> AllocatedWorkspace:
        return self._workspace

    @property
    def limits(self) -> ProcessingLimits:
        return self._limits

    def process(self, plan: ProcessingPlan) -> ProcessingOutcome:
        fixed_location, stem_locations = self._bind(plan)
        try:
            fixed = self._verify(fixed_location)
            others = {track: self._verify(location) for track, location in stem_locations.items()}
            target_samples, mix_samples = _render(plan, fixed, others)
            target_written = self._write(plan, TARGET_FILENAME, target_samples, retained=None)
            mix_written = self._write(plan, MIX_FILENAME, mix_samples, retained=target_written.path)
        except _Failed as failure:
            return ProcessingFailure(ExecutionStatus.FAILED, failure.issue)
        return _candidate(plan, self._workspace, (target_samples, target_written), (mix_samples, mix_written))

    # --- steps -------------------------------------------------------------------------------------

    def _bind(self, plan: ProcessingPlan) -> tuple[ResolvedArtifactLocation, dict[Any, ResolvedArtifactLocation]]:
        """Wiring checks without file access; returns the input locations to verify, in recipe order."""
        if not isinstance(plan, ProcessingPlan):
            raise ValueError(f"plan must be a ProcessingPlan, got {type(plan).__name__}")
        request = self._workspace.request
        if plan.workspace != request.workspace:
            raise ValueError("plan workspace differs from the bound allocated workspace")
        snapshot = request.state.snapshot
        if (snapshot.project_id, snapshot.revision) != (plan.project_id, plan.before_revision):
            raise ValueError("bound workspace state is not the plan's project and before revision")
        _require_compatible(plan)
        _require_distinct_output_locations(plan, request.workspace.directory_token)

        locations: dict[tuple[ProjectId, ArtifactId], ResolvedArtifactLocation] = {
            (record.artifact.project_id, record.artifact.artifact_id): record for record in request.protected_locations
        }

        def lookup(artifact: AudioArtifact) -> ResolvedArtifactLocation:
            record = locations.get((artifact.project_id, artifact.artifact_id))
            if record is None:
                raise ValueError(f"no protected location for input {artifact.artifact_id}")
            if record.artifact != artifact:
                raise ValueError(f"protected location for input {artifact.artifact_id} describes a different artifact")
            return record

        fixed = lookup(plan.fixed_target_input)
        others = {
            stem.source_track_ids[0]: lookup(stem)
            for stem in plan.current_stems
            if stem.artifact_id != plan.target_current_artifact_id
        }
        return fixed, others

    def _verify(self, location: ResolvedArtifactLocation) -> VerifiedAudio:
        try:
            return verify_resolved_wav_artifact(
                location,
                max_file_bytes=self._limits.max_file_bytes,
                max_decoded_bytes=self._limits.max_decoded_bytes,
            )
        except (ArtifactVerificationError, LocationResolutionError) as exc:
            raise _Failed(INPUT_VERIFICATION_FAILED, f"input {location.artifact.artifact_id}: {exc}") from None

    def _write(self, plan: ProcessingPlan, filename: str, samples: np.ndarray, *, retained: str | None) -> WrittenWav:
        try:
            return write_wav_output(
                self._workspace,
                filename,
                samples,
                render_spec=plan.render_spec,
                processing_spec=plan.processing_spec,
                max_file_bytes=self._limits.max_file_bytes,
                max_decoded_bytes=self._limits.max_decoded_bytes,
            )
        except WavOutputError as exc:
            kept = [path for path in (retained, exc.created_file) if path is not None]
            note = f"; retained files: {', '.join(kept)}" if kept else "; no file created"
            raise _Failed(f"{OUTPUT_FAILED}:{exc.code.value}", f"{filename}: {exc}{note}") from None


def _require_compatible(plan: ProcessingPlan) -> None:
    """Preflight guarantees this; a plan built without it is a wiring error, not a processing failure."""
    fixed = plan.fixed_target_input
    expected = (plan.render_spec.sample_rate_hz, plan.render_spec.channel_count, fixed.frame_count)
    for artifact in (fixed, *plan.current_stems):
        actual = (artifact.sample_rate_hz, artifact.channel_count, artifact.frame_count)
        if actual != expected or artifact.timeline_start_seconds != fixed.timeline_start_seconds:
            raise ValueError(f"input {artifact.artifact_id} is incompatible with the plan's render settings")
    if fixed.frame_count < 1:
        raise ValueError("plan inputs must contain at least one frame")


def _output_location(token: str, filename: str) -> str:
    # Relative to the output root, so an output resolves like any other input.
    return f"{token}/{filename}"


def _require_distinct_output_locations(plan: ProcessingPlan, token: str) -> None:
    """Rejects a layout the caller's lexical ``validate_processed_candidate`` check would reject after rendering.

    Location strings carry no root, so an input recorded as ``<token>/target.wav`` under its source root
    equals the planned output string although the files are distinct. This mirrors exactly the input
    locations that check compares (fixed target input and every current stem; not the current mix) and
    is a conservative supported-layout restriction, not root-aware location identity.
    """
    inputs = {plan.fixed_target_input.location} | {stem.location for stem in plan.current_stems}
    for filename in (TARGET_FILENAME, MIX_FILENAME):
        location = _output_location(token, filename)
        if location in inputs:
            raise ValueError(
                f"planned output location {location!r} equals an input location; unsupported layout "
                "because candidate validation compares locations lexically"
            )


def _render(
    plan: ProcessingPlan, fixed: VerifiedAudio, others: dict[Any, VerifiedAudio]
) -> tuple[np.ndarray, np.ndarray]:
    try:
        target = apply_absolute_gain(fixed.samples, plan.requested_gain_db)
        stems = tuple(
            target if track == plan.target_track_id else others[track].samples for track in plan.recipe.track_ids
        )
        return target, sum_stems(stems)
    except ValueError as exc:
        raise _Failed(PROCESSING_FAILED, str(exc)) from None


def _candidate(
    plan: ProcessingPlan,
    workspace: AllocatedWorkspace,
    target: tuple[np.ndarray, WrittenWav],
    mix: tuple[np.ndarray, WrittenWav],
) -> ProcessedCandidate:
    token = workspace.request.workspace.directory_token
    fixed = plan.fixed_target_input
    candidate = plan.candidate

    def artifact(artifact_id: ArtifactId, tracks: tuple, kind: ArtifactKind, filename: str, written: WrittenWav):
        return AudioArtifact(
            artifact_id,
            plan.project_id,
            candidate.revision,
            tracks,
            kind,
            _output_location(token, filename),
            fixed.timeline_start_seconds,
            written.sample_rate_hz,
            written.channel_count,
            written.frame_count,
            written.sha256,
            plan.render_spec.ref,
        )

    def evidence(artifact_id: ArtifactId, inputs: tuple[ArtifactId, ...], samples: np.ndarray):
        return ArtifactProcessingEvidence(
            artifact_id=artifact_id,
            verified_input_ids=inputs,
            input_hashes_verified=True,
            finite_samples=True,  # verifier, kernels and writer all reject non-finite samples
            peak_linear=float(np.max(np.abs(samples))),
            processor=GAIN_MIX_PROCESSOR,
            processing_spec=plan.processing_spec,
        )

    target_artifact = artifact(
        candidate.target_artifact_id, (plan.target_track_id,), ArtifactKind.STEM, TARGET_FILENAME, target[1]
    )
    mix_artifact = artifact(candidate.mix_artifact_id, plan.recipe.track_ids, ArtifactKind.MIX, MIX_FILENAME, mix[1])
    # The new target enters the mix as the in-memory array whose bit-exact readback the writer confirmed.
    non_target = tuple(s.artifact_id for s in plan.current_stems if s.artifact_id != plan.target_current_artifact_id)
    return ProcessedCandidate(
        target_artifact,
        mix_artifact,
        (
            evidence(target_artifact.artifact_id, (fixed.artifact_id,), target[0]),
            evidence(mix_artifact.artifact_id, (target_artifact.artifact_id, *non_target), mix[0]),
        ),
    )
