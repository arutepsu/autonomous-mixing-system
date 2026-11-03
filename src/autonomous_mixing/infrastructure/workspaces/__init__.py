"""Output workspace allocation: one fresh directory per execution below an explicit output root, and
read-only revalidation of an allocated workspace record."""

from .allocation import (
    AllocatedWorkspace,
    WorkspaceAllocationError,
    WorkspaceAllocationFailureCode,
    WorkspaceAllocationRequest,
    WorkspaceRoot,
    allocate_workspace,
)
from .validation import validate_allocated_workspace

__all__ = [
    "WorkspaceRoot",
    "WorkspaceAllocationRequest",
    "AllocatedWorkspace",
    "WorkspaceAllocationFailureCode",
    "WorkspaceAllocationError",
    "allocate_workspace",
    "validate_allocated_workspace",
]
