"""P1 support preparation and its content-addressed manifest (R1 Task 2; method spec sections 2-4).

``MaskingPreparer.prepare`` turns a validated ``MaskingPreparationRequest`` into a persisted, descriptive
``MaskingPreparedPlan`` of **geometric support U**: for each ordered pair (masker A, target B), the declared region
bands crossed with the eligible dist10 calls whose complete window lies inside the interval. No qualification
predicate runs and no S0 is produced: the plan is ``GEOMETRIC`` and ``UNCALIBRATED`` by construction, so it cannot be
consumed as qualified support. Preparation runs before a run request exists; it charges no analysis budget.

Call sequence (no partial success; any failure returns a ``PortFailure`` and writes no manifest):

1. Request type (``ValueError`` otherwise). Every job's alignment method must be one the preparer was configured to
   know; unknown provenance is ``UNAVAILABLE`` (spec section 2: unknown alignment yields unavailable evidence).
2. Every job's two sources must each match exactly one current member, with a configured root key
   (``INVALID_INPUT`` otherwise). Interval boundaries must be integral sample positions of that clip
   (``INVALID_INPUT``; never rounded).
3. Each distinct member is resolved and verified once, in job order, through ``wav_input.read_dist10_member``
   (root containment, bounded capture, SHA-256, declared metadata, mono 44,100 Hz PCM_16 counts). A clock reading
   precedes every read: cancellation or elapsed time beyond ``max_elapsed_seconds`` returns ``UNAVAILABLE`` before
   the read, and the completion reading is checked the same way before anything is written. Read failures return
   ``UNAVAILABLE`` naming the code. The verified samples are not kept: U depends only
   on verified geometry; P1 values are computed later, by the analyzer, from each state's current files.
4. The plan is encoded canonically once (sorted keys, no NaN/Inf, explicit cell list). A manifest longer than
   ``MAX_MANIFEST_BYTES``, the bound ``load_prepared_plan`` enforces, returns ``UNAVAILABLE`` and writes nothing, so
   every successful preparation can be reloaded. Otherwise exactly those bytes are written to
   ``<store>/<sha256>.json`` through a temporary file, ``fsync`` and ``os.replace``; an existing file must already
   hold exactly these bytes. A write failure returns ``UNAVAILABLE`` and removes the temporary file.

``load_prepared_plan`` reads a manifest back by digest and refuses a missing, altered, non-canonical, unsupported
or invalid one with ``PreparationStoreError``; it never recreates support. The manifest is evidence of the prepared
geometry and its baseline provenance (verified hashes), not of masking.

Opt-in experimental extension: a supplied ExperimentalMaskingPolicy runs the same full-clip front end once per
verified member and freezes actual screened cells within U. Manifest schema 2 binds policy/source/noise identities;
legacy schema 1 bytes stay unchanged. Inclusive energy/quiet thresholds compare powers directly. Existing reviewed
rounding-noise functions live here and the research script re-exports them. Experimental support is unvalidated,
not scientific qualification; preparation still emits no diagnosis.
"""

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import numpy as np

from autonomous_mixing.adapters.dsp_analysis.bands import dist10_long_bands
from autonomous_mixing.adapters.dsp_analysis.front_end import dist10_clip_to_bands
from autonomous_mixing.adapters.dsp_analysis.partitions import dist10_long_partition_map
from autonomous_mixing.adapters.dsp_analysis.thresholds import DIST10_LONG_QTHR_44100
from autonomous_mixing.adapters.dsp_analysis.wav_input import Dist10Pcm16Clip, Dist10ReadError, read_dist10_member
from autonomous_mixing.core.ports.coordinator_runtime import RunClock
from autonomous_mixing.core.records.artifacts import AudioArtifact
from autonomous_mixing.core.records.coordinator_runtime import ClockReading
from autonomous_mixing.core.records.identifiers import ArtifactId, TrackId, VersionRef
from autonomous_mixing.core.records.invariants import require_instance, require_text
from autonomous_mixing.core.records.masking_measurement import (
    P1_EXACT_DUAL_MONO_PROFILE,
    P1_FLOAT64_PROFILE,
    P1_PROFILE,
    AlignmentProvenance,
    BandRegion,
    ExperimentalMaskingPolicy,
    ExperimentalSupport,
    MaskingPair,
    MaskingPreparationRequest,
    MaskingPreparedPlan,
    PreparationTiming,
    PreparedSupport,
    QualificationStatus,
    SupportKind,
    complete_window_calls,
    interval_samples,
    support_digest,
)
from autonomous_mixing.core.records.observation import TimeRange
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, PortResult, Success
from autonomous_mixing.core.records.verification_evidence import SourceRequirement

MANIFEST_SCHEMA = "autonomous_mixing.p1_prepared_plan"
MANIFEST_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 1 << 24  # resource bound on reading a manifest back, not a scientific threshold
_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class PersistedPreparation:
    """Adapter-local result: the manifest digest (its file name) and the plan it encodes."""

    digest: str
    plan: MaskingPreparedPlan


class PreparationStoreFailureCode(Enum):
    MISSING = "missing"
    DIGEST_MISMATCH = "digest_mismatch"
    MALFORMED = "malformed"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    NOT_CANONICAL = "not_canonical"
    TOO_LARGE = "too_large"


class PreparationStoreError(ValueError):
    """A stored manifest cannot be used; ``code`` names why. Support is never regenerated in its place."""

    def __init__(self, code: PreparationStoreFailureCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code


class _Rejected(Exception):
    def __init__(self, code: PortFailureCode, message: str) -> None:
        super().__init__(message)
        self.failure = PortFailure(code, message, retryable=False)


# --- shared member matching (used by the directional analyzer too) ------------------------------------------------


def matches_source(artifact: AudioArtifact, source: SourceRequirement) -> bool:
    """Whether a current member is exactly the declared source subject (same fields as the level analyzer)."""
    return (
        artifact.kind is source.kind
        and artifact.source_track_ids == source.source_track_ids
        and artifact.render_spec == source.render_spec
        and artifact.sample_rate_hz == source.sample_rate_hz
        and artifact.channel_count == source.channel_count
        and artifact.timeline_start_seconds == source.timeline_start_seconds
        and artifact.frame_count == source.frame_count
    )


def single_member(members: tuple[AudioArtifact, ...], source: SourceRequirement) -> AudioArtifact | None:
    found = [artifact for artifact in members if matches_source(artifact, source)]
    return found[0] if len(found) == 1 else None


# --- canonical manifest -------------------------------------------------------------------------------------------


def _canonical(document: object) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def _version(ref: VersionRef) -> list[str]:
    return [ref.name, ref.version]


def _support_document(support: PreparedSupport) -> dict:
    return {
        "pair": [str(support.pair.masker), str(support.pair.target)],
        "region": {"name": support.region.name, "bands": list(support.region.bands)},
        "time_range": [support.time_range.start_seconds, support.time_range.end_seconds],
        "alignment": {"method": _version(support.alignment.method), "statement": support.alignment.statement},
        "masker": {"artifact_id": str(support.masker_artifact_id), "sha256": support.masker_sha256},
        "target": {"artifact_id": str(support.target_artifact_id), "sha256": support.target_sha256},
        "sample_rate_hz": support.sample_rate_hz,
        "frame_count": support.frame_count,
        "timeline_start_seconds": support.timeline_start_seconds,
        "start_sample": support.start_sample,
        "stop_sample": support.stop_sample,
        "first_call": support.first_call,
        "call_count": support.call_count,
        "cells": [
            [k, b]
            for k in range(support.first_call, support.first_call + support.call_count)
            for b in support.region.bands
        ],  # fmt: skip
        "digest": support.digest,
    }


def _experimental_document(support: ExperimentalSupport) -> dict:
    g = support.geometry
    return {
        "geometry_digest": g.digest,
        "policy_digest": support.policy_digest,
        "masker": [str(g.masker_artifact_id), g.masker_sha256],
        "target": [str(g.target_artifact_id), g.target_sha256],
        "noise_reference_sha256": support.noise_reference_sha256,
        "cells": [list(cell) for cell in support.cells],
        "digest": support.digest,
    }


def _decode_policy(item: dict) -> ExperimentalMaskingPolicy:
    return ExperimentalMaskingPolicy(
        method=VersionRef(*item["method"]),
        energy_above_noise_db=item["energy_above_noise_db"],
        quiet_elevation_db=item["quiet_elevation_db"],
        margin_deadband_db=item["margin_deadband_db"],
        minimum_cells=item["minimum_cells"],
        minimum_robust_fraction=item["minimum_robust_fraction"],
        minimum_consecutive_calls=item["minimum_consecutive_calls"],
    )


def _decode_experimental(item: dict, supports: tuple[PreparedSupport, ...]) -> ExperimentalSupport:
    found = [s for s in supports if s.digest == item["geometry_digest"]]
    if len(found) != 1:
        raise ValueError("experimental support geometry is missing or ambiguous")
    support = ExperimentalSupport(
        found[0], item["policy_digest"], item["noise_reference_sha256"], tuple(tuple(cell) for cell in item["cells"])
    )
    if _experimental_document(support) != item:
        raise ValueError("experimental support differs from its exact encoding")
    return support


def encode_prepared_plan(plan: MaskingPreparedPlan) -> bytes:
    """The canonical manifest bytes of ``plan``; its SHA-256 is the manifest digest."""
    require_instance(plan, MaskingPreparedPlan, "plan")
    document = {
        "schema": MANIFEST_SCHEMA,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "profile": _version(plan.profile),
        "kind": plan.kind.value,
        "qualification": plan.qualification.value,
        "limits": {"max_file_bytes": plan.max_file_bytes, "max_decoded_bytes": plan.max_decoded_bytes},
        "timing": {
            "started_at": plan.timing.started_at.isoformat(),
            "finished_at": plan.timing.finished_at.isoformat(),
            "elapsed_seconds": plan.timing.elapsed_seconds,
        },
        "supports": [_support_document(s) for s in plan.supports],
    }
    if plan.experimental_policy is not None:
        document["schema_version"] = 2
        document["experimental_policy"] = json.loads(plan.experimental_policy.canonical_payload)
        document["experimental_supports"] = [_experimental_document(s) for s in plan.experimental_supports]
    return _canonical(document)


def _refuse_constant(name: str) -> object:
    raise ValueError(f"non-finite JSON constant {name}")


def _decode_support(item: dict) -> PreparedSupport:
    support = PreparedSupport(
        pair=MaskingPair(TrackId(item["pair"][0]), TrackId(item["pair"][1])),
        region=BandRegion(item["region"]["name"], tuple(item["region"]["bands"])),
        time_range=TimeRange(*item["time_range"]),
        alignment=AlignmentProvenance(VersionRef(*item["alignment"]["method"]), item["alignment"]["statement"]),
        masker_artifact_id=ArtifactId(item["masker"]["artifact_id"]),
        target_artifact_id=ArtifactId(item["target"]["artifact_id"]),
        masker_sha256=item["masker"]["sha256"],
        target_sha256=item["target"]["sha256"],
        sample_rate_hz=item["sample_rate_hz"],
        frame_count=item["frame_count"],
        timeline_start_seconds=item["timeline_start_seconds"],
        start_sample=item["start_sample"],
        stop_sample=item["stop_sample"],
        first_call=item["first_call"],
        call_count=item["call_count"],
        digest=item["digest"],
    )
    if _support_document(support) != item:
        raise ValueError("support entry differs from its recomputed encoding (cells or fields)")
    return support


def decode_prepared_plan(payload: bytes) -> MaskingPreparedPlan:
    """Parse and validate canonical manifest bytes; ``PreparationStoreError`` for anything else."""
    try:
        document = json.loads(payload.decode("ascii"), parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError) as error:
        raise PreparationStoreError(PreparationStoreFailureCode.MALFORMED, f"not strict JSON: {error}") from error
    if not isinstance(document, dict):
        raise PreparationStoreError(PreparationStoreFailureCode.MALFORMED, "manifest is not a JSON object")
    if document.get("schema") != MANIFEST_SCHEMA or document.get("schema_version") not in (MANIFEST_SCHEMA_VERSION, 2):
        raise PreparationStoreError(
            PreparationStoreFailureCode.UNSUPPORTED_SCHEMA,
            f"schema {document.get('schema')!r} version {document.get('schema_version')!r} is not "
            f"{MANIFEST_SCHEMA}@{MANIFEST_SCHEMA_VERSION}",
        )
    try:
        timing = document["timing"]
        supports = tuple(_decode_support(item) for item in document["supports"])
        extended = document["schema_version"] == 2
        plan = MaskingPreparedPlan(
            profile=VersionRef(*document["profile"]),
            supports=supports,
            experimental_policy=_decode_policy(document["experimental_policy"]) if extended else None,
            experimental_supports=tuple(
                _decode_experimental(item, supports) for item in document["experimental_supports"]
            )
            if extended
            else (),
            kind=SupportKind(document["kind"]),
            qualification=QualificationStatus(document["qualification"]),
            max_file_bytes=document["limits"]["max_file_bytes"],
            max_decoded_bytes=document["limits"]["max_decoded_bytes"],
            timing=PreparationTiming(
                datetime.fromisoformat(timing["started_at"]),
                datetime.fromisoformat(timing["finished_at"]),
                timing["elapsed_seconds"],
            ),  # fmt: skip
        )
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise PreparationStoreError(PreparationStoreFailureCode.MALFORMED, f"invalid manifest: {error!r}") from error
    if encode_prepared_plan(plan) != payload:
        raise PreparationStoreError(PreparationStoreFailureCode.NOT_CANONICAL, "manifest bytes are not canonical")
    return plan


def _require_digest(digest: object) -> str:
    if not isinstance(digest, str) or len(digest) != 64 or not set(digest) <= _HEX:
        raise ValueError("digest must be a lowercase SHA-256 hex digest")
    return digest


def manifest_path(store: str | os.PathLike, digest: str) -> Path:
    return Path(store) / f"{_require_digest(digest)}.json"


def load_prepared_plan(store: str | os.PathLike, digest: str) -> MaskingPreparedPlan:
    """Read the manifest named by ``digest`` and verify bytes, schema, records and canonical form."""
    path = manifest_path(store, digest)
    try:
        with open(path, "rb") as handle:
            payload = handle.read(MAX_MANIFEST_BYTES + 1)
    except FileNotFoundError as error:
        raise PreparationStoreError(PreparationStoreFailureCode.MISSING, f"no manifest {path.name}") from error
    if len(payload) > MAX_MANIFEST_BYTES:
        raise PreparationStoreError(PreparationStoreFailureCode.TOO_LARGE, f"{path.name} exceeds the read bound")
    if hashlib.sha256(payload).hexdigest() != digest:
        raise PreparationStoreError(PreparationStoreFailureCode.DIGEST_MISMATCH, f"{path.name} was altered")
    return decode_prepared_plan(payload)


def write_content_addressed(store: Path, payload: bytes) -> str:
    """Write ``payload`` as ``<store>/<sha256>.json`` atomically (temporary file, fsync, ``os.replace``).

    An existing file must already hold exactly these bytes. ``OSError`` on failure; the temporary file is removed.
    """
    digest = hashlib.sha256(payload).hexdigest()
    target = manifest_path(store, digest)
    if target.exists():
        if target.read_bytes() != payload:
            raise OSError(f"{target.name} exists with different bytes")
        return digest
    handle, temporary = tempfile.mkstemp(prefix=".prep-", suffix=".tmp", dir=store)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return digest


def rounding_noise_per_bin() -> float:
    """Expected unnormalised FFT energy per bin of int16 rounding noise: (1/12) x sum(w^2), dist10's long window."""
    i = np.arange(1024)
    window = 0.5 * (1.0 - np.cos(2.0 * 3.14159265358979 * (i - 0.5) / 1024))
    return float(np.sum(window * window)) / 12.0


def rounding_noise_partition_energy() -> np.ndarray:
    """Partition energy (63,) of rounding noise over every bin 0..512 the partition mapping consumes.

    Nyquist (bin 512) is included: the FFT energy floor excludes it, but the reproduced dist10 mapping accumulates it,
    with bins 465..511, into partition 0.
    """
    partition = dist10_long_partition_map()
    eb = np.zeros(63)
    for k in range(partition.size):  # 513 bins, Nyquist included
        eb[partition[k]] += rounding_noise_per_bin()
    return eb


def rounding_noise_band_energy() -> np.ndarray:
    """Band energy (21,) of int16 rounding noise, through the partitions and the long-band mapping."""
    eb = rounding_noise_partition_energy()[None, :]
    return dist10_long_bands(eb, eb.copy()).en[0]


def quiet_floor_bands() -> np.ndarray:
    """``Q[b]``: the long-band mapping of the pinned partition threshold-in-quiet table (read-only, 21 values)."""
    qthr = np.asarray(DIST10_LONG_QTHR_44100, dtype=np.float64)[None, :]
    q = dist10_long_bands(qthr, qthr).thm[0].copy()
    q.setflags(write=False)
    return q


def noise_reference_digest(noise: np.ndarray) -> str:
    return hashlib.sha256(
        _canonical({"method": "p1.pcm16_uniform_rounding_noise@1", "band_energy": noise.tolist()})
    ).hexdigest()


def _at_least_db_above(values: np.ndarray, reference: np.ndarray, db: float) -> np.ndarray:
    """Inclusive power-domain comparison, without cancellation or intermediate power overflow.

    Split the nonnegative base-10 exponent into at most three representable factors. Any positive finite binary64
    reference spans less than 633 decimal exponents to binary64 max, so larger reserves cannot admit finite values.
    The extra decade in the early guard makes it insensitive to logarithmic rounding at that unreachable boundary.
    """
    if not np.isfinite(reference).all() or np.any(reference <= 0):
        raise ValueError("screen reference must be finite and positive")
    exponent = float(db) / 10.0
    limit = math.log10(np.finfo(float).max) - math.log10(float(np.min(reference))) + 1.0 if reference.size else 0.0
    if exponent > limit:
        return np.zeros(values.shape, dtype=bool)
    floor = reference.copy()
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        for _ in range(3):
            part = min(exponent, 300.0)
            floor *= 10.0**part
            exponent -= part
    return values >= floor


def experimental_cell_flags(masker, target, cells, policy, noise, quiet) -> dict[str, np.ndarray]:
    """Predicates on the specified cells only; never chooses a candidate's replacement denominator."""
    rows = np.array([cell[0] for cell in cells], dtype=int)
    bands = np.array([cell[1] for cell in cells], dtype=int)
    ea, eb, t = masker.en[rows, bands], target.en[rows, bands], masker.thm[rows, bands]
    return {
        "masker_silent": masker.nonzero_samples[rows] == 0,
        "target_silent": target.nonzero_samples[rows] == 0,
        "masker_low_energy": ~_at_least_db_above(ea, noise[bands], policy.energy_above_noise_db),
        "target_low_energy": ~_at_least_db_above(eb, noise[bands], policy.energy_above_noise_db),
        "quiet_elevation_insufficient": ~_at_least_db_above(t, quiet[bands], policy.quiet_elevation_db),
        "quiet_floor": t == quiet[bands],
        "nonfinite": ~(np.isfinite(ea) & np.isfinite(eb) & np.isfinite(t)),
    }


def _screen_cells(masker, target, geometry, policy, noise, quiet) -> tuple[tuple[int, int], ...]:
    cells = tuple(
        (k, b)
        for k in range(geometry.first_call, geometry.first_call + geometry.call_count)
        for b in geometry.region.bands
    )
    flags = experimental_cell_flags(masker, target, cells, policy, noise, quiet)
    keep = np.ones(len(cells), dtype=bool)
    for values in flags.values():
        keep &= ~values
    return tuple(cell for cell, selected in zip(cells, keep, strict=True) if selected)


# --- preparation --------------------------------------------------------------------------------------------------


class MaskingPreparer:
    """Prepares geometric support over configured roots and persists it in ``store`` (an existing directory).

    ``known_alignments`` lists the alignment-provenance methods this configuration documents; a job citing any
    other method is unavailable. Read limits come from the request (resource bounds, not scientific thresholds).
    """

    def __init__(self, roots: Mapping[str, str], *, store: str | os.PathLike, clock: RunClock,
                 known_alignments: frozenset[VersionRef]) -> None:  # fmt: skip
        if not isinstance(roots, Mapping):
            raise ValueError(f"roots must be a Mapping, got {type(roots).__name__}")
        snapshot = dict(roots.items())
        for key, path in snapshot.items():
            require_text(key, "root key")
            require_text(path, f"root path for {key!r}")
        self._roots = snapshot
        self._store = Path(store)
        if not callable(getattr(clock, "read", None)):
            raise ValueError("clock must provide read()")
        self._clock = clock
        if not isinstance(known_alignments, frozenset) or not all(isinstance(m, VersionRef) for m in known_alignments):
            raise ValueError("known_alignments must be a frozenset of VersionRef")
        self._alignments = known_alignments

    def _read_clock(self) -> ClockReading:
        reading = self._clock.read()
        if not isinstance(reading, ClockReading):
            raise ValueError(f"clock returned {type(reading).__name__}, not a ClockReading")
        return reading

    def prepare(self, request: MaskingPreparationRequest) -> PortResult[PersistedPreparation]:
        require_instance(request, MaskingPreparationRequest, "request")
        started = self._read_clock()
        try:
            supports, experimental = self._prepare(request, started)
        except _Rejected as rejected:
            return rejected.failure
        try:
            finished = self._check_time(request, started)
        except _Rejected as rejected:
            return rejected.failure
        plan = MaskingPreparedPlan(
            profile=request.profile,
            supports=supports,
            experimental_policy=request.experimental_policy,
            experimental_supports=experimental,
            kind=SupportKind.GEOMETRIC,
            qualification=request.qualification,
            max_file_bytes=request.max_file_bytes,
            max_decoded_bytes=request.max_decoded_bytes,
            timing=PreparationTiming(
                started.utc, finished.utc, finished.monotonic_seconds - started.monotonic_seconds
            ),  # fmt: skip
        )
        payload = encode_prepared_plan(plan)
        if len(payload) > MAX_MANIFEST_BYTES:
            return PortFailure(
                PortFailureCode.UNAVAILABLE,
                f"prepared plan manifest of {len(payload)} bytes exceeds the {MAX_MANIFEST_BYTES}-byte bound that "
                "load_prepared_plan enforces; nothing was written",
                False,
            )
        try:
            if not self._store.is_dir():
                raise OSError(f"store {self._store} is not an existing directory")
            digest = write_content_addressed(self._store, payload)
        except OSError as error:
            return PortFailure(PortFailureCode.UNAVAILABLE, f"prepared plan could not be persisted: {error}", False)
        return Success(PersistedPreparation(digest, plan))

    def _prepare(
        self, request: MaskingPreparationRequest, started: ClockReading
    ) -> tuple[tuple[PreparedSupport, ...], tuple[ExperimentalSupport, ...]]:
        members = request.state.audio.artifacts
        root_keys = {b.artifact_id: b.root_key for b in request.root_bindings}
        planned = []
        for job in request.jobs:
            label = f"{job.pair.masker}->{job.pair.target} {job.time_range}"
            if job.alignment.method not in self._alignments:
                raise _Rejected(PortFailureCode.UNAVAILABLE,
                                f"{label}: unknown alignment provenance {job.alignment.method}")  # fmt: skip
            pair = []
            for role, source in (("masker", job.masker_source), ("target", job.target_source)):
                artifact = single_member(members, source)
                if artifact is None:
                    raise _Rejected(PortFailureCode.INVALID_INPUT,
                                    f"{label}: {role} source does not match exactly one current member")  # fmt: skip
                if root_keys[artifact.artifact_id] not in self._roots:
                    raise _Rejected(PortFailureCode.INVALID_INPUT,
                                    f"{label}: root of {artifact.artifact_id} is not configured")  # fmt: skip
                pair.append(artifact)
            masker, target = pair
            try:
                first, stop = interval_samples(job.time_range, timeline_start_seconds=masker.timeline_start_seconds,
                                               sample_rate_hz=masker.sample_rate_hz,
                                               frame_count=masker.frame_count)  # fmt: skip
            except ValueError as error:
                raise _Rejected(PortFailureCode.INVALID_INPUT, f"{label}: {error}") from error
            planned.append((job, masker, target, first, stop))
        verified: dict[ArtifactId, str] = {}
        analyses = {}
        for _, masker, target, _, _ in planned:
            for artifact in (masker, target):
                if artifact.artifact_id not in verified:
                    self._check_time(request, started)
                    clip = self._verify(artifact, root_keys[artifact.artifact_id], request)
                    verified[artifact.artifact_id] = clip.verified_sha256
                    if request.experimental_policy is not None:
                        self._check_time(request, started)
                        analyses[artifact.artifact_id] = dist10_clip_to_bands(
                            clip.samples, sample_rate_hz=clip.artifact.sample_rate_hz
                        )
                        self._check_time(request, started)
        supports = []
        for job, masker, target, first, stop in planned:
            call0, calls = complete_window_calls(frame_count=masker.frame_count, start_sample=first, stop_sample=stop)
            supports.append(PreparedSupport(
                pair=job.pair, region=job.region, time_range=job.time_range, alignment=job.alignment,
                masker_artifact_id=masker.artifact_id, target_artifact_id=target.artifact_id,
                masker_sha256=verified[masker.artifact_id], target_sha256=verified[target.artifact_id],
                sample_rate_hz=masker.sample_rate_hz, frame_count=masker.frame_count,
                timeline_start_seconds=masker.timeline_start_seconds, start_sample=first, stop_sample=stop,
                first_call=call0, call_count=calls,
                digest=support_digest(profile=P1_PROFILE, pair=job.pair, region=job.region,
                                      sample_rate_hz=masker.sample_rate_hz, frame_count=masker.frame_count,
                                      timeline_start_seconds=masker.timeline_start_seconds, start_sample=first,
                                      stop_sample=stop, first_call=call0, call_count=calls),
            ))  # fmt: skip
        experimental = ()
        if request.experimental_policy is not None:
            noise, quiet = rounding_noise_band_energy(), quiet_floor_bands()
            reference = noise_reference_digest(noise)
            policy = request.experimental_policy
            experimental = tuple(
                ExperimentalSupport(
                    g,
                    policy.digest,
                    reference,
                    _screen_cells(
                        analyses[g.masker_artifact_id], analyses[g.target_artifact_id], g, policy, noise, quiet
                    ),
                )
                for g in supports
            )
        return tuple(supports), experimental

    def _check_time(self, request: MaskingPreparationRequest, started: ClockReading) -> ClockReading:
        reading = self._read_clock()
        if reading.cancelled:
            raise _Rejected(PortFailureCode.UNAVAILABLE, "preparation cancelled before completion")
        if reading.monotonic_seconds - started.monotonic_seconds > request.max_elapsed_seconds:
            raise _Rejected(PortFailureCode.UNAVAILABLE,
                            f"preparation exceeded its time limit of {request.max_elapsed_seconds} s")  # fmt: skip
        return reading

    def _verify(self, artifact: AudioArtifact, root_key: str, request: MaskingPreparationRequest) -> Dist10Pcm16Clip:
        try:
            clip = read_dist10_member(artifact, root=self._roots[root_key], max_file_bytes=request.max_file_bytes,
                                      max_decoded_bytes=request.max_decoded_bytes,
                                      allow_exact_dual_mono=request.profile in (P1_EXACT_DUAL_MONO_PROFILE, P1_FLOAT64_PROFILE),
                                      allow_float64_quantization=request.profile == P1_FLOAT64_PROFILE)  # fmt: skip
        except Dist10ReadError as error:
            raise _Rejected(PortFailureCode.UNAVAILABLE,
                            f"artifact {artifact.artifact_id} under root {root_key!r} could not be read and "
                            f"verified: {error}") from error  # fmt: skip
        return clip
