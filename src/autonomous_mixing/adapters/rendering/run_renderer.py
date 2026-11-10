"""File-backed ``RunRenderer``: prepare one exclusive workspace and run the gain/mix processor once.

One ``process`` call checks that every logical root it needs is configured, resolves every protected
artifact (current audio, fixed inputs and further explicitly bound retained files) against its configured
physical root, allocates the plan's workspace with ``allocate_workspace`` and invokes a fresh
``GainMixProcessor`` once. Path, inventory, overlap and collision policy stay in the resolver and the
allocator; numerical behavior stays in the processor. The adapter performs no admission, budgeting,
verification, acceptance, recovery, retry or cleanup.

Expected preparation failures become ``RenderPreparationFailure``: ``root_not_configured``,
``location_resolution:<code>`` and ``workspace_allocation:<code>``. An allocation failure after a known
directory creation names the request's logical workspace and the reported physical path; nothing is
deleted. Once the processor runs, its ``RenderedCandidate`` or ``RenderFailure`` is returned unchanged.
Only ``LocationResolutionError`` (during resolution) and ``WorkspaceAllocationError`` (during allocation)
are mapped; every other exception propagates unchanged and may follow real file or directory creation.
"""

from collections.abc import Mapping

from autonomous_mixing.adapters.rendering.gain_mix import GainMixProcessor, ProcessingLimits
from autonomous_mixing.adapters.rendering.locations import LocationResolutionError, resolve_artifact_location
from autonomous_mixing.adapters.rendering.workspaces import (
    WorkspaceAllocationError,
    WorkspaceAllocationRequest,
    WorkspaceRoot,
    allocate_workspace,
)
from autonomous_mixing.core.records.actions import ExecutionIssue
from autonomous_mixing.core.records.coordinator_runtime import (
    RenderPreparationFailure,
    RunRenderOutcome,
    RunRenderRequest,
)
from autonomous_mixing.core.records.invariants import require_instance, require_text

ROOT_NOT_CONFIGURED = "root_not_configured"


class FileRunRenderer:
    """Implements ``RunRenderer`` over configured filesystem roots (structural protocol conformance).

    ``roots`` maps logical root keys to physical root paths and is copied at construction; nothing is
    accessed on the filesystem until ``process``. ``limits`` are passed unchanged to every processor.
    """

    def __init__(self, roots: Mapping[str, str], *, limits: ProcessingLimits) -> None:
        if not isinstance(roots, Mapping):
            raise ValueError(f"roots must be a Mapping, got {type(roots).__name__}")
        snapshot = dict(roots.items())
        for key, path in snapshot.items():
            require_text(key, "root key")
            require_text(path, f"root path for {key!r}")
        require_instance(limits, ProcessingLimits, "limits")
        self._roots: dict[str, str] = snapshot
        self._limits = limits

    def process(self, request: RunRenderRequest) -> RunRenderOutcome:
        """Prepare one workspace and render the plan once; see the module docstring for outcomes."""
        require_instance(request, RunRenderRequest, "request")
        plan = request.plan
        bound = {binding.artifact_id: binding.root_key for binding in request.protected_root_bindings}
        output_key = plan.workspace.root_key
        needed = dict.fromkeys((output_key, *(bound[a.artifact_id] for a in request.protected_artifacts)))
        missing = [key for key in needed if key not in self._roots]
        if missing:  # every key is checked before any resolution or allocation
            return _preparation_failure(ROOT_NOT_CONFIGURED, f"root keys not configured: {', '.join(missing)}")

        locations = []
        for artifact in request.protected_artifacts:
            key = bound[artifact.artifact_id]
            try:
                locations.append(resolve_artifact_location(artifact, root=self._roots[key]))
            except LocationResolutionError as error:
                return _preparation_failure(
                    f"location_resolution:{error.code.value}",
                    f"protected artifact {artifact.artifact_id} under root {key!r}: {error}",
                )

        allocation = WorkspaceAllocationRequest(
            plan.workspace, WorkspaceRoot(output_key, self._roots[output_key]), request.before.state, tuple(locations)
        )
        try:
            workspace = allocate_workspace(allocation)
        except WorkspaceAllocationError as error:
            code = f"workspace_allocation:{error.code.value}"
            if error.created_directory is None:
                return _preparation_failure(code, f"workspace {plan.workspace.directory_token!r}: {error}")
            message = f"workspace {plan.workspace.directory_token!r}: {error}; created directory retained: "
            return RenderPreparationFailure(ExecutionIssue(code, message + error.created_directory), plan.workspace)
        return GainMixProcessor(workspace, self._limits).process(plan)


def _preparation_failure(code: str, message: str) -> RenderPreparationFailure:
    return RenderPreparationFailure(ExecutionIssue(code, message), None)
