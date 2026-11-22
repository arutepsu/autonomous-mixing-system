"""File-backed ``AudioAnalyzer`` for P1 directional model-margin measurements (R1 Task 3; method spec sections 3-5).

One job measures one direction: ``scope.track_ids == (A, B)`` with ``sources == (A's source, B's source)``; A is the
masker (threshold ``T_A``), B the target (energy ``E_B``). ``(A, B)`` and ``(B, A)`` are different jobs. The job's
specification must be registered with a ``MaskingMeasurementPlan`` whose persisted manifest verifies; the job's
``(pair, time_range)`` selects that plan's geometric support U.

Each current member is read once through ``wav_input.read_dist10_member`` and run **as a whole clip from call 0**
through ``dist10_clip_to_bands`` (never cropped: predictor and threshold histories need the prefix). The margin is
``directional_masking_margin_db(T_A, E_B)`` on all calls and all 21 bands; U then selects cells. No numerical kernel
is duplicated here.

Metrics (all summaries have the denominator ``|U|``, the geometric support, never a qualified S0):

- ``p1.raw.*@1``: descriptive geometric-support summaries and exact-silence / quiet-floor / non-finite cell counts.
  An empty U makes every summary except the cell count ``Unavailable``; a non-finite margin in U makes the margin
  summaries ``Unavailable`` (never replaced by zero). Positive raw margins with a silent target are counted as
  positive margins and reported in ``p1.raw.target_silent_cells``: they are not a masking diagnosis.
- ``p1.*@1`` (qualified catalogue of spec section 5): always ``Unavailable`` with an explicit uncalibrated-policy
  reason, because no qualification policy or deadband is approved; geometric support is never reported under them.

``Q[b]`` is the existing long-band mapping applied to the pinned partition ``qthr`` table (``quiet_floor_bands``), a
per-profile constant. A cell's masker threshold equal to ``Q[b]`` gives no evidence of elevation above quiet; the
band comparison does not identify partition shares.

Raw evidence (schema ``autonomous_mixing.p1_directional_evidence@1``, canonical JSON, one content-addressed file per
direction and interval in the request) holds the full-clip call metadata, ``T_A``, ``E_B`` and ``M`` on all 21 bands
(``null`` where M is undefined, with the exact-zero masks), Q and its provenance, the exact support cells and their
flags, source hashes, the prepared-plan and support digests and the observation IDs it backs. It is written before
success; a write failure, or evidence beyond ``max_evidence_bytes``, returns ``UNAVAILABLE``. Evidence is model
output, never a sufficiency or audibility claim.
"""

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from autonomous_mixing.adapters.dsp_analysis.bands import dist10_long_bands
from autonomous_mixing.adapters.dsp_analysis.front_end import ClipBandAnalysis, dist10_clip_to_bands
from autonomous_mixing.adapters.dsp_analysis.masking import MaskingMarginGrid, directional_masking_margin_db
from autonomous_mixing.adapters.dsp_analysis.masking_preparation import (
    PreparationStoreError,
    load_prepared_plan,
    single_member,
    write_content_addressed,
)
from autonomous_mixing.adapters.dsp_analysis.thresholds import DIST10_LONG_QTHR_44100
from autonomous_mixing.adapters.dsp_analysis.wav_input import Dist10Pcm16Clip, Dist10ReadError, read_dist10_member
from autonomous_mixing.core.ports.analysis import validate_analysis_response
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource, RunClock
from autonomous_mixing.core.records.analysis import AnalysisRequest, AnalysisResponse
from autonomous_mixing.core.records.artifacts import AudioArtifact
from autonomous_mixing.core.records.coordinator_runtime import ClockReading
from autonomous_mixing.core.records.identifiers import ArtifactId, ObservationId, VersionRef
from autonomous_mixing.core.records.invariants import require_instance, require_text
from autonomous_mixing.core.records.masking_measurement import (
    P1_MODEL_BANDS,
    P1_PROFILE,
    MaskingMeasurementPlan,
    MaskingPair,
    PreparedSupport,
)
from autonomous_mixing.core.records.observation import MeasuredScalar, Observation, TimeRange, Unavailable
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, PortResult, Success
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition

ANALYZER = VersionRef("autonomous_mixing.p1_directional_analyzer", "1")
EVIDENCE_SCHEMA = "autonomous_mixing.p1_directional_evidence"
EVIDENCE_SCHEMA_VERSION = 1


def _ref(name: str) -> VersionRef:
    return VersionRef(name, "1")


# Descriptive metrics over geometric support U (distinct IDs from the qualified catalogue).
RAW_GEOMETRIC_SUPPORT_CELLS = _ref("p1.raw.geometric_support_cells")
RAW_SIGNED_MARGIN_MEDIAN_DB = _ref("p1.raw.signed_margin_median_db")
RAW_POSITIVE_MARGIN_CELLS = _ref("p1.raw.positive_margin_cells")
RAW_POSITIVE_MARGIN_FRACTION = _ref("p1.raw.positive_margin_fraction")
RAW_MEAN_POSITIVE_MARGIN_DB = _ref("p1.raw.mean_positive_margin_db")
RAW_TARGET_SILENT_CELLS = _ref("p1.raw.target_silent_cells")
RAW_MASKER_SILENT_CELLS = _ref("p1.raw.masker_silent_cells")
RAW_QUIET_FLOOR_THRESHOLD_CELLS = _ref("p1.raw.quiet_floor_threshold_cells")
RAW_NONFINITE_MARGIN_CELLS = _ref("p1.raw.nonfinite_margin_cells")
RAW_METRICS = {
    RAW_GEOMETRIC_SUPPORT_CELLS: "cells",
    RAW_SIGNED_MARGIN_MEDIAN_DB: "dB",
    RAW_POSITIVE_MARGIN_CELLS: "cells",
    RAW_POSITIVE_MARGIN_FRACTION: "fraction",
    RAW_MEAN_POSITIVE_MARGIN_DB: "dB",
    RAW_TARGET_SILENT_CELLS: "cells",
    RAW_MASKER_SILENT_CELLS: "cells",
    RAW_QUIET_FLOOR_THRESHOLD_CELLS: "cells",
    RAW_NONFINITE_MARGIN_CELLS: "cells",
}
# Qualified catalogue (spec section 5): defined over S0, which needs an approved policy. Always Unavailable in R1.
QUALIFIED_METRICS = {
    _ref("p1.support_cells"): "cells",
    _ref("p1.signed_margin_median_db"): "dB",
    _ref("p1.positive_margin_cells"): "cells",
    _ref("p1.positive_margin_fraction"): "fraction",
    _ref("p1.robust_exceedance_cells"): "cells",
    _ref("p1.longest_robust_run_seconds"): "s",
    _ref("p1.mean_positive_margin_db"): "dB",
}
UNCALIBRATED_REASON = (
    "uncalibrated qualification policy: no approved qualification predicates, deadband or initial qualified support "
    "S0 exist (P1 method spec section 8); geometric support is not reported as qualified evidence"
)
_MARGIN_SUMMARIES = {RAW_SIGNED_MARGIN_MEDIAN_DB, RAW_POSITIVE_MARGIN_CELLS, RAW_POSITIVE_MARGIN_FRACTION,
                     RAW_MEAN_POSITIVE_MARGIN_DB}  # fmt: skip
_QTHR_PROVENANCE = "dist10_long_bands applied to thresholds.DIST10_LONG_QTHR_44100 (63 partitions -> 21 bands)"


def quiet_floor_bands() -> np.ndarray:
    """``Q[b]``: the long-band mapping of the pinned partition threshold-in-quiet table (read-only, 21 values)."""
    qthr = np.asarray(DIST10_LONG_QTHR_44100, dtype=np.float64)[None, :]
    q = dist10_long_bands(qthr, qthr).thm[0].copy()
    q.setflags(write=False)
    return q


def _qthr_sha256() -> str:
    return hashlib.sha256(json.dumps([float(v) for v in DIST10_LONG_QTHR_44100]).encode()).hexdigest()


@dataclass(frozen=True)
class _Job:
    definition: MeasurementDefinition
    support: PreparedSupport
    masker: AudioArtifact
    target: AudioArtifact


@dataclass(frozen=True, eq=False)
class DirectionalCells:
    """Adapter-local values of one direction on its support (arrays read-only; equality is identity)."""

    margin: MaskingMarginGrid
    cells: tuple[tuple[int, int], ...]
    margin_values: np.ndarray
    target_silent: np.ndarray
    masker_silent: np.ndarray
    quiet_floor: np.ndarray
    nonfinite: np.ndarray


class _Rejected(Exception):
    def __init__(self, code: PortFailureCode, message: str) -> None:
        super().__init__(message)
        self.failure = PortFailure(code, message, retryable=False)


def _label(definition: MeasurementDefinition) -> str:
    metric, scope = definition.selector.metric, definition.selector.scope
    return f"job {metric.name}@{metric.version} over {', '.join(str(t) for t in scope.track_ids)}"


def _unit(metric: VersionRef) -> str | None:
    return RAW_METRICS.get(metric) or QUALIFIED_METRICS.get(metric)


def directional_cells(masker: ClipBandAnalysis, target: ClipBandAnalysis, support: PreparedSupport,
                      q: np.ndarray) -> DirectionalCells:  # fmt: skip
    """Margins of masker over target on all calls and bands, then the support cells in (call, band) order."""
    if masker.sample_count != target.sample_count or masker.sample_count != support.frame_count:
        raise ValueError("clip lengths differ from the prepared support")
    margin = directional_masking_margin_db(masker.thm, target.en)
    calls = np.arange(support.first_call, support.first_call + support.call_count)
    bands = np.asarray(support.region.bands, dtype=np.int64)
    if calls.size and not masker.eligible[calls].all():
        raise ValueError("prepared support names an ineligible call")
    rows, columns = np.repeat(calls, bands.size), np.tile(bands, calls.size)
    values = margin.margin_db[rows, columns].copy()
    result = {
        "target_silent": target.nonzero_samples[rows] == 0,
        "masker_silent": masker.nonzero_samples[rows] == 0,
        "quiet_floor": masker.thm[rows, columns] == q[columns],
        "nonfinite": ~np.isfinite(values),
    }
    for array in (values, *result.values()):
        array.setflags(write=False)
    cells = tuple((int(k), int(b)) for k, b in zip(rows, columns, strict=True))
    return DirectionalCells(margin, cells, values, **result)


def summarize(metric: VersionRef, cells: DirectionalCells) -> MeasuredScalar | Unavailable:
    """One metric over the geometric support (denominator ``|U|``)."""
    if metric in QUALIFIED_METRICS:
        return Unavailable(UNCALIBRATED_REASON)
    unit, size = RAW_METRICS[metric], len(cells.cells)
    if metric == RAW_GEOMETRIC_SUPPORT_CELLS:
        return MeasuredScalar(float(size), unit)
    if size == 0:
        return Unavailable("empty geometric support: no complete-window eligible call in the interval")
    counts = {
        RAW_TARGET_SILENT_CELLS: cells.target_silent,
        RAW_MASKER_SILENT_CELLS: cells.masker_silent,
        RAW_QUIET_FLOOR_THRESHOLD_CELLS: cells.quiet_floor,
        RAW_NONFINITE_MARGIN_CELLS: cells.nonfinite,
    }
    if metric in counts:
        return MeasuredScalar(float(np.count_nonzero(counts[metric])), unit)
    missing = int(np.count_nonzero(cells.nonfinite))
    if missing:
        return Unavailable(f"{missing} of {size} support cells have an undefined margin (zero threshold or energy)")
    values = cells.margin_values
    if metric == RAW_SIGNED_MARGIN_MEDIAN_DB:
        return MeasuredScalar(float(np.median(values)), unit)
    positive = int(np.count_nonzero(values > 0.0))
    if metric == RAW_POSITIVE_MARGIN_CELLS:
        return MeasuredScalar(float(positive), unit)
    if metric == RAW_POSITIVE_MARGIN_FRACTION:
        return MeasuredScalar(positive / size, unit)
    if metric == RAW_MEAN_POSITIVE_MARGIN_DB:
        return MeasuredScalar(float(np.sum(np.maximum(values, 0.0))) / size, unit)
    raise ValueError(f"unknown metric {metric}")


def _grid(values: np.ndarray) -> list:
    return [[float(v) if np.isfinite(v) else None for v in row] for row in values]


def _canonical(document: object) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


class DirectionalMaskingAnalyzer:
    """Implements ``AudioAnalyzer`` for P1 directional jobs over configured roots and registered prepared plans.

    ``plans`` registers one ``MaskingMeasurementPlan`` per analysis specification (all must name this analyzer);
    ``plan_store`` holds their manifests, which are re-verified on every call. ``evidence_store`` (an existing
    directory) receives raw evidence. Limits are caller-supplied resource bounds, not scientific thresholds.
    """

    def __init__(self, roots: Mapping[str, str], *, plans: tuple[MaskingMeasurementPlan, ...],
                 plan_store: str | os.PathLike, evidence_store: str | os.PathLike, max_file_bytes: int,
                 max_decoded_bytes: int, max_evidence_bytes: int, clock: RunClock,
                 identities: IdentitySource) -> None:  # fmt: skip
        if not isinstance(roots, Mapping):
            raise ValueError(f"roots must be a Mapping, got {type(roots).__name__}")
        snapshot = dict(roots.items())
        for key, path in snapshot.items():
            require_text(key, "root key")
            require_text(path, f"root path for {key!r}")
        self._roots = snapshot
        if not isinstance(plans, tuple) or not all(isinstance(p, MaskingMeasurementPlan) for p in plans):
            raise ValueError("plans must be a tuple of MaskingMeasurementPlan")
        registry: dict[VersionRef, MaskingMeasurementPlan] = {}
        for plan in plans:
            if plan.analyzer != ANALYZER:
                raise ValueError(f"plan for {plan.specification} names analyzer {plan.analyzer}, not {ANALYZER}")
            if plan.specification in registry:
                raise ValueError(f"two plans register specification {plan.specification}")
            registry[plan.specification] = plan
        self._plans = registry
        self._plan_store, self._evidence_store = Path(plan_store), Path(evidence_store)
        for name, value in (("max_file_bytes", max_file_bytes), ("max_decoded_bytes", max_decoded_bytes),
                            ("max_evidence_bytes", max_evidence_bytes)):  # fmt: skip
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive int")
        self._max_file, self._max_decoded, self._max_evidence = max_file_bytes, max_decoded_bytes, max_evidence_bytes
        if not callable(getattr(clock, "read", None)):
            raise ValueError("clock must provide read()")
        if not callable(getattr(identities, "next_token", None)):
            raise ValueError("identities must provide next_token()")
        self._clock, self._identities = clock, identities
        self._issued: set[ObservationId] = set()
        self._evidence: dict[ObservationId, str] = {}
        self._q = quiet_floor_bands()

    def evidence_digest(self, observation_id: ObservationId) -> str | None:
        """The evidence file digest (in ``evidence_store``) backing an observation this instance returned."""
        return self._evidence.get(observation_id)

    def analyze(self, request: AnalysisRequest) -> PortResult[AnalysisResponse]:
        require_instance(request, AnalysisRequest, "request")
        try:
            plan = self._verified_plan(request)
            jobs = self._plan(request, plan)
        except _Rejected as rejected:
            return rejected.failure
        reading = self._clock.read()
        if not isinstance(reading, ClockReading):
            raise ValueError(f"clock returned {type(reading).__name__}, not a ClockReading")
        clips: dict[ArtifactId, tuple[Dist10Pcm16Clip, ClipBandAnalysis]] = {}
        root_keys = {b.artifact_id: b.root_key for b in request.root_bindings}
        for job in jobs:
            for artifact in (job.masker, job.target):
                if artifact.artifact_id in clips:
                    continue
                root_key = root_keys[artifact.artifact_id]
                try:
                    clip = read_dist10_member(artifact, root=self._roots[root_key], max_file_bytes=self._max_file,
                                              max_decoded_bytes=self._max_decoded)  # fmt: skip
                except Dist10ReadError as error:
                    return PortFailure(PortFailureCode.UNAVAILABLE,
                                       f"artifact {artifact.artifact_id} under root {root_key!r} could not be read "
                                       f"and verified: {error}", retryable=False)  # fmt: skip
                clips[artifact.artifact_id] = (clip, dist10_clip_to_bands(clip.samples, sample_rate_hz=44100))
        directions: dict[tuple[MaskingPair, TimeRange], DirectionalCells] = {}
        for job in jobs:
            key = (job.support.pair, job.support.time_range)
            if key not in directions:
                directions[key] = directional_cells(clips[job.masker.artifact_id][1], clips[job.target.artifact_id][1],
                                                    job.support, self._q)  # fmt: skip
        observations = []
        for job in jobs:
            cells = directions[(job.support.pair, job.support.time_range)]
            observations.append(Observation(
                self._fresh_id(), job.definition.selector.metric, (job.masker.artifact_id, job.target.artifact_id),
                job.definition.selector.scope, summarize(job.definition.selector.metric, cells),
                request.specification.ref, ANALYZER, reading.utc))  # fmt: skip
        response = AnalysisResponse(tuple(observations))
        validate_analysis_response(request, response)
        written: dict[ObservationId, str] = {}
        for key, cells in directions.items():
            members = [(job, obs) for job, obs in zip(jobs, observations, strict=True)
                       if (job.support.pair, job.support.time_range) == key]  # fmt: skip
            payload = self._evidence_payload(plan, members, cells, clips, reading)
            if len(payload) > self._max_evidence:
                return PortFailure(PortFailureCode.UNAVAILABLE,
                                   f"raw evidence of {len(payload)} bytes exceeds max_evidence_bytes", False)  # fmt: skip
            try:
                if not self._evidence_store.is_dir():
                    raise OSError(f"evidence store {self._evidence_store} is not an existing directory")
                digest = write_content_addressed(self._evidence_store, payload)
            except OSError as error:
                return PortFailure(PortFailureCode.UNAVAILABLE, f"raw evidence could not be persisted: {error}", False)
            written.update((obs.observation_id, digest) for _, obs in members)
        self._evidence.update(written)
        return Success(response)

    def _verified_plan(self, request: AnalysisRequest) -> MaskingMeasurementPlan:
        plan = self._plans.get(request.specification.ref)
        if plan is None:
            raise _Rejected(PortFailureCode.UNSUPPORTED,
                            f"no prepared plan is registered for specification {request.specification.ref}")  # fmt: skip
        try:
            stored = load_prepared_plan(self._plan_store, plan.prepared_digest)
        except PreparationStoreError as error:
            raise _Rejected(PortFailureCode.UNAVAILABLE, f"prepared plan {plan.prepared_digest}: {error}") from error
        if stored != plan.prepared:
            raise _Rejected(PortFailureCode.UNAVAILABLE,
                            f"prepared plan {plan.prepared_digest} differs from the registered plan")  # fmt: skip
        return plan

    def _plan(self, request: AnalysisRequest, plan: MaskingMeasurementPlan) -> tuple[_Job, ...]:
        for definition in request.definitions:
            scope = definition.selector.scope
            reason = None
            if definition.analyzer != ANALYZER:
                reason = f"analyzer {definition.analyzer} is not {ANALYZER}"
            elif _unit(definition.selector.metric) is None:
                reason = "metric is not in the P1 catalogue"
            elif definition.unit != _unit(definition.selector.metric):
                reason = f"unit {definition.unit!r} is not {_unit(definition.selector.metric)!r}"
            elif scope.frequency_range is not None:
                reason = "P1 regions are whole model bands of the prepared support, not frequency ranges"
            elif len(scope.track_ids) != 2 or len(definition.sources) != 2:
                reason = "a P1 job names exactly one ordered pair (masker, target) and its two sources"
            elif tuple(s.source_track_ids for s in definition.sources) != tuple((t,) for t in scope.track_ids):
                reason = "sources must be the single-track sources of the scope's masker and target, in that order"
            if reason is not None:
                raise _Rejected(PortFailureCode.UNSUPPORTED, f"{_label(definition)}: {reason}")
        members = request.state.audio.artifacts
        root_keys = {b.artifact_id: b.root_key for b in request.root_bindings}
        jobs = []
        for definition in request.definitions:
            scope = definition.selector.scope
            pair = MaskingPair(*scope.track_ids)
            support = plan.prepared.support(pair, scope.time_range)
            if support is None:
                raise _Rejected(PortFailureCode.INVALID_INPUT,
                                f"{_label(definition)}: no prepared support for this direction and interval")  # fmt: skip
            if definition.alignment != support.alignment.method:
                raise _Rejected(PortFailureCode.INVALID_INPUT,
                                f"{_label(definition)}: alignment {definition.alignment} differs from the prepared "
                                f"provenance {support.alignment.method}")  # fmt: skip
            found = []
            for role, source in zip(("masker", "target"), definition.sources, strict=True):
                artifact = single_member(members, source)
                if artifact is None:
                    raise _Rejected(PortFailureCode.INVALID_INPUT,
                                    f"{_label(definition)}: {role} source does not match exactly one current member")  # fmt: skip
                geometry = (artifact.sample_rate_hz, artifact.frame_count, artifact.timeline_start_seconds)
                if geometry != (support.sample_rate_hz, support.frame_count, support.timeline_start_seconds):
                    raise _Rejected(PortFailureCode.INVALID_INPUT,
                                    f"{_label(definition)}: {role} sample geometry {geometry} differs from the "
                                    f"prepared support")  # fmt: skip
                if root_keys[artifact.artifact_id] not in self._roots:
                    raise _Rejected(PortFailureCode.INVALID_INPUT,
                                    f"{_label(definition)}: root of {artifact.artifact_id} is not configured")  # fmt: skip
                found.append(artifact)
            jobs.append(_Job(definition, support, *found))
        return tuple(jobs)

    def _fresh_id(self) -> ObservationId:
        token = self._identities.next_token()
        if not isinstance(token, str):
            raise ValueError(f"identity source returned {type(token).__name__}, not a string token")
        observation_id = ObservationId(token)
        if observation_id in self._issued:
            raise ValueError(f"identity source reused token {token!r}")
        self._issued.add(observation_id)
        return observation_id

    def _evidence_payload(self, plan: MaskingMeasurementPlan, members, cells: DirectionalCells, clips,
                          reading: ClockReading) -> bytes:  # fmt: skip
        job = members[0][0]
        support = job.support
        masker_clip, masker = clips[job.masker.artifact_id]
        target_clip, target = clips[job.target.artifact_id]

        def source(clip: Dist10Pcm16Clip) -> dict:
            artifact = clip.artifact
            return {"artifact_id": str(artifact.artifact_id), "sha256": clip.verified_sha256,
                    "container": clip.container, "subtype": clip.subtype, "frame_count": artifact.frame_count,
                    "sample_rate_hz": artifact.sample_rate_hz,
                    "timeline_start_seconds": artifact.timeline_start_seconds}  # fmt: skip

        return _canonical({
            "schema": EVIDENCE_SCHEMA,
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "analyzer": [ANALYZER.name, ANALYZER.version],
            "profile": [P1_PROFILE.name, P1_PROFILE.version],
            "specification": [plan.specification.name, plan.specification.version],
            "prepared_digest": plan.prepared_digest,
            "support": {"digest": support.digest, "pair": [str(support.pair.masker), str(support.pair.target)],
                        "region": {"name": support.region.name, "bands": list(support.region.bands)},
                        "time_range": [support.time_range.start_seconds, support.time_range.end_seconds],
                        "start_sample": support.start_sample, "stop_sample": support.stop_sample,
                        "first_call": support.first_call, "call_count": support.call_count,
                        "kind": "geometric", "qualification": "uncalibrated"},
            "alignment": {"method": [support.alignment.method.name, support.alignment.method.version],
                          "statement": support.alignment.statement},
            "sources": {"masker": source(masker_clip), "target": source(target_clip)},
            "quiet_floor": {"provenance": _QTHR_PROVENANCE, "qthr_sha256": _qthr_sha256(),
                            "q": [float(v) for v in self._q]},
            "calls": {"call": masker.call.tolist(), "window_start": masker.window_start.tolist(),
                      "window_end": masker.window_end.tolist(), "eligible": masker.eligible.tolist(),
                      "masker_nonzero_samples": masker.nonzero_samples.tolist(),
                      "target_nonzero_samples": target.nonzero_samples.tolist()},
            "bands": P1_MODEL_BANDS,
            "masker_threshold": masker.thm.tolist(),
            "target_energy": target.en.tolist(),
            "margin_db": _grid(cells.margin.margin_db),
            "zero_threshold": cells.margin.zero_threshold.tolist(),
            "zero_target_energy": cells.margin.zero_target_energy.tolist(),
            "support_cells": [list(cell) for cell in cells.cells],
            "cell_flags": {"target_silent": cells.target_silent.tolist(), "masker_silent": cells.masker_silent.tolist(),
                           "threshold_at_quiet_floor": cells.quiet_floor.tolist(),
                           "nonfinite_margin": cells.nonfinite.tolist()},
            "observations": [{"id": str(obs.observation_id), "metric": [obs.metric.name, obs.metric.version]}
                             for _, obs in members],
            "created_at": reading.utc.isoformat(),
        })  # fmt: skip
