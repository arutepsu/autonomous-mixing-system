"""Runtime responsibilities of the controlled serial coordinator.

Clock checkpoints, fresh identities, trace appends, dispatch gates, observed results and the atomic
confirmed-selection update. Every state-changing helper takes the immutable ``RuntimeProgress`` and returns
``(progress, result)``; services are explicit keyword parameters. ``on_progress`` is the run's exception
progress observer: it is called with each replacement progress so that, if an unexpected exception escapes
a deeper helper, the public ``run`` can still finalize from the latest acknowledged facts. Normal control
flow always uses the returned progress, never the observer. ``promote`` lives here rather than in
acceptance or recovery so both can apply a confirmed selection without importing each other.
"""

from collections.abc import Callable
from dataclasses import replace
from typing import TypeVar

from autonomous_mixing.core.loop.budgets import check_budget
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource, RunClock
from autonomous_mixing.core.ports.run_journal import RunJournal, validate_trace_append_response
from autonomous_mixing.core.records.acceptance import AcceptedReference
from autonomous_mixing.core.records.admission import StemBinding
from autonomous_mixing.core.records.budgets import BudgetBlock, BudgetOperation, BudgetUsage
from autonomous_mixing.core.records.coordinator_progress import AssessedCorrection, RuntimeProgress, Stop
from autonomous_mixing.core.records.coordinator_runtime import ClockReading
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    AttemptId,
    DecisionCallId,
    ExecutionId,
    Revision,
    RunEventId,
    TrackId,
)
from autonomous_mixing.core.records.integrity import ArtifactRootBinding
from autonomous_mixing.core.records.observation import Observation
from autonomous_mixing.core.records.results import Success
from autonomous_mixing.core.records.run_trace import (
    DispatchIntent,
    DispatchSkipped,
    InvalidPortResponse,
    OperationKind,
    OperationObserved,
    OperationOutcome,
    RunJournalSnapshot,
    RunTraceEvent,
    RunTracePayload,
    StageRequest,
    TraceAppended,
    TraceAppendResult,
    TraceAppendUnknown,
)
from autonomous_mixing.core.records.termination import TerminationReason

_Identity = TypeVar("_Identity")


def checkpoint(
    progress: RuntimeProgress, *, clock: RunClock, on_progress: Callable[[RuntimeProgress], None]
) -> tuple[RuntimeProgress, Stop | None]:
    reading = clock.read()
    if not isinstance(reading, ClockReading):
        return (progress, (TerminationReason.RUNTIME_CONTRACT_FAILED, "the run clock returned an invalid reading"))
    if reading.monotonic_seconds < progress.last.monotonic_seconds:
        return (progress, (TerminationReason.RUNTIME_CONTRACT_FAILED, "the run clock's monotonic reading decreased"))
    progress = replace(progress, last=reading)
    on_progress(progress)
    progress = replace(
        progress,
        usage=replace(progress.usage, elapsed_seconds=reading.monotonic_seconds - progress.first.monotonic_seconds),
    )
    on_progress(progress)
    return (progress, None)


def fresh(
    progress: RuntimeProgress,
    kind: type[_Identity],
    *,
    identities: IdentitySource,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, _Identity | None]:
    """A new typed identity, or None when the source repeats or supplies an invalid token."""
    token = identities.next_token()
    if not isinstance(token, str) or not token.strip() or token in progress.tokens:
        return (progress, None)
    progress = replace(progress, tokens=progress.tokens | {token})
    on_progress(progress)
    try:
        return (progress, kind(token))
    except ValueError:
        return (progress, None)


def identity_failure(what: str) -> Stop:
    return (TerminationReason.RUNTIME_CONTRACT_FAILED, f"the identity source supplied an invalid or repeated {what}")


def new_event(
    progress: RuntimeProgress,
    payload: RunTracePayload,
    event_usage: BudgetUsage,
    *,
    identities: IdentitySource,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, RunTraceEvent | None]:
    progress, event_id = fresh(progress, RunEventId, identities=identities, on_progress=on_progress)
    if event_id is None:
        return (progress, None)
    return (
        progress,
        RunTraceEvent(
            event_id, len(progress.known) + 1, progress.request.run_id, progress.last.utc, event_usage, payload
        ),
    )


def append(
    progress: RuntimeProgress,
    event: RunTraceEvent,
    *,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, TraceAppendResult]:
    """Append once; returns the validated response. Unknown outcomes are resolved by one snapshot."""
    response = journal.append(event)
    try:
        validate_trace_append_response(event, response)
    except ValueError as error:
        response = TraceAppendUnknown(event.event_id, f"invalid append response: {error}")
    if isinstance(response, TraceAppended):
        progress = replace(progress, known=progress.known + (event,))
        on_progress(progress)
        return (progress, response)
    progress = replace(progress, unacknowledged=progress.unacknowledged + (event,))
    on_progress(progress)
    if isinstance(response, TraceAppendUnknown):
        snapshot = journal.snapshot()
        if isinstance(snapshot, Success) and isinstance(snapshot.value, RunJournalSnapshot):
            events = snapshot.value.events
            if (
                events[: len(progress.known)] == tuple(progress.known)
                and len(events) == len(progress.known) + 1
                and (events[-1] == event)
            ):
                progress = replace(progress, known=progress.known + (event,))
                on_progress(progress)
    return (progress, response)


def record(
    progress: RuntimeProgress,
    payload: RunTracePayload,
    event_usage: BudgetUsage | None = None,
    *,
    identities: IdentitySource,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | None]:
    """Record one ordinary event; any non-acknowledgement ends ordinary work for recording failure."""
    if not progress.recording_ok:
        return (progress, (TerminationReason.RECORDING_FAILED, "an earlier trace event was not acknowledged"))
    progress, event = new_event(
        progress,
        payload,
        progress.usage if event_usage is None else event_usage,
        identities=identities,
        on_progress=on_progress,
    )
    if event is None:
        return (progress, identity_failure("event ID"))
    progress, append_result = append(progress, event, journal=journal, on_progress=on_progress)
    if isinstance(append_result, TraceAppended):
        return (progress, None)
    progress = replace(progress, recording_ok=False)
    on_progress(progress)
    return (progress, (TerminationReason.RECORDING_FAILED, f"trace event {event.event_id} was not acknowledged"))


def gate(
    progress: RuntimeProgress,
    kind: OperationKind,
    build: Callable[[BudgetUsage], StageRequest],
    budget: BudgetOperation | None,
    *,
    call: DecisionCallId | None = None,
    attempt: AttemptId | None = None,
    execution: ExecutionId | None = None,
    clock: RunClock,
    identities: IdentitySource,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, tuple[RunEventId | None, StageRequest | None, Stop | None, bool]]:
    """Pre-gate, intent and post-acknowledgement gate. Returns (intent_id, stage_request, stop, skipped)."""
    progress, stop = checkpoint(progress, clock=clock, on_progress=on_progress)
    if stop:
        return (progress, (None, None, stop, False))
    if progress.last.cancelled:
        return (
            progress,
            (None, None, (TerminationReason.USER_CANCELLED, f"cancelled before the {kind.value} intent"), False),
        )
    if budget is None:
        if progress.usage.elapsed_seconds >= progress.request.configuration.budget_limits.max_elapsed_seconds:
            return (
                progress,
                (None, None, (TerminationReason.ELAPSED_TIME_LIMIT, f"no time left to {kind.value}"), False),
            )
        charged = progress.usage
    else:
        assessment = check_budget(
            budget,
            limits=progress.request.configuration.budget_limits,
            usage=progress.usage,
            elapsed_seconds=progress.usage.elapsed_seconds,
        )
        if isinstance(assessment, BudgetBlock):
            reason = TerminationReason[assessment.reason.name]
            progress, stop = record(
                progress, assessment, assessment.usage, identities=identities, journal=journal, on_progress=on_progress
            )
            blocked = (reason, f"budget blocks the next {budget.value}: {assessment.reason.value}")
            return (progress, (None, None, stop or blocked, False))
        charged = assessment.usage_after_charge
    stage_request = build(charged)
    if not progress.recording_ok:
        return (
            progress,
            (None, stage_request, (TerminationReason.RECORDING_FAILED, "recording failed before dispatch"), False),
        )
    intent = DispatchIntent(kind, stage_request, progress.accepted, call, attempt, execution)
    progress, event = new_event(progress, intent, charged, identities=identities, on_progress=on_progress)
    if event is None:
        return (progress, (None, stage_request, identity_failure("intent event ID"), False))
    progress, response = append(progress, event, journal=journal, on_progress=on_progress)
    if not isinstance(response, TraceAppended):
        progress, _ = _set_recording_failed(progress, on_progress=on_progress)
        if isinstance(response, TraceAppendUnknown):
            progress = replace(progress, usage=charged)
            on_progress(progress)
        return (
            progress,
            (None, stage_request, (TerminationReason.RECORDING_FAILED, f"{kind.value} intent unacknowledged"), False),
        )
    progress = replace(progress, usage=charged)
    on_progress(progress)
    progress, stop = checkpoint(progress, clock=clock, on_progress=on_progress)
    reason = None
    if stop:
        reason, explanation = stop
    elif progress.last.cancelled:
        reason, explanation = (TerminationReason.USER_CANCELLED, f"cancelled after the {kind.value} intent")
    elif progress.usage.elapsed_seconds >= progress.request.configuration.budget_limits.max_elapsed_seconds:
        reason, explanation = (TerminationReason.ELAPSED_TIME_LIMIT, f"time ran out after the {kind.value} intent")
    if reason is not None:
        progress, stop = record(
            progress,
            OperationObserved(event.event_id, kind, DispatchSkipped(reason, explanation)),
            identities=identities,
            journal=journal,
            on_progress=on_progress,
        )
        return (progress, (event.event_id, stage_request, stop or (reason, explanation), True))
    return (progress, (event.event_id, stage_request, None, False))


def _set_recording_failed(
    progress: RuntimeProgress, *, on_progress: Callable[[RuntimeProgress], None]
) -> tuple[RuntimeProgress, None]:
    progress = replace(progress, recording_ok=False)
    on_progress(progress)
    return (progress, None)


def observed(
    progress: RuntimeProgress,
    intent_id: RunEventId,
    kind: OperationKind,
    outcome: OperationOutcome,
    *,
    clock: RunClock,
    identities: IdentitySource,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | None]:
    progress, clock_stop = checkpoint(progress, clock=clock, on_progress=on_progress)
    progress, stop = record(
        progress,
        OperationObserved(intent_id, kind, outcome),
        identities=identities,
        journal=journal,
        on_progress=on_progress,
    )
    return (progress, clock_stop or stop)


def _invalid(response: object, error: object) -> InvalidPortResponse:
    return InvalidPortResponse(type(response).__name__, str(error) or "invalid response")


def promote(
    progress: RuntimeProgress,
    reference: AcceptedReference,
    ctx: AssessedCorrection,
    *,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, None]:
    """Select a confirmed accepted reference and move the edited stem, mix and output roots onto it.

    The selection fields change in one replacement; fixed inputs are never touched.
    """
    progress = replace(progress, accepted=reference, verified=reference, restored_last=False, authority_known=True)
    on_progress(progress)
    receipt = ctx.receipt
    target, new_mix = receipt.candidate_artifacts
    progress = replace(progress, stems=_updated_stems(progress.stems, target.source_track_ids[0], target.artifact_id))
    on_progress(progress)
    progress = replace(progress, mix_id=new_mix.artifact_id)
    on_progress(progress)
    for artifact in receipt.candidate_artifacts:
        progress = replace(
            progress, roots=_updated_roots(progress.roots, artifact.artifact_id, progress.request.output_root_key)
        )
        on_progress(progress)
    return (progress, None)


def _root_key(progress: RuntimeProgress, artifact_id: ArtifactId, default: str) -> str:
    return next((b.root_key for b in progress.roots if b.artifact_id == artifact_id), default)


def _stem_id(progress: RuntimeProgress, track: TrackId) -> ArtifactId:
    return next((b.artifact_id for b in progress.stems if b.track_id == track))


def _observations_at(progress: RuntimeProgress, revision: Revision) -> tuple[Observation, ...]:
    return next((b.observations for b in progress.observations if b.state.revision == revision))


def _updated_stems(
    bindings: tuple[StemBinding, ...], track: TrackId, artifact_id: ArtifactId
) -> tuple[StemBinding, ...]:
    return tuple((StemBinding(b.track_id, artifact_id) if b.track_id == track else b for b in bindings))


def _updated_roots(
    bindings: tuple[ArtifactRootBinding, ...], artifact_id: ArtifactId, root_key: str
) -> tuple[ArtifactRootBinding, ...]:
    if any((b.artifact_id == artifact_id for b in bindings)):
        return bindings
    return (*bindings, ArtifactRootBinding(artifact_id, root_key))
