"""The additive ``restore_gain`` wire command, device script v2 and its import isolation.

Device scripts run against a fake host here (tests/unit/fl_device_support.py); this is not live FL evidence.
"""

import ast
import hashlib
import importlib.util
import math
import sys
from pathlib import Path

import pytest
from fl_device_support import ROOT, V2_DIR, FakeHost, h_db, load_device

from autonomous_mixing.adapters.fl_studio import protocol

TITLE, INSERT, NAME = "Thesis Bass Bridge Test", 2, "Bass"
START_N = 0.7257362319041692  # where the fader sits before the restoration
TARGET_N = 0.76  # the recorded start the restoration returns to (upward)
RESTORE_ID = "restore_cmd_0001"
RESEARCH = ROOT / "scripts" / "fl_studio" / "bridge_protocol.py"


def research_protocol():
    spec = importlib.util.spec_from_file_location("historical_protocol_parity", RESEARCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def restore_command(command_id=RESTORE_ID, start=START_N, target=TARGET_N, title=TITLE, target_db=None, **changes):
    command = protocol.restore_gain_command(command_id, title, INSERT, NAME, start, h_db(start), target,
                                            h_db(target) if target_db is None else target_db)  # fmt: skip
    command.update(changes)
    return command


# --- snapshot provenance and parity -----------------------------------------------------------------------------------


def test_frozen_research_protocol_is_unchanged_and_the_runtime_snapshot_is_pinned():
    assert hashlib.sha256(RESEARCH.read_bytes()).hexdigest() == (
        "03637001d7e953e5fe3a61a962f94c6c80f7bc01729d0e560cbac26ab373f485"
    )
    runtime = Path(protocol.__file__).read_bytes()
    assert hashlib.sha256(runtime).hexdigest() == RUNTIME_PROTOCOL_V2_SHA256  # ADR-032 snapshot
    assert runtime != RESEARCH.read_bytes()  # deliberately extended (restore_gain), no longer byte-identical


RUNTIME_PROTOCOL_V2_SHA256 = (
    "68fb70ddb4f7fd3e80186e0f7a5fc8b19686a4744027e4ca17bc3637c0584698"  # runtime protocol v2 (restore_gain), ADR-032
)


def _corpus():
    """Every existing command type, valid and invalid, plus replies of each existing status."""
    history = research_protocol()
    inspect = protocol.inspect_command("parity_inspect_01", TITLE, INSERT, NAME)
    db = protocol.set_gain_db_command("parity_db_cmd_01", TITLE, INSERT, NAME, 0.76, -1.1527416706085205, -1.0)
    gain = protocol.set_gain_command("parity_gain_c01", TITLE, INSERT, NAME, 0.78, -0.5745, 0.76)
    probe = protocol.probe_command("parity_probe_01", 0.76, -1.15, 0)
    commands = [inspect, db, gain, probe, {**db, "conversion": "other"}, {**gain, "target_normalized": 0.70},
                {**inspect, "type": "delete"}, {**probe, "step": 99}]  # fmt: skip
    replies = [protocol.reply("parity_inspect_01", "invalid_command", error="type"),
               protocol.reply("parity_db_cmd_01", "error", error="api_too_old")]  # fmt: skip
    return history, commands, replies


def _outcome(fn, value):
    try:
        return ("ok", fn(value))
    except Exception as error:
        return ("error", type(error).__name__, getattr(error, "code", None))


def test_existing_commands_and_replies_behave_exactly_as_the_frozen_protocol():
    history, commands, replies = _corpus()
    for command in commands:
        assert _outcome(protocol.validate_command, dict(command)) == _outcome(history.validate_command, dict(command))
        if _outcome(protocol.validate_command, dict(command))[0] == "ok":
            assert protocol.encode(command) == history.encode(command)
    for reply in replies:
        assert _outcome(protocol.validate_reply, reply) == _outcome(history.validate_reply, reply)
        assert protocol.encode(reply) == history.encode(reply)
    for name in ("COMMAND_TYPES", "WRITE_TYPES"):
        old, new = getattr(history, name), getattr(protocol, name)
        assert new[: len(old)] == old and new[len(old) :] == ("restore_gain",)  # purely additive


# --- restore_gain command --------------------------------------------------------------------------------------------


def test_restore_command_round_trips_and_carries_both_coordinates():
    command = restore_command()
    assert command["type"] == "restore_gain" and command["target"] == {"normalized": TARGET_N, "db": h_db(TARGET_N)}
    assert protocol.decode(protocol.encode(command)) == command
    assert "conversion" not in command and "target_normalized" not in command  # exact target, no conversion


@pytest.mark.parametrize(
    "build,code",
    [
        (lambda: restore_command(target=0.87), "range"),
        (lambda: restore_command(target=0.61), "range"),
        (lambda: restore_command(target_db=h_db(TARGET_N) + 0.011), "conversion_mismatch"),  # off the curve
        (lambda: restore_command(start=TARGET_N), "bounds"),  # already there: nothing to restore
        (lambda: restore_command(title="Other Project"), "conversion_scope"),
        (lambda: restore_command(target_db=float("nan")), "field"),
        (lambda: restore_command(target=True), "field"),
        (lambda: {**restore_command(), "extra": 1}, "field"),
        (lambda: {**restore_command(), "target_normalized": 0.76}, "field"),
    ],
)
def test_restore_command_validation(build, code):
    with pytest.raises(protocol.ProtocolError) as info:
        protocol.validate_command(build())
    assert info.value.code == code


def test_restore_accepts_targets_at_both_range_edges_and_a_start_outside_the_range():
    assert protocol.validate_command(restore_command(target=0.62))
    assert protocol.validate_command(restore_command(target=0.86))
    assert protocol.validate_command(restore_command(start=0.95))  # recovering from anywhere


def _written_reply(command, before, after, status="applied", error=None):
    observed = {"project_title": TITLE, "fl_version": "Producer Edition v26.1.7 [build 5654]", "api": 45,
                "insert": {"index": INSERT, "name": NAME}, "before": before, "after": after,
                "db_change": after["db"] - before["db"], "replayed": False}  # fmt: skip
    return protocol.reply(command["id"], status, observed, error)


def test_restore_reply_consistency():
    command = restore_command()
    before, target = command["expected"], command["target"]
    assert protocol.reply_consistency(command, protocol.validate_reply(_written_reply(command, before, target))) is None
    elsewhere = {"normalized": 0.74, "db": h_db(0.74)}
    assert protocol.reply_consistency(command, _written_reply(command, before, elsewhere)) == (
        "applied_values_inconsistent"
    )
    off = _written_reply(command, before, target, "applied_off_target", "db_off_target")
    assert protocol.reply_consistency(command, off) == "status_not_for_type"
    refused = protocol.reply(command["id"], "invalid_command", error="type")
    assert protocol.reply_consistency(command, protocol.validate_reply(refused)) is None


# --- the frozen v1 device script refuses restore_gain before any FL call ----------------------------------------------


def test_frozen_v1_device_refuses_restore_gain_before_any_host_call():
    device, v1_protocol = load_device("v1")
    host = FakeHost(normalized=START_N)
    raw = protocol.encode(restore_command())
    answer = v1_protocol.decode(device.handle(raw, host, {}))
    assert (answer["status"], answer["error"], answer["id"]) == ("invalid_command", "type", RESTORE_ID)
    assert host.calls == [] and host.writes == []
    assert protocol.reply_consistency(restore_command(), protocol.validate_reply(answer)) is None


# --- device script v2 -----------------------------------------------------------------------------------------------


def v2():
    return load_device("v2")


def ask(device, dev_protocol, host, command, history=None):
    history = {} if history is None else history
    return dev_protocol.decode(device.handle(dev_protocol.encode(command), host, history))


def test_v2_restores_exactly_once_upward():
    device, dev_protocol = v2()
    host = FakeHost(normalized=START_N)
    answer = ask(device, dev_protocol, host, restore_command())
    assert answer["status"] == "applied" and host.writes == [(INSERT, TARGET_N)]
    assert answer["observed"]["after"] == {"normalized": TARGET_N, "db": h_db(TARGET_N)}
    assert answer["observed"]["before"] == {"normalized": START_N, "db": h_db(START_N)}
    assert protocol.reply_consistency(restore_command(), protocol.validate_reply(answer)) is None


@pytest.mark.parametrize(
    "setup,status,error",
    [
        (lambda h: setattr(h, "playing", True), "precondition_failed", "transport_playing"),
        (lambda h: setattr(h, "safe", False), "precondition_failed", "edit_unavailable"),
        (lambda h: h.volumes.__setitem__(2, START_N + 0.0015), "precondition_failed", "start_gain"),
    ],
)
def test_v2_restore_preconditions_refuse_without_writing(setup, status, error):
    device, dev_protocol = v2()
    host = FakeHost(normalized=START_N)
    setup(host)
    answer = ask(device, dev_protocol, host, restore_command())
    assert (answer["status"], answer["error"]) == (status, error) and host.writes == []


def test_v2_restore_refuses_other_build_title_and_insert_without_writing():
    device, dev_protocol = v2()
    for host, expected in (
        (FakeHost(normalized=START_N, build="Producer Edition v26.2.0 [build 6000]"), ("precondition_failed",
                                                                                       "uncalibrated_build")),
        (FakeHost(normalized=START_N, title="Other Project"), ("identity_mismatch", "project_title")),
        (FakeHost(normalized=START_N, names=("Kick", "Sub")), ("identity_mismatch", "insert")),
        (FakeHost(normalized=START_N, api=37), ("error", "api_too_old")),
    ):  # fmt: skip
        answer = ask(device, dev_protocol, host, restore_command())
        assert (answer["status"], answer["error"]) == expected and host.writes == []


def test_v2_restore_failures_after_the_setter_are_uncertain():
    device, dev_protocol = v2()
    host = FakeHost(normalized=START_N)
    host.land_offset = 0.01  # the fader lands elsewhere
    answer = ask(device, dev_protocol, host, restore_command())
    assert (answer["status"], answer["error"]) == ("uncertain", "readback_disagrees") and len(host.writes) == 1
    host = FakeHost(normalized=START_N)
    host.fail_after_set = RuntimeError("setter raised")
    answer = ask(device, dev_protocol, host, restore_command())
    assert answer["status"] == "uncertain" and answer["error"] == "exception_after_write:RuntimeError"


def test_v2_restore_within_tolerance_but_not_exact_is_applied_on_the_wire():
    # the device reports a landing within 0.001; exactness is judged by the runtime (DawRestoreMismatch)
    device, dev_protocol = v2()
    host = FakeHost(normalized=START_N)
    host.land_offset = 2e-10
    answer = ask(device, dev_protocol, host, restore_command())
    assert answer["status"] == "applied" and answer["observed"]["after"]["normalized"] != TARGET_N


def test_v2_replays_a_restore_without_a_second_write_and_refuses_a_conflict():
    device, dev_protocol = v2()
    host, history = FakeHost(normalized=START_N), {}
    first = ask(device, dev_protocol, host, restore_command(), history)
    again = ask(device, dev_protocol, host, restore_command(), history)
    assert again["observed"]["replayed"] is True and len(host.writes) == 1
    conflict = ask(device, dev_protocol, host, restore_command(target=0.75), history)
    assert conflict["error"] == "duplicate_id_conflict" and len(host.writes) == 1
    assert first["status"] == "applied"


def test_v2_keeps_the_reviewed_db_cut_behaviour():
    device, dev_protocol = v2()
    host = FakeHost(normalized=0.76)
    command = protocol.set_gain_db_command("v2_db_cut_00001", TITLE, INSERT, NAME, 0.76, h_db(0.76), -1.0)
    answer = ask(device, dev_protocol, host, command)
    assert answer["status"] == "applied" and host.writes == [(INSERT, command["target_normalized"])]


# --- import isolation ------------------------------------------------------------------------------------------------


def test_device_protocol_copy_is_byte_identical_to_the_runtime_protocol():
    assert (V2_DIR / "thesis_bridge_protocol.py").read_bytes() == Path(protocol.__file__).read_bytes()


def test_device_script_top_level_imports_only_its_protocol_and_hides_fl_modules_in_functions():
    tree = ast.parse((V2_DIR / "device_thesis_bridge.py").read_text(encoding="utf-8"))
    top = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert [alias.name for node in top for alias in node.names] == ["thesis_bridge_protocol"]
    fl_modules = {"general", "mixer", "transport", "ui", "device"}
    nested = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert fl_modules <= nested  # FL host modules are imported only inside functions
    assert not (V2_DIR / "__init__.py").exists()


def test_no_runtime_module_imports_the_device_directory():
    for path in (ROOT / "src").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "thesis_bridge_protocol" not in source and "device_thesis_bridge" not in source, path
        assert "device/fl_studio" not in source.replace("\\", "/"), path


def test_loading_the_device_restores_sys_modules():
    before = set(sys.modules)
    load_device("v2")
    assert "thesis_bridge_protocol" not in sys.modules
    assert not {"general", "mixer", "transport", "ui", "device"} & set(sys.modules)
    assert {m for m in set(sys.modules) - before if not m.startswith("fl_v2_")} == set()


def test_restore_command_size_fits():
    command = restore_command(command_id="x" * 64)
    assert len(protocol.encode(command)) <= protocol.MAX_MESSAGE_BYTES
    assert math.isfinite(command["target"]["db"])
