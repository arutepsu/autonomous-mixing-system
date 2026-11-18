"""Evaluate one FL Studio fader calibration run against the predeclared criteria.

Design and criteria: docs/research/fl-studio-db-conversion-design.md, section 5 (pinned before any run).

    uv run python scripts/fl_studio/evaluate_calibration.py --log <bridge log.jsonl> --run-id <id> [--out result.json]

Reads the bridge client's JSON-lines log (requests and replies exactly as captured) and selects the records of one
run (IDs ``cal-<run>-...``). Before any number is used, every record is validated (``check_records``), and a run
with any problem gets the verdict ``invalid_records``, never an accepted curve:

- each command passes ``bridge_protocol.validate_command``: probe type, plan ID, the plan's project title and insert,
  and the pinned target for its step (an edited target or identity is refused even if the reply agrees with it);
- each probe's ID is ``cal-<run>-NN`` for its step, or ``cal-<run>-restore-N``;
- each reply passes ``validate_reply`` with the command's ID, and ``reply_consistency`` with its command (observed
  identity, start against ``expected``, read-back at the target, ``db_change`` arithmetic);
- the initial inspect (the last inspect before the run) is an ``ok`` reply for the plan's project and insert;
- applied steps appear in log order 0, 1, 2, ... with no gap, and each step expects exactly the previous step's
  read-back (step 0: the initial inspect's gain);
- a later reply to an applied step is a replay: ``replayed`` true, same command, same observations;
- every restore targets exactly the initial inspect's normalized gain.

Then it checks the predeclared criteria:

1. ``normalized_exact``: every probe's normalized read-back is within 1e-6 of its pinned target;
2. ``primary_within_tolerance``: |H(v) - dB| <= 0.01 dB at every primary-range probe, with v the read-back;
3. ``visits_agree``: the two visits of each primary point read dB within 1e-6 of each other.

Verdict: ``invalid_records`` if any check above fails; ``incomplete`` if any pinned step lacks an applied probe; ``H_accepted`` (for [0.62, 0.86] only) if all
three hold; otherwise, if 1 and 3 hold, the held-out table fallback (fit on the even-indexed primary points, predict the
odd-indexed ones by linear interpolation within 0.01 dB) gives ``table_accepted`` or ``not_established``. Wide
points and the restore are reported, not part of the verdict on H. H is the candidate curve
H(v) = 20 log10((11^(1.25 v) - 1) / 10); it was selected from three earlier live points (0.80, 0.78, 0.76), none
of which is a test point here.
"""

import argparse
import hashlib
import json
import math
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bridge_protocol as protocol  # noqa: E402

DB_TOLERANCE = 0.01  # |H - read-back| at primary points (predeclared)
NORMALIZED_TOLERANCE = 1e-6  # stored value vs written target (predeclared)
VISITS_DB_TOLERANCE = 1e-6  # agreement of the two visits (predeclared)
PINNED_PLAN_DIGEST = "8b4512b645cfb48ebe74be337e70d1663bc4a5df39195ee1d6fc175e834fbd00"  # plan + tolerances, 2026-11-18


def h_db(v: float) -> float:
    """Candidate fader law H (hypothesis under test, not a documented FL Studio function)."""
    return 20.0 * math.log10((math.exp(1.25 * v * math.log(11.0)) - 1.0) / 10.0)


def approach(previous: float, point: float) -> str:
    return "above" if previous > point else "below"


def plan_digest() -> str:
    plan = {"id": protocol.CALIBRATION_PLAN_ID, "project_title": protocol.CALIBRATION_PROJECT_TITLE,
            "insert": protocol.CALIBRATION_INSERT, "primary": protocol.CALIBRATION_PRIMARY,
            "wide": protocol.CALIBRATION_WIDE, "seed": protocol.CALIBRATION_SEED,
            "sequence": protocol.CALIBRATION_SEQUENCE, "restore": protocol.RESTORE_STEP,
            "tolerances": [DB_TOLERANCE, NORMALIZED_TOLERANCE, VISITS_DB_TOLERANCE]}  # fmt: skip
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _interpolate(points: list, v: float) -> float:
    for (v0, d0), (v1, d1) in zip(points, points[1:]):
        if v0 <= v <= v1:
            return d0 + (d1 - d0) * (v - v0) / (v1 - v0)
    raise ValueError(f"{v} lies outside the table")


def _record_id(record: dict) -> str:
    command = record.get("command")
    return str(command.get("id", "")) if isinstance(command, dict) else ""


def _checked_reply(record: dict, problems: list) -> dict | None:
    """The record's reply if it is valid, answers its command and is consistent with it; else None (problem noted)."""
    command, reply, rid = record["command"], record.get("reply"), _record_id(record)
    try:
        protocol.validate_reply(reply)
    except (protocol.ProtocolError, AttributeError, KeyError, TypeError) as exc:
        problems.append(f"{rid}: reply {getattr(exc, 'code', type(exc).__name__)}")
        return None
    if reply["id"] != command["id"]:
        problems.append(f"{rid}: reply id {reply['id']} answers another command")
        return None
    inconsistency = protocol.reply_consistency(command, reply)
    if inconsistency is not None:
        problems.append(f"{rid}: {inconsistency}")
        return None
    return reply


def _initial_inspect(records: list, first_index: int, problems: list) -> dict | None:
    inspects = [r for r in records[:first_index] if r["command"].get("type") == "inspect" and r["status"] == "replied"]
    if not inspects:
        problems.append("initial inspect: none before the run")
        return None
    record = inspects[-1]
    try:
        protocol.validate_command(record["command"])
    except protocol.ProtocolError as exc:
        problems.append(f"initial inspect: command {exc.code}")
        return None
    plan_target = {"project_title": protocol.CALIBRATION_PROJECT_TITLE, "insert": protocol.CALIBRATION_INSERT}
    if {"project_title": record["command"]["project_title"], "insert": record["command"]["insert"]} != plan_target:
        problems.append("initial inspect: not the plan's project title and insert")
        return None
    reply = _checked_reply(record, problems)
    if reply is None or reply["status"] != "ok":
        problems.append("initial inspect: no ok reply")
        return None
    return reply["observed"]["gain"]


def check_records(records: list, run_id: str) -> dict:
    """Validate a run's records (see module docstring); returns problems and the trusted probes."""
    prefix = f"cal-{run_id}-"
    restore_id = re.compile(re.escape(prefix) + r"restore-[1-9][0-9]*")
    problems, by_step, order, restores = [], {}, [], []
    run = [(i, r) for i, r in enumerate(records) if _record_id(r).startswith(prefix)]
    if not run:
        return {"problems": ["no records for this run"], "initial": None, "by_step": {}, "restores": [], "final": None}
    initial = _initial_inspect(records, run[0][0], problems)
    last_restore_index = None
    for index, record in run:
        command, rid = record["command"], _record_id(record)
        try:
            protocol.validate_command(command)
        except protocol.ProtocolError as exc:
            problems.append(f"{rid}: command {exc.code}")
            continue
        if command["type"] != "probe_gain":
            problems.append(f"{rid}: a {command['type']} command carries a calibration id")
            continue
        step = command["step"]
        expected_id = probe_id_pattern = None
        if step == protocol.RESTORE_STEP:
            probe_id_pattern = restore_id
        else:
            expected_id = f"{prefix}{step:02d}"
        if (expected_id is not None and rid != expected_id) or (
            probe_id_pattern and not probe_id_pattern.fullmatch(rid)
        ):
            problems.append(f"{rid}: id does not match step {step!r}")
            continue
        if record["status"] != "replied":
            continue  # pending, timeout, invalid reply, blocked: no observation to use
        reply = _checked_reply(record, problems)
        if reply is None or reply["status"] != "applied":
            continue
        if step == protocol.RESTORE_STEP:
            if initial is not None and command["target_normalized"] != initial["normalized"]:
                problems.append(f"{rid}: restore target {command['target_normalized']} is not the initial inspect's")
            restores.append(record)
            last_restore_index = index
            continue
        if step in by_step:
            original = by_step[step]
            same = (protocol.payload_key(command) == protocol.payload_key(original["command"])
                    and reply["observed"]["replayed"] is True
                    and {**reply["observed"], "replayed": False} == original["reply"]["observed"])  # fmt: skip
            if not same:
                problems.append(f"{rid}: a second applied reply that is not a faithful replay")
            continue
        by_step[step] = record
        order.append(step)
    if order != list(range(len(order))):
        problems.append(f"order: applied steps appear as {order}, not 0, 1, 2, ...")
    for step, record in by_step.items():
        reference = (
            initial if step == 0 else by_step.get(step - 1, {}).get("reply", {}).get("observed", {}).get("after")
        )
        if reference is not None and record["command"]["expected"] != reference:
            problems.append(f"chain: step {step} expects {record['command']['expected']}, not the previous read-back")
    final = None
    if last_restore_index is not None:
        for record in records[last_restore_index + 1 :]:
            if record["command"].get("type") == "inspect" and _applied_inspect(record):
                final = record["reply"]["observed"]["gain"]
                break
    return {"problems": problems, "initial": initial, "by_step": by_step, "restores": restores, "final": final}


def evaluate(records: list, run_id: str) -> dict:
    checked = check_records(records, run_id)
    by_step, restores, final = checked["by_step"], checked["restores"], checked["final"]
    sequence = protocol.CALIBRATION_SEQUENCE
    missing = [step for step in range(len(sequence)) if step not in by_step]
    probes = []
    for step in sorted(by_step):
        record = by_step[step]
        after = record["reply"]["observed"]["after"]
        previous = sequence[step - 1] if step > 0 else record["command"]["expected"]["normalized"]
        target = sequence[step]
        probes.append({
            "id": record["command"]["id"], "step": step, "target": target,
            "kind": "primary" if target in protocol.CALIBRATION_PRIMARY else "wide",
            "approach": approach(previous, target), "normalized": after["normalized"], "db": after["db"],
            "normalized_minus_target": after["normalized"] - target, "h_minus_db": h_db(after["normalized"]) - after["db"],
        })  # fmt: skip
    primary = [p for p in probes if p["kind"] == "primary"]
    normalized_exact = all(abs(p["normalized_minus_target"]) <= NORMALIZED_TOLERANCE for p in probes)
    within = all(abs(p["h_minus_db"]) <= DB_TOLERANCE for p in primary)
    visits = {}
    for p in primary:
        visits.setdefault(p["target"], []).append(p)
    visits_agree = all(len(v) < 2 or abs(v[0]["db"] - v[1]["db"]) <= VISITS_DB_TOLERANCE for v in visits.values())
    criteria = {"normalized_exact": normalized_exact, "primary_within_tolerance": within, "visits_agree": visits_agree}
    result = {
        "run_id": run_id, "plan": protocol.CALIBRATION_PLAN_ID, "plan_digest": plan_digest(),
        "complete": not missing, "missing_steps": missing, "criteria": criteria,
        "tolerances": {"db": DB_TOLERANCE, "normalized": NORMALIZED_TOLERANCE, "visits_db": VISITS_DB_TOLERANCE},
        "max_abs_h_minus_db_primary": max((abs(p["h_minus_db"]) for p in primary), default=None),
        "max_abs_h_minus_db_wide": max((abs(p["h_minus_db"]) for p in probes if p["kind"] == "wide"), default=None),
        "wide_within_tolerance": {str(p["target"]): abs(p["h_minus_db"]) <= DB_TOLERANCE
                                  for p in probes if p["kind"] == "wide"},
        "visit_db_differences": {str(t): (v[1]["db"] - v[0]["db"]) if len(v) == 2 else None
                                 for t, v in sorted(visits.items())},
        "probes": probes, "accepted_range": None, "fallback": None, "problems": checked["problems"],
        "restore": _restore_summary(checked["initial"], restores, final),
    }  # fmt: skip
    if checked["problems"]:
        result["verdict"] = "invalid_records"
    elif missing:
        result["verdict"] = "incomplete"
    elif all(criteria.values()):
        result["verdict"] = "H_accepted"
        result["accepted_range"] = [min(protocol.CALIBRATION_PRIMARY), max(protocol.CALIBRATION_PRIMARY)]
    elif normalized_exact and visits_agree:
        result["fallback"] = _table_fallback(visits)
        result["verdict"] = "table_accepted" if result["fallback"]["passed"] else "not_established"
        if result["fallback"]["passed"]:
            result["accepted_range"] = [min(protocol.CALIBRATION_PRIMARY), max(protocol.CALIBRATION_PRIMARY)]
    else:
        result["verdict"] = "not_established"
    return result


def _applied_inspect(record: dict) -> bool:
    reply = record.get("reply")
    return record["status"] == "replied" and reply is not None and reply["status"] == "ok"


def _table_fallback(visits: dict) -> dict:
    """Option B, predeclared: fit on even-indexed primary points, predict the odd-indexed ones within 0.01 dB."""
    points = sorted(protocol.CALIBRATION_PRIMARY)
    fit, held_out = points[0::2], points[1::2]

    def mean(point, key):
        return sum(p[key] for p in visits[point]) / len(visits[point])

    table = [(mean(p, "normalized"), mean(p, "db")) for p in fit]
    errors = {str(p): _interpolate(table, mean(p, "normalized")) - mean(p, "db") for p in held_out}
    worst = max(abs(e) for e in errors.values())
    return {"fit_points": fit, "held_out_points": held_out, "errors_db": errors, "max_error_db": worst,
            "passed": worst <= DB_TOLERANCE}  # fmt: skip


def _restore_summary(start, restores: list, final) -> dict:
    """Start = the initial inspect's gain (step 0 must expect exactly it); exact = restored and re-inspected equal."""
    if start is None:
        return {"start": None, "restored": None, "final_inspect": final, "exact": False}
    restored = restores[-1]["reply"]["observed"]["after"] if restores else None
    exact = restored is not None and restored["normalized"] == start["normalized"] and final == {
        "normalized": start["normalized"], "db": start["db"]}  # fmt: skip
    return {"start": start, "restored": restored, "final_inspect": final, "exact": exact}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", type=pathlib.Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args(argv)
    records = [json.loads(line) for line in args.log.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = evaluate(records, args.run_id)
    text = json.dumps(result, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
