"""Frozen provider/experimental-policy configuration for an explicitly audited run."""

import json
from dataclasses import dataclass

from autonomous_mixing.core.records.decision_exchange import ProviderRequestIdentity, canonical_json
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


@dataclass(frozen=True)
class DawAgentBinding(ProviderRunBinding):
    """Additional local-only composition settings, without changing the existing provider record shape.

    Paths and resource settings stay in this run binding, never the public Situation. Existing schema2 records
    remain decodable; this additive subtype has its own explicit codec tag. Credentials are supplied separately.
    """

    runtime_settings: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.runtime_settings, str):
            raise ValueError("runtime settings must be canonical JSON text")
        try:
            value = json.loads(self.runtime_settings)
            if not isinstance(value, dict) or canonical_json(value) != self.runtime_settings:
                raise ValueError("runtime settings must be a canonical JSON object")
        except (ValueError, TypeError) as error:
            raise ValueError("invalid runtime settings") from error
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                for key, child in item.items():
                    lower = key.lower()
                    if lower in {
                        "api_key",
                        "apikey",
                        "authorization",
                        "credentials",
                        "password",
                        "secret",
                        "access_token",
                    } or lower.endswith(("_api_key", "_secret", "_password")):
                        raise ValueError("credentials must not be stored in runtime settings")
                    pending.append(child)
            elif isinstance(item, list):
                pending.extend(item)
