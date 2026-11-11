"""File-backed ``AudioAnalyzer`` for full-band sample-peak and RMS levels (file-audio-analyzer design).

``FileAudioAnalyzer.analyze`` measures the jobs an ``AnalysisRequest`` names on the verified WAV files of
the request state's current audio members. Call sequence:

1. Batch validation, with no filesystem access: the request type (``ValueError`` otherwise), then the
   support of every job (``UNSUPPORTED``: another analyzer identity, metric, unit, a frequency-restricted
   scope, more or fewer than one source, or a source whose contributor set differs from the selector's
   tracks), then for every job the exact source subject among the current members, its coverage of the
   requested time range and a configured root (``INVALID_INPUT``: no or several matching artifacts, a
   source that does not cover the interval, or an unconfigured root key). Nothing partial is returned.
2. One ``RunClock`` reading supplies the batch's UTC creation time (start of the batch, not a persistence
   time). An invalid reading is a wiring error and raises ``ValueError``.
3. Each distinct selected artifact is resolved and verified once per call, in first-occurrence order of
   the jobs, through ``adapters.audio_io`` (bounded single capture, SHA-256, WAV format, declared metadata,
   finite samples); its jobs use exactly those samples, which are released before the next artifact.
   Expected resolution and integrity errors return ``UNAVAILABLE`` naming the code and artifact; there is
   no retry and no partial success. This states that the batch could not be completed, not a zero level.
4. Fresh ``ObservationId`` values are drawn from ``IdentitySource`` in job order; blank or already issued
   tokens (tracked per instance across calls) raise ``ValueError`` without regeneration.
5. Observations are returned in job order with the exact requested scope, the selected original artifact
   ID, the implemented metric/unit/analyzer identities and the request's specification, and are checked
   with ``validate_analysis_response`` before returning.

Time selection keeps exactly the source frames ``i`` whose declared project time
``timeline_start_seconds + i / sample_rate_hz`` lies in the start-inclusive, end-exclusive scope range,
located by lower-bound searches over that monotonic float expression; no rounding of requested times and
no tolerance. This maps the interval onto discrete samples; it does not establish physical alignment.

Unexpected ``OSError``, ``MemoryError``, numerical programming errors and clock or identity contract
errors propagate unchanged. Read limits bound each file, not total memory or wall-clock time. All state is
per instance and non-durable; the analyzer is meant for serial use and makes no concurrency claim. The
references below identify these algorithms; they are not scientifically justified thresholds.
"""

import bisect
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from autonomous_mixing.adapters.audio_io.integrity import IntegrityError, check_resolved_wav_integrity
from autonomous_mixing.adapters.audio_io.limits import AudioReadLimits
from autonomous_mixing.adapters.audio_io.locations import LocationResolutionError, resolve_artifact_location
from autonomous_mixing.adapters.dsp_analysis.levels import (
    DBFS,
    LevelResult,
    sample_peak_dbfs,
    sample_rms_dbfs,
)
from autonomous_mixing.core.ports.analysis import validate_analysis_response
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource, RunClock
from autonomous_mixing.core.records.analysis import AnalysisRequest, AnalysisResponse
from autonomous_mixing.core.records.artifacts import AudioArtifact
from autonomous_mixing.core.records.coordinator_runtime import ClockReading
from autonomous_mixing.core.records.identifiers import ArtifactId, ObservationId, VersionRef
from autonomous_mixing.core.records.invariants import require_instance, require_text
from autonomous_mixing.core.records.observation import Observation, TimeRange
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, PortResult, Success
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition, SourceRequirement

ANALYZER = VersionRef("autonomous_mixing.file_level_analyzer", "1")
SAMPLE_PEAK_DBFS = VersionRef("audio.sample_peak_dbfs", "1")
SAMPLE_RMS_DBFS = VersionRef("audio.sample_rms_dbfs", "1")
_KERNELS = {SAMPLE_PEAK_DBFS: sample_peak_dbfs, SAMPLE_RMS_DBFS: sample_rms_dbfs}


@dataclass(frozen=True)
class _Job:
    """One validated job: its definition and the exact current member it measures (adapter-local)."""

    definition: MeasurementDefinition
    artifact: AudioArtifact
    root_key: str


class _BatchRejected(Exception):
    """Internal signal carrying the typed failure of batch validation; never leaves ``analyze``."""

    def __init__(self, failure: PortFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


def _reject(code: PortFailureCode, message: str) -> _BatchRejected:
    return _BatchRejected(PortFailure(code, message, retryable=False))


def _label(definition: MeasurementDefinition) -> str:
    metric, scope = definition.selector.metric, definition.selector.scope
    tracks = ", ".join(str(t) for t in scope.track_ids)
    span = f"[{scope.time_range.start_seconds}, {scope.time_range.end_seconds}) s"
    return f"job {metric.name}@{metric.version} over tracks {tracks}, {span}"


def _unsupported_reason(definition: MeasurementDefinition) -> str | None:
    selector = definition.selector
    if definition.analyzer != ANALYZER:
        return f"analyzer {definition.analyzer.name}@{definition.analyzer.version} is not implemented here"
    if selector.metric not in _KERNELS:
        return "metric is not implemented by this analyzer"
    if definition.unit != DBFS:
        return f"unit {definition.unit!r} is not supported (only {DBFS!r})"
    if selector.scope.frequency_range is not None:
        return "frequency-restricted scopes are not supported by the full-band methods"
    if len(definition.sources) != 1:
        return f"exactly one source is supported, got {len(definition.sources)}"
    if set(definition.sources[0].source_track_ids) != set(selector.scope.track_ids):
        return "the source's contributors must equal the selector's tracks (no subset of a combined source)"
    return None


def _matches(artifact: AudioArtifact, source: SourceRequirement) -> bool:
    return (
        artifact.kind is source.kind
        and artifact.source_track_ids == source.source_track_ids
        and artifact.render_spec == source.render_spec
        and artifact.sample_rate_hz == source.sample_rate_hz
        and artifact.channel_count == source.channel_count
        and artifact.timeline_start_seconds == source.timeline_start_seconds
        and artifact.frame_count == source.frame_count
    )


def frame_bounds(artifact: AudioArtifact, time_range: TimeRange) -> tuple[int, int]:
    """Frame slice ``[first, stop)`` of the frames whose declared project time lies in ``time_range``.

    Frame ``i`` has time ``timeline_start_seconds + i / sample_rate_hz``, a non-decreasing float expression
    of ``i``; both bounds are lower-bound searches over it (first frame at or after start, first frame at or
    after end). ``first == stop`` means no sample lies in the interval.
    """
    start, rate = artifact.timeline_start_seconds, artifact.sample_rate_hz

    def time_of(i: int) -> float:
        return start + i / rate

    frames = range(artifact.frame_count)
    first = bisect.bisect_left(frames, time_range.start_seconds, key=time_of)
    stop = bisect.bisect_left(frames, time_range.end_seconds, key=time_of)
    return first, stop


class FileAudioAnalyzer:
    """Implements ``AudioAnalyzer`` with full-band sample-peak and RMS levels over configured roots.

    ``roots`` maps logical root keys to physical root paths and is copied at construction; nothing touches
    the filesystem until ``analyze``. ``max_file_bytes`` and ``max_decoded_bytes`` bound each file, exactly
    as in the shared WAV reader; they are caller-supplied resource bounds, not scientific thresholds.
    """

    def __init__(
        self,
        roots: Mapping[str, str],
        *,
        max_file_bytes: int,
        max_decoded_bytes: int,
        clock: RunClock,
        identities: IdentitySource,
    ) -> None:
        if not isinstance(roots, Mapping):
            raise ValueError(f"roots must be a Mapping, got {type(roots).__name__}")
        snapshot = dict(roots.items())
        for key, path in snapshot.items():
            require_text(key, "root key")
            require_text(path, f"root path for {key!r}")
        self._roots: dict[str, str] = snapshot
        self._limits = AudioReadLimits(max_file_bytes, max_decoded_bytes)
        if not callable(getattr(clock, "read", None)):
            raise ValueError("clock must provide read()")
        if not callable(getattr(identities, "next_token", None)):
            raise ValueError("identities must provide next_token()")
        self._clock = clock
        self._identities = identities
        self._issued: set[ObservationId] = set()

    def analyze(self, request: AnalysisRequest) -> PortResult[AnalysisResponse]:
        require_instance(request, AnalysisRequest, "request")
        try:
            jobs = self._plan(request)
        except _BatchRejected as rejected:
            return rejected.failure
        reading = self._clock.read()
        if not isinstance(reading, ClockReading):
            raise ValueError(f"clock returned {type(reading).__name__}, not a ClockReading")
        results: dict[int, LevelResult] = {}
        by_artifact: dict[ArtifactId, list[int]] = {}
        for index, job in enumerate(jobs):
            by_artifact.setdefault(job.artifact.artifact_id, []).append(index)
        for indices in by_artifact.values():
            artifact, root_key = jobs[indices[0]].artifact, jobs[indices[0]].root_key
            try:
                samples = self._read(artifact, root_key)
            except (LocationResolutionError, IntegrityError) as error:
                return PortFailure(
                    PortFailureCode.UNAVAILABLE,
                    f"{error.code.value}: artifact {artifact.artifact_id} under root {root_key!r} could not be "
                    f"read and verified: {error}",
                    retryable=False,
                )
            for index in indices:
                results[index] = _measure(jobs[index], samples)
            del samples
        observations = tuple(
            Observation(
                self._fresh_id(),
                job.definition.selector.metric,
                (job.artifact.artifact_id,),
                job.definition.selector.scope,
                results[index],
                request.specification.ref,
                ANALYZER,
                reading.utc,
            )
            for index, job in enumerate(jobs)
        )
        response = AnalysisResponse(observations)
        validate_analysis_response(request, response)
        return Success(response)

    def _plan(self, request: AnalysisRequest) -> tuple[_Job, ...]:
        for definition in request.definitions:
            reason = _unsupported_reason(definition)
            if reason is not None:
                raise _reject(PortFailureCode.UNSUPPORTED, f"{_label(definition)}: {reason}")
        root_keys = {binding.artifact_id: binding.root_key for binding in request.root_bindings}
        members = request.state.audio.artifacts
        jobs = []
        for definition in request.definitions:
            (source,) = definition.sources
            matching = [artifact for artifact in members if _matches(artifact, source)]
            if len(matching) != 1:
                found = "no" if not matching else f"{len(matching)} ambiguous"
                raise _reject(
                    PortFailureCode.INVALID_INPUT,
                    f"{_label(definition)}: {found} current artifacts match the declared source subject",
                )
            (artifact,) = matching
            span = definition.selector.scope.time_range
            if artifact.timeline_start_seconds > span.start_seconds or artifact.timeline_end_seconds < span.end_seconds:
                raise _reject(
                    PortFailureCode.INVALID_INPUT,
                    f"{_label(definition)}: artifact {artifact.artifact_id} does not cover the requested time range",
                )
            root_key = root_keys[artifact.artifact_id]
            if root_key not in self._roots:
                raise _reject(
                    PortFailureCode.INVALID_INPUT,
                    f"{_label(definition)}: root key {root_key!r} of artifact {artifact.artifact_id} is not configured",
                )
            jobs.append(_Job(definition, artifact, root_key))
        return tuple(jobs)

    def _read(self, artifact: AudioArtifact, root_key: str) -> np.ndarray:
        location = resolve_artifact_location(artifact, root=self._roots[root_key])
        checked = check_resolved_wav_integrity(
            location, max_file_bytes=self._limits.max_file_bytes, max_decoded_bytes=self._limits.max_decoded_bytes
        )
        return checked.samples

    def _fresh_id(self) -> ObservationId:
        token = self._identities.next_token()
        if not isinstance(token, str):
            raise ValueError(f"identity source returned {type(token).__name__}, not a string token")
        observation_id = ObservationId(token)
        if observation_id in self._issued:
            raise ValueError(f"identity source reused token {token!r}")
        self._issued.add(observation_id)
        return observation_id


def _measure(job: _Job, samples: np.ndarray) -> LevelResult:
    """Apply the job's kernel to its selected frames; an empty selection is reported by the kernel."""
    first, stop = frame_bounds(job.artifact, job.definition.selector.scope.time_range)
    return _KERNELS[job.definition.selector.metric](samples[first:stop])
