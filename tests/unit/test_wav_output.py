"""Tests for exclusive WAV/DOUBLE output of already computed float64 samples.

Sample arrays are tiny analytical unit-test signals (signed zeros, subnormals, ordinary and
above-full-scale finite values); they are not evaluation data and no project media is used. Real
writes, collisions (files, directories, symbolic links, Windows junctions, dangling links), same-name
concurrent writers and independent ``soundfile`` decoding run against the native filesystem in
``tmp_path`` and skip with the host's reason where a link type cannot be created. Short or zero
writes, flush/seek/read/close failures, corrupted readback, workspace changes between steps and
encoder responses are injected with controlled doubles; those exercise branch mapping and handle
lifecycle only and are not evidence of real platform behaviour. Every injected test that creates a
file still creates it through the real exclusive open, so retention is observed on disk.
"""

import builtins
import dataclasses
import enum
import errno
import hashlib
import io
import os
import shutil
import stat
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

import autonomous_mixing.adapters.rendering.wav_output as writing  # moved in ADR-027 step M3 and to audio_io by ADR-028; tests patch or inspect its internals
from autonomous_mixing.adapters.audio_io.locations import resolve_artifact_location
from autonomous_mixing.adapters.rendering.wav_output import (
    WavOutputError,
    WavOutputFailureCode,
    WrittenWav,
    write_wav_output,
)
from autonomous_mixing.adapters.rendering.workspaces import (
    AllocatedWorkspace,
    WorkspaceAllocationError,
    WorkspaceAllocationFailureCode,
    WorkspaceAllocationRequest,
    WorkspaceRoot,
    allocate_workspace,
)
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    CheckpointId,
    ExecutionId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.mix import (
    GainRule,
    LevelPolicy,
    NumericalPrecision,
    OutputContainer,
    ProcessingSpec,
    SampleEncoding,
    SummationOrder,
)
from autonomous_mixing.core.records.observation import RenderSpec
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)
from autonomous_mixing.core.records.rendering import ExecutionWorkspace

Code = WavOutputFailureCode
WINDOWS = os.name == "nt"
TOKEN = "exec-1"
ROOT_KEY = "outputs"
_REAL = SimpleNamespace(open=builtins.open, symlink=os.symlink, rmdir=os.rmdir, lstat=os.lstat, soundfile=sf.SoundFile)


# --- domain fixtures (no filesystem access) -----------------------------------------------------


def _artifact(artifact_id, location, *, tracks=("t1",), kind=ArtifactKind.STEM, revision="r1"):
    return AudioArtifact(
        ArtifactId(artifact_id),
        ProjectId("p1"),
        Revision(revision),
        tuple(TrackId(t) for t in tracks),
        kind,
        location,
        0.0,
        48000,
        1,
        4,
        "0" * 64,
        VersionRef("render", "1"),
    )


STEM_T1 = _artifact("stem-t1", "stem-t1.wav")
MIX = _artifact("mix", "mix.wav", tracks=("t1", "t2"), kind=ArtifactKind.MIX, revision="r2")
BASE_T2 = _artifact("base-t2", "base-t2.wav", tracks=("t2",), revision="r0")


def _state() -> ProjectState:
    project, revision = ProjectId("p1"), Revision("r2")
    snapshot = ProjectSnapshot(
        project,
        revision,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        (Track(TrackId("t1"), "one"), Track(TrackId("t2"), "two")),
    )
    processing = ProjectProcessingState(
        project,
        revision,
        (TrackGainState(TrackId("t1"), STEM_T1, 0.0), TrackGainState(TrackId("t2"), BASE_T2, -3.0)),
    )
    audio = ArtifactCheckpoint(CheckpointId("c1"), project, revision, (STEM_T1, MIX))
    return ProjectState(snapshot, processing, audio)


def _render(rate=48000, channels=2) -> RenderSpec:
    return RenderSpec(VersionRef("render", "1"), rate, channels)


PROCESSING = ProcessingSpec(
    VersionRef("processing", "1"),
    NumericalPrecision.FLOAT64,
    GainRule.DB_AMPLITUDE,
    SummationOrder.RECIPE_SEQUENTIAL,
    LevelPolicy.PRESERVE,
    OutputContainer.WAV,
    SampleEncoding.FLOAT64,
)
TINY = 5e-324  # smallest positive subnormal float64
# Analytical unit-test values: signed zeros, subnormals, ordinary and above-full-scale finite values.
SPECIAL = np.array(
    [[0.0, -0.0], [TINY, -TINY], [2.2250738585072014e-308 / 3, -1.0e-310], [0.5, -0.25], [3.75, -1.0e300]],
    dtype=np.float64,
)
MONO = np.array([[0.0], [-0.0], [TINY], [-TINY], [1.5], [-2.0e9]], dtype=np.float64)
BIG = 1 << 40


def _manual_workspace(tmp_path=None) -> AllocatedWorkspace:
    base = Path(tmp_path) if tmp_path is not None else Path("C:\\none" if WINDOWS else "/none")
    request = WorkspaceAllocationRequest(
        ExecutionWorkspace(ExecutionId("e1"), ROOT_KEY, TOKEN), WorkspaceRoot(ROOT_KEY, str(base)), _state(), ()
    )
    return AllocatedWorkspace(request, str(base), str(base / TOKEN))


def _bits(array: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(array).view(np.uint64)


def _same_bits(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and np.array_equal(_bits(a), _bits(b))


def _decode(payload: bytes):
    with _REAL.soundfile(io.BytesIO(payload)) as handle:
        info = (handle.format, handle.subtype, handle.samplerate, handle.channels, handle.frames)
        samples = handle.read(dtype="float64", always_2d=True)
    return info, samples


def _id(value):
    if isinstance(value, BaseException):
        return f"{type(value).__name__}-{getattr(value, 'errno', '')}"
    return repr(value)


class _Forbidden(AssertionError):
    pass


def _deny(monkeypatch, entries) -> list:
    calls = []

    def deny(name):
        def call(*args, **kwargs):
            calls.append(name)
            raise _Forbidden(name)

        return call

    for owner, name in entries:
        monkeypatch.setattr(owner, name, deny(f"{getattr(owner, '__name__', owner)}.{name}"))
    return calls


FILESYSTEM = [
    (Path, "resolve"),
    (Path, "mkdir"),
    (Path, "exists"),
    (Path, "stat"),
    (Path, "lstat"),
    (Path, "open"),
    (os, "stat"),
    (os, "lstat"),
    (os, "mkdir"),
    (os, "scandir"),
    (os, "listdir"),
    (os, "open"),
    (builtins, "open"),
    (io, "open"),
]


# --- Task 2: public surface, records and errors ----------------------------------------------------


def test_failure_codes_are_exactly_the_specified_members_with_lowercase_values():
    expected = [
        "RESOURCE_LIMIT",
        "ENCODING_FAILED",
        "WORKSPACE_REJECTED",
        "COLLISION",
        "NOT_WRITABLE",
        "WORKSPACE_CHANGED",
        "IO_FAILED",
        "VERIFICATION_FAILED",
    ]
    assert [member.name for member in Code] == expected
    assert [member.value for member in Code] == [name.lower() for name in expected]


@pytest.mark.parametrize("code", list(Code), ids=lambda c: c.name)
def test_error_carries_code_prefix_and_no_created_file_by_default(code):
    error = WavOutputError(code, "details")
    assert isinstance(error, ValueError) and error.code is code and error.created_file is None
    assert str(error) == f"{code.value}: details"


def test_error_created_file_is_keyword_only():
    error = WavOutputError(Code.IO_FAILED, "failed", created_file="/w/target.wav")
    assert error.created_file == "/w/target.wav"
    with pytest.raises(TypeError):
        WavOutputError(Code.IO_FAILED, "failed", "/w/target.wav")  # type: ignore[misc]


def _written(**changes) -> WrittenWav:
    fields = dict(
        workspace=_manual_workspace(),
        path="/w/exec-1/target.wav",
        sha256="a" * 64,
        byte_count=96,
        frame_count=2,
        sample_rate_hz=48000,
        channel_count=2,
        container="WAV",
        subtype="DOUBLE",
        render_spec=_render(),
        processing_spec=PROCESSING,
    )
    fields.update(changes)
    return WrittenWav(**fields)


def test_written_wav_has_exactly_the_specified_fields_in_order_and_is_frozen():
    names = [f.name for f in dataclasses.fields(WrittenWav)]
    assert names == [
        "workspace",
        "path",
        "sha256",
        "byte_count",
        "frame_count",
        "sample_rate_hz",
        "channel_count",
        "container",
        "subtype",
        "render_spec",
        "processing_spec",
    ]
    record = _written()
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.path = "/x"  # type: ignore[misc]


def test_written_wav_retains_original_objects_and_performs_no_filesystem_access(monkeypatch):
    workspace, render = _manual_workspace(), _render()
    touched = _deny(monkeypatch, FILESYSTEM)
    record = _written(workspace=workspace, render_spec=render)
    assert touched == []
    assert record.workspace is workspace and record.render_spec is render and record.processing_spec is PROCESSING
    assert not any(isinstance(getattr(record, f.name), np.ndarray) for f in dataclasses.fields(record))


@pytest.mark.parametrize(
    "changes",
    [
        {"workspace": "ws"},
        {"path": ""},
        {"path": "  "},
        {"path": Path("/w/a.wav")},
        {"sha256": "A" * 64},
        {"sha256": "a" * 63},
        {"sha256": b"a" * 64},
        {"byte_count": 0},
        {"byte_count": True},
        {"byte_count": 96.0},
        {"byte_count": np.int64(96)},
        {"frame_count": 0},
        {"frame_count": False},
        {"sample_rate_hz": 44100},
        {"channel_count": 1},
        {"channel_count": 3},
        {"container": ""},
        {"subtype": None},
        {"render_spec": "spec"},
        {"processing_spec": None},
    ],
    ids=repr,
)
def test_written_wav_rejects_structurally_invalid_fields(changes):
    with pytest.raises(ValueError):
        _written(**changes)


# --- Task 2: argument validation ---------------------------------------------------------------------

VALID_NAMES = ["target.wav", "mix.wav", "A_b-9.wav", "CON_.wav", "COM10.wav", "x.wav"]
INVALID_NAMES = [
    "",
    "a",
    ".wav",
    "a.WAV",
    "a.Wav",
    "a.wave",
    "a.wav.",
    "a.wav ",
    " a.wav",
    "a..wav",
    "a.b.wav",
    "a b.wav",
    "\u00fc.wav",
    "a/b.wav",
    "a\\b.wav",
    "../a.wav",
    "..\\a.wav",
    "/a.wav",
    "C:a.wav",
    "C:\\a.wav",
    "a.wav:stream",
    "~a.wav",
    "$HOME.wav",
    "%TEMP%.wav",
    "a\x00.wav",
    "a.wav\n",
]
RESERVED_NAMES = ["CON.wav", "con.wav", "NUL.wav", "Aux.wav", "COM1.wav", "lpt9.wav", "PRN.wav"]


@pytest.mark.parametrize("name", VALID_NAMES)
def test_filename_helper_accepts_plain_leaf_names_on_both_policies(name):
    assert writing._filename_problem(name, windows=True) is None
    assert writing._filename_problem(name, windows=False) is None


@pytest.mark.parametrize("name", INVALID_NAMES + [None, b"a.wav", 1], ids=repr)
def test_filename_helper_rejects_everything_outside_the_exact_leaf_pattern(name):
    assert writing._filename_problem(name, windows=True) is not None
    assert writing._filename_problem(name, windows=False) is not None


@pytest.mark.parametrize("name", RESERVED_NAMES)
def test_filename_helper_rejects_windows_reserved_stems_only_under_the_windows_policy(name):
    assert writing._filename_problem(name, windows=True) is not None
    assert writing._filename_problem(name, windows=False) is None


def _prepare(samples=SPECIAL, *, filename="target.wav", workspace=None, render=None, processing=PROCESSING, **limits):
    kwargs = {"max_file_bytes": BIG, "max_decoded_bytes": BIG, **limits}
    return writing._prepare(
        workspace if workspace is not None else _manual_workspace(),
        filename,
        samples,
        render_spec=render if render is not None else _render(channels=samples.shape[1]),
        processing_spec=processing,
        **kwargs,
    )


def _plain_error(callable_, *args, **kwargs) -> ValueError:
    with pytest.raises(ValueError) as info:
        callable_(*args, **kwargs)
    assert type(info.value) is ValueError, info.value
    return info.value


@pytest.mark.parametrize("name", INVALID_NAMES)
def test_prepare_rejects_invalid_filenames_as_plain_value_errors(name):
    _plain_error(_prepare, filename=name)


class _Subarray(np.ndarray):
    pass


_ORDINARY = np.array([[0.5, -0.25], [0.0, 1.0]], dtype=np.float64)
BAD_SAMPLES = {
    "list": [[0.0, 0.0]],
    "subclass": _ORDINARY.view(_Subarray),
    "float32": _ORDINARY.astype(np.float32),
    "non-native": _ORDINARY.astype(">f8" if sys.byteorder == "little" else "<f8"),
    "complex": _ORDINARY.astype(np.complex128),
    "int64": np.zeros((4, 2), dtype=np.int64),
    "1-d": np.zeros(4),
    "3-d": np.zeros((2, 2, 1)),
    "no-frames": np.zeros((0, 2)),
    "no-channels": np.zeros((3, 0)),
    "three-channels": np.zeros((3, 3)),
    "nan": np.array([[0.0, np.nan]]),
    "inf": np.array([[np.inf, 0.0]]),
    "-inf": np.array([[0.0, -np.inf]]),
}


@pytest.mark.parametrize("samples", list(BAD_SAMPLES.values()), ids=list(BAD_SAMPLES))
def test_prepare_rejects_invalid_samples_without_casting(samples):
    render = _render(channels=2)
    _plain_error(
        writing._prepare,
        _manual_workspace(),
        "target.wav",
        samples,
        render_spec=render,
        processing_spec=PROCESSING,
        max_file_bytes=BIG,
        max_decoded_bytes=BIG,
    )


def test_prepare_rejects_channel_disagreement_with_the_render_spec():
    _plain_error(_prepare, SPECIAL, render=_render(channels=1))
    _plain_error(_prepare, MONO, render=_render(channels=2))


def test_prepare_rejects_sample_rates_libsndfile_cannot_represent():
    _plain_error(_prepare, MONO, render=_render(rate=2**31, channels=1))
    _plain_error(_prepare, MONO, render=_render(rate=10**12, channels=1))


def test_prepare_accepts_the_largest_representable_sample_rate():
    snapshot, payload = _prepare(MONO, render=_render(rate=2**31 - 1, channels=1))
    assert _decode(payload)[0][2] == 2**31 - 1


class _IntSubclass(int):
    pass


@pytest.mark.parametrize("which", ["max_file_bytes", "max_decoded_bytes"])
@pytest.mark.parametrize(
    "value", [0, -1, True, 1.0, 10.0**6, np.int64(10**6), _IntSubclass(10**6), "100", None], ids=repr
)
def test_prepare_requires_positive_exact_built_in_int_limits(which, value):
    _plain_error(_prepare, **{which: value})


@pytest.mark.parametrize(
    "field, value",
    [("workspace", "ws"), ("render", "spec"), ("processing", "spec")],
)
def test_prepare_rejects_wrong_record_types(field, value):
    kwargs = {"workspace": value} if field == "workspace" else {field: value}
    _plain_error(_prepare, **kwargs)


class _FutureContainer(enum.Enum):
    FLAC = "flac"


class _FutureEncoding(enum.Enum):
    FLOAT32 = "float32"


@pytest.mark.parametrize(
    "field, value", [("output_container", _FutureContainer.FLAC), ("sample_encoding", _FutureEncoding.FLOAT32)]
)
def test_unsupported_container_or_encoding_never_falls_back(field, value):
    spec = dataclasses.replace(PROCESSING)
    object.__setattr__(spec, field, value)  # bypasses domain validation to simulate a future value
    _plain_error(_prepare, processing=spec)


def test_prepare_performs_no_filesystem_access(monkeypatch):
    touched = _deny(monkeypatch, FILESYSTEM)
    _prepare()
    _plain_error(_prepare, filename="../a.wav")
    assert touched == []


# --- Task 2: private snapshot ---------------------------------------------------------------------------


def _layouts():
    base = np.arange(24, dtype=np.float64).reshape(12, 2) - 6.5
    base[0] = (-0.0, TINY)
    readonly = base.copy()
    readonly.setflags(write=False)
    return {
        "contiguous": base.copy(),
        "readonly": readonly,
        "row-strided": base[::2],
        "negative-stride": base[::-1],
        "fortran": np.asfortranarray(base),
        "column-view": base[:, 1:],
    }


@pytest.mark.parametrize("layout", list(_layouts()))
def test_snapshot_is_a_private_exact_copy_and_the_caller_array_is_unchanged(layout):
    samples = _layouts()[layout]
    before = samples.copy()
    flags = (samples.flags.writeable, samples.flags.c_contiguous, samples.strides)
    snapshot, payload = _prepare(samples)
    assert snapshot is not samples and not np.shares_memory(snapshot, samples)
    assert snapshot.dtype == np.float64 and snapshot.dtype.isnative and snapshot.flags.c_contiguous
    assert _same_bits(snapshot, samples) and _same_bits(samples, before)
    assert (samples.flags.writeable, samples.flags.c_contiguous, samples.strides) == flags
    assert _same_bits(_decode(payload)[1], before)


# --- Task 2: in-memory encoding ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "samples", [SPECIAL, MONO, SPECIAL[:1], MONO[:1]], ids=["stereo", "mono", "stereo-1", "mono-1"]
)
def test_encoding_is_wav_double_with_bit_exact_samples(samples):
    snapshot, payload = _prepare(samples)
    (container, subtype, rate, channels, frames), decoded = _decode(payload)
    assert (container, subtype, rate, channels, frames) == ("WAV", "DOUBLE", 48000, samples.shape[1], samples.shape[0])
    assert payload[:4] == b"RIFF" and payload[8:12] == b"WAVE"
    assert int.from_bytes(payload[4:8], "little") == len(payload) - 8
    assert decoded.dtype == np.float64 and _same_bits(decoded, samples)
    # Signed zeros, subnormals and finite values above full scale survive: no clipping or normalization.
    assert np.signbit(decoded).tolist() == np.signbit(samples).tolist()
    assert np.max(np.abs(decoded)) == np.max(np.abs(samples))


def test_encoding_uses_explicit_wav_double_parameters_on_a_memory_buffer(monkeypatch):
    calls = []

    def spy(file, mode="r", **kwargs):
        keys = ("samplerate", "channels", "format", "subtype")
        calls.append((type(file), mode, *(kwargs.get(k) for k in keys)))
        return _REAL.soundfile(file, mode, **kwargs)

    monkeypatch.setattr(sf, "SoundFile", spy)
    _prepare(SPECIAL)
    assert calls[0] == (io.BytesIO, "w", 48000, 2, "WAV", "DOUBLE")
    assert all(call[0] is io.BytesIO for call in calls) and len(calls) == 2  # encode, then decode check


def test_decoded_size_limit_has_an_exact_boundary():
    exact = SPECIAL.size * 8
    _prepare(SPECIAL, max_decoded_bytes=exact)
    with pytest.raises(WavOutputError) as info:
        _prepare(SPECIAL, max_decoded_bytes=exact - 1)
    assert info.value.code is Code.RESOURCE_LIMIT and info.value.created_file is None


def test_decoded_size_limit_precedes_copying_and_full_sample_scans():
    # A zero-stride view of 2**31 stereo frames occupies 16 bytes but would need 32 GiB if copied
    # (and 4 GiB for a finiteness mask); the limit must reject it from its shape alone.
    huge = np.broadcast_to(np.zeros((1, 2)), (2**31, 2))
    assert type(huge) is np.ndarray and huge.nbytes == 2**31 * 16
    with pytest.raises(WavOutputError) as info:
        _prepare(huge, max_decoded_bytes=1 << 20)
    assert info.value.code is Code.RESOURCE_LIMIT


def test_nonfinite_check_follows_the_decoded_size_limit():
    with pytest.raises(WavOutputError) as info:
        _prepare(np.array([[np.nan, 0.0]]), max_decoded_bytes=15)
    assert info.value.code is Code.RESOURCE_LIMIT


def test_encoded_size_limit_has_an_exact_boundary():
    _snapshot, payload = _prepare(SPECIAL)
    _prepare(SPECIAL, max_file_bytes=len(payload))
    with pytest.raises(WavOutputError) as info:
        _prepare(SPECIAL, max_file_bytes=len(payload) - 1)
    assert info.value.code is Code.RESOURCE_LIMIT and info.value.created_file is None


@pytest.mark.parametrize("limit", [sys.maxsize, 10**100], ids=["maxsize", "googol"])
def test_huge_valid_limits_are_accepted_on_tiny_data(limit):
    snapshot, payload = _prepare(MONO, max_file_bytes=limit, max_decoded_bytes=limit)
    assert _same_bits(_decode(payload)[1], MONO)


@pytest.mark.parametrize(
    "length, ok",
    [(12, True), (0xFFFFFFFF + 8, True), (0xFFFFFFFF + 9, False), (2**40, False), (11, False)],
)
def test_riff_chunk_size_guard_is_numeric(length, ok):
    assert (writing._riff_size_problem(length) is None) is ok


def test_riff_representation_failure_is_an_encoding_failure_not_a_format_switch(monkeypatch):
    _snapshot, payload = _prepare(MONO)
    monkeypatch.setattr(writing, "_RIFF_CHUNK_SIZE_MAX", len(payload) - 9)
    with pytest.raises(WavOutputError) as info:
        _prepare(MONO)
    assert info.value.code is Code.ENCODING_FAILED and "RIFF" in str(info.value)


class _EncoderDouble:
    """Wraps the real ``SoundFile``; in write mode it alters the parameters or samples, or fails."""

    def __init__(self, monkeypatch, *, subtype=None, format=None, rate=None, samples=None, fail=None):
        def factory(file, mode="r", **kwargs):
            if mode != "w":
                return _REAL.soundfile(file, mode, **kwargs)
            if fail is not None:
                raise fail
            for key, value in (("subtype", subtype), ("format", format), ("samplerate", rate)):
                if value is not None:
                    kwargs[key] = value
            handle = _REAL.soundfile(file, mode, **kwargs)
            if samples is not None:
                real_write = handle.write
                handle.write = lambda data: real_write(samples(data))
            return handle

        monkeypatch.setattr(sf, "SoundFile", factory)


@pytest.mark.parametrize(
    "change",
    [
        {"subtype": "FLOAT"},
        {"subtype": "PCM_24"},
        {"format": "WAVEX"},
        {"format": "RF64"},
        {"rate": 44100},
        {"samples": lambda data: data * 0.5},
        {"samples": lambda data: np.abs(data)},
        {"samples": lambda data: data[:-1]},
    ],
    ids=["float32", "pcm24", "wavex", "rf64", "rate", "scaled", "signed-zero-lost", "short"],
)
def test_prewrite_encode_validation_mismatch_is_an_encoding_failure(monkeypatch, change):
    _EncoderDouble(monkeypatch, **change)
    with pytest.raises(WavOutputError) as info:
        _prepare(SPECIAL)
    assert info.value.code is Code.ENCODING_FAILED and info.value.created_file is None


def test_libsndfile_error_during_encoding_is_an_encoding_failure(monkeypatch):
    exc = sf.LibsndfileError(1, "injected")
    _EncoderDouble(monkeypatch, fail=exc)
    with pytest.raises(WavOutputError) as info:
        _prepare(SPECIAL)
    assert info.value.code is Code.ENCODING_FAILED and info.value.__cause__ is exc


@pytest.mark.parametrize(
    "exc", [MemoryError(), RuntimeError("not libsndfile"), TypeError("bug"), OSError(errno.EIO, "io")], ids=_id
)
def test_unexpected_encoder_errors_propagate_unchanged(monkeypatch, exc):
    _EncoderDouble(monkeypatch, fail=exc)
    with pytest.raises(type(exc)) as info:
        _prepare(SPECIAL)
    assert info.value is exc


# --- Task 3: native fixtures and controlled stream doubles ---------------------------------------


@pytest.fixture
def tree(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    for name in ("stem-t1.wav", "mix.wav", "base-t2.wav"):
        (sources / name).write_bytes(b"fixture")
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "inside.txt").write_bytes(b"target")
    return tmp_path


@pytest.fixture
def allocated(tree) -> AllocatedWorkspace:
    protected = tuple(resolve_artifact_location(a, root=str(tree / "sources")) for a in (STEM_T1, MIX, BASE_T2))
    request = WorkspaceAllocationRequest(
        ExecutionWorkspace(ExecutionId("e1"), ROOT_KEY, TOKEN),
        WorkspaceRoot(ROOT_KEY, str(tree / "outputs")),
        _state(),
        protected,
    )
    return allocate_workspace(request)


def _write(workspace, filename="target.wav", samples=SPECIAL, *, render=None, **limits):
    kwargs = {"max_file_bytes": BIG, "max_decoded_bytes": BIG, **limits}
    return write_wav_output(
        workspace,
        filename,
        samples,
        render_spec=render if render is not None else _render(channels=samples.shape[1]),
        processing_spec=PROCESSING,
        **kwargs,
    )


def _fail(code, *args, **kwargs) -> WavOutputError:
    with pytest.raises(WavOutputError) as info:
        _write(*args, **kwargs)
    assert info.value.code is code, info.value
    assert str(info.value).startswith(f"{code.value}: ")
    return info.value


def _file_bytes(path) -> bytes:
    with _REAL.open(path, "rb") as handle:
        return handle.read()


def _snapshot(base: Path) -> dict:
    """Entry type, link target, regular-file bytes and mtimes below ``base``, not following links."""
    entries = {}
    pending = [base]
    while pending:
        with os.scandir(pending.pop()) as listing:
            names = sorted(entry.path for entry in listing)
        for path in names:
            st = _REAL.lstat(path)
            if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
                entries[path] = ("link", os.readlink(path))
            elif stat.S_ISDIR(st.st_mode):
                entries[path] = ("dir",)
                pending.append(Path(path))
            else:
                entries[path] = ("file", _file_bytes(path), st.st_mtime_ns)
    return entries


class _Stream:
    """Delegates to the real created handle; optional hooks replace one operation.

    Each hook receives the real handle and the call's arguments. Recorded events let the tests check
    request sizes, ordering and closure; the real handle still determines what reaches the disk.
    """

    def __init__(self, real, hooks):
        self.real, self.hooks, self.events = real, hooks, []

    def _call(self, name, *args):
        self.events.append((name, *[len(a) if isinstance(a, (bytes, memoryview)) else a for a in args]))
        hook = self.hooks.get(name)
        return hook(self.real, *args) if hook else getattr(self.real, name)(*args)

    def write(self, data):
        return self._call("write", data)

    def flush(self):
        return self._call("flush")

    def seek(self, offset, whence=0):
        return self._call("seek", offset, whence)

    def read(self, size=-1):
        return self._call("read", size)

    def close(self):
        return self._call("close")

    @property
    def closed(self):
        return self.real.closed


class _Opens:
    """Spy on every content open of a path inside ``directory``; real opens are wrapped in ``_Stream``."""

    def __init__(self, monkeypatch, directory, hooks=None, fail=None, on_open=None):
        self.calls, self.streams, self.os_calls = [], [], []
        directory = os.path.normcase(str(directory))

        def inside(path) -> bool:
            try:
                return os.path.normcase(os.fspath(path)).startswith(directory)
            except TypeError:
                return False

        def fake_open(file, mode="r", *args, **kwargs):
            if not inside(file):
                return _REAL.open(file, mode, *args, **kwargs)
            self.calls.append((os.fspath(file), mode, args, kwargs))
            if fail is not None:
                raise fail
            stream = _Stream(_REAL.open(file, mode, *args, **kwargs), hooks or {})
            self.streams.append(stream)
            if on_open is not None:
                on_open()
            return stream

        real_os_open = os.open

        def fake_os_open(path, *args, **kwargs):
            if inside(path):
                self.os_calls.append(os.fspath(path))
            return real_os_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", fake_open)
        monkeypatch.setattr(io, "open", fake_open)
        monkeypatch.setattr(os, "open", fake_os_open)


DESTRUCTIVE = [
    (os, "remove"),
    (os, "unlink"),
    (os, "rmdir"),
    (os, "removedirs"),
    (os, "rename"),
    (os, "renames"),
    (os, "replace"),
    (os, "truncate"),
    (os, "makedirs"),
    (shutil, "rmtree"),
    (shutil, "move"),
    (Path, "unlink"),
    (Path, "rmdir"),
    (Path, "rename"),
    (Path, "replace"),
    (Path, "touch"),
    (Path, "write_bytes"),
]


def _symlink(target, link, *, directory):
    try:
        _REAL.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"host cannot create symbolic links: {exc}")


def _junction(target, link):
    import _winapi

    try:
        _winapi.CreateJunction(str(target), str(link))
    except OSError as exc:
        pytest.skip(f"host cannot create a directory junction: {exc}")


def _target(allocated, filename="target.wav") -> str:
    return str(Path(allocated.directory) / filename)


# --- Task 3: real successful writes ------------------------------------------------------------------


@pytest.mark.parametrize(
    "samples", [SPECIAL, MONO, SPECIAL[:1], MONO[:1]], ids=["stereo", "mono", "stereo-1", "mono-1"]
)
def test_real_write_creates_a_verified_wav_double_file(allocated, samples):
    before = samples.copy()
    render = _render(channels=samples.shape[1])
    result = write_wav_output(
        allocated,
        "target.wav",
        samples,
        render_spec=render,
        processing_spec=PROCESSING,
        max_file_bytes=BIG,
        max_decoded_bytes=BIG,
    )
    data = _file_bytes(result.path)
    assert isinstance(result, WrittenWav) and result.path == _target(allocated)
    assert result.workspace is allocated and result.render_spec is render and result.processing_spec is PROCESSING
    assert result.sha256 == hashlib.sha256(data).hexdigest() and result.byte_count == len(data)
    assert (result.container, result.subtype) == ("WAV", "DOUBLE")
    assert (result.frame_count, result.channel_count, result.sample_rate_hz) == (*samples.shape, 48000)
    info = sf.info(result.path)  # independent inspection of the real file on disk
    found = (info.format, info.subtype, info.samplerate, info.channels, info.frames)
    assert found == ("WAV", "DOUBLE", 48000, samples.shape[1], samples.shape[0])
    assert _same_bits(_decode(data)[1], samples) and _same_bits(samples, before)
    assert os.listdir(allocated.directory) == ["target.wav"]


def test_real_write_preserves_signed_zero_subnormal_and_overrange_values(allocated):
    result = _write(allocated)
    decoded = _decode(_file_bytes(result.path))[1]
    assert np.signbit(decoded[0]).tolist() == [False, True]
    assert decoded[1].tolist() == [TINY, -TINY] and decoded[4, 1] == -1.0e300 and decoded[4, 0] == 3.75


def test_protocol_order_one_exclusive_open_and_memory_only_soundfile(allocated, monkeypatch):
    events = []
    real_validate = writing.validate_allocated_workspace

    def validate(workspace):
        events.append("validate")
        return real_validate(workspace)

    def soundfile(file, mode="r", **kwargs):
        events.append(f"soundfile-{mode}")
        assert isinstance(file, io.BytesIO), file  # never a pathname or file handle
        return _REAL.soundfile(file, mode, **kwargs)

    monkeypatch.setattr(writing, "validate_allocated_workspace", validate)
    monkeypatch.setattr(sf, "SoundFile", soundfile)
    opens = _Opens(monkeypatch, allocated.directory, on_open=lambda: events.append("open"))
    result = _write(allocated)
    monkeypatch.undo()
    assert opens.calls == [(_target(allocated), "x+b", (), {"buffering": 0, "opener": writing._OPENER})]
    assert opens.os_calls == []
    assert events == ["soundfile-w", "soundfile-r", "validate", "open", "soundfile-r", "validate"]
    names = [event[0] for event in opens.streams[0].events]
    assert names[-1] == "close" and names.count("close") == 1 and "write" in names
    assert names.index("flush") < names.index("seek") < names.index("read")
    assert opens.streams[0].closed and result.byte_count == len(_file_bytes(result.path))


def test_hash_is_computed_from_the_captured_readback_not_the_encoding_buffer(allocated, monkeypatch):
    payloads, hashed = [], []
    real_encode, real_sha256 = writing._encode, hashlib.sha256

    def encode(*args, **kwargs):
        payloads.append(real_encode(*args, **kwargs))
        return payloads[-1]

    def sha256(data=b""):
        hashed.append(data)
        return real_sha256(data)

    monkeypatch.setattr(writing, "_encode", encode)
    monkeypatch.setattr(hashlib, "sha256", sha256)
    result = _write(allocated)
    monkeypatch.undo()
    assert len(payloads) == 1 and len(hashed) == 1
    assert hashed[0] is not payloads[0] and hashed[0] == _file_bytes(result.path)


def test_captured_bytes_are_decoded_again_through_memory(allocated, monkeypatch):
    decoded = []

    def soundfile(file, mode="r", **kwargs):
        if mode == "r":
            decoded.append(file.getvalue())
        return _REAL.soundfile(file, mode, **kwargs)

    monkeypatch.setattr(sf, "SoundFile", soundfile)
    result = _write(allocated)
    monkeypatch.undo()
    assert len(decoded) == 2 and decoded[1] == _file_bytes(result.path)


def test_payload_larger_than_one_chunk_uses_bounded_requests(allocated, monkeypatch):
    frames = (1 << 20) // 16 + 5000  # stereo float64 payload larger than one 1 MiB request
    samples = np.linspace(-2.0, 2.0, frames * 2, dtype=np.float64).reshape(frames, 2)
    opens = _Opens(monkeypatch, allocated.directory)
    result = _write(allocated, samples=samples, max_file_bytes=10**100, max_decoded_bytes=10**100)
    monkeypatch.undo()
    events = opens.streams[0].events
    writes = [e[1] for e in events if e[0] == "write"]
    reads = [e[1] for e in events if e[0] == "read"]
    assert len(writes) >= 2 and max(writes) <= 1 << 20 and sum(writes) == result.byte_count
    assert reads and max(reads) <= 1 << 20
    assert _same_bits(_decode(_file_bytes(result.path))[1], samples)


@pytest.mark.parametrize("limit", [sys.maxsize, 10**100], ids=["maxsize", "googol"])
def test_huge_valid_limits_never_become_oversized_requests(allocated, monkeypatch, limit):
    opens = _Opens(monkeypatch, allocated.directory)
    _write(allocated, max_file_bytes=limit, max_decoded_bytes=limit)
    monkeypatch.undo()
    sizes = [e[1] for e in opens.streams[0].events if e[0] in ("read", "write")]
    assert sizes and max(sizes) <= 1 << 20


def test_exact_file_size_limit_succeeds_and_one_byte_less_creates_nothing(allocated):
    first = _write(allocated, "first.wav")
    _write(allocated, "exact.wav", max_file_bytes=first.byte_count)
    error = _fail(Code.RESOURCE_LIMIT, allocated, "small.wav", max_file_bytes=first.byte_count - 1)
    assert error.created_file is None and not Path(_target(allocated, "small.wav")).exists()


def test_second_distinct_filename_succeeds_in_the_same_workspace(allocated):
    first = _write(allocated, "target.wav", SPECIAL)
    first_bytes = _file_bytes(first.path)
    second = _write(allocated, "mix.wav", SPECIAL[::-1].copy())
    assert sorted(os.listdir(allocated.directory)) == ["mix.wav", "target.wav"]
    assert _file_bytes(first.path) == first_bytes and second.path == _target(allocated, "mix.wav")


def test_repeated_filename_collides_without_modifying_the_existing_file(allocated, tree, monkeypatch):
    first = _write(allocated)
    before = _snapshot(tree)
    destructive = _deny(monkeypatch, DESTRUCTIVE)
    error = _fail(Code.COLLISION, allocated, samples=MONO)
    monkeypatch.undo()
    assert isinstance(error.__cause__, FileExistsError) and error.created_file is None
    assert destructive == [] and _snapshot(tree) == before
    assert hashlib.sha256(_file_bytes(first.path)).hexdigest() == first.sha256


def test_write_does_not_depend_on_the_current_directory(allocated, tree, monkeypatch):
    monkeypatch.chdir(tree / "outside")
    result = _write(allocated)
    assert Path(result.path).parent == Path(allocated.directory)
    assert not (tree / "outside" / "target.wav").exists()


# --- Task 3: rejections before creation -------------------------------------------------------------


def test_invalid_arguments_create_and_open_nothing(allocated, monkeypatch):
    opens = _Opens(monkeypatch, allocated.directory)
    _plain_error(_write, allocated, "../escape.wav")
    _plain_error(_write, allocated, samples=_ORDINARY.astype(np.float32))
    _plain_error(_write, allocated, max_file_bytes=True)
    _plain_error(_write, allocated, render=_render(rate=2**31, channels=2))
    monkeypatch.undo()
    assert opens.calls == [] and os.listdir(allocated.directory) == []


@pytest.mark.parametrize("failure", ["decoded-limit", "file-limit", "encoder", "mismatch"])
def test_encoding_failures_and_limits_leave_no_destination(allocated, monkeypatch, failure):
    opens = _Opens(monkeypatch, allocated.directory)
    validations = []
    monkeypatch.setattr(writing, "validate_allocated_workspace", lambda w: validations.append(w))
    limits, code = {}, Code.RESOURCE_LIMIT
    if failure == "decoded-limit":
        limits = {"max_decoded_bytes": SPECIAL.size * 8 - 1}
    elif failure == "file-limit":
        limits = {"max_file_bytes": 100}
    elif failure == "encoder":
        _EncoderDouble(monkeypatch, fail=sf.LibsndfileError(1, "injected"))
        code = Code.ENCODING_FAILED
    else:
        _EncoderDouble(monkeypatch, subtype="FLOAT")
        code = Code.ENCODING_FAILED
    error = _fail(code, allocated, **limits)
    monkeypatch.undo()
    assert error.created_file is None and opens.calls == [] and validations == []
    assert os.listdir(allocated.directory) == []


def test_workspace_rejected_before_open_creates_nothing(allocated, tree, monkeypatch):
    (tree / "sources" / "mix.wav").unlink()  # real change to the protected inventory
    opens = _Opens(monkeypatch, allocated.directory)
    error = _fail(Code.WORKSPACE_REJECTED, allocated)
    monkeypatch.undo()
    assert isinstance(error.__cause__, WorkspaceAllocationError) and error.created_file is None
    assert error.__cause__.code is WorkspaceAllocationFailureCode.PROTECTED_LOCATION
    assert opens.calls == [] and os.listdir(allocated.directory) == []


def test_forged_record_cannot_direct_output_outside_the_assigned_child(allocated, tree, monkeypatch):
    forged = dataclasses.replace(allocated, directory=str(tree / "outside"))
    opens = _Opens(monkeypatch, tree)
    error = _fail(Code.WORKSPACE_REJECTED, forged)
    monkeypatch.undo()
    assert error.__cause__.code is WorkspaceAllocationFailureCode.WORKSPACE_CHANGED
    assert opens.calls == [] and sorted(os.listdir(tree / "outside")) == ["inside.txt"]


def test_manually_constructed_equivalent_record_is_accepted_as_inspected(allocated):
    manual = AllocatedWorkspace(allocated.request, allocated.resolved_root, allocated.directory)
    assert _write(manual).workspace is manual  # descriptive record, not a capability


def test_real_child_redirected_by_a_symlink_is_rejected_before_open(allocated, tree):
    os.rmdir(allocated.directory)
    _symlink(tree / "outside", allocated.directory, directory=True)
    error = _fail(Code.WORKSPACE_REJECTED, allocated)
    assert error.__cause__.code is WorkspaceAllocationFailureCode.WORKSPACE_CHANGED
    assert os.listdir(tree / "outside") == ["inside.txt"]


# --- Task 3: real collisions --------------------------------------------------------------------------


def _make_collision(tree, allocated, kind):
    child, outside = Path(_target(allocated)), tree / "outside"
    if kind == "file":
        child.write_bytes(b"existing")
    elif kind == "empty-dir":
        child.mkdir()
    elif kind == "nonempty-dir":
        child.mkdir()
        (child / "old.txt").write_bytes(b"old")
    elif kind == "file-symlink":
        _symlink(outside / "inside.txt", child, directory=False)
    elif kind == "dir-symlink":
        _symlink(outside, child, directory=True)
    elif kind == "dangling-file-symlink":
        _symlink(outside / "missing.txt", child, directory=False)
    elif kind == "dangling-dir-symlink":
        _symlink(outside / "missing", child, directory=True)
    elif kind == "junction":
        _junction(outside, child)
    elif kind == "dangling-junction":
        (tree / "gone").mkdir()
        _junction(tree / "gone", child)
        _REAL.rmdir(tree / "gone")


_JUNCTIONS_ONLY = pytest.mark.skipif(not WINDOWS, reason="junctions exist only on Windows")
COLLISION_KINDS = [
    "file",
    "empty-dir",
    "nonempty-dir",
    "file-symlink",
    "dir-symlink",
    "dangling-file-symlink",
    "dangling-dir-symlink",
    pytest.param("junction", marks=_JUNCTIONS_ONLY),
    pytest.param("dangling-junction", marks=_JUNCTIONS_ONLY),
]


@pytest.mark.parametrize("kind", COLLISION_KINDS)
def test_every_existing_entry_form_is_a_collision_and_stays_untouched(allocated, tree, monkeypatch, kind):
    _make_collision(tree, allocated, kind)
    before = _snapshot(tree)
    destructive = _deny(monkeypatch, DESTRUCTIVE)
    opens = _Opens(monkeypatch, allocated.directory)
    error = _fail(Code.COLLISION, allocated)
    monkeypatch.undo()
    assert error.created_file is None and isinstance(error.__cause__, OSError)
    # The collision comes from the one exclusive open itself, not from an existence pre-check.
    assert [(c[0], c[1]) for c in opens.calls] == [(_target(allocated), "x+b")] and opens.streams == []
    assert destructive == [] and _snapshot(tree) == before


DANGLING_KINDS = [
    ("dangling-file-symlink", "missing.txt"),
    ("dangling-dir-symlink", "missing"),
]


@pytest.mark.parametrize("kind, missing", DANGLING_KINDS, ids=[k for k, _ in DANGLING_KINDS])
def test_real_dangling_link_is_not_followed_and_its_target_is_not_created(allocated, tree, kind, missing):
    _make_collision(tree, allocated, kind)
    link = _target(allocated)
    target_before = os.readlink(link)
    error = _fail(Code.COLLISION, allocated)
    assert error.created_file is None
    assert not os.path.lexists(tree / "outside" / missing)  # nothing created outside the workspace
    assert sorted(os.listdir(tree / "outside")) == ["inside.txt"]
    assert os.path.islink(link) and os.readlink(link) == target_before  # the link itself is unchanged


@pytest.mark.skipif(not WINDOWS, reason="directory junctions exist only on Windows")
def test_real_dangling_junction_is_not_followed_and_its_target_is_not_created(allocated, tree):
    _make_collision(tree, allocated, "dangling-junction")
    link = _target(allocated)
    target_before = os.readlink(link)
    _fail(Code.COLLISION, allocated)
    assert not os.path.lexists(tree / "gone") and os.readlink(link) == target_before


# --- Task 3: Windows no-follow opener --------------------------------------------------------------------

_WINDOWS_ONLY = pytest.mark.skipif(not WINDOWS, reason="the no-follow CreateFileW opener is used only on Windows")
_X_FLAGS = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)


@pytest.mark.skipif(WINDOWS, reason="POSIX keeps the default O_CREAT | O_EXCL opener")
def test_posix_uses_the_default_opener():
    assert writing._OPENER is None


@_WINDOWS_ONLY
def test_real_windows_opener_descriptor_is_binary_read_write_and_not_inheritable(tmp_path):
    path = str(tmp_path / "probe.bin")
    fd = writing._OPENER(path, _X_FLAGS)
    try:
        assert os.get_inheritable(fd) is False
        payload = b"\r\n\x1a\n\r\x00"  # would be altered by text-mode translation or an EOF marker
        assert os.write(fd, payload) == len(payload)
        os.lseek(fd, 0, os.SEEK_SET)
        assert os.read(fd, 64) == payload
    finally:
        os.close(fd)
    assert _file_bytes(path) == payload


@_WINDOWS_ONLY
def test_real_windows_opener_reports_existing_entries_and_missing_parents(tmp_path):
    (tmp_path / "file").write_bytes(b"keep")
    (tmp_path / "dir").mkdir()
    with pytest.raises(FileExistsError):
        writing._OPENER(str(tmp_path / "file"), _X_FLAGS)
    with pytest.raises(PermissionError):
        writing._OPENER(str(tmp_path / "dir"), _X_FLAGS)
    with pytest.raises(FileNotFoundError):
        writing._OPENER(str(tmp_path / "missing" / "x.wav"), _X_FLAGS)
    assert _file_bytes(tmp_path / "file") == b"keep"


@_WINDOWS_ONLY
def test_windows_opener_requires_exclusive_creation_flags(tmp_path):
    with pytest.raises(ValueError):
        writing._OPENER(str(tmp_path / "x.wav"), os.O_RDWR | os.O_CREAT)
    assert not (tmp_path / "x.wav").exists()


@_WINDOWS_ONLY
@pytest.mark.parametrize(
    "code, expected",
    [
        (80, FileExistsError),
        (183, FileExistsError),
        (5, PermissionError),
        (3, FileNotFoundError),
        (2, FileNotFoundError),
    ],
    ids=["file-exists", "already-exists", "access-denied", "path-not-found", "file-not-found"],
)
def test_win32_errors_translate_to_the_default_openers_exception_classes(code, expected):
    error = writing._win_error(code, "C:\\x.wav")
    assert type(error) is expected and error.winerror == code and error.filename == "C:\\x.wav"


@_WINDOWS_ONLY
def test_handle_is_closed_when_attaching_a_descriptor_fails(allocated, monkeypatch):
    # Controlled: CreateFileW really creates the file; only open_osfhandle is made to fail.
    failure = OSError(errno.EMFILE, "too many open files")
    closed, real_close = [], writing._close_handle

    def fail(handle, flags):
        raise failure

    def close(handle):
        closed.append(handle)
        real_close(handle)

    monkeypatch.setattr(writing.msvcrt, "open_osfhandle", fail)
    monkeypatch.setattr(writing, "_close_handle", close)
    destructive = _deny(monkeypatch, DESTRUCTIVE)
    with pytest.raises(OSError) as info:
        _write(allocated)
    monkeypatch.undo()
    assert info.value is failure and not isinstance(info.value, WavOutputError)
    assert len(closed) == 1 and destructive == []
    assert os.path.isfile(_target(allocated)) and _file_bytes(_target(allocated)) == b""  # retained, empty
    os.remove(_target(allocated))  # the handle is closed, so the test's own cleanup can remove it


# Injected: open_osfhandle and the raw-handle close are controlled doubles; CreateFileW is real (counted)
# and really creates the file, so retention is observed on disk. The failure-reporting close double
# still closes the real handle first, so no handle leaks into later tests.
ATTACH_PRIMARIES = [
    pytest.param(lambda: MemoryError(), id="MemoryError"),
    pytest.param(lambda: OSError(errno.EIO, "unexpected I/O"), id="OSError-EIO"),
    pytest.param(lambda: TypeError("programming error"), id="TypeError"),
]


class _AttachFailure:
    """Installs failing ``open_osfhandle``, a recording raw-handle close and a counting ``CreateFileW``."""

    def __init__(self, monkeypatch, primary, *, close_fails):
        self.primary, self.created, self.closed, self.fd_closes = primary, [], [], []
        self.secondary = OSError(errno.EIO, "CloseHandle failed") if close_fails else None
        real_create, real_close, real_os_close = writing._CreateFileW, writing._close_handle, os.close

        def create(*args):
            handle = real_create(*args)
            self.created.append(handle)
            return handle

        def attach(handle, flags):
            raise primary

        def close(handle):
            self.closed.append(handle)
            real_close(handle)  # the real handle is released either way
            if self.secondary is not None:
                raise self.secondary

        def os_close(fd):
            self.fd_closes.append(fd)
            return real_os_close(fd)

        monkeypatch.setattr(writing, "_CreateFileW", create)
        monkeypatch.setattr(writing.msvcrt, "open_osfhandle", attach)
        monkeypatch.setattr(writing, "_close_handle", close)
        monkeypatch.setattr(os, "close", os_close)
        self.destructive = _deny(monkeypatch, DESTRUCTIVE)

    def check(self, raised, path):
        assert raised is self.primary  # identity: neither replaced nor wrapped by the close failure
        assert not isinstance(raised, WavOutputError) and not getattr(raised, "__notes__", None)
        assert len(self.created) == 1  # one creation, no retry
        assert self.closed == self.created  # the owned raw handle is closed exactly once
        assert self.fd_closes == [] and self.destructive == []  # no descriptor exists; nothing deleted
        assert os.path.isfile(path) and _file_bytes(path) == b""  # created file retained (empty)


@_WINDOWS_ONLY
@pytest.mark.parametrize("close_fails", [False, True], ids=["close-succeeds", "close-fails"])
@pytest.mark.parametrize("make_primary", ATTACH_PRIMARIES)
def test_opener_preserves_the_attachment_failure_whether_or_not_the_handle_close_fails(
    tmp_path, monkeypatch, make_primary, close_fails
):
    path = str(tmp_path / "x.wav")
    double = _AttachFailure(monkeypatch, make_primary(), close_fails=close_fails)
    with pytest.raises(BaseException) as info:
        writing._OPENER(path, _X_FLAGS)
    monkeypatch.undo()
    double.check(info.value, path)


@_WINDOWS_ONLY
@pytest.mark.parametrize("close_fails", [False, True], ids=["close-succeeds", "close-fails"])
@pytest.mark.parametrize("make_primary", ATTACH_PRIMARIES)
def test_public_write_propagates_the_original_attachment_failure_unchanged(
    allocated, monkeypatch, make_primary, close_fails
):
    double = _AttachFailure(monkeypatch, make_primary(), close_fails=close_fails)
    with pytest.raises(BaseException) as info:
        _write(allocated)
    monkeypatch.undo()
    double.check(info.value, _target(allocated))
    # No structured created_file exists for unexpected failures before open returns: the empty file
    # created by CreateFileW is retained and only observable on disk.
    assert os.listdir(allocated.directory) == ["target.wav"]


# --- Task 3: open-error mapping (controlled doubles) ---------------------------------------------------


def _lstat_says(monkeypatch, path, *, exists=None, exc=None):
    calls = []

    def fake(target, *args, **kwargs):
        if os.fspath(target) == path:
            calls.append(target)
            if exc is not None:
                raise exc
            if exists:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755)
            raise FileNotFoundError(errno.ENOENT, "absent")
        return _REAL.lstat(target, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", fake)
    return calls


OPEN_MAPPING = [
    ("permission-existing", PermissionError(errno.EACCES, "denied"), True, Code.COLLISION),
    ("permission-absent", PermissionError(errno.EACCES, "denied"), False, Code.NOT_WRITABLE),
    ("is-a-directory-existing", IsADirectoryError(errno.EISDIR, "directory"), True, Code.COLLISION),
    ("exists", FileExistsError(errno.EEXIST, "exists"), None, Code.COLLISION),
    ("parent-missing", FileNotFoundError(errno.ENOENT, "parent gone"), None, Code.WORKSPACE_CHANGED),
    ("parent-not-directory", NotADirectoryError(errno.ENOTDIR, "replaced"), None, Code.WORKSPACE_CHANGED),
    ("link-cycle", OSError(errno.ELOOP, "loop"), None, Code.WORKSPACE_CHANGED),
]


@pytest.mark.parametrize("exc, exists, code", [m[1:] for m in OPEN_MAPPING], ids=[m[0] for m in OPEN_MAPPING])
def test_open_errors_map_narrowly_without_a_created_file(allocated, monkeypatch, exc, exists, code):
    opens = _Opens(monkeypatch, allocated.directory, fail=exc)
    lstat_calls = _lstat_says(monkeypatch, _target(allocated), exists=exists)
    error = _fail(code, allocated)
    monkeypatch.undo()
    assert error.__cause__ is exc and error.created_file is None and len(opens.calls) == 1
    assert bool(lstat_calls) is (exists is not None)
    assert os.listdir(allocated.directory) == []


def test_unconfirmed_is_a_directory_error_propagates(allocated, monkeypatch):
    exc = IsADirectoryError(errno.EISDIR, "directory")
    _Opens(monkeypatch, allocated.directory, fail=exc)
    _lstat_says(monkeypatch, _target(allocated), exists=False)
    with pytest.raises(IsADirectoryError) as info:
        _write(allocated)
    assert info.value is exc


def test_collision_inspection_failure_does_not_invent_a_collision(allocated, monkeypatch):
    exc = PermissionError(errno.EACCES, "denied")
    _Opens(monkeypatch, allocated.directory, fail=exc)
    _lstat_says(monkeypatch, _target(allocated), exc=OSError(errno.EIO, "io"))
    error = _fail(Code.NOT_WRITABLE, allocated)
    assert error.__cause__ is exc


@pytest.mark.parametrize(
    "exc", [OSError(errno.EIO, "io"), OSError(errno.ENOSPC, "full"), MemoryError(), TypeError("bug")], ids=_id
)
def test_unexpected_open_errors_propagate_unchanged(allocated, monkeypatch, exc):
    _Opens(monkeypatch, allocated.directory, fail=exc)
    with pytest.raises(type(exc)) as info:
        _write(allocated)
    assert info.value is exc


# --- Task 3: concurrency ---------------------------------------------------------------------------------


def _race(calls):
    barrier = threading.Barrier(len(calls), timeout=30)
    outcomes = [None] * len(calls)

    def run(index):
        barrier.wait()
        try:
            outcomes[index] = calls[index]()
        except BaseException as exc:  # recorded and asserted below, never swallowed
            outcomes[index] = exc

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "writer thread did not finish"
    return outcomes


def test_same_name_concurrent_writers_yield_one_file_and_one_collision(allocated):
    # Repeated barrier-started rounds; this tests exclusive file creation, not an execution lock.
    names = [f"race-{i}.wav" for i in range(10)]
    for name in names:
        outcomes = _race([lambda n=name: _write(allocated, n), lambda n=name: _write(allocated, n, MONO[:, :1])])
        written = [o for o in outcomes if isinstance(o, WrittenWav)]
        failed = [o for o in outcomes if isinstance(o, WavOutputError)]
        assert len(written) == 1 and len(failed) == 1, outcomes
        assert failed[0].code is Code.COLLISION and failed[0].created_file is None
        assert hashlib.sha256(_file_bytes(written[0].path)).hexdigest() == written[0].sha256
    assert sorted(os.listdir(allocated.directory)) == sorted(names)


def test_distinct_names_written_concurrently_both_succeed(allocated):
    outcomes = _race([lambda: _write(allocated, "target.wav"), lambda: _write(allocated, "mix.wav")])
    assert all(isinstance(o, WrittenWav) for o in outcomes), outcomes
    assert sorted(os.listdir(allocated.directory)) == ["mix.wav", "target.wav"]


# --- Task 3: injected I/O failures after creation ---------------------------------------------------------


def _failing_write(allocated, monkeypatch, hooks, expected_type=WavOutputError):
    destructive = _deny(monkeypatch, DESTRUCTIVE)
    opens = _Opens(monkeypatch, allocated.directory, hooks=hooks)
    with pytest.raises(expected_type) as info:
        _write(allocated)
    monkeypatch.undo()
    assert destructive == [] and len(opens.calls) == 1
    stream = opens.streams[0]
    closes = [e for e in stream.events if e[0] == "close"]
    assert len(closes) == 1, stream.events  # the owned handle is closed exactly once on every path
    assert stream.closed and os.path.isfile(_target(allocated))  # the created file is retained
    return info.value, stream


def _io_failed(allocated, monkeypatch, hooks):
    error, stream = _failing_write(allocated, monkeypatch, hooks)
    assert error.code is Code.IO_FAILED and error.created_file == _target(allocated)
    return error, stream


def test_positive_short_writes_are_continued_until_complete(allocated, monkeypatch):
    opens = _Opens(monkeypatch, allocated.directory, hooks={"write": lambda real, data: real.write(data[:7])})
    result = _write(allocated)
    monkeypatch.undo()
    writes = [e for e in opens.streams[0].events if e[0] == "write"]
    assert len(writes) > 2 and result.byte_count == len(_file_bytes(result.path))
    assert _same_bits(_decode(_file_bytes(result.path))[1], SPECIAL)


@pytest.mark.parametrize("value", [0, None, -1, True, 10**9], ids=repr)
def test_invalid_write_counts_are_io_failures_not_retries(allocated, monkeypatch, value):
    calls = []

    def write(real, data):
        calls.append(len(data))
        return real.write(data[:5]) if len(calls) == 1 else value

    _io_failed(allocated, monkeypatch, {"write": write})
    assert len(calls) == 2 and len(_file_bytes(_target(allocated))) == 5  # partial file retained, not accepted


def test_write_error_after_partial_progress_retains_the_partial_file(allocated, monkeypatch):
    full = OSError(errno.ENOSPC, "disk full")
    calls = []

    def write(real, data):
        calls.append(1)
        if len(calls) == 1:
            return real.write(data[:10])
        raise full

    error, _stream = _io_failed(allocated, monkeypatch, {"write": write})
    assert error.__cause__ is full and len(_file_bytes(_target(allocated))) == 10


@pytest.mark.parametrize("operation", ["flush", "seek", "read"])
def test_flush_seek_and_read_errors_are_io_failures_with_a_complete_retained_file(allocated, monkeypatch, operation):
    exc = OSError(errno.EIO, f"{operation} failed")

    def fail(real, *args):
        raise exc

    error, _stream = _io_failed(allocated, monkeypatch, {operation: fail})
    assert error.__cause__ is exc
    assert _same_bits(_decode(_file_bytes(_target(allocated)))[1], SPECIAL)  # complete, still not accepted


def test_read_returning_no_data_indicator_is_an_io_failure(allocated, monkeypatch):
    _io_failed(allocated, monkeypatch, {"read": lambda real, size: None})


def test_read_returning_more_than_requested_is_an_io_failure(allocated, monkeypatch):
    _io_failed(allocated, monkeypatch, {"read": lambda real, size: real.read(size) + b"\x00" * (size + 1)})


def test_seek_landing_elsewhere_is_an_io_failure(allocated, monkeypatch):
    _io_failed(allocated, monkeypatch, {"seek": lambda real, offset, whence: real.seek(4)})


def test_sole_close_failure_is_an_io_failure_with_creation(allocated, monkeypatch):
    exc = OSError(errno.EIO, "close failed")

    def close(real):
        real.close()
        raise exc

    error, _stream = _io_failed(allocated, monkeypatch, {"close": close})
    assert error.__cause__ is exc
    assert _same_bits(_decode(_file_bytes(_target(allocated)))[1], SPECIAL)


def test_primary_failure_is_preserved_when_close_also_fails(allocated, monkeypatch):
    primary, secondary = OSError(errno.EIO, "read failed"), OSError(errno.EIO, "close failed")

    def read(real, size):
        raise primary

    def close(real):
        real.close()
        raise secondary

    error, _stream = _io_failed(allocated, monkeypatch, {"read": read, "close": close})
    assert error.__cause__ is primary and any("close" in note for note in getattr(error, "__notes__", []))


@pytest.mark.parametrize("exc", [MemoryError(), TypeError("bug"), RuntimeError("library")], ids=_id)
def test_unexpected_errors_after_creation_propagate_unchanged_and_close_the_handle(allocated, monkeypatch, exc):
    def read(real, size):
        raise exc

    error, _stream = _failing_write(allocated, monkeypatch, {"read": read}, type(exc))
    assert error is exc and not isinstance(error, WavOutputError)


def test_unexpected_primary_error_survives_a_close_failure(allocated, monkeypatch):
    primary = MemoryError()

    def close(real):
        real.close()
        raise OSError(errno.EIO, "close failed")

    def write(real, data):
        raise primary

    error, _stream = _failing_write(allocated, monkeypatch, {"write": write, "close": close}, MemoryError)
    assert error is primary and not getattr(error, "__notes__", None)


# --- Task 3: captured-content verification ----------------------------------------------------------------


def _verification_failed(allocated, monkeypatch, hooks):
    error, _stream = _failing_write(allocated, monkeypatch, hooks)
    assert error.code is Code.VERIFICATION_FAILED and error.created_file == _target(allocated)
    return error


def test_corrupted_readback_is_rejected_although_the_disk_and_payload_are_intact(allocated, monkeypatch):
    def read(real, size):
        data = real.read(size)
        return data[:-1] + bytes([data[-1] ^ 1]) if data else data

    _verification_failed(allocated, monkeypatch, {"read": read})
    assert _same_bits(_decode(_file_bytes(_target(allocated)))[1], SPECIAL)


def test_truncated_readback_is_rejected(allocated, monkeypatch):
    reads = []

    def read(real, size):
        reads.append(size)
        return real.read(40) if len(reads) == 1 else b""  # early end of data after 40 bytes

    _verification_failed(allocated, monkeypatch, {"read": read})
    assert len(_file_bytes(_target(allocated))) > 40


def test_readback_beyond_the_file_limit_is_a_resource_limit(allocated, monkeypatch):
    limit = _write(allocated, "first.wav").byte_count

    def read(real, size):
        data = real.read(size)
        return data + b"\x00" * (size - len(data)) if data else b""

    destructive = _deny(monkeypatch, DESTRUCTIVE)
    opens = _Opens(monkeypatch, allocated.directory, hooks={"read": read})
    with pytest.raises(WavOutputError) as info:
        _write(allocated, max_file_bytes=limit)
    monkeypatch.undo()
    assert info.value.code is Code.RESOURCE_LIMIT and info.value.created_file == _target(allocated)
    assert destructive == [] and [e[0] for e in opens.streams[0].events].count("close") == 1
    assert os.path.isfile(_target(allocated))


def test_libsndfile_rejection_of_captured_bytes_is_a_verification_failure(allocated, monkeypatch):
    decodes = []

    def soundfile(file, mode="r", **kwargs):
        if mode == "r":
            decodes.append(1)
            if len(decodes) == 2:
                raise sf.LibsndfileError(1, "injected")
        return _REAL.soundfile(file, mode, **kwargs)

    monkeypatch.setattr(sf, "SoundFile", soundfile)
    error = _verification_failed(allocated, monkeypatch, {})
    assert isinstance(error.__cause__, sf.LibsndfileError)


@pytest.mark.parametrize("problem", ["format", "samples"])
def test_captured_metadata_or_sample_mismatch_is_a_verification_failure(allocated, monkeypatch, problem):
    calls = []
    real = writing._payload_problem

    def payload_problem(*args):
        calls.append(1)
        found = real(*args)
        return (f"injected {problem} mismatch", *found[1:]) if len(calls) == 2 else found

    monkeypatch.setattr(writing, "_payload_problem", payload_problem)
    error = _verification_failed(allocated, monkeypatch, {})
    assert problem in str(error)


# --- Task 3: workspace changes after creation ---------------------------------------------------------------


def test_real_protected_change_after_creation_is_a_workspace_change_and_retains_the_file(allocated, tree, monkeypatch):
    protected = tree / "sources" / "base-t2.wav"
    real_remove = os.remove

    def read(real, size):
        if os.path.exists(protected):
            real_remove(protected)  # real filesystem change between the open and the final revalidation
        return real.read(size)

    error, _stream = _failing_write(allocated, monkeypatch, {"read": read})
    assert error.code is Code.WORKSPACE_CHANGED and error.created_file == _target(allocated)
    assert isinstance(error.__cause__, WorkspaceAllocationError)
    assert _same_bits(_decode(_file_bytes(_target(allocated)))[1], SPECIAL)  # complete but not accepted


def test_unexpected_error_from_the_final_revalidation_propagates(allocated, monkeypatch):
    calls, exc = [], OSError(errno.EIO, "io")
    real = writing.validate_allocated_workspace

    def validate(workspace):
        calls.append(1)
        if len(calls) == 2:
            raise exc
        return real(workspace)

    monkeypatch.setattr(writing, "validate_allocated_workspace", validate)
    error, _stream = _failing_write(allocated, monkeypatch, {}, OSError)
    assert error is exc


def test_retry_after_a_retained_failure_collides_instead_of_overwriting(allocated, monkeypatch):
    def read(real, size):
        raise OSError(errno.EIO, "read failed")

    _io_failed(allocated, monkeypatch, {"read": read})
    retained = _file_bytes(_target(allocated))
    retry = _fail(Code.COLLISION, allocated)
    assert retry.created_file is None and _file_bytes(_target(allocated)) == retained
