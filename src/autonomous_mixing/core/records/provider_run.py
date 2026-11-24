"""Frozen provider/experimental-policy configuration for an explicitly audited run."""

from dataclasses import dataclass

from autonomous_mixing.core.records.decision_exchange import ProviderRequestIdentity
from autonomous_mixing.core.records.identifiers import VersionRef
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.masking_diagnosis import ExperimentalMaskingDiagnosisPolicy


@dataclass(frozen=True)
class ProviderRunBinding:
    """No credential, threshold copy, path, SDK type or evaluation input is stored."""

    provider: ProviderRequestIdentity
    prompt: VersionRef
    response_schema: VersionRef
    diagnosis_policy: ExperimentalMaskingDiagnosisPolicy

    def __post_init__(self) -> None:
        require_instance(self.provider, ProviderRequestIdentity, "provider")
        require_instance(self.prompt, VersionRef, "prompt")
        require_instance(self.response_schema, VersionRef, "response schema")
        require_instance(self.diagnosis_policy, ExperimentalMaskingDiagnosisPolicy, "diagnosis policy")
