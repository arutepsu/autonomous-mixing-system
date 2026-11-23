"""Explicit standalone diagnosis-source composition; no CLI or coordinator registration (R2A)."""

from autonomous_mixing.core.diagnosis.masking import diagnose_masking
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource, validate_diagnosis_response
from autonomous_mixing.core.records.coordinator_runtime import DiagnosisRequest
from autonomous_mixing.core.records.diagnosis import DiagnosisReport
from autonomous_mixing.core.records.identifiers import DiagnosisCoverageId
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.masking_diagnosis import MaskingDiagnosisPolicy
from autonomous_mixing.core.records.results import PortResult, Success


class ConfiguredDiagnosisSource:
    """Binds a real pure readiness rule to explicit configuration/IDs; never manufactures qualification.

    Results are validated against the actual request and coverage IDs may not be reused by this instance.
    No policy is loaded automatically. Programming/configuration errors propagate rather than becoming outages.
    """

    def __init__(self, *, policy: MaskingDiagnosisPolicy, identities: IdentitySource) -> None:
        require_instance(policy, MaskingDiagnosisPolicy, "policy")
        if not callable(getattr(identities, "next_token", None)):
            raise ValueError("identities must provide next_token()")
        self._policy, self._identities = policy, identities
        self._issued: set[DiagnosisCoverageId] = set()

    def diagnose(self, request: DiagnosisRequest) -> PortResult[DiagnosisReport]:
        report = diagnose_masking(request, policy=self._policy, identities=self._identities)
        validate_diagnosis_response(request, report)
        ids = {r.coverage_id for r in report.coverage}
        if ids & self._issued:
            raise ValueError("reused diagnosis coverage identity across calls")
        self._issued.update(ids)
        return Success(report)
