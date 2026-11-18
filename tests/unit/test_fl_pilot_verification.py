"""Tests for the decision-pilot round verifier (``scripts/fl_studio/verify_pilot_round.py``).

Small synthetic int16 stereo WAV files in ``tmp_path`` (unit-test signals, not audio material or evaluation data)
stand in for FL Studio's baseline and candidate exports; the frozen sets come from the packet script itself.
"""

import importlib.util
import json
import pathlib

import numpy as np
import pytest
import soundfile as sf

ROOT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "fl_studio"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = _load("verify_pilot_round")
packet_script = _load("prepare_decision_packet")

RATE = 44100
N = 576 * 30


def stems(bass_gain_db: float = 0.0, kick_offset: int = 0):
    t = np.arange(N) / RATE
    kick = np.where((t % 0.1) < 0.03, np.sin(2 * np.pi * 70 * t) * 14000, 0.0)
    if kick_offset:
        kick = np.concatenate([np.zeros(kick_offset), kick[:-kick_offset]])
    bass = np.sin(2 * np.pi * 45 * t) * 12000 * 10 ** (bass_gain_db / 20)
    out = {"Kick": np.round(kick).astype(np.int16), "Bass": np.round(bass).astype(np.int16)}
    out["Master"] = np.clip(out["Kick"].astype(np.int32) + out["Bass"], -32768, 32767).astype(np.int16)
    return out


def write(directory: pathlib.Path, prefix: str, signals: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, mono in signals.items():
        sf.write(str(directory / f"{prefix}_{name}.wav"), np.stack([mono, mono], axis=1), RATE, subtype="PCM_16")


@pytest.fixture
def setup(tmp_path):
    exports, packet_dir = tmp_path / "exports", tmp_path / "packet"
    write(exports, "trap-baseline", stems())
    packet_script.prepare(exports, packet_dir, "trap-baseline", {"kick": "Kick", "808": "Bass"}, "Master")
    sets = json.loads((packet_dir / "verification-sets.json").read_text())
    assert sets["directions"]["808 -> kick"]["evidence"], "the synthetic baseline must give 808 -> kick evidence"
    return exports, packet_dir


def test_a_bass_cut_is_measured_and_judged_by_the_frozen_rules(setup):
    exports, packet_dir = setup
    write(exports, "cand", stems(bass_gain_db=-2.0))
    report = verifier.verify(exports, "trap-baseline", "cand", "A5", packet_dir)
    assert report["integrity"]["passed"] and report["primary"]["baseline_reproduces_frozen_margins"]
    assert report["execution"]["measured_808_rms_change_db"] == pytest.approx(-2.0, abs=0.02)
    assert report["execution"]["within_tolerance"] is None  # displayed readings not given
    assert report["primary"]["median_change_db"] == pytest.approx(-2.0, abs=0.05)
    assert report["guards"]["bass_rms"]["passed"]
    assert report["reverse_descriptive"]["median_change_db"] > 0  # the 808 becomes more covered: reported only
    if report["measured_criteria_passed"]:
        assert report["outcome"].startswith("accept on measured criteria")


def test_displayed_readings_decide_the_execution_rule(setup):
    exports, packet_dir = setup
    write(exports, "cand", stems(bass_gain_db=-2.0))
    inside = verifier.verify(exports, "trap-baseline", "cand", "A5", packet_dir, (0.0, -2.2))
    assert inside["execution"]["within_tolerance"] is True
    outside = verifier.verify(exports, "trap-baseline", "cand", "A5", packet_dir, (0.0, -1.5))
    assert outside["execution"]["within_tolerance"] is False and outside["outcome"] == "not verified"


def test_guards_roll_back_and_integrity_failures_are_not_verified(setup):
    exports, packet_dir = setup
    write(exports, "big", stems(bass_gain_db=-6.0))
    big = verifier.verify(exports, "trap-baseline", "big", "A6", packet_dir)
    assert not big["guards"]["bass_rms"]["passed"] and big["outcome"] == "roll back"
    write(exports, "moved", stems(bass_gain_db=-2.0, kick_offset=7))
    moved = verifier.verify(exports, "trap-baseline", "moved", "A5", packet_dir)
    assert not moved["integrity"]["untouched_stem_bit_identical"] and moved["outcome"] == "not verified"


def test_kick_cut_fails_the_primary_check(setup):
    exports, packet_dir = setup
    signals = stems()
    signals["Kick"] = np.round(signals["Kick"] * 10 ** (-2 / 20)).astype(np.int16)
    write(exports, "kickcut", signals)
    report = verifier.verify(exports, "trap-baseline", "kickcut", "A2", packet_dir)
    assert report["primary"]["median_change_db"] > 0 and not report["primary"]["passed"]


def test_inputs_must_match(setup):
    exports, packet_dir = setup
    write(exports, "cand", stems(bass_gain_db=-2.0))
    with pytest.raises(SystemExit, match="not on the action menu"):
        verifier.verify(exports, "trap-baseline", "cand", "A9", packet_dir)
    sets = json.loads((packet_dir / "verification-sets.json").read_text())
    sets["decision_packet_sha256"] = "0" * 64
    (packet_dir / "verification-sets.json").write_text(json.dumps(sets))
    with pytest.raises(SystemExit, match="does not belong"):
        verifier.verify(exports, "trap-baseline", "cand", "A5", packet_dir)


def test_a_baseline_that_does_not_reproduce_the_frozen_margins_is_not_verified(setup):
    # Regression (Codex review): the baseline agreement is a gate, checked before any accept or roll back.
    exports, packet_dir = setup
    write(exports, "cand", stems(bass_gain_db=-2.0))
    good = verifier.verify(exports, "trap-baseline", "cand", "A5", packet_dir)
    assert good["primary"]["baseline_reproduces_frozen_margins"] and good["not_verified_reasons"] == []
    path = packet_dir / "verification-sets.json"
    sets = json.loads(path.read_text())
    sets["directions"]["808 -> kick"]["evidence"][0][2] += 1e-9  # one frozen margin no longer matches the exports
    path.write_text(json.dumps(sets))
    bad = verifier.verify(exports, "trap-baseline", "cand", "A5", packet_dir)
    assert not bad["primary"]["baseline_reproduces_frozen_margins"]
    assert bad["outcome"] == "not verified"
    assert bad["not_verified_reasons"] == ["baseline exports do not reproduce the frozen evidence margins"]
    assert bad["measured_criteria_passed"] == good["measured_criteria_passed"]  # measured values are still reported
