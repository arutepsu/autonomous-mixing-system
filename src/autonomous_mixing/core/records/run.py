"""Run configuration, run lifecycle and correction-attempt records."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from autonomous_mixing.core.records.actions import (
    ExecutionReceipt,
    ExecutionStatus,
    RollbackReceipt,
    RollbackStatus,
    derive_candidate_checkpoint,
    validate_receipt_against_checkpoint,
    validate_rollback_references,
)
from autonomous_mixing.core.records.budgets import BudgetLimits, BudgetUsage
from autonomous_mixing.core.records.context import MusicalContext
from autonomous_mixing.core.records.decision import ControllerIdentity
from autonomous_mixing.core.records.identifiers import ActionId, AttemptId, DecisionId, ProjectId, Revision, RunId
from autonomous_mixing.core.records.invariants import require_instance, require_text, require_utc
from autonomous_mixing.core.records.observation import AnalysisScope, AnalysisSpec, RenderSpec
from autonomous_mixing.core.records.project import ProjectSnapshot, ProjectState
from autonomous_mixing.core.records.verification import (
    AssessmentMode,
    ValidationCriteria,
    ValidationResult,
    ValidationVerdict,
)


@dataclass(frozen=True)
class RunConfiguration:
    """Fixed run configuration, embedded as immutable values rather than a reference to a mutable file."""

    scope: AnalysisScope
    analysis_spec: AnalysisSpec
    render_spec: RenderSpec
    context: MusicalContext
    criteria: ValidationCriteria
    controller: ControllerIdentity
    budget_limits: BudgetLimits

    def __post_init__(self) -> None:
        require_instance(self.scope, AnalysisScope, "RunConfiguration.scope")
        require_instance(self.analysis_spec, AnalysisSpec, "RunConfiguration.analysis_spec")
        require_instance(self.render_spec, RenderSpec, "RunConfiguration.render_spec")
        require_instance(self.context, MusicalContext, "RunConfiguration.context")
        require_instance(self.criteria, ValidationCriteria, "RunConfiguration.criteria")
        require_instance(self.controller, ControllerIdentity, "RunConfiguration.controller")
        require_instance(self.budget_limits, BudgetLimits, "RunConfiguration.budget_limits")

    def validate_against(self, snapshot: ProjectSnapshot) -> None:
        """Check structural references and scope containment.

        Condition scopes may be subsets of the run scope. This does not check scientific validity,
        audio alignment, threshold calibration or metric/unit compatibility.
        """
        self.scope.validate_against(snapshot)
        self.context.validate_against(snapshot)
        run_tracks = set(self.scope.track_ids)
        run_time = self.scope.time_range
        run_band = self.scope.frequency_range
        nyquist_hz = self.render_spec.nyquist_hz
        if run_band is not None and run_band.high_hz > nyquist_hz:
            raise ValueError(f"run scope frequency {run_band.high_hz} Hz exceeds render Nyquist {nyquist_hz} Hz")
        criteria = self.criteria
        for condition in (*criteria.targets, *criteria.improvements, *criteria.guards):
            name = condition.condition_id
            selector = condition.selector
            selector.scope.validate_against(snapshot)
            outside = sorted(str(t) for t in set(selector.scope.track_ids) - run_tracks)
            if outside:
                raise ValueError(f"condition {name} uses tracks outside the run scope: {', '.join(outside)}")
            time = selector.scope.time_range
            if time.start_seconds < run_time.start_seconds or time.end_seconds > run_time.end_seconds:
                raise ValueError(f"condition {name} time range lies outside the run scope")
            band = selector.scope.frequency_range
            if run_band is not None:
                if band is None:
                    raise ValueError(f"condition {name} needs an explicit frequency range within the run scope")
                if band.low_hz < run_band.low_hz or band.high_hz > run_band.high_hz:
                    raise ValueError(f"condition {name} frequency range lies outside the run scope")
            if band is not None and band.high_hz > nyquist_hz:
                raise ValueError(f"condition {name} frequency {band.high_hz} Hz exceeds render Nyquist {nyquist_hz} Hz")
            if selector.metric not in self.analysis_spec.metric_ids:
                raise ValueError(
                    f"condition {name} metric {selector.metric.name}@{selector.metric.version} "
                    "is not listed in the analysis spec"
                )


class RunState(Enum):
    INITIALIZING = "initializing"
    OBSERVING = "observing"
    DECIDING = "deciding"
    CHECKING_ACTION = "checking_action"
    PREPARING_EXECUTION = "preparing_execution"
    EXECUTING = "executing"
    RECONCILING = "reconciling"
    ANALYZING_RESULT = "analyzing_result"
    VALIDATING = "validating"
    RESTORING = "restoring"
    FINISHED = "finished"


class RunOutcome(Enum):
    SUCCEEDED = "succeeded"
    ABSTAINED = "abstained"
    LIMIT_REACHED = "limit_reached"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FinalProjectState(Enum):
    """Disposition of project outputs at the end of a run, reported separately from the run outcome."""

    UNCHANGED = "unchanged"
    ACCEPTED_EDITS_RETAINED = "accepted_edits_retained"
    RESTORED_TO_LAST_ACCEPTED = "restored_to_last_accepted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Run:
    """Immutable run record. FINISHED runs carry all terminal fields; active runs carry none."""

    run_id: RunId
    project_id: ProjectId
    configuration: RunConfiguration
    initial_revision: Revision
    created_at: datetime
    state: RunState
    usage: BudgetUsage
    outcome: RunOutcome | None
    final_project_state: FinalProjectState | None
    termination_reason: str | None

    def __post_init__(self) -> None:
        require_instance(self.run_id, RunId, "Run.run_id")
        require_instance(self.project_id, ProjectId, "Run.project_id")
        require_instance(self.configuration, RunConfiguration, "Run.configuration")
        require_instance(self.initial_revision, Revision, "Run.initial_revision")
        require_utc(self.created_at, "Run.created_at")
        require_instance(self.state, RunState, "Run.state")
        require_instance(self.usage, BudgetUsage, "Run.usage")

        if self.state is RunState.FINISHED:
            require_instance(self.outcome, RunOutcome, "Run.outcome")
            require_instance(self.final_project_state, FinalProjectState, "Run.final_project_state")
            require_text(self.termination_reason, "Run.termination_reason")
            if self.outcome is RunOutcome.SUCCEEDED and self.final_project_state is FinalProjectState.UNKNOWN:
                raise ValueError("SUCCEEDED run cannot have UNKNOWN final project state")
        elif (self.outcome, self.final_project_state, self.termination_reason) != (None, None, None):
            raise ValueError(f"run in state {self.state.name} must not have terminal fields")


class AttemptDisposition(Enum):
    PENDING = "pending"  # partial records while processing is in progress; not permission to execute
    ACCEPTED = "accepted"
    ROLLED_BACK = "rolled_back"  # restoration of the complete before state is verified
    FAILED = "failed"  # known unsuccessful attempt (not started or failed execution), nothing to restore
    UNRESOLVED = "unresolved"  # execution or restoration cannot be established


def _check_candidate_state(before: ProjectState, execution: ExecutionReceipt, candidate: ProjectState) -> None:
    """Structural consistency of a candidate with its receipt and before state.

    Which track's gain may change depends on the decision's action; that check lives in
    ``validate_attempt_references``.
    """
    if candidate.project_id != before.project_id:
        raise ValueError(f"candidate state project {candidate.project_id} does not match {before.project_id}")
    if candidate.revision != execution.candidate_revision:
        raise ValueError(
            f"candidate state revision {candidate.revision} does not match the receipt's "
            f"candidate revision {execution.candidate_revision}"
        )
    if candidate.snapshot.tracks != before.snapshot.tracks:
        raise ValueError("candidate state must keep the before state's complete track inventory and order")
    if candidate.revision in before.known_revisions:
        raise ValueError(f"candidate state revision {candidate.revision} reuses a locally known revision")
    expected_audio = derive_candidate_checkpoint(before.audio, execution, candidate.audio.checkpoint_id)
    if candidate.audio != expected_audio:
        raise ValueError(
            "candidate audio membership must equal the retained before artifacts followed by the receipt's candidates"
        )
    before_inputs = [(gain.track_id, gain.input_artifact) for gain in before.processing.track_gains]
    candidate_inputs = [(gain.track_id, gain.input_artifact) for gain in candidate.processing.track_gains]
    if candidate_inputs != before_inputs:
        raise ValueError("candidate processing must keep the same represented tracks and fixed input artifacts")


@dataclass(frozen=True)
class CorrectionAttempt:
    """One attempted correction: its decision, execution, candidate state, validation and rollback evidence.

    ``before_state`` is the complete saved state the attempt starts from. ``candidate_state`` is required
    exactly when the execution is CANDIDATE_READY (whatever the disposition) and must be consistent with
    the receipt and the before state. Construction checks consistency among the embedded records only.
    Run membership, decision identity, the action-specific gain change and criteria need external
    objects and are checked by ``validate_attempt_references``.
    """

    attempt_id: AttemptId
    run_id: RunId
    decision_id: DecisionId
    action_id: ActionId
    before_state: ProjectState
    execution: ExecutionReceipt | None
    candidate_state: ProjectState | None
    validation: ValidationResult | None
    rollback: RollbackReceipt | None
    disposition: AttemptDisposition

    def __post_init__(self) -> None:
        require_instance(self.attempt_id, AttemptId, "CorrectionAttempt.attempt_id")
        require_instance(self.run_id, RunId, "CorrectionAttempt.run_id")
        require_instance(self.decision_id, DecisionId, "CorrectionAttempt.decision_id")
        require_instance(self.action_id, ActionId, "CorrectionAttempt.action_id")
        require_instance(self.before_state, ProjectState, "CorrectionAttempt.before_state")
        if self.execution is not None:
            require_instance(self.execution, ExecutionReceipt, "CorrectionAttempt.execution")
        if self.candidate_state is not None:
            require_instance(self.candidate_state, ProjectState, "CorrectionAttempt.candidate_state")
        if self.validation is not None:
            require_instance(self.validation, ValidationResult, "CorrectionAttempt.validation")
        if self.rollback is not None:
            require_instance(self.rollback, RollbackReceipt, "CorrectionAttempt.rollback")
        require_instance(self.disposition, AttemptDisposition, "CorrectionAttempt.disposition")

        execution, before = self.execution, self.before_state
        status = execution.status if execution is not None else None
        if execution is not None:
            if execution.action_id != self.action_id:
                raise ValueError(f"execution action {execution.action_id} does not match attempt {self.action_id}")
            # Project, before revision, superseded membership and no candidate-ID reuse.
            validate_receipt_against_checkpoint(execution, before.audio)
        if status is ExecutionStatus.CANDIDATE_READY:
            if self.candidate_state is None:
                raise ValueError("a CANDIDATE_READY execution requires candidate_state")
            _check_candidate_state(before, execution, self.candidate_state)
        elif self.candidate_state is not None:
            raise ValueError("candidate_state is only allowed with a CANDIDATE_READY execution")
        if self.validation is not None:
            if status is not ExecutionStatus.CANDIDATE_READY:
                raise ValueError("validation requires a CANDIDATE_READY execution")
            # An INITIAL assessment describes the starting state and is never evidence for an attempt.
            if self.validation.mode is not AssessmentMode.ATTEMPT:
                raise ValueError("attempt validation must be an ATTEMPT assessment, not an INITIAL assessment")
        if self.rollback is not None:
            if execution is None:
                raise ValueError("rollback evidence requires an execution receipt")
            if self.rollback.execution_id != execution.execution_id:
                raise ValueError(
                    f"rollback refers to execution {self.rollback.execution_id}, not {execution.execution_id}"
                )
            validate_rollback_references(self.rollback, before)
        rollback_status = self.rollback.status if self.rollback is not None else None

        disposition = self.disposition
        if disposition is AttemptDisposition.ACCEPTED:
            if status is not ExecutionStatus.CANDIDATE_READY:
                raise ValueError("ACCEPTED attempt requires a CANDIDATE_READY execution")
            if self.validation is None or self.validation.verdict is not ValidationVerdict.ACCEPTED:
                raise ValueError("ACCEPTED attempt requires an ACCEPTED validation verdict")
            if self.rollback is not None:
                raise ValueError("ACCEPTED attempt must not carry rollback evidence")
        elif disposition is AttemptDisposition.ROLLED_BACK:
            if rollback_status is not RollbackStatus.VERIFIED:
                raise ValueError("ROLLED_BACK attempt requires a VERIFIED rollback receipt")
        elif disposition is AttemptDisposition.FAILED:
            if status not in (ExecutionStatus.NOT_STARTED, ExecutionStatus.FAILED):
                raise ValueError("FAILED attempt requires a NOT_STARTED or FAILED execution")
            if self.rollback is not None:
                raise ValueError("FAILED attempt must not carry rollback evidence")
        elif disposition is AttemptDisposition.UNRESOLVED:
            unsuccessful_rollback = rollback_status in (RollbackStatus.FAILED, RollbackStatus.UNCERTAIN)
            if status is not ExecutionStatus.UNCERTAIN and not unsuccessful_rollback:
                raise ValueError("UNRESOLVED attempt requires uncertain execution or failed/uncertain rollback")
