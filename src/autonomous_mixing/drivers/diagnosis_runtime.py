"""Explicit standalone diagnosis-source composition; no CLI or coordinator registration (R2A, experimental 2026-11-23)."""

from autonomous_mixing.core.diagnosis.masking import diagnose_experimental_masking, diagnose_masking
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource, validate_diagnosis_response
from autonomous_mixing.core.records.coordinator_runtime import DiagnosisRequest
from autonomous_mixing.core.records.diagnosis import DiagnosisReport
from autonomous_mixing.core.records.masking_diagnosis import ExperimentalMaskingDiagnosisPolicy, MaskingDiagnosisPolicy
from autonomous_mixing.core.records.results import PortResult, Success


class ConfiguredDiagnosisSource:
    """Binds a real pure rule to explicit configuration/IDs; never manufactures qualification.

    The policy is explicit: a legacy ``MaskingDiagnosisPolicy`` dispatches to the readiness rule (always INCOMPLETE, no
    candidates); an ``ExperimentalMaskingDiagnosisPolicy`` to the experimental fixed-support rule (uncalibrated
    hypotheses or honest incomplete coverage). Dispatch is by policy type only, never by metric names, and no policy
    is loaded automatically. Results are validated against the actual request; coverage and candidate IDs may not be
    reused or collide with each other across this instance's calls. Programming/configuration errors propagate rather
    than becoming outages.
    """

    def __init__(
        self, *, policy: MaskingDiagnosisPolicy | ExperimentalMaskingDiagnosisPolicy, identities: IdentitySource
    ) -> None:
        if not isinstance(policy, (MaskingDiagnosisPolicy, ExperimentalMaskingDiagnosisPolicy)):
            raise ValueError("policy must be an explicit MaskingDiagnosisPolicy or ExperimentalMaskingDiagnosisPolicy")
        if not callable(getattr(identities, "next_token", None)):
            raise ValueError("identities must provide next_token()")
        self._policy, self._identities = policy, identities
        self._issued: set[str] = set()

    def diagnose(self, request: DiagnosisRequest) -> PortResult[DiagnosisReport]:
        if isinstance(self._policy, ExperimentalMaskingDiagnosisPolicy):
            report = diagnose_experimental_masking(request, policy=self._policy, identities=self._identities)
        else:
            report = diagnose_masking(request, policy=self._policy, identities=self._identities)
        validate_diagnosis_response(request, report)
        ids = [r.coverage_id.value for r in report.coverage] + [c.candidate_id.value for c in report.candidates]
        if len(set(ids)) != len(ids) or set(ids) & self._issued:
            raise ValueError("reused diagnosis coverage or candidate identity across calls")
        self._issued.update(ids)
        return Success(report)
