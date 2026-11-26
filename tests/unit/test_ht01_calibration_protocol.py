"""Pinned HT01 calibration only; fake device, never live evidence."""

import hashlib
from pathlib import Path

import pytest
from fl_device_support import FakeHost, h_db, load_device

from autonomous_mixing.adapters.fl_studio import protocol as p

START = {"normalized": 0.9056249996647239, "db": 2.982499599456787}


def host(**kwargs):
    kwargs.setdefault("curve", lambda v: START["db"] if v == START["normalized"] else h_db(v))
    h = FakeHost(title="HT01 Live Calibration v001", names=("a", "b", "c", "d", "Bass"), **kwargs)
    h.volumes[5] = START["normalized"]
    return h


def answer(device, h, history, cmd):
    return p.decode(device.handle(p.encode(cmd), h, history))


def test_ht01_plan_is_separate_and_digest_is_pinned():
    assert p.HT01_PLAN_ID == "ht01-mixer-cal-v1"
    assert p.HT01_PRIMARY == (0.78, 0.80, 0.82, 0.84, 0.86, 0.88, 0.90, 0.91, 0.92)
    assert p.HT01_SEQUENCE == tuple(v for x in p.HT01_PRIMARY for v in (0.76, x, 0.94, x))
    assert len(p.HT01_SEQUENCE) == 36
    assert p.HT01_RANGE == (0.78, 0.92)
    assert p.HT01_PLAN_DIGEST == hashlib.sha256(p.HT01_PLAN_JSON.encode("ascii")).hexdigest()
    assert p.HT01_PLAN_DIGEST == "c7bcbf4aae3196d6235ffec995c8e0da7481ec9ace9ce8cdc1b31a6838358b39"
    assert p.CALIBRATION_PLAN_ID == "fl-mixer-cal-v1"
    assert p.CONVERSION_RANGE == (0.62, 0.86)


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_title", "other"),
        ("insert", {"index": 2, "name": "Bass"}),
        ("step", True),
        ("step", 36),
        ("target_normalized", 0.77),
        ("plan_digest", "0" * 64),
        ("plan", "other"),
    ],
)
def test_bad_ht01_commands_refused_without_host_access(field, value):
    d, _ = load_device("v2")
    h = host()
    cmd = p.ht01_probe_command("ht01_test_000", START, 0)
    cmd[field] = value
    assert answer(d, h, {}, cmd)["status"] == "invalid_command"
    assert not h.calls


@pytest.mark.parametrize("kwargs,error", [({"build": "other"}, "uncalibrated_build"), ({"api": 44}, "calibration_api")])
def test_ht01_build_and_api_are_checked_before_writing(kwargs, error):
    d, _ = load_device("v2")
    h = host(**kwargs)
    result = answer(d, h, {}, p.ht01_probe_command("ht01_test_000", h.reading(5), 0))
    assert result["error"] == error
    assert not h.writes


def test_ht01_requires_order_and_session_start_for_restore():
    d, _ = load_device("v2")
    h = host()
    for step in (1, "restore"):
        cmd = p.ht01_probe_command("ht01_test_" + str(step), h.reading(5), step)
        assert answer(d, h, {}, cmd)["status"] == "precondition_failed"
    assert not h.writes


def test_ht01_replay_conflict_and_exact_recorded_restore():
    d, _ = load_device("v2")
    h, history = host(), {}
    start = h.reading(5)
    cmd = p.ht01_probe_command("ht01_test_000", start, 0)
    assert answer(d, h, history, cmd)["status"] == "applied"
    assert answer(d, h, history, cmd)["observed"]["replayed"] is True
    conflict = p.ht01_probe_command(cmd["id"], start, 1)
    assert answer(d, h, history, conflict)["error"] == "duplicate_id_conflict"
    assert len(h.writes) == 1
    restore = p.ht01_probe_command("ht01_restore_000", h.reading(5), "restore")
    assert answer(d, h, history, restore)["status"] == "applied"
    assert h.reading(5) == start
    assert answer(d, h, {}, restore)["status"] == "precondition_failed"


@pytest.mark.parametrize("change", ["playing", "safe", "start", "after"])
def test_ht01_existing_write_safety(change):
    d, _ = load_device("v2")
    h = host()
    cmd = p.ht01_probe_command("ht01_test_000", h.reading(5), 0)
    if change == "playing":
        h.playing = True
    if change == "safe":
        h.safe = False
    if change == "start":
        h.volumes[5] -= 0.1
    if change == "after":
        h.fail_after_set = RuntimeError("after write")
    result = answer(d, h, {}, cmd)
    assert result["status"] == ("uncertain" if change == "after" else "precondition_failed")
    assert len(h.writes) == (1 if change == "after" else 0)


def test_ht01_production_commands_are_limited_to_the_approved_profile_scope():
    """Supersedes the calibration-stage guard that HT01 production writes stay disabled: the owner approved the
    HT01 profile (plan 2026-11-26) for normalized [0.78, 0.92] only. The calibration staging points still grant
    nothing, and the calibration plan's start is not a production target."""
    title = "HT01 Live Calibration v001"
    command = p.set_gain_db_command("ht01_db_0000", title, 5, "Bass", START["normalized"], START["db"], -1.0)
    assert command["conversion"] == p.HT01_CONVERSION_ID
    for staging in (0.76, 0.94):
        with pytest.raises(p.ProtocolError):
            p.set_gain_db_command("ht01_db_0001", title, 5, "Bass", staging, h_db(staging), -0.1)
        with pytest.raises(p.ProtocolError):
            p.restore_gain_command("ht01_restore_00", title, 5, "Bass", 0.85, h_db(0.85), staging, h_db(staging))
    with pytest.raises(p.ProtocolError):  # already at the target: no restoration
        p.restore_gain_command("ht01_restore_00", title, 5, "Bass", START["normalized"], START["db"],
                               START["normalized"], START["db"])  # fmt: skip


def test_device_protocol_copy_is_exact():
    root = Path(__file__).resolve().parents[2]
    assert (
        Path(p.__file__).read_bytes()
        == (root / "device/fl_studio/thesis_bridge_v2/thesis_bridge_protocol.py").read_bytes()
    )


@pytest.mark.parametrize("failed_step", [0, 1])
def test_uncertain_ht01_session_refuses_further_writes(failed_step):
    d, _ = load_device("v2")
    h, history = host(), {}
    for step in range(failed_step):
        assert (
            answer(d, h, history, p.ht01_probe_command("ht01_before_" + str(step), h.reading(5), step))["status"]
            == "applied"
        )
    command = p.ht01_probe_command("ht01_failed_00", h.reading(5), failed_step)
    h.fail_after_set = RuntimeError("may have written")
    assert answer(d, h, history, command)["status"] == "uncertain"
    assert answer(d, h, history, command)["observed"]["replayed"] is True
    h.fail_after_set = None
    before = len(h.writes)
    restore = p.ht01_probe_command("ht01_restore_01", h.reading(5), "restore")
    assert answer(d, h, history, restore)["error"] == "calibration_uncertain"
    next_step = p.ht01_probe_command("ht01_next_000", h.reading(5), failed_step + 1)
    assert answer(d, h, history, next_step)["status"] == "precondition_failed"
    assert len(h.writes) == before


def test_cannot_skip_a_step_after_session_started():
    d, _ = load_device("v2")
    h, history = host(), {}
    assert answer(d, h, history, p.ht01_probe_command("ht01_order_00", h.reading(5), 0))["status"] == "applied"
    result = answer(d, h, history, p.ht01_probe_command("ht01_order_02", h.reading(5), 2))
    assert result["error"] == "calibration_order"
    assert len(h.writes) == 1
