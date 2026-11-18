# name=Thesis Gain Trial
"""One-insert FL Studio MIDI controller script for the gain feasibility trial (technical feasibility test).

Owner-authorized trial under ADR-009 on a disposable project or a dedicated project copy; FR-14 stays withdrawn.
The script runs inside FL Studio when it is loaded or reloaded (Script output window, Reload) as the controller
script of an enabled MIDI input. Save, reopen and export are manual.

Only calls documented in Image-Line's MIDI scripting manual are used: ``general.getVersion`` (API 1),
``general.getProjectTitle`` (API 38, the project's metadata title from Project Info), ``general.safeToEdit`` (API 29),
``ui.getVersion`` (API 1), ``ui.getProgTitle`` (API 1, program title; recorded only), ``mixer.trackCount`` (API 1;
includes Master and the "Current" track), ``mixer.getTrackName``, ``mixer.getTrackVolume`` and
``mixer.setTrackVolume`` (API 1), ``transport.isPlaying`` (API 1).

Project identity: the script checks the project's **metadata title** (``general.getProjectTitle``) against
``PROJECT_TITLE``; it does not establish the file path, and no documented call returns it. Live on FL Studio 2026
(26.1.7, API 45) ``ui.getProgTitle()`` returned only "FL Studio 2026", so the window title is recorded, not checked.

Gain: ``setTrackVolume`` takes the normalized fader value (0.0-1.0, default 0.8). ``getTrackVolume(index, mode)``
returns it normalized (mode 0) or in dB (mode 1, documented). The script never converts dB to a fader value itself:
the intended dB change is recorded separately, the normalized value is what is sent, and FL Studio's own dB read-back
is what is measured. Live, a write from 0.800000 (0.000000 dB) to 0.780000 read back as -0.574523 dB against an
intended -1 dB, so a normalized cut is not an intended dB change; that conversion needs its own design.
"""

from math import isfinite

import general
import mixer
import transport
import ui

PROJECT_TITLE = ""  # Project Info (F11) metadata title of the trial copy, e.g. "Thesis Bass Bridge Test"
INSERT_INDEX = 0  # numbered mixer insert (not Master 0, not the last "current" track)
INSERT_NAME = ""  # exact insert name shown in the mixer
MODE = "inspect"  # inspect, apply, or verify
REFERENCE = None  # (normalized, dB): baseline for apply, candidate for verify
INTENDED_DB_CHANGE = None  # apply only: the change the trial intends, in dB (negative); recorded, not converted
TARGET_NORMALIZED = None  # apply only: absolute fader value actually sent

DB_MODE = 1  # getTrackVolume mode 1 returns dB (documented; live 0.800000 read 0.000000 dB on API 45)
MIN_API_VERSION = 38  # getProjectTitle (safeToEdit needs 29)
NORMALIZED_TOLERANCE = 0.001  # read-back tolerance on the fader value (trial setting)
DB_TOLERANCE = 0.05  # read-back tolerance in dB (trial setting)
MAX_NORMALIZED_CUT = 0.03  # bounded change: normalized cut in (2 x NORMALIZED_TOLERANCE, MAX_NORMALIZED_CUT]
MAX_INTENDED_DB_CUT = 3.0  # bounded intent: -3 dB <= intended change < 0


def _record(key, value):
    print(f"RESULT {key}={value}")


def _read_gain(label):
    normalized = mixer.getTrackVolume(INSERT_INDEX, 0)
    db = mixer.getTrackVolume(INSERT_INDEX, DB_MODE)
    _record(f"{label}_normalized", f"{normalized:.6f}")
    _record(f"{label}_db", f"{db:.6f}")
    if not (0.0 < normalized <= 1.0 and isfinite(db)):
        raise RuntimeError("Expected a finite, nonzero fader setting.")
    return normalized, db


def _check_reference(normalized, db):
    if REFERENCE is None:
        raise RuntimeError("Configure REFERENCE from the recorded read-back.")
    expected_normalized, expected_db = REFERENCE
    if not (
        0.0 < expected_normalized <= 1.0
        and abs(normalized - expected_normalized) <= NORMALIZED_TOLERANCE
        and abs(db - expected_db) <= DB_TOLERANCE
    ):
        raise RuntimeError("Current fader value is outside the reference tolerances.")


def _check_identity():
    api_version = general.getVersion()
    _record("fl_studio", ui.getVersion(5))
    _record("scripting_api", api_version)
    _record("mode", MODE)
    if api_version < MIN_API_VERSION:
        raise RuntimeError(f"The trial requires MIDI scripting API {MIN_API_VERSION} or newer.")
    _record("window_title", repr(ui.getProgTitle()))  # recorded only: live it held no project name
    project_title = general.getProjectTitle()
    _record("project_title", repr(project_title))
    if not PROJECT_TITLE or project_title != PROJECT_TITLE:
        raise RuntimeError("Project metadata title does not match the designated trial.")
    _record("project_identity_check", "metadata title matches; file path not verified by this API")
    if not 1 <= INSERT_INDEX <= mixer.trackCount() - 2:
        raise RuntimeError("Configure a numbered mixer insert (not Master or the current track).")
    name = mixer.getTrackName(INSERT_INDEX)
    _record("insert", INSERT_INDEX)
    _record("insert_name", repr(name))
    if not INSERT_NAME or name != INSERT_NAME:
        raise RuntimeError("The mixer insert name does not match the configuration.")
    if transport.isPlaying():
        raise RuntimeError("Stop transport before running the trial.")
    _record("tolerance_normalized", NORMALIZED_TOLERANCE)
    _record("tolerance_db", DB_TOLERANCE)


def _check_intent(before_normalized, target):
    if INTENDED_DB_CHANGE is None or not -MAX_INTENDED_DB_CUT <= INTENDED_DB_CHANGE < 0.0:
        raise RuntimeError("Configure INTENDED_DB_CHANGE as a cut of at most 3 dB.")
    if target is None or not 0.0 < target <= 1.0:
        raise RuntimeError("Configure a target within the nonzero fader range.")
    if not 2 * NORMALIZED_TOLERANCE < before_normalized - target <= MAX_NORMALIZED_CUT:
        raise RuntimeError("Choose a normalized cut greater than 0.002 and at most 0.03.")


def OnInit():
    """Run the configured operation on script load or explicit reload."""
    if MODE not in ("inspect", "apply", "verify"):
        raise RuntimeError("Choose inspect, apply, or verify.")
    _check_identity()
    before_normalized, before_db = _read_gain("current")
    if MODE == "inspect":
        return
    _check_reference(before_normalized, before_db)
    if MODE == "verify":
        print("VERIFIED: current gain matches the recorded candidate.")
        return

    target = TARGET_NORMALIZED
    _check_intent(before_normalized, target)
    if not general.safeToEdit():
        raise RuntimeError("FL Studio reports that editing is currently unavailable.")
    _record("intended_db_change", f"{INTENDED_DB_CHANGE:+.6f}")
    _record("sent_normalized", f"{target:.6f}")
    mixer.setTrackVolume(INSERT_INDEX, target)  # the only write
    after_normalized, after_db = _read_gain("candidate")
    measured = after_db - before_db
    _record("measured_db_change", f"{measured:+.6f}")
    _record("intended_minus_measured_db", f"{INTENDED_DB_CHANGE - measured:+.6f}")
    if abs(after_normalized - target) > NORMALIZED_TOLERANCE or before_db - after_db <= DB_TOLERANCE:
        raise RuntimeError("Read-back failed. Inspect the trial copy before saving.")
    if abs(INTENDED_DB_CHANGE - measured) <= DB_TOLERANCE:
        _record("label", "intended dB change reached within tolerance at this one point (mapping not generalized)")
    else:
        _record("label", "bounded normalized-fader change; measured dB change differs from the intended one")
    print("APPLIED: target matched and FL Studio reported a dB decrease.")
