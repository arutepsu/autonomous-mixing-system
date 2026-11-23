"""Deterministic P1 diagnosis rules: legacy readiness and the experimental fixed-support rule.

Neither is a qualified detector or an all-clear. No file, provider, array, manifest or producer preference is
consumed; the experimental rule reads its thresholds only from the configured plan's screening policy. Source matching
mirrors R1's seven-field SourceRequirement predicate; a later revision is not a selection heuristic. Alignment remains
a declaration.
"""

from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource
from autonomous_mixing.core.records.coordinator_runtime import DiagnosisRequest
from autonomous_mixing.core.records.diagnosis import (
    DiagnosisCoverage,
    DiagnosisCoverageStatus,
    DiagnosisReport,
    DiagnosticEvidence,
    ProblemCandidate,
    ProblemFamily,
    UncertaintyRepresentation,
)
from autonomous_mixing.core.records.identifiers import DiagnosisCoverageId, ProblemCandidateId
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.masking_diagnosis import (
    EXPERIMENTAL_DIAGNOSIS_METRICS,
    ExperimentalMaskingDiagnosisJob,
    ExperimentalMaskingDiagnosisPolicy,
    MaskingDiagnosisJob,
    MaskingDiagnosisPolicy,
)
from autonomous_mixing.core.records.observation import Unavailable

# The dist10 long-block call grid the analyzer reports run durations on (576 samples at 44,100 Hz).
_HOP, _RATE = 576, 44100


def _matches_source(artifact, source) -> bool:
    """Exact current-member subject/geometry matching; IDs/hash/revision are not SourceRequirement fields."""
    return (
        artifact.kind is source.kind
        and artifact.source_track_ids == source.source_track_ids
        and artifact.render_spec == source.render_spec
        and artifact.sample_rate_hz == source.sample_rate_hz
        and artifact.channel_count == source.channel_count
        and artifact.timeline_start_seconds == source.timeline_start_seconds
        and artifact.frame_count == source.frame_count
    )


def _job_readiness(request: DiagnosisRequest, job: MaskingDiagnosisJob) -> str:
    support = job.support
    label = f"{support.pair.masker} -> {support.pair.target}; region={support.region.name}; support={support.digest}"
    if support.cell_count == 0:
        label += "; empty geometric support"
    expected_ids = []
    for position, source in enumerate(job.measurements[0].sources):
        matches = [a for a in request.state.audio.artifacts if _matches_source(a, source)]
        if len(matches) != 1:
            status = "missing_source" if not matches else "ambiguous_source"
            return f"{label}: {status} at ordered source {position}"
        expected_ids.append(matches[0].artifact_id)
    findings = []
    for definition in job.measurements:
        metric, scope = definition.selector.metric, definition.selector.scope
        observations = [o for o in request.observations if o.metric == metric and o.scope == scope]
        if len(observations) > 1:
            status = "ambiguous_observation"
        elif not observations:
            status = "mismatched" if any(o.metric.name == metric.name for o in request.observations) else "missing"
        else:
            observation = observations[0]
            if (observation.analysis_spec, observation.analyzer) != (definition.analysis_spec, definition.analyzer):
                status = "settings_mismatch"
            elif observation.artifact_ids != tuple(expected_ids):
                status = "source_mismatch"
            elif isinstance(observation.result, Unavailable):
                status = f"unavailable ({observation.result.reason})"
            elif observation.result.unit != definition.unit:
                status = "unit_mismatch"
            else:
                status = f"present ({observation.observation_id}; bound scalar only)"
        findings.append(f"{metric.name}@{metric.version}: {status}")
    return f"{label}: " + "; ".join(findings)


def _require_jobs_within_request(request: DiagnosisRequest, jobs) -> None:
    for job in jobs:
        scope = job.measurements[0].selector.scope
        scope.validate_against(request.state.snapshot)
        outer = request.scope
        if (
            not set(scope.track_ids) <= set(outer.track_ids)
            or scope.time_range.start_seconds < outer.time_range.start_seconds
            or scope.time_range.end_seconds > outer.time_range.end_seconds
            or outer.frequency_range is not None
        ):
            raise ValueError("configured whole-band diagnosis job lies outside the request scope")


def diagnose_masking(
    request: DiagnosisRequest, *, policy: MaskingDiagnosisPolicy, identities: IdentitySource
) -> DiagnosisReport:
    """Validate declared readiness, always INCOMPLETE for uncalibrated P1 and no candidates (R2A).

    Invalid job/request scope and invalid/duplicate identity tokens are wiring errors. Missing, ambiguous or
    mismatched evidence is a readiness finding, not an infrastructure failure. Evidence presence never qualifies U.
    """
    require_instance(request, DiagnosisRequest, "request")
    require_instance(policy, MaskingDiagnosisPolicy, "policy")
    _require_jobs_within_request(request, policy.jobs)
    explanation = (
        "P1 INCOMPLETE: uncalibrated qualification policy; no qualified support S0; "
        "observations are not qualified. " + " | ".join(_job_readiness(request, j) for j in policy.jobs)
    )
    issued = set()

    def row(family, status, method, message):
        coverage_id = DiagnosisCoverageId(identities.next_token())
        if coverage_id in issued:
            raise ValueError("duplicate diagnosis coverage identity token")
        issued.add(coverage_id)
        return DiagnosisCoverage(coverage_id, family, request.scope, status, method, message)

    coverage = (
        row(ProblemFamily.MASKING, DiagnosisCoverageStatus.INCOMPLETE, policy.method, explanation),
        row(
            ProblemFamily.FREQUENCY_BUILDUP,
            DiagnosisCoverageStatus.UNAVAILABLE,
            None,
            "P2 frequency buildup: no supported diagnosis method configured; not an all-clear.",
        ),
        row(
            ProblemFamily.PHASE_INTERACTION,
            DiagnosisCoverageStatus.UNAVAILABLE,
            None,
            "P3 phase interaction: no supported diagnosis method configured; not an all-clear.",
        ),
    )
    return DiagnosisReport(request.state.project_id, request.state.revision, coverage, ())


# --- experimental fixed-support rule ----------------------------------------------------------------------------------


def _bound_scalars(request: DiagnosisRequest, job: ExperimentalMaskingDiagnosisJob):
    """The job's three observations, each uniquely and exactly bound to the current ordered sources, or the reason
    (a string) why the evidence is incomplete. Incomplete evidence is a finding, never a wiring error."""
    expected_ids = []
    for position, source in enumerate(job.measurements[0].sources):
        matches = [a for a in request.state.audio.artifacts if _matches_source(a, source)]
        if len(matches) != 1:
            status = "missing_source" if not matches else "ambiguous_source"
            return f"{status} at ordered source {position}"
        expected_ids.append(matches[0].artifact_id)
    bound = []
    for definition in job.measurements:
        metric, scope = definition.selector.metric, definition.selector.scope
        name = f"{metric.name}@{metric.version}"
        found = [o for o in request.observations if o.metric == metric and o.scope == scope]
        if len(found) > 1:
            return f"{name}: ambiguous_observation"
        if not found:
            return f"{name}: missing"
        observation = found[0]
        if (observation.analysis_spec, observation.analyzer) != (definition.analysis_spec, definition.analyzer):
            return f"{name}: settings_mismatch"
        if observation.artifact_ids != tuple(expected_ids):
            return f"{name}: source_mismatch"
        if isinstance(observation.result, Unavailable):
            return f"{name}: unavailable ({observation.result.reason})"
        if observation.result.unit != definition.unit:
            return f"{name}: unit_mismatch"
        bound.append(observation)
    # Resolve by metric identity, never by definition position: the records accept the catalogue in any order
    # (review fix 2026-11-23).
    by_metric = {o.metric: o for o in bound}
    return tuple(by_metric[metric] for metric, _ in EXPERIMENTAL_DIAGNOSIS_METRICS)


def _longest_possible_run(cells: tuple[tuple[int, int], ...]) -> int:
    """Longest run of consecutive calls within one band that the frozen cells allow."""
    longest, previous, length = 0, {}, {}
    for call, band in cells:  # sorted by (call, band)
        length[band] = length.get(band, 0) + 1 if previous.get(band) == call - 1 else 1
        previous[band] = call
        longest = max(longest, length[band])
    return longest


def _usable_counts(job: ExperimentalMaskingDiagnosisJob, observations) -> tuple[int, int, int, float] | str:
    """(frozen count, robust count, run calls, run seconds) after the declared consistency checks, or the reason.

    ``observations`` are in ``EXPERIMENTAL_DIAGNOSIS_METRICS`` order (support, robust, run), resolved by metric identity.
    """
    support, robust, run = (o.result.value for o in observations)
    frozen = job.support.cell_count
    if not float(support).is_integer() or int(support) != frozen:
        return f"support count {support!r} is not the {frozen} frozen cells"
    if not float(robust).is_integer() or not 0 <= robust <= frozen:
        return f"robust count {robust!r} is not an integer in [0, {frozen}]"
    robust = int(robust)
    if run < 0:
        return f"run {run!r} s is negative"
    possible = _longest_possible_run(job.support.cells)
    # Bound the duration by the frozen cells before converting it to calls, so a huge finite value cannot overflow
    # (review fix 2026-11-23); the exact grid check below still applies to every accepted value.
    if run > possible * _HOP / _RATE:
        return f"run {run!r} s exceeds the longest same-band run ({possible} calls) of the frozen cells"
    steps = round(run * _RATE / _HOP)
    if steps * _HOP / _RATE != run:
        return f"run {run!r} s is not a whole number of {_HOP}/{_RATE} s calls"
    if steps > robust:
        return f"run of {steps} calls exceeds the robust count {robust}"
    return frozen, robust, steps, run


def _job_label(job: ExperimentalMaskingDiagnosisJob) -> str:
    g = job.support.geometry
    return f"{g.pair.masker} -> {g.pair.target}; region={g.region.name}; experimental support={job.support.digest}"


def diagnose_experimental_masking(
    request: DiagnosisRequest, *, policy: ExperimentalMaskingDiagnosisPolicy, identities: IdentitySource
) -> DiagnosisReport:
    """Apply the experimental fixed-support rule to each configured job; one MASKING coverage row per job.

    Per job: INCOMPLETE (no candidate) when its evidence is missing, unavailable, wrongly bound, internally inconsistent
    or below the plan's minimum frozen cells; otherwise ASSESSED, with one uncalibrated candidate iff
    robust/frozen >= minimum_robust_fraction and run >= minimum_consecutive_calls * 576/44100 (both inclusive). The
    analyzer has already counted robust cells with the strict deadband; it is not reapplied. ASSESSED means only that
    this experimental rule ran. P2/P3 stay UNAVAILABLE. Out-of-scope jobs and invalid/duplicate identity tokens are
    wiring errors.
    """
    require_instance(request, DiagnosisRequest, "request")
    require_instance(policy, ExperimentalMaskingDiagnosisPolicy, "policy")
    _require_jobs_within_request(request, policy.jobs)
    screen = policy.screening
    minimum_run = screen.minimum_consecutive_calls * _HOP / _RATE
    issued: set[str] = set()

    def token() -> str:
        value = identities.next_token()
        if not isinstance(value, str) or value in issued:
            raise ValueError("invalid or duplicate diagnosis identity token")
        issued.add(value)
        return value

    identity = (f"method={policy.method.name}@{policy.method.version}; prepared={policy.measurement_plan.prepared_digest}"
                f"; screening policy={screen.digest}")  # fmt: skip
    coverage, candidates = [], []
    for job in policy.jobs:
        scope = job.measurements[0].selector.scope
        label = f"{_job_label(job)}; {identity}"
        observations = _bound_scalars(request, job)
        usable = observations if isinstance(observations, str) else _usable_counts(job, observations)
        if not isinstance(usable, str) and usable[0] < screen.minimum_cells:
            usable = f"frozen experimental support has {usable[0]} cells, below the minimum {screen.minimum_cells}"
        if isinstance(usable, str):
            gap = usable
            row = DiagnosisCoverage(DiagnosisCoverageId(token()), ProblemFamily.MASKING, scope,
                                    DiagnosisCoverageStatus.INCOMPLETE, policy.method,
                                    f"P1 experimental INCOMPLETE ({label}): {gap}; no candidate, not an all-clear.")  # fmt: skip
            coverage.append(row)
            continue
        frozen, robust, steps, run = usable
        fraction = robust / frozen
        hit = fraction >= screen.minimum_robust_fraction and run >= minimum_run
        counts = (f"robust exceedance {robust}/{frozen} = {fraction!r} (minimum {screen.minimum_robust_fraction!r}); "
                  f"longest same-band robust run {steps} calls = {run!r} s (minimum {screen.minimum_consecutive_calls} "
                  f"calls = {minimum_run!r} s)")  # fmt: skip
        row = DiagnosisCoverage(DiagnosisCoverageId(token()), ProblemFamily.MASKING, scope,
                                DiagnosisCoverageStatus.ASSESSED, policy.method,
                                f"P1 experimental ASSESSED ({label}): {counts}; "
                                f"{'candidate reported' if hit else 'no candidate under this experimental rule'}; "
                                "the rule ran, this is not scientific validation or an all-clear.")  # fmt: skip
        coverage.append(row)
        if hit:
            candidates.append(
                ProblemCandidate(
                    ProblemCandidateId(token()),
                    row.coverage_id,
                    request.state.project_id,
                    request.state.revision,
                    ProblemFamily.MASKING,
                    scope,
                    tuple(o.observation_id for o in observations),
                    policy.method,
                    _evidence(counts, label),
                )  # fmt: skip
            )
    for family, name in ((ProblemFamily.FREQUENCY_BUILDUP, "P2 frequency buildup"),
                         (ProblemFamily.PHASE_INTERACTION, "P3 phase interaction")):  # fmt: skip
        coverage.append(DiagnosisCoverage(DiagnosisCoverageId(token()), family, request.scope,
                                          DiagnosisCoverageStatus.UNAVAILABLE, None,
                                          f"{name}: no supported diagnosis method configured; not an all-clear."))  # fmt: skip
    return DiagnosisReport(request.state.project_id, request.state.revision, tuple(coverage), tuple(candidates))


def _evidence(counts: str, label: str) -> DiagnosticEvidence:
    return DiagnosticEvidence(
        strength=f"experimental dist10 exceedance hypothesis: {counts}",
        uncertainty=(
            "uncalibrated experimental screening values; no confidence probability or severity is inferred from the "
            "counts"
        ),
        representation=UncertaintyRepresentation.UNCALIBRATED,
        # False refers only to the declared engineering predicates of this experimental rule, never to scientific
        # sufficiency.
        insufficient_evidence=False,
        limitations=(
            f"identities: {label}",
            "uncalibrated thresholds; no perceptual validation: a model-threshold exceedance, not proven audible masking",
            "relies on declared alignment of the ordered sources, not sample-level authentication",
            "energy screens use a theoretical PCM_16 rounding noise reference, not measured noise",
            "duration counts overlapping dist10 analysis windows on the 576/44100 s call grid, not listening time",
            "no automatic correction implication: no gain change or improvement is specified",
        ),
        missing_information=(),
    )
