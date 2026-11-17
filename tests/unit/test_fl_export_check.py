"""Tests for the FL Studio gain-trial export check (``scripts/fl_studio/check_exports.py``).

Synthetic numerical WAV files written to ``tmp_path`` (unit-test signals, not audio material or evaluation data)
stand in for FL Studio's baseline and candidate exports.
"""

import hashlib
import importlib.util
import json
import math
import pathlib

import numpy as np
import pytest
import soundfile as sf

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "fl_studio" / "check_exports.py"
_spec = importlib.util.spec_from_file_location("check_exports", SCRIPT)
check_exports = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_exports)

RATE = 44100
ROLES = {"kick": "Kick.wav", "bass": "Bass.wav", "master": "Master.wav"}


def signals(n: int = 22050):
    rng = np.random.default_rng(5)
    kick = (rng.standard_normal(n) * 3000).astype(np.int16)
    bass = (np.sin(2 * np.pi * 55 * np.arange(n) / RATE) * 8000).astype(np.int16)
    return kick, bass


def export(directory: pathlib.Path, kick, bass, *, subtype="PCM_16", rate=RATE, stereo=False):
    directory.mkdir(parents=True, exist_ok=True)
    master = np.clip(kick.astype(np.int32) + bass, -32768, 32767).astype(np.int16)
    for name, values in (("Kick.wav", kick), ("Bass.wav", bass), ("Master.wav", master)):
        data = np.stack([values, values], axis=1) if stereo else values
        if subtype == "PCM_16":
            sf.write(str(directory / name), data, rate, subtype=subtype)
        else:
            sf.write(str(directory / name), data.astype(np.float64) / 32768.0, rate, subtype=subtype)


def scaled(values: np.ndarray, db: float) -> np.ndarray:
    return np.round(values * 10 ** (db / 20)).astype(np.int16)


def test_profile_hashes_frames_and_gain(tmp_path):
    kick, bass = signals()
    export(tmp_path / "base", kick, bass)
    export(tmp_path / "cand", kick, scaled(bass, -0.5))
    report = check_exports.check(tmp_path / "base", tmp_path / "cand", ROLES, "bass", 4410)
    entry = report["files"]["baseline"]["kick"]
    assert entry["sha256"] == hashlib.sha256((tmp_path / "base" / "Kick.wav").read_bytes()).hexdigest()
    assert (entry["samplerate"], entry["channels"], entry["frames"], entry["subtype"]) == (RATE, 1, 22050, "PCM_16")
    assert report["all_meet_profile_v1"] and report["common_samplerate"] and report["common_frames"]
    assert report["alignment"]["kick"] == {"bit_identical": True, "lag_samples": 0}
    bass_report = report["alignment"]["bass"]
    assert not bass_report["bit_identical"] and bass_report["lag_samples"] == 0
    assert math.isclose(bass_report["gain_db"], -0.5, abs_tol=0.01)  # PCM_16 rounding of the scaled stem
    assert bass_report["residual_ratio"] < 1e-6
    assert "gain_db" not in report["alignment"]["master"]


def test_stereo_and_24_bit_exports_are_reported_not_converted(tmp_path):
    kick, bass = signals()
    export(tmp_path / "base", kick, bass, subtype="PCM_24", stereo=True)
    export(tmp_path / "cand", kick, bass, subtype="PCM_24", stereo=True)
    before = (tmp_path / "base" / "Bass.wav").read_bytes()
    report = check_exports.check(tmp_path / "base", tmp_path / "cand", ROLES, "bass", 4410)
    entry = report["files"]["candidate"]["bass"]
    assert not entry["meets_profile_v1"] and entry["profile_mismatches"] == ["2 channels", "subtype PCM_24"]
    assert not report["all_meet_profile_v1"]
    assert (tmp_path / "base" / "Bass.wav").read_bytes() == before  # originals are only read
    assert report["alignment"]["bass"]["gain_db"] == pytest.approx(0.0, abs=1e-12)


def test_other_rate_and_unequal_frames_are_reported(tmp_path):
    kick, bass = signals()
    export(tmp_path / "base", kick, bass, rate=48000)
    export(tmp_path / "cand", kick[:-10], bass[:-10], rate=48000)
    report = check_exports.check(tmp_path / "base", tmp_path / "cand", ROLES, "bass", 4410)
    assert report["files"]["baseline"]["master"]["profile_mismatches"] == ["48000 Hz"]
    assert report["common_samplerate"] and not report["common_frames"]
    assert (
        report["alignment"]["bass"]["gain_db"] is None and "frame counts differ" in report["alignment"]["bass"]["note"]
    )


def test_a_timeline_offset_is_reported_as_a_lag(tmp_path):
    kick, bass = signals()
    export(tmp_path / "base", kick, bass)
    shifted = np.concatenate([np.zeros(37, np.int16), kick[:-37]])
    export(tmp_path / "cand", shifted, bass)
    report = check_exports.check(tmp_path / "base", tmp_path / "cand", ROLES, "bass", 4410)
    assert report["alignment"]["kick"] == {"bit_identical": False, "lag_samples": 37}
    assert report["alignment"]["bass"]["lag_samples"] == 0


def test_silent_stems_have_no_lag(tmp_path):
    zeros = np.zeros(4410, np.int16)
    export(tmp_path / "base", zeros, zeros)
    export(tmp_path / "cand", zeros, zeros)
    report = check_exports.check(tmp_path / "base", tmp_path / "cand", ROLES, "kick", 441)
    assert report["alignment"]["kick"]["lag_samples"] is None and report["alignment"]["kick"]["gain_db"] is None


def test_command_line_writes_the_report(tmp_path, capsys):
    kick, bass = signals(4410)
    export(tmp_path / "base", kick, bass)
    export(tmp_path / "cand", kick, scaled(bass, -1.0))
    out = tmp_path / "report.json"
    args = ["--baseline", str(tmp_path / "base"), "--candidate", str(tmp_path / "cand"), "--changed", "bass",
            "--out", str(out)] + [arg for role, name in ROLES.items() for arg in ("--role", f"{role}={name}")]  # fmt: skip
    assert check_exports.main(args) == 0
    assert json.loads(out.read_text())["changed_role"] == "bass"
    assert '"changed_role": "bass"' in capsys.readouterr().out


def test_prefixed_names_in_one_folder(tmp_path):
    kick, bass = signals()
    export(tmp_path / "base", kick, bass)
    export(tmp_path / "cand", kick, scaled(bass, -0.5))
    folder = tmp_path / "both"
    folder.mkdir()
    for condition, source in (("trap-baseline", "base"), ("trap-candidate", "cand")):
        for name in ROLES.values():
            (folder / f"{condition}_{name}").write_bytes((tmp_path / source / name).read_bytes())
    roles = {role: f"trap-baseline_{name}:trap-candidate_{name}" for role, name in ROLES.items()}
    report = check_exports.check(folder, folder, roles, "bass", 4410)
    assert report["files"]["candidate"]["bass"]["file"].endswith("trap-candidate_Bass.wav")
    assert report["alignment"]["kick"]["bit_identical"]
    assert math.isclose(report["alignment"]["bass"]["gain_db"], -0.5, abs_tol=0.01)
    assert check_exports._roles(["kick=a.wav:b.wav"]) == {"kick": "a.wav:b.wav"}


@pytest.mark.parametrize("roles", [["kick"], ["kick=a.wav", "kick=b.wav"], ["=a.wav"], ["kick=a.wav:"], ["kick=a:b:c"]])
def test_bad_roles_are_rejected(roles):
    with pytest.raises(SystemExit):
        check_exports._roles(roles)


def test_changed_role_must_be_listed(tmp_path):
    with pytest.raises(ValueError, match="changed role"):
        check_exports.check(tmp_path, tmp_path, ROLES, "snare", 10)
