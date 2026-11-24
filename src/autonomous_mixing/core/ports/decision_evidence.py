"""One provenance boundary: exact decision exchange and persisted run evidence verification."""

from typing import Protocol

from autonomous_mixing.core.records.decision import DecisionResponse, Situation
from autonomous_mixing.core.records.decision_evidence import DecisionEvidenceRejected, DecisionEvidenceVerified
from autonomous_mixing.core.records.provider_run import ProviderRunBinding
from autonomous_mixing.core.records.results import PortFailure, Success
from autonomous_mixing.core.records.run_trace import RunJournalSnapshot


class DecisionEvidenceSource(Protocol):
    @property
    def binding(self) -> ProviderRunBinding: ...

    def verify(
        self, situation: Situation, result: Success[DecisionResponse] | PortFailure
    ) -> DecisionEvidenceVerified | DecisionEvidenceRejected: ...

    def verify_run(self, snapshot: RunJournalSnapshot) -> str | None:
        """A refusal reason, or None after re-verifying all persisted provider-run evidence. No DSP/dispatch."""
        ...
