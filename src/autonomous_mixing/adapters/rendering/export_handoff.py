"""File-based pending-export handoff and verified import (plan 2026-11-18-daw-export-handoff-import, revision 3).

Implements ``core.ports.daw_export.DawExportHandoff``. ``prepare`` allocates one fresh workspace through the
rendering allocator (protected originals are rechecked and never touched), writes ``export-request.json`` there
exclusively with ``fsync``, and records the directory's and request file's file-system identity, size and SHA-256.
It starts no export and contacts no DAW; the result awaits the operator.

``import_files`` reads only that workspace and writes nothing. In order: configured state; workspace policy
(``validate_allocated_workspace``) and directory identity; the request file (``lstat``, handle identity, a read of at
most the recorded size plus one byte, exact canonical bytes); the manifest; the directory listing (only the request
file and the declared outputs, compared after NFC normalization and case folding); every output's file object
(regular, no link or reparse point, distinct physical identity, one link, strictly inside the workspace); then the
shared verifier ``check_resolved_wav_integrity`` (hash, WAV encoding, declared rate, channels and frames, finite
samples) and the exact requested container and subtype. After each file's read its metadata (identity, size,
modification and change timestamps, link count, type) must equal what was observed before it; before returning,
the request file and every output are re-checked against those observations again, so a change to an earlier file
during a later file's read is caught too. Nothing is converted, resampled or folded down.

None of these checks is race-free. File-system identities can be reused after deletion, and timestamp checks
depend on the file system's timestamp granularity and on writers not resetting timestamps: they detect
replacement and ordinary in-place writes, not every concurrent modification. A change after the final re-check is
not detected. A verified file proves its captured bytes only: not how it was
exported, not the open project, not physical timeline alignment, and never acceptance.
"""

import hashlib
import json
import os
import stat
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from autonomous_mixing.adapters.audio_io.integrity import (
    IntegrityError,
    IntegrityFailureCode,
    check_resolved_wav_integrity,
)
from autonomous_mixing.adapters.audio_io.limits import AudioReadLimits
from autonomous_mixing.adapters.audio_io.locations import (
    LocationResolutionError,
    ResolvedArtifactLocation,
    resolve_artifact_location,
)
from autonomous_mixing.adapters.rendering.revalidation import validate_allocated_workspace
from autonomous_mixing.adapters.rendering.workspaces import (
    AllocatedWorkspace,
    WorkspaceAllocationError,
    WorkspaceAllocationFailureCode,
    WorkspaceAllocationRequest,
    WorkspaceRoot,
    allocate_workspace,
)
from autonomous_mixing.core.records.daw_export import (
    DAW_EXPORT_REQUEST_FILE,
    DawExportAwaiting,
    DawExportPreparationCode,
    DawExportPreparationFailure,
    DawExportRequest,
    DawFileIdentity,
    DawFilesVerified,
    DawImportedFileEvidence,
    DawImportRejected,
    DawImportRejectionCode,
    DawImportRequest,
    daw_export_artifact,
    daw_export_file_name_problem,
    daw_file_name_key,
)
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.project import ProjectState

_Code = DawImportRejectionCode
_INTEGRITY_CODES = {
    IntegrityFailureCode.HASH_MISMATCH: _Code.HASH_MISMATCH,
    IntegrityFailureCode.INVALID_WAV: _Code.ENCODING,
    IntegrityFailureCode.METADATA_MISMATCH: _Code.DIMENSIONS,
    IntegrityFailureCode.NONFINITE_SAMPLES: _Code.NONFINITE,
    IntegrityFailureCode.RESOURCE_LIMIT: _Code.RESOURCE_LIMIT,
    IntegrityFailureCode.NOT_FOUND: _Code.UNAVAILABLE,
    IntegrityFailureCode.NOT_READABLE: _Code.UNAVAILABLE,
    IntegrityFailureCode.NOT_REGULAR_FILE: _Code.LOCATION,
    IntegrityFailureCode.UNSUPPORTED_PATH: _Code.LOCATION,
}
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_READ_CHUNK = 1 << 16


def _plain(value):
    """A JSON-ready view of a core record: dataclass fields by name, enums by value, UTC datetimes in ISO form."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"unsupported value in an export request: {type(value).__name__}")


def export_request_bytes(request: DawExportRequest) -> bytes:
    """Canonical persisted form of one export request: sorted-key ASCII JSON with a trailing newline."""
    require_instance(request, DawExportRequest, "request")
    text = json.dumps({"v": 1, "export_request": _plain(request)}, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, ensure_ascii=True)  # fmt: skip
    return (text + "\n").encode("ascii")


def _identity(status: os.stat_result) -> DawFileIdentity:
    return DawFileIdentity(status.st_dev, status.st_ino)


def _observed(status: os.stat_result) -> tuple:
    """Change-relevant metadata of one file object (in-place review fix, 2026-11-18)."""
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns, status.st_nlink,
            status.st_mode, getattr(status, "st_file_attributes", 0))  # fmt: skip


def _linked(status: os.stat_result) -> bool:
    return stat.S_ISLNK(status.st_mode) or bool(getattr(status, "st_file_attributes", 0) & _REPARSE)


class _Rejected(Exception):
    def __init__(self, code: DawImportRejectionCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class FileExportHandoff:
    """One configured output root, project state, protected-location inventory and read limits.

    ``state`` is the before state of the export: its audio membership must equal each request's before checkpoint.
    The caller owns the inventory's completeness (as for rendering workspaces); nothing here deletes or cleans up.
    """

    def __init__(
        self,
        *,
        root: WorkspaceRoot,
        state: ProjectState,
        protected_locations: tuple[ResolvedArtifactLocation, ...],
        limits: AudioReadLimits,
    ):
        require_instance(root, WorkspaceRoot, "root")
        require_instance(state, ProjectState, "state")
        require_instance(limits, AudioReadLimits, "limits")
        if not isinstance(protected_locations, tuple) or not all(
            isinstance(item, ResolvedArtifactLocation) for item in protected_locations
        ):
            raise ValueError("protected_locations must be a tuple of ResolvedArtifactLocation")
        self._root, self._state, self._protected, self._limits = root, state, protected_locations, limits

    def _allocation_request(self, request: DawExportRequest) -> WorkspaceAllocationRequest:
        return WorkspaceAllocationRequest(request.workspace, self._root, self._state, self._protected)

    # --- preparation -----------------------------------------------------------------------------------------------

    def prepare(self, request: DawExportRequest) -> DawExportAwaiting | DawExportPreparationFailure:
        require_instance(request, DawExportRequest, "request")

        def failure(code, message):
            return DawExportPreparationFailure(request.export_id, code, message)

        if self._state.audio != request.before_checkpoint:
            return failure(DawExportPreparationCode.REQUEST_STATE_MISMATCH,
                           "the configured state's audio is not the request's before checkpoint")  # fmt: skip
        try:
            allocated = allocate_workspace(self._allocation_request(request))
        except WorkspaceAllocationError as error:
            code = (
                DawExportPreparationCode.WORKSPACE_OVERLAPS_PROTECTED
                if error.code is WorkspaceAllocationFailureCode.PROTECTED_OVERLAP
                else DawExportPreparationCode.WORKSPACE_UNAVAILABLE
            )
            return failure(code, str(error))
        except OSError as error:
            return failure(DawExportPreparationCode.WORKSPACE_UNAVAILABLE, str(error) or "workspace unavailable")
        payload = export_request_bytes(request)
        directory = Path(allocated.directory)
        try:
            directory_status = os.lstat(directory)
            descriptor = os.open(directory / DAW_EXPORT_REQUEST_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY,
                                 0o644)  # fmt: skip
            try:
                view = memoryview(payload)
                while view:
                    view = view[os.write(descriptor, view) :]
                os.fsync(descriptor)
                file_status = os.fstat(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            # The created directory is left in place and reported; nothing is deleted.
            return failure(DawExportPreparationCode.REQUEST_NOT_PERSISTED,
                           f"{allocated.directory}: {error.strerror or error}")  # fmt: skip
        return DawExportAwaiting(request, hashlib.sha256(payload).hexdigest(), len(payload), allocated.resolved_root,
                                 allocated.directory, _identity(directory_status), _identity(file_status))  # fmt: skip

    # --- import ----------------------------------------------------------------------------------------------------

    def import_files(self, request: DawImportRequest) -> DawFilesVerified | DawImportRejected:
        require_instance(request, DawImportRequest, "request")
        export_id = request.awaiting.request.export_id
        try:
            return self._import(request)
        except _Rejected as rejection:
            return DawImportRejected(export_id, rejection.code, str(rejection) or rejection.code.value)
        except OSError as error:
            return DawImportRejected(export_id, _Code.UNAVAILABLE, str(error) or "the workspace cannot be read")

    def _import(self, request: DawImportRequest) -> DawFilesVerified:
        awaiting, manifest = request.awaiting, request.manifest
        export = awaiting.request
        if self._state.audio != export.before_checkpoint:
            raise _Rejected(_Code.REQUEST_MISMATCH, "the configured state is not the request's before state")
        directory = self._check_workspace(awaiting)
        request_status = self._check_request_file(awaiting, directory / DAW_EXPORT_REQUEST_FILE)
        listed = self._check_manifest(awaiting, manifest)
        self._check_listing(export, directory)
        statuses = self._check_objects(awaiting, directory)
        evidence = tuple(
            self._verify(export, output, listed[output.artifact_id].content_hash, directory, statuses[index])
            for index, output in enumerate(export.outputs)
        )
        # Final re-check of every observed object: an earlier file may have changed during a later file's read.
        observed = [(DAW_EXPORT_REQUEST_FILE, request_status), *zip((o.file_name for o in export.outputs), statuses)]
        for name, status in observed:
            if _observed(os.lstat(directory / name)) != _observed(status):
                raise _Rejected(_Code.WORKSPACE_CHANGED, f"{name} changed during the import")
        if _identity(os.lstat(directory)) != awaiting.directory_identity:
            raise _Rejected(_Code.WORKSPACE_CHANGED, "the workspace changed during the import")
        return DawFilesVerified(request, evidence)

    def _check_workspace(self, awaiting: DawExportAwaiting) -> Path:
        allocated = AllocatedWorkspace(self._allocation_request(awaiting.request), awaiting.resolved_root,
                                       awaiting.workspace_directory)  # fmt: skip
        try:
            validate_allocated_workspace(allocated)
        except WorkspaceAllocationError as error:
            raise _Rejected(_Code.WORKSPACE_CHANGED, str(error)) from error
        directory = Path(awaiting.workspace_directory)
        status = os.lstat(directory)
        if _linked(status) or not stat.S_ISDIR(status.st_mode):
            raise _Rejected(_Code.WORKSPACE_CHANGED, "the workspace is not a plain directory")
        if _identity(status) != awaiting.directory_identity:
            raise _Rejected(_Code.WORKSPACE_REPLACED, "the workspace directory is not the prepared one")
        return directory

    def _check_request_file(self, awaiting: DawExportAwaiting, path: Path) -> os.stat_result:
        try:
            status = os.lstat(path)
        except FileNotFoundError as error:
            raise _Rejected(_Code.WORKSPACE_REPLACED, "the prepared request file is missing") from error
        if _linked(status):
            raise _Rejected(_Code.LINKED_FILE, "the request file is a link or reparse point")
        if not stat.S_ISREG(status.st_mode):
            raise _Rejected(_Code.LOCATION, "the request file is not a regular file")
        if status.st_nlink != 1:
            raise _Rejected(_Code.LINKED_FILE, "the request file has another hard link")
        if _identity(status) != awaiting.request_file_identity:
            raise _Rejected(_Code.WORKSPACE_REPLACED, "the request file is not the prepared one")
        descriptor = os.open(path, os.O_RDONLY | _BINARY)
        try:
            if _identity(os.fstat(descriptor)) != awaiting.request_file_identity:
                raise _Rejected(_Code.WORKSPACE_REPLACED, "the opened request file is not the prepared one")
            chunks, remaining = [], awaiting.request_size + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(_READ_CHUNK, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            os.close(descriptor)
        payload = b"".join(chunks)
        if len(payload) != awaiting.request_size or hashlib.sha256(payload).hexdigest() != awaiting.request_fingerprint:
            raise _Rejected(_Code.REQUEST_MISMATCH, "the request file differs from the prepared bytes")
        if payload != export_request_bytes(awaiting.request):
            raise _Rejected(_Code.REQUEST_MISMATCH, "the awaiting record holds another request than the file")
        return status

    @staticmethod
    def _check_manifest(awaiting: DawExportAwaiting, manifest) -> dict:
        export = awaiting.request
        if (manifest.export_id, manifest.request_fingerprint) != (export.export_id, awaiting.request_fingerprint):
            raise _Rejected(_Code.REQUEST_MISMATCH, "the manifest answers another export request")
        listed = {item.artifact_id: item for item in manifest.files}
        expected = {output.artifact_id for output in export.outputs}
        if set(listed) - expected:
            raise _Rejected(_Code.REQUEST_MISMATCH, "the manifest lists artifacts the request does not require")
        if expected - set(listed):
            raise _Rejected(_Code.INCOMPLETE_COVERAGE, "the manifest omits a required output")
        if any(listed[output.artifact_id].file_name != output.file_name for output in export.outputs):
            raise _Rejected(_Code.REQUEST_MISMATCH, "the manifest names another file than the request")
        return listed

    @staticmethod
    def _check_listing(export: DawExportRequest, directory: Path) -> None:
        allowed = {daw_file_name_key(name): name for name in (DAW_EXPORT_REQUEST_FILE,
                                                              *(o.file_name for o in export.outputs))}  # fmt: skip
        seen = {}
        for name in os.listdir(directory):
            key = daw_file_name_key(name)
            if key not in allowed:
                raise _Rejected(_Code.UNEXPECTED_FILE, f"unexpected workspace entry {name!r}")
            if key in seen or name != allowed[key]:
                raise _Rejected(_Code.NAME_COLLISION, f"workspace entry {name!r} collides with {allowed[key]!r}")
            seen[key] = name
        if set(seen) != set(allowed):
            raise _Rejected(_Code.INCOMPLETE_COVERAGE, "a required output file is missing")

    @staticmethod
    def _check_objects(awaiting: DawExportAwaiting, directory: Path) -> list:
        statuses = []
        for output in awaiting.request.outputs:
            if daw_export_file_name_problem(output.file_name) is not None:
                raise _Rejected(_Code.LOCATION, f"{output.file_name!r} is not a valid output name")
            status = os.lstat(directory / output.file_name)
            if _linked(status):
                raise _Rejected(_Code.LINKED_FILE, f"{output.file_name} is a link or reparse point")
            if not stat.S_ISREG(status.st_mode):
                raise _Rejected(_Code.LOCATION, f"{output.file_name} is not a regular file")
            statuses.append(status)
        identities = [awaiting.request_file_identity, *(_identity(status) for status in statuses)]
        if len(set(identities)) != len(identities):
            raise _Rejected(_Code.DUPLICATE_PHYSICAL_FILE, "two workspace names refer to one physical file")
        for output, status in zip(awaiting.request.outputs, statuses, strict=True):
            if status.st_nlink != 1:
                raise _Rejected(_Code.LINKED_FILE, f"{output.file_name} has another hard link")
            resolved = (directory / output.file_name).resolve(strict=True)
            if resolved.parent != directory or resolved.name != output.file_name:
                raise _Rejected(_Code.LOCATION, f"{output.file_name} does not lie directly in the workspace")
        return statuses

    def _verify(self, export, output, content_hash, directory, status) -> DawImportedFileEvidence:
        artifact = daw_export_artifact(export, output, content_hash)
        try:
            location = resolve_artifact_location(artifact, root=self._root.path)
        except LocationResolutionError as error:
            raise _Rejected(_Code.LOCATION, str(error)) from error
        if Path(location.absolute_path) != directory / output.file_name:
            raise _Rejected(_Code.LOCATION, f"{output.file_name} resolves outside the prepared workspace")
        try:
            checked = check_resolved_wav_integrity(location, max_file_bytes=self._limits.max_file_bytes,
                                                   max_decoded_bytes=self._limits.max_decoded_bytes)  # fmt: skip
        except IntegrityError as error:
            raise _Rejected(_INTEGRITY_CODES[error.code], str(error)) from error
        except LocationResolutionError as error:
            raise _Rejected(_Code.LOCATION, str(error)) from error
        if (checked.container, checked.subtype) != (export.format.container, export.format.subtype):
            raise _Rejected(_Code.ENCODING, f"{output.file_name} is {checked.container}/{checked.subtype}, "
                                            f"not {export.format.container}/{export.format.subtype}")  # fmt: skip
        after = os.lstat(directory / output.file_name)
        if _observed(after) != _observed(status):
            raise _Rejected(_Code.WORKSPACE_CHANGED, f"{output.file_name} changed during its verification")
        return DawImportedFileEvidence(output.artifact_id, checked.verified_sha256, checked.container, checked.subtype,
                                       True, _identity(status))  # fmt: skip
