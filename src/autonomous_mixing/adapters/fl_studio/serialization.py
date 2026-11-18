"""Versioned canonical command identity and allowlisted core outcome serialization; no pickle."""

import json
from dataclasses import asdict
from enum import Enum

from autonomous_mixing.core.records.budgets import BudgetGrant, BudgetOperation, BudgetUsage
from autonomous_mixing.core.records.daw import DawFaderReading
from autonomous_mixing.core.records.daw_control import (
    DawApplyRequest,
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
