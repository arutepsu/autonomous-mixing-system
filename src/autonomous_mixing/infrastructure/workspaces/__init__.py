"""Output workspace allocation: one fresh directory per execution below an explicit output root."""

from .allocation import (
    AllocatedWorkspace,
    WorkspaceAllocationError,
    WorkspaceAllocationFailureCode,
    WorkspaceAllocationRequest,
    WorkspaceRoot,
    allocate_workspace,
)

__all__ = [
    "WorkspaceRoot",
    "WorkspaceAllocationRequest",
    "AllocatedWorkspace",
    "WorkspaceAllocationFailureCode",
    "WorkspaceAllocationError",
    "allocate_workspace",
]
