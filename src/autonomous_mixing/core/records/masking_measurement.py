"""P1 directional masking: preparation and measurement-plan records (R1; method spec 2026-11-20, sections 2-4).

Pure identity and geometry, no arrays and no I/O. One ordered pair names a direction: A (``masker``) supplies the
model threshold and B (``target``) the band energy, and ``(A, B)`` and ``(B, A)`` are different jobs. The first
profile is native mono 44,100 Hz PCM_16 audio of equal length and timeline start; the dist10 call grid of that
profile fixes geometry: call ``k`` is eligible when ``k >= 2`` and ``576k + 256 <= N``, and its source window is
``[576k - 768, 576k + 256)``. A requested interval keeps only eligible calls whose complete window lies inside it,
and interval boundaries must be integral sample positions (never rounded).

R1 provides **geometric support U** only: declared region bands crossed with those calls. The qualified initial
support S0 needs an approved qualification policy and numerical deadband (section 8), and none exists, so no record
here can describe a qualified plan: qualification is ``UNCALIBRATED`` and the support kind is ``GEOMETRIC``. A
prepared plan is a descriptive record of U and of the baseline it was prepared from; it is never qualified evidence.
"""

import bisect
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from autonomous_mixing.core.records.identifiers import ArtifactId, TrackId, VersionRef
from autonomous_mixing.core.records.integrity import ArtifactRootBinding
from autonomous_mixing.core.records.invariants import (
    require_count,
    require_finite,
    require_instance,
    require_text,
    require_tuple_of,
    require_utc,
)
from autonomous_mixing.core.records.observation import TimeRange
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.verification_evidence import SourceRequirement

P1_PROFILE = VersionRef("p1.dist10_mono_pcm16_44100", "1")
P1_SAMPLE_RATE_HZ = 44100
P1_CHANNEL_COUNT = 1
P1_MODEL_BANDS = 21  # every band is kept descriptively in raw evidence
P1_REGION_BANDS = frozenset(range(1, 21))  # band 0 also receives bins 465-512 (partition 0): descriptive only
CALL_HOP_SAMPLES = 576
WINDOW_LEAD_SAMPLES = 768  # window start = 576k - 768
WINDOW_TAIL_SAMPLES = 256  # window end = 576k + 256 (exclusive)
FIRST_ELIGIBLE_CALL = 2
_SHA256 = frozenset("0123456789abcdef")


class QualificationStatus(Enum):
    """Whether qualification predicates are approved. R1 has no calibration: only ``UNCALIBRATED`` exists."""

    UNCALIBRATED = "uncalibrated"


class SupportKind(Enum):
    """Which support a plan describes. Only geometric support U exists; a qualified S0 kind needs calibration."""

    GEOMETRIC = "geometric"


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or not set(value) <= _SHA256:
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def interval_samples(time_range: TimeRange, *, timeline_start_seconds: float, sample_rate_hz: int,
                     frame_count: int) -> tuple[int, int]:  # fmt: skip
    """``[start, stop)`` sample positions of ``time_range`` on a clip, exactly; never rounded.

    Sample ``i`` has the declared project time ``timeline_start_seconds + i / sample_rate_hz`` (the float expression
    the level analyzer's ``frame_bounds`` uses). A boundary is an integral sample position only when it *equals* that
    expression for some ``i`` in ``[0, N]``; the position is found by a lower-bound search and then compared exactly,
    with no tolerance. Any other boundary, including one outside the clip, raises ``ValueError``: the method rejects
    rather than rounds it (spec section 2).
    """
    require_instance(time_range, TimeRange, "time_range")
    rate = require_count(sample_rate_hz, "sample_rate_hz", minimum=1)
    n = require_count(frame_count, "frame_count", minimum=0)
    start = require_finite(timeline_start_seconds, "timeline_start_seconds")

    def time_of(i: int) -> float:
        return start + i / rate

    positions = range(n + 1)
    bounds = []
    for label, value in (("start", time_range.start_seconds), ("end", time_range.end_seconds)):
        i = bisect.bisect_left(positions, value, key=time_of)
        if i > n or time_of(i) != value:
            where = "outside the clip" if i > n or value < start else "not an integral sample position"
            raise ValueError(f"interval {label} {value!r} s is {where} at {rate} Hz (clip of {n} samples)")
        bounds.append(i)
    return bounds[0], bounds[1]


def complete_window_calls(*, frame_count: int, start_sample: int, stop_sample: int) -> tuple[int, int]:
    """``(first_call, call_count)`` of the eligible calls whose complete source window lies in the interval.

    Eligible: ``k >= 2`` and ``576k + 256 <= N``. Inside: ``576k - 768 >= start`` and ``576k + 256 <= stop``. Zero
    calls is a valid answer (a short interval may leave nothing assessable); it is never padded.
    """
    n = require_count(frame_count, "frame_count", minimum=0)
    start, stop = require_count(start_sample, "start_sample", minimum=0), require_count(stop_sample, "stop_sample",
                                                                                         minimum=0)  # fmt: skip
    if stop > n or start > stop:
        raise ValueError(f"interval [{start}, {stop}) samples does not lie within a clip of {n} samples")
    lowest = max(FIRST_ELIGIBLE_CALL, -(-(start + WINDOW_LEAD_SAMPLES) // CALL_HOP_SAMPLES))
    highest = (min(stop, n) - WINDOW_TAIL_SAMPLES) // CALL_HOP_SAMPLES
    count = max(0, highest - lowest + 1)
    return (lowest if count else 0), count


@dataclass(frozen=True)
class MaskingPair:
    """One direction: ``masker`` (A) supplies the threshold, ``target`` (B) the energy. Never reordered."""

    masker: TrackId
    target: TrackId

    def __post_init__(self) -> None:
        require_instance(self.masker, TrackId, "MaskingPair.masker")
        require_instance(self.target, TrackId, "MaskingPair.target")
        if self.masker == self.target:
            raise ValueError("MaskingPair needs two different tracks")

    @property
    def track_ids(self) -> tuple[TrackId, TrackId]:
        """The ordered scope ``(A, B)`` that this direction's jobs and observations use."""
        return (self.masker, self.target)


@dataclass(frozen=True)
class AlignmentProvenance:
    """How the common sample origin of a pair was established (a declaration, never sample-level authentication).

    ``method`` is the identity measurement definitions cite as their ``alignment``; ``statement`` says what was
    declared (for example a documented common export selection). Hashes and metadata alone do not prove alignment.
    """

    method: VersionRef
    statement: str

    def __post_init__(self) -> None:
        require_instance(self.method, VersionRef, "AlignmentProvenance.method")
        require_text(self.statement, "AlignmentProvenance.statement")


@dataclass(frozen=True)
class BandRegion:
    """A fixed list of whole model-band indices, chosen in configuration before any outcome is seen."""

    name: str
    bands: tuple[int, ...]

    def __post_init__(self) -> None:
        require_text(self.name, "BandRegion.name")
        if not isinstance(self.bands, tuple) or not self.bands or any(type(b) is not int for b in self.bands):
            raise ValueError("BandRegion.bands must be a nonempty tuple of band indices")
        if list(self.bands) != sorted(set(self.bands)):
            raise ValueError("BandRegion.bands must be strictly ascending")
        if 0 in self.bands:
            raise ValueError("band 0 is descriptive only: dist10 also maps bins 465-512 into partition 0")
        if not set(self.bands) <= P1_REGION_BANDS:
            raise ValueError("BandRegion.bands must lie in the supported set 1-20")


def _require_profile_source(source: SourceRequirement, track: TrackId, field: str) -> None:
    require_instance(source, SourceRequirement, field)
    if source.source_track_ids != (track,):
        raise ValueError(f"{field} must be the single-track source of {track}")
    if source.sample_rate_hz != P1_SAMPLE_RATE_HZ or source.channel_count != P1_CHANNEL_COUNT:
        raise ValueError(f"{field} must be native mono {P1_SAMPLE_RATE_HZ} Hz (no resampling or fold-down)")


@dataclass(frozen=True)
class MaskingPreparationJob:
    """One direction over one interval and region, with both ordered sources and the alignment provenance."""

    pair: MaskingPair
    time_range: TimeRange
    region: BandRegion
    masker_source: SourceRequirement
    target_source: SourceRequirement
    alignment: AlignmentProvenance

    def __post_init__(self) -> None:
        require_instance(self.pair, MaskingPair, "MaskingPreparationJob.pair")
        require_instance(self.time_range, TimeRange, "MaskingPreparationJob.time_range")
        require_instance(self.region, BandRegion, "MaskingPreparationJob.region")
        _require_profile_source(self.masker_source, self.pair.masker, "MaskingPreparationJob.masker_source")
        _require_profile_source(self.target_source, self.pair.target, "MaskingPreparationJob.target_source")
        require_instance(self.alignment, AlignmentProvenance, "MaskingPreparationJob.alignment")
        a, b = self.masker_source, self.target_source
        if (a.frame_count, a.timeline_start_seconds) != (b.frame_count, b.timeline_start_seconds):
            raise ValueError("the pair's sources must agree exactly in sample count and timeline start (no padding)")

    @property
    def key(self) -> tuple[MaskingPair, TimeRange]:
        return (self.pair, self.time_range)


@dataclass(frozen=True)
class MaskingPreparationRequest:
    """Prepare support for one initial runtime state before any run is constructed (spec section 4).

    Names the producer-supplied initial state and its current-member roots, the profile, the ordered jobs and the
    resource bounds. It holds no reference target and no AI proposal. ``qualification`` is ``UNCALIBRATED``: only
    geometric support U can be prepared. The bounds are resource limits, not scientific thresholds.
    """

    state: ProjectState
    root_bindings: tuple[ArtifactRootBinding, ...]
    profile: VersionRef
    jobs: tuple[MaskingPreparationJob, ...]
    qualification: QualificationStatus
    max_file_bytes: int
    max_decoded_bytes: int
    max_elapsed_seconds: float

    def __post_init__(self) -> None:
        require_instance(self.state, ProjectState, "MaskingPreparationRequest.state")
        require_tuple_of(self.root_bindings, ArtifactRootBinding, "MaskingPreparationRequest.root_bindings",
                         nonempty=True, distinct=True)  # fmt: skip
        if self.profile != P1_PROFILE:
            raise ValueError(f"MaskingPreparationRequest.profile must be {P1_PROFILE.name}@{P1_PROFILE.version}")
        require_tuple_of(self.jobs, MaskingPreparationJob, "MaskingPreparationRequest.jobs", nonempty=True,
                         distinct=True)  # fmt: skip
        keys = [job.key for job in self.jobs]
        if len(set(keys)) != len(keys):
            raise ValueError("each direction and interval is prepared once")
        require_instance(self.qualification, QualificationStatus, "MaskingPreparationRequest.qualification")
        require_count(self.max_file_bytes, "MaskingPreparationRequest.max_file_bytes", minimum=1)
        require_count(self.max_decoded_bytes, "MaskingPreparationRequest.max_decoded_bytes", minimum=1)
        if require_finite(self.max_elapsed_seconds, "MaskingPreparationRequest.max_elapsed_seconds") <= 0:
            raise ValueError("MaskingPreparationRequest.max_elapsed_seconds must be positive")
        members = {artifact.artifact_id for artifact in self.state.audio.artifacts}
        bound = {binding.artifact_id for binding in self.root_bindings}
        if bound != members:
            raise ValueError("root_bindings must bind exactly the state's current audio members")


def support_digest(*, profile: VersionRef, pair: MaskingPair, region: BandRegion, sample_rate_hz: int,
                   frame_count: int, timeline_start_seconds: float, start_sample: int, stop_sample: int,
                   first_call: int, call_count: int) -> str:  # fmt: skip
    """The identity of geometric support U: profile, ordered pair, region, sample geometry and the exact calls.

    Two supports with the same cell count but different cells, geometry or direction have different digests.
    """
    canonical = json.dumps(
        {"kind": SupportKind.GEOMETRIC.value, "profile": [profile.name, profile.version],
         "pair": [str(pair.masker), str(pair.target)], "region": list(region.bands), "rate": sample_rate_hz,
         "frames": frame_count, "timeline_start": float(timeline_start_seconds).hex(), "start": start_sample,
         "stop": stop_sample, "first_call": first_call, "calls": call_count},
        sort_keys=True, separators=(",", ":"),
    )  # fmt: skip
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PreparedSupport:
    """Geometric support U of one job, with the baseline artifacts it was prepared from (descriptive only).

    The baseline hashes are provenance: later states bind their own current artifacts, which must share this exact
    sample geometry; they need not equal these bytes. ``digest`` must equal ``support_digest`` of the fields.
    """

    pair: MaskingPair
    region: BandRegion
    time_range: TimeRange
    alignment: AlignmentProvenance
    masker_artifact_id: ArtifactId
    target_artifact_id: ArtifactId
    masker_sha256: str
    target_sha256: str
    sample_rate_hz: int
    frame_count: int
    timeline_start_seconds: float
    start_sample: int
    stop_sample: int
    first_call: int
    call_count: int
    digest: str

    def __post_init__(self) -> None:
        require_instance(self.pair, MaskingPair, "PreparedSupport.pair")
        require_instance(self.region, BandRegion, "PreparedSupport.region")
        require_instance(self.time_range, TimeRange, "PreparedSupport.time_range")
        require_instance(self.alignment, AlignmentProvenance, "PreparedSupport.alignment")
        require_instance(self.masker_artifact_id, ArtifactId, "PreparedSupport.masker_artifact_id")
        require_instance(self.target_artifact_id, ArtifactId, "PreparedSupport.target_artifact_id")
        _require_sha256(self.masker_sha256, "PreparedSupport.masker_sha256")
        _require_sha256(self.target_sha256, "PreparedSupport.target_sha256")
        if self.sample_rate_hz != P1_SAMPLE_RATE_HZ:
            raise ValueError("PreparedSupport.sample_rate_hz must be the profile rate")
        geometry = interval_samples(self.time_range, timeline_start_seconds=self.timeline_start_seconds,
                                    sample_rate_hz=self.sample_rate_hz, frame_count=self.frame_count)  # fmt: skip
        if geometry != (self.start_sample, self.stop_sample):
            raise ValueError("PreparedSupport sample bounds differ from its interval on its clip")
        calls = complete_window_calls(frame_count=self.frame_count, start_sample=self.start_sample,
                                      stop_sample=self.stop_sample)  # fmt: skip
        if calls != (self.first_call, self.call_count):
            raise ValueError("PreparedSupport calls differ from the complete-window eligible calls of its interval")
        expected = support_digest(profile=P1_PROFILE, pair=self.pair, region=self.region,
                                  sample_rate_hz=self.sample_rate_hz, frame_count=self.frame_count,
                                  timeline_start_seconds=self.timeline_start_seconds, start_sample=self.start_sample,
                                  stop_sample=self.stop_sample, first_call=self.first_call,
                                  call_count=self.call_count)  # fmt: skip
        if _require_sha256(self.digest, "PreparedSupport.digest") != expected:
            raise ValueError("PreparedSupport.digest is not the identity of its support")

    @property
    def cell_count(self) -> int:
        return self.call_count * len(self.region.bands)

    @property
    def kind(self) -> SupportKind:
        return SupportKind.GEOMETRIC


@dataclass(frozen=True)
class PreparationTiming:
    """When preparation ran and how long it took; reported separately from any run budget (spec section 4)."""

    started_at: datetime
    finished_at: datetime
    elapsed_seconds: float

    def __post_init__(self) -> None:
        require_utc(self.started_at, "PreparationTiming.started_at")
        require_utc(self.finished_at, "PreparationTiming.finished_at")
        if require_finite(self.elapsed_seconds, "PreparationTiming.elapsed_seconds") < 0:
            raise ValueError("PreparationTiming.elapsed_seconds must be non-negative")
        if self.finished_at < self.started_at:
            raise ValueError("PreparationTiming must not finish before it starts")


@dataclass(frozen=True)
class MaskingPreparedPlan:
    """The persisted, descriptive result of preparation: geometric support of every job, never qualified.

    ``kind`` is ``GEOMETRIC`` and ``qualification`` ``UNCALIBRATED``; no field can mark it qualified. Its
    content-addressed manifest digest is held by ``MaskingMeasurementPlan``, not by the plan itself.
    """

    profile: VersionRef
    supports: tuple[PreparedSupport, ...]
    kind: SupportKind
    qualification: QualificationStatus
    max_file_bytes: int
    max_decoded_bytes: int
    timing: PreparationTiming

    def __post_init__(self) -> None:
        if self.profile != P1_PROFILE:
            raise ValueError("MaskingPreparedPlan.profile must be the P1 profile")
        require_tuple_of(self.supports, PreparedSupport, "MaskingPreparedPlan.supports", nonempty=True,
                         distinct=True)  # fmt: skip
        keys = [(s.pair, s.time_range) for s in self.supports]
        if len(set(keys)) != len(keys):
            raise ValueError("each direction and interval has exactly one prepared support")
        require_instance(self.kind, SupportKind, "MaskingPreparedPlan.kind")
        require_instance(self.qualification, QualificationStatus, "MaskingPreparedPlan.qualification")
        require_count(self.max_file_bytes, "MaskingPreparedPlan.max_file_bytes", minimum=1)
        require_count(self.max_decoded_bytes, "MaskingPreparedPlan.max_decoded_bytes", minimum=1)
        require_instance(self.timing, PreparationTiming, "MaskingPreparedPlan.timing")

    def support(self, pair: MaskingPair, time_range: TimeRange) -> PreparedSupport | None:
        return next((s for s in self.supports if s.pair == pair and s.time_range == time_range), None)


PREPARED_DIGEST_MARKER = "+prepared.sha256."


def bound_specification(name: str, version: str, prepared_digest: str) -> VersionRef:
    """The analysis-specification identity bound to one prepared plan: ``version + "+prepared.sha256." + digest``.

    Spec section 4: the final specification identity binds the prepared-plan digest, so a specification can never
    name, or be re-pointed to, other prepared support. ``version`` is the specification's own version and must not
    already contain the marker.
    """
    require_text(name, "specification name")
    require_text(version, "specification version")
    if PREPARED_DIGEST_MARKER in version or "+" in version:
        raise ValueError("the specification's own version must not contain '+'")
    return VersionRef(name, f"{version}{PREPARED_DIGEST_MARKER}{_require_sha256(prepared_digest, 'prepared_digest')}")


@dataclass(frozen=True)
class MaskingMeasurementPlan:
    """Binds one analysis specification to exactly one persisted prepared plan (content-addressed).

    ``specification`` must be ``bound_specification(..., prepared_digest)``: its identity carries the digest, so the
    same specification cannot be registered with different prepared support. The directional analyzer measures a
    request only when its specification is ``specification`` and the persisted manifest with ``prepared_digest``
    verifies to exactly ``prepared``; missing or changed preparation evidence refuses analysis rather than
    regenerating support.
    """

    specification: VersionRef
    analyzer: VersionRef
    prepared_digest: str
    prepared: MaskingPreparedPlan

    def __post_init__(self) -> None:
        require_instance(self.specification, VersionRef, "MaskingMeasurementPlan.specification")
        require_instance(self.analyzer, VersionRef, "MaskingMeasurementPlan.analyzer")
        _require_sha256(self.prepared_digest, "MaskingMeasurementPlan.prepared_digest")
        require_instance(self.prepared, MaskingPreparedPlan, "MaskingMeasurementPlan.prepared")
        own, marker, digest = self.specification.version.partition(PREPARED_DIGEST_MARKER)
        if not own or not marker or digest != self.prepared_digest:
            raise ValueError("MaskingMeasurementPlan.specification must be bound to its prepared-plan digest "
                             "(bound_specification)")  # fmt: skip
