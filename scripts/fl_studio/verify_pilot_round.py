"""Verify one round of the FL Studio decision pilot against the frozen rules (agent decision pilot 1).

Usage (repository root, repository environment):

    uv run python scripts/fl_studio/verify_pilot_round.py --exports <dir> --candidate-prefix <prefix> \
        --action <id> --packet-dir <agent-pilot-packet dir> --out <report.json> [--baseline-prefix trap-baseline]

Inputs are only read: the baseline and candidate split exports (``<prefix>_Kick.wav``, ``_Bass.wav``,
``_Master.wav``), ``decision-packet.json``, ``action-menu.json`` and the verifier-only ``verification-sets.json``.
The rules are those fixed in ``docs/research/agent-decision-pilot-packet.md`` before the agent was run, for the goal
"make kick more dominant then bass" (protected stem P = kick, covering stem C = 808 / insert "Bass"):

- integrity (exact): equal frame counts and sample rates across the six exports, lag 0 per role, and the untouched
  stem (kick) decoded bit-identically to the baseline;
- baseline agreement (gate): the baseline exports, run through the same chain, must reproduce the frozen 808 -> kick
  evidence margins exactly; otherwise the frozen cells do not describe these exports and the round is not verified
  (the guard set's agreement is reported too; it is descriptive and does not gate);
- primary: over the frozen 808 -> kick evidence cells E, median (after - before) <= -1.0 dB and fewer cells with a
  positive margin after than n_E; unusable cells (target window now silent, undefined margin) make it not verified;
- 808 level guard (owner-approved): candidate 808 RMS - baseline 808 RMS >= -3.25 dB, pooled channels, stereo stem;
- master guard: candidate master sample peak <= -1.0 dBFS and candidate RMS - baseline RMS >= -1.5 dB;
- descriptive only: kick -> 808 over the frozen guard cells G, median change and share of the at-risk cells
  (baseline margin <= 0) that newly exceed.

The execution tolerance (displayed fader change within 0.25 dB of the intended change) needs the displayed FL Studio
readings; they are passed with ``--displayed-before``/``--displayed-after`` when recorded and are otherwise reported
as unknown. The measured stem gain is reported next to it but does not replace it. Prints and writes the report;
the outcome is accept, roll back or not verified.
"""

import argparse
import hashlib
import importlib.util
import json
import math
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trial = _load("analyze_trial")
exports_check = _load("check_exports")

from autonomous_mixing.adapters.dsp_analysis.front_end import dist10_clip_to_bands  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.masking import directional_masking_margin_db  # noqa: E402

ROLES = {"kick": "Kick", "808": "Bass", "master": "Master"}
PROTECTED, COVERING = "kick", "808"
PRIMARY_MEDIAN_DB = -1.0  # pilot setting
BASS_RMS_GUARD_DB = -3.25  # owner-approved pilot setting
MASTER_PEAK_DBFS = -1.0  # pilot setting
MASTER_RMS_GUARD_DB = -1.5  # pilot setting
EXECUTION_TOLERANCE_DB = 0.25  # pilot setting
MAX_LAG = 4410


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def margins(masker, target, cells: list[list]) -> tuple[np.ndarray, list[list[int]]]:
    """Margins at frozen [call, band] cells; also the cells that became unusable (silent target, undefined)."""
    calls = np.array([c[0] for c in cells], dtype=int)
    bands = np.array([c[1] for c in cells], dtype=int)
    grid = directional_masking_margin_db(masker.thm[calls], target.en[calls]).margin_db  # (cells, 21)
    values = grid[np.arange(calls.size), bands]
    silent = target.nonzero_samples[calls] == 0
    unusable = [
        [int(k), int(b)] for k, b, s, v in zip(calls, bands, silent, values, strict=True) if s or not np.isfinite(v)
    ]
    return values, unusable


def front_end(path: pathlib.Path):
    return dist10_clip_to_bands(trial.mono_average(trial.read_int16(path)), sample_rate_hz=trial.RATE)


def verify(exports, baseline_prefix, candidate_prefix, action_id, packet_dir, displayed=(None, None)) -> dict:
    packet_path, menu_path = packet_dir / "decision-packet.json", packet_dir / "action-menu.json"
    sets_path = packet_dir / "verification-sets.json"
    sets = json.loads(sets_path.read_text(encoding="utf-8"))
    if sets["decision_packet_sha256"] != sha256(packet_path):
        raise SystemExit("verification-sets.json does not belong to this decision-packet.json")
    menu = {a["id"]: a for a in json.loads(menu_path.read_text(encoding="utf-8"))["actions"]}
    if action_id not in menu:
        raise SystemExit(f"{action_id} is not on the action menu")
    action = menu[action_id]
    files = {
        cond: {role: exports / f"{prefix}_{insert}.wav" for role, insert in ROLES.items()}
        for cond, prefix in (("baseline", baseline_prefix), ("candidate", candidate_prefix))
    }
    report = {
        "label": "agent decision pilot 1: round verification (frozen rules)",
        "action": action,
        "inputs": {
            "decision_packet_sha256": sha256(packet_path),
            "action_menu_sha256": sha256(menu_path),
            "verification_sets_sha256": sha256(sets_path),
            "exports": {
                cond: {role: {"file": str(p), "sha256": sha256(p)} for role, p in roles.items()}
                for cond, roles in files.items()
            },  # fmt: skip
        },
    }
    # integrity
    described = [exports_check.describe(p) for roles in files.values() for p in roles.values()]
    alignment = {
        role: exports_check.compare(files["baseline"][role], files["candidate"][role], role == COVERING, MAX_LAG)
        for role in ROLES
    }
    integrity = {
        "common_frames": len({d["frames"] for d in described}) == 1,
        "common_samplerate": len({d["samplerate"] for d in described}) == 1,
        "lag_zero": all(a["lag_samples"] == 0 for a in alignment.values()),
        "untouched_stem_bit_identical": alignment[PROTECTED]["bit_identical"],
        "alignment": alignment,
    }
    integrity["passed"] = all(integrity[k] for k in ("common_frames", "common_samplerate", "lag_zero",
                                                     "untouched_stem_bit_identical"))  # fmt: skip
    report["integrity"] = integrity
    # levels
    levels = {cond: {role: trial.level(p) for role, p in roles.items()} for cond, roles in files.items()}
    bass_rms_change = levels["candidate"]["808"]["rms_dbfs"] - levels["baseline"]["808"]["rms_dbfs"]
    master_rms_change = levels["candidate"]["master"]["rms_dbfs"] - levels["baseline"]["master"]["rms_dbfs"]
    report["levels"] = levels
    # execution
    before, after = displayed
    displayed_change = None if before is None or after is None else after - before
    report["execution"] = {
        "intended_change_db": action.get("change_db"),
        "displayed_before_db": before,
        "displayed_after_db": after,
        "displayed_change_db": displayed_change,
        "tolerance_db": EXECUTION_TOLERANCE_DB,
        "within_tolerance": None
        if displayed_change is None or action.get("change_db") is None
        else abs(displayed_change - action["change_db"]) <= EXECUTION_TOLERANCE_DB,
        "measured_808_rms_change_db": bass_rms_change,
        "measured_808_least_squares_gain_db": alignment[COVERING].get("gain_db"),
        "note": "the rule is on FL Studio's displayed fader values; the measured stem changes are reported, not "
        "substituted for it",
    }
    # masking
    clips = {cond: {role: front_end(files[cond][role]) for role in (PROTECTED, COVERING)} for cond in files}
    directions = sets["directions"]
    primary_cells = directions[f"{COVERING} -> {PROTECTED}"]["evidence"]
    base_e, _ = margins(clips["baseline"][COVERING], clips["baseline"][PROTECTED], primary_cells)
    frozen_e = np.array([c[2] for c in primary_cells])
    cand_e, unusable_e = margins(clips["candidate"][COVERING], clips["candidate"][PROTECTED], primary_cells)
    delta_e = cand_e - frozen_e
    n_e = len(primary_cells)
    usable = not unusable_e and n_e > 0
    positive_after = int(np.sum(cand_e > 0))
    median_e = float(np.median(delta_e)) if usable else None
    report["primary"] = {
        "direction": f"{COVERING} -> {PROTECTED}",
        "n_cells": n_e,
        "baseline_reproduces_frozen_margins": bool(np.array_equal(base_e, frozen_e)),
        "max_abs_baseline_deviation_db": float(np.max(np.abs(base_e - frozen_e))) if n_e else None,
        "median_change_db": median_e,
        "positive_before": int(np.sum(frozen_e > 0)),
        "positive_after": positive_after,
        "unusable_cells": unusable_e,
        "rule": f"median change <= {PRIMARY_MEDIAN_DB} dB and positive_after < n_cells",
        "passed": bool(usable and median_e <= PRIMARY_MEDIAN_DB and positive_after < n_e),
    }
    guard_cells = directions[f"{PROTECTED} -> {COVERING}"]["guard"]
    frozen_g = np.array([c[2] for c in guard_cells])
    base_g, _ = margins(clips["baseline"][PROTECTED], clips["baseline"][COVERING], guard_cells)
    cand_g, unusable_g = margins(clips["candidate"][PROTECTED], clips["candidate"][COVERING], guard_cells)
    at_risk = frozen_g <= 0
    finite = np.isfinite(cand_g)
    report["reverse_descriptive"] = {
        "direction": f"{PROTECTED} -> {COVERING}",
        "n_cells": len(guard_cells),
        "median_change_db": float(np.median((cand_g - frozen_g)[finite])) if finite.any() else None,
        "at_risk_cells": int(at_risk.sum()),
        "newly_exceeding": int(np.sum(at_risk & finite & (cand_g > 0))),
        "share_newly_exceeding": float(np.sum(at_risk & finite & (cand_g > 0)) / at_risk.sum())
        if at_risk.any()
        else None,
        "unusable_cells": len(unusable_g),
        "baseline_reproduces_frozen_margins": bool(np.array_equal(base_g, frozen_g)),
        "rule": "descriptive only (owner decision)",
    }
    report["guards"] = {
        "bass_rms": {"change_db": bass_rms_change, "rule": f">= {BASS_RMS_GUARD_DB} dB",
                     "passed": bass_rms_change >= BASS_RMS_GUARD_DB},
        "master_peak": {"candidate_dbfs": levels["candidate"]["master"]["peak_dbfs"], "rule": f"<= {MASTER_PEAK_DBFS} dBFS",
                        "passed": levels["candidate"]["master"]["peak_dbfs"] <= MASTER_PEAK_DBFS},
        "master_rms": {"change_db": master_rms_change, "rule": f">= {MASTER_RMS_GUARD_DB} dB",
                       "passed": master_rms_change >= MASTER_RMS_GUARD_DB},
    }  # fmt: skip
    measured_pass = report["primary"]["passed"] and all(g["passed"] for g in report["guards"].values())
    execution_known = report["execution"]["within_tolerance"]
    reasons = []
    if not integrity["passed"]:
        reasons.append("export integrity failed")
    if not report["primary"]["baseline_reproduces_frozen_margins"]:
        reasons.append("baseline exports do not reproduce the frozen evidence margins")
    if n_e == 0:
        reasons.append("empty evidence set")
    if unusable_e:
        reasons.append(f"{len(unusable_e)} evidence cells unusable after the edit")
    if execution_known is False:
        reasons.append("displayed fader change outside the execution tolerance")
    report["not_verified_reasons"] = reasons
    if reasons:
        outcome = "not verified"
    elif not measured_pass:
        outcome = "roll back"
    elif execution_known is None:
        outcome = "accept on measured criteria; execution tolerance unknown (displayed fader readings not recorded)"
    else:
        outcome = "accept"
    report["measured_criteria_passed"] = bool(measured_pass)
    report["outcome"] = outcome
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exports", type=pathlib.Path, required=True)
    parser.add_argument("--baseline-prefix", default="trap-baseline")
    parser.add_argument("--candidate-prefix", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--packet-dir", type=pathlib.Path, required=True)
    parser.add_argument("--displayed-before", type=float)
    parser.add_argument("--displayed-after", type=float)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    report = verify(args.exports, args.baseline_prefix, args.candidate_prefix, args.action, args.packet_dir,
                    (args.displayed_before, args.displayed_after))  # fmt: skip
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, default=lambda v: None if isinstance(v, float) and math.isnan(v) else v) + "\n",
        encoding="utf-8",
    )
    summary = {
        k: report[k]
        for k in ("execution", "primary", "guards", "reverse_descriptive", "measured_criteria_passed", "outcome")
    }
    summary["integrity"] = {k: v for k, v in report["integrity"].items() if k != "alignment"}
    summary["primary"] = {k: v for k, v in summary["primary"].items() if k != "unusable_cells"}
    print(json.dumps(summary, indent=2))
    print("report sha256:", sha256(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
