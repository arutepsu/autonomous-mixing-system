"""Baseline-only decision packet for the first agent decision pilot (FL Studio kick/808 project).

Usage (repository root, repository environment):

    uv run python scripts/fl_studio/prepare_decision_packet.py --exports <dir> --out <packet dir> \
        [--prefix trap-baseline] [--stem kick=Kick --stem 808=Bass] [--master Master]

Only the three baseline exports ``<prefix>_<insert>.wav`` (two stems and the master) are opened; no other file in
the export folder is read, so no candidate render or earlier trial result can enter the packet. The exports are
only read. The packet records:

- insert identities as far as the exports show them (file name and the RIFF ``LIST/INFO/INAM`` title FL Studio
  writes); mixer insert indices are not in the exports and must be confirmed in FL Studio;
- baseline levels: sample peak and pooled-channel RMS of each stereo stem and of the master (``levels`` kernels);
- the channel relation and the derived mono rule (``round_half_even((L + R) / 2)`` on int16 counts, as in
  ``analyze_trial.py``) used as analysis input;
- both directional masking measurements (dist10 front end and ``directional_masking_margin_db``; T = masker
  ``thm``, E = target ``en``) on the eligible calls and all 21 bands, with exceedance regions (time range and
  nominal bands) that support each observation;
- cell categories, per direction (``thm_quiet`` is the band mapping of the threshold in quiet):
  - silent target window (``nonzero_samples == 0``): excluded;
  - exceedance (``T > E``) *with an elevated masker threshold*: ``T > thm_quiet`` (not every partition at threshold
    in quiet; silent masker windows never qualify). This alone leaves open whether the target was already below the
    threshold in quiet;
  - *masker-dependent* exceedance: ``thm_quiet <= E < T``, i.e. the target is audible against the threshold in quiet
    and only the masker's elevated threshold exceeds it. Only these support an observation;
  - low-energy flag: the target's or masker's band energy in a sounding window lies within ``LOW_ENERGY_DB`` of the
    int16 rounding-noise reference, where quantization makes the evidence uncertain;
- frozen cell sets for verification, per direction, with their baseline margins: ``evidence`` (masker-dependent and not
  low-energy flagged) and ``guard`` (eligible, target window not silent, not low-energy flagged). They are written to
  ``verification-sets.json``, for the verifier only: the decision agent receives ``decision-packet.json`` (the evidence
  summary), the producer goal and ``action-menu.json``, never the frozen sets;
- ``action-menu.json``: the bounded menu of single-fader cuts plus abstention, executed manually in this pilot.

Margins are model exceedance of the dist10 threshold over the target's energy, not proven perceptual masking. The
packet states no producer goal and recommends no action.
"""

import argparse
import hashlib
import importlib.util
import json
import pathlib
import struct
import sys

import numpy as np

_spec = importlib.util.spec_from_file_location("analyze_trial", pathlib.Path(__file__).with_name("analyze_trial.py"))
trial = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trial)

from autonomous_mixing.adapters.dsp_analysis.bands import dist10_long_bands  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.front_end import dist10_clip_to_bands  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.masking import directional_masking_margin_db  # noqa: E402
from autonomous_mixing.adapters.dsp_analysis.thresholds import DIST10_LONG_QTHR_44100  # noqa: E402

LOW_ENERGY_DB = 10.0  # pilot setting for review: band energy within 10 dB of the rounding-noise reference
TOP_REGIONS = 8  # how many supporting regions are listed per direction
MENU_CUTS_DB = (-1.0, -2.0, -3.0)  # pilot setting for review: fader cuts offered per stem insert
EXECUTION_TOLERANCE_DB = 0.25  # pilot setting for review: displayed change vs intended change


def riff_title(path: pathlib.Path) -> str | None:
    """The RIFF LIST/INFO/INAM title, if present (FL Studio writes "<project> (<insert>)")."""
    data = path.read_bytes()
    position = 12
    while position + 8 <= len(data):
        chunk, size = data[position : position + 4], struct.unpack("<I", data[position + 4 : position + 8])[0]
        body = data[position + 8 : position + 8 + size]
        if chunk == b"LIST" and body[:4] == b"INFO":
            inner = 4
            while inner + 8 <= len(body):
                sub, sub_size = body[inner : inner + 4], struct.unpack("<I", body[inner + 4 : inner + 8])[0]
                if sub == b"INAM":
                    return body[inner + 8 : inner + 8 + sub_size].split(b"\x00")[0].decode("latin-1")
                inner += 8 + sub_size + (sub_size & 1)
        position += 8 + size + (size & 1)
    return None


def segments(active, calls: np.ndarray) -> list[list[int]]:
    """Runs of consecutive calls (from ``calls``) for which ``active(call)`` is true, as [first, last]."""
    runs, start, previous = [], None, None
    for k in calls:
        if active(k) and start is None:
            start = k
        if not active(k) and start is not None:
            runs.append([int(start), int(previous)])
            start = None
        previous = k
    if start is not None:
        runs.append([int(start), int(previous)])
    return runs


def seconds(first: int, last: int) -> list[float]:
    """Time span covered by the window centres of calls first..last (s)."""
    return [round(trial.window_centre_seconds(first), 4), round(trial.window_centre_seconds(last), 4)]


def direction_evidence(name, margin, target_silent, masker_elevated, target_audible, low, frames, spans) -> dict:
    """Cell categories of one direction (see the module doc); all (frames, 21) except ``target_silent`` (frames,)."""
    usable = np.broadcast_to(~target_silent[:, None], margin.shape)
    exceed = usable & (margin > 0)
    elevated = exceed & masker_elevated  # T > E and T > thm_quiet
    dependent = exceed & target_audible  # thm_quiet <= E < T (implies T > thm_quiet)
    evidence = dependent & ~low
    guard = usable & ~low
    per_band = []
    for sb in range(margin.shape[1]):
        cells = margin[usable[:, sb], sb]
        per_band.append({
            "band": sb,
            "band_hz_nominal": [round(spans[sb][0], 1), round(spans[sb][1], 1)],
            "cells": int(cells.size),
            "exceeding": int(exceed[:, sb].sum()),
            "exceeding_with_elevated_masker_threshold": int(elevated[:, sb].sum()),
            "masker_dependent": int(dependent[:, sb].sum()),
            "masker_dependent_not_low_energy": int(evidence[:, sb].sum()),
            "median_margin_db": round(float(np.median(cells)), 2) if cells.size else None,
            "max_margin_db": round(float(cells.max()), 2) if cells.size else None,
        })  # fmt: skip
    rows_with_evidence = dict(zip(frames.tolist(), dependent.any(axis=1).tolist(), strict=True))
    regions = []
    for first, last in segments(rows_with_evidence.__getitem__, frames):
        rows = np.flatnonzero((frames >= first) & (frames <= last))
        flags = dependent[rows]
        bands = sorted({int(b) for b in np.flatnonzero(flags.any(axis=0))})
        values = margin[rows][flags]
        regions.append({
            "calls": [first, last],
            "time_s": seconds(first, last),
            "bands": bands,
            "bands_hz_nominal": [round(spans[bands[0]][0], 1), round(spans[bands[-1]][1], 1)],
            "masker_dependent_cells": int(flags.sum()),
            "median_margin_db": round(float(np.median(values)), 2),
            "max_margin_db": round(float(values.max()), 2),
            "share_low_energy_flagged": round(float(low[rows][flags].mean()), 3),
        })  # fmt: skip
    regions.sort(key=lambda r: (-r["masker_dependent_cells"], r["calls"][0]))

    def frozen(mask):
        rows, bands = np.nonzero(mask)
        return [[int(frames[r]), int(b), float(margin[r, b])] for r, b in zip(rows, bands, strict=True)]

    return {
        "direction": name,
        "eligible_frames": int(frames.size),
        "target_silent_frames": [int(k) for k in frames[target_silent]],
        "cells_compared": int(usable.sum()),
        "cells_exceeding": int(exceed.sum()),
        "cells_exceeding_with_elevated_masker_threshold": int(elevated.sum()),
        "cells_masker_dependent": int(dependent.sum()),
        "cells_masker_dependent_not_low_energy": int(evidence.sum()),
        "cells_low_energy_flagged": int((usable & low).sum()),
        "median_margin_db": round(float(np.median(margin[usable])), 2),
        "per_band": per_band,
        "regions_total": len(regions),
        "top_regions": regions[:TOP_REGIONS],
        "frozen_cells": {
            "format": "[call, band, baseline margin dB]",
            "evidence": frozen(evidence),
            "guard": frozen(guard),
        },
    }


def prepare(exports: pathlib.Path, out: pathlib.Path, prefix: str, stems: dict[str, str], master: str) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    files = {label: exports / f"{prefix}_{insert}.wav" for label, insert in stems.items()}
    files["master"] = exports / f"{prefix}_{master}.wav"
    inserts, clips = {}, {}
    for label, path in files.items():
        stereo = trial.read_int16(path)
        samples = stereo.astype(np.float64) / 32768.0
        inserts[label] = {
            "insert_name_from_export": stems.get(label, master),
            "riff_title": riff_title(path),
            "mixer_insert_index": 0
            if label == "master"
            else "unconfirmed: not in the exports; the owner confirms index and name in the FL Studio mixer",
            "file": str(path),
            "sha256": trial.sha256(path),
            "frames": int(stereo.shape[0]),
            "channels": int(stereo.shape[1]),
            "peak_dbfs": round(trial.sample_peak_dbfs(samples).value, 3),
            "rms_dbfs": round(trial.sample_rms_dbfs(samples).value, 3),
        }
        if label != "master":
            mono = trial.mono_average(stereo)
            inserts[label]["channel_relation"] = trial.channel_report(stereo)
            clips[label] = dist10_clip_to_bands(mono, sample_rate_hz=trial.RATE)
    labels = list(stems)
    frames = np.flatnonzero(np.logical_and.reduce([clips[label].eligible for label in labels]))
    spans = trial.band_spans_hz()
    noise = trial.rounding_noise_band_energy()
    silent = {label: clips[label].nonzero_samples[frames] == 0 for label in labels}
    # low energy is a property of a sounding window: a silent window is its own category, not quantization noise
    low = {
        label: (10.0 * np.log10(clips[label].en[frames] / noise) < LOW_ENERGY_DB) & ~silent[label][:, None]
        for label in labels
    }
    qthr = np.array([DIST10_LONG_QTHR_44100])
    thm_quiet = dist10_long_bands(qthr, qthr.copy()).thm[0]
    elevated = {label: clips[label].thm[frames] > thm_quiet for label in labels}
    audible = {label: clips[label].en[frames] >= thm_quiet for label in labels}
    directions = {}
    for masker, target in ((labels[0], labels[1]), (labels[1], labels[0])):
        margin = directional_masking_margin_db(clips[masker].thm[frames], clips[target].en[frames]).margin_db
        name = f"{masker} -> {target}"
        directions[name] = direction_evidence(
            name, margin, silent[target], elevated[masker], audible[target], low[target] | low[masker], frames, spans
        )
    calls = clips[labels[0]].call
    activity = {
        label: [
            {"calls": run, "time_s": seconds(*run)}
            for run in segments((clips[label].nonzero_samples > 0).__getitem__, calls)
        ]
        for label in labels
    }
    packet = {
        "label": "agent decision pilot: baseline decision packet (draft for owner review)",
        "producer_goal": None,  # stated by the owner in their own words
        "source": {
            "exports_dir": str(exports),
            "files_read": [str(path) for path in files.values()],
            "rule": "only the baseline stem and master exports were opened",
        },
        "inserts": inserts,
        "analysis": {
            "sample_rate_hz": trial.RATE,
            "mono_rule": "m = round_half_even((L + R) / 2) on int16 counts, cast to int16 (stems only; master stereo)",
            "front_end": "dist10_clip_to_bands (profile v1), history from call 0",
            "margin": "directional_masking_margin_db, T = masker thm, E = target en; > 0 dB = model exceedance",
            "frames": f"calls eligible in both stems (k >= 2 and 576k + 256 <= N): {int(frames[0])}-{int(frames[-1])}",
            "silent_windows": "nonzero_samples == 0; silent target windows are excluded from the evidence",
            "threshold_in_quiet": "thm_quiet = band mapping of qthr; 'elevated masker threshold' means T > thm_quiet; "
            "'masker-dependent' means thm_quiet <= E < T: only these cells support an observation",
            "frozen_cells": "per direction, fixed before any action and kept in verification-sets.json (not part of "
            "this packet): evidence = masker-dependent and not low-energy flagged; guard = eligible, target window not "
            "silent, not low-energy flagged (flags from the baseline)",
            "low_energy_flag_db": LOW_ENERGY_DB,
            "low_energy_flag": "pilot setting for review: band energy within 10 dB of the int16 rounding-noise "
            "reference ((1/12) x sum(w^2) per bin, bins 0-512); quantization makes such cells uncertain",
            "band_hz_nominal": "nominal: bins < 465; dist10 also assigns bins 465-512 to partition 0 (band 0)",
            "caveat": "model exceedance under dist10, not proven perceptual masking",
        },
        "activity": activity,
        "directions": directions,
    }
    frozen = {name: d.pop("frozen_cells") for name, d in directions.items()}  # verifier-only
    path = out / "decision-packet.json"
    path.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
    packet["packet_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    sets = out / "verification-sets.json"
    record = {
        "label": "agent decision pilot 1: frozen verification cell sets (verifier only; not given to the agent)",
        "decision_packet_sha256": packet["packet_sha256"],
        "directions": frozen,
    }
    sets.write_text(json.dumps(record) + "\n", encoding="utf-8")
    packet["verification_sets"] = frozen
    packet["verification_sets_sha256"] = hashlib.sha256(sets.read_bytes()).hexdigest()
    menu = out / "action-menu.json"
    menu.write_text(json.dumps(action_menu(stems), indent=2) + "\n", encoding="utf-8")
    packet["menu_sha256"] = hashlib.sha256(menu.read_bytes()).hexdigest()
    return packet


def action_menu(stems: dict[str, str]) -> dict:
    """The bounded menu: abstention plus single-fader cuts on the two stem inserts (pilot settings)."""
    actions = [{"id": "A0", "action": "abstain", "insert": None, "change_db": None,
                "note": "no change; state the reason (e.g. the evidence does not support the goal or is too uncertain)"}]  # fmt: skip
    number = 1
    for label, insert in stems.items():
        for change in MENU_CUTS_DB:
            actions.append({"id": f"A{number}", "action": "fader_cut", "insert": insert, "stem": label,
                            "change_db": change, "units": "dB, mixer insert volume fader"})  # fmt: skip
            number += 1
    return {
        "label": "agent decision pilot 1: action menu (pilot settings for review)",
        "choose": "exactly one id; reply with the id, a short rationale citing packet regions and bands, and the "
        "uncertainty you see; no other parameters are accepted",
        "actions": actions,
        "execution": {
            "mode": "manual, by the owner, in FL Studio on a fresh copy of the baseline project",
            "insert_identity": "index and exact name confirmed in the FL Studio mixer before the pilot",
            "record": "the fader value FL Studio displays before and after the change, in dB",
            "tolerance_db": EXECUTION_TOLERANCE_DB,
            "rule": "executed as specified only if |(after - before) - change_db| <= tolerance_db; otherwise "
            "'not executable as specified', not verified and not replaced by another change",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exports", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--prefix", default="trap-baseline")
    parser.add_argument("--stem", action="append", help="label=Insert, twice (default kick=Kick, 808=Bass)")
    parser.add_argument("--master", default="Master")
    args = parser.parse_args(argv)
    if args.out.resolve() == args.exports.resolve():
        raise SystemExit("--out must differ from --exports, so the original exports stay untouched")
    if "candidate" in args.prefix.lower():
        raise SystemExit("the decision packet uses baseline exports only")
    stems = trial._pairs(args.stem or ["kick=Kick", "808=Bass"])
    packet = prepare(args.exports, args.out, args.prefix, stems, args.master)
    keys = ("packet_sha256", "menu_sha256", "verification_sets_sha256", "inserts")
    print(json.dumps({k: packet[k] for k in keys}, indent=2))
    for name, d in packet["directions"].items():
        keys = ("cells_compared", "cells_exceeding", "cells_exceeding_with_elevated_masker_threshold",
                "cells_masker_dependent", "cells_masker_dependent_not_low_energy", "cells_low_energy_flagged",
                "median_margin_db", "regions_total")  # fmt: skip
        print(name, {k: d[k] for k in keys})
    return 0


if __name__ == "__main__":
    sys.exit(main())
