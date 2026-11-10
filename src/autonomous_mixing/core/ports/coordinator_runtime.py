"""Coordinator runtime ports: diagnosis source, outer renderer, clock and identity source.

The existing ``AudioAnalyzer``, ``DecisionMaker``, ``StateIntegrityInspector`` and ``AcceptanceJournal``
ports remain the stage boundaries; the plan-only ``Renderer`` stays the inner processing boundary. No
implementation exists here: a labelled diagnosis double, concrete clocks, identity generators and the outer
rendering adapter are later work.
"""

from typing import Protocol

from autonomous_mixing.core.ports.rendering import validate_rendered_candidate
from autonomous_mixing.core.records.coordinator_runtime import (
    ClockReading,
    DiagnosisRequest,
    RenderPreparationFailure,
    RunRenderOutcome,
    RunRenderRequest,
)
from autonomous_mixing.core.records.diagnosis import DiagnosisReport, validate_diagnosis_references
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.rendering import RenderedCandidate, RenderFailure
from autonomous_mixing.core.records.results import PortResult


class DiagnosisSource(Protocol):
    """Produces a diagnosis report for one selected state, run scope and set of current observations.

    Returns ``Success(DiagnosisReport)`` or an expected ``PortFailure``; the coordinator checks a report
    with ``validate_diagnosis_response``. Method selection is outside this milestone.
    """

    def diagnose(self, request: DiagnosisRequest) -> PortResult[DiagnosisReport]: ...


class RunRenderer(Protocol):
    """Prepares and performs one render of a validated plan, protecting the complete before state.

    Covers preparation (e.g. workspace allocation) and one inner render call. Outcomes: a rendered
    candidate, a ``RenderFailure`` once rendering may have begun, or a ``RenderPreparationFailure``.
    """

    def process(self, request: RunRenderRequest) -> RunRenderOutcome: ...


class RunClock(Protocol):
    """Observes elapsed monotonic time, UTC time and cancellation; the coordinator checks progression."""

    def read(self) -> ClockReading: ...


class IdentitySource(Protocol):
    """Supplies nonblank identity tokens; the coordinator wraps them in typed identifiers and checks freshness
    against every identity already observed during the run."""

    def next_token(self) -> str: ...


def validate_diagnosis_response(request: DiagnosisRequest, response: DiagnosisReport) -> None:
    """Check a diagnosis report against the request's state, scope and observations (structure and provenance)."""
    require_instance(request, DiagnosisRequest, "request")
    require_instance(response, DiagnosisReport, "response")
    validate_diagnosis_references(response, state=request.state, scope=request.scope, observations=request.observations)


def validate_run_render_response(request: RunRenderRequest, response: RunRenderOutcome) -> None:
    """Check an outer render outcome against its request; no I/O, selection or acceptance."""
    require_instance(request, RunRenderRequest, "request")
    require_instance(response, (RenderedCandidate, RenderFailure, RenderPreparationFailure), "response")
    if isinstance(response, RenderedCandidate):
        validate_rendered_candidate(request.plan, response)
    elif isinstance(response, RenderPreparationFailure):
        workspace = response.created_workspace
        if workspace is not None and workspace != request.plan.workspace:
            raise ValueError("preparation failure names a workspace other than the plan's")
