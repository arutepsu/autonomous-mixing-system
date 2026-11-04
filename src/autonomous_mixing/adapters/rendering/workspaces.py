"""Allocation of one fresh output directory for an application-issued ``ExecutionWorkspace``.

The configured output root is explicit, must already exist as a directory and uses the same native
absolute local path syntax as the input location resolver. The workspace's ``directory_token`` is
used unchanged as the single child name below the strictly resolved root. Before creating that child,
the current state's audio members and fixed input baselines must appear in the caller's protected
locations with exactly equal metadata; every supplied location is freshly re-resolved, compared with
its record and required to be a regular file, and no protected file may equal, contain or lie below
the planned directory. The child is then created by one exclusive ``mkdir`` without creating parents,
and the observed root and child are re-inspected.

Nothing is deleted, renamed, reused or cleaned up: a directory created before a later failure is left
in place and reported. The checks describe the filesystem observed during the calls. They do not
establish a complete project-history inventory, race-free confinement, durable ownership, exclusive
access, an execution lock or protection of files against later writers.
"""

import errno
import os
import re
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import Any

from autonomous_mixing.adapters.rendering.locations import (
    LocationResolutionError,
    ResolvedArtifactLocation,
    resolve_artifact_location,
)
from autonomous_mixing.core.records.artifacts import AudioArtifact
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.rendering import ExecutionWorkspace

# Same shape as the application's ExecutionWorkspace token; rechecked here because the directory
# name is derived from it. Explicit ASCII classes, no normalization.
_DIRECTORY_TOKEN = re.compile(r"[A-Za-z0-9_-]+")
# Root syntax policy mirrored from the input resolver (no import of its private helpers).
_URI_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]+:")
_DRIVE = re.compile(r"[A-Za-z]:")
_ANY_SEPARATOR = ("/", "\\")
_WINDOWS_SPLIT = re.compile(r"[\\/]")


class WorkspaceAllocationFailureCode(Enum):
    ROOT_KEY_MISMATCH = "root_key_mismatch"
    INVALID_INVENTORY = "invalid_inventory"
    INVALID_ROOT = "invalid_root"
    UNSUPPORTED_PATH = "unsupported_path"
    PROTECTED_LOCATION = "protected_location"
    PROTECTED_OVERLAP = "protected_overlap"
    COLLISION = "collision"
    NOT_WRITABLE = "not_writable"
    WORKSPACE_CHANGED = "workspace_changed"


class WorkspaceAllocationError(ValueError):
    """An expected allocation failure; ``code`` names the rejected condition.

    ``created_directory`` is the intended child path when this call's ``mkdir`` had returned
    successfully before the failure, else ``None``. It reports a known creation only: the directory
    may since have changed or disappeared, and the field is no authorization to delete it.
    """

    def __init__(
        self, code: WorkspaceAllocationFailureCode, message: str, *, created_directory: str | None = None
    ) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.created_directory = created_directory


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string, got {value!r}")
    return value


def _require_instance(value: Any, cls: type, field: str) -> None:
    if not isinstance(value, cls):
        raise ValueError(f"{field} must be a {cls.__name__}, got {type(value).__name__}")


@dataclass(frozen=True)
class WorkspaceRoot:
    """Explicit configuration of the physical output root for exactly one ``root_key``.

    Construction checks types and non-blank text only, without filesystem access.
    """

    root_key: str
    path: str

    def __post_init__(self) -> None:
        _require_text(self.root_key, "WorkspaceRoot.root_key")
        _require_text(self.path, "WorkspaceRoot.path")


@dataclass(frozen=True)
class WorkspaceAllocationRequest:
    """One allocation request: the logical assignment, its root, the state and protected locations.

    ``protected_locations`` must cover the state's audio members and fixed input baselines; the
    caller adds further source artifacts and retained outputs that the current state does not list.
    Identity keys ``(project_id, artifact_id)`` must be unique even for equal records; distinct keys
    may share one physical file. Construction performs no filesystem access and does not establish
    that the supplied inventory is complete.
    """

    workspace: ExecutionWorkspace
    root: WorkspaceRoot
    state: ProjectState
    protected_locations: tuple[ResolvedArtifactLocation, ...]

    def __post_init__(self) -> None:
        _require_instance(self.workspace, ExecutionWorkspace, "WorkspaceAllocationRequest.workspace")
        _require_instance(self.root, WorkspaceRoot, "WorkspaceAllocationRequest.root")
        _require_instance(self.state, ProjectState, "WorkspaceAllocationRequest.state")
        if not isinstance(self.protected_locations, tuple):
            raise ValueError("WorkspaceAllocationRequest.protected_locations must be a tuple")
        keys = set()
        for location in self.protected_locations:
            _require_instance(location, ResolvedArtifactLocation, "WorkspaceAllocationRequest.protected_locations[]")
            key = (location.artifact.project_id, location.artifact.artifact_id)
            if key in keys:
                raise ValueError(
                    f"WorkspaceAllocationRequest.protected_locations has a duplicate identity key: "
                    f"project {key[0]}, artifact {key[1]}"
                )
            keys.add(key)


@dataclass(frozen=True)
class AllocatedWorkspace:
    """The request with the resolved root and created directory observed by one allocation.

    Construction checks types and non-blank text only: a record may be constructed manually or be
    stale, and it is not proof of allocation, ownership or permission to write.
    """

    request: WorkspaceAllocationRequest
    resolved_root: str
    directory: str

    def __post_init__(self) -> None:
        _require_instance(self.request, WorkspaceAllocationRequest, "AllocatedWorkspace.request")
        _require_text(self.resolved_root, "AllocatedWorkspace.resolved_root")
        _require_text(self.directory, "AllocatedWorkspace.directory")


_Code = WorkspaceAllocationFailureCode


def _token_problem(token: Any, *, windows: bool) -> str | None:
    """Return why ``token`` cannot be the child name under the given policy, or None; no filesystem access.

    The Windows policy uses Python 3.11's native reserved-name check (``PureWindowsPath.is_reserved``);
    POSIX tokens are held only to the shared token pattern.
    """
    if not isinstance(token, str) or not _DIRECTORY_TOKEN.fullmatch(token):
        return f"directory token {token!r} must match [A-Za-z0-9_-]+"
    if windows and PureWindowsPath(token).is_reserved():
        return f"directory token {token!r} is a reserved Windows device name"
    return None


def _root_syntax_problem(text: str, *, windows: bool) -> str | None:
    """Return why ``text`` is not a supported absolute local root under the given policy, or None.

    Mirrors the input resolver's root policy; no filesystem access.
    """
    if "\x00" in text:
        return "contains a NUL character"
    if text[:1] in _ANY_SEPARATOR and text[1:2] in _ANY_SEPARATOR:
        return "UNC, network or device-namespace paths are not supported"
    if _URI_SCHEME.match(text):
        return "URI-style locations are not supported"
    drive = _DRIVE.match(text)
    if windows:
        if drive is None:
            return "must be a fully qualified absolute local path with a drive"
        if text[2:3] not in _ANY_SEPARATOR:
            return "drive-relative Windows paths are not supported"
        if ":" in text[2:]:
            return "colons outside a drive prefix, including alternate data streams, are not supported"
        components = _WINDOWS_SPLIT.split(text)
    else:
        if drive:
            return "Windows drive syntax is not supported on this platform"
        components = text.split("/")
    if ".." in components:
        return "parent-directory components are not supported"
    if not windows and not text.startswith("/"):
        return "must be a fully qualified absolute local path"
    return None


def _strict_resolve(path: Path, *, failure: _Code, permission: _Code, what: str, created: str | None) -> Path:
    """Strictly resolve ``path``, translating only missing, permission and link-cycle failures."""
    try:
        return path.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as exc:
        message = f"{what} {str(path)!r} does not exist: {exc.strerror}"
        raise WorkspaceAllocationError(failure, message, created_directory=created) from exc
    except PermissionError as exc:
        message = f"cannot resolve {what} {str(path)!r}"
        raise WorkspaceAllocationError(permission, message, created_directory=created) from exc
    except OSError as exc:
        if exc.errno != errno.ELOOP:
            raise
        message = f"{what} {str(path)!r} has a link cycle"
        raise WorkspaceAllocationError(failure, message, created_directory=created) from exc
    except RuntimeError as exc:
        # Python 3.11 pathlib reports a strict-resolution link cycle as a plain RuntimeError;
        # subclasses such as RecursionError and NotImplementedError propagate.
        if type(exc) is not RuntimeError:
            raise
        message = f"{what} {str(path)!r} has a link cycle"
        raise WorkspaceAllocationError(failure, message, created_directory=created) from exc


def _require_directory(path: Path, *, failure: _Code, permission: _Code, what: str, created: str | None) -> None:
    try:
        mode = os.stat(path).st_mode
    except (FileNotFoundError, NotADirectoryError) as exc:
        message = f"{what} {path} is no longer present"
        raise WorkspaceAllocationError(failure, message, created_directory=created) from exc
    except PermissionError as exc:
        message = f"cannot inspect {what} {path}"
        raise WorkspaceAllocationError(permission, message, created_directory=created) from exc
    except OSError as exc:
        if exc.errno != errno.ELOOP:
            raise
        message = f"{what} {path} has a link cycle"
        raise WorkspaceAllocationError(failure, message, created_directory=created) from exc
    if not stat.S_ISDIR(mode):
        raise WorkspaceAllocationError(failure, f"{what} {path} is not a directory", created_directory=created)


def _check_inventory(request: WorkspaceAllocationRequest) -> None:
    """Require every current audio member and fixed input baseline with exactly equal metadata.

    This is the current-state minimum only; it says nothing about further historical files.
    """
    supplied = {
        (loc.artifact.project_id, loc.artifact.artifact_id): loc.artifact for loc in request.protected_locations
    }
    state = request.state
    required: list[AudioArtifact] = [*state.audio.artifacts, *(g.input_artifact for g in state.processing.track_gains)]
    for artifact in required:
        label = f"artifact {artifact.artifact_id} of project {artifact.project_id}"
        recorded = supplied.get((artifact.project_id, artifact.artifact_id))
        if recorded is None:
            raise WorkspaceAllocationError(_Code.INVALID_INVENTORY, f"protected locations omit {label}")
        if recorded != artifact:
            raise WorkspaceAllocationError(
                _Code.INVALID_INVENTORY, f"protected location for {label} differs from the state record"
            )


def _check_protected(location: ResolvedArtifactLocation) -> Path:
    """Freshly resolve one protected record, compare it exactly and require a regular file; no content opens."""
    artifact = location.artifact
    label = f"protected artifact {artifact.artifact_id} of project {artifact.project_id}"
    try:
        fresh = resolve_artifact_location(artifact, root=location.configured_root)
    except LocationResolutionError as exc:
        raise WorkspaceAllocationError(_Code.PROTECTED_LOCATION, f"{label} cannot be resolved: {exc}") from exc
    if fresh.resolved_root != location.resolved_root or fresh.absolute_path != location.absolute_path:
        raise WorkspaceAllocationError(
            _Code.PROTECTED_LOCATION,
            f"{label} is stale: recorded {location.resolved_root!r} / {location.absolute_path!r}, "
            f"now {fresh.resolved_root!r} / {fresh.absolute_path!r}",
        )
    try:
        mode = os.stat(fresh.absolute_path).st_mode
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        message = f"{label} at {fresh.absolute_path} cannot be inspected"
        raise WorkspaceAllocationError(_Code.PROTECTED_LOCATION, message) from exc
    if not stat.S_ISREG(mode):
        message = f"{label} at {fresh.absolute_path} is not a regular file"
        raise WorkspaceAllocationError(_Code.PROTECTED_LOCATION, message)
    return Path(fresh.absolute_path)


def _overlaps(protected: Path, directory: Path) -> bool:
    """Native component relationship: the protected path equals, lies below or lies above ``directory``."""
    return protected.is_relative_to(directory) or directory.is_relative_to(protected)


def _changed(message: str, created: str) -> WorkspaceAllocationError:
    return WorkspaceAllocationError(_Code.WORKSPACE_CHANGED, message, created_directory=created)


def allocate_workspace(request: WorkspaceAllocationRequest) -> AllocatedWorkspace:
    """Create the workspace's single fresh child directory below the explicit output root.

    Order: request and token checks; root-key agreement; mandatory inventory coverage; root syntax,
    strict resolution and directory check; fresh protected-location checks in tuple order; overlap;
    one exclusive ``mkdir`` (``parents=False``, ``exist_ok=False``); re-inspection of root and child.
    The first expected failure raises ``WorkspaceAllocationError``; unexpected errors propagate. No
    failure path deletes, renames or reuses anything, and nothing is retried.
    """
    if not isinstance(request, WorkspaceAllocationRequest):
        raise ValueError(f"request must be a WorkspaceAllocationRequest, got {type(request).__name__}")
    windows = os.name == "nt"
    token = request.workspace.directory_token
    problem = _token_problem(token, windows=windows)
    if problem is not None:
        raise WorkspaceAllocationError(_Code.UNSUPPORTED_PATH, problem)
    if request.workspace.root_key != request.root.root_key:
        raise WorkspaceAllocationError(
            _Code.ROOT_KEY_MISMATCH,
            f"workspace root key {request.workspace.root_key!r} does not match configured {request.root.root_key!r}",
        )
    _check_inventory(request)

    root_text = request.root.path
    problem = _root_syntax_problem(root_text, windows=windows)
    if problem is not None:
        raise WorkspaceAllocationError(_Code.UNSUPPORTED_PATH, f"root {root_text!r}: {problem}")
    resolved_root = _strict_resolve(
        Path(root_text), failure=_Code.INVALID_ROOT, permission=_Code.NOT_WRITABLE, what="root", created=None
    )
    _require_directory(
        resolved_root, failure=_Code.INVALID_ROOT, permission=_Code.NOT_WRITABLE, what="root", created=None
    )
    directory = resolved_root / token

    protected = [_check_protected(location) for location in request.protected_locations]
    for location, path in zip(request.protected_locations, protected, strict=True):
        if _overlaps(path, directory):
            raise WorkspaceAllocationError(
                _Code.PROTECTED_OVERLAP,
                f"protected artifact {location.artifact.artifact_id} at {path} overlaps planned directory {directory}",
            )

    try:
        directory.mkdir(parents=False, exist_ok=False)
    except FileExistsError as exc:
        raise WorkspaceAllocationError(_Code.COLLISION, f"{directory} already exists") from exc
    except PermissionError as exc:
        raise WorkspaceAllocationError(_Code.NOT_WRITABLE, f"cannot create {directory}") from exc
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise WorkspaceAllocationError(_Code.WORKSPACE_CHANGED, f"root changed before creating {directory}") from exc

    created = str(directory)
    changed, denied = _Code.WORKSPACE_CHANGED, _Code.WORKSPACE_CHANGED
    root_now = _strict_resolve(Path(root_text), failure=changed, permission=denied, what="root", created=created)
    if str(root_now) != str(resolved_root):
        raise _changed(f"root now resolves to {root_now}, not {resolved_root}", created)
    _require_directory(root_now, failure=changed, permission=denied, what="root", created=created)
    child_now = _strict_resolve(directory, failure=changed, permission=denied, what="directory", created=created)
    if str(child_now) != created:
        raise _changed(f"{created} now resolves to {child_now}", created)
    try:
        status = os.lstat(directory)
        with os.scandir(directory) as listing:
            empty = next(listing, None) is None
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        raise _changed(f"cannot inspect {created}", created) from exc
    except OSError as exc:
        if exc.errno != errno.ELOOP:
            raise
        raise _changed(f"{created} has a link cycle", created) from exc
    reparse = getattr(status, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if not stat.S_ISDIR(status.st_mode) or stat.S_ISLNK(status.st_mode) or reparse:
        raise _changed(f"{created} is not a plain directory", created)
    if not empty:
        raise _changed(f"{created} is not empty", created)
    return AllocatedWorkspace(request, str(resolved_root), created)
