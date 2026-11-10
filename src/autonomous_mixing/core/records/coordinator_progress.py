"""Immutable internal progress and explicit correction phases for the serial coordinator.

These values describe observed logical facts, not durable storage or physical restoration. Runtime
collaborators and the exception-reporting observer remain function parameters, never record fields.
"""

from dataclasses import dataclass

from autonomous_mixing.core.records.acceptance import AcceptanceCommitted, AcceptedReference
from autonomous_mixing.core.records.actions import Action, ExecutionReceipt
from autonomous_mixing.core.records.admission import StemBinding
from autonomous_mixing.core.records.budgets import BudgetUsage
from autonomous_mixing.core.records.coordinator_runtime import ClockReading, RunRequest
from autonomous_mixing.core.records.decision import Decision, ProposeAction
from autonomous_mixing.core.records.diagnosis import DiagnosisReport
from autonomous_mixing.core.records.feedback import DecisionFeedback
from autonomous_mixing.core.records.identifiers import ArtifactId, AttemptId, DecisionCallId, ExecutionId, Revision
from autonomous_mixing.core.records.integrity import ArtifactRootBinding
from autonomous_mixing.core.records.invariants import require_instance, require_text, require_tuple_of
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.rendering import RenderPlan
from autonomous_mixing.core.records.run_trace import RunTraceEvent
from autonomous_mixing.core.records.termination import TerminationReason
from autonomous_mixing.core.records.verification import VerificationResult
from autonomous_mixing.core.records.verification_assessment import VerificationAssessment
from autonomous_mixing.core.records.verification_evidence import StateEvidence

Stop = tuple[TerminationReason, str]


@dataclass(frozen=True)
class RuntimeProgress:
    request: RunRequest
    initial: AcceptedReference
    first: ClockReading
    last: ClockReading
    usage: BudgetUsage
    known: tuple[RunTraceEvent, ...]
    unacknowledged: tuple[RunTraceEvent, ...]
    commits: tuple[AcceptanceCommitted, ...]
    recording_ok: bool
    accepted: AcceptedReference
    authority_known: bool
    verified: AcceptedReference | None
    restored_last: bool
    tokens: frozenset[str]
    history: tuple[DecisionFeedback, ...]
    stems: tuple[StemBinding, ...]
    mix_id: ArtifactId
    roots: tuple[ArtifactRootBinding, ...]
    observations: tuple[StateEvidence, ...]
    diagnosed: tuple[Revision, DiagnosisReport] | None

    def __post_init__(self) -> None:
        for name, cls in (
            ("request", RunRequest),
            ("initial", AcceptedReference),
            ("first", ClockReading),
            ("last", ClockReading),
            ("usage", BudgetUsage),
            ("accepted", AcceptedReference),
            ("mix_id", ArtifactId),
        ):
            require_instance(getattr(self, name), cls, f"RuntimeProgress.{name}")
        for name, cls in (
            ("known", RunTraceEvent),
            ("unacknowledged", RunTraceEvent),
            ("commits", AcceptanceCommitted),
            ("history", DecisionFeedback),
            ("stems", StemBinding),
            ("roots", ArtifactRootBinding),
            ("observations", StateEvidence),
        ):
            require_tuple_of(getattr(self, name), cls, f"RuntimeProgress.{name}", nonempty=False, distinct=False)
        for name in ("recording_ok", "authority_known", "restored_last"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"RuntimeProgress.{name} must be a bool")
        if self.verified is not None:
            require_instance(self.verified, AcceptedReference, "RuntimeProgress.verified")
        if not isinstance(self.tokens, frozenset):
            raise ValueError("RuntimeProgress.tokens must be a frozenset")
        for token in self.tokens:
            require_text(token, "RuntimeProgress.tokens element")
        if self.diagnosed is not None:
            if not isinstance(self.diagnosed, tuple) or len(self.diagnosed) != 2:
                raise ValueError("RuntimeProgress.diagnosed must be a revision/report pair")
            require_instance(self.diagnosed[0], Revision, "RuntimeProgress.diagnosed revision")
            require_instance(self.diagnosed[1], DiagnosisReport, "RuntimeProgress.diagnosed report")


@dataclass(frozen=True)
class PreparedCorrection:
    call_id: DecisionCallId
    before: ProjectState
    decision: Decision
    attempt_id: AttemptId
    plan: RenderPlan

    def __post_init__(self) -> None:
        for name, cls in (
            ("call_id", DecisionCallId),
            ("before", ProjectState),
            ("decision", Decision),
            ("attempt_id", AttemptId),
            ("plan", RenderPlan),
        ):
            require_instance(getattr(self, name), cls, f"PreparedCorrection.{name}")
        if not isinstance(self.decision.outcome, ProposeAction):
            raise ValueError("PreparedCorrection requires a proposed action")
        if (self.before.project_id, self.before.revision) != (self.plan.project_id, self.plan.before_revision):
            raise ValueError("PreparedCorrection before state differs from its plan")
        if (self.action.project_id, self.action.expected_revision) != (self.before.project_id, self.before.revision):
            raise ValueError("PreparedCorrection action differs from its before state")
        if (self.action.operation.track_id, self.action.operation.value_db) != (
            self.plan.target_track_id,
            self.plan.requested_gain_db,
        ):
            raise ValueError("PreparedCorrection action differs from its render operation")

    @property
    def action(self) -> Action:
        return self.decision.outcome.action

    @property
    def execution_id(self) -> ExecutionId:
        return self.plan.execution_id

    @property
    def candidate_revision(self) -> Revision:
        return self.plan.candidate.revision


@dataclass(frozen=True)
class AssessedCorrection(PreparedCorrection):
    receipt: ExecutionReceipt
    candidate_state: ProjectState
    assessment: VerificationAssessment
    verification: VerificationResult

    def __post_init__(self) -> None:
        super().__post_init__()
        for name, cls in (
            ("receipt", ExecutionReceipt),
            ("candidate_state", ProjectState),
            ("assessment", VerificationAssessment),
            ("verification", VerificationResult),
        ):
            require_instance(getattr(self, name), cls, f"AssessedCorrection.{name}")
        if self.assessment.before is None or self.assessment.initial is None:
            raise ValueError("AssessedCorrection requires before and initial evidence")
        if self.assessment.assessed.state != self.candidate_state or self.assessment.before.state != self.before:
            raise ValueError("AssessedCorrection assessment differs from its candidate or before state")
        if (
            self.receipt.execution_id,
            self.receipt.action_id,
            self.receipt.project_id,
            self.receipt.before_revision,
            self.receipt.candidate_revision,
        ) != (
            self.execution_id,
            self.action.action_id,
            self.before.project_id,
            self.before.revision,
            self.candidate_revision,
        ):
            raise ValueError("AssessedCorrection receipt differs from its prepared correction")

    @property
    def bundles(self) -> tuple[StateEvidence, ...]:
        return (self.assessment.assessed, self.assessment.before, self.assessment.initial)
