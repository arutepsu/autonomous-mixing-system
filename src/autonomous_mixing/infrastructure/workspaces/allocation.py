"""Compatibility shim (ADR-027 migration step M3): definitions moved to ``autonomous_mixing.adapters.rendering.workspaces``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.adapters.rendering.workspaces import _ANY_SEPARATOR as _ANY_SEPARATOR

# Private helpers re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.adapters.rendering.workspaces import _DIRECTORY_TOKEN as _DIRECTORY_TOKEN
from autonomous_mixing.adapters.rendering.workspaces import _DRIVE as _DRIVE
from autonomous_mixing.adapters.rendering.workspaces import _URI_SCHEME as _URI_SCHEME
from autonomous_mixing.adapters.rendering.workspaces import _WINDOWS_SPLIT as _WINDOWS_SPLIT
from autonomous_mixing.adapters.rendering.workspaces import (
    AllocatedWorkspace,
    WorkspaceAllocationError,
    WorkspaceAllocationFailureCode,
    WorkspaceAllocationRequest,
    WorkspaceRoot,
    allocate_workspace,
)
from autonomous_mixing.adapters.rendering.workspaces import _changed as _changed
from autonomous_mixing.adapters.rendering.workspaces import _check_inventory as _check_inventory
from autonomous_mixing.adapters.rendering.workspaces import _check_protected as _check_protected
from autonomous_mixing.adapters.rendering.workspaces import _Code as _Code
from autonomous_mixing.adapters.rendering.workspaces import _overlaps as _overlaps
from autonomous_mixing.adapters.rendering.workspaces import _require_directory as _require_directory
from autonomous_mixing.adapters.rendering.workspaces import _require_instance as _require_instance
from autonomous_mixing.adapters.rendering.workspaces import _require_text as _require_text
from autonomous_mixing.adapters.rendering.workspaces import _root_syntax_problem as _root_syntax_problem
from autonomous_mixing.adapters.rendering.workspaces import _strict_resolve as _strict_resolve
from autonomous_mixing.adapters.rendering.workspaces import _token_problem as _token_problem

__all__ = [
    "WorkspaceAllocationFailureCode",
    "WorkspaceAllocationError",
    "WorkspaceRoot",
    "WorkspaceAllocationRequest",
    "AllocatedWorkspace",
    "allocate_workspace",
]
