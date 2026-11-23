"""Tests for the dist10 WAV input boundary (front-end contract §2.1 profile v1; Task 3P; ADR-029, Proposed).

Fixtures are real WAV files written with soundfile to ``tmp_path`` from small int16 arrays (unit-test signals, not
project media or evaluation audio): PCM_16 for the accepted profile, and PCM_24, PCM_32, PCM_U8, FLOAT and DOUBLE,
other rates and stereo for the rejected ones. Verification itself is the shared ``adapters.audio_io`` path; these
tests check that its failures reach the caller unchanged.
"""

import dataclasses
import hashlib
import warnings
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import autonomous_mixing.adapters.dsp_analysis.wav_input as wav_input
from autonomous_mixing.adapters.audio_io.integrity import CheckedAudio, IntegrityError, IntegrityFailureCode
from autonomous_mixing.adapters.audio_io.limits import AudioReadLimits
from autonomous_mixing.adapters.audio_io.locations import (
    LocationResolutionError,
    LocationResolutionFailureCode,
    resolve_artifact_location,
)
from autonomous_mixing.adapters.dsp_analysis.front_end import dist10_clip_to_bands
from autonomous_mixing.adapters.dsp_analysis.wav_input import (
    Dist10InputError,
    Dist10InputFailureCode,
    Dist10Pcm16Clip,
    Dist10ReadError,
    pcm16_counts,
    read_dist10_member,
    read_dist10_pcm16,
)
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.identifiers import ArtifactId, ProjectId, Revision, TrackId, VersionRef

Code = Dist10InputFailureCode
LIMITS = AudioReadLimits(max_file_bytes=1 << 22, max_decoded_bytes=1 << 24)
EXTREMES = np.array([-32768, -1, 0, 1, 32767], dtype=np.int16)


def counts(n: int) -> np.ndarray:
    k = np.arange(n)
    return ((k * 7919) % 65536 - 32768).astype(np.int16)  # deterministic, covers the whole int16 range


def write(root: Path, name: str, samples, *, rate=44100, subtype="PCM_16", fmt="WAV") -> bytes:
    path = root / name
    sf.write(str(path), samples, rate, format=fmt, subtype=subtype)
    return path.read_bytes()


def artifact(name: str, data: bytes, *, rate=44100, channels=1, frames=None, start=1.25, digest=None) -> AudioArtifact:
    return AudioArtifact(
        ArtifactId("a1"),
        ProjectId("p1"),
        Revision("r1"),
        (TrackId("t1"),),
        ArtifactKind.STEM,
        name,
        start,
        rate,
        channels,
        frames,
        digest or hashlib.sha256(data).hexdigest(),
        VersionRef("render", "1"),
    )


def located(tmp_path: Path, samples, *, name="clip.wav", rate=44100, subtype="PCM_16", **declared):
    data = write(tmp_path, name, samples, rate=rate, subtype=subtype)
    frames = declared.pop("frames", np.asarray(samples).shape[0])
    channels = declared.pop("channels", 1 if np.asarray(samples).ndim == 1 else np.asarray(samples).shape[1])
    record = artifact(
        name, data, rate=declared.pop("declared_rate", rate), channels=channels, frames=frames, **declared
    )
    return resolve_artifact_location(record, root=str(tmp_path))


# --- exact recovery ----------------------------------------------------------------------------------------


def test_extreme_counts_are_recovered_exactly(tmp_path):
    clip = read_dist10_pcm16(located(tmp_path, EXTREMES), limits=LIMITS)
    assert isinstance(clip, Dist10Pcm16Clip)
    assert clip.samples.dtype == np.int16 and clip.samples.ndim == 1
    assert clip.samples.tolist() == [-32768, -1, 0, 1, 32767]
    assert clip.subtype == "PCM_16" and clip.container == "WAV"


def test_every_count_round_trips(tmp_path):
    original = np.arange(-32768, 32768, dtype=np.int16)  # all 65,536 values
    clip = read_dist10_pcm16(located(tmp_path, original), limits=LIMITS)
    np.testing.assert_array_equal(clip.samples, original)


def test_converted_samples_give_the_same_bands_as_the_original_array(tmp_path):
    original = counts(4000)
    clip = read_dist10_pcm16(located(tmp_path, original), limits=LIMITS)
    from_wav = dist10_clip_to_bands(clip.samples, sample_rate_hz=clip.artifact.sample_rate_hz)
    direct = dist10_clip_to_bands(original, sample_rate_hz=44100)
    np.testing.assert_array_equal(from_wav.en, direct.en)
    np.testing.assert_array_equal(from_wav.thm, direct.thm)
    np.testing.assert_array_equal(from_wav.eligible, direct.eligible)
    np.testing.assert_array_equal(from_wav.nonzero_samples, direct.nonzero_samples)


def test_result_keeps_the_artifact_timeline_and_verification(tmp_path):
    location = located(tmp_path, counts(1200), start=2.5)
    clip = read_dist10_pcm16(location, limits=LIMITS)
    assert clip.artifact is location.artifact  # timeline_start_seconds, rate and frame count for pair alignment
    assert clip.artifact.timeline_start_seconds == 2.5 and clip.artifact.frame_count == 1200
    assert clip.verified_sha256 == location.artifact.content_hash
    assert not clip.samples.flags.writeable
    assert [f.name for f in dataclasses.fields(Dist10Pcm16Clip)] == [
        "artifact", "samples", "container", "subtype", "verified_sha256"
    ]  # fmt: skip


def test_wavex_container_with_pcm16_is_accepted(tmp_path):
    data = write(tmp_path, "x.wav", EXTREMES, subtype="PCM_16", fmt="WAVEX")
    location = resolve_artifact_location(artifact("x.wav", data, frames=5), root=str(tmp_path))
    clip = read_dist10_pcm16(location, limits=LIMITS)
    assert clip.container == "WAVEX" and clip.samples.tolist() == EXTREMES.tolist()


# --- unsupported formats ----------------------------------------------------------------------------------


@pytest.mark.parametrize("rate", [48000, 32000, 22050, 96000])
def test_other_sample_rates_are_rejected_before_reading(tmp_path, rate, monkeypatch):
    location = located(tmp_path, EXTREMES, rate=rate)
    monkeypatch.setattr(wav_input, "check_resolved_wav_integrity", lambda *a, **k: pytest.fail("file was read"))
    with pytest.raises(Dist10InputError) as raised:
        read_dist10_pcm16(location, limits=LIMITS)
    assert raised.value.code is Code.UNSUPPORTED_SAMPLE_RATE


def test_stereo_is_rejected_before_reading(tmp_path, monkeypatch):
    location = located(tmp_path, np.stack([EXTREMES, EXTREMES], axis=1))
    monkeypatch.setattr(wav_input, "check_resolved_wav_integrity", lambda *a, **k: pytest.fail("file was read"))
    with pytest.raises(Dist10InputError) as raised:
        read_dist10_pcm16(location, limits=LIMITS)
    assert raised.value.code is Code.UNSUPPORTED_CHANNEL_COUNT


@pytest.mark.parametrize("subtype", ["PCM_24", "PCM_32", "PCM_U8", "FLOAT", "DOUBLE"])
def test_other_subtypes_are_rejected(tmp_path, subtype):
    with pytest.raises(Dist10InputError) as raised:
        read_dist10_pcm16(located(tmp_path, EXTREMES, subtype=subtype), limits=LIMITS)
    assert raised.value.code is Code.UNSUPPORTED_SUBTYPE and subtype in str(raised.value)


# --- shared verification failures propagate unchanged -----------------------------------------------------


def test_hash_mismatch_propagates(tmp_path):
    location = located(tmp_path, EXTREMES, digest="0" * 64)
    with pytest.raises(IntegrityError) as raised:
        read_dist10_pcm16(location, limits=LIMITS)
    assert raised.value.code is IntegrityFailureCode.HASH_MISMATCH


def test_frame_count_mismatch_propagates(tmp_path):
    with pytest.raises(IntegrityError) as raised:
        read_dist10_pcm16(located(tmp_path, EXTREMES, frames=6), limits=LIMITS)
    assert raised.value.code is IntegrityFailureCode.METADATA_MISMATCH


def test_declared_profile_rate_against_a_file_of_another_rate_is_a_metadata_mismatch(tmp_path):
    # Declared 44.1 kHz (the profile) while the file says 48 kHz: the shared verifier rejects the mismatch.
    with pytest.raises(IntegrityError) as raised:
        read_dist10_pcm16(located(tmp_path, EXTREMES, rate=48000, declared_rate=44100), limits=LIMITS)
    assert raised.value.code is IntegrityFailureCode.METADATA_MISMATCH


def test_resource_limit_propagates(tmp_path):
    tight = AudioReadLimits(max_file_bytes=1 << 22, max_decoded_bytes=8)
    with pytest.raises(IntegrityError) as raised:
        read_dist10_pcm16(located(tmp_path, EXTREMES), limits=tight)
    assert raised.value.code is IntegrityFailureCode.RESOURCE_LIMIT


def test_stale_location_propagates(tmp_path):
    location = located(tmp_path, EXTREMES)
    (tmp_path / "clip.wav").unlink()
    with pytest.raises(LocationResolutionError) as raised:
        read_dist10_pcm16(location, limits=LIMITS)
    assert raised.value.code is LocationResolutionFailureCode.NOT_FOUND


def test_the_shared_verification_entry_point_is_used(tmp_path, monkeypatch):
    seen = []
    real = wav_input.check_resolved_wav_integrity

    def spy(location, *, max_file_bytes, max_decoded_bytes):
        seen.append((location, max_file_bytes, max_decoded_bytes))
        return real(location, max_file_bytes=max_file_bytes, max_decoded_bytes=max_decoded_bytes)

    monkeypatch.setattr(wav_input, "check_resolved_wav_integrity", spy)
    location = located(tmp_path, EXTREMES)
    read_dist10_pcm16(location, limits=LIMITS)
    assert seen == [(location, LIMITS.max_file_bytes, LIMITS.max_decoded_bytes)]


# --- conversion checks on verified audio ------------------------------------------------------------------


def checked(samples, *, subtype="PCM_16", rate=44100, channels=1) -> CheckedAudio:
    values = np.asarray(samples, dtype=np.float64).reshape(-1, channels)
    values.setflags(write=False)
    record = artifact("c.wav", b"", rate=rate, channels=channels, frames=values.shape[0])
    return CheckedAudio(record, values, "WAV", subtype, record.content_hash)


def test_pcm16_scaling_is_the_decoders_division_by_32768():
    got = pcm16_counts(checked([-1.0, -1 / 32768, 0.0, 1 / 32768, 32767 / 32768]))
    assert got.dtype == np.int16 and got.tolist() == [-32768, -1, 0, 1, 32767]
    assert not got.flags.writeable


@pytest.mark.parametrize("value", [0.5 / 32768, 1e-300, 32766.5 / 32768])
def test_non_integral_scaled_values_are_rejected(value):
    with pytest.raises(Dist10InputError) as raised:
        pcm16_counts(checked([0.0, value, 0.0]))
    assert raised.value.code is Code.NON_INTEGRAL_SAMPLES and "frame 1" in str(raised.value)


@pytest.mark.parametrize("value", [1.0, -32769 / 32768, 2.0])
def test_out_of_range_scaled_values_are_rejected(value):
    with pytest.raises(Dist10InputError) as raised:
        pcm16_counts(checked([0.0, 0.0, value]))
    assert raised.value.code is Code.OUT_OF_RANGE_SAMPLES and "frame 2" in str(raised.value)


@pytest.mark.parametrize("value", [1e308, -1e308, np.finfo(np.float64).max, np.inf, -np.inf, np.nan])
@pytest.mark.parametrize("policy", ["raise", "warn", "ignore"])
def test_decoded_range_is_checked_before_scaling(value, policy):
    # Regression (Codex review of Task 3P): a huge decoded value must be rejected as out of range without the
    # x 32768 scaling overflowing, whatever the caller's NumPy policy; NaN is not within the range either.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with np.errstate(all=policy), pytest.raises(Dist10InputError) as raised:
            pcm16_counts(checked([0.0, 1 / 32768, value]))
    assert raised.value.code is Code.OUT_OF_RANGE_SAMPLES and "frame 2" in str(raised.value)


def test_decoded_range_bounds_are_exact():
    assert pcm16_counts(checked([-1.0, 32767 / 32768])).tolist() == [-32768, 32767]
    for value in (np.nextafter(-1.0, -2.0), np.nextafter(32767 / 32768, 2.0)):
        with pytest.raises(Dist10InputError) as raised:
            pcm16_counts(checked([value]))
        assert raised.value.code is Code.OUT_OF_RANGE_SAMPLES


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"subtype": "PCM_24"}, Code.UNSUPPORTED_SUBTYPE),
        ({"rate": 48000}, Code.UNSUPPORTED_SAMPLE_RATE),
        ({"channels": 2}, Code.UNSUPPORTED_CHANNEL_COUNT),
    ],
)
def test_conversion_checks_the_profile_of_verified_audio(kwargs, code):
    with pytest.raises(Dist10InputError) as raised:
        pcm16_counts(checked([0.0, 0.0], **kwargs))
    assert raised.value.code is code


def test_conversion_leaves_the_verified_samples_untouched():
    audio = checked([0.0, 1 / 32768])
    before = audio.samples.copy()
    pcm16_counts(audio)
    np.testing.assert_array_equal(audio.samples, before)


@pytest.mark.parametrize("bad", [None, "clip.wav", object()])
def test_invalid_arguments_raise_before_any_file_access(bad, tmp_path):
    with pytest.raises(ValueError):
        read_dist10_pcm16(bad, limits=LIMITS)
    with pytest.raises(ValueError):
        read_dist10_pcm16(located(tmp_path, EXTREMES), limits=bad)
    with pytest.raises(ValueError):
        pcm16_counts(bad)


def test_errors_are_value_errors_with_codes():
    error = Dist10InputError(Code.UNSUPPORTED_SUBTYPE, "x")
    assert isinstance(error, ValueError) and str(error) == "unsupported_subtype: x"


# --- resolve-and-read helper (P1 R1 handoff, 2026-11-20) ----------------------------------------------------------


def _member(tmp_path: Path, samples, **declared) -> AudioArtifact:
    data = write(
        tmp_path, "clip.wav", samples, rate=declared.pop("rate", 44100), subtype=declared.pop("subtype", "PCM_16")
    )
    return artifact("clip.wav", data, frames=np.asarray(samples).shape[0], **declared)


def test_member_read_equals_the_resolved_read(tmp_path):
    record = _member(tmp_path, counts(3000))
    clip = read_dist10_member(record, root=str(tmp_path), max_file_bytes=LIMITS.max_file_bytes,
                              max_decoded_bytes=LIMITS.max_decoded_bytes)  # fmt: skip
    direct = read_dist10_pcm16(resolve_artifact_location(record, root=str(tmp_path)), limits=LIMITS)
    assert np.array_equal(clip.samples, direct.samples) and clip.verified_sha256 == direct.verified_sha256


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"digest": "0" * 64}, "hash_mismatch"),
        ({"subtype": "PCM_24"}, "unsupported_subtype"),
    ],
)
def test_member_read_failures_are_one_error_type_with_the_original_code(tmp_path, change, code):
    record = _member(tmp_path, counts(3000), **change)
    with pytest.raises(Dist10ReadError) as raised:
        read_dist10_member(record, root=str(tmp_path), max_file_bytes=LIMITS.max_file_bytes,
                           max_decoded_bytes=LIMITS.max_decoded_bytes)  # fmt: skip
    assert raised.value.code == code and raised.value.__cause__ is not None


def test_member_outside_its_root_is_refused(tmp_path):
    inner = tmp_path / "root"
    inner.mkdir()
    record = dataclasses.replace(_member(tmp_path, counts(3000)), location="../clip.wav")
    with pytest.raises(Dist10ReadError):
        read_dist10_member(record, root=str(inner), max_file_bytes=LIMITS.max_file_bytes,
                           max_decoded_bytes=LIMITS.max_decoded_bytes)  # fmt: skip


def test_member_read_validates_limits(tmp_path):
    with pytest.raises(ValueError):
        read_dist10_member(_member(tmp_path, counts(10)), root=str(tmp_path), max_file_bytes=0, max_decoded_bytes=1)


# Exact dual-mono extension: input representation only, no audio conversion/output.
@pytest.mark.parametrize("n", [5, 4000, 65536])
def test_exact_dual_mono_opt_in_preserves_counts_and_original_artifact(tmp_path, n):
    original = np.arange(-32768, 32768, dtype=np.int16) if n == 65536 else counts(n)
    location = located(tmp_path, np.column_stack((original, original)))
    before = (tmp_path / "clip.wav").read_bytes()
    with pytest.raises(Dist10InputError) as refused:
        read_dist10_pcm16(location, limits=LIMITS)
    assert refused.value.code is Code.UNSUPPORTED_CHANNEL_COUNT
    clip = read_dist10_pcm16(location, limits=LIMITS, allow_exact_dual_mono=True)
    np.testing.assert_array_equal(clip.samples, original)
    assert type(clip.samples) is np.ndarray and clip.samples.ndim == 1 and not clip.samples.flags.writeable
    assert clip.artifact is location.artifact and clip.artifact.channel_count == 2
    assert clip.verified_sha256 == location.artifact.content_hash
    assert clip.analysis_channel_count == 1 and clip.channel_mapping == "exact_dual_mono_first_channel@1"
    assert (tmp_path / "clip.wav").read_bytes() == before
    assert len(list(tmp_path.iterdir())) == 1
    band = dist10_clip_to_bands(clip.samples, sample_rate_hz=44100)
    expected = dist10_clip_to_bands(original, sample_rate_hz=44100)
    for name in ("en", "thm", "call", "eligible", "nonzero_samples"):
        np.testing.assert_array_equal(getattr(band, name), getattr(expected, name))


@pytest.mark.parametrize("index", [0, 3999])
def test_a_single_lsb_difference_in_either_end_of_full_clip_is_refused(tmp_path, index):
    samples = np.zeros((4000, 2), dtype=np.int16)
    samples[index, 1] = 1
    with pytest.raises(Dist10InputError) as raised:
        read_dist10_pcm16(located(tmp_path, samples), limits=LIMITS, allow_exact_dual_mono=True)
    assert raised.value.code is Code.NON_IDENTICAL_CHANNELS and f"frame {index}" in str(raised.value)


def test_inverted_channels_are_not_dual_mono(tmp_path):
    one = np.array([1, -1, 100, -100], dtype=np.int16)
    with pytest.raises(Dist10InputError) as raised:
        read_dist10_pcm16(located(tmp_path, np.column_stack((one, -one))), limits=LIMITS, allow_exact_dual_mono=True)
    assert raised.value.code is Code.NON_IDENTICAL_CHANNELS


@pytest.mark.parametrize(
    "value,code",
    [
        (np.nan, "OUT_OF_RANGE_SAMPLES"),
        (np.inf, "OUT_OF_RANGE_SAMPLES"),
        (-np.inf, "OUT_OF_RANGE_SAMPLES"),
        (1e308, "OUT_OF_RANGE_SAMPLES"),
        (0.5 / 32768, "NON_INTEGRAL_SAMPLES"),
    ],
)
@pytest.mark.parametrize("policy", ["raise", "warn", "ignore"])
def test_right_channel_is_validated_before_selection(value, code, policy):
    audio = checked([[0, 0], [0, value]], channels=2)
    with np.errstate(all=policy), warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(Dist10InputError) as raised:
            pcm16_counts(audio, allow_exact_dual_mono=True)
    assert raised.value.code is getattr(Code, code)


@pytest.mark.parametrize("shape", [(3, 2), (2, 1), (2, 3), (4,)])
def test_decoded_frame_and_channel_shape_must_match_metadata(shape):
    audio = checked(np.zeros((2, 2)), channels=2)
    forged = dataclasses.replace(audio, samples=np.zeros(shape, dtype=np.float64))
    with pytest.raises(Dist10InputError) as raised:
        pcm16_counts(forged, allow_exact_dual_mono=True)
    assert raised.value.code is Code.DECODED_SHAPE_MISMATCH


@pytest.mark.parametrize("value", [1, 0, None, "yes"])
def test_dual_mono_opt_in_is_a_strict_bool(tmp_path, value):
    location = located(tmp_path, EXTREMES)
    for call in (
        lambda: pcm16_counts(checked([0]), allow_exact_dual_mono=value),
        lambda: read_dist10_pcm16(location, limits=LIMITS, allow_exact_dual_mono=value),
        lambda: read_dist10_member(
            location.artifact,
            root=str(tmp_path),
            max_file_bytes=LIMITS.max_file_bytes,
            max_decoded_bytes=LIMITS.max_decoded_bytes,
            allow_exact_dual_mono=value,
        ),
    ):
        with pytest.raises(ValueError, match="bool"):
            call()


def test_dual_mono_uses_one_shared_read_and_original_stereo_limits(tmp_path, monkeypatch):
    real = wav_input.check_resolved_wav_integrity
    seen = []

    def spy(location, **limits):
        seen.append((location.artifact.channel_count, limits))
        return real(location, **limits)

    monkeypatch.setattr(wav_input, "check_resolved_wav_integrity", spy)
    location = located(tmp_path, np.column_stack((EXTREMES, EXTREMES)))
    clip = read_dist10_pcm16(location, limits=LIMITS, allow_exact_dual_mono=True)
    assert seen == [(2, dict(max_file_bytes=LIMITS.max_file_bytes, max_decoded_bytes=LIMITS.max_decoded_bytes))]
    assert clip.artifact.channel_count == 2
    with pytest.raises(IntegrityError) as raised:
        read_dist10_pcm16(location, limits=AudioReadLimits(LIMITS.max_file_bytes, 5 * 8), allow_exact_dual_mono=True)
    assert raised.value.code is IntegrityFailureCode.RESOURCE_LIMIT


def test_opted_in_mono_keeps_identity_mapping(tmp_path):
    clip = read_dist10_pcm16(located(tmp_path, EXTREMES), limits=LIMITS, allow_exact_dual_mono=True)
    assert clip.channel_mapping == "mono_identity@1" and clip.analysis_channel_count == 1
    np.testing.assert_array_equal(clip.samples, EXTREMES)


def test_empty_dual_mono_counts_are_well_defined_but_empty_files_keep_shared_refusal(tmp_path):
    result = pcm16_counts(checked([], channels=2), allow_exact_dual_mono=True)
    assert result.shape == (0,) and result.dtype == np.int16 and not result.flags.writeable
    with pytest.raises(IntegrityError):
        read_dist10_pcm16(
            located(tmp_path, np.empty((0, 2), dtype=np.int16)), limits=LIMITS, allow_exact_dual_mono=True
        )


@pytest.mark.parametrize("subtype", ["PCM_24", "PCM_32", "PCM_U8", "FLOAT", "DOUBLE"])
def test_dual_mono_opt_in_does_not_relax_subtype(tmp_path, subtype):
    with pytest.raises(Dist10InputError) as raised:
        read_dist10_pcm16(
            located(tmp_path, np.column_stack((EXTREMES, EXTREMES)), subtype=subtype),
            limits=LIMITS,
            allow_exact_dual_mono=True,
        )
    assert raised.value.code is Code.UNSUPPORTED_SUBTYPE


def test_dual_mono_subclass_cannot_hide_right_channel_values_and_input_is_unchanged():
    class HidingSlices(np.ndarray):
        def __getitem__(self, key):
            value = super().__getitem__(key)
            return np.zeros_like(value) if isinstance(value, np.ndarray) else value

    base = np.array([[0.0, 0.0], [0.0, np.nan]])
    disguised = base.view(HidingSlices)
    audio = dataclasses.replace(checked([[0, 0], [0, 0]], channels=2), samples=disguised)
    with pytest.raises(Dist10InputError) as raised:
        pcm16_counts(audio, allow_exact_dual_mono=True)
    assert raised.value.code is Code.OUT_OF_RANGE_SAMPLES
    np.testing.assert_array_equal(base, [[0, 0], [0, np.nan]])
    valid = np.array([[0.0, 0.0], [1 / 32768, 1 / 32768]])
    original = valid.copy()
    result = pcm16_counts(dataclasses.replace(audio, samples=valid.view(HidingSlices)), allow_exact_dual_mono=True)
    assert type(result) is np.ndarray and not np.shares_memory(result, valid)
    np.testing.assert_array_equal(result, [0, 1])
    np.testing.assert_array_equal(valid, original)
