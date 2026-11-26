"""HT01-only calibration runner and evidence checks; never enables a production profile.

The caller owns the transport. A fresh exclusive JSONL log is required; there is
no automatic restart, retry, or uncertain-outcome restoration in this helper.
"""

import json
import math
import os
import re
import time
from pathlib import Path

from autonomous_mixing.adapters.fl_studio import protocol as p
from autonomous_mixing.adapters.fl_studio.exchange import exchange

_NOT_WRITTEN = {"precondition_failed", "identity_mismatch", "invalid_command", "unsupported", "error"}


def _run_id(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{8,32}", value) is None:
        raise ValueError("run_id must be 8-32 ASCII letters, digits, underscores or hyphens")
    return value


def _header(run_id):
    return {"kind": "plan", "run_id": _run_id(run_id), "plan_digest": p.HT01_PLAN_DIGEST, "plan_json": p.HT01_PLAN_JSON}


def _inspect(run_id, final=False):
    return p.inspect_command(run_id + ("-final" if final else "-initial"), p.HT01_TITLE, 5, "Bass")


def _probe(run_id, expected, step):
    return p.ht01_probe_command(run_id + "-" + str(step), expected, step)


def _gain(reply):
    if reply is None:
        return None
    observed = reply.get("observed")
    return observed.get("gain") if isinstance(observed, dict) else None


def _replayed(reply):
    """A write reply the device answered from its duplicate history: not evidence of a fresh write."""
    observed = reply.get("observed")
    return not isinstance(observed, dict) or observed.get("replayed") is not False


def _same(a, b):
    """Type-strict equality of JSON values: true is not 1 and 5.0 is not 5."""
    return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False)


def _identity_ok(reply):
    observed = reply.get("observed", {}) if reply else {}
    return (
        observed.get("project_title") == p.HT01_TITLE
        and observed.get("insert") == p.HT01_INSERT
        and observed.get("api") == p.HT01_API
        and observed.get("fl_version") == p.HT01_BUILD
    )


def run_ht01(transport, *, path: Path, run_id: str, timeout_s: float = 5.0, clock=time.monotonic) -> dict:
    """Run the pinned plan once. Unknown outcomes stop without any further write.

    The log is exclusive and each line is flushed/fsynced before proceeding.
    Existing logs are never resumed or overwritten. Transport cleanup is the caller's responsibility.
    """
    header = _header(run_id)
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or timeout_s <= 0
    ):
        raise ValueError("timeout_s must be finite and positive")
    with Path(path).open("x", encoding="utf-8", newline="\n") as log:

        def record(row):
            log.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
            log.flush()
            os.fsync(log.fileno())

        def send(command):
            if command["type"] == "probe_gain":
                record({"kind": "pending", "command": command})
            try:
                result = exchange(command, transport=transport, timeout_s=timeout_s, clock=clock)
            except Exception as error:
                record(
                    {
                        "kind": "exchange",
                        "command": command,
                        "status": "unknown",
                        "reply": None,
                        "diagnostics": [type(error).__name__],
                    }
                )
                if isinstance(error, OSError):
                    return None
                raise
            reply = json.loads(result.reply_json) if result.reply_json else None
            record(
                {
                    "kind": "exchange",
                    "command": command,
                    "status": result.status,
                    "reply": reply,
                    "diagnostics": list(result.diagnostics),
                }
            )
            return reply if result.status == "replied" else None

        def finish(result):
            record({"kind": "result", "result": result})
            return result

        record(header)
        initial = send(_inspect(run_id))
        if initial is None or initial["status"] != "ok" or not _identity_ok(initial) or _gain(initial) != p.HT01_START:
            return finish({"status": "stopped", "reason": "initial_inspect", "restored_exact": False})
        current, applied, refused = dict(_gain(initial)), 0, False
        for step in range(len(p.HT01_SEQUENCE)):
            reply = send(_probe(run_id, current, step))
            if reply is None or reply["status"] == "uncertain":
                return finish({"status": "stopped", "reason": "unknown_outcome", "restored_exact": False})
            if _replayed(reply):
                return finish({"status": "stopped", "reason": "replayed_reply", "restored_exact": False})
            if reply["status"] != "applied":
                refused = reply["status"] in _NOT_WRITTEN
                break
            current = dict(reply["observed"]["after"])
            applied += 1
        if applied:
            restore = send(_probe(run_id, current, p.RESTORE_STEP))
            if restore is not None and restore["status"] != "uncertain" and _replayed(restore):
                return finish({"status": "stopped", "reason": "replayed_reply", "restored_exact": False})
            if restore is None or restore["status"] != "applied":
                return finish({"status": "stopped", "reason": "restore_not_confirmed", "restored_exact": False})
        final = send(_inspect(run_id, final=True))
        exact = bool(final and final["status"] == "ok" and _identity_ok(final) and _gain(final) == p.HT01_START)
        return finish(
            {
                "status": "completed" if exact and not refused and applied == len(p.HT01_SEQUENCE) else "stopped",
                "reason": None if exact and not refused else "refused_or_restore_mismatch",
                "restored_exact": exact,
            }
        )


_COMPLETED = {"status": "completed", "reason": None, "restored_exact": True}
_PRE_WRITE_STOP = {"status": "stopped", "reason": "initial_inspect", "restored_exact": False}


def release_evidence(records, run_id, result) -> bool:
    """Whether this run's own log, read back, permits releasing the restart guard (conservative).

    Only two shapes qualify, each bound to ``run_id`` and to the runner's in-memory ``result``: a complete run whose
    evidence validates (curve accepted or rejected), or a stop at the initial inspect, whose log is exactly the header,
    that one inspect exchange and the matching decision. Anything else, damaged or shortened, keeps the guard.
    """
    try:
        rows = list(records)
        if not rows or not _same(rows[0], _header(run_id)):
            return False
        if _same(result, _COMPLETED):
            return evaluate_ht01(rows)["verdict"] in ("H_accepted", "H_rejected")
        if not _same(result, _PRE_WRITE_STOP) or len(rows) != 3:
            return False
        return _initial_inspect_stopped(rows[1], _inspect(run_id)) and _same(
            rows[2], {"kind": "result", "result": _PRE_WRITE_STOP}
        )
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return False


def _initial_inspect_stopped(row, command) -> bool:
    """The one initial-inspect exchange, in a shape the runner can write, and one that justifies its stop.

    No reply (timeout, rejected reply, or a recorded transport error) carries ``reply: null``. A reply must be this
    command's, structurally valid and consistent with it, and must itself fail the start check (not ok, other scope or
    build, or a reading other than the exact start); an exact-start ok reply contradicts the stop.
    """
    if (
        not isinstance(row, dict)
        or set(row) != {"kind", "command", "status", "reply", "diagnostics"}
        or row["kind"] != "exchange"
        or not _same(row["command"], command)
        or not isinstance(row["diagnostics"], list)
        or not all(isinstance(d, str) for d in row["diagnostics"])
    ):
        return False
    status, reply = row["status"], row["reply"]
    if status in ("timeout", "invalid_reply", "unknown"):
        return reply is None
    if status != "replied" or not isinstance(reply, dict) or reply.get("id") != command["id"]:
        return False
    p.validate_reply(reply)
    if p.reply_consistency(command, reply):
        return False
    return reply["status"] != "ok" or not _identity_ok(reply) or not _same(_gain(reply), p.HT01_START)


def evaluate_ht01(records) -> dict:
    """Validate the complete command/pending/reply chain before assessing H.

    Rejects all duplicate records, including replays: this one-shot runner never
    retries. A truncated valid prefix is incomplete, never accepted. No fallback.
    """
    problems = []
    primary = {}
    errors = []
    rows = iter(records)
    try:
        header = next(rows)
        run_id = header["run_id"]
        if not _same(header, _header(run_id)):
            raise ValueError("plan header mismatch")
        expected = dict(p.HT01_START)
        commands = [_inspect(run_id), *range(len(p.HT01_SEQUENCE)), p.RESTORE_STEP, _inspect(run_id, True)]
        for entry in commands:
            write = isinstance(entry, (int, str))
            command = _probe(run_id, expected, entry) if write else entry
            if write:
                pending = next(rows)
                if not _same(pending, {"kind": "pending", "command": command}):
                    raise ValueError("pending command/order/chain mismatch")
            row = next(rows)
            if (
                set(row) != {"kind", "command", "status", "reply", "diagnostics"}
                or row["kind"] != "exchange"
                or not _same(row["command"], command)
            ):
                raise ValueError("exchange command/order mismatch")
            p.validate_command(row["command"])
            if not isinstance(row["diagnostics"], list) or not all(isinstance(d, str) for d in row["diagnostics"]):
                raise ValueError("diagnostics must be a list of strings")
            if row["status"] != "replied":
                raise ValueError("exchange has no accepted reply")
            if not isinstance(row["reply"], dict) or row["reply"].get("id") != command["id"]:
                raise ValueError("reply object or command ID mismatch")
            reply = p.validate_reply(row["reply"])
            if p.reply_consistency(command, reply) or not _identity_ok(reply):
                raise ValueError("reply inconsistent with command/scope/build")
            if reply["status"] != ("applied" if write else "ok"):
                raise ValueError("required successful reply missing")
            if not write:
                if not _same(_gain(reply), p.HT01_START):
                    raise ValueError("initial/final reading is not exact")
                continue
            observed = reply["observed"]
            if not _same(observed["before"], expected) or observed["replayed"] is not False:
                raise ValueError("before chain or replay mismatch")
            after = observed["after"]
            if abs(after["normalized"] - command["target_normalized"]) > 0.000001:
                raise ValueError("stored position missed")
            if entry == p.RESTORE_STEP:
                if not _same(after, p.HT01_START):
                    raise ValueError("restoration not exact")
            elif entry % 4 in (1, 3):
                point = p.HT01_SEQUENCE[entry]
                errors.append(abs(p.h_db(after["normalized"]) - after["db"]))
                primary.setdefault(point, []).append(after["db"])
            expected = dict(after)
        terminal = next(rows)
        if not _same(
            terminal, {"kind": "result", "result": {"status": "completed", "reason": None, "restored_exact": True}}
        ):
            raise ValueError("completion decision missing or contradictory")
        if next(rows, None) is not None:
            raise ValueError("extra records")
    except StopIteration:
        return {"verdict": "incomplete", "problems": ["truncated record stream"]}
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        problems.append(str(error))
    if problems:
        return {"verdict": "invalid_records", "problems": problems}
    repeat = max(abs(values[0] - values[1]) for values in primary.values())
    worst = max(errors)
    return {
        "verdict": "H_accepted" if worst <= 0.01 and repeat <= 0.000001 else "H_rejected",
        "problems": [],
        "max_error_db": worst,
        "max_repeat_difference_db": repeat,
        "range": list(p.HT01_RANGE),
        "restored_exact": True,
    }
