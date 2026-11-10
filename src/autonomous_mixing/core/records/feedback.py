"""Compact decision feedback: an ordered prefix of prior decision calls (attempt contract, sections 13-15).

These lower-level records import no decision, admission or run records and embed no ``Situation``, full
state, run record or provider transcript. They summarize recorded outcomes; their construction does not
show that a call, execution, acceptance or restoration actually happened. The complete audit trail is
separate. Historical observations keep the state revision they were recorded for; they are never
re-validated against a later state's checkpoint.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

from autonomous_mixing.core.records.actions import Action, ExecutionStatus, RollbackStatus
from autonomous_mixing.core.records.budgets import BudgetUsage
from autonomous_mixing.core.records.identifiers import (
    AttemptId,
    DecisionCallId,
    DecisionId,
    ExecutionId,
    ObservationId,
    ProjectId,
    Revision,
    RunId,
)
from autonomous_mixing.core.records.invariants import require_count, require_instance, require_text, require_tuple_of
from autonomous_mixing.core.records.observation import Observation
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.results import PortFailure
from autonomous_mixing.core.records.verification import AssessmentMode, Verdict, VerificationResult


class ActionFeedbackDisposition(Enum):
    """Recorded outcome of a proposed action; a summary label, not a run or attempt state."""

    NOT_EXECUTED = "not_executed"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    UNRESOLVED = "unresolved"


class FeedbackStage(Enum):
    """The loop stage at which an action's recorded outcome was established."""

    EVIDENCE_GATE = "evidence_gate"
    ADMISSION = "admission"
    PREFLIGHT = "preflight"
    PREPARATION = "preparation"
    RENDER = "render"
    VERIFICATION = "verification"
    RECOVERY = "recovery"
    ACCEPTANCE = "acceptance"
    RECORDING = "recording"


_MISSING_CONTEXT_CODE = "missing_context"

_STAGES = {
    ActionFeedbackDisposition.NOT_EXECUTED: frozenset(
        {
            FeedbackStage.EVIDENCE_GATE,
            FeedbackStage.ADMISSION,
            FeedbackStage.PREFLIGHT,
            FeedbackStage.PREPARATION,
            FeedbackStage.RENDER,
            FeedbackStage.RECORDING,
        }
    ),
    ActionFeedbackDisposition.REJECTED: frozenset(
        {FeedbackStage.EVIDENCE_GATE, FeedbackStage.ADMISSION, FeedbackStage.PREFLIGHT}
    ),
    ActionFeedbackDisposition.ACCEPTED: frozenset({FeedbackStage.ACCEPTANCE}),
    ActionFeedbackDisposition.ROLLED_BACK: frozenset({FeedbackStage.RECOVERY}),
    ActionFeedbackDisposition.FAILED: frozenset(
        {FeedbackStage.PREPARATION, FeedbackStage.RENDER, FeedbackStage.RECORDING}
    ),
    ActionFeedbackDisposition.UNRESOLVED: frozenset(
        {
            FeedbackStage.PREPARATION,
            FeedbackStage.RENDER,
            FeedbackStage.VERIFICATION,
            FeedbackStage.RECOVERY,
            FeedbackStage.ACCEPTANCE,
            FeedbackStage.RECORDING,
        }
    ),
}


def _require_optional(value: Any, cls: type, field: str) -> None:
    if value is not None:
        require_instance(value, cls, field)


def _require_texts(value: Any, field: str) -> tuple[str, ...]:
    """A nonempty tuple of distinct non-blank strings, kept exactly as supplied."""
    require_tuple_of(value, str, field, nonempty=True, distinct=True)
    for item in value:
        require_text(item, f"{field} element")
    return value


def _require_consistent_observations(
    observations: tuple["ObservationFeedback", ...], seen: dict[ObservationId, Observation], field: str
) -> None:
    """Unique (state revision, observation ID) pairs; one ID always denotes identical contents."""
    pairs: set[tuple[Revision, ObservationId]] = set()
    for item in observations:
        observation_id = item.observation.observation_id
        pair = (item.state_revision, observation_id)
        if pair in pairs:
            raise ValueError(f"{field} has a duplicate observation {observation_id} at revision {item.state_revision}")
        pairs.add(pair)
        recorded = seen.setdefault(observation_id, item.observation)
        if recorded != item.observation:
            raise ValueError(f"{field}: observation ID {observation_id} is used with different contents")


@dataclass(frozen=True)
class ObservationFeedback:
    """A historical observation and the state revision it was recorded for.

    The association is declared, not proven: membership in that historical checkpoint is retained by the
    full trace. It is never re-validated against a later state.
    """

    state_revision: Revision
    observation: Observation

    def __post_init__(self) -> None:
        require_instance(self.state_revision, Revision, "ObservationFeedback.state_revision")
        require_instance(self.observation, Observation, "ObservationFeedback.observation")


@dataclass(frozen=True)
class ActionFeedback:
    """Compact summary of one proposed action and what is recorded to have happened to it.

    Construction rejects shapes that contradict the disposition (for example an ACCEPTED action without a
    ready candidate and ACCEPTED verification, or an UNRESOLVED outcome with neither an uncertain execution
    nor a failed/uncertain rollback). A verified rollback does not resolve an uncertain execution: restoring
    the saved reference need not account for every effect of the interrupted execution. Construction does
    not show that the execution, verification or restoration took place or was computed correctly.
    """

    action: Action
    attempt_id: AttemptId | None
    execution_id: ExecutionId | None
    disposition: ActionFeedbackDisposition
    stage: FeedbackStage
    execution_status: ExecutionStatus | None
    verification: VerificationResult | None
    rollback_status: RollbackStatus | None
    reason_code: str
    explanation: str
    observations: tuple[ObservationFeedback, ...]

    def __post_init__(self) -> None:
        require_instance(self.action, Action, "ActionFeedback.action")
        _require_optional(self.attempt_id, AttemptId, "ActionFeedback.attempt_id")
        _require_optional(self.execution_id, ExecutionId, "ActionFeedback.execution_id")
        require_instance(self.disposition, ActionFeedbackDisposition, "ActionFeedback.disposition")
        require_instance(self.stage, FeedbackStage, "ActionFeedback.stage")
        _require_optional(self.execution_status, ExecutionStatus, "ActionFeedback.execution_status")
        _require_optional(self.verification, VerificationResult, "ActionFeedback.verification")
        _require_optional(self.rollback_status, RollbackStatus, "ActionFeedback.rollback_status")
        require_text(self.reason_code, "ActionFeedback.reason_code")
        require_text(self.explanation, "ActionFeedback.explanation")
        require_tuple_of(
            self.observations, ObservationFeedback, "ActionFeedback.observations", nonempty=False, distinct=False
        )

        disposition = self.disposition
        if self.attempt_id is None and disposition is not ActionFeedbackDisposition.NOT_EXECUTED:
            raise ValueError(f"ActionFeedback with disposition {disposition.name} requires an AttemptId")
        if (self.execution_id is None) != (self.execution_status is None):
            raise ValueError("ActionFeedback execution_id and execution_status must be both present or both absent")
        if self.rollback_status is not None and self.execution_id is None:
            raise ValueError("ActionFeedback rollback_status requires an execution")
        if self.verification is not None:
            if self.verification.mode is not AssessmentMode.ATTEMPT:
                raise ValueError("ActionFeedback verification must be an ATTEMPT assessment")
            if self.execution_status is not ExecutionStatus.CANDIDATE_READY:
                raise ValueError("ActionFeedback verification requires a CANDIDATE_READY execution")
        _require_consistent_observations(self.observations, {}, "ActionFeedback.observations")
        if self.verification is not None:
            copied = {item.observation.observation_id for item in self.observations}
            missing = sorted(
                {str(e) for check in self.verification.checks for e in check.evidence_ids if e not in copied}
            )
            if missing:
                raise ValueError(f"ActionFeedback verification cites observations not copied: {', '.join(missing)}")
        self._require_disposition_shape()

    def _require_disposition_shape(self) -> None:
        disposition, status, rollback = self.disposition, self.execution_status, self.rollback_status
        if self.stage not in _STAGES[disposition]:
            raise ValueError(f"ActionFeedback stage {self.stage.name} does not fit disposition {disposition.name}")
        match disposition:
            case ActionFeedbackDisposition.NOT_EXECUTED | ActionFeedbackDisposition.REJECTED:
                valid = status in (None, ExecutionStatus.NOT_STARTED) and self.verification is None and rollback is None
            case ActionFeedbackDisposition.ACCEPTED:
                valid = (
                    status is ExecutionStatus.CANDIDATE_READY
                    and self.verification is not None
                    and self.verification.verdict is Verdict.ACCEPTED
                    and rollback is None
                )
            case ActionFeedbackDisposition.ROLLED_BACK:
                valid = status is not None and rollback is RollbackStatus.VERIFIED
            case ActionFeedbackDisposition.FAILED:
                valid = (
                    status in (ExecutionStatus.NOT_STARTED, ExecutionStatus.FAILED)
                    and self.verification is None
                    and rollback is None
                )
            case ActionFeedbackDisposition.UNRESOLVED:
                # Uncertain execution stays unresolved even after a verified rollback of the saved state.
                valid = status is ExecutionStatus.UNCERTAIN or rollback in (
                    RollbackStatus.FAILED,
                    RollbackStatus.UNCERTAIN,
                )
            case _:
                raise ValueError(f"unknown disposition {disposition!r}")
        if not valid:
            raise ValueError(
                f"ActionFeedback disposition {disposition.name} contradicts execution "
                f"{None if status is None else status.name}, rollback {None if rollback is None else rollback.name} "
                f"or verification {None if self.verification is None else self.verification.verdict.name}"
            )


@dataclass(frozen=True)
class NoChangeFeedback:
    """A parsed deliberate recommendation to keep the accepted state; not success and not a zero-gain action."""

    explanation: str

    def __post_init__(self) -> None:
        require_text(self.explanation, "NoChangeFeedback.explanation")


@dataclass(frozen=True)
class AbstentionFeedback:
    """A parsed abstention with its recorded reason code; missing context uses ``ContextRequestFeedback``."""

    reason_code: str
    explanation: str

    def __post_init__(self) -> None:
        require_text(self.reason_code, "AbstentionFeedback.reason_code")
        require_text(self.explanation, "AbstentionFeedback.explanation")
        if self.reason_code == _MISSING_CONTEXT_CODE:
            raise ValueError("a missing-context abstention must be recorded as ContextRequestFeedback with questions")


@dataclass(frozen=True)
class ContextRequestFeedback:
    """A parsed request for specific missing context; it ends the run and is not a paused run."""

    questions: tuple[str, ...]
    explanation: str

    def __post_init__(self) -> None:
        _require_texts(self.questions, "ContextRequestFeedback.questions")
        require_text(self.explanation, "ContextRequestFeedback.explanation")


@dataclass(frozen=True)
class InvalidResponseFeedback:
    """The provider returned something that is not a valid decision; no decision was parsed."""

    reason_code: str
    explanation: str

    def __post_init__(self) -> None:
        require_text(self.reason_code, "InvalidResponseFeedback.reason_code")
        require_text(self.explanation, "InvalidResponseFeedback.explanation")


@dataclass(frozen=True)
class PortFailureFeedback:
    """The decision port returned an expected failure."""

    failure: PortFailure

    def __post_init__(self) -> None:
        require_instance(self.failure, PortFailure, "PortFailureFeedback.failure")


@dataclass(frozen=True)
class NotDispatchedFeedback:
    """A charged decision slot whose provider call was never invoked; not a failure returned by the provider."""

    reason_code: str
    explanation: str

    def __post_init__(self) -> None:
        require_text(self.reason_code, "NotDispatchedFeedback.reason_code")
        require_text(self.explanation, "NotDispatchedFeedback.explanation")


FeedbackOutcome = (
    ActionFeedback
    | NoChangeFeedback
    | AbstentionFeedback
    | ContextRequestFeedback
    | InvalidResponseFeedback
    | PortFailureFeedback
    | NotDispatchedFeedback
)

_PARSED_NON_ACTION = (NoChangeFeedback, AbstentionFeedback, ContextRequestFeedback)
_UNPARSED = (InvalidResponseFeedback, PortFailureFeedback, NotDispatchedFeedback)


@dataclass(frozen=True)
class DecisionFeedback:
    """One completed decision call: its charged slot, state references, usage and recorded outcome.

    ``usage.decision_calls`` equals ``sequence`` (charged slots of the prefix, not proof of remote calls).
    A valid ``decision_id`` is present exactly for parsed decisions. An ACCEPTED action selects its
    candidate; other known outcomes select the before state; an UNRESOLVED action selects the before
    state or nothing, never a tentative candidate.
    """

    call_id: DecisionCallId
    sequence: int
    run_id: RunId
    project_id: ProjectId
    before_revision: Revision
    candidate_revision: Revision | None
    selected_revision: Revision | None
    decision_id: DecisionId | None
    usage: BudgetUsage
    outcome: FeedbackOutcome

    def __post_init__(self) -> None:
        require_instance(self.call_id, DecisionCallId, "DecisionFeedback.call_id")
        require_count(self.sequence, "DecisionFeedback.sequence", minimum=1)
        require_instance(self.run_id, RunId, "DecisionFeedback.run_id")
        require_instance(self.project_id, ProjectId, "DecisionFeedback.project_id")
        require_instance(self.before_revision, Revision, "DecisionFeedback.before_revision")
        _require_optional(self.candidate_revision, Revision, "DecisionFeedback.candidate_revision")
        _require_optional(self.selected_revision, Revision, "DecisionFeedback.selected_revision")
        _require_optional(self.decision_id, DecisionId, "DecisionFeedback.decision_id")
        require_instance(self.usage, BudgetUsage, "DecisionFeedback.usage")
        require_instance(self.outcome, (ActionFeedback, *_PARSED_NON_ACTION, *_UNPARSED), "DecisionFeedback.outcome")
        if self.usage.decision_calls != self.sequence:
            raise ValueError(
                f"DecisionFeedback usage.decision_calls {self.usage.decision_calls} must equal sequence {self.sequence}"
            )
        name = f"decision call {self.call_id}"
        before, candidate, selected = self.before_revision, self.candidate_revision, self.selected_revision

        if isinstance(self.outcome, ActionFeedback):
            feedback = self.outcome
            if self.decision_id is None:
                raise ValueError(f"{name} with an action outcome requires a DecisionId")
            if feedback.action.project_id != self.project_id:
                raise ValueError(f"{name}: action project {feedback.action.project_id} is not {self.project_id}")
            if feedback.action.expected_revision != before:
                raise ValueError(
                    f"{name}: action expects revision {feedback.action.expected_revision}, before revision is {before}"
                )
            if feedback.execution_status is ExecutionStatus.CANDIDATE_READY:
                if candidate is None or candidate == before:
                    raise ValueError(
                        f"{name}: a CANDIDATE_READY execution needs a candidate revision other than before"
                    )
            elif candidate is not None:
                raise ValueError(f"{name}: candidate revision is set without a CANDIDATE_READY execution")
            match feedback.disposition:
                case ActionFeedbackDisposition.ACCEPTED:
                    valid = selected == candidate
                case ActionFeedbackDisposition.UNRESOLVED:
                    valid = selected is None or selected == before
                case _:
                    valid = selected == before
            if not valid:
                raise ValueError(
                    f"{name}: selected revision {selected} does not fit disposition {feedback.disposition.name}"
                )
            return

        if isinstance(self.outcome, _PARSED_NON_ACTION):
            if self.decision_id is None:
                raise ValueError(f"{name}: a parsed {type(self.outcome).__name__} requires a DecisionId")
        elif self.decision_id is not None:
            raise ValueError(f"{name}: {type(self.outcome).__name__} describes no valid decision and has no DecisionId")
        if candidate is not None:
            raise ValueError(f"{name}: a non-action outcome has no candidate revision")
        if selected != before:
            raise ValueError(f"{name}: a non-action outcome must select its before revision")


_USAGE_FIELDS = ("decision_calls", "attempts", "render_calls", "recovery_operations", "elapsed_seconds")


def _require_usage_not_below(later: BudgetUsage, earlier: BudgetUsage, field: str) -> None:
    lower = [name for name in _USAGE_FIELDS if getattr(later, name) < getattr(earlier, name)]
    if lower:
        raise ValueError(f"{field} usage decreases in: {', '.join(lower)}")


def _require_unique(values: list[Any], label: str) -> None:
    seen: set[Any] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"DecisionHistory reuses {label} {value}")
        seen.add(value)


@dataclass(frozen=True)
class DecisionHistory:
    """Ordered complete prefix of completed decision calls in one run; possibly empty.

    Entries are in exact sequence 1..n (never sorted or renumbered). Each entry continues from the previous
    selected revision, so nothing may follow an unknown selection; candidate revisions are fresh within the
    visible prefix; usage never decreases (overruns are kept). Observations name a revision visible by
    their entry, and one observation ID always denotes identical contents. This checks data consistency,
    not actual execution, retry or termination policy, scientific evidence or physical restoration.
    """

    run_id: RunId
    project_id: ProjectId
    entries: tuple[DecisionFeedback, ...]

    def __post_init__(self) -> None:
        require_instance(self.run_id, RunId, "DecisionHistory.run_id")
        require_instance(self.project_id, ProjectId, "DecisionHistory.project_id")
        require_tuple_of(self.entries, DecisionFeedback, "DecisionHistory.entries", nonempty=False, distinct=False)

        known: set[Revision] = set()
        observations: dict[ObservationId, Observation] = {}
        previous: DecisionFeedback | None = None
        for position, entry in enumerate(self.entries, start=1):
            name = f"DecisionHistory entry {position} ({entry.call_id})"
            if entry.run_id != self.run_id:
                raise ValueError(f"{name} belongs to run {entry.run_id}, not {self.run_id}")
            if entry.project_id != self.project_id:
                raise ValueError(f"{name} belongs to project {entry.project_id}, not {self.project_id}")
            if entry.sequence != position:
                raise ValueError(f"{name} has sequence {entry.sequence}; expected {position}")
            if previous is not None:
                if previous.selected_revision is None:
                    raise ValueError(f"{name} follows an entry whose selected revision is unknown")
                if entry.before_revision != previous.selected_revision:
                    raise ValueError(
                        f"{name} before revision {entry.before_revision} does not continue from the previous "
                        f"selected revision {previous.selected_revision}"
                    )
                _require_usage_not_below(entry.usage, previous.usage, name)
            if entry.candidate_revision is not None and entry.candidate_revision in known:
                raise ValueError(f"{name} reuses candidate revision {entry.candidate_revision}")
            known |= {r for r in (entry.before_revision, entry.candidate_revision, entry.selected_revision) if r}
            if isinstance(entry.outcome, ActionFeedback):
                for item in entry.outcome.observations:
                    if item.state_revision not in known:
                        raise ValueError(
                            f"{name} cites observation {item.observation.observation_id} at revision "
                            f"{item.state_revision}, which is not visible by this entry"
                        )
                _require_consistent_observations(entry.outcome.observations, observations, name)
            previous = entry

        actions = [e.outcome for e in self.entries if isinstance(e.outcome, ActionFeedback)]
        _require_unique([e.call_id for e in self.entries], "call ID")
        _require_unique([e.decision_id for e in self.entries if e.decision_id is not None], "decision ID")
        _require_unique([a.action.action_id for a in actions], "action ID")
        _require_unique([a.attempt_id for a in actions if a.attempt_id is not None], "attempt ID")
        _require_unique([a.execution_id for a in actions if a.execution_id is not None], "execution ID")


def validate_decision_history(
    history: DecisionHistory,
    *,
    state: ProjectState,
    run_id: RunId,
    usage: BudgetUsage,
) -> None:
    """Check that a history prefix links to the next decision input for ``state`` and ``usage``.

    Requires the expected run and project, a known last selected revision equal to the state revision,
    ``usage.decision_calls == len(history.entries) + 1`` (the next call's charge included) and current usage
    not below the last entry's. Historical observations are deliberately not checked against the current
    checkpoint: their sources may have been superseded since. This does not establish audit or termination
    correctness and must not be used to continue a run after a context request.
    """
    require_instance(history, DecisionHistory, "history")
    require_instance(state, ProjectState, "state")
    require_instance(run_id, RunId, "run_id")
    require_instance(usage, BudgetUsage, "usage")
    if history.run_id != run_id:
        raise ValueError(f"history run {history.run_id} does not match run {run_id}")
    if history.project_id != state.project_id:
        raise ValueError(f"history project {history.project_id} does not match state project {state.project_id}")
    expected_calls = len(history.entries) + 1
    if usage.decision_calls != expected_calls:
        raise ValueError(
            f"usage.decision_calls {usage.decision_calls} must be {expected_calls} (history length plus this call)"
        )
    if history.entries:
        last = history.entries[-1]
        if last.selected_revision is None:
            raise ValueError(f"the last history entry {last.call_id} has an unknown selected revision")
        if last.selected_revision != state.revision:
            raise ValueError(
                f"the last history entry selected revision {last.selected_revision}, "
                f"not the current state revision {state.revision}"
            )
        _require_usage_not_below(usage, last.usage, "current")
