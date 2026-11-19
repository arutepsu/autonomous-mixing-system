"""Versioned canonical command identity and allowlisted core outcome serialization; no pickle."""

import json
from dataclasses import asdict
from enum import Enum

from autonomous_mixing.core.records.actions import Action, SetTrackGain
from autonomous_mixing.core.records.admission import AdmittedAction
from autonomous_mixing.core.records.budgets import BudgetGrant, BudgetOperation, BudgetUsage
from autonomous_mixing.core.records.daw import DawGainResolution, DawTrackState
from autonomous_mixing.core.records.daw_binding import DawFaderReading, DawTrackBinding
from autonomous_mixing.core.records.daw_control import (
    DawApplyRequest,
    DawBuild,
    DawControlProfile,
    DawInspection,
    DawInspectRequest,
    DawRange,
    DawRefusalCode,
    DawUncertainReason,
    DawWriteConfirmed,
    DawWriteOffTarget,
    DawWriteRefused,
    DawWriteUncertain,
)
from autonomous_mixing.core.records.daw_recovery import (
    DawRestoreConfirmed,
    DawRestoreMismatch,
    DawRestoreRefused,
    DawRestoreRequest,
    DawRestoreUncertain,
)
from autonomous_mixing.core.records.identifiers import ActionId, ExecutionId, ProjectId, Revision, TrackId, VersionRef

_KINDS = {
    "confirmed": DawWriteConfirmed,
    "off_target": DawWriteOffTarget,
    "refused": DawWriteRefused,
    "uncertain": DawWriteUncertain,
    # recovery plan 2026-11-18: restoration outcomes, decoded by the same allowlist
    "restore_confirmed": DawRestoreConfirmed,
    "restore_mismatch": DawRestoreMismatch,
    "restore_refused": DawRestoreRefused,
    "restore_uncertain": DawRestoreUncertain,
}


def _reject_constant(value):
    raise ValueError(f"non-finite JSON constant {value}")


def _loads(value):
    return json.loads(value, parse_constant=_reject_constant)


def _dumps(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True)


def _plain(value):
    """``asdict`` output with every enum replaced by its value (records contain no other non-JSON values)."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def request_json(request: DawApplyRequest) -> str:
    if not isinstance(request, DawApplyRequest):
        raise ValueError("request must be DawApplyRequest")
    return _dumps({"v": 1, "request": asdict(request)})


def restore_request_json(request: DawRestoreRequest) -> str:
    """Canonical identity of a restoration: its complete request, authorization and budget grant included."""
    if not isinstance(request, DawRestoreRequest):
        raise ValueError("request must be DawRestoreRequest")
    return _dumps({"v": 1, "restore": _plain(asdict(request))})


def grant_json(grant: BudgetGrant) -> str:
    if not isinstance(grant, BudgetGrant):
        raise ValueError("grant must be BudgetGrant")
    return _dumps({"v": 1, "grant": _plain(asdict(grant))})


def load_grant(value: str) -> BudgetGrant:
    try:
        data = _loads(value)
        if not isinstance(data, dict) or set(data) != {"v", "grant"} or type(data["v"]) is not int or data["v"] != 1:
            raise ValueError("unknown grant schema")
        fields = data["grant"]
        return BudgetGrant(BudgetOperation(fields["operation"]), BudgetUsage(**fields["usage_after_charge"]))
    except (TypeError, KeyError, ValueError) as error:
        raise ValueError("invalid serialized budget grant") from error


def outcome_json(outcome) -> str:
    for kind, cls in _KINDS.items():
        if type(outcome) is cls:
            fields = asdict(outcome)
            for name in ("code", "reason"):
                if name in fields:
                    fields[name] = getattr(outcome, name).value
            return _dumps({"v": 1, "kind": kind, "fields": fields})
    raise ValueError("unsupported DAW outcome")


def load_outcome(value: str):
    try:
        data = _loads(value)
        if (
            not isinstance(data, dict)
            or set(data) != {"v", "kind", "fields"}
            or type(data["v"]) is not int
            or data["v"] != 1
        ):
            raise ValueError("unknown outcome schema")
        cls = _KINDS[data["kind"]]
        fields = dict(data["fields"])
        for name in ("before", "after", "observed", "target"):
            if name in fields and fields[name] is not None:
                fields[name] = DawFaderReading(**fields[name])
        if "code" in fields:
            fields["code"] = DawRefusalCode(fields["code"])
        if "reason" in fields:
            fields["reason"] = DawUncertainReason(fields["reason"])
        return cls(**fields)
    except (TypeError, KeyError, ValueError) as error:
        raise ValueError("invalid serialized DAW outcome") from error


# --- journaled request context (export plan 2026-11-18, revision 3) ---------------------------------------------------


def _round_trip(record, data, what):
    if _plain(asdict(record)) != data:
        raise ValueError(f"journaled {what} does not serialize back to its journaled text")
    return record


def load_binding(data) -> DawTrackBinding:
    """The complete journaled binding; fails unless it serializes back to exactly the journaled dictionary."""
    try:
        record = DawTrackBinding(
            VersionRef(**data["reference"]), ProjectId(**data["project_id"]), TrackId(**data["track_id"]),
            data["project_title"], data["insert_index"], data["insert_name"], data["baseline_gain_db"],
            DawFaderReading(**data["baseline_fader"]),
        )  # fmt: skip
    except (TypeError, KeyError) as error:
        raise ValueError("invalid journaled binding") from error
    return _round_trip(record, data, "binding")


def load_profile(data) -> DawControlProfile:
    """The complete journaled profile; fails unless it serializes back to exactly the journaled dictionary."""
    try:
        fields = dict(data)
        fields["reference"] = VersionRef(**data["reference"])
        fields["build"] = DawBuild(**data["build"])
        fields["conversion"] = VersionRef(**data["conversion"])
        fields["normalized_range"] = DawRange(**data["normalized_range"])
        fields["fader_db_range"] = DawRange(**data["fader_db_range"])
        record = DawControlProfile(**fields)
    except (TypeError, KeyError) as error:
        raise ValueError("invalid journaled profile") from error
    return _round_trip(record, data, "profile")


def load_execution_id(data) -> ExecutionId:
    try:
        record = ExecutionId(**data)
    except TypeError as error:
        raise ValueError("invalid journaled execution ID") from error
    return _round_trip(record, data, "execution ID")


def load_apply_request(text: str) -> DawApplyRequest:
    """The exact journaled ``DawApplyRequest``: rebuilt from its stored text, which it must serialize back to exactly.

    Review fix 2026-11-19: an export anchor carries this record, so request identity is exact agreement with what was
    journaled before dispatch, never a tolerance on the achieved result.
    """
    try:
        data = _loads(text)
        if not isinstance(data, dict) or set(data) != {"v", "request"} or data["v"] != 1:
            raise ValueError("unknown apply request schema")
        fields = data["request"]

        def action(value):
            operation = value["operation"]
            return Action(ActionId(**value["action_id"]), ProjectId(**value["project_id"]),
                          Revision(**value["expected_revision"]),
                          SetTrackGain(TrackId(**operation["track_id"]), operation["value_db"]))  # fmt: skip

        admitted = AdmittedAction(action(fields["admitted"]["action"]),
                                  VersionRef(**fields["admitted"]["policy_reference"]))  # fmt: skip
        resolution, state = fields["resolution"], fields["resolution"]["state"]
        track_state = DawTrackState(load_binding(state["binding"]), Revision(**state["revision"]),
                                    state["selected_gain_db"], DawFaderReading(**state["confirmed_fader"]))  # fmt: skip
        gain = DawGainResolution(action(resolution["action"]), track_state,
                                 DawFaderReading(**resolution["observed_fader"]), resolution["target_fader_db"],
                                 resolution["delta_db"])  # fmt: skip
        seen = fields["inspection"]
        inspection = DawInspection(
            DawInspectRequest(load_binding(seen["request"]["binding"]), load_profile(seen["request"]["profile"])),
            DawBuild(**seen["build"]), seen["project_title"], seen["insert_index"], seen["insert_name"],
            None if seen["fader"] is None else DawFaderReading(**seen["fader"]),
        )  # fmt: skip
        record = DawApplyRequest(ExecutionId(**fields["execution_id"]), fields["command_id"], admitted, gain,
                                 load_profile(fields["profile"]), inspection, fields["requested_change_db"])  # fmt: skip
    except (TypeError, KeyError, ValueError) as error:
        raise ValueError("invalid journaled apply request") from error
    if request_json(record) != text:
        raise ValueError("journaled apply request does not serialize back to its stored text")
    return record
