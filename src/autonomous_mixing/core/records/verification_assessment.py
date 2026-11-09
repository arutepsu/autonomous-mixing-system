"""Verification assessment inputs: conditions bound to measurement definitions and state evidence.

A ``VerificationAssessment`` binds every configured condition to exactly one ``MeasurementDefinition``
and supplies the assessed state's evidence with the baselines its mode needs. Construction checks
binding coverage, selector and unit agreement, baseline presence, shared project identity, equal states
for equal revisions and identical contents for repeated observation IDs. These checks do not establish
run membership or chronological order. The verdict is computed by ``core.verification.assessment``.
"""

from dataclasses import dataclass

from autonomous_mixing.core.records.identifiers import ConditionId, VerificationResultId
from autonomous_mixing.core.records.invariants import require_instance, require_tuple_of
from autonomous_mixing.core.records.verification import (
    AcceptanceCriteria,
    AssessmentMode,
    BaselineReference,
    DeltaGuard,
    ImprovementCondition,
)
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition, StateEvidence


@dataclass(frozen=True)
class ConditionMeasurement:
    """Binds one configured condition to the measurement it needs."""

    condition_id: ConditionId
    definition: MeasurementDefinition

    def __post_init__(self) -> None:
        require_instance(self.condition_id, ConditionId, "ConditionMeasurement.condition_id")
        require_instance(self.definition, MeasurementDefinition, "ConditionMeasurement.definition")


def _condition_unit(condition) -> str:
    if isinstance(condition, ImprovementCondition):
        return condition.minimum_delta.unit
    if isinstance(condition, DeltaGuard):
        return condition.maximum_absolute_delta.unit
    return condition.threshold.unit  # TargetCondition or AbsoluteGuard


def validate_condition_measurements(
    criteria: AcceptanceCriteria, measurements: tuple[ConditionMeasurement, ...]
) -> None:
    """Require exactly one measurement binding per configured condition, agreeing in selector and unit.

    ``measurements`` must be a nonempty tuple of ``ConditionMeasurement`` (any order). Duplicate,
    missing and extra condition IDs are rejected, and each definition's selector and unit must equal its
    condition's (threshold, minimum delta or maximum delta unit), including conditions an INITIAL
    assessment defers. Structure only: no scientific validity of the definitions is implied.
    """
    require_instance(criteria, AcceptanceCriteria, "criteria")
    require_tuple_of(measurements, ConditionMeasurement, "measurements", nonempty=True, distinct=False)
    bound: dict[ConditionId, MeasurementDefinition] = {}
    for measurement in measurements:
        if measurement.condition_id in bound:
            raise ValueError(f"duplicate measurement binding for condition {measurement.condition_id}")
        bound[measurement.condition_id] = measurement.definition
    conditions = (*criteria.targets, *criteria.improvements, *criteria.guards)
    configured = {condition.condition_id for condition in conditions}
    missing = sorted(str(c) for c in configured - bound.keys())
    extra = sorted(str(c) for c in bound.keys() - configured)
    if missing or extra:
        raise ValueError(f"measurement bindings do not match criteria (missing: {missing}, extra: {extra})")
    for condition in conditions:
        definition = bound[condition.condition_id]
        if definition.selector != condition.selector:
            raise ValueError(f"definition selector for {condition.condition_id} differs from the condition's")
        unit = _condition_unit(condition)
        if definition.unit != unit:
            raise ValueError(
                f"definition unit {definition.unit!r} for {condition.condition_id} is not the condition unit {unit!r}"
            )


@dataclass(frozen=True)
class VerificationAssessment:
    """One assessment request: INITIAL (``assessed`` is the initial state, no baselines) or ATTEMPT
    (``assessed`` is the candidate, ``before`` the pre-attempt state, ``initial`` needed by INITIAL delta guards).

    Measurement order is irrelevant; checks follow the criteria's configuration order.
    """

    result_id: VerificationResultId
    mode: AssessmentMode
    criteria: AcceptanceCriteria
    measurements: tuple[ConditionMeasurement, ...]
    assessed: StateEvidence
    before: StateEvidence | None
    initial: StateEvidence | None

    def __post_init__(self) -> None:
        require_instance(self.result_id, VerificationResultId, "VerificationAssessment.result_id")
        require_instance(self.mode, AssessmentMode, "VerificationAssessment.mode")
        require_instance(self.criteria, AcceptanceCriteria, "VerificationAssessment.criteria")
        require_tuple_of(
            self.measurements, ConditionMeasurement, "VerificationAssessment.measurements", nonempty=True,
            distinct=False,
        )  # fmt: skip
        require_instance(self.assessed, StateEvidence, "VerificationAssessment.assessed")
        if self.before is not None:
            require_instance(self.before, StateEvidence, "VerificationAssessment.before")
        if self.initial is not None:
            require_instance(self.initial, StateEvidence, "VerificationAssessment.initial")
        validate_condition_measurements(self.criteria, self.measurements)
        self._validate_baselines()
        self._validate_bundle_consistency()

    def _validate_baselines(self) -> None:
        assessed = self.assessed.state.revision
        if self.mode is AssessmentMode.INITIAL:
            if self.before is not None or self.initial is not None:
                raise ValueError("INITIAL assessment takes no before or initial baseline")
            return
        if self.before is None:
            raise ValueError("ATTEMPT assessment requires a before baseline")
        if self.before.state.revision == assessed:
            raise ValueError(f"before baseline must differ in revision from the assessed state {assessed}")
        needs_initial = any(
            isinstance(guard, DeltaGuard) and guard.baseline is BaselineReference.INITIAL
            for guard in self.criteria.guards
        )
        if needs_initial and self.initial is None:
            raise ValueError("a delta guard names the INITIAL baseline, but no initial evidence is supplied")
        if self.initial is not None and self.initial.state.revision == assessed:
            raise ValueError(f"initial baseline must differ in revision from the assessed state {assessed}")

    def _validate_bundle_consistency(self) -> None:
        bundles = [b for b in (self.assessed, self.before, self.initial) if b is not None]
        projects = {bundle.state.project_id for bundle in bundles}
        if len(projects) != 1:
            raise ValueError(f"evidence bundles name different projects: {sorted(str(p) for p in projects)}")
        states = {}
        observations = {}
        for bundle in bundles:
            revision = bundle.state.revision
            if revision in states and states[revision] != bundle.state:
                raise ValueError(f"evidence bundles carry different states for revision {revision}")
            states[revision] = bundle.state
            for observation in bundle.observations:
                known = observations.setdefault(observation.observation_id, observation)
                if known != observation:
                    raise ValueError(f"observation ID {observation.observation_id} has conflicting contents")
