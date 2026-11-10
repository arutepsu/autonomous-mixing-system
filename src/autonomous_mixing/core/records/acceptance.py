"""Candidate acceptance events and acceptance-journal outcomes (attempt contract §29).

An ``AcceptanceEvent`` records everything that justified accepting one candidate: the expected accepted
reference, the action, its CANDIDATE_READY execution, the verification request and its ACCEPTED ATTEMPT
result. Constructors check embedded cross-links only. Whether the candidate state follows from the action
and whether the verdict follows from the evidence is checked by ``core.loop.acceptance``; run, attempt and
decision membership, authoritative criteria and rendered-file integrity need coordinator evidence.

``AcceptedReference.sequence`` counts acceptances (zero for the run's initial accepted state); it is not a
journal's all-event sequence number. Commit and query outcomes report one event's commit, never a claim
about the journal's latest head.
"""

from dataclasses import dataclass

from autonomous_mixing.core.records.actions import Action, ExecutionReceipt, ExecutionStatus
from autonomous_mixing.core.records.identifiers import AcceptanceId, AttemptId, DecisionId, RunId
from autonomous_mixing.core.records.invariants import require_count, require_instance, require_text
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.verification import AssessmentMode, Verdict, VerificationResult
from autonomous_mixing.core.records.verification_assessment import VerificationAssessment


@dataclass(frozen=True)
class AcceptedReference:
    """An accepted project state and its acceptance sequence (zero for the initial state)."""

    state: ProjectState
    sequence: int

    def __post_init__(self) -> None:
        require_instance(self.state, ProjectState, "AcceptedReference.state")
        require_count(self.sequence, "AcceptedReference.sequence", minimum=0)


@dataclass(frozen=True)
class AcceptanceEvent:
    """One proposed acceptance of the candidate ``assessment.assessed.state`` over ``expected``."""

    acceptance_id: AcceptanceId
    run_id: RunId
    attempt_id: AttemptId
    decision_id: DecisionId
    expected: AcceptedReference
    action: Action
    execution: ExecutionReceipt
    assessment: VerificationAssessment
    verification: VerificationResult

    def __post_init__(self) -> None:
        require_instance(self.acceptance_id, AcceptanceId, "AcceptanceEvent.acceptance_id")
        require_instance(self.run_id, RunId, "AcceptanceEvent.run_id")
        require_instance(self.attempt_id, AttemptId, "AcceptanceEvent.attempt_id")
        require_instance(self.decision_id, DecisionId, "AcceptanceEvent.decision_id")
        require_instance(self.expected, AcceptedReference, "AcceptanceEvent.expected")
        require_instance(self.action, Action, "AcceptanceEvent.action")
        require_instance(self.execution, ExecutionReceipt, "AcceptanceEvent.execution")
        require_instance(self.assessment, VerificationAssessment, "AcceptanceEvent.assessment")
        require_instance(self.verification, VerificationResult, "AcceptanceEvent.verification")

        expected = self.expected.state
        assessment, verification = self.assessment, self.verification
        if assessment.mode is not AssessmentMode.ATTEMPT:
            raise ValueError("acceptance requires an ATTEMPT assessment")
        if assessment.before is None or assessment.before.state != expected:
            raise ValueError("assessment before evidence must carry exactly the expected accepted state")
        if verification.mode is not AssessmentMode.ATTEMPT or verification.verdict is not Verdict.ACCEPTED:
            raise ValueError("acceptance requires an ACCEPTED ATTEMPT verification result")
        if verification.validation_result_id != assessment.result_id:
            raise ValueError(
                f"verification result {verification.validation_result_id} is not the assessment's {assessment.result_id}"
            )
        criteria = assessment.criteria
        if (verification.criteria_id, verification.criteria_version) != (criteria.criteria_id, criteria.version):
            raise ValueError("verification criteria reference differs from the assessment's criteria")

        candidate = assessment.assessed.state
        action, execution = self.action, self.execution
        projects = {expected.project_id, candidate.project_id, action.project_id, execution.project_id}
        if len(projects) != 1:
            raise ValueError(f"expected, candidate, action and execution name different projects: {sorted(map(str, projects))}")  # fmt: skip
        if action.expected_revision != expected.revision:
            raise ValueError(f"action expects revision {action.expected_revision}, not {expected.revision}")
        if execution.before_revision != expected.revision:
            raise ValueError(f"execution before revision {execution.before_revision} is not {expected.revision}")
        if execution.status is not ExecutionStatus.CANDIDATE_READY:
            raise ValueError(f"acceptance requires a CANDIDATE_READY execution, got {execution.status.name}")
        if execution.action_id != action.action_id:
            raise ValueError(f"execution is for action {execution.action_id}, not {action.action_id}")
        if execution.candidate_revision != candidate.revision:
            raise ValueError(
                f"execution candidate revision {execution.candidate_revision} is not the assessed {candidate.revision}"
            )
        if candidate.revision == expected.revision:
            raise ValueError("candidate revision must differ from the expected accepted revision")


def _require_commit(event: AcceptanceEvent, accepted: AcceptedReference, owner: str) -> None:
    require_instance(event, AcceptanceEvent, f"{owner}.event")
    require_instance(accepted, AcceptedReference, f"{owner}.accepted")
    if accepted.state != event.assessment.assessed.state:
        raise ValueError(f"{owner}.accepted state must be the event's candidate state")
    if accepted.sequence != event.expected.sequence + 1:
        raise ValueError(f"{owner}.accepted sequence must be {event.expected.sequence + 1}")


@dataclass(frozen=True)
class AcceptanceCommitted:
    """The journal recorded ``event`` and advanced the accepted reference to ``accepted``."""

    event: AcceptanceEvent
    accepted: AcceptedReference

    def __post_init__(self) -> None:
        _require_commit(self.event, self.accepted, "AcceptanceCommitted")


@dataclass(frozen=True)
class AcceptanceNotCommitted:
    """The journal refused the event and changed nothing (reused ID, wrong run or stale expected reference)."""

    acceptance_id: AcceptanceId
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.acceptance_id, AcceptanceId, "AcceptanceNotCommitted.acceptance_id")
        require_text(self.explanation, "AcceptanceNotCommitted.explanation")


@dataclass(frozen=True)
class AcceptanceCommitUnknown:
    """No claim either way about whether the event was stored."""

    acceptance_id: AcceptanceId
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.acceptance_id, AcceptanceId, "AcceptanceCommitUnknown.acceptance_id")
        require_text(self.explanation, "AcceptanceCommitUnknown.explanation")


AcceptanceCommitResult = AcceptanceCommitted | AcceptanceNotCommitted | AcceptanceCommitUnknown


@dataclass(frozen=True)
class AcceptanceQuery:
    """Asks whether one acceptance event of one run was committed."""

    run_id: RunId
    acceptance_id: AcceptanceId

    def __post_init__(self) -> None:
        require_instance(self.run_id, RunId, "AcceptanceQuery.run_id")
        require_instance(self.acceptance_id, AcceptanceId, "AcceptanceQuery.acceptance_id")


@dataclass(frozen=True)
class AcceptanceFound:
    """The event's historical commit; not a claim that ``accepted`` is still the journal's latest head."""

    event: AcceptanceEvent
    accepted: AcceptedReference

    def __post_init__(self) -> None:
        _require_commit(self.event, self.accepted, "AcceptanceFound")


@dataclass(frozen=True)
class AcceptanceAbsent:
    run_id: RunId
    acceptance_id: AcceptanceId

    def __post_init__(self) -> None:
        require_instance(self.run_id, RunId, "AcceptanceAbsent.run_id")
        require_instance(self.acceptance_id, AcceptanceId, "AcceptanceAbsent.acceptance_id")


@dataclass(frozen=True)
class AcceptanceAuthorityUnknown:
    """The journal cannot establish whether the event was committed."""

    run_id: RunId
    acceptance_id: AcceptanceId
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.run_id, RunId, "AcceptanceAuthorityUnknown.run_id")
        require_instance(self.acceptance_id, AcceptanceId, "AcceptanceAuthorityUnknown.acceptance_id")
        require_text(self.explanation, "AcceptanceAuthorityUnknown.explanation")


AcceptanceQueryResult = AcceptanceFound | AcceptanceAbsent | AcceptanceAuthorityUnknown
