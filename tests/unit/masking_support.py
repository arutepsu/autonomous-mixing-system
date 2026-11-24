"""Shared builders for the P1 directional-measurement tests (R1).

Audio is either a stored dist10 fixture input (``tests/fixtures/mpeg_psy_model2_dist10/inputs``, synthetic numerical
test signals, read unchanged) or a tiny labelled analytical int16 array. Both are unit-test signals, never evaluation
data. Track identities are generic (``ta``, ``tb``, ``tc``); no role is implied.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

from autonomous_mixing.adapters.dsp_analysis.directional_analysis import (
    ANALYZER,
    QUALIFIED_METRICS,
    RAW_METRICS,
    DirectionalMaskingAnalyzer,
)
from autonomous_mixing.adapters.dsp_analysis.masking_preparation import MaskingPreparer
from autonomous_mixing.core.records.analysis import AnalysisRequest
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.coordinator_runtime import ClockReading
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    CheckpointId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.integrity import ArtifactRootBinding
from autonomous_mixing.core.records.masking_measurement import (
    P1_PROFILE,
    AlignmentProvenance,
    BandRegion,
    MaskingMeasurementPlan,
    MaskingPair,
    MaskingPreparationJob,
    MaskingPreparationRequest,
    QualificationStatus,
    bound_specification,
)
from autonomous_mixing.core.records.observation import AnalysisScope, AnalysisSpec, TimeRange
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)
from autonomous_mixing.core.records.results import Success
from autonomous_mixing.core.records.verification import MetricSelector
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition, SourceRequirement

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mpeg_psy_model2_dist10"
RATE = 44100
UTC = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
RENDER = VersionRef("render", "1")
ALIGNMENT = AlignmentProvenance(VersionRef("alignment.common_export_selection", "1"),
                                "labelled test declaration: both stems exported from one selection")  # fmt: skip
TA, TB, TC = TrackId("ta"), TrackId("tb"), TrackId("tc")
P = ProjectId("p")
LIMIT = 1 << 22
REGION = BandRegion("all-supported", tuple(range(1, 21)))
N = 6912  # twelve granules: the length of the full fixture inputs
FULL = TimeRange(0.0, N / RATE)
SPEC_NAME, SPEC_VERSION = "p1.analysis_spec", "1"


def fixture_counts(name: str) -> np.ndarray:
    """The stored int16 little-endian counts of one fixture input (read-only; never modified on disk)."""
    return np.frombuffer((FIXTURE / "inputs" / f"{name}.raw").read_bytes(), dtype="<i2").astype(np.int16)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Clock:
    def __init__(self, step: float = 1.0, cancel_after: int | None = None):
        self.reads, self.step, self.cancel_after = 0, step, cancel_after

    def read(self) -> ClockReading:
        self.reads += 1
        cancelled = self.cancel_after is not None and self.reads > self.cancel_after
        return ClockReading(self.reads * self.step, UTC + timedelta(seconds=self.reads * self.step), cancelled)


class Tokens:
    def __init__(self, prefix: str = "obs"):
        self.prefix, self.count = prefix, 0

    def next_token(self) -> str:
        self.count += 1
        return f"{self.prefix}-{self.count}"


class Stems:
    """Mono PCM_16 stems in one input root, one per track, as current members of one project state."""

    def __init__(self, tmp: Path, counts: dict[TrackId, np.ndarray], *, rate: int = RATE, subtype="PCM_16",
                 channels: int | dict[TrackId, int] = 1, start: float = 0.0):  # fmt: skip
        self.root = tmp / "inputs"
        self.root.mkdir(exist_ok=True)
        self.roots = {"inputs": str(self.root)}
        self.artifacts = {track: self.write(track, values, rate=rate, subtype=subtype, channels=channels.get(track, 1) if isinstance(channels, dict) else channels, start=start)
                          for track, values in counts.items()}  # fmt: skip
        self.state = state_of(tuple(self.artifacts.values()))
        self.bindings = tuple(ArtifactRootBinding(a.artifact_id, "inputs") for a in self.artifacts.values())

    def write(self, track: TrackId, counts: np.ndarray, *, rate: int, subtype: str, channels: int,
              start: float, revision: str = "r1") -> AudioArtifact:  # fmt: skip
        name = f"{track}-{revision}"
        path = self.root / f"{name}.wav"
        data = np.asarray(counts, dtype=np.int16)
        if channels > 1:
            data = np.repeat(data[:, None], channels, axis=1)
        if subtype == "PCM_16":
            sf.write(str(path), data, rate, format="WAV", subtype="PCM_16")
        else:
            sf.write(str(path), data.astype(np.float64) / 32768.0, rate, format="WAV", subtype=subtype)
        return AudioArtifact(ArtifactId(name), P, Revision(revision), (track,), ArtifactKind.STEM, f"{name}.wav",
                             start, rate, channels, len(data), sha256(path), RENDER)  # fmt: skip

    def source(self, track: TrackId) -> SourceRequirement:
        return source_of(self.artifacts[track])


def source_of(artifact: AudioArtifact, **overrides) -> SourceRequirement:
    fields = dict(kind=artifact.kind, source_track_ids=artifact.source_track_ids, render_spec=artifact.render_spec,
                  sample_rate_hz=artifact.sample_rate_hz, channel_count=artifact.channel_count,
                  timeline_start_seconds=artifact.timeline_start_seconds, frame_count=artifact.frame_count)  # fmt: skip
    fields.update(overrides)
    return SourceRequirement(**fields)


def state_of(members: tuple[AudioArtifact, ...], revision: str = "r1") -> ProjectState:
    tracks = tuple(dict.fromkeys(t for a in members for t in a.source_track_ids))
    snapshot = ProjectSnapshot(P, Revision(revision), UTC, tuple(Track(t, f"track {t}") for t in tracks))
    by_track = {a.source_track_ids[0]: a for a in members}
    processing = ProjectProcessingState(P, Revision(revision), tuple(TrackGainState(t, by_track[t], 0.0)
                                                                      for t in tracks))  # fmt: skip
    return ProjectState(snapshot, processing, ArtifactCheckpoint(CheckpointId(f"cp-{revision}"), P,
                                                                 Revision(revision), members))  # fmt: skip


def metadata_artifact(track: TrackId, frames: int, *, start: float = 0.0, rate: int = RATE, channels: int = 1,
                      digest: str = "0" * 64) -> AudioArtifact:  # fmt: skip
    return AudioArtifact(ArtifactId(f"{track}-meta"), P, Revision("r1"), (track,), ArtifactKind.STEM,
                         f"{track}-meta.wav", start, rate, channels, frames, digest, RENDER)  # fmt: skip


def prep_job(masker: AudioArtifact, target: AudioArtifact, time_range: TimeRange, *, region: BandRegion = REGION,
             alignment: AlignmentProvenance = ALIGNMENT) -> MaskingPreparationJob:  # fmt: skip
    pair = MaskingPair(masker.source_track_ids[0], target.source_track_ids[0])
    return MaskingPreparationJob(pair, time_range, region, source_of(masker), source_of(target), alignment)


def prep_request(state: ProjectState, bindings, jobs, **overrides) -> MaskingPreparationRequest:
    fields = dict(state=state, root_bindings=tuple(bindings), profile=P1_PROFILE, jobs=tuple(jobs),
                  qualification=QualificationStatus.UNCALIBRATED, max_file_bytes=LIMIT, max_decoded_bytes=LIMIT,
                  max_elapsed_seconds=60.0)  # fmt: skip
    fields.update(overrides)
    return MaskingPreparationRequest(**fields)


class World:
    """Stems, a persisted prepared plan for the given directions and an analyzer registered for its bound specification."""

    def __init__(
        self,
        tmp: Path,
        counts: dict,
        directions=((TA, TB), (TB, TA)),
        time_range=FULL,
        region=None,
        *,
        channels=1,
        profile=P1_PROFILE,
        experimental_policy=None,
    ):
        self.tmp = tmp
        self.stems = Stems(tmp, counts, channels=channels)
        self.plans_dir, self.evidence_dir = tmp / "plans", tmp / "evidence"
        self.plans_dir.mkdir()
        self.evidence_dir.mkdir()
        kwargs = {"region": region} if region else {}
        jobs = tuple(prep_job(self.stems.artifacts[a], self.stems.artifacts[b], time_range, **kwargs)
                     for a, b in directions)  # fmt: skip
        result = MaskingPreparer(self.stems.roots, store=self.plans_dir, clock=Clock(),
                                 known_alignments=frozenset({ALIGNMENT.method})).prepare(
            prep_request(self.stems.state, self.stems.bindings, jobs, profile=profile, experimental_policy=experimental_policy))  # fmt: skip
        assert isinstance(result, Success), result
        self.persisted = result.value
        self.spec = bound_specification(SPEC_NAME, SPEC_VERSION, result.value.digest)
        self.plan = MaskingMeasurementPlan(self.spec, ANALYZER, result.value.digest, result.value.plan)
        self.analyzer = self.make()

    def make(self, **overrides) -> DirectionalMaskingAnalyzer:
        fields = dict(plans=(self.plan,), plan_store=self.plans_dir, evidence_store=self.evidence_dir,
                      max_file_bytes=LIMIT, max_decoded_bytes=LIMIT, max_evidence_bytes=1 << 24, clock=Clock(),
                      identities=Tokens())  # fmt: skip
        fields.update(overrides)
        return DirectionalMaskingAnalyzer(overrides.pop("roots", self.stems.roots), **fields)

    def job(self, metric, masker=TA, target=TB, time_range=FULL, *, artifacts=None, **overrides):
        artifacts = artifacts or self.stems.artifacts
        fields = dict(selector=MetricSelector(metric, AnalysisScope((masker, target), time_range)),
                      unit=RAW_METRICS.get(metric) or QUALIFIED_METRICS.get(metric), analysis_spec=self.spec,
                      analyzer=ANALYZER, alignment=ALIGNMENT.method,
                      sources=(source_of(artifacts[masker]), source_of(artifacts[target])))  # fmt: skip
        fields.update(overrides)
        return MeasurementDefinition(**fields)

    def request(self, definitions, *, state=None, bindings=None) -> AnalysisRequest:
        state = state or self.stems.state
        tracks = tuple(dict.fromkeys(t for d in definitions for t in d.selector.scope.track_ids))
        metrics = tuple(dict.fromkeys(d.selector.metric for d in definitions))
        return AnalysisRequest(state, AnalysisScope(tracks, FULL), AnalysisSpec(self.spec, metrics), tuple(definitions),
                               bindings or self.stems.bindings)  # fmt: skip
