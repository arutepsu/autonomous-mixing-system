"""Exclusive creation of one WAV/DOUBLE file from already computed float64 samples.

The writer encodes supplied samples; it does not apply gain, sum stems, select actions or build
artifacts or receipts. ``ProcessingSpec.output_container`` WAV maps to soundfile ``format='WAV'`` and
``sample_encoding`` FLOAT64 to ``subtype='DOUBLE'``; other values are rejected, never substituted. The
remaining ``ProcessingSpec`` fields are carried as descriptive configuration only.

Protocol: validate arguments; copy the samples into a private native float64 snapshot; encode it into
memory and check the completed payload (size, RIFF representation, container, subtype, dimensions and
bit-exact samples) before any destination exists; revalidate the workspace; open
``workspace.directory / filename`` exactly once with mode ``'x+b'`` (on Windows through a ``CreateFileW``
opener that does not follow a final link or junction); write the payload in bounded
chunks; flush, seek and read back at most ``max_file_bytes + 1`` bytes through the same handle; require
the captured bytes to equal the payload and decode them again against the snapshot; revalidate the
workspace; close the handle; and only then report the SHA-256 of the captured bytes. soundfile only
ever operates on in-memory buffers.

A created file is retained after every failure: nothing is deleted, renamed, replaced, truncated or
retried. Expected failures after the exclusive open report ``created_file``. The evidence describes
bytes observed through the created handle during this call; it is not durable-storage (fsync)
assurance, atomic publication, a pinned parent directory, race-free confinement or proof that the
pathname keeps these bytes. Limits bound accepted sample storage and payload size, not peak memory of
the interpreter, libsndfile or the encoding buffer, CPU time or disk quota.
"""

import ctypes
import errno
import hashlib
import io
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import Any

import numpy as np
import soundfile as sf

if os.name == "nt":
    import msvcrt
    from ctypes import wintypes

from autonomous_mixing.domain.observation import RenderSpec
from autonomous_mixing.domain.project import OutputContainer, ProcessingSpec, SampleEncoding
from autonomous_mixing.infrastructure.workspaces import (
    AllocatedWorkspace,
    WorkspaceAllocationError,
    validate_allocated_workspace,
)

# Exact single-leaf output names: explicit ASCII classes, lowercase extension, no normalization.
_FILENAME = re.compile(r"[A-Za-z0-9_-]+\.wav")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CHANNEL_COUNTS = (1, 2)
_FLOAT64_BYTES = 8
# libsndfile stores the sample rate in a signed 32-bit int: a library representation limit, not a
# musical threshold or a change to RenderSpec's domain rules.
_MAX_SAMPLE_RATE = 2**31 - 1
# The RIFF chunk-size field is an unsigned 32-bit count of the bytes after the 8-byte RIFF header.
_RIFF_CHUNK_SIZE_MAX = 0xFFFFFFFF
_RIFF_HEADER_BYTES = 12
# I/O request size for bounded write and readback loops; an I/O buffer size, not a scientific threshold.
_IO_CHUNK_BYTES = 1 << 20
# Explicit mapping from the domain's encoding settings to soundfile names; no fallback exists.
_FORMATS = {OutputContainer.WAV: "WAV"}
_SUBTYPES = {SampleEncoding.FLOAT64: "DOUBLE"}


# --- exclusive no-follow creation ---------------------------------------------------------------------
#
# POSIX ``O_CREAT | O_EXCL`` (the default opener for mode 'x') fails on any existing final entry,
# including a dangling symbolic link. The Windows C runtime's equivalent follows a dangling file
# symbolic link and creates its target, possibly outside the workspace. On Windows the single
# ``open(path, 'x+b')`` therefore uses an opener that calls ``CreateFileW`` with ``CREATE_NEW`` and
# ``FILE_FLAG_OPEN_REPARSE_POINT``: an existing final reparse point (symbolic link or junction, dangling
# or not) is itself the existing entry, so creation fails instead of following it. Intermediate
# components are still resolved normally; this does not pin the parent directory.

if os.name == "nt":
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    # Same sharing as the C runtime's default for open(): other readers and writers are not denied.
    _FILE_SHARE_READ_WRITE = 0x00000001 | 0x00000002
    _CREATE_NEW = 1
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    # Descriptor flags matching the default opener for 'x+b': read/write, binary, not inheritable.
    _DESCRIPTOR_FLAGS = os.O_RDWR | os.O_BINARY | os.O_NOINHERIT

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateFileW = _kernel32.CreateFileW
    _CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _CreateFileW.restype = wintypes.HANDLE
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL

    def _win_error(code: int, path: str) -> OSError:
        """Translate a Win32 error code; Python selects the OSError subclass from the mapped errno.

        ERROR_FILE_EXISTS/ERROR_ALREADY_EXISTS become FileExistsError, ERROR_ACCESS_DENIED becomes
        PermissionError and ERROR_PATH_NOT_FOUND becomes FileNotFoundError, as for the default opener.
        """
        return OSError(0, ctypes.FormatError(code).strip(), path, code)

    def _close_handle(handle: int) -> None:
        if not _CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def _exclusive_opener(path: str, flags: int) -> int:
        """Create ``path`` with CREATE_NEW without following a final reparse point; return a C descriptor.

        ``flags`` are those Python computed for mode 'x+b'; they must request exclusive creation. The
        security attributes are NULL, so the handle is not inheritable. If the descriptor cannot be
        attached, the Win32 handle is closed and the error propagates; the created file is kept.
        """
        if not flags & os.O_EXCL:
            raise ValueError("the exclusive opener requires exclusive-creation flags")
        handle = _CreateFileW(
            path,
            _GENERIC_READ | _GENERIC_WRITE,
            _FILE_SHARE_READ_WRITE,
            None,
            _CREATE_NEW,
            _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle is None or handle == _INVALID_HANDLE_VALUE:
            raise _win_error(ctypes.get_last_error(), path)
        try:
            return msvcrt.open_osfhandle(handle, _DESCRIPTOR_FLAGS)
        except BaseException:
            try:
                _close_handle(handle)
            except Exception:
                pass  # the attachment failure stays the reported error; the close is not retried
            raise  # re-raises the original attachment exception object unchanged

    _OPENER = _exclusive_opener
else:
    _OPENER = None  # the default opener: O_CREAT | O_EXCL never follows a final symbolic link


class WavOutputFailureCode(Enum):
    RESOURCE_LIMIT = "resource_limit"
    ENCODING_FAILED = "encoding_failed"
    WORKSPACE_REJECTED = "workspace_rejected"
    COLLISION = "collision"
    NOT_WRITABLE = "not_writable"
    WORKSPACE_CHANGED = "workspace_changed"
    IO_FAILED = "io_failed"
    VERIFICATION_FAILED = "verification_failed"


class WavOutputError(ValueError):
    """An expected output failure; ``code`` names the rejected condition.

    ``created_file`` is the destination path when this call's exclusive open had returned successfully
    before the failure, else ``None``. It reports a known creation only: the file may be empty, partial
    or complete, may since have changed or disappeared, and the field is no authorization to delete it.
    """

    def __init__(self, code: WavOutputFailureCode, message: str, *, created_file: str | None = None) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.created_file = created_file


_Code = WavOutputFailureCode


def _require_instance(value: Any, cls: type, field: str) -> None:
    if not isinstance(value, cls):
        raise ValueError(f"{field} must be a {cls.__name__}, got {type(value).__name__}")


def _require_text(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string, got {value!r}")


def _require_count(value: Any, field: str) -> int:
    # Exact built-in int: rejects bool, NumPy scalars and int subclasses before any comparison.
    if type(value) is not int or value < 1:
        raise ValueError(f"{field} must be a positive built-in int, got {value!r}")
    return value


@dataclass(frozen=True)
class WrittenWav:
    """Description of one file created and verified by ``write_wav_output``.

    This is a descriptive record, not a persisted artifact, success verdict, capability or guarantee
    about later path contents. Construction checks types and structural values without filesystem
    access; only the writer's own operation establishes what was observed.
    """

    workspace: AllocatedWorkspace
    path: str
    sha256: str
    byte_count: int
    frame_count: int
    sample_rate_hz: int
    channel_count: int
    container: str
    subtype: str
    render_spec: RenderSpec
    processing_spec: ProcessingSpec

    def __post_init__(self) -> None:
        _require_instance(self.workspace, AllocatedWorkspace, "WrittenWav.workspace")
        _require_text(self.path, "WrittenWav.path")
        if not isinstance(self.sha256, str) or not _SHA256.fullmatch(self.sha256):
            raise ValueError(f"WrittenWav.sha256 must be 64 lowercase hexadecimal digits, got {self.sha256!r}")
        _require_count(self.byte_count, "WrittenWav.byte_count")
        _require_count(self.frame_count, "WrittenWav.frame_count")
        _require_count(self.sample_rate_hz, "WrittenWav.sample_rate_hz")
        _require_count(self.channel_count, "WrittenWav.channel_count")
        _require_text(self.container, "WrittenWav.container")
        _require_text(self.subtype, "WrittenWav.subtype")
        _require_instance(self.render_spec, RenderSpec, "WrittenWav.render_spec")
        _require_instance(self.processing_spec, ProcessingSpec, "WrittenWav.processing_spec")
        if self.channel_count not in _CHANNEL_COUNTS or self.channel_count != self.render_spec.channel_count:
            raise ValueError(f"WrittenWav.channel_count {self.channel_count} disagrees with the render specification")
        if self.sample_rate_hz != self.render_spec.sample_rate_hz:
            raise ValueError(f"WrittenWav.sample_rate_hz {self.sample_rate_hz} disagrees with the render specification")


# --- argument validation ------------------------------------------------------------------------


def _filename_problem(filename: Any, *, windows: bool) -> str | None:
    """Return why ``filename`` is not an accepted output leaf under the given policy, or None; no I/O.

    The Windows policy additionally rejects stems Python 3.11 reports as reserved device names.
    """
    if not isinstance(filename, str) or not _FILENAME.fullmatch(filename):
        return f"filename {filename!r} must match [A-Za-z0-9_-]+\\.wav exactly"
    if windows and PureWindowsPath(filename[: -len(".wav")]).is_reserved():
        return f"filename {filename!r} uses a reserved Windows device name"
    return None


def _require_samples(samples: Any) -> tuple[int, int]:
    """Check type, dtype and shape without reading sample values; return (frames, channels)."""
    if type(samples) is not np.ndarray:
        raise ValueError(f"samples must be a numpy.ndarray (not a subclass), got {type(samples).__name__}")
    if samples.dtype.type is not np.float64 or not samples.dtype.isnative:
        raise ValueError(f"samples must have native-endian float64 dtype, got {samples.dtype.str}")
    if samples.ndim != 2:
        raise ValueError(f"samples must have shape (frames, channels), got {samples.shape}")
    frames, channels = samples.shape
    if frames < 1:
        raise ValueError("samples must contain at least one frame")
    if channels not in _CHANNEL_COUNTS:
        raise ValueError(f"samples must have 1 or 2 channels, got {channels}")
    return frames, channels


def _validate_arguments(
    workspace: Any,
    filename: Any,
    samples: Any,
    render_spec: Any,
    processing_spec: Any,
    max_file_bytes: Any,
    max_decoded_bytes: Any,
) -> tuple[str, str]:
    """Plain ``ValueError`` for every invalid argument; returns the soundfile (format, subtype)."""
    _require_instance(workspace, AllocatedWorkspace, "workspace")
    problem = _filename_problem(filename, windows=os.name == "nt")
    if problem is not None:
        raise ValueError(problem)
    _frames, channels = _require_samples(samples)
    _require_instance(render_spec, RenderSpec, "render_spec")
    _require_instance(processing_spec, ProcessingSpec, "processing_spec")
    container = _FORMATS.get(processing_spec.output_container)
    if container is None:
        raise ValueError(f"unsupported output container {processing_spec.output_container!r}")
    subtype = _SUBTYPES.get(processing_spec.sample_encoding)
    if subtype is None:
        raise ValueError(f"unsupported sample encoding {processing_spec.sample_encoding!r}")
    if render_spec.channel_count != channels:
        raise ValueError(f"samples have {channels} channel(s), render_spec declares {render_spec.channel_count}")
    if not 1 <= render_spec.sample_rate_hz <= _MAX_SAMPLE_RATE:
        raise ValueError(
            f"sample rate {render_spec.sample_rate_hz} cannot be represented by libsndfile (1..{_MAX_SAMPLE_RATE})"
        )
    _require_count(max_file_bytes, "max_file_bytes")
    _require_count(max_decoded_bytes, "max_decoded_bytes")
    return container, subtype


def _snapshot(samples: np.ndarray, max_decoded_bytes: int) -> np.ndarray:
    """Bound the decoded size from the shape, then return a validated private C-contiguous copy.

    The caller's array is never modified. No atomic snapshot is promised against concurrent mutation
    during copying; all later comparisons use only this private copy.
    """
    frames, channels = samples.shape
    decoded = frames * channels * _FLOAT64_BYTES
    if decoded > max_decoded_bytes:
        raise WavOutputError(
            _Code.RESOURCE_LIMIT, f"{decoded} decoded sample bytes exceed max_decoded_bytes={max_decoded_bytes}"
        )
    if not np.isfinite(samples).all():
        raise ValueError("samples must contain only finite values")
    snapshot = np.array(samples, dtype=np.float64, order="C", copy=True)
    _require_samples(snapshot)
    if snapshot.shape != samples.shape or not np.isfinite(snapshot).all():
        raise ValueError("samples changed while being copied")
    return snapshot


# --- in-memory encoding and inspection ------------------------------------------------------------


def _riff_size_problem(length: int) -> str | None:
    """Return why a payload of ``length`` bytes cannot be a single RIFF chunk, or None."""
    if length < _RIFF_HEADER_BYTES:
        return f"payload of {length} bytes is shorter than a RIFF/WAVE header"
    if length - 8 > _RIFF_CHUNK_SIZE_MAX:
        return f"payload of {length} bytes exceeds the 32-bit RIFF chunk-size field"
    return None


def _payload_problem(payload: bytes, snapshot: np.ndarray, render_spec: RenderSpec, container: str, subtype: str):
    """Decode ``payload`` from memory and compare it with the snapshot; return (problem, container, subtype).

    ``sf.LibsndfileError`` from decoding propagates for the caller to classify.
    """
    if payload[:4] != b"RIFF" or payload[8:12] != b"WAVE":
        return "payload does not start with a RIFF/WAVE header", None, None
    if int.from_bytes(payload[4:8], "little") != len(payload) - 8:
        return "RIFF chunk size does not match the payload length", None, None
    with sf.SoundFile(io.BytesIO(payload)) as handle:
        found = (handle.format, handle.subtype, handle.samplerate, handle.channels, handle.frames)
        expected = (container, subtype, render_spec.sample_rate_hz, snapshot.shape[1], snapshot.shape[0])
        if found != expected:
            return f"format/subtype/rate/channels/frames {found} differ from {expected}", found[0], found[1]
        decoded = handle.read(snapshot.shape[0], dtype="float64", always_2d=True)
    if decoded.shape != snapshot.shape or decoded.dtype != np.float64 or not decoded.dtype.isnative:
        return f"decoded {decoded.dtype.str} {decoded.shape}, expected float64 {snapshot.shape}", found[0], found[1]
    decoded_bits = np.ascontiguousarray(decoded).view(np.uint64)
    if not np.array_equal(decoded_bits, snapshot.view(np.uint64)):
        return "decoded samples are not bit-identical to the supplied samples", found[0], found[1]
    return None, found[0], found[1]


def _encode(snapshot: np.ndarray, render_spec: RenderSpec, container: str, subtype: str, max_file_bytes: int) -> bytes:
    """Encode into memory and validate the completed payload before any destination exists."""
    buffer = io.BytesIO()
    try:
        with sf.SoundFile(
            buffer,
            mode="w",
            samplerate=render_spec.sample_rate_hz,
            channels=snapshot.shape[1],
            format=container,
            subtype=subtype,
        ) as handle:
            handle.write(snapshot)
    except sf.LibsndfileError as exc:
        raise WavOutputError(_Code.ENCODING_FAILED, f"libsndfile could not encode: {exc.error_string}") from exc
    payload = buffer.getvalue()
    if len(payload) > max_file_bytes:
        raise WavOutputError(
            _Code.RESOURCE_LIMIT, f"encoded payload of {len(payload)} bytes exceeds max_file_bytes={max_file_bytes}"
        )
    problem = _riff_size_problem(len(payload))
    if problem is not None:
        raise WavOutputError(_Code.ENCODING_FAILED, problem)
    try:
        problem, _container, _subtype = _payload_problem(payload, snapshot, render_spec, container, subtype)
    except sf.LibsndfileError as exc:
        raise WavOutputError(_Code.ENCODING_FAILED, f"encoded payload is unreadable: {exc.error_string}") from exc
    if problem is not None:
        raise WavOutputError(_Code.ENCODING_FAILED, f"in-memory encoding check failed: {problem}")
    return payload


def _prepare(
    workspace: AllocatedWorkspace,
    filename: str,
    samples: np.ndarray,
    *,
    render_spec: RenderSpec,
    processing_spec: ProcessingSpec,
    max_file_bytes: int,
    max_decoded_bytes: int,
) -> tuple[np.ndarray, bytes]:
    """Steps before any filesystem access: arguments, private snapshot and validated in-memory payload."""
    container, subtype = _validate_arguments(
        workspace, filename, samples, render_spec, processing_spec, max_file_bytes, max_decoded_bytes
    )
    snapshot = _snapshot(samples, max_decoded_bytes)
    return snapshot, _encode(snapshot, render_spec, container, subtype, max_file_bytes)


# --- exclusive creation, bounded write and readback ------------------------------------------------


def _entry_exists(path: str) -> bool:
    """Narrow ``lstat`` after a failed exclusive open: True only if an entry is confirmed to exist."""
    try:
        os.lstat(path)
    except OSError:
        return False  # existence not established; never invent collision evidence
    return True


def _open_exclusive(path: str):
    """Open ``path`` once with ``'x+b'`` (unbuffered, no-follow on Windows); classify expected failures."""
    try:
        return open(path, "x+b", buffering=0, opener=_OPENER)
    except FileExistsError as exc:
        raise WavOutputError(_Code.COLLISION, f"{path} already exists") from exc
    except (IsADirectoryError, PermissionError) as exc:
        # Windows reports some existing entries (directories, junctions) this way instead of EEXIST.
        if _entry_exists(path):
            raise WavOutputError(_Code.COLLISION, f"{path} already exists") from exc
        if isinstance(exc, PermissionError):
            raise WavOutputError(_Code.NOT_WRITABLE, f"cannot create {path}") from exc
        raise
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise WavOutputError(_Code.WORKSPACE_CHANGED, f"workspace changed before creating {path}") from exc
    except OSError as exc:
        if exc.errno != errno.ELOOP:
            raise
        raise WavOutputError(_Code.WORKSPACE_CHANGED, f"workspace path of {path} has a link cycle") from exc


def _io_failed(message: str, path: str) -> WavOutputError:
    return WavOutputError(_Code.IO_FAILED, message, created_file=path)


def _write_all(stream, payload: bytes, path: str) -> None:
    """Write ``payload`` in requests of at most ``_IO_CHUNK_BYTES``, continuing after short writes."""
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        request = view[offset : offset + _IO_CHUNK_BYTES]
        try:
            written = stream.write(request)
        except OSError as exc:
            raise _io_failed(f"writing {path} failed after {offset} bytes", path) from exc
        if type(written) is not int or not 1 <= written <= len(request):
            # Zero/None is not retried: it signals no progress, so looping could never terminate.
            raise _io_failed(f"write to {path} reported {written!r} of {len(request)} bytes", path)
        offset += written


def _read_back(stream, max_file_bytes: int, path: str) -> bytes:
    """Flush, rewind and capture at most ``max_file_bytes + 1`` bytes through the created handle."""
    try:
        stream.flush()
        position = stream.seek(0)
    except OSError as exc:
        raise _io_failed(f"flushing or rewinding {path} failed", path) from exc
    if position != 0:
        raise _io_failed(f"rewinding {path} reached offset {position!r}", path)
    chunks, remaining = [], max_file_bytes + 1
    while remaining > 0:
        request = min(_IO_CHUNK_BYTES, remaining)
        try:
            chunk = stream.read(request)
        except OSError as exc:
            raise _io_failed(f"reading back {path} failed", path) from exc
        if chunk is None or len(chunk) > request:
            raise _io_failed(f"read from {path} returned {type(chunk).__name__} for {request} bytes", path)
        if not chunk:
            break
        chunks.append(bytes(chunk))
        remaining -= len(chunk)
    captured = b"".join(chunks)
    if len(captured) > max_file_bytes:
        raise WavOutputError(
            _Code.RESOURCE_LIMIT, f"{path} reads back beyond max_file_bytes={max_file_bytes}", created_file=path
        )
    return captured


def _verify_captured(
    captured: bytes,
    payload: bytes,
    snapshot: np.ndarray,
    render_spec: RenderSpec,
    container: str,
    subtype: str,
    path: str,
) -> tuple[str, str]:
    """Compare the captured bytes with the prepared payload and decode them again from memory."""
    if captured != payload:
        message = f"{path} read back {len(captured)} bytes that differ from the {len(payload)}-byte encoded payload"
        raise WavOutputError(_Code.VERIFICATION_FAILED, message, created_file=path)
    try:
        problem, found_container, found_subtype = _payload_problem(captured, snapshot, render_spec, container, subtype)
    except sf.LibsndfileError as exc:
        message = f"{path} read back unreadable content: {exc.error_string}"
        raise WavOutputError(_Code.VERIFICATION_FAILED, message, created_file=path) from exc
    if problem is not None:
        raise WavOutputError(_Code.VERIFICATION_FAILED, f"{path}: {problem}", created_file=path)
    return found_container, found_subtype


def _close_after_failure(stream, primary: BaseException, path: str) -> None:
    """Attempt to close the owned handle without replacing ``primary``; nothing is deleted."""
    try:
        stream.close()
    except Exception as secondary:  # the primary failure is re-raised by the caller either way
        if isinstance(primary, WavOutputError):
            primary.add_note(f"closing {path} also failed: {secondary!r}")


def write_wav_output(
    workspace: AllocatedWorkspace,
    filename: str,
    samples: np.ndarray,
    *,
    render_spec: RenderSpec,
    processing_spec: ProcessingSpec,
    max_file_bytes: int,
    max_decoded_bytes: int,
) -> WrittenWav:
    """Create ``workspace.directory / filename`` exclusively as WAV/DOUBLE holding ``samples`` and verify it.

    Invalid arguments, sample/spec disagreement, unsupported filenames and unrepresentable sample rates
    raise plain ``ValueError`` before any filesystem access. Expected failures raise ``WavOutputError``;
    those after the exclusive open carry ``created_file`` and leave the file in place. Unexpected
    exceptions propagate unchanged after the owned handle has been closed. The result describes bytes
    read back through the created handle before it was closed successfully.
    """
    snapshot, payload = _prepare(
        workspace,
        filename,
        samples,
        render_spec=render_spec,
        processing_spec=processing_spec,
        max_file_bytes=max_file_bytes,
        max_decoded_bytes=max_decoded_bytes,
    )
    container = _FORMATS[processing_spec.output_container]
    subtype = _SUBTYPES[processing_spec.sample_encoding]
    try:
        validate_allocated_workspace(workspace)
    except WorkspaceAllocationError as exc:
        raise WavOutputError(_Code.WORKSPACE_REJECTED, f"workspace failed revalidation: {exc}") from exc
    path = str(Path(workspace.directory) / filename)

    stream = _open_exclusive(path)
    try:
        _write_all(stream, payload, path)
        captured = _read_back(stream, max_file_bytes, path)
        found_container, found_subtype = _verify_captured(
            captured, payload, snapshot, render_spec, container, subtype, path
        )
        try:
            validate_allocated_workspace(workspace)
        except WorkspaceAllocationError as exc:
            message = f"workspace changed after creating {path}: {exc}"
            raise WavOutputError(_Code.WORKSPACE_CHANGED, message, created_file=path) from exc
    except BaseException as primary:
        _close_after_failure(stream, primary, path)
        raise
    try:
        stream.close()
    except OSError as exc:
        raise _io_failed(f"closing {path} failed", path) from exc

    return WrittenWav(
        workspace=workspace,
        path=path,
        sha256=hashlib.sha256(captured).hexdigest(),
        byte_count=len(captured),
        frame_count=snapshot.shape[0],
        sample_rate_hz=render_spec.sample_rate_hz,
        channel_count=snapshot.shape[1],
        container=found_container,
        subtype=found_subtype,
        render_spec=render_spec,
        processing_spec=processing_spec,
    )
