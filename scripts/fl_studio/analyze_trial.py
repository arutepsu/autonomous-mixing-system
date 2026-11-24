"""Before/after analysis of a manual FL Studio two-stem gain trial (manual feasibility trial).

Usage (repository root, repository environment):

    uv run python scripts/fl_studio/analyze_trial.py --exports <dir> --out <analysis dir> \
        [--baseline-prefix trap-baseline] [--candidate-prefix trap-candidate] \
        [--stem kick=Kick --stem 808=Bass] [--master Master]

Inputs are FL Studio split exports named ``<prefix>_<insert>.wav``; they are only read. Steps:

1. Channels: for every stem export, the left/right relation before any fold-down (identical channels, largest
   |L - R|, channel correlation, and the energy of the arithmetic mean relative to the mean channel energy in dB:
   0 dB means no cancellation, large negative values mean the mean cancels).
2. Derived mono analysis input (trial rule, not the production WAV profile): ``m = round_half_even((L + R) / 2)``
   computed exactly from the int16 counts (``L + R`` in int64; the halves are exact in float64), cast to int16.
   The result always lies in [-32768, 32767]. The same rule is applied to all four stem exports; each derived
   array is written as a mono PCM_16 WAV with its SHA-256 for reproduction. Masters stay stereo.
3. dist10 front end (``dist10_clip_to_bands``, profile v1, 44,100 Hz) on each derived mono stem, history from call
   0; directional margins (``directional_masking_margin_db``; T = masker ``thm``, E = target ``en``) for both
   directions, before and after, on the calls eligible in all four clips and all 21 bands. Target windows that are
   exactly silent (``nonzero_samples == 0``) are flagged and kept out of the change statistics.
4. Master: sample peak and pooled-channel RMS (``levels`` kernels) of the stereo masters, and their change.

Diagnostic: int16 rounding noise (variance 1/12 count^2 per sample) gives about (1/12) x sum(w^2) of unnormalised FFT
energy per bin with dist10's Hann window, for every bin 0..512 (Nyquist included: the reproduced dist10 partition
mapping consumes bin 512, even though the FFT energy floor excludes it, and assigns bins 465..512 to partition 0).
Mapped through the partitions and bands like any energy, it is a reference band energy; every listed cell carries the masker's and the target's band energy relative to it (baseline, dB), and
the change statistics are also broken down by the target's (or, for the threshold, the masker's) energy above that
reference, in fixed descriptive groups. Where a band holds little more than rounding noise, a re-quantized candidate
is not a scaled copy of the baseline in that band, so its margins can move by more than the applied gain.

The report states numbers only; no tolerance, threshold or perceptual judgement is applied.
"""

import argparse
import hashlib
import json
import math
import pathlib
import sys

import numpy as np
import soundfile as sf

from autonomous_mixing.adapters.dsp_analysis.bands import DIST10_LONG_BO_44100, DIST10_LONG_BU_44100
from autonomous_mixing.adapters.dsp_analysis.front_end import dist10_clip_to_bands
from autonomous_mixing.adapters.dsp_analysis.levels import sample_peak_dbfs, sample_rms_dbfs
from autonomous_mixing.adapters.dsp_analysis.masking import directional_masking_margin_db
from autonomous_mixing.adapters.dsp_analysis.masking_preparation import (
    rounding_noise_band_energy,
)
from autonomous_mixing.adapters.dsp_analysis.masking_preparation import (
    rounding_noise_partition_energy as rounding_noise_partition_energy,
)
from autonomous_mixing.adapters.dsp_analysis.masking_preparation import (
    rounding_noise_per_bin as rounding_noise_per_bin,
)
from autonomous_mixing.adapters.dsp_analysis.partitions import dist10_long_partition_map

RATE = 44100
GRANULE, LOOKBACK, WINDOW = 576, 768, 1024
TOP = 5  # how many largest increases and decreases are listed per direction
NOISE_GROUPS_DB = (10.0, 20.0, 40.0)  # descriptive group edges for the breakdown; not thresholds or filters


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_int16(path: pathlib.Path) -> np.ndarray:
    info = sf.info(str(path))
    if info.samplerate != RATE or info.subtype != "PCM_16":
        raise SystemExit(f"{path}: needs 44,100 Hz PCM_16, got {info.samplerate} Hz {info.subtype}")
    samples, _ = sf.read(str(path), dtype="int16", always_2d=True)  # exact PCM_16 counts
    return samples


def channel_report(stereo: np.ndarray) -> dict:
    if stereo.shape[1] == 1:
        return {"channels": 1}
    left, right = stereo[:, 0].astype(np.int64), stereo[:, 1].astype(np.int64)
    energy_l, energy_r = float(np.dot(left, left)), float(np.dot(right, right))
    total = left + right
    energy_mean = float(np.dot(total, total)) / 4.0
    diff = left - right
    mean_channel = (energy_l + energy_r) / 2.0
    denominator = math.sqrt(energy_l * energy_r)
    return {
        "channels": 2,
        "identical_channels": bool(np.array_equal(left, right)),
        "max_abs_left_minus_right": int(np.max(np.abs(diff))) if diff.size else 0,
        "correlation": float(np.dot(left, right)) / denominator if denominator else None,
        "mean_energy_vs_channel_energy_db": 10 * math.log10(energy_mean / mean_channel)
        if mean_channel and energy_mean
        else None,
        "side_energy_vs_mid_energy_db": 10 * math.log10(float(np.dot(diff, diff)) / float(np.dot(total, total)))
        if np.any(diff) and np.any(total)
        else None,
    }


def mono_average(stereo: np.ndarray) -> np.ndarray:
    """Trial rule: round_half_even((L + R) / 2) on int16 counts; mono input is returned unchanged."""
    if stereo.shape[1] == 1:
        return stereo[:, 0].copy()
    total = stereo[:, 0].astype(np.int64) + stereo[:, 1].astype(np.int64)
    return np.round(total / 2.0).astype(np.int16)  # exact halves; numpy rounds half to even


def band_spans_hz() -> list[tuple[float, float]]:
    """Nominal band edges in Hz: lowest bin of partition bu to highest bin of partition bo, bins < 465 only.

    Nominal because dist10 also assigns bins 465..512 (about 20.0-22.05 kHz) to partition 0, which is band 0's lower
    endpoint (weight 1.0): band 0 therefore also holds energy far above its displayed nominal range.
    """
    partition = dist10_long_partition_map()
    spans = []
    for bu, bo in zip(DIST10_LONG_BU_44100, DIST10_LONG_BO_44100, strict=True):
        bins = [k for k in range(465) if bu <= partition[k] <= bo]
        spans.append((bins[0] * RATE / WINDOW, bins[-1] * RATE / WINDOW))
    return spans


def window_centre_seconds(call: int) -> float:
    return (GRANULE * call - LOOKBACK + WINDOW / 2) / RATE


def direction(masker: dict, target: dict, frames: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    grid = directional_masking_margin_db(masker["bands"].thm[frames], target["bands"].en[frames])
    return grid.margin_db, target["bands"].nonzero_samples[frames] == 0


def _above_noise_db(energy: np.ndarray, noise: np.ndarray) -> np.ndarray:
    return 10.0 * np.log10(energy / noise)


def _groups(delta: np.ndarray, level_db: np.ndarray) -> list[dict]:
    edges = (-np.inf, *NOISE_GROUPS_DB, np.inf)
    out = []
    for low, high in zip(edges[:-1], edges[1:], strict=True):
        chosen = delta[(level_db >= low) & (level_db < high)]
        out.append({
            "above_rounding_noise_db": [None if math.isinf(low) else low, None if math.isinf(high) else high],
            "cells": int(chosen.size),
            "median_change_db": float(np.median(chosen)) if chosen.size else None,
            "min_change_db": float(chosen.min()) if chosen.size else None,
            "max_change_db": float(chosen.max()) if chosen.size else None,
        })  # fmt: skip
    return out


def summarize(before, after, silent, frames, spans, masker_level, target_level) -> dict:
    """``masker_level``/``target_level``: baseline band energy above rounding noise (dB), (frames, 21)."""
    delta = after - before
    active = ~silent
    cells = delta[active]
    rows = np.flatnonzero(active)
    order = np.argsort(delta[active], axis=None)

    def cell(flat: int) -> dict:
        i, sb = np.unravel_index(flat, delta[active].shape)
        k = int(frames[rows[i]])
        return {
            "call": k,
            "time_s": round(window_centre_seconds(k), 4),
            "band": int(sb),
            "band_hz_nominal": [round(spans[sb][0], 1), round(spans[sb][1], 1)],
            "before_db": float(before[rows[i], sb]),
            "after_db": float(after[rows[i], sb]),
            "change_db": float(delta[rows[i], sb]),
            "masker_energy_above_rounding_noise_db": round(float(masker_level[rows[i], sb]), 1),
            "target_energy_above_rounding_noise_db": round(float(target_level[rows[i], sb]), 1),
        }

    per_band = delta[active].mean(axis=0) if rows.size else np.full(delta.shape[1], np.nan)
    per_frame = delta[active].mean(axis=1) if rows.size else np.array([])
    return {
        "eligible_frames": int(frames.size),
        "silent_target_frames": [int(frames[i]) for i in np.flatnonzero(silent)],
        "cells_compared": int(cells.size),
        "change_db": {
            "mean": float(cells.mean()) if cells.size else None,
            "median": float(np.median(cells)) if cells.size else None,
            "min": float(cells.min()) if cells.size else None,
            "max": float(cells.max()) if cells.size else None,
            "cells_increased": int((cells > 0).sum()),
            "cells_decreased": int((cells < 0).sum()),
            "cells_unchanged": int((cells == 0).sum()),
        },
        "threshold_exceeds_energy": {
            "before": int((before[active] > 0).sum()),
            "after": int((after[active] > 0).sum()),
            "became_positive": int(((before[active] <= 0) & (after[active] > 0)).sum()),
            "became_non_positive": int(((before[active] > 0) & (after[active] <= 0)).sum()),
        },
        "zero_crossings": [cell(int(f)) for f in np.flatnonzero(((before[active] > 0) != (after[active] > 0)).ravel())],
        "change_by_target_energy_above_rounding_noise": _groups(cells, target_level[active]),
        "change_by_masker_energy_above_rounding_noise": _groups(cells, masker_level[active]),
        "largest_decreases": [cell(int(f)) for f in order[:TOP]] if cells.size else [],
        "largest_increases": [cell(int(f)) for f in order[::-1][:TOP]] if cells.size else [],
        "mean_change_per_band_db": [round(float(v), 4) for v in per_band],
        "mean_change_per_frame_db": {
            str(int(frames[r])): round(float(v), 4) for r, v in zip(rows, per_frame, strict=True)
        },
    }


def level(stereo_path: pathlib.Path) -> dict:
    samples, _ = sf.read(str(stereo_path), dtype="float64", always_2d=True)  # int16 / 32768, exact
    peak, rms = sample_peak_dbfs(samples), sample_rms_dbfs(samples)
    return {"peak_dbfs": getattr(peak, "value", None), "rms_dbfs": getattr(rms, "value", None)}


def analyze(exports: pathlib.Path, out: pathlib.Path, prefixes: dict, stems: dict, master: str) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    clips, inputs = {}, {}
    for condition, prefix in prefixes.items():
        for label, insert in stems.items():
            source = exports / f"{prefix}_{insert}.wav"
            stereo = read_int16(source)
            mono = mono_average(stereo)
            derived = out / f"{prefix}_{insert}_mono-mean.wav"
            sf.write(str(derived), mono, RATE, subtype="PCM_16")
            if not np.array_equal(sf.read(str(derived), dtype="int16")[0], mono):
                raise SystemExit(f"{derived}: derived WAV does not round-trip")
            clips[(condition, label)] = {"bands": dist10_clip_to_bands(mono, sample_rate_hz=RATE)}
            inputs[f"{condition}/{label}"] = {
                "source": str(source),
                "source_sha256": sha256(source),
                "source_frames": int(stereo.shape[0]),
                "source_channels": int(stereo.shape[1]),
                "channels": channel_report(stereo),
                "derived": str(derived),
                "derived_sha256": sha256(derived),
            }
    lengths = {c["bands"].sample_count for c in clips.values()}
    if len(lengths) != 1:
        raise SystemExit(f"stem lengths differ: {sorted(lengths)}")
    eligible = np.logical_and.reduce([c["bands"].eligible for c in clips.values()])
    frames = np.flatnonzero(eligible)
    spans = band_spans_hz()
    noise = rounding_noise_band_energy()
    labels = list(stems)
    directions = {}
    for masker, target in ((labels[0], labels[1]), (labels[1], labels[0])):
        before, silent_before = direction(clips[("baseline", masker)], clips[("baseline", target)], frames)
        after, silent_after = direction(clips[("candidate", masker)], clips[("candidate", target)], frames)
        silent = silent_before | silent_after
        masker_level = _above_noise_db(clips[("baseline", masker)]["bands"].en[frames], noise)
        target_level = _above_noise_db(clips[("baseline", target)]["bands"].en[frames], noise)
        directions[f"{masker} -> {target}"] = summarize(
            before, after, silent, frames, spans, masker_level, target_level
        )
    masters = {condition: exports / f"{prefix}_{master}.wav" for condition, prefix in prefixes.items()}
    master_levels = {condition: level(path) for condition, path in masters.items()}
    for condition, path in masters.items():
        inputs[f"{condition}/master"] = {"source": str(path), "source_sha256": sha256(path)}
    report = {
        "label": "manual feasibility trial (FL Studio exports); no perceptual or listening conclusion",
        "settings": {
            "sample_rate_hz": RATE,
            "mono_rule": "m = round_half_even((L + R) / 2) on int16 counts (exact halves), cast to int16; "
            "applied to all four stem exports; masters kept stereo",
            "front_end": "adapters.dsp_analysis.front_end.dist10_clip_to_bands (profile v1), history from call 0",
            "margin": "adapters.dsp_analysis.masking.directional_masking_margin_db, T = masker thm, E = target en",
            "frames": "calls eligible in all four clips (k >= 2 and 576k + 256 <= N), all 21 bands",
            "silent_targets": "target windows with nonzero_samples == 0 are listed and excluded from the statistics",
            "band_hz_nominal": "nominal: lowest bin of partition bu to highest bin of partition bo (bins < 465), "
            "bin = k x 44100/1024; dist10 also assigns bins 465-512 (about 20.0-22.05 kHz) to partition 0, so band 0 "
            "also holds energy outside its displayed 0-172 Hz",
            "master_levels": "levels.sample_peak_dbfs and sample_rms_dbfs (pooled channels) on the stereo master",
            "rounding_noise_reference": "(1/12) x sum(w^2) per bin (dist10 Hann window) for bins 0-512 incl. Nyquist, "
            "mapped through the dist10 partitions to bands; "
            "descriptive groups at 10, 20 and 40 dB above it",
        },
        "rounding_noise_band_energy": [round(float(v), 3) for v in noise],
        "inputs": inputs,
        "band_hz_nominal": [[round(a, 1), round(b, 1)] for a, b in spans],
        "directions": directions,
        "master": {
            **master_levels,
            "peak_change_db": master_levels["candidate"]["peak_dbfs"] - master_levels["baseline"]["peak_dbfs"],
            "rms_change_db": master_levels["candidate"]["rms_dbfs"] - master_levels["baseline"]["rms_dbfs"],
        },
    }
    (out / "trial-analysis.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _pairs(values: list[str]) -> dict[str, str]:
    pairs = {}
    for value in values:
        label, sep, insert = value.partition("=")
        if not sep or not label or not insert or label in pairs:
            raise SystemExit(f"--stem must be label=Insert, each label once: {value!r}")
        pairs[label] = insert
    if len(pairs) != 2:
        raise SystemExit("exactly two --stem values are needed")
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exports", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--baseline-prefix", default="trap-baseline")
    parser.add_argument("--candidate-prefix", default="trap-candidate")
    parser.add_argument("--stem", action="append", help="label=Insert, twice (default kick=Kick, 808=Bass)")
    parser.add_argument("--master", default="Master")
    args = parser.parse_args(argv)
    if args.out.resolve() == args.exports.resolve():
        raise SystemExit("--out must differ from --exports, so the original exports stay untouched")
    stems = _pairs(args.stem or ["kick=Kick", "808=Bass"])
    prefixes = {"baseline": args.baseline_prefix, "candidate": args.candidate_prefix}
    report = analyze(args.exports, args.out, prefixes, stems, args.master)
    print(json.dumps({k: report[k] for k in ("inputs", "master")}, indent=2))
    for name, summary in report["directions"].items():
        print(name, json.dumps({k: summary[k] for k in ("eligible_frames", "silent_target_frames", "change_db",
                                                       "threshold_exceeds_energy")}))  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
