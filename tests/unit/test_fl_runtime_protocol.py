"""Parity with the frozen research protocol and calibrated runtime configuration."""

import hashlib
import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest
from fl_runtime_support import make_request

from autonomous_mixing.adapters.fl_studio import protocol
from autonomous_mixing.adapters.fl_studio.profile import calibrated_profile, make_apply_command
from autonomous_mixing.core.records.daw_control import DawBuild, DawRange
from autonomous_mixing.core.records.identifiers import VersionRef


def test_protocol_snapshot_is_exact():
    # Revised 2026-11-18 (recovery plan, ADR-032): the runtime protocol gained the additive restore_gain command,
    # so it is no longer byte-identical to the frozen research snapshot. The research file stays pinned here; the
    # runtime v2 snapshot is pinned, and behavioural parity of every existing command and reply is checked, in
    # tests/unit/test_fl_runtime_restore_protocol.py.
    root = Path(__file__).resolve().parents[2]
    trial = root / "scripts/fl_studio/bridge_protocol.py"
    assert (
        hashlib.sha256(trial.read_bytes()).hexdigest()
        == "03637001d7e953e5fe3a61a962f94c6c80f7bc01729d0e560cbac26ab373f485"
    )
    assert Path(protocol.__file__).read_bytes() != trial.read_bytes()


def test_profile_endpoints_come_from_versioned_curve_without_rounding():
    profile = calibrated_profile(reference=VersionRef("profile", "1"))
    assert profile.build == DawBuild("Producer Edition v26.1.7 [build 5654]", 45)
    assert profile.normalized_range == DawRange(0.62, 0.86)
    assert profile.fader_db_range == DawRange(protocol.h_db(0.62), protocol.h_db(0.86))
    assert profile.conversion == VersionRef("fl-mixer-h125", "v1")


def test_command_matches_frozen_codec_and_preserves_absolute_coordinates():
    request = make_request()
    command = make_apply_command(request)
    assert command["conversion"] == "fl-mixer-h125-v1"
    assert command["intended_db_change"] == request.resolution.delta_db
    assert command["target_normalized"] == protocol.db_target(request.inspection.fader.db, request.requested_change_db)
    spec = importlib.util.spec_from_file_location(
        "historical_fl_protocol", Path(__file__).resolve().parents[2] / "scripts/fl_studio/bridge_protocol.py"
    )
    historical = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(historical)
    assert historical.encode(command) == protocol.encode(command)
    assert protocol.decode(protocol.encode(command)) == command


@pytest.mark.parametrize(
    "field,value",
    [
        ("build", DawBuild("another build", 45)),
        ("conversion", VersionRef("other", "1")),
        ("maximum_cut_db", 4.0),
        ("start_db_tolerance", 0.1),
        ("normalized_range", DawRange(0.6, 0.9)),
    ],
)
def test_profile_declarations_cannot_widen_support(field, value):
    request = make_request()
    # Widened profile is structurally valid core data; the runtime adapter must reject it.
    profile = replace(request.profile, **{field: value})
    inspection = replace(
        request.inspection, request=replace(request.inspection.request, profile=profile), build=profile.build
    )
    request = replace(request, profile=profile, inspection=inspection)
    with pytest.raises(ValueError):
        make_apply_command(request)


def test_off_curve_start_is_refused_before_command_creation():
    request = make_request()
    bad = replace(request.inspection.fader, normalized=0.74)
    state = replace(request.resolution.state, confirmed_fader=bad)
    resolution = replace(request.resolution, state=state, observed_fader=bad)
    inspection = replace(request.inspection, fader=bad)
    with pytest.raises(ValueError):
        make_apply_command(replace(request, resolution=resolution, inspection=inspection))


def test_unicode_wire_limits_remain_enforced():
    with pytest.raises(protocol.ProtocolError):
        protocol.inspect_command("unicode_command_01", "é" * 128, 2, "Bass")
