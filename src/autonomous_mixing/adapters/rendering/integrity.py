"""Read-only verification of one WAV artifact against its declared ``AudioArtifact`` metadata.

The file is opened once, its bytes are captured with a bounded read, the SHA-256 of that captured
payload is compared with ``AudioArtifact.content_hash``, and the same in-memory payload is inspected
and decoded. This establishes the identity of the captured payload only: it does not prove that the
pathname still holds those bytes afterwards, and it is not file-write protection (F4 stays open). A
later processor must consume the returned samples or reverify what it reads. Contributes to QR-01,
QR-02, QR-03, FR-02 and FR-17 without completing them; nothing is written, renamed or removed.

Two entry points share one private capture/hash/decode pipeline. ``check_wav_integrity`` accepts only
explicit absolute local paths: its lexical checks reject relative, URI-style and UNC/device-namespace
locations; they do not confine access to any directory and do not rule out aliases, symbolic links,
junctions or other reparse points. ``check_resolved_wav_integrity`` takes a ``ResolvedArtifactLocation``,
resolves its original artifact afresh against the record's configured root, requires the fresh root
and path to equal the record's exactly, and then verifies at the freshly resolved path. That recheck
precedes the single content open but is not race-free. Regular-file checks are made on the pathname
and on the opened handle, but no safety against concurrent modification is claimed.

``max_file_bytes`` bounds the captured payload and ``max_decoded_bytes`` the returned float64 sample
storage; neither bounds total interpreter or libsndfile memory use. Both are caller-supplied: no
default or scientific threshold is implied.
"""

import hashlib
import io
import os
import re
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from autonomous_mixing.adapters.rendering.locations import (
    LocationResolutionError,
    LocationResolutionFailureCode,
    ResolvedArtifactLocation,
    resolve_artifact_location,
)
from autonomous_mixing.core.records.artifacts import AudioArtifact

# libsndfile container names accepted as WAV (RIFF, WAVE_FORMAT_EXTENSIBLE, RF64); detected from content.
WAV_CONTAINERS = ("WAV", "WAVEX", "RF64")
_CHANNEL_COUNTS = (1, 2)
_FLOAT64_BYTES = 8
# A URI scheme of two or more characters followed by ":" (one letter plus ":" is a Windows drive).
_URI_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]+:")
_SEPARATORS = ("/", "\\")
# I/O buffer size for the bounded capture loop, not a scientific threshold: small enough to be a valid
# read size on every platform and to bound each temporary buffer, large enough that typical stems
# need few read calls. Any positive max_file_bytes is honoured by reading in chunks of at most this size.
_READ_CHUNK_BYTES = 1 << 20


class IntegrityFailureCode(Enum):
    UNSUPPORTED_PATH = "unsupported_path"
    NOT_FOUND = "not_found"
    NOT_READABLE = "not_readable"
    NOT_REGULAR_FILE = "not_regular_file"
    RESOURCE_LIMIT = "resource_limit"
    HASH_MISMATCH = "hash_mismatch"
    INVALID_WAV = "invalid_wav"
    METADATA_MISMATCH = "metadata_mismatch"
    NONFINITE_SAMPLES = "nonfinite_samples"


class IntegrityError(ValueError):
    """An expected file or content failure; ``code`` names which verification step rejected it."""

    def __init__(self, code: IntegrityFailureCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code


@dataclass(frozen=True, eq=False)
class CheckedAudio:
    """Decoded samples of a payload whose hash and format metadata matched ``artifact``.

    ``samples`` is a native float64 ``(frames, channels)`` array returned with ``writeable=False``.
    That flag guards against accidental mutation only; it is neither a security boundary nor a
    permanent integrity guarantee. ``container`` and ``subtype`` are libsndfile's names for the
    detected format. Equality is identity (arrays have no single truth value).
    """

    artifact: AudioArtifact
    samples: np.ndarray
    container: str
    subtype: str
    verified_sha256: str


def _require_limit(value: Any, field: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field} must be a positive built-in int, got {value!r}")
    return value


def _fail(code: IntegrityFailureCode, message: str) -> IntegrityError:
    return IntegrityError(code, message)


def _local_absolute_path(location: str) -> str:
    unsupported = IntegrityFailureCode.UNSUPPORTED_PATH
    if "\x00" in location:
        raise _fail(unsupported, "location contains a NUL character")
    if location[:1] in _SEPARATORS and location[1:2] in _SEPARATORS:
        raise _fail(unsupported, f"UNC, network or device-namespace paths are not supported: {location!r}")
    if _URI_SCHEME.match(location):
        raise _fail(unsupported, f"URI-style locations are not supported: {location!r}")
    if not Path(location).is_absolute():
        # On Windows this also rejects root-relative ("\\x") and drive-relative ("C:x") forms.
        raise _fail(unsupported, f"location must be an absolute local path: {location!r}")
    return location


def _require_regular(mode: int, location: str, what: str) -> None:
    if not stat.S_ISREG(mode):
        raise _fail(IntegrityFailureCode.NOT_REGULAR_FILE, f"{what} is not a regular file: {location}")


def _capture(location: str, limit: int) -> bytes:
    """Return the file's bytes, reading at most ``limit + 1`` so an oversized payload is detected.

    Reads are chunked (each at most ``_READ_CHUNK_BYTES`` and at most the remaining allowance), so
    arbitrarily large integer limits never reach the stream as an oversized read size.
    """
    try:
        _require_regular(os.stat(location).st_mode, location, "path")
        with open(location, "rb") as stream:
            _require_regular(os.fstat(stream.fileno()).st_mode, location, "opened handle")
            chunks, remaining = [], limit + 1
            while remaining > 0:
                chunk = stream.read(min(_READ_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise _fail(IntegrityFailureCode.NOT_FOUND, f"no file at {location}") from exc
    except IsADirectoryError as exc:
        raise _fail(IntegrityFailureCode.NOT_REGULAR_FILE, f"path is a directory: {location}") from exc
    except PermissionError as exc:
        raise _fail(IntegrityFailureCode.NOT_READABLE, f"cannot read {location}: {exc.strerror}") from exc
    payload = b"".join(chunks)
    if len(payload) > limit:
        raise _fail(IntegrityFailureCode.RESOURCE_LIMIT, f"file exceeds max_file_bytes={limit}: {location}")
    return payload


def _decode(artifact: AudioArtifact, payload: bytes, max_decoded_bytes: int) -> tuple[np.ndarray, str, str]:
    location = artifact.location
    # libsndfile reports unreadable or unrecognized content as LibsndfileError, a RuntimeError subclass
    # specific to soundfile; IntegrityError raised inside the block is not caught here.
    try:
        with sf.SoundFile(io.BytesIO(payload)) as handle:
            container, subtype = handle.format, handle.subtype
            frames, channels, rate = handle.frames, handle.channels, handle.samplerate
            if container not in WAV_CONTAINERS:
                raise _fail(IntegrityFailureCode.INVALID_WAV, f"not a WAV container (detected {container})")
            mismatches = [
                f"{name} declared {declared}, file has {actual}"
                for name, declared, actual in (
                    ("sample rate", artifact.sample_rate_hz, rate),
                    ("channel count", artifact.channel_count, channels),
                    ("frame count", artifact.frame_count, frames),
                )
                if declared != actual
            ]
            if mismatches:
                raise _fail(IntegrityFailureCode.METADATA_MISMATCH, f"{location}: " + "; ".join(mismatches))
            if frames < 1 or channels not in _CHANNEL_COUNTS:
                raise _fail(
                    IntegrityFailureCode.INVALID_WAV,
                    f"{location}: need at least one frame and 1 or 2 channels, got {frames} x {channels}",
                )
            decoded_bytes = frames * channels * _FLOAT64_BYTES
            if decoded_bytes > max_decoded_bytes:
                raise _fail(
                    IntegrityFailureCode.RESOURCE_LIMIT,
                    f"{location}: {decoded_bytes} decoded bytes exceed max_decoded_bytes={max_decoded_bytes}",
                )
            samples = handle.read(frames, dtype="float64", always_2d=True)
    except sf.LibsndfileError as exc:
        raise _fail(IntegrityFailureCode.INVALID_WAV, f"{location}: {exc.error_string}") from exc
    if samples.shape != (frames, channels):
        raise _fail(
            IntegrityFailureCode.INVALID_WAV,
            f"{location}: decoded {samples.shape}, expected {(frames, channels)} (truncated data?)",
        )
    return samples, container, subtype


def check_wav_integrity(artifact: AudioArtifact, *, max_file_bytes: int, max_decoded_bytes: int) -> CheckedAudio:
    """Verify the file at ``artifact.location`` against ``artifact`` and return its decoded samples.

    Steps: bounded single read of a regular file; SHA-256 of the captured payload against
    ``content_hash``; WAV/WAVEX/RF64 container detected from content; declared sample rate, channel
    count and frame count; at least one frame and mono/stereo; decoded-size limit; float64 decode of
    exactly the inspected frames; finite samples. Samples are not normalized, clipped, resampled,
    padded or folded down. Invalid arguments raise ValueError before any file access; expected file
    and content failures raise ``IntegrityError``.
    """
    if not isinstance(artifact, AudioArtifact):
        raise ValueError(f"artifact must be an AudioArtifact, got {type(artifact).__name__}")
    _require_limit(max_file_bytes, "max_file_bytes")
    _require_limit(max_decoded_bytes, "max_decoded_bytes")
    location = _local_absolute_path(artifact.location)
    return _verify_at(artifact, location, max_file_bytes, max_decoded_bytes)


def check_resolved_wav_integrity(
    location: ResolvedArtifactLocation, *, max_file_bytes: int, max_decoded_bytes: int
) -> CheckedAudio:
    """Verify ``location.artifact`` at a fresh resolution of its location and return its decoded samples.

    Invalid arguments raise ValueError before any filesystem access. The original artifact is resolved
    again against ``location.configured_root``; resolution failures propagate as
    ``LocationResolutionError``. If the fresh resolved root or absolute path differs from the record's,
    ``STALE_RESOLUTION`` is raised before any content is opened. Otherwise the file at the fresh path is
    verified exactly as by ``check_wav_integrity`` against the original artifact's declared hash and
    metadata. The returned ``CheckedAudio.artifact`` is ``location.artifact`` itself; the record is not
    retained in the result.
    """
    if not isinstance(location, ResolvedArtifactLocation):
        raise ValueError(f"location must be a ResolvedArtifactLocation, got {type(location).__name__}")
    _require_limit(max_file_bytes, "max_file_bytes")
    _require_limit(max_decoded_bytes, "max_decoded_bytes")
    fresh = resolve_artifact_location(location.artifact, root=location.configured_root)
    if fresh.resolved_root != location.resolved_root or fresh.absolute_path != location.absolute_path:
        raise LocationResolutionError(
            LocationResolutionFailureCode.STALE_RESOLUTION,
            f"record ({location.resolved_root!r}, {location.absolute_path!r}) differs from fresh resolution "
            f"({fresh.resolved_root!r}, {fresh.absolute_path!r})",
        )
    return _verify_at(location.artifact, fresh.absolute_path, max_file_bytes, max_decoded_bytes)


def _verify_at(artifact: AudioArtifact, location: str, max_file_bytes: int, max_decoded_bytes: int) -> CheckedAudio:
    """Shared pipeline: bounded single capture at ``location``, hash check, then decode of the captured bytes."""
    payload = _capture(location, max_file_bytes)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != artifact.content_hash:
        raise _fail(
            IntegrityFailureCode.HASH_MISMATCH,
            f"{location}: SHA-256 {digest} does not match declared {artifact.content_hash}",
        )
    samples, container, subtype = _decode(artifact, payload, max_decoded_bytes)
    if not np.isfinite(samples).all():
        raise _fail(IntegrityFailureCode.NONFINITE_SAMPLES, f"{location}: contains NaN or infinite samples")
    samples.setflags(write=False)
    return CheckedAudio(artifact, samples, container, subtype, digest)
