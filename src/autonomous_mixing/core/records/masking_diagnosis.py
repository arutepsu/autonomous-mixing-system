"""Immutable P1 diagnosis-readiness bindings; geometric support never establishes qualification.

The supplied measurement plan must come from the verified preparation boundary. These record checks establish
internal agreement, not file integrity, physical alignment, a manifest's authenticity or a calibrated policy.
"""

from dataclasses import dataclass

from autonomous_mixing.core.records.identifiers import VersionRef
from autonomous_mixing.core.records.invariants import require_instance, require_tuple_of
from autonomous_mixing.core.records.masking_measurement import (
    P1_PROFILE,
    MaskingMeasurementPlan,
    MaskingPreparationJob,
    PreparedSupport,
)
from autonomous_mixing.core.records.verification_evidence import MeasurementDefinition

READINESS_METRICS = (
    (VersionRef("p1.support_cells", "1"), "cells"),
    (VersionRef("p1.robust_exceedance_cells", "1"), "cells"),
    (VersionRef("p1.longest_robust_run_seconds", "1"), "s"),
)


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
        require_tuple_of(
            self.measurements, MeasurementDefinition, "MaskingDiagnosisJob.measurements", nonempty=True, distinct=False
        )
        metrics = [d.selector.metric for d in self.measurements]
        units = dict(READINESS_METRICS)
        if len(metrics) != len(units) or set(metrics) != set(units):
            raise ValueError("readiness requires exactly the qualified metric catalogue, without duplicates")
        support = self.support
        sources = self.measurements[0].sources
        if len(sources) != 2:
            raise ValueError("a readiness job has exactly two ordered source subjects")
        # Reuse the reviewed profile/ordered-subject checks, without importing an adapter or duplicating DSP.
        MaskingPreparationJob(support.pair, support.time_range, support.region, *sources, support.alignment)
        for definition in self.measurements:
            scope = definition.selector.scope
            if (
                scope.track_ids != (support.pair.masker, support.pair.target)
                or scope.time_range != support.time_range
                or scope.frequency_range is not None
            ):
                raise ValueError("measurement scope must be the exact ordered pair and prepared interval, not Hz")
            if definition.unit != units[definition.selector.metric]:
                raise ValueError("measurement unit differs from the qualified catalogue")
            if definition.alignment != support.alignment.method:
                raise ValueError("measurement alignment differs from prepared provenance")
            if definition.sources != sources:
                raise ValueError("readiness metrics must share exactly the same ordered source requirements")
        for source in sources:
            if (source.sample_rate_hz, source.frame_count, source.timeline_start_seconds) != (
                support.sample_rate_hz,
                support.frame_count,
                support.timeline_start_seconds,
            ):
                raise ValueError("source geometry differs from prepared support")


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
            for definition in job.measurements:
                if self.measurement_plan.prepared.profile == P1_PROFILE and any(
                    source.channel_count != 1 for source in definition.sources
                ):
                    raise ValueError("legacy profile requires mono diagnosis source metadata")
                if (definition.analysis_spec, definition.analyzer) != (
                    self.measurement_plan.specification,
                    self.measurement_plan.analyzer,
                ):
                    raise ValueError("measurement specification/analyzer differ from the configured plan")
