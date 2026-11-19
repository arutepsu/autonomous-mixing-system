"""Real-adapter harness for the DAW continuation (coordinator-integration Increment 3; tests only).

Baselines and operator exports are tiny synthetic stereo PCM_24 WAVs written by the tests (unit-test data only,
never evaluation data). The FL controller and journal run against device script v2 on a fake host; analysis and
integrity use the real file-backed adapters; decisions and diagnosis are labelled doubles from ``test_coordinator``.
"""

import hashlib
from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
import soundfile as sf
from daw_export_support import routing
from fl_runtime_support import make_request
from test_coordinator import Clock, Decider, Diagnosis, Identities
from test_fl_runtime_recovery_controller import World
from test_run_recording_contracts import P1, POLICY, T1, T2, _configuration, _run_request

from autonomous_mixing.adapters.audio_io.limits import AudioReadLimits
from autonomous_mixing.adapters.audio_io.locations import resolve_artifact_location
from autonomous_mixing.adapters.dsp_analysis.file_analyzer import ANALYZER, SAMPLE_RMS_DBFS, FileAudioAnalyzer
from autonomous_mixing.adapters.fl_studio.journal import SqliteCommandJournal
from autonomous_mixing.adapters.journal.run_memory import InMemoryRunJournal
from autonomous_mixing.adapters.rendering.export_handoff import FileExportHandoff
from autonomous_mixing.adapters.rendering.state_integrity import FileStateIntegrityInspector
from autonomous_mixing.adapters.rendering.workspaces import WorkspaceRoot
from autonomous_mixing.core.loop.coordinator import resume_daw, run_daw
from autonomous_mixing.core.records.acceptance import AcceptedReference
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
    ComparisonOperator,
    ImprovementCondition,
    ImprovementDirection,
    MetricSelector,
    TargetCondition,
)
from autonomous_mixing.core.records.verification_assessment import ConditionMeasurement
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition, SourceRequirement

RATE, FRAMES = 8000, 8000  # 1 s of tiny synthetic stereo audio
FL_EXPORT = VersionRef("fl-export", "1")  # the FL export settings: stereo PCM_24 at RATE
UTC = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
APPLY = make_request()
BINDING, PROFILE = APPLY.resolution.state.binding, APPLY.profile
SCOPE = AnalysisScope((T1, T2), TimeRange(0.0, 1.0))
SELECTOR = MetricSelector(SAMPLE_RMS_DBFS, SCOPE)
SPEC = VersionRef("analysis-spec", "1")
ALIGNMENT = VersionRef("alignment", "1")
DEFINITION = MeasurementDefinition(SELECTOR, "dBFS", SPEC, ANALYZER, ALIGNMENT,
                                   (SourceRequirement(ArtifactKind.MIX, (T1, T2), FL_EXPORT, RATE, 2, 0.0, FRAMES),))  # fmt: skip
# Baseline master at 0.5 (about -6.02 dBFS). The target is <= -10 dBFS and the improvement LOWER by >= 1 dB.
CRITERIA = AcceptanceCriteria(
    CriteriaId("daw-criteria"), "1",
    (TargetCondition(ConditionId("tg"), SELECTOR, ComparisonOperator.LE, MeasuredScalar(-10.0, "dBFS")),),
    (ImprovementCondition(ConditionId("im"), SELECTOR, ImprovementDirection.LOWER, MeasuredScalar(1.0, "dBFS")),),
    (),
)  # fmt: skip
SESSION = DawSessionConfig(BINDING, PROFILE, TrackGainPermission(T1, -6.0, 0.0), routing(dependents=()),
                           DawExportFormat("WAV", "PCM_24"), DawExportTimeline(0.0, RATE, FRAMES), FL_EXPORT,
                           "daw-exports")  # fmt: skip
ACCEPT, INTERMEDIATE, NO_IMPROVEMENT, SILENT = 0.25, 0.4, 0.5, 0.0  # exported master amplitudes


def write_wav(path, amplitude, *, channels=2, frames=FRAMES):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.full((frames, channels), amplitude), RATE, subtype="PCM_24", format="WAV")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def limits(**overrides):
    fields = dict(max_decision_calls=5, max_attempts=4, max_render_calls=1, max_recovery_operations=3,
                  max_elapsed_seconds=60.0, max_daw_writes=3)  # fmt: skip
    fields.update(overrides)
    return BudgetLimits(**fields)


class Continuation:
    def __init__(self, tmp_path, *, decisions, budget=None, journal=None):
        self.tmp = tmp_path
        self.inputs, self.exports = tmp_path / "inputs", tmp_path / "exports"
        self.exports.mkdir(parents=True)
        members = []
        for aid, tracks, kind, amplitude in (("s1", (T1,), ArtifactKind.STEM, 0.3), ("s2", (T2,), ArtifactKind.STEM, 0.3),
                                             ("mix", (T1, T2), ArtifactKind.MIX, 0.5)):  # fmt: skip
            location = f"baseline/{aid}.wav"
            digest = write_wav(self.inputs / location, amplitude)
            members.append(AudioArtifact(ArtifactId(aid), P1, Revision("r1"), tracks, kind, location, 0.0, RATE, 2,
                                         FRAMES, digest, FL_EXPORT))  # fmt: skip
        snapshot = ProjectSnapshot(P1, Revision("r1"), UTC, (Track(T1, "bass"), Track(T2, "pad")))
        self.initial = ProjectState(snapshot, ProjectProcessingState(P1, Revision("r1"), ()),
                                    ArtifactCheckpoint(CheckpointId("c1"), P1, Revision("r1"), tuple(members)),
                                    DawParameterState(P1, Revision("r1"),
                                                      (DawTrackParameter(BINDING, 2.0, BINDING.baseline_fader),)))  # fmt: skip
        configuration = _configuration(scope=SCOPE, analysis_spec=AnalysisSpec(SPEC, (SAMPLE_RMS_DBFS,)),
                                       criteria=CRITERIA, render_spec=RenderSpec(FL_EXPORT, RATE, 2),
                                       budget_limits=budget or limits())  # fmt: skip
        self.request = _run_request(
            initial_state=self.initial, configuration=configuration, policy=POLICY,
            measurements=(ConditionMeasurement(ConditionId("tg"), DEFINITION),
                          ConditionMeasurement(ConditionId("im"), DEFINITION)),
            analysis_definitions=(DEFINITION,), alignment=ALIGNMENT,
            root_bindings=tuple(ArtifactRootBinding(m.artifact_id, "inputs") for m in members),
            stem_bindings=(StemBinding(T1, ArtifactId("s1")), StemBinding(T2, ArtifactId("s2"))),
            mix_artifact_id=ArtifactId("mix"), daw_session=SESSION,
        )  # fmt: skip
        self.fl_journal = SqliteCommandJournal(tmp_path / "fl.db", session_key="session-1")
        self.world = World(self.fl_journal)
        self.real_journal = InMemoryRunJournal(self.request.run_id, AcceptedReference(self.initial, 0))
        self.journal = journal(self.real_journal) if journal else self.real_journal
        self.identities = Identities()
        self.decider = Decider(*decisions)
        roots = {"inputs": str(self.inputs), "daw-exports": str(self.exports)}
        read = dict(max_file_bytes=1 << 22, max_decoded_bytes=1 << 24)
        self.analyzer = FileAudioAnalyzer(roots, clock=Clock(), identities=self.identities, **read)
        self.inspector = FileStateIntegrityInspector(roots, **read)
        self.handoff = FileExportHandoff(root=WorkspaceRoot("daw-exports", str(self.exports)),
                                         inventory=self.inventory, limits=AudioReadLimits(1 << 22, 1 << 24))  # fmt: skip

    def inventory(self, checkpoint):
        """Driver wiring: the before state for ``checkpoint`` among the run's accepted states, and its files."""
        snapshot = self.real_journal.snapshot().value
        for state in (self.initial, *(c.accepted.state for c in snapshot.acceptances)):
            if state.audio == checkpoint:
                fixed = tuple(g.input_artifact for g in state.processing.track_gains)
                protected = tuple(
                    resolve_artifact_location(a, root=str(self.inputs if (self.inputs / a.location).exists()
                                                          else self.exports))
                    for a in (*state.audio.artifacts, *fixed)
                )  # fmt: skip
                return state, protected
        return None

    def collaborators(self, clock=None):
        controller = self.world.controller
        return dict(analyzer=self.analyzer, diagnosis_source=Diagnosis(), decision_maker=self.decider,
                    integrity_inspector=self.inspector, journal=self.journal, clock=clock or Clock(),
                    identities=self.identities, daw_controller=controller, recovery_controller=controller,
                    export_handoff=self.handoff)  # fmt: skip

    def start(self):
        return run_daw(self.request, **self.collaborators())

    def operator_export(self, suspended, master=ACCEPT, *, stem=0.3, extra=None, mono=False, tamper=False):
        """The operator's manual export: writes every requested output and returns the manifest."""
        awaiting = suspended.checkpoint_event.payload.awaiting
        directory = awaiting.workspace_directory
        files = []
        for output in awaiting.request.outputs:
            amplitude = master if output.kind is ArtifactKind.MIX else stem
            path = self.tmp / "exports" / awaiting.request.workspace.directory_token / output.file_name
            assert str(path.parent) == directory
            digest = write_wav(path, amplitude, channels=1 if mono and output.kind is ArtifactKind.MIX else 2)
            files.append(DawExportedFile(output.artifact_id, output.file_name, "0" * 64 if tamper else digest))
        if extra:
            (self.tmp / "exports" / awaiting.request.workspace.directory_token / extra).write_bytes(b"x")
        return DawExportManifest(awaiting.request.export_id, awaiting.request_fingerprint, tuple(files),
                                 VersionRef("operator", "1"), UTC, "exported as requested")  # fmt: skip

    def resume(self, suspended, manifest, clock=None):
        return resume_daw(suspended, manifest, **self.collaborators(clock))

    def sent(self, kind):
        return [m for m in self.world.sent if m["type"] == kind]

    def close(self):
        self.fl_journal.close()


def other_manifest(manifest):
    return replace(manifest, attestation="a different attestation")
