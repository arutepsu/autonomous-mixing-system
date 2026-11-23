"""WAV input boundary of the dist10 front end (front-end contract §2.1 profiles v1/v2; Task 3P; ADR-029, Proposed).

Profile v1 accepts exactly one kind of file: verified **mono, 44,100 Hz WAV with PCM_16 samples** (WAV, WAVEX or
RF64 container, as the shared verifier detects them). The file is read and verified only through the shared
``adapters.audio_io`` path (``check_resolved_wav_integrity``: bounded single capture, SHA-256 against the declared
hash, declared rate/channel/frame metadata, finite samples), and its failures (``IntegrityError``,
``LocationResolutionError``) reach the caller unchanged.

Explicit opt-in ``allow_exact_dual_mono=True`` additionally accepts two-channel PCM_16 only when both decoded
channels contain exactly the same counts over the entire verified clip. Shape, range and integrality are checked
for both channels before selection. Channel 0 is selected without averaging; original artifact metadata and hash
remain stereo. The P1 prepared profile v2 persists this choice; the default v1 remains strictly mono. Shared read
limits charge the actual source channels. A one-count difference anywhere is rejected.

Conversion: libsndfile decodes PCM_16 to float64 by dividing each signed 16-bit count by 32768 (0x8000), so the
original count is ``sample x 32768``. That product is exact (a power-of-two scale of a value with at most 16
significant bits). Every scaled value must be integral and lie in ``[-32768, 32767]`` before the cast to ``int16``;
otherwise the samples are not PCM_16 counts and are rejected. The range is checked on the decoded values first,
so no out-of-range value is ever scaled. The result is a one-dimensional, read-only ``int16``
array for ``dist10_clip_to_bands``, together with the verified artifact record, whose ``timeline_start_seconds``,
``sample_rate_hz`` and ``frame_count`` later pair alignment needs.

Rejected explicitly, never converted: other sample rates and unsupported channel counts (checked against the declared record
before any file access, and again on the verified audio) and other subtypes (PCM_24, PCM_32, PCM_U8, FLOAT, DOUBLE,
...; checked on the verified audio). Resampling, stereo fold-down, and the clipping or quantization of float or
higher-bit-depth exports belong to a separately specified profile, not to this boundary.

Producer export settings for this profile: WAV, 44,100 Hz, 1 channel (mono), 16-bit signed integer PCM (PCM_16),
no sample-rate conversion on export. Dither and any gain change happen, if at all, in the producer before export
and are part of the exported audio; this boundary reads the exported counts as they are.
"""

from dataclasses import dataclass
from enum import Enum

import numpy as np

from autonomous_mixing.adapters.audio_io.integrity import CheckedAudio, IntegrityError, check_resolved_wav_integrity
from autonomous_mixing.adapters.audio_io.limits import AudioReadLimits
from autonomous_mixing.adapters.audio_io.locations import (
    LocationResolutionError,
    ResolvedArtifactLocation,
    resolve_artifact_location,
)
from autonomous_mixing.core.records.artifacts import AudioArtifact

PROFILE_SAMPLE_RATE_HZ = 44100
PROFILE_CHANNEL_COUNT = 1
PROFILE_SUBTYPE = "PCM_16"  # libsndfile's name for signed 16-bit integer PCM
PCM16_SCALE = 32768.0  # libsndfile's PCM_16 -> double normalisation divisor (0x8000)
INT16_MIN, INT16_MAX = -32768, 32767
DECODED_MIN, DECODED_MAX = INT16_MIN / PCM16_SCALE, INT16_MAX / PCM16_SCALE  # -1.0 and 32767/32768, exact


class Dist10InputFailureCode(Enum):
    UNSUPPORTED_SAMPLE_RATE = "unsupported_sample_rate"
    UNSUPPORTED_CHANNEL_COUNT = "unsupported_channel_count"
    UNSUPPORTED_SUBTYPE = "unsupported_subtype"
    NON_INTEGRAL_SAMPLES = "non_integral_samples"
    OUT_OF_RANGE_SAMPLES = "out_of_range_samples"
    DECODED_SHAPE_MISMATCH = "decoded_shape_mismatch"
    NON_IDENTICAL_CHANNELS = "non_identical_channels"


class Dist10InputError(ValueError):
    """Verified audio outside the selected input profile, or decoded samples that are not PCM_16 counts; ``code`` names which."""

    def __init__(self, code: Dist10InputFailureCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code


class Dist10ReadError(ValueError):
    """A current member could not be resolved under its root, verified, or accepted by the selected input profile.

    Raised only by ``read_dist10_member``; ``code`` is the underlying failure code's value (location resolution,
    integrity or ``Dist10InputFailureCode``) and the original error is chained as ``__cause__``. It lets the P1
    preparation and analysis modules read through this boundary without importing ``adapters.audio_io``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, eq=False)
class Dist10Pcm16Clip:
    """One verified artifact under the selected input profile (adapter-local, not a core record).

    ``artifact`` is the verified record itself (timeline start, rate, frame count, hash); ``samples`` is a
    read-only one-dimensional ``int16`` array of the original counts, one per frame (channel 0 for verified exact dual mono). ``container`` and ``subtype``
    are libsndfile's names for the detected format and ``verified_sha256`` the hash of the captured payload.
    Equality is identity.
    """

    artifact: AudioArtifact
    samples: np.ndarray
    container: str
    subtype: str
    verified_sha256: str

    @property
    def analysis_channel_count(self) -> int:
        """The numerical front end always consumes one unchanged count per frame."""
        return 1

    @property
    def channel_mapping(self) -> str:
        """Fixed representation rule applied by the verified reader; original metadata stays intact."""
        if self.artifact.channel_count == 1:
            return "mono_identity@1"
        if self.artifact.channel_count == 2:
            return "exact_dual_mono_first_channel@1"
        raise ValueError("clip has an unsupported source channel count")


def _require_dual_mono_option(value: bool) -> None:
    if type(value) is not bool:
        raise ValueError("allow_exact_dual_mono must be bool")


def _require_profile(artifact: AudioArtifact, where: str, allow_exact_dual_mono: bool = False) -> None:
    _require_dual_mono_option(allow_exact_dual_mono)
    if artifact.sample_rate_hz != PROFILE_SAMPLE_RATE_HZ:
        raise Dist10InputError(
            Dist10InputFailureCode.UNSUPPORTED_SAMPLE_RATE,
            f"{where}: sample rate {artifact.sample_rate_hz} Hz, profile v1 needs {PROFILE_SAMPLE_RATE_HZ} Hz "
            "(no resampling)",
        )
    if artifact.channel_count not in ((1, 2) if allow_exact_dual_mono else (1,)):
        raise Dist10InputError(
            Dist10InputFailureCode.UNSUPPORTED_CHANNEL_COUNT,
            f"{where}: {artifact.channel_count} channels, "
            + ("selected profile permits mono or exact dual mono" if allow_exact_dual_mono else "profile v1 needs mono")
            + " (no fold-down)",
        )


def pcm16_counts(audio: CheckedAudio, *, allow_exact_dual_mono: bool = False) -> np.ndarray:
    """The original signed 16-bit counts of verified audio, as a fresh read-only ``int16`` array.

    Raises ``ValueError`` for an argument that is not ``CheckedAudio`` and ``Dist10InputError`` for another
    sample rate, channel count or subtype, for decoded values outside ``[-32768, 32767] / 32768`` (checked first,
    before any scaling, NaN included) and for scaled values that are not integral, naming the first such frame. The
    Optional exact dual mono requires both channels to pass these checks and match everywhere before selection.
    The result does not depend on the caller's NumPy error policy. The verified samples are never modified.
    """
    if not isinstance(audio, CheckedAudio):
        raise ValueError(f"audio must be CheckedAudio, got {type(audio).__name__}")
    where = audio.artifact.location
    _require_profile(audio.artifact, where, allow_exact_dual_mono)
    if audio.subtype != PROFILE_SUBTYPE:
        raise Dist10InputError(
            Dist10InputFailureCode.UNSUPPORTED_SUBTYPE,
            f"{where}: subtype {audio.subtype}, profile v1 needs {PROFILE_SUBTYPE} (no quantization)",
        )
    samples = np.asarray(audio.samples)
    expected = (audio.artifact.frame_count, audio.artifact.channel_count)
    if samples.shape != expected:
        raise Dist10InputError(
            Dist10InputFailureCode.DECODED_SHAPE_MISMATCH,
            f"{where}: decoded shape {samples.shape} differs from verified source metadata {expected}",
        )
    decoded = samples
    # Range first, on the decoded values: [-32768/32768, 32767/32768] are exact doubles, so this is the count range
    # exactly, and no value outside it is ever scaled (a huge value would overflow x 32768). Written as "not within",
    # so NaN is rejected too.
    out_of_range = np.flatnonzero(~((decoded >= DECODED_MIN) & (decoded <= DECODED_MAX)))
    if out_of_range.size:
        frame, channel = divmod(int(out_of_range[0]), audio.artifact.channel_count)
        raise Dist10InputError(
            Dist10InputFailureCode.OUT_OF_RANGE_SAMPLES,
            f"{where}: frame {frame}, channel {channel} decodes to {decoded[frame, channel]!r}, outside the PCM_16 range "
            f"[{INT16_MIN}, {INT16_MAX}] / 32768",
        )
    scaled = decoded * PCM16_SCALE  # exact: a power-of-two scale of a value in range; cannot overflow
    non_integral = np.flatnonzero(scaled != np.floor(scaled))
    if non_integral.size:
        frame, channel = divmod(int(non_integral[0]), audio.artifact.channel_count)
        raise Dist10InputError(
            Dist10InputFailureCode.NON_INTEGRAL_SAMPLES,
            f"{where}: frame {frame}, channel {channel} decodes to {scaled[frame, channel]!r} x 1/32768, not an integer PCM_16 count",
        )
    if audio.artifact.channel_count == 2:
        differences = np.flatnonzero(scaled[:, 0] != scaled[:, 1])
        if differences.size:
            raise Dist10InputError(
                Dist10InputFailureCode.NON_IDENTICAL_CHANNELS,
                f"{where}: channels differ at frame {int(differences[0])}; exact equality is required",
            )
    counts = scaled[:, 0].astype(np.int16)
    counts.flags.writeable = False
    return counts


def read_dist10_pcm16(
    location: ResolvedArtifactLocation, *, limits: AudioReadLimits, allow_exact_dual_mono: bool = False
) -> Dist10Pcm16Clip:
    """Verify one resolved artifact through the shared path and return its PCM_16 counts for the dist10 front end.

    Order: argument types (``ValueError``); the declared sample rate and channel count against the selected input profile
    (``Dist10InputError``, before any file access); ``check_resolved_wav_integrity`` with ``limits`` (its
    ``IntegrityError`` and ``LocationResolutionError`` propagate unchanged); then ``pcm16_counts`` on exactly the
    verified samples (shape, subtype, integrality, range and, when opted in, full-clip channel equality).
    """
    if not isinstance(location, ResolvedArtifactLocation):
        raise ValueError(f"location must be a ResolvedArtifactLocation, got {type(location).__name__}")
    if not isinstance(limits, AudioReadLimits):
        raise ValueError(f"limits must be AudioReadLimits, got {type(limits).__name__}")
    _require_profile(location.artifact, location.artifact.location, allow_exact_dual_mono)
    audio = check_resolved_wav_integrity(
        location, max_file_bytes=limits.max_file_bytes, max_decoded_bytes=limits.max_decoded_bytes
    )
    return Dist10Pcm16Clip(
        artifact=audio.artifact,
        samples=pcm16_counts(audio, allow_exact_dual_mono=allow_exact_dual_mono),
        container=audio.container,
        subtype=audio.subtype,
        verified_sha256=audio.verified_sha256,
    )


def read_dist10_member(artifact: AudioArtifact, *, root: str, max_file_bytes: int,
                       max_decoded_bytes: int, allow_exact_dual_mono: bool = False) -> Dist10Pcm16Clip:  # fmt: skip
    """Resolve one current member under its configured physical ``root`` and read it with ``read_dist10_pcm16``.

    Resolution is ``resolve_artifact_location`` (the location must stay inside ``root``); the read is exactly
    ``read_dist10_pcm16`` with ``AudioReadLimits(max_file_bytes, max_decoded_bytes)``. Expected resolution,
    integrity and profile failures are raised as ``Dist10ReadError``; argument and limit errors stay ``ValueError``.
    Added for the P1 R1 handoff (2026-11-20) so that new P1 modules gain no ``audio_io`` edge; behaviour of
    ``allow_exact_dual_mono`` is forwarded unchanged; its default remains strict mono.
    """
    _require_dual_mono_option(allow_exact_dual_mono)
    limits = AudioReadLimits(max_file_bytes, max_decoded_bytes)
    try:
        location = resolve_artifact_location(artifact, root=root)
        return read_dist10_pcm16(location, limits=limits, allow_exact_dual_mono=allow_exact_dual_mono)
    except (LocationResolutionError, IntegrityError, Dist10InputError) as error:
        raise Dist10ReadError(error.code.value, str(error)) from error
