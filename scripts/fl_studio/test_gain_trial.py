"""Local contract checks for the FL host script. Run with standard Python: python scripts/fl_studio/test_gain_trial.py

The simulated host modules expose only MIDI-scripting names documented in Image-Line's MIDI scripting manual, so a
call to an undocumented name fails here instead of inside FL Studio. The defaults reproduce the live trial of
2026-11-18 as pasted by the owner from FL Studio Producer Edition 26.1.7 build 5654, scripting API 45: project
metadata title "Thesis Bass Bridge Test", window title "FL Studio 2026", insert 2 "Bass", 0.800000 / 0.000000 dB
before and 0.780000 / -0.574523 dB after one write.
"""

import ast
import contextlib
import io
import runpy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("device_gain_trial.py")
# Calls documented in Image-Line's MIDI scripting manual (module -> names, API version): checked against the source.
DOCUMENTED = {
    "general": {"getVersion", "safeToEdit", "getProjectTitle"},  # 1, 29, 38
    "ui": {"getVersion", "getProgTitle"},  # 1, 1
    "mixer": {"trackCount", "getTrackCount", "getTrackName", "getTrackVolume", "setTrackVolume"},  # 1, 38, 1, 1, 1
    "transport": {"isPlaying"},  # 1
}
PROJECT = "Thesis Bass Bridge Test"  # live project metadata title
WINDOW = "FL Studio 2026"  # live ui.getProgTitle(): no project name
LIVE_AFTER_DB = -0.574523  # live read-back after writing 0.780000
BASE = dict(PROJECT_TITLE=PROJECT, INSERT_INDEX=2, INSERT_NAME="Bass")
APPLY = dict(MODE="apply", REFERENCE=(0.8, 0.0), INTENDED_DB_CHANGE=-1.0, TARGET_NORMALIZED=0.78)


class TrialTest(unittest.TestCase):
    def setUp(self):
        self.level = (0.8, 0.0)
        self.writes = []
        self.api = 45
        self.project = PROJECT

        def set_volume(index, value):
            self.writes.append((index, value))
            self.level = (value, LIVE_AFTER_DB)

        self.host = {
            "general": SimpleNamespace(getVersion=lambda: self.api, safeToEdit=lambda: True,
                                       getProjectTitle=lambda: self.project),
            "ui": SimpleNamespace(getVersion=lambda _: "Producer Edition v26.1.7 [build 5654]",
                                  getProgTitle=lambda: WINDOW),
            "mixer": SimpleNamespace(trackCount=lambda: 127, getTrackName=lambda _: "Bass",
                                     getTrackVolume=lambda _, mode: self.level[mode],
                                     setTrackVolume=set_volume),
            "transport": SimpleNamespace(isPlaying=lambda: False),
        }  # fmt: skip

    def run_script(self, **config):
        with patch.dict(sys.modules, self.host):
            trial = runpy.run_path(str(SCRIPT))
        callback = trial["OnInit"]
        callback.__globals__.update(BASE)
        callback.__globals__.update(config)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            callback()
        return output.getvalue()

    def test_script_calls_only_documented_names(self):
        used = {}
        for node in ast.walk(ast.parse(SCRIPT.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in DOCUMENTED:
                used.setdefault(node.value.id, set()).add(node.attr)
        for module, names in used.items():
            self.assertLessEqual(names, DOCUMENTED[module], module)
        self.assertIn("getProjectTitle", used["general"])  # identity comes from the project metadata title

    def test_inspect_records_identity_and_gain_as_live(self):
        output = self.run_script()
        for line in ("scripting_api=45", f"window_title='{WINDOW}'", f"project_title='{PROJECT}'",
                     "project_identity_check=metadata title matches; file path not verified by this API",
                     "insert=2", "insert_name='Bass'", "current_normalized=0.800000",
                     "current_db=0.000000"):  # fmt: skip
            self.assertIn(f"RESULT {line}", output)
        self.assertEqual(self.writes, [])

    def test_window_title_is_recorded_not_checked(self):
        self.host["ui"].getProgTitle = lambda: "something else entirely"
        self.assertIn("RESULT current_db=0.000000", self.run_script())  # identity rests on the metadata title

    def test_live_write_records_intent_and_measured_change_separately(self):
        output = self.run_script(**APPLY)
        for line in ("intended_db_change=-1.000000", "sent_normalized=0.780000", "candidate_normalized=0.780000",
                     "candidate_db=-0.574523", "measured_db_change=-0.574523",
                     "intended_minus_measured_db=-0.425477"):  # fmt: skip
            self.assertIn(f"RESULT {line}", output)
        self.assertIn("bounded normalized-fader change; measured dB change differs", output)
        self.assertIn("APPLIED", output)  # the write itself was read back; the intended dB change was not reached
        self.assertEqual(self.writes, [(2, 0.78)])

    def test_resending_the_same_command_is_refused_before_the_write(self):
        self.run_script(**APPLY)
        with self.assertRaisesRegex(RuntimeError, "outside the reference tolerances"):
            self.run_script(**APPLY)  # live: refused by the baseline check
        self.assertEqual(self.writes, [(2, 0.78)])  # exactly one write, no second relative cut
        self.assertIn("RESULT current_db=-0.574523", self.run_script())  # inspect after the refusal

    def test_reopened_project_verifies_the_candidate(self):
        self.level = (0.78, LIVE_AFTER_DB)  # live after save and reopen: 0.780000 / -0.574523 dB
        self.assertIn("VERIFIED", self.run_script(MODE="verify", REFERENCE=(0.78, LIVE_AFTER_DB)))
        self.level = (0.78, LIVE_AFTER_DB - 0.06)
        with self.assertRaises(RuntimeError):
            self.run_script(MODE="verify", REFERENCE=(0.78, LIVE_AFTER_DB))
        self.assertEqual(self.writes, [])

    def test_reject_unsafe_target_states_before_write(self):
        for overrides in (
            {"PROJECT_TITLE": "trap-baseline"},
            {"PROJECT_TITLE": ""},
            {"INSERT_INDEX": 0},  # Master
            {"INSERT_INDEX": 126},  # the "current" track
            {"INSERT_NAME": "Kick"},
            {"INSERT_NAME": "bass"},  # exact name, case included
            {"TARGET_NORMALIZED": 0.84},  # a raise
            {"TARGET_NORMALIZED": 0.7},  # cut beyond 0.03
            {"TARGET_NORMALIZED": 0.799},  # cut within the read-back tolerance
            {"INTENDED_DB_CHANGE": None},
            {"INTENDED_DB_CHANGE": 0.5},
            {"INTENDED_DB_CHANGE": -3.5},
            {"REFERENCE": (0.8, -1.0)},
            {"MODE": "write"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(RuntimeError):
                self.run_script(**(APPLY | overrides))
        self.assertEqual(self.writes, [])

    def test_reject_other_project_old_api_and_playing_transport(self):
        self.project = "Some Other Project"
        with self.assertRaisesRegex(RuntimeError, "metadata title"):
            self.run_script(**APPLY)
        self.project = PROJECT
        self.api = 37  # getProjectTitle needs API 38
        with self.assertRaises(RuntimeError):
            self.run_script(**APPLY)
        self.api = 45
        self.host["transport"].isPlaying = lambda: True
        with self.assertRaises(RuntimeError):
            self.run_script(**APPLY)
        self.assertEqual(self.writes, [])

    def test_noop_readback_fails_without_retry(self):
        def ignore(index, value):
            self.writes.append((index, value))

        self.host["mixer"].setTrackVolume = ignore
        with self.assertRaises(RuntimeError):
            self.run_script(**APPLY)
        self.assertEqual(self.writes, [(2, 0.78)])


if __name__ == "__main__":
    unittest.main()
