"""Read-only revalidation of an ``AllocatedWorkspace`` record against the current filesystem.

The record's request is held to the current allocation policy again: directory token, root-key
agreement, minimum protected inventory and root syntax. The configured root is strictly resolved and
must equal the recorded ``resolved_root``; the assigned direct child recomputed from it must equal the
recorded ``directory``, resolve to that same path and ``lstat`` as a plain directory (no symbolic link
or Windows reparse point). Every protected location is freshly resolved, compared with its record and
required to be a regular file, and none may overlap the child.

Nothing is created, opened, listed, modified or removed, and the allocator is not called. The child
need not be empty, so earlier outputs do not block later ones. A manually constructed record that
matches the current filesystem passes: this is inspection at the time of the call, not proof of
allocation provenance, history completeness, ownership or race-free confinement.
"""

import errno
import os
import stat
from pathlib import Path

from autonomous_mixing.adapters.rendering.workspaces import (
    AllocatedWorkspace,
    WorkspaceAllocationError,
    WorkspaceAllocationFailureCode,
    _check_inventory,
    _check_protected,
    _overlaps,
    _require_directory,
    _root_syntax_problem,
    _strict_resolve,
    _token_problem,
)

_Code = WorkspaceAllocationFailureCode


def _changed(message: str) -> WorkspaceAllocationError:
    return WorkspaceAllocationError(_Code.WORKSPACE_CHANGED, message)


def _require_plain_directory(directory: Path) -> None:
    """``lstat`` the child without following a final link; only a real, non-reparse directory passes."""
    try:
        status = os.lstat(directory)
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        raise _changed(f"cannot inspect workspace directory {directory}") from exc
    except OSError as exc:
        if exc.errno != errno.ELOOP:
            raise
        raise _changed(f"workspace directory {directory} has a link cycle") from exc
    reparse = getattr(status, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if not stat.S_ISDIR(status.st_mode) or stat.S_ISLNK(status.st_mode) or reparse:
        raise _changed(f"workspace directory {directory} is not a plain directory")


def validate_allocated_workspace(workspace: AllocatedWorkspace) -> None:
    """Recheck ``workspace`` against the current allocation policy and filesystem; return None if it holds.

    Order: argument type (plain ``ValueError``); token; root-key agreement; mandatory inventory; root
    syntax; strict root resolution and directory check; recorded root and recomputed child equality;
    fresh protected-location checks in tuple order; overlap; strict child resolution; child ``lstat``.
    The first expected failure raises ``WorkspaceAllocationError`` with the allocator's codes (stale,
    missing or redirected records are ``WORKSPACE_CHANGED``) and ``created_directory=None``; unexpected
    errors propagate unchanged.
    """
    if not isinstance(workspace, AllocatedWorkspace):
        raise ValueError(f"workspace must be an AllocatedWorkspace, got {type(workspace).__name__}")
    request = workspace.request
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
    if str(resolved_root) != workspace.resolved_root:
        raise _changed(f"root now resolves to {resolved_root}, not the recorded {workspace.resolved_root!r}")
    directory = resolved_root / token
    if str(directory) != workspace.directory:
        raise _changed(f"recorded directory {workspace.directory!r} is not the assigned child {directory}")

    protected = [_check_protected(location) for location in request.protected_locations]
    for location, path in zip(request.protected_locations, protected, strict=True):
        if _overlaps(path, directory):
            raise WorkspaceAllocationError(
                _Code.PROTECTED_OVERLAP,
                f"protected artifact {location.artifact.artifact_id} at {path} overlaps workspace {directory}",
            )

    changed = _Code.WORKSPACE_CHANGED
    child = _strict_resolve(directory, failure=changed, permission=changed, what="workspace directory", created=None)
    if str(child) != workspace.directory:
        raise _changed(f"workspace directory {workspace.directory!r} now resolves to {child}")
    _require_plain_directory(directory)
