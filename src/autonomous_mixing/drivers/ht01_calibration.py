"""Explicit HT01 calibration operator helper, separate from autonomous runs.

Run only after device installation and live writes are separately authorized.
No automatic installation, retries, profile adoption, or recovery of unknown writes.
"""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from autonomous_mixing.adapters.fl_studio import protocol
from autonomous_mixing.adapters.fl_studio.calibration import evaluate_ht01, release_evidence, run_ht01
from autonomous_mixing.adapters.fl_studio.transport import TransportCleanupError, WinMMTransport

_HELD = []  # failed cleanup retains ownership until process exit; never retried here
_MAX_LOG_BYTES = 4 * 1024 * 1024
# One fixed restart guard for the whole HT01 calibration scope, independent of plan digest, run ID and log path.
# It survives crashes, new processes and FL script reloads; only validated evidence after clean MIDI cleanup releases
# it. Manual clearance is an owner decision outside this tool (calibration design, persistent restart guard).
_LOCAL = os.environ.get("LOCALAPPDATA")
GUARD_PATH = Path(_LOCAL) / "autonomous-mixing-system" / "ht01" / "ht01-calibration.guard.json" if _LOCAL else None


def _constant(value):
    raise ValueError("non-finite JSON constant")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _rows(path):
    with path.open("rb") as stream:
        raw = stream.read(_MAX_LOG_BYTES + 1)
    if len(raw) > _MAX_LOG_BYTES:
        raise ValueError("calibration log exceeds size limit")
    return [
        json.loads(line, parse_constant=_constant, object_pairs_hook=_object)
        for line in raw.decode("utf-8").splitlines()
    ]


def _evaluate(path):
    return evaluate_ht01(_rows(path))


def _arm(guard, record):
    guard.parent.mkdir(parents=True, exist_ok=True)
    with guard.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(record)
        stream.flush()
        os.fsync(stream.fileno())


def _releasable(log, run_id, result):
    """The run's own log, read back strictly, must be validated release evidence for this run ID and result."""
    try:
        return release_evidence(_rows(log), run_id, result)
    except (OSError, ValueError, RecursionError):
        return False


def _release(guard, record):
    """Remove only the guard this process armed, unchanged; anything else stays for the owner."""
    try:
        if guard.read_text(encoding="utf-8") != record:
            return False
        guard.unlink()
        return True
    except OSError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="send the pinned calibration writes once, then restore exactly")
    run.add_argument("--log", type=Path, required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--in", dest="input", required=True)
    run.add_argument("--authorize-plan-digest", required=True)
    run.add_argument("--timeout", type=float, default=5.0)
    evaluate = sub.add_parser("evaluate", help="read and assess a captured log, without MIDI")
    evaluate.add_argument("--log", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.action == "evaluate":
        try:
            result = _evaluate(args.log)
        except (OSError, ValueError, RecursionError) as error:
            print(json.dumps({"status": "refused", "reason": type(error).__name__}))
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0 if result["verdict"] == "H_accepted" else 4
    if _HELD:
        print("REFUSED: MIDI cleanup failed earlier in this process; check MIDI before using a new process")
        return 4
    if args.authorize_plan_digest != protocol.HT01_PLAN_DIGEST or args.log.exists():
        print("REFUSED: wrong plan digest or log already exists")
        return 2
    # Validate all pure arguments before constructing native resources.
    import math
    import re

    if (
        re.fullmatch(r"[A-Za-z0-9_-]{8,32}", args.run_id) is None
        or not math.isfinite(args.timeout)
        or args.timeout <= 0
    ):
        print("REFUSED: invalid run ID or timeout")
        return 2
    guard = GUARD_PATH
    if guard is None or os.path.lexists(guard):
        print(f"REFUSED: unresolved HT01 calibration guard (or no guard location): {guard}")
        return 4
    record = json.dumps(
        {
            "kind": "ht01_calibration_guard",
            "plan_id": protocol.HT01_PLAN_ID,
            "plan_digest": protocol.HT01_PLAN_DIGEST,
            "run_id": args.run_id,
            "log": str(args.log.resolve()),
            "created_utc": datetime.now(UTC).isoformat(),
            "pid": os.getpid(),
        },
        sort_keys=True,
    )
    # The guard is armed before MIDI opens: a failed or partial arming opens nothing (a partial guard stays).
    try:
        _arm(guard, record)
    except OSError:
        print("REFUSED: could not arm the HT01 calibration guard; MIDI was not opened")
        return 4
    try:
        transport = WinMMTransport(args.out, args.input)
    except TransportCleanupError as error:
        # Partial native construction whose cleanup failed: resources may still be held. Retain the partial
        # transport for this process and keep the persistent guard; nothing here retries or releases.
        _HELD.append(error.transport)
        print(f"STOPPED: partial MIDI construction cleanup failed; transport and guard retained: {guard}")
        return 4
    except OSError:
        # Known in this process: no transport existed and the runner never ran, so nothing was sent.
        released = _release(guard, record)
        print("REFUSED: MIDI transport unavailable" + ("" if released else f"; guard retained: {guard}"))
        return 4
    result = None
    cleanup_failed = False
    try:
        result = run_ht01(transport, path=args.log, run_id=args.run_id, timeout_s=args.timeout)
    finally:
        try:
            transport.close()
        except Exception:
            _HELD.append(transport)
            cleanup_failed = True
            print("STOPPED: MIDI cleanup failed; transport retained until process exit")
    released = not cleanup_failed and _releasable(args.log, args.run_id, result) and _release(guard, record)
    if not released:
        print(f"BLOCKED: HT01 calibration guard retained for owner review: {guard}")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "completed" and released else 4


if __name__ == "__main__":
    raise SystemExit(main())
