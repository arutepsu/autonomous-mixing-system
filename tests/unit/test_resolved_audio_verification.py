"""Tests for WAV verification through a resolved artifact location.

Fixtures are tiny analytical arrays written with soundfile to ``tmp_path``; no project media or
evaluation audio is used. Shared verifier guarantees are exercised through both entry points: the
original absolute-path ``verify_wav_artifact`` and ``verify_resolved_wav_artifact``. Failures the host
cannot produce reliably are simulated with focused doubles of the verifier module's ``open`` or of its
reference to ``resolve_artifact_location``; those doubles do not validate real permission behaviour.
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
from autonomous_mixing.domain.project import ArtifactKind, AudioArtifact
from autonomous_mixing.domain.shared import ArtifactId, ProjectId, Revision, TrackId, VersionRef
from autonomous_mixing.infrastructure.audio import (
    ArtifactVerificationError,
    VerificationFailureCode,
    VerifiedAudio,
    verify_resolved_wav_artifact,
    verify_wav_artifact,
)
from autonomous_mixing.infrastructure.filesystem import (
    LocationResolutionError,
    LocationResolutionFailureCode,
    ResolvedArtifactLocation,
    resolve_artifact_location,
)

Code = VerificationFailureCode
LCode = LocationResolutionFailureCode
LARGE = 1 << 20  # generous limits for tests that are not about resource limits
RATE = 48000
MONO = [[0.5], [-0.25], [0.125], [0.0]]
STEREO = [[0.5, -0.5], [0.25, -0.25]]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, samples, *, rate=RATE, fmt="WAV", subtype="FLOAT") -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.asarray(samples, dtype=np.float64), rate, format=fmt, subtype=subtype)
    return path.read_bytes()


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


def _declared(location, data: bytes, samples, **overrides) -> AudioArtifact:
    array = np.asarray(samples, dtype=np.float64)
    fields = {"channels": array.shape[1], "frames": array.shape[0], "content_hash": _sha(data)}
    return _artifact(location, **{**fields, **overrides})


def _verify_resolved(record, *, max_file_bytes=LARGE, max_decoded_bytes=LARGE) -> VerifiedAudio:
    return verify_resolved_wav_artifact(record, max_file_bytes=max_file_bytes, max_decoded_bytes=max_decoded_bytes)


def _audio_failure(call, code) -> ArtifactVerificationError:
    with pytest.raises(ArtifactVerificationError) as info:
        call()
    assert info.value.code is code, info.value
    return info.value


def _location_failure(call, code) -> LocationResolutionError:
    with pytest.raises(LocationResolutionError) as info:
        call()
    assert info.value.code is code, info.value
    return info.value


@pytest.fixture
def root(tmp_path):
    directory = tmp_path / "root"
    directory.mkdir()
    return directory


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


# --- the new entry point: success and identity -----------------------------------------------


def test_public_audio_interface_exports_the_new_entry_point():
    import autonomous_mixing.infrastructure.audio as audio

    assert audio.verify_resolved_wav_artifact is verification.verify_resolved_wav_artifact
    assert len(audio.__all__) == 7 and "verify_resolved_wav_artifact" in audio.__all__


@pytest.mark.parametrize("samples", [MONO, STEREO], ids=["mono", "stereo"])
def test_relative_artifact_is_verified_and_keeps_its_identity_and_metadata(root, samples):
    data = _write(root / "outputs" / "stem.wav", samples, subtype="DOUBLE")
    artifact = _declared("outputs/stem.wav", data, samples)
    fields_before = tuple(getattr(artifact, f) for f in artifact.__dataclass_fields__)
    record = resolve_artifact_location(artifact, root=str(root))
    result = _verify_resolved(record)
    assert type(result) is VerifiedAudio
    assert result.artifact is artifact and record.artifact is artifact
    assert artifact.location == "outputs/stem.wav"
    assert tuple(getattr(artifact, f) for f in artifact.__dataclass_fields__) == fields_before
    assert (result.container, result.subtype, result.verified_sha256) == ("WAV", "DOUBLE", _sha(data))
    assert result.verified_sha256 == artifact.content_hash
    assert type(result.samples) is np.ndarray
    assert result.samples.dtype == np.float64 and result.samples.dtype.isnative
    assert result.samples.flags.writeable is False
    np.testing.assert_array_equal(result.samples, np.asarray(samples))


def test_absolute_artifact_inside_the_root_is_verified(root):
    path = root / "stem.wav"
    data = _write(path, MONO)
    artifact = _declared(str(path), data, MONO)
    result = _verify_resolved(resolve_artifact_location(artifact, root=str(root)))
    assert result.artifact is artifact and artifact.location == str(path)


def test_equivalent_manually_constructed_record_is_accepted(root):
    data = _write(root / "outputs" / "stem.wav", MONO)
    artifact = _declared("outputs/stem.wav", data, MONO)
    fresh = resolve_artifact_location(artifact, root=str(root))
    manual = ResolvedArtifactLocation(artifact, str(root), fresh.resolved_root, fresh.absolute_path)
    assert manual == fresh and manual is not fresh
    assert _verify_resolved(manual).artifact is artifact


# --- argument checks before resolution or capture ----------------------------------------------


def _forbid_resolution_and_capture(monkeypatch) -> list:
    touched = []
    monkeypatch.setattr(verification, "resolve_artifact_location", lambda *a, **k: touched.append("resolve"))
    monkeypatch.setattr(verification, "open", lambda *a, **k: touched.append("open"), raising=False)
    monkeypatch.setattr(os, "stat", lambda *a, **k: touched.append("stat"))
    return touched


@pytest.mark.parametrize("bad", ["outputs/stem.wav", None, 3], ids=repr)
def test_record_must_be_a_resolved_location_before_any_access(monkeypatch, root, bad):
    touched = _forbid_resolution_and_capture(monkeypatch)
    with pytest.raises(ValueError) as info:
        verify_resolved_wav_artifact(bad, max_file_bytes=LARGE, max_decoded_bytes=LARGE)
    assert not isinstance(info.value, (ArtifactVerificationError, LocationResolutionError))
    assert touched == []


def test_artifact_in_place_of_a_record_is_rejected_before_any_access(monkeypatch, root):
    touched = _forbid_resolution_and_capture(monkeypatch)
    with pytest.raises(ValueError):
        verify_resolved_wav_artifact(_artifact("outputs/stem.wav"), max_file_bytes=LARGE, max_decoded_bytes=LARGE)
    assert touched == []


@pytest.mark.parametrize("bad", [0, -1, True, False, 1.0, None, "10", np.int64(10)], ids=repr)
@pytest.mark.parametrize("which", ["max_file_bytes", "max_decoded_bytes"])
def test_invalid_limits_raise_before_resolution_or_capture(monkeypatch, root, which, bad):
    record = ResolvedArtifactLocation(_artifact("outputs/stem.wav"), str(root), str(root), str(root / "x"))
    touched = _forbid_resolution_and_capture(monkeypatch)
    limits = {"max_file_bytes": LARGE, "max_decoded_bytes": LARGE, which: bad}
    with pytest.raises(ValueError) as info:
        verify_resolved_wav_artifact(record, **limits)
    assert not isinstance(info.value, (ArtifactVerificationError, LocationResolutionError))
    assert touched == []


def test_limits_are_keyword_only_without_defaults(root):
    record = ResolvedArtifactLocation(_artifact("outputs/stem.wav"), str(root), str(root), str(root / "x"))
    with pytest.raises(TypeError):
        verify_resolved_wav_artifact(record)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        verify_resolved_wav_artifact(record, LARGE, LARGE)  # type: ignore[misc]


# --- fresh re-resolution and stale records ---------------------------------------------------


def _resolution_spy(monkeypatch) -> list:
    calls = []
    real = verification.resolve_artifact_location

    def spy(artifact, *, root):
        calls.append((artifact, root))
        return real(artifact, root=root)

    monkeypatch.setattr(verification, "resolve_artifact_location", spy)
    return calls


def test_verifier_re_resolves_the_original_artifact_with_the_configured_root(monkeypatch, root):
    data = _write(root / "outputs" / "stem.wav", MONO)
    artifact = _declared("outputs/stem.wav", data, MONO)
    configured = str(root) + os.sep
    record = resolve_artifact_location(artifact, root=configured)
    calls = _resolution_spy(monkeypatch)
    _verify_resolved(record)
    assert len(calls) == 1
    assert calls[0][0] is artifact and calls[0][1] is configured


@pytest.mark.parametrize("field", ["resolved_root", "absolute_path"])
def test_stale_record_fields_are_rejected_before_any_content_open(monkeypatch, root, field):
    data = _write(root / "outputs" / "stem.wav", MONO)
    decoy = root / "outputs" / "decoy.wav"
    decoy.write_bytes(data)  # identical content: only the path comparison can reject it
    artifact = _declared("outputs/stem.wav", data, MONO)
    fresh = resolve_artifact_location(artifact, root=str(root))
    replacement = {"resolved_root": str(root / "outputs"), "absolute_path": str(decoy.resolve())}[field]
    stale = ResolvedArtifactLocation(
        artifact,
        fresh.configured_root,
        replacement if field == "resolved_root" else fresh.resolved_root,
        replacement if field == "absolute_path" else fresh.absolute_path,
    )
    opened = []
    _spy_open(monkeypatch, opened)
    error = _location_failure(lambda: _verify_resolved(stale), LCode.STALE_RESOLUTION)
    assert str(error).startswith("stale_resolution: ")
    assert opened == []


def test_textually_different_but_canonically_equal_paths_are_stale(monkeypatch, root):
    # Exact equality with the fresh strings is required; no normalization of supplied fields is attempted.
    data = _write(root / "outputs" / "stem.wav", MONO)
    artifact = _declared("outputs/stem.wav", data, MONO)
    fresh = resolve_artifact_location(artifact, root=str(root))
    variant = ResolvedArtifactLocation(
        artifact, fresh.configured_root, fresh.resolved_root + os.sep, fresh.absolute_path
    )
    opened = []
    _spy_open(monkeypatch, opened)
    _location_failure(lambda: _verify_resolved(variant), LCode.STALE_RESOLUTION)
    assert opened == []


def test_link_retargeted_after_resolution_makes_the_record_stale(monkeypatch, root):
    first = root / "first.wav"
    data = _write(first, MONO)
    second = root / "second.wav"
    second.write_bytes(data)
    alias = root / "alias.wav"
    try:
        os.symlink(first, alias)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"host cannot create symbolic links: {exc}")
    artifact = _declared("alias.wav", data, MONO)
    record = resolve_artifact_location(artifact, root=str(root))
    alias.unlink()
    os.symlink(second, alias)
    opened = []
    _spy_open(monkeypatch, opened)
    _location_failure(lambda: _verify_resolved(record), LCode.STALE_RESOLUTION)
    assert opened == []


def test_stale_configured_root_is_resolved_afresh(monkeypatch, root, tmp_path):
    # The record's configured root, not its resolved root, drives the fresh resolution.
    data = _write(root / "outputs" / "stem.wav", MONO)
    artifact = _declared("outputs/stem.wav", data, MONO)
    fresh = resolve_artifact_location(artifact, root=str(root))
    other = tmp_path / "other"
    _write(other / "outputs" / "stem.wav", MONO)
    misdirected = ResolvedArtifactLocation(artifact, str(other), fresh.resolved_root, fresh.absolute_path)
    opened = []
    _spy_open(monkeypatch, opened)
    _location_failure(lambda: _verify_resolved(misdirected), LCode.STALE_RESOLUTION)
    assert opened == []


@pytest.mark.parametrize("code", list(LCode), ids=lambda c: c.name)
def test_fresh_resolution_errors_propagate_unchanged(monkeypatch, root, code):
    data = _write(root / "outputs" / "stem.wav", MONO)
    artifact = _declared("outputs/stem.wav", data, MONO)
    record = resolve_artifact_location(artifact, root=str(root))
    raised = LocationResolutionError(code, "controlled")

    def failing(*args, **kwargs):
        raise raised

    monkeypatch.setattr(verification, "resolve_artifact_location", failing)
    opened = []
    _spy_open(monkeypatch, opened)
    with pytest.raises(LocationResolutionError) as info:
        _verify_resolved(record)
    assert info.value is raised
    assert opened == []


def test_unexpected_errors_from_fresh_resolution_propagate(monkeypatch, root):
    record = ResolvedArtifactLocation(_artifact("outputs/stem.wav"), str(root), str(root), str(root / "x"))
    raised = OSError(5, "I/O error")

    def failing(*args, **kwargs):
        raise raised

    monkeypatch.setattr(verification, "resolve_artifact_location", failing)
    with pytest.raises(OSError) as info:
        _verify_resolved(record)
    assert info.value is raised


def test_file_removed_after_resolution_record_is_a_resolution_failure(root):
    path = root / "outputs" / "stem.wav"
    data = _write(path, MONO)
    record = resolve_artifact_location(_declared("outputs/stem.wav", data, MONO), root=str(root))
    path.unlink()
    _location_failure(lambda: _verify_resolved(record), LCode.NOT_FOUND)


def test_root_removed_after_resolution_record_is_a_resolution_failure(tmp_path):
    root = tmp_path / "root"
    path = root / "stem.wav"
    data = _write(path, MONO)
    record = resolve_artifact_location(_declared("stem.wav", data, MONO), root=str(root))
    path.unlink()
    root.rmdir()
    _location_failure(lambda: _verify_resolved(record), LCode.INVALID_ROOT)


def _after_fresh_resolution(monkeypatch, action):
    real = verification.resolve_artifact_location

    def then(artifact, *, root):
        result = real(artifact, root=root)
        action()
        return result

    monkeypatch.setattr(verification, "resolve_artifact_location", then)


def test_file_disappearing_after_fresh_resolution_keeps_the_audio_capture_code(monkeypatch, root):
    path = root / "outputs" / "stem.wav"
    data = _write(path, MONO)
    record = resolve_artifact_location(_declared("outputs/stem.wav", data, MONO), root=str(root))
    _after_fresh_resolution(monkeypatch, path.unlink)
    _audio_failure(lambda: _verify_resolved(record), Code.NOT_FOUND)


def test_permission_failure_after_fresh_resolution_keeps_the_audio_capture_code(monkeypatch, root):
    path = root / "outputs" / "stem.wav"
    data = _write(path, MONO)
    record = resolve_artifact_location(_declared("outputs/stem.wav", data, MONO), root=str(root))

    def deny(*args, **kwargs):
        raise PermissionError(13, "Permission denied", record.absolute_path)

    monkeypatch.setattr(verification, "open", deny, raising=False)
    _audio_failure(lambda: _verify_resolved(record), Code.NOT_READABLE)
    assert path.read_bytes() == data


def test_changed_content_at_the_same_path_is_a_hash_mismatch(root):
    path = root / "outputs" / "stem.wav"
    data = _write(path, MONO)
    record = resolve_artifact_location(_declared("outputs/stem.wav", data, MONO), root=str(root))
    _write(path, [[-1.0], [-1.0], [-1.0], [-1.0]])
    _audio_failure(lambda: _verify_resolved(record), Code.HASH_MISMATCH)


def test_resolved_directory_is_rejected_by_the_verifier_not_certified_by_resolution(root):
    (root / "outputs").mkdir()
    record = resolve_artifact_location(_artifact("outputs"), root=str(root))
    _audio_failure(lambda: _verify_resolved(record), Code.NOT_REGULAR_FILE)


# --- the original entry point is unchanged ------------------------------------------------------


@pytest.mark.parametrize("location", ["outputs/stem.wav", "./outputs/stem.wav", "stem.wav"], ids=repr)
def test_original_entry_point_still_rejects_relative_locations(monkeypatch, root, location):
    _write(root / "outputs" / "stem.wav", MONO)
    monkeypatch.chdir(root)  # even where the relative path would exist
    opened = []
    _spy_open(monkeypatch, opened)
    _audio_failure(
        lambda: verify_wav_artifact(_artifact(location), max_file_bytes=LARGE, max_decoded_bytes=LARGE),
        Code.UNSUPPORTED_PATH,
    )
    assert opened == []


def test_original_entry_point_has_no_root_confinement(tmp_path):
    path = tmp_path / "anywhere" / "stem.wav"
    data = _write(path, MONO)
    result = verify_wav_artifact(_declared(str(path), data, MONO), max_file_bytes=LARGE, max_decoded_bytes=LARGE)
    assert result.verified_sha256 == _sha(data)


# --- shared guarantees, exercised through both entry points ----------------------------------


class Entry:
    """Builds the artifact for one entry point and runs it with given limits."""

    def __init__(self, kind: str, root: Path):
        self.kind, self.root = kind, root

    def artifact(self, path: Path, data: bytes, samples, **overrides) -> AudioArtifact:
        location = path.relative_to(self.root).as_posix() if self.kind == "resolved" else str(path)
        return _declared(location, data, samples, **overrides)

    def run(self, artifact, *, max_file_bytes=LARGE, max_decoded_bytes=LARGE) -> VerifiedAudio:
        if self.kind == "absolute":
            return verify_wav_artifact(artifact, max_file_bytes=max_file_bytes, max_decoded_bytes=max_decoded_bytes)
        record = resolve_artifact_location(artifact, root=str(self.root))
        return verify_resolved_wav_artifact(record, max_file_bytes=max_file_bytes, max_decoded_bytes=max_decoded_bytes)

    def opened_path(self, path: Path) -> str:
        return str(path) if self.kind == "absolute" else str(path.resolve(strict=True))

    def fail(self, artifact, code, **limits) -> ArtifactVerificationError:
        return _audio_failure(lambda: self.run(artifact, **limits), code)


@pytest.fixture(params=["absolute", "resolved"])
def entry(request, root):
    return Entry(request.param, root)


def test_both_entries_open_once_and_decode_only_the_captured_bytes(entry, root, monkeypatch):
    path = root / "stem.wav"
    data = _write(path, MONO)
    artifact = entry.artifact(path, data, MONO)
    calls, decoder_inputs = [], []
    _spy_open(monkeypatch, calls)
    real_soundfile = sf.SoundFile

    def soundfile_spy(file, *args, **kwargs):
        decoder_inputs.append(file)
        return real_soundfile(file, *args, **kwargs)

    monkeypatch.setattr(sf, "SoundFile", soundfile_spy)
    result = entry.run(artifact)
    assert calls == [(entry.opened_path(path), "rb")]
    assert decoder_inputs and all(isinstance(f, io.BytesIO) for f in decoder_inputs)
    assert result.artifact is artifact and result.verified_sha256 == _sha(data)


def test_both_entries_report_the_captured_payload_when_the_file_changes_afterwards(entry, root, monkeypatch):
    path = root / "stem.wav"
    data = _write(path, MONO)
    replacement = _write(root / "other.wav", [[-1.0], [-1.0], [-1.0], [-1.0]])
    artifact = entry.artifact(path, data, MONO)
    _spy_open(monkeypatch, [], after_close=lambda: path.write_bytes(replacement))
    result = entry.run(artifact)
    assert result.verified_sha256 == _sha(data)
    np.testing.assert_array_equal(result.samples, MONO)
    assert path.read_bytes() == replacement  # the swap really happened


def test_both_entries_reject_a_hash_mismatch_before_decoding(entry, root, monkeypatch):
    path = root / "stem.wav"
    data = _write(path, MONO)
    artifact = entry.artifact(path, data, MONO, content_hash="f" * 64)
    decoded = []
    monkeypatch.setattr(sf, "SoundFile", lambda *a, **k: decoded.append(a) or pytest.fail("decoded"))
    entry.fail(artifact, Code.HASH_MISMATCH)
    assert decoded == []


@pytest.mark.parametrize("fmt", ["WAV", "WAVEX", "RF64"])
def test_both_entries_accept_wav_wavex_and_rf64(entry, root, fmt):
    path = root / "stem.wav"
    try:
        data = _write(path, STEREO, fmt=fmt, subtype="FLOAT")
    except (sf.LibsndfileError, ValueError, TypeError) as exc:
        pytest.skip(f"installed libsndfile {sf.__libsndfile_version__} cannot write {fmt}: {exc}")
    result = entry.run(entry.artifact(path, data, STEREO))
    assert (result.container, result.subtype) == (fmt, "FLOAT")
    np.testing.assert_array_equal(result.samples, STEREO)


@pytest.mark.parametrize("fmt", ["FLAC", "AIFF"])
def test_both_entries_reject_non_wav_containers(entry, root, fmt):
    path = root / "stem.wav"
    data = _write(path, MONO, fmt=fmt, subtype="PCM_16")
    entry.fail(entry.artifact(path, data, MONO), Code.INVALID_WAV)


@pytest.mark.parametrize(
    "overrides", [{"rate": 44100}, {"channels": 2}, {"frames": 3}, {"frames": 5}], ids=["rate", "ch", "few", "many"]
)
def test_both_entries_reject_wrong_metadata(entry, root, overrides):
    path = root / "stem.wav"
    data = _write(path, MONO)
    entry.fail(entry.artifact(path, data, MONO, **overrides), Code.METADATA_MISMATCH)


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_both_entries_reject_nonfinite_samples(entry, root, value):
    samples = [[0.5], [value], [0.25]]
    path = root / "bad.wav"
    data = _write(path, samples, subtype="DOUBLE")
    entry.fail(entry.artifact(path, data, samples), Code.NONFINITE_SAMPLES)


def test_both_entries_preserve_finite_values_above_full_scale(entry, root):
    samples = [[2.5, -3.0], [1.5, 0.0]]
    path = root / "loud.wav"
    data = _write(path, samples, subtype="DOUBLE")
    np.testing.assert_array_equal(entry.run(entry.artifact(path, data, samples)).samples, samples)


def test_both_entries_honour_the_raw_byte_limit_boundary(entry, root):
    path = root / "stem.wav"
    data = _write(path, MONO)
    artifact = entry.artifact(path, data, MONO)
    assert entry.run(artifact, max_file_bytes=len(data)).verified_sha256 == _sha(data)
    entry.fail(artifact, Code.RESOURCE_LIMIT, max_file_bytes=len(data) - 1)


def test_both_entries_honour_the_decoded_byte_limit_boundary(entry, root):
    path = root / "stem.wav"
    data = _write(path, STEREO)
    artifact = entry.artifact(path, data, STEREO)
    exact = 2 * 2 * 8  # frames * channels * float64
    assert entry.run(artifact, max_decoded_bytes=exact).samples.nbytes == exact
    entry.fail(artifact, Code.RESOURCE_LIMIT, max_decoded_bytes=exact - 1)


@pytest.mark.parametrize("limit", [sys.maxsize, 10**100], ids=["sys.maxsize", "googol"])
def test_both_entries_accept_very_large_limits_for_a_tiny_wav(entry, root, limit):
    path = root / "stem.wav"
    data = _write(path, MONO)
    result = entry.run(entry.artifact(path, data, MONO), max_file_bytes=limit, max_decoded_bytes=limit)
    assert result.verified_sha256 == _sha(data)
    np.testing.assert_array_equal(result.samples, MONO)


class _RecordingStream(_StreamProxy):
    def __init__(self, real):
        super().__init__(real)
        self.reads = []

    def read(self, size=-1):
        chunk = self._real.read(size)
        self.reads.append((size, len(chunk)))
        return chunk


@pytest.mark.parametrize(
    "chunk_size, limit_delta, code",
    [(None, 0, None), (None, -1, Code.RESOURCE_LIMIT), (7, 0, None), (7, -1, Code.RESOURCE_LIMIT)],
    ids=["default-exact", "default-over", "7-exact", "7-over"],
)
def test_both_entries_read_in_bounded_chunks(entry, root, monkeypatch, chunk_size, limit_delta, code):
    path = root / "stem.wav"
    data = _write(path, MONO)
    limit = len(data) + limit_delta
    if chunk_size is not None:
        monkeypatch.setattr(verification, "_READ_CHUNK_BYTES", chunk_size)
    chunk_limit = verification._READ_CHUNK_BYTES
    assert chunk_limit <= 1 << 20
    streams = []

    def recording(file, mode="r", *args, **kwargs):
        streams.append(_RecordingStream(builtins.open(file, mode, *args, **kwargs)))
        return streams[-1]

    monkeypatch.setattr(verification, "open", recording, raising=False)
    artifact = entry.artifact(path, data, MONO)
    if code is None:
        assert entry.run(artifact, max_file_bytes=limit).verified_sha256 == _sha(data)
    else:
        entry.fail(artifact, code, max_file_bytes=limit)
    (stream,) = streams
    assert stream.reads and all(0 < size <= chunk_limit for size, _ in stream.reads)
    assert sum(n for _, n in stream.reads) <= limit + 1


@pytest.mark.parametrize(
    "make",
    [
        lambda e, p, d: e.artifact(p, d, MONO),
        lambda e, p, d: e.artifact(p, d, MONO, content_hash="e" * 64),
        lambda e, p, d: e.artifact(p, d, MONO, rate=44100),
    ],
    ids=["success", "hash-failure", "metadata-failure"],
)
def test_both_entries_leave_the_tree_unchanged(entry, root, make):
    path = root / "stem.wav"
    data = _write(path, MONO)
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.parent.rglob("*") if p.is_file()}
    dirs_before = sorted(str(p) for p in root.parent.rglob("*") if p.is_dir())
    try:
        entry.run(make(entry, path, data))
    except ArtifactVerificationError:
        pass
    after = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.parent.rglob("*") if p.is_file()}
    assert after == before
    assert sorted(str(p) for p in root.parent.rglob("*") if p.is_dir()) == dirs_before


MUTATORS = [
    (os, "remove"),
    (os, "unlink"),
    (os, "rename"),
    (os, "replace"),
    (os, "mkdir"),
    (os, "makedirs"),
    (os, "rmdir"),
    (os, "truncate"),
    (os, "utime"),
    (os, "chmod"),
    (Path, "mkdir"),
    (Path, "touch"),
    (Path, "unlink"),
    (Path, "rename"),
    (Path, "replace"),
    (Path, "rmdir"),
    (Path, "write_bytes"),
    (Path, "write_text"),
]


def test_resolved_entry_calls_no_mutating_filesystem_function(root, monkeypatch):
    path = root / "outputs" / "stem.wav"
    data = _write(path, MONO)
    record = resolve_artifact_location(_declared("outputs/stem.wav", data, MONO), root=str(root))
    calls, modes = [], []

    def deny(name):
        def call(*args, **kwargs):
            calls.append(name)
            raise AssertionError(name)

        return call

    for owner, name in MUTATORS:
        if hasattr(owner, name):
            monkeypatch.setattr(owner, name, deny(f"{owner.__name__}.{name}"))
    _spy_open(monkeypatch, modes)
    assert _verify_resolved(record).verified_sha256 == _sha(data)
    assert calls == []
    assert [mode for _, mode in modes] == ["rb"]
