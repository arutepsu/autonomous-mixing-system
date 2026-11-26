"""Numerical R5B integration fixtures; only external provider/device transports are doubles."""

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf
from daw_export_support import routing
from fl_runtime_support import make_request
from masking_diagnosis_support import CURRENT_SCREEN
from masking_support import ALIGNMENT, REGION, prep_job, prep_request, source_of
from test_claude_config import config as provider_config
from test_claude_decision_maker import ABSTAIN, received
from test_coordinator import Clock
from test_daw_cli import write_config
from test_file_run_renderer import PROCESSING, RECIPE
from test_fl_runtime_recovery_controller import DeviceTransport, World
from test_run_recording_contracts import P1, POLICY, RENDER_REF, T1, T2, _configuration, _run_request

from autonomous_mixing.adapters.decision_agent.transport import TransportResult
from autonomous_mixing.adapters.dsp_analysis.file_analyzer import ANALYZER, SAMPLE_PEAK_DBFS
from autonomous_mixing.core.records.admission import StemBinding, TrackGainPermission
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.budgets import BudgetLimits
from autonomous_mixing.core.records.daw_correction import DawSessionConfig
from autonomous_mixing.core.records.daw_export import (
    DawExportedFile,
    DawExportFormat,
    DawExportManifest,
    DawExportTimeline,
)
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    CheckpointId,
    ConditionId,
    CriteriaId,
    Revision,
    VersionRef,
)
from autonomous_mixing.core.records.integrity import ArtifactRootBinding
from autonomous_mixing.core.records.masking_measurement import P1_FLOAT64_PROFILE
from autonomous_mixing.core.records.observation import (
    AnalysisScope,
    AnalysisSpec,
    MeasuredScalar,
    RenderSpec,
    TimeRange,
)
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    DawParameterState,
    DawTrackParameter,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
)
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
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition
from autonomous_mixing.drivers.daw_runtime import UuidIdentitySource, load_config

UTC = datetime(2026, 11, 24, tzinfo=timezone.utc)
RATE, N = 44100, 34560
BINDING, PROFILE = make_request().resolution.state.binding, make_request().profile
SPAN = TimeRange(0, N / RATE)
LIMIT = 1 << 24


class ProviderTransport:
    """Labelled response generator: uses public aliases/evidence, never supplies diagnosis or verification."""

    def __init__(self, gains=(0.0,)):
        self.gains = gains
        self.calls = []
        self.closed = False
        self.config = provider_config()
        self.mode = "normal"

    def send(self, body, *, timeout_seconds):
        packet = json.loads(json.loads(body)["messages"][0]["content"])
        self.calls.append(packet)
        if self.mode == "malformed":
            return received("{invalid")
        if self.mode == "refused":
            return received("refused", stop="refusal")
        if self.mode == "timeout":
            return TransportResult(None, None, None, "unit-test", "connection_failed", True)
        candidates = packet["diagnosis"]["problem_candidates"]
        if not candidates:
            return received(ABSTAIN)
        index = len(self.calls) - 1
        if index >= len(self.gains):
            return received(ABSTAIN)
        track = packet["actions"]["set_track_gain"][0]["track"]
        candidate = candidates[0]
        reply = {
            "type": "propose_action",
            "action": {"operation": "set_track_gain", "track": track, "gain_db": self.gains[index]},
            "addressed_problem_candidates": [candidate["alias"]],
            "evidence_refs": candidate["evidence_refs"],
            "expected_effect": "numerical fixture level reduction",
            "rationale": "labelled transport response",
            "justification": {
                "reasoning": "numerical test",
                "uncertainty": "uncalibrated",
                "assumptions": [],
                "limitations": [],
            },
        }
        return received(json.dumps(reply))

    def close(self):
        self.closed = True


class Setup:
    def __init__(self, root: Path, *, silent=False, gains=(0.0,), frames=N, region=REGION, target_counts=2048):
        self.root = root
        self.config = load_config(write_config(root))
        self.n = frames
        k = np.arange(frames)
        self.a = np.zeros(frames) if silent else np.rint(8192 * np.sin(2 * np.pi * 1000 * k / RATE)) / 32768
        self.b = np.zeros(frames) if silent else np.rint(target_counts * np.sin(2 * np.pi * 1000 * k / RATE)) / 32768
        self.artifacts = []
        for aid, tracks, kind, values in [
            ("s1", (T1,), ArtifactKind.STEM, self.a),
            ("s2", (T2,), ArtifactKind.STEM, self.b),
            ("mix", (T1, T2), ArtifactKind.MIX, self.a + self.b),
        ]:
            path = root / "inputs" / f"{aid}.wav"
            sf.write(path, np.repeat(values[:, None], 2, axis=1), RATE, subtype="DOUBLE")
            self.artifacts.append(
                AudioArtifact(
                    ArtifactId(aid),
                    P1,
                    Revision("r1"),
                    tracks,
                    kind,
                    path.name,
                    0.0,
                    RATE,
                    2,
                    frames,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    RENDER_REF,
                )
            )
        snapshot = ProjectSnapshot(P1, Revision("r1"), UTC, (Track(T1, "bass"), Track(T2, "kick")))
        self.state = ProjectState(
            snapshot,
            ProjectProcessingState(P1, Revision("r1"), ()),
            ArtifactCheckpoint(CheckpointId("c1"), P1, Revision("r1"), tuple(self.artifacts)),
            DawParameterState(P1, Revision("r1"), (DawTrackParameter(BINDING, 2.0, BINDING.baseline_fader),)),
        )
        self.bindings = tuple(ArtifactRootBinding(a.artifact_id, "inputs") for a in self.artifacts)
        job = prep_job(self.artifacts[0], self.artifacts[1], TimeRange(0, frames / RATE), region=region)
        self.preparation = prep_request(
            self.state, self.bindings, (job,), profile=P1_FLOAT64_PROFILE, experimental_policy=CURRENT_SCREEN
        )
        self.preparation = replace(
            self.preparation, max_file_bytes=self.config.max_file_bytes, max_decoded_bytes=self.config.max_decoded_bytes
        )
        self.provider = ProviderTransport(gains)
        self.world = None
        self.clock = Clock(step=0.001)
        self.identities = UuidIdentitySource()

    def fl_factory(self, journal):
        if self.world is None:
            self.world = World(journal)
        else:
            self.world.journal = journal
        return DeviceTransport(self.world)

    def request(self, plan, diagnostics):
        span = TimeRange(0, self.n / RATE)
        scope = AnalysisScope((T1, T2), span)
        session = DawSessionConfig(
            BINDING,
            PROFILE,
            TrackGainPermission(T1, -6.0, 2.0),
            routing(dependents=()),
            DawExportFormat("WAV", "DOUBLE"),
            DawExportTimeline(0.0, RATE, self.n),
            RENDER_REF,
            "daw-exports",
        )
        selector = MetricSelector(SAMPLE_PEAK_DBFS, AnalysisScope((T1,), span))
        definition = MeasurementDefinition(
            selector, "dBFS", plan.specification, ANALYZER, ALIGNMENT.method, (source_of(self.artifacts[0]),)
        )
        master_selector = MetricSelector(SAMPLE_PEAK_DBFS, AnalysisScope((T1, T2), span))
        master_definition = MeasurementDefinition(
            master_selector, "dBFS", plan.specification, ANALYZER, ALIGNMENT.method, (source_of(self.artifacts[2]),)
        )
        criteria = AcceptanceCriteria(
            CriteriaId("engineering"),
            "1",
            (TargetCondition(ConditionId("target"), selector, ComparisonOperator.LE, MeasuredScalar(-12.8, "dBFS")),),
            (
                ImprovementCondition(
                    ConditionId("improve"), selector, ImprovementDirection.LOWER, MeasuredScalar(0.5, "dBFS")
                ),
            ),
            (
                DeltaGuard(
                    ConditionId("master-guard"), master_selector, BaselineReference.INITIAL, MeasuredScalar(5.0, "dBFS")
                ),
            ),
        )
        metrics = tuple(dict.fromkeys([SAMPLE_PEAK_DBFS, *[d.selector.metric for d in diagnostics]]))
        cfg = _configuration(
            scope=scope,
            analysis_spec=AnalysisSpec(plan.specification, metrics),
            render_spec=RenderSpec(RENDER_REF, RATE, 2),
            criteria=criteria,
            budget_limits=BudgetLimits(4, 3, 0, 7, 120.0, 3),
        )
        return _run_request(
            initial_state=self.state,
            configuration=cfg,
            policy=POLICY,
            processing_spec=PROCESSING,
            recipe=RECIPE,
            stem_bindings=(StemBinding(T1, ArtifactId("s1")), StemBinding(T2, ArtifactId("s2"))),
            mix_artifact_id=ArtifactId("mix"),
            measurements=(
                ConditionMeasurement(ConditionId("target"), definition),
                ConditionMeasurement(ConditionId("improve"), definition),
                ConditionMeasurement(ConditionId("master-guard"), master_definition),
            ),
            analysis_definitions=(definition, master_definition, *diagnostics),
            alignment=ALIGNMENT.method,
            root_bindings=self.bindings,
            output_root_key="daw-exports",
            daw_session=session,
        )

    def create(self):
        from autonomous_mixing.drivers.agent_runtime import DawAgentRuntime

        return DawAgentRuntime.create(
            config=self.config,
            run_id=self.request_id,
            preparation=self.preparation,
            request_factory=self.request,
            provider_config=provider_config(),
            api_key="unit-test-only",
            max_evidence_bytes=LIMIT,
            max_audit_record_bytes=LIMIT,
            known_alignments=frozenset({ALIGNMENT.method}),
            clock=self.clock,
            identities=self.identities,
            provider_transport_factory=lambda config, key: self.provider,
            fl_transport_factory=self.fl_factory,
        )

    @property
    def request_id(self):
        from autonomous_mixing.core.records.identifiers import RunId

        return RunId("run-1")

    def export(self, suspended):
        data = suspended.checkpoint_event.payload
        gain = 10 ** ((data.write.after.db - BINDING.baseline_fader.db) / 20)
        files = []
        for output in data.awaiting.request.outputs:
            values = self.a * gain if output.kind is ArtifactKind.STEM else self.a * gain + self.b
            path = Path(data.awaiting.workspace_directory) / output.file_name
            sf.write(path, np.repeat(values[:, None], 2, axis=1), RATE, subtype="DOUBLE")
            files.append(
                DawExportedFile(output.artifact_id, output.file_name, hashlib.sha256(path.read_bytes()).hexdigest())
            )
        return DawExportManifest(
            data.awaiting.request.export_id,
            data.awaiting.request_fingerprint,
            tuple(files),
            VersionRef("test-operator", "1"),
            UTC,
            "test files exported as declared",
        )
