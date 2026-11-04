"""Reference checks between a run, its configuration and a correction attempt."""

from autonomous_mixing.core.loop.transitions import validate_gain_execution
from autonomous_mixing.core.records.actions import Action
from autonomous_mixing.core.records.decision import Decision, ProposeAction
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.run import CorrectionAttempt, RunRecord
from autonomous_mixing.core.verification.consistency import validate_verification_references


def _validate_candidate_gain_change(before: ProjectState, candidate: ProjectState, action: Action) -> None:
    """Require exactly the action's absolute target gain; all other settings and fixed inputs unchanged."""
    operation = action.operation
    target = operation.track_id
    before_gains = {gain.track_id: gain for gain in before.processing.track_gains}
    candidate_gains = {gain.track_id: gain for gain in candidate.processing.track_gains}
    if target not in before_gains or target not in candidate_gains:
        raise ValueError(f"decision target track {target} has no explicit gain state; no baseline is assumed")
    for track_id, gain in before_gains.items():
        if track_id != target and candidate_gains.get(track_id) != gain:
            raise ValueError(f"candidate state changes the non-target setting of track {track_id}")
    if set(candidate_gains) != set(before_gains):
        raise ValueError("candidate state represents a different set of tracks than the before state")
    candidate_target = candidate_gains[target]
    if candidate_target.input_artifact != before_gains[target].input_artifact:
        raise ValueError(f"candidate state changes the fixed input artifact of track {target}")
    if candidate_target.gain_db != operation.value_db:
        raise ValueError(
            f"candidate gain for track {target} is {candidate_target.gain_db} dB, "
            f"but the action requested {operation.value_db} dB"
        )


def validate_attempt_references(attempt: CorrectionAttempt, run: RunRecord, decision: Decision) -> None:
    """Check an attempt's references against its run and decision.

    Uses the attempt's before state throughout: it may follow earlier accepted attempts, so its
    revision need not equal ``run.initial_revision``. When a candidate state is present, requires
    the decision's target gain to equal the requested absolute value and every non-target setting
    and fixed input artifact to be unchanged. Does not check execution permissions, evidence
    sufficiency, file integrity or lifecycle transitions.
    """
    if attempt.run_id != run.run_id:
        raise ValueError(f"attempt belongs to run {attempt.run_id}, not {run.run_id}")
    if attempt.decision_id != decision.decision_id:
        raise ValueError(f"attempt refers to decision {attempt.decision_id}, not {decision.decision_id}")
    if not isinstance(decision.outcome, ProposeAction):
        raise ValueError("attempt requires a decision with a ProposeAction outcome")
    action = decision.outcome.action
    if action.action_id != attempt.action_id:
        raise ValueError(f"decision action {action.action_id} does not match attempt action {attempt.action_id}")
    before = attempt.before_state
    if not run.project_id == before.project_id == decision.project_id:
        raise ValueError(
            f"project mismatch: run {run.project_id}, before state {before.project_id}, decision {decision.project_id}"
        )
    if decision.expected_revision != before.revision:
        raise ValueError(f"decision expects revision {decision.expected_revision}, before state is {before.revision}")
    configuration = run.configuration
    context = configuration.context
    if decision.context_id != context.context_id or decision.context_version != context.version:
        raise ValueError("decision context does not match the run configuration")
    if decision.controller != configuration.controller:
        raise ValueError("decision controller does not match the run configuration")
    run_tracks = set(configuration.scope.track_ids)
    for artifact in before.audio.artifacts:
        outside = sorted(str(t) for t in set(artifact.source_track_ids) - run_tracks)
        if outside:
            raise ValueError(
                f"checkpoint artifact {artifact.artifact_id} uses tracks outside the run scope: {', '.join(outside)}"
            )
    if attempt.candidate_state is not None and attempt.execution is not None:
        # Manually assembled candidates must also replace exactly the target-dependent artifacts.
        validate_gain_execution(before, attempt.execution, action)
        _validate_candidate_gain_change(before, attempt.candidate_state, action)
    if attempt.validation is not None:
        validate_verification_references(attempt.validation, configuration.criteria)
