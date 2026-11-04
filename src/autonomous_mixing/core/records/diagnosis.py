"""Diagnosis coverage, problem candidates and their descriptive evidence (attempt contract, section 12).

These records describe what a diagnosis method reported and how it was covered; they do not establish
that a candidate is perceptually real, calibrate any confidence or justify an action. Family members are
labels only. The scientifically justified uncertainty representation and methods remain open (D11).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from autonomous_mixing.core.records.identifiers import (
    DiagnosisCoverageId,
    ObservationId,
    ProblemCandidateId,
    ProjectId,
    Revision,
    VersionRef,
)
from autonomous_mixing.core.records.invariants import require_instance, require_text, require_tuple_of
from autonomous_mixing.core.records.observation import (
    AnalysisScope,
    Observation,
    Unavailable,
    validate_observation_sources,
)
from autonomous_mixing.core.records.project import ProjectState


class ProblemFamily(Enum):
    """A declared problem family label; it names no detection method."""

    MASKING = "masking"
    FREQUENCY_BUILDUP = "frequency_buildup"
    PHASE_INTERACTION = "phase_interaction"


class UncertaintyRepresentation(Enum):
    """How the evidence statement is expressed; there is deliberately no calibrated member."""

    QUALITATIVE = "qualitative"
    UNCALIBRATED = "uncalibrated"


class DiagnosisCoverageStatus(Enum):
    """Whether a method assessed a family over a scope, could not run, or lacked required evidence."""

    ASSESSED = "assessed"
    UNAVAILABLE = "unavailable"
    INCOMPLETE = "incomplete"


def _require_texts(value: Any, field: str, *, nonempty: bool) -> tuple[str, ...]:
    """A tuple of distinct non-blank strings, kept exactly as supplied."""
    require_tuple_of(value, str, field, nonempty=nonempty, distinct=True)
    for item in value:
        require_text(item, f"{field} element")
    return value


def _scope_within(inner: AnalysisScope, outer: AnalysisScope) -> bool:
    """Exact containment: track subset, time range inside, and a contained band if the outer restricts one."""
    if not set(inner.track_ids) <= set(outer.track_ids):
        return False
    if (
        inner.time_range.start_seconds < outer.time_range.start_seconds
        or inner.time_range.end_seconds > outer.time_range.end_seconds
    ):
        return False
    if outer.frequency_range is None:
        return True
    if inner.frequency_range is None:
        return False
    return (
        inner.frequency_range.low_hz >= outer.frequency_range.low_hz
        and inner.frequency_range.high_hz <= outer.frequency_range.high_hz
    )


@dataclass(frozen=True)
class DiagnosticEvidence:
    """Descriptive, uncalibrated account of a candidate's evidence strength, uncertainty and gaps.

    ``insufficient_evidence`` is a separate fact from the representation: a qualitative statement can be
    insufficient and an uncalibrated one is not thereby sufficient. Insufficiency must name at least one
    missing item. No strength is derived from the flag and no wording is required in the free text.
    """

    strength: str
    uncertainty: str
    representation: UncertaintyRepresentation
    insufficient_evidence: bool
    limitations: tuple[str, ...]
    missing_information: tuple[str, ...]

    def __post_init__(self) -> None:
        require_text(self.strength, "DiagnosticEvidence.strength")
        require_text(self.uncertainty, "DiagnosticEvidence.uncertainty")
        require_instance(self.representation, UncertaintyRepresentation, "DiagnosticEvidence.representation")
        if not isinstance(self.insufficient_evidence, bool):
            raise ValueError(
                f"DiagnosticEvidence.insufficient_evidence must be a bool, got {self.insufficient_evidence!r}"
            )
        _require_texts(self.limitations, "DiagnosticEvidence.limitations", nonempty=False)
        _require_texts(
            self.missing_information,
            "DiagnosticEvidence.missing_information",
            nonempty=self.insufficient_evidence,
        )


@dataclass(frozen=True)
class DiagnosisCoverage:
    """Coverage of one family by one method over one scope; not a statement about the whole project.

    ASSESSED means the method returned a report for this scope, which may contain no candidates; that
    is not an all-clear. INCOMPLETE means required evidence was missing. Both name the method used.
    UNAVAILABLE means the family was not assessed; the method may be absent or one that could not run.
    """

    coverage_id: DiagnosisCoverageId
    family: ProblemFamily
    scope: AnalysisScope
    status: DiagnosisCoverageStatus
    method: VersionRef | None
    explanation: str

    def __post_init__(self) -> None:
        require_instance(self.coverage_id, DiagnosisCoverageId, "DiagnosisCoverage.coverage_id")
        require_instance(self.family, ProblemFamily, "DiagnosisCoverage.family")
        require_instance(self.scope, AnalysisScope, "DiagnosisCoverage.scope")
        require_instance(self.status, DiagnosisCoverageStatus, "DiagnosisCoverage.status")
        if self.method is None:
            if self.status is not DiagnosisCoverageStatus.UNAVAILABLE:
                raise ValueError(f"DiagnosisCoverage with status {self.status.name} requires a method")
        else:
            require_instance(self.method, VersionRef, "DiagnosisCoverage.method")
        require_text(self.explanation, "DiagnosisCoverage.explanation")


@dataclass(frozen=True)
class ProblemCandidate:
    """One evidence-linked problem hypothesis reported under exactly one coverage row.

    The scope's tracks are the affected tracks. Project membership of the cited evidence and its
    availability are checked by ``validate_diagnosis_references``, not by this constructor.
    """

    candidate_id: ProblemCandidateId
    coverage_id: DiagnosisCoverageId
    project_id: ProjectId
    revision: Revision
    family: ProblemFamily
    scope: AnalysisScope
    evidence_ids: tuple[ObservationId, ...]
    method: VersionRef
    evidence: DiagnosticEvidence

    def __post_init__(self) -> None:
        require_instance(self.candidate_id, ProblemCandidateId, "ProblemCandidate.candidate_id")
        require_instance(self.coverage_id, DiagnosisCoverageId, "ProblemCandidate.coverage_id")
        require_instance(self.project_id, ProjectId, "ProblemCandidate.project_id")
        require_instance(self.revision, Revision, "ProblemCandidate.revision")
        require_instance(self.family, ProblemFamily, "ProblemCandidate.family")
        require_instance(self.scope, AnalysisScope, "ProblemCandidate.scope")
        require_tuple_of(
            self.evidence_ids, ObservationId, "ProblemCandidate.evidence_ids", nonempty=True, distinct=True
        )
        require_instance(self.method, VersionRef, "ProblemCandidate.method")
        require_instance(self.evidence, DiagnosticEvidence, "ProblemCandidate.evidence")


@dataclass(frozen=True)
class DiagnosisReport:
    """Coverage rows and the candidates reported under them for one project revision.

    Construction checks only the embedded records: unique IDs and coverage keys, candidates bound to an
    existing row with the same family and method and a contained scope, no candidates under UNAVAILABLE
    coverage and only insufficient ones under INCOMPLETE coverage. It does not show that any method ran
    or that a candidate is true; an empty candidate tuple keeps the meaning of its coverage rows.
    """

    project_id: ProjectId
    revision: Revision
    coverage: tuple[DiagnosisCoverage, ...]
    candidates: tuple[ProblemCandidate, ...]

    def __post_init__(self) -> None:
        require_instance(self.project_id, ProjectId, "DiagnosisReport.project_id")
        require_instance(self.revision, Revision, "DiagnosisReport.revision")
        require_tuple_of(self.coverage, DiagnosisCoverage, "DiagnosisReport.coverage", nonempty=True, distinct=False)
        require_tuple_of(
            self.candidates, ProblemCandidate, "DiagnosisReport.candidates", nonempty=False, distinct=False
        )

        rows: dict[DiagnosisCoverageId, DiagnosisCoverage] = {}
        keys: set[tuple[ProblemFamily, AnalysisScope, VersionRef | None]] = set()
        for row in self.coverage:
            if row.coverage_id in rows:
                raise ValueError(f"DiagnosisReport has a duplicate coverage ID: {row.coverage_id}")
            key = (row.family, row.scope, row.method)
            if key in keys:
                raise ValueError(
                    f"DiagnosisReport coverage {row.coverage_id} repeats an existing (family, scope, method) key"
                )
            rows[row.coverage_id] = row
            keys.add(key)

        candidate_ids: set[ProblemCandidateId] = set()
        for candidate in self.candidates:
            name = f"candidate {candidate.candidate_id}"
            if candidate.candidate_id in candidate_ids:
                raise ValueError(f"DiagnosisReport has a duplicate candidate ID: {candidate.candidate_id}")
            candidate_ids.add(candidate.candidate_id)
            if candidate.project_id != self.project_id or candidate.revision != self.revision:
                raise ValueError(
                    f"{name} describes {candidate.project_id}@{candidate.revision}, "
                    f"not the report's {self.project_id}@{self.revision}"
                )
            row = rows.get(candidate.coverage_id)
            if row is None:
                raise ValueError(f"{name} names unknown coverage {candidate.coverage_id}")
            if row.status is DiagnosisCoverageStatus.UNAVAILABLE:
                raise ValueError(f"{name} cannot be reported under UNAVAILABLE coverage {row.coverage_id}")
            if candidate.family is not row.family or candidate.method != row.method:
                raise ValueError(f"{name} does not share the family and method of coverage {row.coverage_id}")
            if row.status is DiagnosisCoverageStatus.INCOMPLETE and not candidate.evidence.insufficient_evidence:
                raise ValueError(
                    f"{name} under INCOMPLETE coverage {row.coverage_id} must be marked insufficient evidence"
                )
            if not _scope_within(candidate.scope, row.scope):
                raise ValueError(f"{name} scope is not contained in the scope of coverage {row.coverage_id}")


def validate_diagnosis_references(
    report: DiagnosisReport,
    *,
    state: ProjectState,
    scope: AnalysisScope,
    observations: Iterable[Observation],
) -> None:
    """Check a diagnosis report's structural references against the selected state, run scope and evidence.

    The report must describe the state's project and revision; every coverage scope must lie within
    ``scope`` (candidate scopes already lie within their rows). ``observations`` is consumed once; every
    item must be an ``Observation`` and IDs must be unique, including unreferenced extras, which are
    otherwise ignored. Every cited observation must be supplied and its sources must be current members
    of ``state`` (``validate_observation_sources``): an unchanged member produced at an older revision is
    valid, a superseded artifact is not. A candidate whose cited results are all ``Unavailable`` must be
    marked insufficient; partial availability is not classified either way.

    This checks structure and provenance only, not whether a method ran, whether a candidate is true or
    perceptually relevant, or whether any action is permitted or justified.
    """
    require_instance(report, DiagnosisReport, "report")
    require_instance(state, ProjectState, "state")
    require_instance(scope, AnalysisScope, "scope")
    if not isinstance(observations, Iterable):
        raise ValueError(f"observations must be iterable, got {type(observations).__name__}")

    if report.project_id != state.project_id or report.revision != state.revision:
        raise ValueError(
            f"diagnosis report describes {report.project_id}@{report.revision}, "
            f"not the selected state {state.project_id}@{state.revision}"
        )
    snapshot = state.snapshot
    scope.validate_against(snapshot)
    for row in report.coverage:
        row.scope.validate_against(snapshot)
        if not _scope_within(row.scope, scope):
            raise ValueError(f"coverage {row.coverage_id} scope is not contained in the supplied scope")
    for candidate in report.candidates:
        candidate.scope.validate_against(snapshot)

    by_id: dict[ObservationId, Observation] = {}
    for observation in observations:
        require_instance(observation, Observation, "observation")
        if observation.observation_id in by_id:
            raise ValueError(f"duplicate observation ID supplied: {observation.observation_id}")
        by_id[observation.observation_id] = observation

    checked: set[ObservationId] = set()
    for candidate in report.candidates:
        missing = [str(o) for o in candidate.evidence_ids if o not in by_id]
        if missing:
            raise ValueError(f"candidate {candidate.candidate_id} cites missing observations: {', '.join(missing)}")
        for observation_id in candidate.evidence_ids:
            if observation_id not in checked:
                validate_observation_sources(by_id[observation_id], state.audio.artifacts, state.audio, snapshot)
                checked.add(observation_id)
        all_unavailable = all(isinstance(by_id[o].result, Unavailable) for o in candidate.evidence_ids)
        if all_unavailable and not candidate.evidence.insufficient_evidence:
            raise ValueError(
                f"candidate {candidate.candidate_id} cites only unavailable measurements and must be marked "
                "insufficient evidence"
            )
