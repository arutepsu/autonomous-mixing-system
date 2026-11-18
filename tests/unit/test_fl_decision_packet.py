"""Tests for the baseline decision packet of the agent decision pilot (``scripts/fl_studio/prepare_decision_packet.py``).

Small synthetic int16 stereo WAV files in ``tmp_path`` (unit-test signals, not audio material or evaluation data)
stand in for FL Studio's baseline split exports.
"""

import importlib.util
import json
import pathlib
import struct

import numpy as np
import pytest
import soundfile as sf

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "fl_studio" / "prepare_decision_packet.py"
_spec = importlib.util.spec_from_file_location("prepare_decision_packet", SCRIPT)
packet_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(packet_script)

RATE = 44100
N = 576 * 14


def with_title(path: pathlib.Path, title: str) -> None:
    """Append a RIFF LIST/INFO/INAM chunk as FL Studio writes it."""
    data = bytearray(path.read_bytes())
    text = title.encode("latin-1") + b"\x00"
    if len(text) % 2:
        text += b"\x00"
    inam = b"INAM" + struct.pack("<I", len(text)) + text
    body = b"INFO" + inam
    data += b"LIST" + struct.pack("<I", len(body)) + body
    data[4:8] = struct.pack("<I", len(data) - 8)
    path.write_bytes(bytes(data))


def write_baseline(directory: pathlib.Path, prefix: str = "trap-baseline") -> None:
    t = np.arange(N) / RATE
    kick = np.where((t % 0.06) < 0.02, np.sin(2 * np.pi * 70 * t) * 14000, 0.0)
    bass = np.sin(2 * np.pi * 45 * t) * 9000
    bass[: 576 * 5] = 0.0  # the 808 starts later: its first windows are exactly silent
    stems = {"Kick": np.round(kick).astype(np.int16), "Bass": np.round(bass).astype(np.int16)}
    stems["Master"] = np.clip(stems["Kick"].astype(np.int32) + stems["Bass"], -32768, 32767).astype(np.int16)
    directory.mkdir(parents=True, exist_ok=True)
    for name, mono in stems.items():
        path = directory / f"{prefix}_{name}.wav"
        sf.write(str(path), np.stack([mono, mono], axis=1), RATE, subtype="PCM_16")
        with_title(path, f"{prefix} ({name})")


def test_riff_title_and_segments(tmp_path):
    write_baseline(tmp_path)
    assert packet_script.riff_title(tmp_path / "trap-baseline_Kick.wav") == "trap-baseline (Kick)"
    plain = tmp_path / "plain.wav"
    sf.write(str(plain), np.zeros(10, np.int16), RATE, subtype="PCM_16")
    assert packet_script.riff_title(plain) is None
    active = {0: False, 1: True, 2: True, 3: False, 4: True}
    assert packet_script.segments(active.__getitem__, np.arange(5)) == [[1, 2], [4, 4]]


def test_packet_reads_only_baseline_files(tmp_path, monkeypatch):
    exports, out = tmp_path / "exports", tmp_path / "packet"
    write_baseline(exports)
    write_baseline(exports, "trap-candidate")  # present in the folder, must never be opened
    opened = []
    real = packet_script.trial.read_int16
    monkeypatch.setattr(packet_script.trial, "read_int16", lambda p: opened.append(p.name) or real(p))
    before = {p.name: p.read_bytes() for p in exports.iterdir()}
    packet = packet_script.prepare(exports, out, "trap-baseline", {"kick": "Kick", "808": "Bass"}, "Master")
    assert opened == ["trap-baseline_Kick.wav", "trap-baseline_Bass.wav", "trap-baseline_Master.wav"]
    assert {p.name: p.read_bytes() for p in exports.iterdir()} == before
    text = (out / "decision-packet.json").read_text()
    assert "candidate" not in text and packet["producer_goal"] is None
    assert packet["inserts"]["master"]["mixer_insert_index"] == 0
    assert packet["inserts"]["kick"]["riff_title"] == "trap-baseline (Kick)"
    assert "unconfirmed" in packet["inserts"]["kick"]["mixer_insert_index"]


def test_packet_categories(tmp_path):
    exports, out = tmp_path / "exports", tmp_path / "packet"
    write_baseline(exports)
    packet = packet_script.prepare(exports, out, "trap-baseline", {"kick": "Kick", "808": "Bass"}, "Master")
    kick_to_808 = packet["directions"]["kick -> 808"]
    assert kick_to_808["eligible_frames"] == 12  # calls 2..13
    assert kick_to_808["target_silent_frames"] == [2, 3, 4]  # 808 silent until sample 2880
    for d in packet["directions"].values():
        assert d["cells_masker_dependent_not_low_energy"] <= d["cells_masker_dependent"]
        assert (
            d["cells_masker_dependent"] <= d["cells_exceeding_with_elevated_masker_threshold"] <= d["cells_exceeding"]
        )
        assert "frozen_cells" not in d  # the agent-facing packet carries no verification sets
        frozen = packet["verification_sets"][d["direction"]]
        evidence = {(k, b) for k, b, _ in frozen["evidence"]}
        guard = {(k, b): m for k, b, m in frozen["guard"]}
        assert len(evidence) == d["cells_masker_dependent_not_low_energy"] and evidence <= set(guard)
        assert all(guard[cell] > 0 for cell in evidence)  # evidence cells exceed at baseline
        assert not {k for k, _ in guard} & set(d["target_silent_frames"])  # no silent target window is frozen
        assert d["cells_compared"] == 21 * (d["eligible_frames"] - len(d["target_silent_frames"]))
        for region in d["top_regions"]:
            assert region["calls"][0] <= region["calls"][1] and region["masker_dependent_cells"] > 0
    assert packet["analysis"]["low_energy_flag_db"] == 10.0
    assert json.loads((out / "decision-packet.json").read_text())["label"].startswith("agent decision pilot")
    saved = json.loads((out / "verification-sets.json").read_text())
    assert saved["decision_packet_sha256"] == packet["packet_sha256"]
    assert saved["directions"] == json.loads(json.dumps(packet["verification_sets"]))
    assert "frozen" not in (out / "decision-packet.json").read_text().replace("frozen_cells", "")


@pytest.mark.parametrize("prefix", ["trap-candidate", "Trap-Candidate-2"])
def test_candidate_prefix_is_refused(tmp_path, prefix):
    with pytest.raises(SystemExit, match="baseline exports only"):
        packet_script.main(["--exports", str(tmp_path), "--out", str(tmp_path / "p"), "--prefix", prefix])


def test_categories_require_the_target_above_the_threshold_in_quiet():
    # Three cells, all T > E with an elevated masker threshold: only the one with E >= thm_quiet is masker-dependent.
    frames = np.array([2])
    margin = np.array([[5.0, 5.0, 5.0, -1.0] + [-1.0] * 17])
    target_silent = np.array([False])
    elevated = np.zeros((1, 21), dtype=bool)
    elevated[0, :3] = True
    audible = np.zeros((1, 21), dtype=bool)
    audible[0, 0] = True  # cells 1 and 2: the target is already below the threshold in quiet
    low = np.zeros((1, 21), dtype=bool)
    spans = [(float(b), float(b + 1)) for b in range(21)]
    d = packet_script.direction_evidence("m -> t", margin, target_silent, elevated, audible, low, frames, spans)
    assert d["cells_exceeding"] == 3 and d["cells_exceeding_with_elevated_masker_threshold"] == 3
    assert d["cells_masker_dependent"] == 1 and d["frozen_cells"]["evidence"] == [[2, 0, 5.0]]
    assert len(d["frozen_cells"]["guard"]) == 21
    low[0, 0] = True  # a flagged cell leaves both frozen sets
    d = packet_script.direction_evidence("m -> t", margin, target_silent, elevated, audible, low, frames, spans)
    assert d["frozen_cells"]["evidence"] == [] and len(d["frozen_cells"]["guard"]) == 20


def test_action_menu_is_cuts_plus_abstention_with_manual_execution(tmp_path):
    exports, out = tmp_path / "exports", tmp_path / "packet"
    write_baseline(exports)
    packet = packet_script.prepare(exports, out, "trap-baseline", {"kick": "Kick", "808": "Bass"}, "Master")
    menu = json.loads((out / "action-menu.json").read_text())
    assert packet["menu_sha256"] and [a["id"] for a in menu["actions"]] == [f"A{i}" for i in range(7)]
    assert menu["actions"][0]["action"] == "abstain"
    cuts = menu["actions"][1:]
    assert {(a["insert"], a["change_db"]) for a in cuts} == {
        (insert, change) for insert in ("Kick", "Bass") for change in (-1.0, -2.0, -3.0)
    }
    assert menu["execution"]["mode"].startswith("manual") and menu["execution"]["tolerance_db"] == 0.25
