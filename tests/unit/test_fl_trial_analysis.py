"""Tests for the manual FL Studio trial analysis (``scripts/fl_studio/analyze_trial.py``).

Small synthetic int16 stereo WAV files in ``tmp_path`` (unit-test signals, not audio material or evaluation data)
stand in for FL Studio's split exports.
"""

import hashlib
import importlib.util
import json
import math
import pathlib

import numpy as np
import pytest
import soundfile as sf

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "fl_studio" / "analyze_trial.py"
_spec = importlib.util.spec_from_file_location("analyze_trial", SCRIPT)
analyze_trial = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(analyze_trial)

RATE = 44100


def test_mono_rule_is_round_half_even_of_the_exact_mean():
    left = np.array([1, 2, -1, -2, 32767, -32768, 3, 0], dtype=np.int16)
    right = np.array([0, 1, 0, -1, 32767, -32768, -2, 0], dtype=np.int16)
    mono = analyze_trial.mono_average(np.stack([left, right], axis=1))
    # halves: 0.5 -> 0, 1.5 -> 2, -0.5 -> -0, -1.5 -> -2; extremes stay in range; 0.5 -> 0
    assert mono.dtype == np.int16 and mono.tolist() == [0, 2, 0, -2, 32767, -32768, 0, 0]
    same = analyze_trial.mono_average(np.stack([left, left], axis=1))
    np.testing.assert_array_equal(same, left)  # identical channels give the channel exactly


def test_channel_report_measures_cancellation():
    rng = np.random.default_rng(1)
    x = (rng.standard_normal(4096) * 3000).astype(np.int16)
    same = analyze_trial.channel_report(np.stack([x, x], axis=1))
    assert same["identical_channels"] and same["max_abs_left_minus_right"] == 0
    assert same["mean_energy_vs_channel_energy_db"] == pytest.approx(0.0, abs=1e-12)
    assert same["side_energy_vs_mid_energy_db"] is None  # no side signal at all
    opposite = analyze_trial.channel_report(np.stack([x, -x], axis=1))
    assert opposite["correlation"] == pytest.approx(-1.0) and opposite["mean_energy_vs_channel_energy_db"] is None
    near = analyze_trial.channel_report(np.stack([x, (-0.9 * x).astype(np.int16)], axis=1))
    assert near["mean_energy_vs_channel_energy_db"] < -15  # substantial cancellation is visible


def test_rounding_noise_reference_and_band_spans():
    noise = analyze_trial.rounding_noise_band_energy()
    window = 0.5 * (1 - np.cos(2 * np.pi * (np.arange(1024) - 0.5) / 1024))
    per_bin = float(np.sum(window**2)) / 12
    assert per_bin == pytest.approx(32.0, rel=1e-3) and noise.shape == (21,)
    assert noise[1] == pytest.approx(per_bin * (0.944 * 1 + 2 + 0.611 * 1), rel=1e-9)  # band 1: p4..p7, 1 line each
    spans = analyze_trial.band_spans_hz()
    assert spans[0][0] == 0.0 and spans[-1][1] < RATE / 2 and all(a < b for a, b in spans)


def test_rounding_noise_includes_nyquist_in_partition_0():
    # Regression (Codex review): the reproduced dist10 mapping accumulates every bin 0..512 into partitions, Nyquist
    # included (the FFT floor excludes bin 512, the partition loop does not); bins 465..512 all fall in partition 0.
    from autonomous_mixing.adapters.dsp_analysis.partitions import dist10_long_partition_map

    partition = dist10_long_partition_map()
    assert partition.size == 513 and partition[512] == 0 and set(partition[465:].tolist()) == {0}
    per_bin = analyze_trial.rounding_noise_per_bin()
    eb = analyze_trial.rounding_noise_partition_energy()
    assert eb[0] == pytest.approx(49 * per_bin, rel=1e-12)  # bin 0, bins 465..511 and Nyquist 512
    assert eb[0] != pytest.approx(48 * per_bin, rel=1e-6)  # the former reference without Nyquist
    assert eb.sum() == pytest.approx(513 * per_bin, rel=1e-12)
    band_0 = analyze_trial.rounding_noise_band_energy()[0]
    # band 0: w1 = 1.0 x p0, interior p1..p3 (one bin each), w2 = 0.056 x p4 (one bin)
    assert band_0 == pytest.approx(per_bin * (49 + 3 + 0.056), rel=1e-12)


def write_exports(directory: pathlib.Path, prefix: str, kick_gain: float):
    n = 576 * 12
    t = np.arange(n) / RATE
    kick = np.where((t % 0.05) < 0.02, np.sin(2 * np.pi * 60 * t) * 12000 * kick_gain, 0.0)
    bass = np.sin(2 * np.pi * 45 * t) * 9000
    bass[: 576 * 4] = 0.0  # silent 808 windows at the start
    stems = {"Kick": np.round(kick).astype(np.int16), "Bass": np.round(bass).astype(np.int16)}
    stems["Master"] = np.clip(stems["Kick"].astype(np.int32) + stems["Bass"], -32768, 32767).astype(np.int16)
    directory.mkdir(parents=True, exist_ok=True)
    for name, mono in stems.items():
        sf.write(str(directory / f"{prefix}_{name}.wav"), np.stack([mono, mono], axis=1), RATE, subtype="PCM_16")


def test_end_to_end_report(tmp_path):
    exports, out = tmp_path / "exports", tmp_path / "analysis"
    write_exports(exports, "base", 1.0)
    write_exports(exports, "cand", 10 ** (-3 / 20))
    originals = {p.name: p.read_bytes() for p in exports.iterdir()}
    report = analyze_trial.analyze(exports, out, {"baseline": "base", "candidate": "cand"},
                                   {"kick": "Kick", "808": "Bass"}, "Master")  # fmt: skip
    assert {p.name: p.read_bytes() for p in exports.iterdir()} == originals  # inputs only read
    entry = report["inputs"]["candidate/kick"]
    assert entry["source_sha256"] == hashlib.sha256((exports / "cand_Kick.wav").read_bytes()).hexdigest()
    assert entry["channels"]["identical_channels"]
    derived = sf.read(entry["derived"], dtype="int16")[0]
    assert (
        derived.ndim == 1
        and entry["derived_sha256"] == hashlib.sha256(pathlib.Path(entry["derived"]).read_bytes()).hexdigest()
    )
    kick_to_808, bass_to_kick = report["directions"]["kick -> 808"], report["directions"]["808 -> kick"]
    assert kick_to_808["eligible_frames"] == bass_to_kick["eligible_frames"] == 10  # calls 2..11
    # 808 silent until sample 2304: windows [576k - 768, 576k + 256) of calls 2 and 3 hold no nonzero sample
    assert kick_to_808["silent_target_frames"] == [2, 3]
    strong = bass_to_kick["change_by_target_energy_above_rounding_noise"][-1]
    assert strong["cells"] and strong["median_change_db"] == pytest.approx(3.0, abs=0.05)
    assert report["master"]["rms_change_db"] < 0 and math.isfinite(report["master"]["peak_change_db"])
    saved = json.loads((out / "trial-analysis.json").read_text())
    assert saved["settings"]["mono_rule"].startswith("m = round_half_even")
    assert "band_hz" not in saved and len(saved["band_hz_nominal"]) == 21
    assert "465-512" in saved["settings"]["band_hz_nominal"]


def test_output_folder_must_differ_from_the_exports(tmp_path):
    with pytest.raises(SystemExit, match="--out must differ"):
        analyze_trial.main(["--exports", str(tmp_path), "--out", str(tmp_path)])


def test_other_rates_and_subtypes_are_refused(tmp_path):
    sf.write(str(tmp_path / "x.wav"), np.zeros((10, 2)), 48000, subtype="PCM_16")
    with pytest.raises(SystemExit, match="44,100 Hz PCM_16"):
        analyze_trial.read_int16(tmp_path / "x.wav")
