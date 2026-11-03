"""Tests for the gain/full-mix processor adapter (ADR-026).

Fixtures are tiny analytical float64 arrays written as WAV/DOUBLE files into ``tmp_path``; they are
unit-test signals, not evaluation data, and no project media is used. Workspaces are allocated for real
with ``allocate_workspace``. The only double is a patched kernel used to show that unexpected
exceptions propagate unchanged.
"""

import dataclasses
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import autonomous_mixing.infrastructure.processing.gain_mix as gain_mix
from autonomous_mixing.application.execution import (
    AudioProcessor,
    CandidateIdentity,
    ExecutionWorkspace,
    ProcessedCandidate,
    ProcessingFailure,
    ProcessingOutcome,
    ProcessingPlan,
    validate_processed_candidate,
)
from autonomous_mixing.domain.action import ExecutionStatus
from autonomous_mixing.domain.observation import RenderSpec
from autonomous_mixing.domain.project import (
    ArtifactCheckpoint,
    ArtifactKind,
    AudioArtifact,
    GainRule,
    LevelPolicy,
    MixRecipe,
    NumericalPrecision,
    OutputContainer,
    ProcessingSpec,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    SampleEncoding,
    SummationOrder,
    SummingPolicy,
    Track,
    TrackGainState,
)
from autonomous_mixing.domain.shared import (
    ArtifactId,
    CheckpointId,
    ExecutionId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)
from autonomous_mixing.infrastructure.audio import apply_absolute_gain, sum_stems, verify_resolved_wav_artifact
from autonomous_mixing.infrastructure.filesystem import resolve_artifact_location
from autonomous_mixing.infrastructure.processing import GAIN_MIX_PROCESSOR, GainMixProcessor, ProcessingLimits
from autonomous_mixing.infrastructure.workspaces import (
    AllocatedWorkspace,
    WorkspaceAllocationRequest,
    WorkspaceRoot,
    allocate_workspace,
)

RATE = 48000
ROOT_KEY = "outputs"
TOKEN = "e1_run-01"
GAIN_DB = -4.0
LIMITS = ProcessingLimits(1 << 20, 1 << 20)
RENDER_REF = VersionRef("render", "1")
SPEC = ProcessingSpec(
    VersionRef("gain-mix-processing", "1"),
    NumericalPrecision.FLOAT64,
    GainRule.DB_AMPLITUDE,
    SummationOrder.RECIPE_SEQUENTIAL,
    LevelPolicy.PRESERVE,
    OutputContainer.WAV,
    SampleEncoding.FLOAT64,
)
TINY = 5e-324
# Analytical values per track: signed zeros, a subnormal, ordinary and above-full-scale finite values.
STEREO = {
    "a-t1": np.array([[0.0, -0.0], [0.5, -0.25], [1.5, TINY], [-0.125, 0.75]]),
    "a-t2": np.array([[-0.0, 0.0], [0.25, 0.5], [-2.0, 0.1], [TINY, -0.3]]),  # fixed target baseline
    "c-t2": np.array([[9.0, 9.0], [9.0, 9.0], [9.0, 9.0], [9.0, 9.0]]),  # obsolete current target stem
    "a-t3": np.array([[0.2, -0.2], [0.0, 0.0], [3.0, -3.0], [0.01, 0.02]]),
    "c-mix": np.array([[7.0, 7.0], [7.0, 7.0], [7.0, 7.0], [7.0, 7.0]]),
}
TRACK_OF = {"a-t1": "t1", "a-t2": "t2", "c-t2": "t2", "a-t3": "t3"}
REVISION_OF = {"a-t1": "r1", "a-t2": "r0", "c-t2": "r2", "a-t3": "r1", "c-mix": "r2"}
RECIPE = ("t1", "t2", "t3")


@dataclasses.dataclass
class Setup:
    source: Path
    output: Path
    artifacts: dict[str, AudioArtifact]
    state: ProjectState
    plan: ProcessingPlan
    workspace: AllocatedWorkspace
    samples: dict[str, np.ndarray]

    @property
    def directory(self) -> Path:
        return Path(self.workspace.directory)

    def processor(self, limits=LIMITS) -> GainMixProcessor:
        return GainMixProcessor(self.workspace, limits)


def _write_source(path: Path, samples: np.ndarray) -> str:
    sf.write(str(path), samples, RATE, format="WAV", subtype="DOUBLE")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build(tmp_path: Path, samples=None, *, gain_db=GAIN_DB, token=TOKEN, locations=None) -> Setup:
    samples = {k: np.asarray(v, dtype=np.float64) for k, v in (samples or STEREO).items()}
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir(exist_ok=True)
    output.mkdir(exist_ok=True)
    artifacts = {}
    for aid, data in samples.items():
        location = (locations or {}).get(aid, f"{aid}.wav")
        path = source / location
        path.parent.mkdir(parents=True, exist_ok=True)
        digest = _write_source(path, data) if not path.exists() else hashlib.sha256(path.read_bytes()).hexdigest()
        tracks = RECIPE if aid == "c-mix" else (TRACK_OF[aid],)
        artifacts[aid] = AudioArtifact(
            ArtifactId(aid),
            ProjectId("p1"),
            Revision(REVISION_OF[aid]),
            tuple(TrackId(t) for t in tracks),
            ArtifactKind.MIX if aid == "c-mix" else ArtifactKind.STEM,
            location,
            0.0,
            RATE,
            data.shape[1],
            data.shape[0],
            digest,
            RENDER_REF,
        )
    project, revision = ProjectId("p1"), Revision("r2")
    snapshot = ProjectSnapshot(
        project, revision, datetime(2026, 1, 1, tzinfo=timezone.utc), tuple(Track(TrackId(t), t) for t in RECIPE)
    )
    gains = (
        TrackGainState(TrackId("t1"), artifacts["a-t1"], 0.0),
        TrackGainState(TrackId("t2"), artifacts["a-t2"], -3.0),
        TrackGainState(TrackId("t3"), artifacts["a-t3"], 0.0),
    )
    current = (artifacts["a-t1"], artifacts["c-t2"], artifacts["a-t3"])
    state = ProjectState(
        snapshot,
        ProjectProcessingState(project, revision, gains),
        ArtifactCheckpoint(CheckpointId("c1"), project, revision, (*current, artifacts["c-mix"])),
    )
    channels = samples["a-t2"].shape[1]
    execution_workspace = ExecutionWorkspace(ExecutionId("e1"), ROOT_KEY, token)
    plan = ProcessingPlan(
        execution_id=ExecutionId("e1"),
        project_id=project,
        before_revision=revision,
        candidate=CandidateIdentity(Revision("r3"), ArtifactId("c3-t2"), ArtifactId("c3-mix")),
        target_track_id=TrackId("t2"),
        fixed_target_input=artifacts["a-t2"],
        requested_gain_db=gain_db,
        current_stems=current,
        target_current_artifact_id=ArtifactId("c-t2"),
        current_mix_artifact_id=ArtifactId("c-mix"),
        recipe=MixRecipe(
            VersionRef("full-mix-recipe", "1"), tuple(TrackId(t) for t in RECIPE), SummingPolicy.SAMPLE_SUM
        ),
        render_spec=RenderSpec(RENDER_REF, RATE, channels),
        processing_spec=SPEC,
        workspace=execution_workspace,
    )
    protected = tuple(resolve_artifact_location(a, root=str(source)) for a in artifacts.values())
    request = WorkspaceAllocationRequest(execution_workspace, WorkspaceRoot(ROOT_KEY, str(output)), state, protected)
    return Setup(source, output, artifacts, state, plan, allocate_workspace(request), samples)


def _expected(setup: Setup, gain_db=GAIN_DB) -> tuple[np.ndarray, np.ndarray]:
    target = apply_absolute_gain(setup.samples["a-t2"], gain_db)
    return target, sum_stems((setup.samples["a-t1"], target, setup.samples["a-t3"]))


def _bits(array: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(array, dtype=np.float64).view(np.uint64)


def _read(path: Path) -> np.ndarray:
    data, rate = sf.read(str(path), dtype="float64", always_2d=True)
    assert rate == RATE
    return data


def _source_hashes(setup: Setup) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(setup.source.iterdir())}


def _failure(outcome: ProcessingOutcome, code: str) -> ProcessingFailure:
    assert isinstance(outcome, ProcessingFailure)
    assert outcome.status is ExecutionStatus.FAILED
    assert outcome.issue.code == code
    return outcome


# --- construction ------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, -1, True, 1.0, np.int64(5), None, "1"], ids=repr)
@pytest.mark.parametrize("field", ["max_file_bytes", "max_decoded_bytes"])
def test_limits_require_positive_builtin_ints(field, value):
    fields = {"max_file_bytes": 1, "max_decoded_bytes": 1, field: value}
    with pytest.raises(ValueError, match=field):
        ProcessingLimits(**fields)


def test_constructor_rejects_wrong_types_without_filesystem_access(tmp_path):
    setup = _build(tmp_path)
    with pytest.raises(ValueError, match="AllocatedWorkspace"):
        GainMixProcessor(setup.workspace.request, LIMITS)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="ProcessingLimits"):
        GainMixProcessor(setup.workspace, (1, 1))  # type: ignore[arg-type]
    processor = setup.processor()
    assert (processor.workspace, processor.limits) == (setup.workspace, LIMITS)


def test_processor_satisfies_the_port_structurally(tmp_path):
    setup = _build(tmp_path)
    processor: AudioProcessor = setup.processor()  # static check: structural Protocol conformance
    assert isinstance(processor.process(setup.plan), ProcessedCandidate)


# --- semantics ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("channels", [2, 1])
def test_outputs_equal_the_kernels_bit_for_bit_and_pass_contract_validation(tmp_path, channels):
    samples = {k: v[:, :channels] for k, v in STEREO.items()}
    setup = _build(tmp_path, samples)
    before = _source_hashes(setup)

    outcome = setup.processor().process(setup.plan)

    assert isinstance(outcome, ProcessedCandidate)
    validate_processed_candidate(setup.plan, outcome)
    expected_target, expected_mix = _expected(setup)
    assert sorted(p.name for p in setup.directory.iterdir()) == ["mix.wav", "target.wav"]
    assert np.array_equal(_bits(_read(setup.directory / "target.wav")), _bits(expected_target))
    assert np.array_equal(_bits(_read(setup.directory / "mix.wav")), _bits(expected_mix))
    assert _source_hashes(setup) == before  # QR-01: sources unchanged


def test_non_target_stems_are_not_gained_again_and_obsolete_target_is_excluded(tmp_path):
    setup = _build(tmp_path)
    setup.processor().process(setup.plan)
    mix = _read(setup.directory / "mix.wav")
    target = apply_absolute_gain(setup.samples["a-t2"], GAIN_DB)
    # Removing the new target and the untouched non-target stems leaves exactly zero: no 9.0 (obsolete) or 7.0 (mix).
    residual = mix - target - setup.samples["a-t1"] - setup.samples["a-t3"]
    assert np.allclose(residual, 0.0, atol=1e-12)


def test_output_records_describe_the_written_files(tmp_path):
    setup = _build(tmp_path)
    outcome = setup.processor().process(setup.plan)
    assert isinstance(outcome, ProcessedCandidate)
    target, mix = outcome.target_artifact, outcome.mix_artifact
    assert (target.location, mix.location) == (f"{TOKEN}/target.wav", f"{TOKEN}/mix.wav")
    assert target.source_track_ids == (TrackId("t2"),) and target.kind is ArtifactKind.STEM
    assert mix.source_track_ids == tuple(TrackId(t) for t in RECIPE) and mix.kind is ArtifactKind.MIX
    for artifact, name in ((target, "target.wav"), (mix, "mix.wav")):
        assert artifact.revision == Revision("r3") and artifact.render_spec == RENDER_REF
        assert artifact.content_hash == hashlib.sha256((setup.directory / name).read_bytes()).hexdigest()
        # The output resolves against the output root like any other input and verifies against its record.
        verified = verify_resolved_wav_artifact(
            resolve_artifact_location(artifact, root=str(setup.output)),
            max_file_bytes=1 << 20,
            max_decoded_bytes=1 << 20,
        )
        assert verified.samples.shape == (artifact.frame_count, artifact.channel_count)

    evidence = {record.artifact_id: record for record in outcome.evidence}
    expected_target, expected_mix = _expected(setup)
    assert evidence[target.artifact_id].verified_input_ids == (ArtifactId("a-t2"),)
    assert evidence[mix.artifact_id].verified_input_ids == (target.artifact_id, ArtifactId("a-t1"), ArtifactId("a-t3"))
    assert evidence[target.artifact_id].peak_linear == float(np.max(np.abs(expected_target)))
    assert evidence[mix.artifact_id].peak_linear == float(np.max(np.abs(expected_mix)))
    for record in evidence.values():
        assert record.processor == GAIN_MIX_PROCESSOR
        assert record.processing_spec == SPEC
        assert record.input_hashes_verified and record.finite_samples


def test_obsolete_target_stem_and_current_mix_are_never_opened(tmp_path):
    setup = _build(tmp_path)
    # Corrupt both after allocation: any read would fail its hash check.
    (setup.source / "c-t2.wav").write_bytes(b"not a wav")
    (setup.source / "c-mix.wav").write_bytes(b"not a wav")
    assert isinstance(setup.processor().process(setup.plan), ProcessedCandidate)


def test_repeating_a_plan_in_a_fresh_workspace_gives_identical_samples(tmp_path):
    first = _build(tmp_path)
    first.processor().process(first.plan)
    second = _build(tmp_path, token="e1_run-02")
    assert isinstance(second.processor().process(second.plan), ProcessedCandidate)
    for name in ("target.wav", "mix.wav"):
        assert np.array_equal(_bits(_read(first.directory / name)), _bits(_read(second.directory / name)))
    # Hashes are not compared: libsndfile's PEAK chunk carries a timestamp (ADR-025).


# --- wiring errors: plain ValueError before any file access -----------------------------------------


def _assert_rejected_without_output(setup: Setup, processor: GainMixProcessor, plan, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        processor.process(plan)
    assert list(setup.directory.iterdir()) == []


def test_non_plan_is_rejected(tmp_path):
    setup = _build(tmp_path)
    _assert_rejected_without_output(setup, setup.processor(), object(), "ProcessingPlan")


def test_plan_for_another_workspace_is_rejected(tmp_path):
    setup = _build(tmp_path)
    plan = dataclasses.replace(setup.plan, workspace=ExecutionWorkspace(ExecutionId("e1"), ROOT_KEY, "other"))
    _assert_rejected_without_output(setup, setup.processor(), plan, "workspace")


def test_plan_for_another_before_revision_is_rejected(tmp_path):
    setup = _build(tmp_path)
    plan = dataclasses.replace(setup.plan, before_revision=Revision("r9"))
    _assert_rejected_without_output(setup, setup.processor(), plan, "before revision")


def _rebound(setup: Setup, protected) -> GainMixProcessor:
    request = dataclasses.replace(setup.workspace.request, protected_locations=tuple(protected))
    return GainMixProcessor(dataclasses.replace(setup.workspace, request=request), LIMITS)


def test_missing_input_location_is_rejected(tmp_path):
    setup = _build(tmp_path)
    protected = [r for r in setup.workspace.request.protected_locations if r.artifact.artifact_id != ArtifactId("a-t3")]
    _assert_rejected_without_output(
        setup, _rebound(setup, protected), setup.plan, "no protected location for input a-t3"
    )


def test_location_describing_a_different_artifact_is_rejected(tmp_path):
    setup = _build(tmp_path)
    protected = [
        dataclasses.replace(r, artifact=dataclasses.replace(r.artifact, content_hash="f" * 64))
        if r.artifact.artifact_id == ArtifactId("a-t2")
        else r
        for r in setup.workspace.request.protected_locations
    ]
    _assert_rejected_without_output(setup, _rebound(setup, protected), setup.plan, "different artifact")


def test_inputs_incompatible_with_render_settings_are_rejected(tmp_path):
    setup = _build(tmp_path)
    plan = dataclasses.replace(setup.plan, render_spec=RenderSpec(RENDER_REF, 44100, 2))
    _assert_rejected_without_output(setup, setup.processor(), plan, "incompatible")


# Equal relative strings under different roots: the files are distinct, but the caller's lexical
# validate_processed_candidate would reject the rendered result (MD-6, Codex architecture audit 2026-11-03).
@pytest.mark.parametrize(
    "aid, filename",
    [("a-t2", "target.wav"), ("a-t1", "mix.wav"), ("c-t2", "target.wav")],
    ids=["fixed-target-input", "non-target-stem", "obsolete-target-stem"],
)
def test_input_location_equal_to_a_planned_output_location_is_rejected_before_any_file_access(tmp_path, aid, filename):
    setup = _build(tmp_path, locations={aid: f"{TOKEN}/{filename}"})
    # Any read would fail its hash check, so a rejection here cannot come from verification.
    (setup.source / TOKEN / filename).write_bytes(b"not a wav")
    _assert_rejected_without_output(setup, setup.processor(), setup.plan, "equals an input location")


@pytest.mark.parametrize(
    "aid, location",
    [("a-t2", "other/target.wav"), ("a-t1", f"{TOKEN}/target.WAV"), ("c-mix", f"{TOKEN}/mix.wav")],
    ids=["other-directory", "different-spelling", "current-mix-not-compared"],
)
def test_layouts_the_lexical_check_accepts_still_process_and_validate(tmp_path, aid, location):
    setup = _build(tmp_path, locations={aid: location})
    outcome = setup.processor().process(setup.plan)
    assert isinstance(outcome, ProcessedCandidate)
    validate_processed_candidate(setup.plan, outcome)


# --- expected failures ---------------------------------------------------------------------------------


def test_input_hash_mismatch_fails_before_any_output(tmp_path):
    setup = _build(tmp_path)
    _write_source(setup.source / "a-t3.wav", STEREO["a-t3"] * 2)
    failure = _failure(setup.processor().process(setup.plan), "input_verification_failed")
    assert "a-t3" in failure.issue.message and "hash_mismatch" in failure.issue.message
    assert list(setup.directory.iterdir()) == []


def test_input_over_the_size_limit_fails_before_any_output(tmp_path):
    setup = _build(tmp_path)
    failure = _failure(setup.processor(ProcessingLimits(16, 1 << 20)).process(setup.plan), "input_verification_failed")
    assert "resource_limit" in failure.issue.message
    assert list(setup.directory.iterdir()) == []


def test_unrepresentable_gain_result_fails_before_any_output(tmp_path):
    samples = dict(STEREO, **{"a-t2": np.array([[1e308, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]])})
    setup = _build(tmp_path, samples, gain_db=10.0)
    _failure(setup.processor().process(setup.plan), "processing_failed")
    assert list(setup.directory.iterdir()) == []


def test_unrepresentable_mix_sum_fails_before_any_output(tmp_path):
    big = np.full((4, 2), 1e308)
    setup = _build(tmp_path, dict(STEREO, **{"a-t1": big, "a-t3": big}), gain_db=0.0)
    failure = _failure(setup.processor().process(setup.plan), "processing_failed")
    assert "overflows" in failure.issue.message
    assert list(setup.directory.iterdir()) == []


def test_existing_target_file_collides_and_nothing_else_is_written(tmp_path):
    setup = _build(tmp_path)
    (setup.directory / "target.wav").write_bytes(b"keep")
    failure = _failure(setup.processor().process(setup.plan), "output_failed:collision")
    assert "no file created" in failure.issue.message
    assert (setup.directory / "target.wav").read_bytes() == b"keep"
    assert not (setup.directory / "mix.wav").exists()


def test_mix_write_failure_retains_and_names_the_written_target(tmp_path):
    setup = _build(tmp_path)
    (setup.directory / "mix.wav").write_bytes(b"keep")
    failure = _failure(setup.processor().process(setup.plan), "output_failed:collision")
    target_path = str(setup.directory / "target.wav")
    assert f"retained files: {target_path}" in failure.issue.message
    assert np.array_equal(_bits(_read(setup.directory / "target.wav")), _bits(_expected(setup)[0]))
    assert (setup.directory / "mix.wav").read_bytes() == b"keep"


def test_unexpected_exceptions_propagate_unchanged(tmp_path, monkeypatch):
    setup = _build(tmp_path)
    error = RuntimeError("kernel bug")

    def broken(stems):
        raise error

    monkeypatch.setattr(gain_mix, "sum_stems", broken)
    with pytest.raises(RuntimeError) as caught:
        setup.processor().process(setup.plan)
    assert caught.value is error
    assert list(setup.directory.iterdir()) == []
