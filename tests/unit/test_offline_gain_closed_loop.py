"""Phase 1 engineering integration: deterministic signals, never music evaluation data.

Real renderer, P1/level DSP, integrity, request projection, coordinator and verifier;
only diagnosis eligibility and proposals are explicitly labelled test doubles.
"""

import dataclasses
import hashlib
import json
import platform
import subprocess
from pathlib import Path

import numpy as np
import pytest
import scipy
import soundfile as sf
from masking_support import ALIGNMENT, REGION, Tokens, prep_job, prep_request
from test_coordinator import Clock, Decider, Diagnosis, Identities, abstain, no_change, propose
from test_file_run_renderer import LIMITS, PROCESSING, RECIPE, Project
from test_run_recording_contracts import P1, RENDER_REF, T1, T2, _configuration, _run_request

from autonomous_mixing.adapters.decision_agent.request import PacketLimits, build_decision_packet
from autonomous_mixing.adapters.dsp_analysis.analysis_router import AnalysisRouter
from autonomous_mixing.adapters.dsp_analysis.directional_analysis import (
    ANALYZER as P1_ANALYZER,
)
from autonomous_mixing.adapters.dsp_analysis.directional_analysis import (
    RAW_SIGNED_MARGIN_MEDIAN_DB,
    DirectionalMaskingAnalyzer,
)
from autonomous_mixing.adapters.dsp_analysis.file_analyzer import ANALYZER, SAMPLE_PEAK_DBFS, FileAudioAnalyzer
from autonomous_mixing.adapters.dsp_analysis.masking_preparation import MaskingPreparer
from autonomous_mixing.adapters.journal.record_codec import decode, dumps, encode
from autonomous_mixing.adapters.journal.run_memory import InMemoryRunJournal
from autonomous_mixing.adapters.rendering.run_renderer import FileRunRenderer
from autonomous_mixing.adapters.rendering.state_integrity import FileStateIntegrityInspector
from autonomous_mixing.core.loop.coordinator import run
from autonomous_mixing.core.records.acceptance import AcceptedReference
from autonomous_mixing.core.records.actions import ExecutionIssue, ExecutionStatus
from autonomous_mixing.core.records.admission import StemBinding
from autonomous_mixing.core.records.analysis import deduplicate_analysis_definitions
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.budgets import BudgetLimits
from autonomous_mixing.core.records.decision import AbstentionReason
from autonomous_mixing.core.records.identifiers import ArtifactId, ConditionId, CriteriaId, Revision
from autonomous_mixing.core.records.masking_measurement import (
    P1_FLOAT64_PROFILE,
    MaskingMeasurementPlan,
    bound_specification,
)
from autonomous_mixing.core.records.observation import (
    AnalysisScope,
    AnalysisSpec,
    MeasuredScalar,
    RenderSpec,
    TimeRange,
)
from autonomous_mixing.core.records.rendering import RenderFailure
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success
from autonomous_mixing.core.records.run import FinalProjectState, RunOutcome
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
    Verdict,
    VerificationResult,
)
from autonomous_mixing.core.records.verification_assessment import ConditionMeasurement
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition, SourceRequirement

RATE, N = 44100, 6912
SPAN = TimeRange(0.0, N / RATE)
RENDER = RenderSpec(RENDER_REF, RATE, 1)
LIMIT = 1 << 22


class NumericalProject(Project):
    """Reuse existing project/state builders; deterministic int16-valued sine stems."""

    def write(self, root, aid, revision, tracks, samples, kind=ArtifactKind.STEM):
        index = np.arange(N)
        a = np.rint(8192 * np.sin(2 * np.pi * 1000 * index / RATE)).astype(np.int16)
        b = np.rint(1024 * np.sin(2 * np.pi * 2000 * index / RATE)).astype(np.int16)
        counts = (a if tracks == (T1,) else b) if kind is ArtifactKind.STEM else a.astype(np.int32) + b
        path = self.dirs[root] / f"{aid}.wav"
        # Initial PCM_16 and candidate DOUBLE exercise both representations in the same prepared profile.
        sf.write(path, counts.astype(np.float64) / 32768, RATE, subtype="PCM_16")
        return AudioArtifact(
            ArtifactId(aid),
            P1,
            Revision(revision),
            tracks,
            kind,
            path.name,
            0.0,
            RATE,
            1,
            N,
            hashlib.sha256(path.read_bytes()).hexdigest(),
            RENDER_REF,
        )


class RecordingAnalyzer:
    def __init__(self, inner, fail_candidate=False):
        self.inner, self.calls, self.fail_candidate = inner, [], fail_candidate

    def analyze(self, request):
        result = (
            PortFailure(PortFailureCode.UNAVAILABLE, "injected candidate read failure", False)
            if self.fail_candidate and self.calls
            else self.inner.analyze(request)
        )
        self.calls.append((request, result))
        return result


class PacketDecider(Decider):
    def __init__(self, *responses):
        super().__init__(*responses)
        self.packets = []

    def decide(self, situation):
        self.packets.append(build_decision_packet(situation, limits=PacketLimits(1 << 20)))
        return super().decide(situation)


def execute(
    tmp,
    *,
    gains=(-6.0,),
    guard=10.0,
    target=-17.0,
    fail_analysis=False,
    fail_render=False,
    protected=False,
    attempts=2,
    ending=no_change,
):
    tmp.mkdir(exist_ok=True)
    project = NumericalProject(tmp)
    plans, evidence = tmp / "plans", tmp / "evidence"
    plans.mkdir()
    evidence.mkdir()
    prepared = MaskingPreparer(
        project.roots, store=plans, clock=Clock(), known_alignments=frozenset({ALIGNMENT.method})
    ).prepare(
        prep_request(
            project.state,
            tuple(
                b
                for b in project.bindings(project.state)
                if b.artifact_id in {a.artifact_id for a in project.state.audio.artifacts}
            ),
            (prep_job(project.s1, project.s2, SPAN, region=REGION),),
            profile=P1_FLOAT64_PROFILE,
        )
    )
    assert isinstance(prepared, Success), prepared
    spec = bound_specification("phase1.engineering", "1", prepared.value.digest)
    plan = MaskingMeasurementPlan(spec, P1_ANALYZER, prepared.value.digest, prepared.value.plan)
    scope = AnalysisScope((T1, T2), SPAN)
    t1_scope, t2_scope = AnalysisScope((T1,), SPAN), AnalysisScope((T2,), SPAN)
    peak = MetricSelector(SAMPLE_PEAK_DBFS, t1_scope)
    other_peak = MetricSelector(SAMPLE_PEAK_DBFS, t2_scope)

    def scalar(x):
        return MeasuredScalar(x, "dBFS")

    criteria = AcceptanceCriteria(
        CriteriaId("engineering-levels"),
        "1",
        (TargetCondition(ConditionId("target"), peak, ComparisonOperator.LE, scalar(target)),),
        (ImprovementCondition(ConditionId("improve"), peak, ImprovementDirection.LOWER, scalar(1.0)),),
        (DeltaGuard(ConditionId("guard"), peak, BaselineReference.INITIAL, scalar(guard)),),
    )

    def source(artifact):
        return SourceRequirement(artifact.kind, artifact.source_track_ids, artifact.render_spec, RATE, 1, 0.0, N)

    level_jobs = tuple(
        MeasurementDefinition(selector, "dBFS", spec, ANALYZER, ALIGNMENT.method, (source(artifact),))
        for selector, artifact in ((peak, project.s1), (other_peak, project.s2))
    )
    p1_job = MeasurementDefinition(
        MetricSelector(RAW_SIGNED_MARGIN_MEDIAN_DB, scope),
        "dB",
        spec,
        P1_ANALYZER,
        ALIGNMENT.method,
        (source(project.s1), source(project.s2)),
    )
    measurements = tuple(
        ConditionMeasurement(ConditionId(name), definition)
        for name, definition in (("target", level_jobs[0]), ("improve", level_jobs[0]), ("guard", level_jobs[0]))
    )
    config = _configuration(
        scope=scope,
        render_spec=RENDER,
        criteria=criteria,
        analysis_spec=AnalysisSpec(spec, (SAMPLE_PEAK_DBFS, RAW_SIGNED_MARGIN_MEDIAN_DB)),
        budget_limits=BudgetLimits(5, attempts, 3, 3, 60.0),
    )
    if protected:
        config = dataclasses.replace(
            config, context=dataclasses.replace(config.context, protected_track_ids=frozenset({T1}))
        )
    request = _run_request(
        initial_state=project.state,
        configuration=config,
        processing_spec=PROCESSING,
        recipe=RECIPE,
        stem_bindings=(StemBinding(T1, project.s1.artifact_id), StemBinding(T2, project.s2.artifact_id)),
        mix_artifact_id=project.mix.artifact_id,
        measurements=measurements,
        analysis_definitions=deduplicate_analysis_definitions((*level_jobs, p1_job)),
        alignment=ALIGNMENT.method,
        root_bindings=project.bindings(project.state),
        output_root_key="renders",
    )
    levels = FileAudioAnalyzer(
        project.roots, max_file_bytes=LIMIT, max_decoded_bytes=LIMIT, clock=Clock(), identities=Tokens("levels")
    )
    masking = DirectionalMaskingAnalyzer(
        project.roots,
        plans=(plan,),
        plan_store=plans,
        evidence_store=evidence,
        max_file_bytes=LIMIT,
        max_decoded_bytes=LIMIT,
        max_evidence_bytes=1 << 24,
        clock=Clock(),
        identities=Tokens("masking"),
    )
    analyzer = RecordingAnalyzer(AnalysisRouter({ANALYZER: levels, P1_ANALYZER: masking}), fail_analysis)
    decider = PacketDecider(*(propose(gain) for gain in gains), ending)
    renderer = FileRunRenderer(project.roots, limits=LIMITS)
    if fail_render:

        class FailedRenderer:
            def process(self, request):
                return RenderFailure(ExecutionStatus.FAILED, ExecutionIssue("injected", "render failure"))

        renderer = FailedRenderer()
    hashes = project.source_hashes()
    report = run(
        request,
        analyzer=analyzer,
        diagnosis_source=Diagnosis(),
        decision_maker=decider,
        renderer=renderer,
        integrity_inspector=FileStateIntegrityInspector(project.roots, max_file_bytes=LIMIT, max_decoded_bytes=LIMIT),
        journal=InMemoryRunJournal(request.run_id, AcceptedReference(project.state, 0)),
        clock=Clock(),
        identities=Identities(),
    )
    assert project.source_hashes() == hashes
    # Canonical core codec preserves full request, observations, actions, criteria, feedback and selected state.
    serialized = dumps(encode(report))
    assert decode(json.loads(serialized)) == report
    (tmp / "report.json").write_text(serialized, encoding="utf-8")
    for i, packet in enumerate(decider.packets):
        (tmp / f"decision-{i}.json").write_text(packet.payload, encoding="utf-8")
    repo = Path(__file__).resolve().parents[2]
    revision = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
    code_hashes = {
        str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((repo / "src").rglob("*.py"))
    }
    code_hashes[str(Path(__file__).relative_to(repo))] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    metadata = {
        "code_sha256": code_hashes,
        "git_revision_at_execution": revision.stdout.strip() if revision.returncode == 0 else None,
        "uv_lock_sha256": hashlib.sha256((repo / "uv.lock").read_bytes()).hexdigest(),
        "fixture": "synthetic engineering signals; not evaluation data",
        "git_revision": "32d49be5f362ca83fe5d6bdfa56720aceebf7b2f",
        "working_tree": "dirty baseline plus Phase 1 changes",
        "dependencies": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "soundfile": sf.__version__,
            "libsndfile": sf.__libsndfile_version__,
        },
        "inputs": hashes,
        "prepared_digest": prepared.value.digest,
        "outcome": report.final_run.outcome.value,
        "reason": report.final_run.termination_reason.value,
    }
    (tmp / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return project, report, analyzer, decider


def test_accept_and_repeat_have_identical_records_and_measurements(tmp_path):
    runs = [execute(tmp_path / str(i)) for i in range(2)]
    for project, report, analyzer, decider in runs:
        assert report.final_run.outcome is RunOutcome.SUCCEEDED
        assert report.final_run.termination_reason is TerminationReason.CANDIDATE_CRITERIA_MET
        assert report.final_run.final_project_state is FinalProjectState.ACCEPTED_EDITS_RETAINED
        assert report.authoritative == report.verified
        assert len(analyzer.calls) == 2
        peak_values = [
            next(
                o.result.value
                for o in result.value.observations
                if o.metric == SAMPLE_PEAK_DBFS and o.scope.track_ids == (T1,)
            )
            for _, result in analyzer.calls
        ]
        assert peak_values[1] - peak_values[0] == pytest.approx(-6.0, abs=1e-10)
        assert all(
            any(o.metric == RAW_SIGNED_MARGIN_MEDIAN_DB for o in r.value.observations) for _, r in analyzer.calls
        )
        assert json.loads(decider.packets[0].payload)["history"] == []
        assert report.verified.state != project.state
    assert runs[0][1] == runs[1][1]
    assert [p.payload for p in runs[0][3].packets] == [p.payload for p in runs[1][3].packets]


@pytest.mark.parametrize(
    "options,reason",
    [
        ({"guard": 1.0}, TerminationReason.NO_CHANGE_PROPOSED),
        ({"gains": (-0.25,)}, TerminationReason.NO_CHANGE_PROPOSED),
        ({"gains": (0.0,), "target": -30.0}, TerminationReason.NO_CHANGE_PROPOSED),
        ({"fail_analysis": True}, TerminationReason.CANDIDATE_ANALYSIS_FAILED),
        ({"fail_render": True}, TerminationReason.RENDER_FAILED),
        ({"protected": True}, TerminationReason.NO_CHANGE_PROPOSED),
        ({"gains": (-7.0,)}, TerminationReason.NO_CHANGE_PROPOSED),
        ({"attempts": 0}, TerminationReason.ATTEMPT_LIMIT),
        ({"attempts": 1, "gains": (-0.25, -6.0)}, TerminationReason.ATTEMPT_LIMIT),
        (
            {"gains": (), "ending": abstain(AbstentionReason.INSUFFICIENT_EVIDENCE)},
            TerminationReason.INSUFFICIENT_EVIDENCE,
        ),
        ({"gains": ()}, TerminationReason.NO_CHANGE_PROPOSED),
    ],
)
def test_recovery_refusals_and_stops_preserve_initial_state(tmp_path, options, reason):
    project, report, analyzer, decider = execute(tmp_path / "run", **options)
    assert report.final_run.termination_reason is reason
    assert report.verified.state == project.state
    assert report.authoritative.state == project.state
    assert not report.trace.acceptances
    if options.get("guard") == 1.0:
        results = [e.payload for e in report.trace.events if isinstance(e.payload, VerificationResult)]
        assert results[-1].verdict is Verdict.REJECTED
        assert any(c.condition_id == ConditionId("guard") and c.status.value == "fail" for c in results[-1].checks)
        assert report.final_run.final_project_state is FinalProjectState.RESTORED_TO_LAST_ACCEPTED


def test_rejected_candidate_feedback_precedes_next_accepted_attempt(tmp_path):
    project, report, analyzer, decider = execute(tmp_path / "run", gains=(-0.25, -6.0))
    assert report.final_run.outcome is RunOutcome.SUCCEEDED
    assert len(analyzer.calls) == 3
    assert len(decider.packets) == 2
    assert decider.situations[1].state == project.state
    assert decider.situations[1].history.entries
    verdicts = [e.payload.verdict for e in report.trace.events if isinstance(e.payload, VerificationResult)]
    assert Verdict.REJECTED in verdicts
    assert verdicts[-1] is Verdict.ACCEPTED


def test_existing_policy_retains_meaningful_progress_without_claiming_target_success(tmp_path):
    project, report, analyzer, decider = execute(tmp_path / "run", gains=(-6.0,), target=-30.0)
    assert report.final_run.outcome is RunOutcome.ABSTAINED
    assert report.final_run.termination_reason is TerminationReason.NO_CHANGE_PROPOSED
    assert report.final_run.final_project_state is FinalProjectState.ACCEPTED_EDITS_RETAINED
    assert report.verified.state != project.state
    assert len(report.trace.acceptances) == 1
