"""Coordinator records of the DAW execution path (coordinator-integration plan, Increment 1; ADR-034, ADR-035).

Pure records only; no loop behaviour uses them yet. A DAW correction is a calibrated fader write followed by a manual
operator export: ``DawCorrectionPlan`` is its execution plan beside the offline ``RenderPlan``, and
``DawCorrectionSuspended`` is the checkpoint recorded when the run waits for the export. A confirmed fader write is
never candidate readiness: no receipt or candidate exists until a verified import (ADR-033).
"""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from autonomous_mixing.core.records.acceptance import AcceptedReference
from autonomous_mixing.core.records.admission import StemBinding, TrackGainPermission
from autonomous_mixing.core.records.artifacts import ArtifactKind
from autonomous_mixing.core.records.daw_binding import DawTrackBinding
from autonomous_mixing.core.records.daw_control import DawApplyRequest, DawControlProfile, DawWriteConfirmed
from autonomous_mixing.core.records.daw_export import (
    DawDependencyStatus,
    DawExportAwaiting,
    DawExportFormat,
    DawExportManifest,
    DawExportOutput,
    DawExportRequest,
    DawExportTimeline,
    DawRoutingDeclaration,
    DawStateBasis,
    RoutingUnknownError,
    daw_export_coverage_problem,
)
from autonomous_mixing.core.records.decision import Decision, ProposeAction
from autonomous_mixing.core.records.feedback import DecisionFeedback
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    AttemptId,
    DecisionCallId,
    ExecutionId,
    ProjectId,
    Revision,
    RunEventId,
    RunId,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.integrity import ArtifactRootBinding
from autonomous_mixing.core.records.invariants import (
    require_finite,
    require_instance,
    require_text,
    require_tuple_of,
    require_utc,
)
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.rendering import ExecutionWorkspace
from autonomous_mixing.core.records.verification_evidence import StateEvidence

_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


def _require_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{field} must be 8-64 characters of A-Z, a-z, 0-9, '_' or '-'")
    return value


def _require_declared(routing: DawRoutingDeclaration, field: str) -> None:
    require_instance(routing, DawRoutingDeclaration, field)
    if routing.status is not DawDependencyStatus.DECLARED:
        raise RoutingUnknownError(f"{field} must declare its dependencies, not leave them unknown")


def daw_apply_export_problem(apply: DawApplyRequest, request: DawExportRequest) -> str | None:
    """Why ``request`` is not the export of exactly the journaled ``apply``, or None (review fixes 2026-11-19).

    Identity is exact agreement: the export's anchor must carry exactly this journaled request (field-for-field
    equality, floats exact). That also fixes its command, execution, binding and profile, because a ``DawStateAnchor``
    carries an apply only for a ``CONFIRMED_WRITE`` source and requires the apply's command, execution, binding and
    profile to be its own, and the ``DawExportRequest`` requires the anchor's to be the request's. Separately, the
    apply's action must act on the export's before state and bound track, and the anchored reading must lie within the
    profile's success tolerance of the apply's resolved target. The tolerance checks the achieved result only; it
    never establishes which request produced it.
    """
    require_instance(apply, DawApplyRequest, "apply")
    require_instance(request, DawExportRequest, "request")
    anchor, action, resolution = request.anchor, apply.admitted.action, apply.resolution
    if anchor.apply != apply:
        return "the apply is not exactly the journaled request of the anchored confirmed write"
    if (action.project_id, action.expected_revision, action.operation.track_id) != (
        request.project_id,
        request.before_checkpoint.revision,
        request.binding.track_id,
    ):
        return "the apply's action does not act on the export's before state and bound track"
    if abs(anchor.reading.db - resolution.target_fader_db) > apply.profile.success_db_tolerance:
        return "the anchored reading is not the confirmed result of this apply's resolved target"
    return None


@dataclass(frozen=True)
class DawSessionConfig:
    """One operator-attended DAW session of a run: the bound insert, its tested profile, policy and export settings.

    ``permission`` is the policy's gain permission for the bound track; the capability view is its intersection with
    the calibrated bounds of the confirmed state (``daw_gain_bounds``). ``export_root_key`` names configured storage
    for export workspaces, never a path. Configuration only: it proves nothing about the open project.
    """

    binding: DawTrackBinding
    profile: DawControlProfile
    permission: TrackGainPermission
    routing: DawRoutingDeclaration
    export_format: DawExportFormat
    export_timeline: DawExportTimeline
    render_spec: VersionRef
    export_root_key: str

    def __post_init__(self) -> None:
        require_instance(self.binding, DawTrackBinding, "DawSessionConfig.binding")
        require_instance(self.profile, DawControlProfile, "DawSessionConfig.profile")
        require_instance(self.permission, TrackGainPermission, "DawSessionConfig.permission")
        _require_declared(self.routing, "DawSessionConfig.routing")
        require_instance(self.export_format, DawExportFormat, "DawSessionConfig.export_format")
        require_instance(self.export_timeline, DawExportTimeline, "DawSessionConfig.export_timeline")
        require_instance(self.render_spec, VersionRef, "DawSessionConfig.render_spec")
        require_text(self.export_root_key, "DawSessionConfig.export_root_key")
        if not self.profile.covers(self.binding):
            raise ValueError("DawSessionConfig binding lies outside the profile's scope")
        if self.permission.track_id != self.binding.track_id:
            raise ValueError("DawSessionConfig permission names another track than the binding")
        if self.binding.track_id in self.routing.dependent_track_ids:
            raise ValueError("DawSessionConfig routing lists the bound track as its own dependent")

    def validate_against(self, state: ProjectState) -> None:
        """The state must hold DAW parameter state for the bound track with exactly this binding."""
        require_instance(state, ProjectState, "state")
        if self.binding.project_id != state.project_id:
            raise ValueError("DawSessionConfig binding belongs to another project")
        if state.daw is None:
            raise ValueError("a DAW session needs a state with DAW parameter state")
        parameter = state.daw.track(self.binding.track_id)
        if parameter is None or parameter.binding != self.binding:
            raise ValueError("the state's DAW parameter state does not hold this session's binding")


@dataclass(frozen=True)
class DawCorrectionPlan:
    """The execution plan of one DAW correction: the fader write and the export it requires.

    ``command_id`` is the stable apply command ID derived from the execution, reused by every retry and replay.
    The outputs are the export request's outputs (ADR-033); ``validate_against`` checks them against a before state.
    """

    execution_id: ExecutionId
    command_id: str
    project_id: ProjectId
    before_revision: Revision
    candidate_revision: Revision
    target_track_id: TrackId
    requested_gain_db: float
    binding: DawTrackBinding
    profile: DawControlProfile
    routing: DawRoutingDeclaration
    outputs: tuple[DawExportOutput, ...]
    timeline: DawExportTimeline
    format: DawExportFormat
    render_spec: VersionRef
    workspace: ExecutionWorkspace

    def __post_init__(self) -> None:
        require_instance(self.execution_id, ExecutionId, "DawCorrectionPlan.execution_id")
        _require_id(self.command_id, "DawCorrectionPlan.command_id")
        require_instance(self.project_id, ProjectId, "DawCorrectionPlan.project_id")
        require_instance(self.before_revision, Revision, "DawCorrectionPlan.before_revision")
        require_instance(self.candidate_revision, Revision, "DawCorrectionPlan.candidate_revision")
        require_instance(self.target_track_id, TrackId, "DawCorrectionPlan.target_track_id")
        require_finite(self.requested_gain_db, "DawCorrectionPlan.requested_gain_db")
        require_instance(self.binding, DawTrackBinding, "DawCorrectionPlan.binding")
        require_instance(self.profile, DawControlProfile, "DawCorrectionPlan.profile")
        _require_declared(self.routing, "DawCorrectionPlan.routing")
        require_tuple_of(self.outputs, DawExportOutput, "DawCorrectionPlan.outputs", nonempty=True, distinct=False)
        require_instance(self.timeline, DawExportTimeline, "DawCorrectionPlan.timeline")
        require_instance(self.format, DawExportFormat, "DawCorrectionPlan.format")
        require_instance(self.render_spec, VersionRef, "DawCorrectionPlan.render_spec")
        require_instance(self.workspace, ExecutionWorkspace, "DawCorrectionPlan.workspace")
        if self.workspace.execution_id != self.execution_id:
            raise ValueError("DawCorrectionPlan workspace belongs to another execution")
        if (self.binding.project_id, self.binding.track_id) != (self.project_id, self.target_track_id):
            raise ValueError("DawCorrectionPlan binding names another project or track than the plan")
        if not self.profile.covers(self.binding):
            raise ValueError("DawCorrectionPlan binding lies outside the profile's scope")
        if self.candidate_revision == self.before_revision:
            raise ValueError("DawCorrectionPlan candidate revision must differ from the before revision")

    def validate_against(self, before: ProjectState) -> None:
        """The before state must be the plan's, hold this binding as DAW parameter state, and be fully coverable."""
        require_instance(before, ProjectState, "before")
        if (before.project_id, before.revision) != (self.project_id, self.before_revision):
            raise ValueError("DawCorrectionPlan before state differs from its plan")
        if before.daw is None:
            raise ValueError("a DAW correction needs a before state with DAW parameter state")
        parameter = before.daw.track(self.target_track_id)
        if parameter is None or parameter.binding != self.binding:
            raise ValueError("the before state's DAW parameter state does not hold the plan's binding")
        if self.candidate_revision in before.known_revisions:
            raise ValueError("DawCorrectionPlan candidate revision reuses a locally known revision")
        problem = daw_export_coverage_problem(before.audio, self.target_track_id, self.routing, self.outputs)
        if problem is not None:
            raise ValueError(f"DawCorrectionPlan {problem}")


@dataclass(frozen=True)
class DawStateQuery:
    """A journal-backed anchor query (``fresh_inspection=False``) or a coherent continuity snapshot (``True``)."""

    source_id: str
    fresh_inspection: bool

    def __post_init__(self) -> None:
        _require_id(self.source_id, "DawStateQuery.source_id")
        if not isinstance(self.fresh_inspection, bool):
            raise ValueError("DawStateQuery.fresh_inspection must be a bool")


@dataclass(frozen=True)
class DawCorrectionSuspended:
    """The checkpoint recorded when a run waits for the operator's export after a confirmed fader write.

    Everything resumption needs without re-deriving it: the prepared correction's parts (``before`` is the selected
    state and equals the accepted reference's state), the issued identity tokens, the feedback history and the
    observations the decision used, the journaled apply request and its confirmed write, and the awaiting export, whose
    anchor is that write and whose settings are exactly the plan's. Usage,
    including elapsed controller time, is the enclosing trace event's. Nothing here is candidate-ready.
    """

    run_id: RunId
    call_id: DecisionCallId
    decision: Decision
    attempt_id: AttemptId
    before: ProjectState
    plan: DawCorrectionPlan
    accepted: AcceptedReference
    tokens: frozenset[str]
    history: tuple[DecisionFeedback, ...]
    observations: tuple[StateEvidence, ...]
    apply: DawApplyRequest  # review fix 2026-11-19: the journaled request whose confirmed write is anchored
    write: DawWriteConfirmed
    awaiting: DawExportAwaiting
    paused_at_utc: datetime
    # Increment 3 (C1): the progress selection fields resumption needs, matching the before state.
    stems: tuple[StemBinding, ...]
    mix_id: ArtifactId
    roots: tuple[ArtifactRootBinding, ...]

    def __post_init__(self) -> None:
        for name, cls in (
            ("run_id", RunId),
            ("call_id", DecisionCallId),
            ("decision", Decision),
            ("attempt_id", AttemptId),
            ("before", ProjectState),
            ("plan", DawCorrectionPlan),
            ("accepted", AcceptedReference),
            ("apply", DawApplyRequest),
            ("write", DawWriteConfirmed),
            ("awaiting", DawExportAwaiting),
        ):
            require_instance(getattr(self, name), cls, f"DawCorrectionSuspended.{name}")
        if not isinstance(self.tokens, frozenset) or not self.tokens:
            raise ValueError("DawCorrectionSuspended.tokens must be a nonempty frozenset of issued tokens")
        for token in self.tokens:
            require_text(token, "DawCorrectionSuspended.tokens element")
        require_tuple_of(self.history, DecisionFeedback, "DawCorrectionSuspended.history", nonempty=False,
                         distinct=False)  # fmt: skip
        require_tuple_of(self.observations, StateEvidence, "DawCorrectionSuspended.observations", nonempty=False,
                         distinct=False)  # fmt: skip
        require_utc(self.paused_at_utc, "DawCorrectionSuspended.paused_at_utc")
        self._check_correction()
        self._check_export()
        self._check_selection()

    def _check_selection(self) -> None:
        """Stems, mix and roots select the before state's current audio, as ``RunRequest`` requires initially."""
        require_tuple_of(self.stems, StemBinding, "DawCorrectionSuspended.stems", nonempty=True, distinct=False)
        require_instance(self.mix_id, ArtifactId, "DawCorrectionSuspended.mix_id")
        require_tuple_of(self.roots, ArtifactRootBinding, "DawCorrectionSuspended.roots", nonempty=True,
                         distinct=False)  # fmt: skip
        audio = self.before.audio
        for binding in self.stems:
            stem = audio.artifact_for(binding.artifact_id)
            if stem is None or stem.kind is not ArtifactKind.STEM or stem.source_track_ids != (binding.track_id,):
                raise ValueError(f"DawCorrectionSuspended stem binding for {binding.track_id} is not a current stem")
        mix = audio.artifact_for(self.mix_id)
        if mix is None or mix.kind is not ArtifactKind.MIX:
            raise ValueError("DawCorrectionSuspended.mix_id is not a current mix of the before state")
        bound = {binding.artifact_id for binding in self.roots}
        if not audio.artifact_ids <= bound:
            raise ValueError("DawCorrectionSuspended.roots must bind every current artifact of the before state")

    def _check_correction(self) -> None:
        if not isinstance(self.decision.outcome, ProposeAction):
            raise ValueError("DawCorrectionSuspended requires a proposed action")
        action, plan = self.decision.outcome.action, self.plan
        if (action.project_id, action.expected_revision) != (self.before.project_id, self.before.revision):
            raise ValueError("DawCorrectionSuspended action differs from its before state")
        if (action.operation.track_id, action.operation.value_db) != (plan.target_track_id, plan.requested_gain_db):
            raise ValueError("DawCorrectionSuspended action differs from its plan")
        plan.validate_against(self.before)
        if self.accepted.state != self.before:
            raise ValueError("DawCorrectionSuspended before state is not the accepted reference's state")

    def _check_export(self) -> None:
        request, plan, write, apply = self.awaiting.request, self.plan, self.write, self.apply
        anchor = request.anchor
        if write.command_id != plan.command_id or apply.command_id != plan.command_id:
            raise ValueError("DawCorrectionSuspended write or apply answers another command than the plan's")
        if apply.admitted.action != self.decision.outcome.action:
            raise ValueError("DawCorrectionSuspended apply executes another action than the decision's")
        problem = daw_apply_export_problem(apply, request)
        if problem is not None:
            raise ValueError(f"DawCorrectionSuspended {problem}")
        if (anchor.source_id, anchor.basis) != (write.command_id, DawStateBasis.CONFIRMED_WRITE):
            raise ValueError("DawCorrectionSuspended export is not anchored on the confirmed write")
        if anchor.reading != write.after:
            raise ValueError("DawCorrectionSuspended anchor reading differs from the confirmed write")
        if (request.execution_id, request.candidate_revision, request.before_checkpoint) != (
            plan.execution_id,
            plan.candidate_revision,
            self.before.audio,
        ):
            raise ValueError("DawCorrectionSuspended export request differs from the plan or before state")
        # Every export setting of the plan (review fix 2026-11-19: timeline, format and render spec included).
        if (
            request.binding,
            request.profile,
            request.routing,
            request.outputs,
            request.timeline,
            request.format,
            request.render_spec,
            request.workspace,
        ) != (
            plan.binding,
            plan.profile,
            plan.routing,
            plan.outputs,
            plan.timeline,
            plan.format,
            plan.render_spec,
            plan.workspace,
        ):
            raise ValueError("DawCorrectionSuspended export request differs from the plan's export")


@dataclass(frozen=True)
class DawPlanReady:
    """The DAW preflight passed (Increment 2): the plan may proceed to inspection and the capability gate.

    Like ``MetadataReady`` for the offline path, it permits no write by itself: ``gate_daw_apply`` still decides.
    """

    plan: DawCorrectionPlan

    def __post_init__(self) -> None:
        require_instance(self.plan, DawCorrectionPlan, "DawPlanReady.plan")


# --- continuation (Increment 3, amended) ----------------------------------------------------------------------------


@dataclass(frozen=True)
class DawContinuationStarted:
    """The first event of an explicit continuation of the suspension ``checkpoint_event_id`` with ``manifest``.

    The pause (``resumed_at_utc - paused_at_utc``, wall-clock UTC) is informational and never charged to the budget.
    """

    run_id: RunId
    checkpoint_event_id: RunEventId
    export_id: str
    manifest: DawExportManifest
    paused_at_utc: datetime
    resumed_at_utc: datetime

    def __post_init__(self) -> None:
        require_instance(self.run_id, RunId, "DawContinuationStarted.run_id")
        require_instance(self.checkpoint_event_id, RunEventId, "DawContinuationStarted.checkpoint_event_id")
        _require_id(self.export_id, "DawContinuationStarted.export_id")
        require_instance(self.manifest, DawExportManifest, "DawContinuationStarted.manifest")
        require_utc(self.paused_at_utc, "DawContinuationStarted.paused_at_utc")
        require_utc(self.resumed_at_utc, "DawContinuationStarted.resumed_at_utc")
        if self.manifest.export_id != self.export_id:
            raise ValueError("DawContinuationStarted manifest answers another export")


class DawContinuationDisposition(Enum):
    ACCEPTED = "accepted"  # its candidate's acceptance was established; the run may continue or finish
    ENDED = "ended"  # the continuation ended the run without an established acceptance


@dataclass(frozen=True)
class DawContinuationFinished:
    """Completion evidence of one continuation (amendment A2), recorded exactly once.

    ``ACCEPTED`` is recorded after the accepted attempt's feedback and before any next decision round, so a later
    write and suspension always follow it; ``ENDED`` is the last event before the terminal snapshot.
    """

    run_id: RunId
    started_event_id: RunEventId
    export_id: str
    disposition: DawContinuationDisposition

    def __post_init__(self) -> None:
        require_instance(self.run_id, RunId, "DawContinuationFinished.run_id")
        require_instance(self.started_event_id, RunEventId, "DawContinuationFinished.started_event_id")
        _require_id(self.export_id, "DawContinuationFinished.export_id")
        require_instance(self.disposition, DawContinuationDisposition, "DawContinuationFinished.disposition")


class DawContinuationRefusalCode(Enum):
    ALREADY_CONTINUED = "already_continued"
    MANIFEST_CONFLICT = "manifest_conflict"
    CONTINUATION_IN_PROGRESS = "continuation_in_progress"
    CONTINUATION_INTERRUPTED = "continuation_interrupted"
    NOT_SUSPENDED = "not_suspended"
    REQUEST_MISMATCH = "request_mismatch"  # review fix 2026-11-19: not the run's recorded RunRequest


@dataclass(frozen=True)
class DawContinuationRefused:
    """An expected refusal of a continuation: nothing was dispatched or recorded.

    ``evidence_event_id`` names the completion evidence (``ALREADY_CONTINUED``) or the earlier start.
    """

    run_id: RunId
    export_id: str
    code: DawContinuationRefusalCode
    explanation: str
    evidence_event_id: RunEventId | None

    def __post_init__(self) -> None:
        require_instance(self.run_id, RunId, "DawContinuationRefused.run_id")
        require_text(self.export_id, "DawContinuationRefused.export_id")
        require_instance(self.code, DawContinuationRefusalCode, "DawContinuationRefused.code")
        require_text(self.explanation, "DawContinuationRefused.explanation")
        if self.evidence_event_id is not None:
            require_instance(self.evidence_event_id, RunEventId, "DawContinuationRefused.evidence_event_id")
