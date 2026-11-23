"""Immutable P1 diagnosis bindings: legacy readiness and the explicitly experimental fixed-support rule.

Geometric support never establishes qualification, and experimental fixed support is not scientific qualification
either: its screening values are owner-approved development configuration, not calibration. The supplied measurement
plan must come from the verified preparation boundary. These record checks establish internal agreement, not file
integrity, physical alignment, a manifest's authenticity or a calibrated policy.
"""

from dataclasses import dataclass

from autonomous_mixing.core.records.identifiers import VersionRef
from autonomous_mixing.core.records.invariants import require_instance, require_tuple_of
from autonomous_mixing.core.records.masking_measurement import (
    P1_PROFILE,
    ExperimentalMaskingPolicy,
    ExperimentalSupport,
    MaskingMeasurementPlan,
    MaskingPreparationJob,
    PreparedSupport,
    bound_specification,
)
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition

READINESS_METRICS = (
    (VersionRef("p1.support_cells", "1"), "cells"),
    (VersionRef("p1.robust_exceedance_cells", "1"), "cells"),
    (VersionRef("p1.longest_robust_run_seconds", "1"), "s"),
)
# The experimental fixed-support catalogue (analyzer ``p1.experimental.*@1``). The separately reported
# ``p1.experimental.positive_margin_fraction`` counts M > 0, not M > deadband, and is deliberately not part of it.
EXPERIMENTAL_DIAGNOSIS_METRICS = (
    (VersionRef("p1.experimental.support_cells", "1"), "cells"),
    (VersionRef("p1.experimental.robust_exceedance_cells", "1"), "cells"),
    (VersionRef("p1.experimental.longest_robust_run_seconds", "1"), "s"),
)
EXPERIMENTAL_DIAGNOSIS_METHOD_NAME = "p1.experimental.diagnosis"
EXPERIMENTAL_DIAGNOSIS_METHOD_VERSION = "1"


def experimental_diagnosis_method(measurement_plan: MaskingMeasurementPlan) -> VersionRef:
    """The operational experimental diagnosis method bound to one prepared plan (its policy and frozen cells).

    Separate from the screening method ``p1.experimental_screen``; the binding implies no calibration.
    """
    require_instance(measurement_plan, MaskingMeasurementPlan, "measurement_plan")
    return bound_specification(EXPERIMENTAL_DIAGNOSIS_METHOD_NAME, EXPERIMENTAL_DIAGNOSIS_METHOD_VERSION,
                               measurement_plan.prepared_digest)  # fmt: skip


def _require_job_bindings(support: PreparedSupport, measurements, catalogue, label: str) -> None:
    """Exact catalogue, shared ordered sources, scope, units, alignment and geometry of one diagnosis job."""
    require_tuple_of(measurements, MeasurementDefinition, f"{label}.measurements", nonempty=True, distinct=False)
    metrics = [d.selector.metric for d in measurements]
    units = dict(catalogue)
    if len(metrics) != len(units) or set(metrics) != set(units):
        raise ValueError(f"{label} requires exactly its metric catalogue, without duplicates")
    sources = measurements[0].sources
    if len(sources) != 2:
        raise ValueError(f"{label} has exactly two ordered source subjects")
    # Reuse the reviewed profile/ordered-subject checks, without importing an adapter or duplicating DSP.
    MaskingPreparationJob(support.pair, support.time_range, support.region, *sources, support.alignment)
    for definition in measurements:
        scope = definition.selector.scope
        if (
            scope.track_ids != (support.pair.masker, support.pair.target)
            or scope.time_range != support.time_range
            or scope.frequency_range is not None
        ):
            raise ValueError("measurement scope must be the exact ordered pair and prepared interval, not Hz")
        if definition.unit != units[definition.selector.metric]:
            raise ValueError(f"measurement unit differs from the {label} catalogue")
        if definition.alignment != support.alignment.method:
            raise ValueError("measurement alignment differs from prepared provenance")
        if definition.sources != sources:
            raise ValueError(f"{label} metrics must share exactly the same ordered source requirements")
    for source in sources:
        if (source.sample_rate_hz, source.frame_count, source.timeline_start_seconds) != (
            support.sample_rate_hz,
            support.frame_count,
            support.timeline_start_seconds,
        ):
            raise ValueError("source geometry differs from prepared support")


def _require_plan_settings(measurement_plan: MaskingMeasurementPlan, measurements) -> None:
    for definition in measurements:
        if measurement_plan.prepared.profile == P1_PROFILE and any(s.channel_count != 1 for s in definition.sources):
            raise ValueError("legacy profile requires mono diagnosis source metadata")
        if (definition.analysis_spec, definition.analyzer) != (
            measurement_plan.specification,
            measurement_plan.analyzer,
        ):
            raise ValueError("measurement specification/analyzer differ from the configured plan")


@dataclass(frozen=True)
class MaskingDiagnosisJob:
    """One fully identified prepared support and the exact three qualified measurement definitions.

    Even finite values bearing these metric names do not qualify geometric U as S0. No thresholds or
    qualification flag are accepted here. All metrics share the same source subjects and exact scope.
    """

    support: PreparedSupport
    measurements: tuple[MeasurementDefinition, ...]

    def __post_init__(self) -> None:
        require_instance(self.support, PreparedSupport, "MaskingDiagnosisJob.support")
        _require_job_bindings(self.support, self.measurements, READINESS_METRICS, "readiness job")


@dataclass(frozen=True)
class MaskingDiagnosisPolicy:
    """Configured uncalibrated readiness method; no positive candidate route exists in R2A."""

    method: VersionRef
    measurement_plan: MaskingMeasurementPlan
    jobs: tuple[MaskingDiagnosisJob, ...]

    def __post_init__(self) -> None:
        require_instance(self.method, VersionRef, "MaskingDiagnosisPolicy.method")
        require_instance(self.measurement_plan, MaskingMeasurementPlan, "MaskingDiagnosisPolicy.measurement_plan")
        require_tuple_of(self.jobs, MaskingDiagnosisJob, "MaskingDiagnosisPolicy.jobs", nonempty=True, distinct=False)
        keys = [(j.support.pair, j.support.time_range) for j in self.jobs]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate or conflicting readiness job")
        for job in self.jobs:
            if job.support not in self.measurement_plan.prepared.supports:
                raise ValueError("job's full support does not belong to the configured prepared plan")
            _require_plan_settings(self.measurement_plan, job.measurements)


@dataclass(frozen=True)
class ExperimentalMaskingDiagnosisJob:
    """One frozen experimental support (with its full geometry) and exactly its three experimental measurements.

    The support carries the exact frozen cells, so equal cell counts with different cells are different jobs.
    """

    support: ExperimentalSupport
    measurements: tuple[MeasurementDefinition, ...]

    def __post_init__(self) -> None:
        require_instance(self.support, ExperimentalSupport, "ExperimentalMaskingDiagnosisJob.support")
        _require_job_bindings(self.support.geometry, self.measurements, EXPERIMENTAL_DIAGNOSIS_METRICS,
                              "experimental diagnosis job")  # fmt: skip


@dataclass(frozen=True)
class ExperimentalMaskingDiagnosisPolicy:
    """Configured experimental fixed-support diagnosis: method, measurement plan and jobs; no thresholds of its own.

    Screening values are read only from ``measurement_plan.prepared.experimental_policy`` (``screening``); this record
    accepts no copy. ``method`` must be ``experimental_diagnosis_method(measurement_plan)``: it names this operational
    rule bound to the complete prepared plan, never a calibrated or qualified method.
    """

    method: VersionRef
    measurement_plan: MaskingMeasurementPlan
    jobs: tuple[ExperimentalMaskingDiagnosisJob, ...]

    def __post_init__(self) -> None:
        require_instance(self.method, VersionRef, "ExperimentalMaskingDiagnosisPolicy.method")
        require_instance(self.measurement_plan, MaskingMeasurementPlan,
                         "ExperimentalMaskingDiagnosisPolicy.measurement_plan")  # fmt: skip
        prepared = self.measurement_plan.prepared
        if prepared.experimental_policy is None or not prepared.experimental_supports:
            raise ValueError("experimental diagnosis requires a prepared plan with the experimental extension")
        if self.method != experimental_diagnosis_method(self.measurement_plan):
            raise ValueError("experimental diagnosis method must be bound to exactly this prepared plan")
        require_tuple_of(self.jobs, ExperimentalMaskingDiagnosisJob, "ExperimentalMaskingDiagnosisPolicy.jobs",
                         nonempty=True, distinct=False)  # fmt: skip
        keys = [(j.support.geometry.pair, j.support.geometry.time_range) for j in self.jobs]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate or conflicting experimental diagnosis job")
        for job in self.jobs:
            if job.support not in prepared.experimental_supports:
                raise ValueError("job's frozen experimental support does not belong to the configured prepared plan")
            _require_plan_settings(self.measurement_plan, job.measurements)

    @property
    def screening(self) -> ExperimentalMaskingPolicy:
        """The plan's screening values (the only source of thresholds for this rule)."""
        return self.measurement_plan.prepared.experimental_policy
