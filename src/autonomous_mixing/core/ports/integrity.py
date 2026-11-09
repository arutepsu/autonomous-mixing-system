"""The state-integrity inspector port and the binding check on its reports (attempt contract §§22, 28-29)."""

from typing import Protocol

from autonomous_mixing.core.records.integrity import StateIntegrityReport, StateIntegrityRequest
from autonomous_mixing.core.records.invariants import require_instance


class StateIntegrityInspector(Protocol):
    """Inspects every artifact a supplied state requires and reports one result per artifact.

    Expected inspection failures and uncertainty are recorded per artifact (an uninspected file is
    UNCERTAIN with a reason); unexpected implementation errors propagate. The inspector reads only the
    supplied request and never chooses an accepted state: the coordinator selects the candidate or
    previous state from journal authority and owns the recovery and time budgets. No implementation
    exists yet; a file-backed inspector follows in a separate adapter task.
    """

    def inspect(self, request: StateIntegrityRequest) -> StateIntegrityReport: ...


def validate_state_integrity_response(request: StateIntegrityRequest, response: StateIntegrityReport) -> None:
    """Require the report to answer exactly ``request``: complete state metadata and the exact binding tuple.

    Result coverage, order and metadata are enforced by ``StateIntegrityReport`` itself. This checks
    structure only, not whether the inspection claims are true.
    """
    require_instance(request, StateIntegrityRequest, "request")
    require_instance(response, StateIntegrityReport, "response")
    if response.request != request:
        raise ValueError("integrity report answers a different request (state or root bindings differ)")
