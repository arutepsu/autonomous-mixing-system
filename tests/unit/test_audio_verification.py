"""Tests for read-only WAV artifact verification against declared ``AudioArtifact`` metadata.

Fixtures are tiny analytical arrays written with soundfile to ``tmp_path``; no project media or
evaluation audio is used. Filesystem failures that the platform cannot produce reliably (unreadable
files, misbehaving streams) are simulated with focused test doubles of the module's ``open``.
"""

import builtins
import hashlib
import io
import os
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import autonomous_mixing.adapters.rendering.integrity as verification  # moved in ADR-027 step M3; tests patch or inspect its internals
from autonomous_mixing.adapters.rendering.integrity import (
    ArtifactVerificationError,
    VerificationFailureCode,
    VerifiedAudio,
    verify_wav_artifact,
)
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.identifiers import ArtifactId, ProjectId, Revision, TrackId, VersionRef

Code = VerificationFailureCode
LARGE = 1 << 20  # generous limits for tests that are not about resource limits
RATE = 48000


def _artifact(location, *, rate=RATE, channels=1, frames=4, content_hash="0" * 64) -> AudioArtifact:
    return AudioArtifact(
        ArtifactId("a1"),
        ProjectId("p1"),
        Revision("r1"),
        (TrackId("t1"),),
        ArtifactKind.STEM,
        str(location),
        0.0,
        rate,
        channels,
        frames,
        content_hash,
        VersionRef("render", "1"),
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, samples, *, rate=RATE, fmt="WAV", subtype="FLOAT") -> bytes:
    array = np.asarray(samples, dtype=np.float64)
    sf.write(str(path), array, rate, format=fmt, subtype=subtype)
    return path.read_bytes()


def _declared(path: Path, data: bytes, samples, **overrides) -> AudioArtifact:
    array = np.asarray(samples, dtype=np.float64)
    fields = {"channels": array.shape[1], "frames": array.shape[0], "content_hash": _sha(data)}
    return _artifact(path, **{**fields, **overrides})


def _verify(artifact, *, max_file_bytes=LARGE, max_decoded_bytes=LARGE) -> VerifiedAudio:
    return verify_wav_artifact(artifact, max_file_bytes=max_file_bytes, max_decoded_bytes=max_decoded_bytes)


def _failure(artifact, code, **limits) -> ArtifactVerificationError:
    with pytest.raises(ArtifactVerificationError) as info:
        _verify(artifact, **limits)
    assert info.value.code is code, info.value
    assert isinstance(info.value, ValueError) and str(info.value)
    return info.value


MONO = [[0.5], [-0.25], [0.125], [0.0]]
STEREO = [[0.5, -0.5], [0.25, -0.25]]


# --- successful verification -------------------------------------------------------------


@pytest.mark.parametrize("samples", [MONO, STEREO, [[0.75]], [[0.75, -0.75]]], ids=["mono", "stereo", "1f", "1f-st"])
def test_valid_float_wav_is_verified_exactly(tmp_path, samples):
    path = tmp_path / "stem.wav"
    data = _write(path, samples, subtype="DOUBLE")
    artifact = _declared(path, data, samples)
    result = _verify(artifact)
    assert type(result) is VerifiedAudio and result.artifact is artifact
    assert (result.container, result.subtype, result.verified_sha256) == ("WAV", "DOUBLE", _sha(data))
    assert type(result.samples) is np.ndarray
    assert result.samples.dtype == np.float64 and result.samples.dtype.isnative
    assert result.samples.shape == np.asarray(samples).shape
    assert result.samples.flags.writeable is False
    np.testing.assert_array_equal(result.samples, np.asarray(samples))


@pytest.mark.parametrize(
    "subtype, tolerance", [("PCM_16", 2.0**-15), ("PCM_24", 2.0**-23), ("FLOAT", 0.0), ("DOUBLE", 0.0)]
)
def test_pcm_and_float_subtypes_report_actual_format(tmp_path, subtype, tolerance):
    path = tmp_path / "stem.wav"
    data = _write(path, STEREO, subtype=subtype)
    result = _verify(_declared(path, data, STEREO))
    assert (result.container, result.subtype) == ("WAV", subtype)
    np.testing.assert_allclose(result.samples, STEREO, rtol=0, atol=tolerance)


def test_float_values_above_full_scale_are_preserved(tmp_path):
    samples = [[2.5, -3.0], [1.5, 0.0]]
    path = tmp_path / "loud.wav"
    data = _write(path, samples, subtype="DOUBLE")
    np.testing.assert_array_equal(_verify(_declared(path, data, samples)).samples, samples)


def test_container_is_detected_from_content_not_extension(tmp_path):
    path = tmp_path / "stem.bin"
    data = _write(path, MONO)
    assert _verify(_declared(path, data, MONO)).container == "WAV"


@pytest.mark.parametrize("fmt", ["WAVEX", "RF64"])
def test_wavex_and_rf64_containers_are_accepted(tmp_path, fmt):
    path = tmp_path / "stem.wav"
    try:
        data = _write(path, STEREO, fmt=fmt, subtype="FLOAT")
    except (sf.LibsndfileError, ValueError, TypeError) as exc:
        pytest.skip(f"installed libsndfile {sf.__libsndfile_version__} cannot write {fmt}: {exc}")
    result = _verify(_declared(path, data, STEREO))
    assert (result.container, result.subtype) == (fmt, "FLOAT")
    np.testing.assert_array_equal(result.samples, STEREO)


# --- container and content failures ------------------------------------------------------


@pytest.mark.parametrize("fmt", ["FLAC", "AIFF"])
def test_non_wav_container_named_wav_is_rejected(tmp_path, fmt):
    path = tmp_path / "stem.wav"
    data = _write(path, MONO, fmt=fmt, subtype="PCM_16")
    _failure(_declared(path, data, MONO), Code.INVALID_WAV)


def test_random_bytes_named_wav_are_rejected(tmp_path):
    path = tmp_path / "noise.wav"
    data = bytes(range(256)) * 4
    path.write_bytes(data)
    _failure(_artifact(path, content_hash=_sha(data)), Code.INVALID_WAV)


def test_wav_truncated_inside_its_header_is_rejected_after_hashing(tmp_path):
    path = tmp_path / "cut.wav"
    data = _write(path, MONO)[:30]
    path.write_bytes(data)
    _failure(_declared(path, data, MONO), Code.INVALID_WAV)


def test_wav_with_truncated_sample_data_does_not_match_its_declaration(tmp_path):
    # libsndfile derives the frame count from the data actually present, so lost frames surface as a mismatch.
    path = tmp_path / "short.wav"
    full = _write(path, MONO, subtype="PCM_16")
    data = full[:-3]  # removes one whole frame and half of another
    path.write_bytes(data)
    _failure(_declared(path, data, MONO), Code.METADATA_MISMATCH)


def test_decoder_returning_fewer_frames_than_inspected_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "stem.wav"
    data = _write(path, MONO)
    real_read = sf.SoundFile.read

    def short_read(self, frames=-1, *args, **kwargs):
        return real_read(self, frames - 1, *args, **kwargs)

    monkeypatch.setattr(sf.SoundFile, "read", short_read)
    _failure(_declared(path, data, MONO), Code.INVALID_WAV)


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_nonfinite_samples_are_rejected(tmp_path, value):
    samples = [[0.5], [value], [0.25]]
    path = tmp_path / "bad.wav"
    data = _write(path, samples, subtype="DOUBLE")
    _failure(_declared(path, data, samples), Code.NONFINITE_SAMPLES)


# --- declaration mismatches --------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [{"rate": 44100}, {"channels": 2}, {"frames": 3}, {"frames": 5}, {"rate": 44100, "frames": 3}],
    ids=["rate", "channels", "fewer-frames", "more-frames", "several"],
)
def test_declared_metadata_must_match_the_file(tmp_path, overrides):
    path = tmp_path / "stem.wav"
    data = _write(path, MONO)
    _failure(_declared(path, data, MONO, **overrides), Code.METADATA_MISMATCH)


def test_three_channel_file_cannot_match_any_valid_declaration(tmp_path):
    samples = [[0.1, 0.2, 0.3]]
    path = tmp_path / "three.wav"
    data = _write(path, samples)
    _failure(_artifact(path, channels=2, frames=1, content_hash=_sha(data)), Code.METADATA_MISMATCH)


def test_empty_wav_is_rejected_even_when_declared_empty(tmp_path):
    path = tmp_path / "empty.wav"
    data = _write(path, np.zeros((0, 1)))
    _failure(_artifact(path, channels=1, frames=0, content_hash=_sha(data)), Code.INVALID_WAV)
    _failure(_artifact(path, channels=1, frames=4, content_hash=_sha(data)), Code.METADATA_MISMATCH)


# --- hash identity and the single captured payload ---------------------------------------


def test_hash_mismatch_is_rejected_before_decoding(tmp_path, monkeypatch):
    path = tmp_path / "stem.wav"
    data = _write(path, MONO)
    opened = []
    monkeypatch.setattr(sf, "SoundFile", lambda *a, **k: opened.append(a) or pytest.fail("decoded"))
    _failure(_declared(path, data, MONO, content_hash="f" * 64), Code.HASH_MISMATCH)
    assert opened == []


class _StreamProxy:
    """Delegates to a real binary stream and runs ``after_close`` once the module has closed it."""

    def __init__(self, real, after_close=None):
        self._real, self._after_close = real, after_close

    def fileno(self):
        return self._real.fileno()

    def read(self, size=-1):
        return self._real.read(size)

    def close(self):
        self._real.close()
        if self._after_close is not None:
            self._after_close()
            self._after_close = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _spy_open(monkeypatch, calls, *, after_close=None):
    def spy(file, mode="r", *args, **kwargs):
        calls.append((file, mode))
        return _StreamProxy(builtins.open(file, mode, *args, **kwargs), after_close)

    monkeypatch.setattr(verification, "open", spy, raising=False)


def test_path_is_opened_once_and_decoded_only_from_the_captured_bytes(tmp_path, monkeypatch):
    path = tmp_path / "stem.wav"
    data = _write(path, MONO)
    calls, decoder_inputs = [], []
    _spy_open(monkeypatch, calls)
    real_soundfile = sf.SoundFile

    def soundfile_spy(file, *args, **kwargs):
        decoder_inputs.append(file)
        return real_soundfile(file, *args, **kwargs)

    monkeypatch.setattr(sf, "SoundFile", soundfile_spy)
    result = _verify(_declared(path, data, MONO))
    assert calls == [(str(path), "rb")]
    assert decoder_inputs and all(isinstance(f, io.BytesIO) for f in decoder_inputs)
    assert result.verified_sha256 == _sha(data)


def test_result_reflects_the_captured_payload_when_the_file_changes_afterwards(tmp_path, monkeypatch):
    path = tmp_path / "stem.wav"
    data = _write(path, MONO)
    replacement = tmp_path / "other.wav"
    _write(replacement, [[-1.0], [-1.0], [-1.0], [-1.0]])
    _spy_open(monkeypatch, [], after_close=lambda: path.write_bytes(replacement.read_bytes()))
    result = _verify(_declared(path, data, MONO))
    assert result.verified_sha256 == _sha(data)
    np.testing.assert_array_equal(result.samples, MONO)
    assert path.read_bytes() == replacement.read_bytes()  # the swap really happened


@pytest.mark.parametrize(
    "make",
    [
        lambda p, d: _declared(p, d, MONO),
        lambda p, d: _declared(p, d, MONO, content_hash="e" * 64),
        lambda p, d: _declared(p, d, MONO, rate=44100),
    ],
    ids=["success", "hash-failure", "metadata-failure"],
)
def test_source_bytes_and_mtime_are_unchanged(tmp_path, make):
    path = tmp_path / "stem.wav"
    data = _write(path, MONO)
    before = os.stat(path)
    try:
        _verify(make(path, data))
    except ArtifactVerificationError:
        pass
    after = os.stat(path)
    assert path.read_bytes() == data
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)


# --- resource limits ---------------------------------------------------------------------


def test_raw_byte_limit_boundary(tmp_path):
    path = tmp_path / "stem.wav"
    data = _write(path, MONO)
    artifact = _declared(path, data, MONO)
    assert _verify(artifact, max_file_bytes=len(data)).verified_sha256 == _sha(data)
    _failure(artifact, Code.RESOURCE_LIMIT, max_file_bytes=len(data) - 1)


def test_decoded_byte_limit_boundary(tmp_path):
    path = tmp_path / "stem.wav"
    data = _write(path, STEREO)
    artifact = _declared(path, data, STEREO)
    exact = 2 * 2 * 8  # frames * channels * float64
    assert _verify(artifact, max_decoded_bytes=exact).samples.nbytes == exact
    _failure(artifact, Code.RESOURCE_LIMIT, max_decoded_bytes=exact - 1)


def test_bounded_read_detects_a_stream_longer_than_its_reported_size(tmp_path, monkeypatch):
    path = tmp_path / "stem.wav"
    data = _write(path, MONO)
    limit = len(data)
    requests = []

    class GrowingStream:
        """Reports the real file's size via fileno/fstat but yields more bytes than that."""

        def __init__(self, real):
            self._real, self._extra = real, io.BytesIO(data + b"\0" * 64)

        def fileno(self):
            return self._real.fileno()

        def read(self, size=-1):
            requests.append(size)
            return self._extra.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._real.close()

        def close(self):
            self._real.close()

    monkeypatch.setattr(
        verification, "open", lambda f, m="r", *a, **k: GrowingStream(builtins.open(f, m)), raising=False
    )
    _failure(_declared(path, data, MONO), Code.RESOURCE_LIMIT, max_file_bytes=limit)
    assert requests and all(0 < size <= limit + 1 for size in requests)


@pytest.mark.parametrize("limit", [sys.maxsize, 10**100], ids=["sys.maxsize", "googol"])
def test_very_large_limits_are_accepted_for_a_tiny_wav(tmp_path, limit):
    path = tmp_path / "stem.wav"
    data = _write(path, MONO)
    result = _verify(_declared(path, data, MONO), max_file_bytes=limit, max_decoded_bytes=limit)
    assert result.verified_sha256 == _sha(data)
    np.testing.assert_array_equal(result.samples, MONO)


@pytest.mark.parametrize("limit", [1, 16], ids=["1-byte", "16-bytes"])
def test_content_exceeding_a_small_limit_is_still_rejected(tmp_path, limit):
    path = tmp_path / "stem.wav"
    data = _write(path, MONO)
    assert len(data) > limit
    _failure(_declared(path, data, MONO), Code.RESOURCE_LIMIT, max_file_bytes=limit)


class _RecordingStream(_StreamProxy):
    """Records each requested size, the bytes returned and the allowance remaining before the request."""

    def __init__(self, real, allowance):
        super().__init__(real)
        self.allowance, self.reads = allowance, []

    def read(self, size=-1):
        remaining = self.allowance - sum(len(chunk) for _, chunk, _ in self.reads)
        chunk = self._real.read(size)
        self.reads.append((size, chunk, remaining))
        return chunk


def _recording_open(monkeypatch, limit):
    streams = []

    def recording(file, mode="r", *args, **kwargs):
        streams.append(_RecordingStream(builtins.open(file, mode, *args, **kwargs), limit + 1))
        return streams[-1]

    monkeypatch.setattr(verification, "open", recording, raising=False)
    return streams


@pytest.mark.parametrize(
    "chunk_size, limit_delta, code",
    [
        (None, -60, Code.RESOURCE_LIMIT),  # limit below the default chunk size
        (7, 0, None),  # payload spans many 7-byte chunks and fits exactly
        (7, -1, Code.RESOURCE_LIMIT),  # many chunks, one byte over
        (7, 7, None),  # allowance not a multiple of the chunk size
    ],
    ids=["small-limit-default-chunk", "multi-chunk-exact", "multi-chunk-over", "multi-chunk-slack"],
)
def test_chunked_reads_never_exceed_chunk_size_or_remaining_allowance(
    tmp_path, monkeypatch, chunk_size, limit_delta, code
):
    path = tmp_path / "stem.wav"
    data = _write(path, MONO)
    limit = len(data) + limit_delta
    if chunk_size is not None:
        monkeypatch.setattr(verification, "_READ_CHUNK_BYTES", chunk_size)
    chunk_limit = verification._READ_CHUNK_BYTES
    assert limit < chunk_limit or chunk_size is not None
    streams = _recording_open(monkeypatch, limit)
    artifact = _declared(path, data, MONO)
    if code is None:
        assert _verify(artifact, max_file_bytes=limit).verified_sha256 == _sha(data)
    else:
        _failure(artifact, code, max_file_bytes=limit)
    (stream,) = streams
    assert stream.reads
    for size, chunk, remaining in stream.reads:
        assert 0 < size <= chunk_limit and size <= remaining
        assert len(chunk) <= size
    consumed = sum(len(chunk) for _, chunk, _ in stream.reads)
    assert consumed <= limit + 1
    if code is None:
        assert consumed == len(data) and stream.reads[-1][1] == b""  # stopped at EOF
        assert len(stream.reads) > 2
    else:
        assert consumed == limit + 1  # stopped as soon as the allowance was exhausted


@pytest.mark.parametrize("bad", [0, -1, True, False, 1.0, None, "10", np.int64(10)], ids=repr)
@pytest.mark.parametrize("which", ["max_file_bytes", "max_decoded_bytes"])
def test_invalid_limits_raise_before_any_file_operation(tmp_path, monkeypatch, which, bad):
    touched = []
    monkeypatch.setattr(verification, "open", lambda *a, **k: touched.append(a), raising=False)
    monkeypatch.setattr(os, "stat", lambda *a, **k: touched.append(a))
    limits = {"max_file_bytes": LARGE, "max_decoded_bytes": LARGE, which: bad}
    with pytest.raises(ValueError) as info:
        verify_wav_artifact(_artifact(tmp_path / "stem.wav"), **limits)
    assert not isinstance(info.value, ArtifactVerificationError)
    assert touched == []


def test_artifact_must_be_an_audio_artifact():
    with pytest.raises(ValueError):
        verify_wav_artifact("C:/stem.wav", max_file_bytes=LARGE, max_decoded_bytes=LARGE)


# --- filesystem failures -----------------------------------------------------------------


def test_missing_file_is_reported(tmp_path):
    _failure(_artifact(tmp_path / "missing.wav"), Code.NOT_FOUND)


def test_missing_parent_directory_is_reported(tmp_path):
    _failure(_artifact(tmp_path / "missing" / "stem.wav"), Code.NOT_FOUND)


def test_directory_is_not_a_regular_file(tmp_path):
    directory = tmp_path / "stem.wav"
    directory.mkdir()
    _failure(_artifact(directory), Code.NOT_REGULAR_FILE)


def test_unreadable_file_is_reported(tmp_path, monkeypatch):
    path = tmp_path / "stem.wav"
    data = _write(path, MONO)

    def deny(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(verification, "open", deny, raising=False)
    _failure(_declared(path, data, MONO), Code.NOT_READABLE)
    assert path.read_bytes() == data


def test_handle_that_is_not_a_regular_file_is_rejected(tmp_path, monkeypatch):
    # The opened handle is checked as well as the pathname: this fstat double reports a directory.
    path = tmp_path / "stem.wav"
    data = _write(path, MONO)
    directory = os.stat(tmp_path)
    fstat_calls = []

    def directory_fstat(fd):
        fstat_calls.append(fd)
        return directory

    monkeypatch.setattr(verification.os, "fstat", directory_fstat)
    _failure(_declared(path, data, MONO), Code.NOT_REGULAR_FILE)
    assert len(fstat_calls) == 1
    assert path.read_bytes() == data


# --- unsupported path forms --------------------------------------------------------------


@pytest.mark.parametrize(
    "location",
    [
        "stem.wav",
        "outputs/stem.wav",
        "./stem.wav",
        "../stem.wav",
        "file:///C:/stem.wav",
        "file:C:/stem.wav",
        "FILE:///tmp/stem.wav",
        "https://example.org/stem.wav",
        "s3://bucket/stem.wav",
        r"\\server\share\stem.wav",
        "//server/share/stem.wav",
        r"\\?\C:\stem.wav",
        r"\\.\C:\stem.wav",
        "/\\server/share/stem.wav",
        "C:/dir/stem\x00.wav",
    ]
    + ([r"\stem.wav", "/stem.wav", "C:stem.wav", r"C:dir\stem.wav"] if os.name == "nt" else []),
    ids=repr,
)
def test_unsupported_path_forms_are_rejected_without_file_access(monkeypatch, location):
    touched = []
    monkeypatch.setattr(verification, "open", lambda *a, **k: touched.append(a), raising=False)
    _failure(_artifact(location), Code.UNSUPPORTED_PATH)
    assert touched == []


def test_verification_failure_codes_are_complete():
    assert {code.name for code in Code} == {
        "UNSUPPORTED_PATH",
        "NOT_FOUND",
        "NOT_READABLE",
        "NOT_REGULAR_FILE",
        "RESOURCE_LIMIT",
        "HASH_MISMATCH",
        "INVALID_WAV",
        "METADATA_MISMATCH",
        "NONFINITE_SAMPLES",
    }
