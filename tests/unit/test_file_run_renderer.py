"""Tests for the file-backed RunRenderer (plan docs: 2026-11-10 file-run-renderer, owner-approved design).

Fixtures are tiny analytical float64 arrays written as WAV/DOUBLE files into ``tmp_path``; they are
unit-test signals, not musical material or evaluation data, and track identities are generic. Tests
marked "native" perform real resolution, allocation, rendering and integrity checks on this platform.
Tests marked "injected" replace one owner-module name (resolver, allocator, processor or writer) to show
error mapping or object identity that cannot be produced reliably otherwise. The coordinator test at the
end uses the real renderer and integrity inspector with labelled controlled analysis, diagnosis and
decision collaborators: it shows orchestration plus real I/O, not scientific improvement.
"""

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import autonomous_mixing.adapters.rendering.gain_mix as gain_mix_module
import autonomous_mixing.adapters.rendering.run_renderer as renderer_module
from autonomous_mixing.adapters.audio_io.integrity import check_resolved_wav_integrity
from autonomous_mixing.adapters.audio_io.locations import (
    LocationResolutionError,
    LocationResolutionFailureCode,
    resolve_artifact_location,
)
from autonomous_mixing.adapters.rendering.dsp import apply_absolute_gain, sum_stems
from autonomous_mixing.adapters.rendering.gain_mix import GainMixProcessor, ProcessingLimits
from autonomous_mixing.adapters.rendering.run_renderer import FileRunRenderer
from autonomous_mixing.adapters.rendering.wav_output import WavOutputError, WavOutputFailureCode
from autonomous_mixing.adapters.rendering.workspaces import (
    WorkspaceAllocationError,
    WorkspaceAllocationFailureCode,
    allocate_workspace,
)
from autonomous_mixing.core.loop.transitions import derive_candidate_project_state
from autonomous_mixing.core.ports.coordinator_runtime import RunRenderer, validate_run_render_response
from autonomous_mixing.core.records.actions import (
    Action,
    ExecutionIssue,
    ExecutionReceipt,
    ExecutionStatus,
    SetTrackGain,
)
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.coordinator_runtime import RenderPreparationFailure, RunRenderRequest
from autonomous_mixing.core.records.identifiers import (
    ActionId,
    ArtifactId,
    CheckpointId,
    ExecutionId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.integrity import ArtifactRootBinding, StateIntegrityRequest
from autonomous_mixing.core.records.mix import (
    GainRule,
    LevelPolicy,
    MixRecipe,
    NumericalPrecision,
    OutputContainer,
    ProcessingSpec,
    SampleEncoding,
    SummationOrder,
    SummingPolicy,
)
from autonomous_mixing.core.records.observation import RenderSpec
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)
from autonomous_mixing.core.records.rendering import (
    CandidateIdentity,
    ExecutionWorkspace,
    RenderedCandidate,
    RenderFailure,
    RenderPlan,
)

RATE = 48000
UTC = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
RENDER_REF = VersionRef("render", "1")
RENDER_SPEC = RenderSpec(RENDER_REF, RATE, 2)
LIMITS = ProcessingLimits(1 << 20, 1 << 20)
PROCESSING = ProcessingSpec(
    VersionRef("gain-mix-processing", "1"), NumericalPrecision.FLOAT64, GainRule.DB_AMPLITUDE,
    SummationOrder.RECIPE_SEQUENTIAL, LevelPolicy.PRESERVE, OutputContainer.WAV, SampleEncoding.FLOAT64,
)  # fmt: skip
RECIPE = MixRecipe(VersionRef("recipe", "1"), (TrackId("t1"), TrackId("t2")), SummingPolicy.SAMPLE_SUM)
T1, T2 = TrackId("t1"), TrackId("t2")
P1 = ProjectId("p1")
# Labelled analytical sample values (stereo, four frames).
FIXED_T1 = np.array([[0.5, -0.25], [1.5, 0.0], [-0.125, 0.75], [0.25, -0.5]])
FIXED_T2 = np.array([[0.1, 0.2], [-0.3, 0.4], [0.0, -0.0], [2.0, -2.0]])
RETAINED_SAMPLES = np.array([[9.0, 9.0], [9.0, 9.0], [9.0, 9.0], [9.0, 9.0]])


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Project:
    """Files across three input roots plus a retained-output root and a separate output root (native)."""

    def __init__(self, tmp: Path):
        self.dirs = {key: tmp / key for key in ("fixed", "current", "retained", "renders")}
        for path in self.dirs.values():
            path.mkdir()
        self.roots = {key: str(path) for key, path in self.dirs.items()}
        self.in_t1 = self.write("fixed", "in-t1", "r0", (T1,), FIXED_T1)
        self.in_t2 = self.write("fixed", "in-t2", "r0", (T2,), FIXED_T2)
        self.s1 = self.write("current", "s1", "r1", (T1,), FIXED_T1)  # rendered at gain 0
        self.s2 = self.write("current", "s2", "r1", (T2,), FIXED_T2)
        self.mix = self.write("current", "mix", "r1", (T1, T2), sum_stems((FIXED_T1, FIXED_T2)), ArtifactKind.MIX)
        self.retained = self.write("retained", "old-output", "r0", (T2,), RETAINED_SAMPLES)
        snapshot = ProjectSnapshot(P1, Revision("r1"), UTC, (Track(T1, "track 1"), Track(T2, "track 2")))
        processing = ProjectProcessingState(P1, Revision("r1"), (TrackGainState(T1, self.in_t1, 0.0),
                                                                 TrackGainState(T2, self.in_t2, 0.0)))  # fmt: skip
        self.state = ProjectState(snapshot, processing, ArtifactCheckpoint(CheckpointId("cp-r1"), P1, Revision("r1"),
                                                                           (self.s1, self.s2, self.mix)))  # fmt: skip
        self.keys = {"in-t1": "fixed", "in-t2": "fixed", "s1": "current", "s2": "current", "mix": "current",
                     "old-output": "retained"}  # fmt: skip

    def write(self, root, aid, revision, tracks, samples, kind=ArtifactKind.STEM) -> AudioArtifact:
        path = self.dirs[root] / f"{aid}.wav"
        sf.write(str(path), np.asarray(samples, dtype=np.float64), RATE, format="WAV", subtype="DOUBLE")
        return AudioArtifact(ArtifactId(aid), P1, Revision(revision), tracks, kind, f"{aid}.wav", 0.0, RATE, 2,
                             len(samples), _sha(path), RENDER_REF)  # fmt: skip

    def source_hashes(self) -> dict[str, str]:
        return {str(p): _sha(p) for key in ("fixed", "current", "retained") for p in self.dirs[key].iterdir()}

    def bindings(self, state, extra=()) -> tuple[ArtifactRootBinding, ...]:
        ids = dict.fromkeys(
            a.artifact_id for a in (*state.audio.artifacts, *(g.input_artifact for g in state.processing.track_gains))
        )
        return tuple(ArtifactRootBinding(i, self.keys.get(str(i), "renders")) for i in (*ids, *extra))

    def plan(self, state, *, gain_db, token, target_stem, mix_id, revision, target_id, mix_out_id) -> RenderPlan:
        stems = tuple(state.audio.artifact_for(ArtifactId(a)) for a in (target_stem, "s2"))
        return RenderPlan(
            ExecutionId(token), P1, state.revision, CandidateIdentity(Revision(revision), ArtifactId(target_id),
                                                                      ArtifactId(mix_out_id)),
            T1, self.in_t1, gain_db, stems, ArtifactId(target_stem), ArtifactId(mix_id), RECIPE, RENDER_SPEC,
            PROCESSING, ExecutionWorkspace(ExecutionId(token), "renders", token),
        )  # fmt: skip

    def request(self, *, gain_db=-6.0, token="e1", with_retained=True, state=None, plan=None) -> RunRenderRequest:
        state = state or self.state
        plan = plan or self.plan(state, gain_db=gain_db, token=token, target_stem="s1", mix_id="mix",
                                 revision="r2", target_id="t1-r2", mix_out_id="mix-r2")  # fmt: skip
        before = StateIntegrityRequest(state, self.bindings(state))
        protected = (*before.required_artifacts, *((self.retained,) if with_retained else ()))
        bindings = (*before.bindings, *((ArtifactRootBinding(self.retained.artifact_id, "retained"),)
                                        if with_retained else ()))  # fmt: skip
        return RunRenderRequest(plan, before, protected, bindings)


@pytest.fixture
def project(tmp_path) -> Project:
    return Project(tmp_path)


def renderer(roots, limits=LIMITS) -> FileRunRenderer:
    return FileRunRenderer(roots, limits=limits)


class Spy:
    """Wraps an owner-module function, records calls and can raise a prepared exception."""

    def __init__(self, function, *, raise_on=None):
        self.function, self.calls, self.raise_on = function, [], raise_on

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raise_on is not None:
            error = self.raise_on(*args, **kwargs)
            if error is not None:
                raise error
        return self.function(*args, **kwargs)


@pytest.fixture
def spies(monkeypatch):
    resolve = Spy(resolve_artifact_location)
    allocate = Spy(allocate_workspace)
    processors = []

    class RecordingProcessor(GainMixProcessor):
        def __init__(self, workspace, limits):
            super().__init__(workspace, limits)
            self.processed = []
            processors.append(self)

        def process(self, plan):
            self.processed.append(plan)
            return super().process(plan)

    monkeypatch.setattr(renderer_module, "resolve_artifact_location", resolve)
    monkeypatch.setattr(renderer_module, "allocate_workspace", allocate)
    monkeypatch.setattr(renderer_module, "GainMixProcessor", RecordingProcessor)
    return resolve, allocate, processors


# --- construction ------------------------------------------------------------------------------------


def test_the_renderer_satisfies_the_run_renderer_port(project):
    port: RunRenderer = renderer(project.roots)
    assert callable(port.process)


@pytest.mark.parametrize(
    "roots",
    [[("renders", "/x")], {"": "/x"}, {" ": "/x"}, {1: "/x"}, {"renders": ""}, {"renders": None}],
    ids=["not-mapping", "empty-key", "blank-key", "int-key", "empty-path", "none-path"],
)
def test_constructor_validates_roots(roots):
    with pytest.raises(ValueError):
        renderer(roots)


@pytest.mark.parametrize("limits", [None, (1, 1), "limits"])
def test_constructor_requires_processing_limits(limits):
    with pytest.raises(ValueError):
        FileRunRenderer({"renders": "/x"}, limits=limits)


def test_construction_touches_no_filesystem_and_copies_its_configuration(tmp_path, project, spies):  # native
    missing = tmp_path / "absent"
    FileRunRenderer({"renders": str(missing)}, limits=LIMITS)
    assert not missing.exists()
    roots = dict(project.roots)
    adapter = renderer(roots)
    roots["renders"] = str(tmp_path / "elsewhere")
    del roots["fixed"]
    outcome = adapter.process(project.request())
    assert isinstance(outcome, RenderedCandidate)
    assert outcome.target_artifact.location.startswith("e1/") and (project.dirs["renders"] / "e1").is_dir()


def test_wrong_request_type_is_rejected_before_any_filesystem_operation(project, spies):
    resolve, allocate, processors = spies
    with pytest.raises(ValueError):
        renderer(project.roots).process(project.request().plan)
    assert (resolve.calls, allocate.calls, processors) == ([], [], [])


# --- configured keys are all checked before preparation ----------------------------------------------


@pytest.mark.parametrize("missing", ["renders", "current", "retained"], ids=["output", "input", "last-extra"])
def test_a_missing_root_key_fails_before_resolution_or_allocation(project, spies, missing):
    resolve, allocate, processors = spies
    roots = {k: v for k, v in project.roots.items() if k != missing}
    outcome = renderer(roots).process(project.request())
    assert isinstance(outcome, RenderPreparationFailure) and outcome.created_workspace is None
    assert outcome.issue.code == "root_not_configured" and missing in outcome.issue.message
    assert (resolve.calls, allocate.calls, processors) == ([], [], [])
    assert list(project.dirs["renders"].iterdir()) == []


def test_empty_root_configuration_is_a_typed_preparation_failure(project, spies):
    outcome = renderer({}).process(project.request())
    assert isinstance(outcome, RenderPreparationFailure) and outcome.issue.code == "root_not_configured"
    assert spies[0].calls == []


# --- resolution and allocation -----------------------------------------------------------------------


def test_every_protected_artifact_is_resolved_in_order_and_reaches_allocation(project, spies):  # native
    resolve, allocate, processors = spies
    request = project.request()
    outcome = renderer(project.roots).process(request)
    assert isinstance(outcome, RenderedCandidate)
    resolved = [args[0] for args, _ in resolve.calls]
    assert resolved == list(request.protected_artifacts)
    assert all(a is b for a, b in zip(resolved, request.protected_artifacts, strict=True))  # original objects
    roots_used = [kwargs["root"] for _, kwargs in resolve.calls]
    assert roots_used[-1] == project.roots["retained"] and roots_used[0] == project.roots["current"]
    ((allocation_request,), _) = allocate.calls[0]
    assert allocation_request.workspace is request.plan.workspace
    assert (allocation_request.root.root_key, allocation_request.root.path) == ("renders", project.roots["renders"])
    assert allocation_request.state is request.before.state
    assert [loc.artifact for loc in allocation_request.protected_locations] == list(request.protected_artifacts)
    assert len(processors) == 1 and processors[0].processed == [request.plan]
    assert processors[0].workspace.request is allocation_request and processors[0].limits is LIMITS


def test_missing_protected_file_is_a_resolution_failure(project, spies):  # native
    (project.dirs["current"] / "s2.wav").unlink()
    outcome = renderer(project.roots).process(project.request())
    assert isinstance(outcome, RenderPreparationFailure) and outcome.created_workspace is None
    assert outcome.issue.code == "location_resolution:not_found" and "s2" in outcome.issue.message
    assert spies[1].calls == [] and spies[2] == []


def test_protected_location_outside_its_root_is_a_resolution_failure(project, spies):  # native
    import dataclasses

    outside = project.dirs["retained"].parent / "outside.wav"
    sf.write(str(outside), RETAINED_SAMPLES, RATE, format="WAV", subtype="DOUBLE")
    escaped = dataclasses.replace(project.retained, location=str(outside), content_hash=_sha(outside))
    base = project.request()
    request = RunRenderRequest(base.plan, base.before, (*base.before.required_artifacts, escaped),
                               base.protected_root_bindings)  # fmt: skip
    outcome = renderer(project.roots).process(request)
    assert outcome.issue.code == "location_resolution:outside_root" and spies[1].calls == []


def test_a_retained_file_inside_the_planned_workspace_blocks_allocation(project, spies):  # native
    import dataclasses

    inside = project.dirs["renders"] / "e1"
    inside.mkdir()
    (inside / "old-output.wav").write_bytes((project.dirs["retained"] / "old-output.wav").read_bytes())
    moved = dataclasses.replace(project.retained, location="e1/old-output.wav")
    base = project.request()
    bindings = (*base.before.bindings, ArtifactRootBinding(moved.artifact_id, "renders"))
    request = RunRenderRequest(base.plan, base.before, (*base.before.required_artifacts, moved), bindings)
    outcome = renderer(project.roots).process(request)
    assert isinstance(outcome, RenderPreparationFailure)
    assert outcome.issue.code == "workspace_allocation:protected_overlap" and outcome.created_workspace is None
    assert spies[2] == []  # the processor never ran


def test_a_repeated_workspace_assignment_collides_and_never_reruns_processing(project, spies):  # native
    adapter = renderer(project.roots)
    first = adapter.process(project.request())
    assert isinstance(first, RenderedCandidate)
    target = project.dirs["renders"] / "e1" / "target.wav"
    before = _sha(target)
    second = adapter.process(project.request())
    assert isinstance(second, RenderPreparationFailure)
    assert second.issue.code == "workspace_allocation:collision" and second.created_workspace is None
    assert len(spies[2]) == 1 and _sha(target) == before  # nothing reused, rewritten or retried


def test_allocation_failure_after_creation_reports_the_workspace_and_its_physical_path(project, monkeypatch):
    # injected: the real allocation runs, then a post-creation failure is reported as the allocator would.
    created = {}

    def allocate_then_fail(request):
        workspace = allocate_workspace(request)
        created["path"] = workspace.directory
        raise WorkspaceAllocationError(WorkspaceAllocationFailureCode.WORKSPACE_CHANGED, "labelled post-creation change",
                                       created_directory=workspace.directory)  # fmt: skip

    processors = []
    monkeypatch.setattr(renderer_module, "allocate_workspace", allocate_then_fail)
    monkeypatch.setattr(renderer_module, "GainMixProcessor", lambda *a: processors.append(a))
    request = project.request()
    outcome = renderer(project.roots).process(request)
    assert isinstance(outcome, RenderPreparationFailure)
    assert outcome.issue.code == "workspace_allocation:workspace_changed"
    assert outcome.created_workspace is request.plan.workspace
    assert created["path"] in outcome.issue.message and Path(created["path"]).is_dir()  # retained, not deleted
    assert processors == []


@pytest.mark.parametrize(
    "error",
    [OSError("unrelated"), MemoryError(), ValueError("wiring"), RuntimeError("programming")],
    ids=["oserror", "memory", "valueerror", "runtime"],
)
@pytest.mark.parametrize("stage", ["resolve", "allocate"])
def test_unexpected_preparation_errors_propagate_unchanged(project, monkeypatch, stage, error):  # injected
    def raising(*args, **kwargs):
        raise error

    monkeypatch.setattr(renderer_module, "resolve_artifact_location" if stage == "resolve" else "allocate_workspace",
                        raising)  # fmt: skip
    with pytest.raises(type(error)) as caught:
        renderer(project.roots).process(project.request())
    assert caught.value is error


def test_location_error_raised_during_allocation_is_not_relabelled(project, monkeypatch):  # injected
    error = LocationResolutionError(LocationResolutionFailureCode.NOT_FOUND, "labelled")

    def raising(request):
        raise error

    monkeypatch.setattr(renderer_module, "allocate_workspace", raising)
    with pytest.raises(LocationResolutionError) as caught:  # only WorkspaceAllocationError is mapped here
        renderer(project.roots).process(project.request())
    assert caught.value is error


# --- the inner processor -----------------------------------------------------------------------------


class ScriptedProcessor:
    instances = []

    def __init__(self, workspace, limits):
        self.workspace, self.limits, self.plans = workspace, limits, []
        ScriptedProcessor.instances.append(self)

    def process(self, plan):
        self.plans.append(plan)
        return ScriptedProcessor.outcome(plan)


@pytest.mark.parametrize(
    "result",
    [RenderFailure(ExecutionStatus.FAILED, ExecutionIssue("output_failed:collision", "labelled")),
     RenderFailure(ExecutionStatus.UNCERTAIN, ExecutionIssue("labelled", "labelled"))],
)  # fmt: skip
def test_inner_failures_are_returned_as_the_same_objects(project, monkeypatch, result):  # injected
    ScriptedProcessor.instances, ScriptedProcessor.outcome = [], staticmethod(lambda plan: result)
    monkeypatch.setattr(renderer_module, "GainMixProcessor", ScriptedProcessor)
    assert renderer(project.roots).process(project.request()) is result
    assert len(ScriptedProcessor.instances) == 1 and len(ScriptedProcessor.instances[0].plans) == 1


def test_inner_success_object_and_exceptions_pass_through(project, monkeypatch):  # injected
    real = {}

    def real_then_record(plan):
        processor = GainMixProcessor(ScriptedProcessor.instances[-1].workspace, LIMITS)
        real["outcome"] = processor.process(plan)
        return real["outcome"]

    ScriptedProcessor.instances, ScriptedProcessor.outcome = [], staticmethod(real_then_record)
    monkeypatch.setattr(renderer_module, "GainMixProcessor", ScriptedProcessor)
    adapter = renderer(project.roots)
    assert adapter.process(project.request()) is real["outcome"]
    error = RuntimeError("labelled inner programming error")

    def raising(plan):
        raise error

    ScriptedProcessor.outcome = staticmethod(raising)
    with pytest.raises(RuntimeError) as caught:
        adapter.process(project.request(token="e2"))
    assert caught.value is error and len(ScriptedProcessor.instances) == 2  # one processor per call


def test_partial_output_failure_keeps_the_written_target_and_the_inner_classification(project, monkeypatch):
    # injected writer failure for the mix only; the target file is a real first output.
    real_write = gain_mix_module.write_wav_output

    def failing_mix(workspace, filename, samples, **kwargs):
        if filename == gain_mix_module.MIX_FILENAME:
            raise WavOutputError(WavOutputFailureCode.IO_FAILED, "labelled mix write failure")
        return real_write(workspace, filename, samples, **kwargs)

    monkeypatch.setattr(gain_mix_module, "write_wav_output", failing_mix)
    outcome = renderer(project.roots).process(project.request())
    assert isinstance(outcome, RenderFailure) and outcome.status is ExecutionStatus.FAILED
    assert outcome.issue.code == "output_failed:io_failed"
    target = project.dirs["renders"] / "e1" / "target.wav"
    assert target.is_file() and str(target) in outcome.issue.message  # retained, reported, not deleted
    assert not (project.dirs["renders"] / "e1" / "mix.wav").exists()


# --- real rendering ----------------------------------------------------------------------------------


def _checked(artifact: AudioArtifact, root: str) -> np.ndarray:
    location = resolve_artifact_location(artifact, root=root)
    return np.asarray(check_resolved_wav_integrity(location, max_file_bytes=1 << 20, max_decoded_bytes=1 << 20).samples)


def test_real_render_writes_verified_outputs_under_the_output_root_and_preserves_sources(project):  # native
    before = project.source_hashes()
    request = project.request()
    outcome = renderer(project.roots).process(request)
    assert isinstance(outcome, RenderedCandidate)
    validate_run_render_response(request, outcome)
    target, mix = outcome.target_artifact, outcome.mix_artifact
    assert (target.location, mix.location) == ("e1/target.wav", "e1/mix.wav")
    expected_target = apply_absolute_gain(FIXED_T1, -6.0)
    np.testing.assert_array_equal(_checked(target, project.roots["renders"]), expected_target)
    np.testing.assert_array_equal(_checked(mix, project.roots["renders"]), sum_stems((expected_target, FIXED_T2)))
    assert project.source_hashes() == before  # inputs and the retained file are unchanged
    evidence = {e.artifact_id: e for e in outcome.evidence}
    assert set(evidence[target.artifact_id].verified_input_ids) == {project.in_t1.artifact_id}
    assert set(evidence[mix.artifact_id].verified_input_ids) == {target.artifact_id, project.s2.artifact_id}


def test_two_successive_edits_use_the_fixed_input_and_the_latest_bindings(project):  # native
    adapter = renderer(project.roots)
    first_request = project.request(gain_db=-6.0, token="e1")
    first = adapter.process(first_request)
    plan = first_request.plan
    receipt = ExecutionReceipt(plan.execution_id, ActionId("a1"), P1, plan.before_revision,
                               ExecutionStatus.CANDIDATE_READY, plan.candidate.revision,
                               (first.target_artifact, first.mix_artifact), (ArtifactId("s1"), ArtifactId("mix")), None)  # fmt: skip
    action = Action(ActionId("a1"), P1, plan.before_revision, SetTrackGain(T1, -6.0))
    state2 = derive_candidate_project_state(project.state, receipt, action, CheckpointId("cp-r2"), UTC)
    before = project.source_hashes()
    first_outputs = {p: _sha(p) for p in (project.dirs["renders"] / "e1").iterdir()}
    plan2 = project.plan(state2, gain_db=-3.0, token="e2", target_stem="t1-r2", mix_id="mix-r2", revision="r3",
                         target_id="t1-r3", mix_out_id="mix-r3")  # fmt: skip
    request2 = project.request(state=state2, plan=plan2)
    assert {a.artifact_id for a in request2.protected_artifacts} >= {
        ArtifactId("t1-r2"), ArtifactId("mix-r2"), ArtifactId("in-t1"), ArtifactId("old-output")}  # fmt: skip
    second = adapter.process(request2)
    assert isinstance(second, RenderedCandidate) and plan2.fixed_target_input == project.in_t1
    expected = apply_absolute_gain(FIXED_T1, -3.0)  # absolute gain on the fixed baseline, never compounded
    np.testing.assert_array_equal(_checked(second.target_artifact, project.roots["renders"]), expected)
    np.testing.assert_array_equal(
        _checked(second.mix_artifact, project.roots["renders"]), sum_stems((expected, FIXED_T2))
    )
    evidence = {e.artifact_id: e for e in second.evidence}
    assert set(evidence[second.mix_artifact.artifact_id].verified_input_ids) == {
        second.target_artifact.artifact_id, project.s2.artifact_id}  # fmt: skip
    assert project.source_hashes() == before and {p: _sha(p) for p in first_outputs} == first_outputs


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need privileges on Windows")
def test_a_protected_symbolic_link_out_of_its_root_fails_closed(project):  # native (POSIX)
    import dataclasses

    link = project.dirs["retained"] / "link.wav"
    link.symlink_to(project.dirs["current"] / "s1.wav")
    linked = dataclasses.replace(project.retained, location="link.wav", content_hash=_sha(link))
    base = project.request()
    request = RunRenderRequest(base.plan, base.before, (*base.before.required_artifacts, linked),
                               base.protected_root_bindings)  # fmt: skip
    outcome = renderer(project.roots).process(request)
    assert outcome.issue.code == "location_resolution:outside_root"


# --- one controlled coordinator run with the real renderer and integrity inspector -------------------


def test_coordinator_completes_one_correction_with_real_rendering_and_integrity(project):
    # native I/O for rendering and integrity; analysis, diagnosis and decision are labelled controlled doubles.
    from test_coordinator import Clock, Decider, Diagnosis, Identities, propose
    from test_run_recording_contracts import _configuration, _run_request

    from autonomous_mixing.adapters.journal.run_memory import InMemoryRunJournal
    from autonomous_mixing.adapters.rendering.state_integrity import FileStateIntegrityInspector
    from autonomous_mixing.core.loop.coordinator import run
    from autonomous_mixing.core.records.acceptance import AcceptedReference
    from autonomous_mixing.core.records.admission import StemBinding
    from autonomous_mixing.core.records.analysis import AnalysisResponse, deduplicate_analysis_definitions
    from autonomous_mixing.core.records.feedback import ActionFeedbackDisposition, DecisionFeedback
    from autonomous_mixing.core.records.identifiers import ConditionId, CriteriaId, ObservationId
    from autonomous_mixing.core.records.observation import (
        AnalysisScope,
        AnalysisSpec,
        MeasuredScalar,
        Observation,
        TimeRange,
    )
    from autonomous_mixing.core.records.results import Success
    from autonomous_mixing.core.records.run import FinalProjectState, RunOutcome
    from autonomous_mixing.core.records.run_trace import DispatchIntent, OperationKind
    from autonomous_mixing.core.records.termination import TerminationReason
    from autonomous_mixing.core.records.verification import (
        AcceptanceCriteria,
        BaselineReference,
        ComparisonOperator,
        DeltaGuard,
        ImprovementCondition,
        ImprovementDirection,
        MetricSelector,
        TargetCondition,
    )
    from autonomous_mixing.core.records.verification_assessment import ConditionMeasurement
    from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition, SourceRequirement

    # Labelled controlled metric over the real mix files' exact span (four frames); not a scientific method.
    metric, spec, analyzer_ref = VersionRef("metric-a", "1"), VersionRef("analysis-spec", "1"), VersionRef("a", "0")
    scope = AnalysisScope((T1, T2), TimeRange(0.0, 4 / RATE))
    selector = MetricSelector(metric, scope)
    criteria = AcceptanceCriteria(
        CriteriaId("criteria-a"), "1",
        (TargetCondition(ConditionId("tg"), selector, ComparisonOperator.LE, MeasuredScalar(0.0, "unit-a")),),
        (ImprovementCondition(ConditionId("im"), selector, ImprovementDirection.LOWER, MeasuredScalar(1.0, "unit-a")),),
        (DeltaGuard(ConditionId("dl"), selector, BaselineReference.INITIAL, MeasuredScalar(2.0, "unit-a")),),
    )  # fmt: skip
    source = SourceRequirement(ArtifactKind.MIX, (T1, T2), RENDER_REF, RATE, 2, 0.0, 4)
    measurements = tuple(
        ConditionMeasurement(ConditionId(c), MeasurementDefinition(selector, "unit-a", spec, analyzer_ref, VersionRef(
            "alignment", "1"), (source,)))
        for c in ("tg", "im", "dl")
    )  # fmt: skip
    configuration = _configuration(scope=scope, analysis_spec=AnalysisSpec(spec, (metric,)), render_spec=RENDER_SPEC,
                                   criteria=criteria)  # fmt: skip
    request = _run_request(
        initial_state=project.state, configuration=configuration, processing_spec=PROCESSING, recipe=RECIPE,
        stem_bindings=(StemBinding(T1, ArtifactId("s1")), StemBinding(T2, ArtifactId("s2"))),
        mix_artifact_id=ArtifactId("mix"), measurements=measurements,
        analysis_definitions=deduplicate_analysis_definitions(tuple(m.definition for m in measurements)),
        root_bindings=project.bindings(project.state),
        output_root_key="renders",
    )  # fmt: skip

    values = iter((1.5, 0.0))  # labelled initial and candidate values: the controlled response, not a measurement

    class ControlledAnalyzer:
        def __init__(self):
            self.requests = []

        def analyze(self, analysis_request):
            self.requests.append(analysis_request)
            state = analysis_request.state
            mix = next(a for a in state.audio.artifacts if a.kind is ArtifactKind.MIX)
            observation = Observation(ObservationId(f"obs-{state.revision}"), metric, (mix.artifact_id,), scope,
                                      MeasuredScalar(next(values), "unit-a"), spec, analyzer_ref, UTC)  # fmt: skip
            return Success(AnalysisResponse((observation,)))

    analyzer = ControlledAnalyzer()
    decider = Decider(propose(-6.0))
    journal = InMemoryRunJournal(request.run_id, AcceptedReference(project.state, 0))
    before = project.source_hashes()
    report = run(
        request, analyzer=analyzer, diagnosis_source=Diagnosis(), decision_maker=decider,
        renderer=FileRunRenderer(project.roots, limits=LIMITS),
        integrity_inspector=FileStateIntegrityInspector(project.roots, max_file_bytes=1 << 20,
                                                        max_decoded_bytes=1 << 20),
        journal=journal, clock=Clock(), identities=Identities(),
    )  # fmt: skip

    assert (report.final_run.outcome, report.final_run.termination_reason) == (
        RunOutcome.SUCCEEDED, TerminationReason.CANDIDATE_CRITERIA_MET)  # fmt: skip
    assert report.final_run.final_project_state is FinalProjectState.ACCEPTED_EDITS_RETAINED
    (commit,) = report.trace.acceptances
    assert report.authoritative == commit.accepted == report.verified and commit.accepted.sequence == 1
    operations = [e.payload.operation for e in report.trace.events if isinstance(e.payload, DispatchIntent)]
    k = OperationKind
    assert operations == [k.INSPECT_STATE, k.ANALYZE, k.DIAGNOSE, k.DECIDE, k.RENDER, k.ANALYZE]
    usage = report.final_run.usage
    assert (usage.decision_calls, usage.attempts, usage.render_calls, usage.recovery_operations) == (1, 1, 1, 0)
    (feedback,) = [e.payload for e in report.trace.events if isinstance(e.payload, DecisionFeedback)]
    assert feedback.outcome.disposition is ActionFeedbackDisposition.ACCEPTED

    candidate = commit.accepted.state
    gains = {g.track_id: g for g in candidate.processing.track_gains}
    assert gains[T1].input_artifact == project.in_t1 and gains[T1].gain_db == -6.0  # fixed input kept
    target = next(a for a in candidate.audio.artifacts if a.kind is ArtifactKind.STEM and a.source_track_ids == (T1,))
    mix = next(a for a in candidate.audio.artifacts if a.kind is ArtifactKind.MIX)
    expected = apply_absolute_gain(FIXED_T1, -6.0)
    np.testing.assert_array_equal(_checked(target, project.roots["renders"]), expected)  # real, verified files
    np.testing.assert_array_equal(_checked(mix, project.roots["renders"]), sum_stems((expected, FIXED_T2)))
    assert Path(project.roots["renders"], target.location).is_file()
    assert project.source_hashes() == before  # sources and the retained file unchanged
    assert [r.state for r in analyzer.requests] == [project.state, candidate]
    # Analysis-jobs plan: each analysis names exactly the current members, and every logical root it names locates
    # the real file; the unaccepted candidate's outputs were bound to the assigned workspace root, not guessed.
    for analysis_request in analyzer.requests:
        state, bound = analysis_request.state, {b.artifact_id: b.root_key for b in analysis_request.root_bindings}
        assert list(bound) == [a.artifact_id for a in state.audio.artifacts]
        for artifact in state.audio.artifacts:
            assert Path(project.roots[bound[artifact.artifact_id]], artifact.location).is_file()
        assert analysis_request.definitions == request.analysis_definitions
    candidate_bound = {b.artifact_id: b.root_key for b in analyzer.requests[1].root_bindings}
    assert candidate_bound[target.artifact_id] == candidate_bound[mix.artifact_id] == "renders"
