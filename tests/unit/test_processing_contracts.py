"""Focused tests for the processing port records and structural response validation.

The processors below are in-memory test doubles; no audio is processed, read or written. Evidence
flags are adapter claims: these tests check structural consistency only, not file integrity.
"""

import dataclasses
import math

import pytest

from autonomous_mixing.core.ports.rendering import AudioProcessor, validate_processed_candidate
from autonomous_mixing.core.records.actions import ExecutionIssue, ExecutionStatus
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.identifiers import ArtifactId, ExecutionId, ProjectId, Revision, TrackId, VersionRef
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
from autonomous_mixing.core.records.rendering import (
    ArtifactProcessingEvidence,
    CandidateIdentity,
    ExecutionWorkspace,
    ProcessedCandidate,
    ProcessingFailure,
    ProcessingOutcome,
    ProcessingPlan,
)

FRAMES = 48000 * 60
PROCESSOR = VersionRef("gain-mix-processor", "0.1")


def _processing_spec(version: str = "1") -> ProcessingSpec:
    """Explicit test configuration; no global default specification exists."""
    return ProcessingSpec(
        VersionRef("gain-mix-processing", version),
        NumericalPrecision.FLOAT64,
        GainRule.DB_AMPLITUDE,
        SummationOrder.RECIPE_SEQUENTIAL,
        LevelPolicy.PRESERVE,
        OutputContainer.WAV,
        SampleEncoding.FLOAT64,
    )


SPEC = _processing_spec()


def _artifact(aid, tracks, revision, *, kind=ArtifactKind.STEM, location=None, **changes) -> AudioArtifact:
    artifact = AudioArtifact(
        ArtifactId(aid),
        ProjectId("p1"),
        Revision(revision),
        tuple(TrackId(t) for t in tracks),
        kind,
        location or f"store/{aid}.wav",
        0.0,
        48000,
        2,
        FRAMES,
        "b" * 64,
        VersionRef("render", "1"),
    )
    return dataclasses.replace(artifact, **changes) if changes else artifact


A_T1, C_T2, A_T3 = _artifact("a-t1", ("t1",), "r1"), _artifact("c-t2", ("t2",), "r2"), _artifact("a-t3", ("t3",), "r1")
A_T2_BASELINE = _artifact("a-t2", ("t2",), "r0")


def _plan() -> ProcessingPlan:
    return ProcessingPlan(
        execution_id=ExecutionId("e1"),
        project_id=ProjectId("p1"),
        before_revision=Revision("r2"),
        candidate=CandidateIdentity(Revision("r3"), ArtifactId("c3-t2"), ArtifactId("c3-mix")),
        target_track_id=TrackId("t2"),
        fixed_target_input=A_T2_BASELINE,
        requested_gain_db=-4.0,
        current_stems=(A_T1, C_T2, A_T3),
        target_current_artifact_id=ArtifactId("c-t2"),
        current_mix_artifact_id=ArtifactId("c-mix"),
        recipe=MixRecipe(
            VersionRef("full-mix-recipe", "1"), (TrackId("t1"), TrackId("t2"), TrackId("t3")), SummingPolicy.SAMPLE_SUM
        ),
        render_spec=RenderSpec(VersionRef("render", "1"), 48000, 2),
        processing_spec=SPEC,
        workspace=ExecutionWorkspace(ExecutionId("e1"), "offline-workspace", "e1_run-01"),
    )


def _target(location="workspace/e1_run-01/c3-t2.wav", **changes) -> AudioArtifact:
    return _artifact("c3-t2", ("t2",), "r3", location=location, **changes)


def _mix(tracks=("t1", "t2", "t3"), location="workspace/e1_run-01/c3-mix.wav", **changes) -> AudioArtifact:
    return _artifact("c3-mix", tracks, "r3", kind=ArtifactKind.MIX, location=location, **changes)


def _evidence(
    aid, inputs, *, hashes=True, finite=True, peak=0.8, processor=PROCESSOR, spec=SPEC
) -> ArtifactProcessingEvidence:
    return ArtifactProcessingEvidence(
        ArtifactId(aid), tuple(ArtifactId(i) for i in inputs), hashes, finite, peak, processor, spec
    )


TARGET_EVIDENCE = _evidence("c3-t2", ("a-t2",))
MIX_EVIDENCE = _evidence("c3-mix", ("c3-t2", "a-t1", "a-t3"), peak=1.25)  # over-range peak is descriptive only


def _response(target=None, mix=None, evidence=None) -> ProcessedCandidate:
    return ProcessedCandidate(
        target or _target(), mix or _mix(), evidence if evidence is not None else (TARGET_EVIDENCE, MIX_EVIDENCE)
    )


# --- valid response ----------------------------------------------------------------------


def test_valid_processed_candidate_passes():
    validate_processed_candidate(_plan(), _response())


def test_mix_sources_are_compared_as_a_set_not_by_order():
    validate_processed_candidate(_plan(), _response(mix=_mix(tracks=("t3", "t1", "t2"))))


# --- output metadata ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "response, match",
    [
        (lambda: _response(target=_artifact("x-t2", ("t2",), "r3", location="w/x.wav")), "c3-t2"),
        (
            lambda: _response(
                mix=_artifact("x-mix", ("t1", "t2", "t3"), "r3", kind=ArtifactKind.MIX, location="w/x.wav")
            ),
            "c3-mix",
        ),
        (lambda: _response(target=_target(project_id=ProjectId("p9"))), "project"),
        (lambda: _response(mix=dataclasses.replace(_mix(), revision=Revision("r2"))), "revision"),
        (lambda: _response(target=_target(kind=ArtifactKind.MIX)), "kind"),
        (lambda: _response(target=_target(source_track_ids=(TrackId("t1"),))), "source"),
        (lambda: _response(mix=dataclasses.replace(_mix(), kind=ArtifactKind.GROUP)), "kind"),
        (lambda: _response(mix=_mix(tracks=("t1", "t2"))), "source"),
        (lambda: _response(target=_target(sample_rate_hz=44100)), "dimensions"),
        (lambda: _response(mix=_mix(channel_count=1)), "dimensions"),
        (lambda: _response(mix=_mix(frame_count=FRAMES - 1)), "dimensions"),
        (lambda: _response(target=_target(timeline_start_seconds=0.5)), "dimensions"),
        (lambda: _response(target=_target(render_spec=VersionRef("render", "2"))), "render spec"),
        (lambda: _response(mix=_mix(render_spec=VersionRef("other-render", "1"))), "render spec"),
    ],
    ids=[
        "target-id",
        "mix-id",
        "project",
        "revision",
        "target-kind",
        "target-source",
        "mix-kind",
        "mix-contributors",
        "target-rate",
        "mix-channels",
        "mix-frames",
        "target-offset",
        "target-render-spec-ref",
        "mix-render-spec-ref",
    ],
)
def test_output_metadata_must_match_the_plan(response, match):
    with pytest.raises(ValueError, match=match):
        validate_processed_candidate(_plan(), response())


def test_render_spec_reference_mismatch_is_rejected_even_when_dimensions_match():
    target = _target(render_spec=VersionRef("render", "2"))
    assert (target.sample_rate_hz, target.channel_count, target.frame_count) == (48000, 2, FRAMES)
    with pytest.raises(ValueError, match="render spec"):
        validate_processed_candidate(_plan(), _response(target=target))


# --- evidence ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "evidence, match",
    [
        ((TARGET_EVIDENCE,), "evidence"),
        ((TARGET_EVIDENCE, _evidence("x-mix", ("c3-t2", "a-t1", "a-t3"))), "evidence"),
        ((_evidence("c3-t2", ("a-t2",), hashes=False), MIX_EVIDENCE), "verified"),
        ((TARGET_EVIDENCE, _evidence("c3-mix", ("c3-t2", "a-t1", "a-t3"), finite=False)), "verified"),
        (
            (TARGET_EVIDENCE, _evidence("c3-mix", ("c3-t2", "a-t1", "a-t3"), processor=VersionRef("other", "1"))),
            "processor",
        ),
        ((_evidence("c3-t2", ("c-t2",)), MIX_EVIDENCE), "target evidence"),
        ((_evidence("c3-t2", ("a-t2", "a-t1")), MIX_EVIDENCE), "target evidence"),
        ((TARGET_EVIDENCE, _evidence("c3-mix", ("c-t2", "a-t1", "a-t3"))), "mix evidence"),
        ((TARGET_EVIDENCE, _evidence("c3-mix", ("c3-t2", "a-t1"))), "mix evidence"),
        ((TARGET_EVIDENCE, _evidence("c3-mix", ("c3-t2", "c-t2", "a-t1", "a-t3"))), "mix evidence"),
        ((TARGET_EVIDENCE, _evidence("c3-mix", ("c3-t2", "a-t2", "a-t1", "a-t3"))), "mix evidence"),
    ],
    ids=[
        "missing-mix-evidence",
        "unknown-evidence-id",
        "hashes-not-verified",
        "non-finite-samples",
        "different-processor",
        "target-cites-current-stem",
        "target-cites-extra-input",
        "mix-cites-obsolete-target",
        "mix-misses-non-target-stem",
        "mix-sums-old-and-new-target",
        "mix-cites-fixed-input",
    ],
)
def test_evidence_must_cover_outputs_with_exact_dependency_citations(evidence, match):
    with pytest.raises(ValueError, match=match):
        validate_processed_candidate(_plan(), _response(evidence=evidence))


# --- lexical location conflicts (not path or alias safety) ----------------------------------


@pytest.mark.parametrize(
    "target, mix",
    [
        (_target(location="workspace/e1_run-01/out.wav"), _mix(location="workspace/e1_run-01/out.wav")),
        (_target(location=A_T1.location), None),
        (None, _mix(location=A_T2_BASELINE.location)),
        (_target(location=C_T2.location), None),
    ],
    ids=[
        "outputs-share-location",
        "target-overwrites-stem",
        "mix-overwrites-fixed-input",
        "target-overwrites-current-target",
    ],
)
def test_output_locations_must_be_lexically_distinct(target, mix):
    with pytest.raises(ValueError, match="location"):
        validate_processed_candidate(_plan(), _response(target=target, mix=mix))


# --- record construction ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        lambda: _evidence("c3-t2", ()),
        lambda: _evidence("c3-t2", ("a-t2", "a-t2")),
        lambda: ArtifactProcessingEvidence(ArtifactId("c3-t2"), [ArtifactId("a-t2")], True, True, 0.5, PROCESSOR, SPEC),
        lambda: _evidence("c3-t2", ("a-t2",), hashes=1),
        lambda: _evidence("c3-t2", ("a-t2",), finite="yes"),
        lambda: _evidence("c3-t2", ("a-t2",), peak=math.nan),
        lambda: _evidence("c3-t2", ("a-t2",), peak=math.inf),
        lambda: _evidence("c3-t2", ("a-t2",), peak=-0.1),
        lambda: _evidence("c3-t2", ("a-t2",), peak=True),
        lambda: _evidence("c3-t2", ("a-t2",), processor="gain-mix-processor@0.1"),
        lambda: ArtifactProcessingEvidence("c3-t2", (ArtifactId("a-t2"),), True, True, 0.5, PROCESSOR, SPEC),
    ],
    ids=[
        "no-inputs",
        "duplicate-inputs",
        "mutable-inputs",
        "int-flag",
        "str-flag",
        "nan-peak",
        "inf-peak",
        "negative-peak",
        "bool-peak",
        "untyped-processor",
        "untyped-artifact",
    ],
)
def test_evidence_record_rejects_invalid_values(build):
    with pytest.raises(ValueError):
        build()


def test_zero_peak_is_valid_evidence():
    assert _evidence("c3-t2", ("a-t2",), peak=0).peak_linear == 0


@pytest.mark.parametrize(
    "build",
    [
        lambda: _response(evidence=(TARGET_EVIDENCE, TARGET_EVIDENCE)),
        lambda: _response(evidence=[TARGET_EVIDENCE, MIX_EVIDENCE]),
        lambda: ProcessedCandidate(_target(), _target(), (TARGET_EVIDENCE, MIX_EVIDENCE)),
        lambda: ProcessedCandidate("c3-t2", _mix(), (TARGET_EVIDENCE, MIX_EVIDENCE)),
        lambda: ProcessedCandidate(_target(), _mix(), ("evidence",)),
    ],
    ids=["duplicate-evidence", "mutable-evidence", "same-output-twice", "untyped-target", "untyped-evidence"],
)
def test_processed_candidate_rejects_invalid_records(build):
    with pytest.raises(ValueError):
        build()


@pytest.mark.parametrize("status", [ExecutionStatus.FAILED, ExecutionStatus.UNCERTAIN])
def test_processing_failure_keeps_known_and_uncertain_outcomes_distinct(status):
    failure = ProcessingFailure(status, ExecutionIssue("render_error", "processor stopped"))
    assert failure.status is status


@pytest.mark.parametrize(
    "status, issue",
    [
        (ExecutionStatus.NOT_STARTED, ExecutionIssue("x", "y")),
        (ExecutionStatus.CANDIDATE_READY, ExecutionIssue("x", "y")),
        (ExecutionStatus.FAILED, None),
        ("FAILED", ExecutionIssue("x", "y")),
    ],
    ids=["not-started", "candidate-ready", "missing-issue", "untyped-status"],
)
def test_processing_failure_rejects_other_statuses_and_missing_issue(status, issue):
    with pytest.raises(ValueError):
        ProcessingFailure(status, issue)


def test_validator_requires_typed_plan_and_successful_response():
    failure = ProcessingFailure(ExecutionStatus.FAILED, ExecutionIssue("render_error", "stopped"))
    with pytest.raises(ValueError):
        validate_processed_candidate(_plan(), failure)
    with pytest.raises(ValueError):
        validate_processed_candidate("plan", _response())


# --- processor port (test doubles only) ----------------------------------------------------


class _ScriptedProcessor:
    """Test double returning a scripted outcome; counts calls; performs no processing."""

    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.calls = 0

    def process(self, plan: ProcessingPlan) -> ProcessingOutcome:
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _run(processor: AudioProcessor, plan: ProcessingPlan) -> ProcessingOutcome:
    return processor.process(plan)


def test_processor_port_returns_candidate_or_typed_failure_without_retry():
    plan = _plan()
    success = _ScriptedProcessor(_response())
    outcome = _run(success, plan)
    assert isinstance(outcome, ProcessedCandidate)
    validate_processed_candidate(plan, outcome)
    for status in (ExecutionStatus.FAILED, ExecutionStatus.UNCERTAIN):
        failing = _ScriptedProcessor(ProcessingFailure(status, ExecutionIssue("render_error", "stopped")))
        result = _run(failing, plan)
        assert isinstance(result, ProcessingFailure) and result.status is status
        assert failing.calls == 1
    assert success.calls == 1


def test_unexpected_processor_exceptions_propagate():
    crashing = _ScriptedProcessor(RuntimeError("adapter bug"))
    with pytest.raises(RuntimeError):
        _run(crashing, _plan())
    assert crashing.calls == 1


# --- processing specification in evidence --------------------------------------------------------


@pytest.mark.parametrize(
    "evidence",
    [
        (_evidence("c3-t2", ("a-t2",), spec=_processing_spec("2")), MIX_EVIDENCE),
        (TARGET_EVIDENCE, _evidence("c3-mix", ("c3-t2", "a-t1", "a-t3"), spec=_processing_spec("2"))),
    ],
    ids=["target-only-mismatch", "mix-only-mismatch"],
)
def test_each_output_must_record_the_plan_processing_spec(evidence):
    with pytest.raises(ValueError, match="processing spec"):
        validate_processed_candidate(_plan(), _response(evidence=evidence))


def test_equal_separately_constructed_processing_spec_is_accepted():
    separate = _processing_spec("1")
    assert separate is not SPEC and separate == SPEC
    evidence = (
        _evidence("c3-t2", ("a-t2",), spec=separate),
        _evidence("c3-mix", ("c3-t2", "a-t1", "a-t3"), spec=_processing_spec("1")),
    )
    validate_processed_candidate(_plan(), _response(evidence=evidence))


def test_processor_identity_is_checked_separately_from_processing_spec():
    # Same specification, different processor: still rejected by the processor-identity check.
    evidence = (TARGET_EVIDENCE, _evidence("c3-mix", ("c3-t2", "a-t1", "a-t3"), processor=VersionRef("other", "1")))
    with pytest.raises(ValueError, match="processor"):
        validate_processed_candidate(_plan(), _response(evidence=evidence))


@pytest.mark.parametrize("spec", [VersionRef("gain-mix-processing", "1"), None, "gain-mix-processing@1"])
def test_evidence_and_plan_require_a_typed_processing_spec(spec):
    with pytest.raises(ValueError):
        _evidence("c3-t2", ("a-t2",), spec=spec)
    with pytest.raises(ValueError):
        dataclasses.replace(_plan(), processing_spec=spec)
