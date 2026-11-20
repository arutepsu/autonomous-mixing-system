"""The DAW parameter-control port and the structural checks on its responses.

Separate from the offline ``Renderer``/``RunRenderer``: a DAW write produces no candidate audio. Obligations on
every implementation (the reviewed FL bridge's rules; tests of a simulated controller demonstrate the intended
contract only and must be repeated against each real adapter):

- record the pending intent durably before dispatch;
- an identical retry with the same ``command_id`` never writes again and returns the recorded outcome: a
  written outcome (``DawWriteConfirmed``, ``DawWriteOffTarget``) with ``replayed=True``, and a ``DawWriteRefused``
  or ``DawWriteUncertain`` unchanged (those records have no ``replayed`` field); a different request under the same
  ``command_id`` is ``DawWriteRefused(DUPLICATE_ID_CONFLICT)``;
- recompute the normalized target from the request's expected start dB plus ``requested_change_db`` with the
  profile's versioned conversion, and check it against the adapter's own range, before dispatch;
- everything unknown after dispatch (timeout, malformed matching reply, interruption, failure after the setter)
  is ``DawWriteUncertain``;
- duplicate history may be lost on reload, so exactly-once execution is not claimed.
"""

from typing import Protocol

from autonomous_mixing.core.records.daw_control import (
    DawApplyOutcome,
    DawApplyRequest,
    DawInspection,
    DawInspectRequest,
    DawWriteConfirmed,
    DawWriteOffTarget,
    DawWriteRefused,
    DawWriteUncertain,
)
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.results import PortFailure, PortResult, Success


class DawParameterController(Protocol):
    """Inspects and applies one fader change on the bound insert. It must not choose actions or accept audio."""

    def inspect(self, request: DawInspectRequest) -> PortResult[DawInspection]: ...

    def apply(self, request: DawApplyRequest) -> DawApplyOutcome: ...


def validate_daw_inspection(request: DawInspectRequest, result: PortResult[DawInspection]) -> None:
    """A ``Success`` answering exactly ``request``, or a ``PortFailure`` (read-only: failure means nothing known)."""
    require_instance(request, DawInspectRequest, "request")
    if isinstance(result, PortFailure):
        return
    if not isinstance(result, Success) or not isinstance(result.value, DawInspection):
        raise ValueError("an inspection response must be Success(DawInspection) or a PortFailure")
    if result.value.request != request:
        raise ValueError("the inspection answers another request")


def validate_daw_apply_outcome(request: DawApplyRequest, outcome: DawApplyOutcome) -> None:
    """Structural consistency of an adapter's outcome with its request (raises ``ValueError``).

    For written outcomes: ``before`` within the profile's start tolerances of the inspected start, ``after`` at
    the claimed ``commanded_normalized`` within the read-back tolerance, the claimed position inside the
    profile's normalized range, and the dB change on the correct side of the success tolerance (``<=`` for
    confirmed, ``>`` for off-target). Core cannot verify that ``commanded_normalized`` is the position that
    produces the requested dB change: that is the adapter's claim, checked by the adapter before dispatch.
    Refused and uncertain outcomes are reports and are not judged.
    """
    require_instance(request, DawApplyRequest, "request")
    if not isinstance(outcome, (DawWriteRefused, DawWriteConfirmed, DawWriteOffTarget, DawWriteUncertain)):
        raise ValueError(f"unknown DAW apply outcome {outcome!r}")
    if outcome.command_id != request.command_id:
        raise ValueError(f"outcome command {outcome.command_id} answers another request than {request.command_id}")
    if isinstance(outcome, (DawWriteRefused, DawWriteUncertain)):
        return
    profile, expected = request.profile, request.inspection.fader
    if (
        abs(outcome.before.normalized - expected.normalized) > profile.start_normalized_tolerance
        or abs(outcome.before.db - expected.db) > profile.start_db_tolerance
    ):
        raise ValueError("the written outcome does not start where the request expected")
    if not profile.normalized_range.contains(outcome.commanded_normalized):
        raise ValueError("the claimed commanded position lies outside the profile's normalized range")
    if abs(outcome.after.normalized - outcome.commanded_normalized) > profile.readback_normalized_tolerance:
        raise ValueError("the fader does not read back at the claimed commanded position")
    within = abs(outcome.achieved_change_db - request.requested_change_db) <= profile.success_db_tolerance
    if isinstance(outcome, DawWriteConfirmed) and not within:
        raise ValueError("a confirmed outcome must meet the requested change within the success tolerance")
    if isinstance(outcome, DawWriteOffTarget) and within:
        raise ValueError("an off-target outcome must miss the requested change by more than the success tolerance")
