"""Increment 6 (ADR-040): the operator CLI for the DAW path. Operator tooling is ready; autonomous runs are unavailable.

``daw start`` and ``daw continue`` refuse before opening any store or MIDI connection, naming the missing decision
maker and diagnosis source (no implementation exists yet). ``daw status`` is read-only. ``daw recover`` shows the
interrupted state, the proposed target and the permitted operations before any authorization, and performs exactly
one fingerprint-bound operation. Simulated FL Studio (device script v2 on a fake host) and temporary files only;
interrupted stores come from real crashed child processes (``restart_child.py``). Labelled doubles stay in tests.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fl_device_support import load_device
from fl_recovery_support import journal_apply
from restart_support import STORE, persistent_host
from test_fl_runtime_recovery_controller import DeviceTransport

import autonomous_mixing.drivers.daw_runtime as runtime
from autonomous_mixing.adapters.fl_studio import protocol
from autonomous_mixing.adapters.fl_studio.journal import SqliteCommandJournal
from autonomous_mixing.adapters.journal.sqlite_run import SqliteRunJournal
from autonomous_mixing.core.loop.daw_recovery import recovery_ids
from autonomous_mixing.core.records.coordinator_runtime import ClockReading
from autonomous_mixing.core.records.daw_recovery import DawRestoreConfirmed
from autonomous_mixing.core.records.run_restart import OperatorOperation, OperatorRecoveryAuthorization
from autonomous_mixing.core.records.run_trace import TerminalSnapshot
from autonomous_mixing.drivers import daw_commands
from autonomous_mixing.drivers.cli import main
from autonomous_mixing.drivers.daw_runtime import ConfigError, SystemClock, UuidIdentitySource, load_config

CHILD = Path(__file__).with_name("restart_child.py")
ROOT = Path(__file__).resolve().parents[2]
RUN = "run-1"


# --- fixtures: a crashed run's directory and its configuration ------------------------------------------------------


def crashed(directory, phase="start", scenario="single", point="after_fl_outcome"):
    """Produce a stored run interrupted at ``point`` by a real child process; returns the config path."""
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    if phase == "resume":
        done = subprocess.run([sys.executable, str(CHILD), str(directory), "start", scenario, "none"], cwd=ROOT,
                              env=env, capture_output=True, text=True, timeout=180)  # fmt: skip
        assert done.returncode == 0, done.stderr[-2000:]
    done = subprocess.run([sys.executable, str(CHILD), str(directory), phase, scenario, point], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=180)  # fmt: skip
    assert done.returncode in (0, 77), done.stderr[-2000:]
    stores = directory / "stores"
    stores.mkdir(exist_ok=True)
    if (directory / STORE).exists():
        (directory / STORE).rename(stores / f"{RUN}.sqlite")
    return write_config(directory)


def write_config(directory, **overrides):
    stores = directory / "stores"
    stores.mkdir(exist_ok=True)
    (directory / "inputs").mkdir(exist_ok=True)
    (directory / "exports").mkdir(exist_ok=True)
    config = {
        "schema": 1,
        "run_store_dir": str(stores),
        "fl_journal": {"path": str(directory / "fl.db"), "session_key": "session-1"},
        "profile_reference": {"name": "fl-control-profile", "version": "1"},
        "midi": {"output_port": "labelled-out", "input_port": "labelled-in", "timeout_s": 1.0},
        "roots": {"inputs": str(directory / "inputs"), "daw-exports": str(directory / "exports")},
        "read_limits": {"max_file_bytes": 1 << 22, "max_decoded_bytes": 1 << 24},
    }
    config.update(overrides)
    path = directory / "daw-session.json"
    path.write_text(json.dumps(config))
    return path


def digests(directory):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (*directory.glob("stores/*.sqlite"), directory / "fl.db", directory / "host.json") if p.exists()}  # fmt: skip


@pytest.fixture
def no_midi(monkeypatch):
    """Any attempt to open the real MIDI transport fails the test (status and phase 1 never open one)."""

    def forbidden(*args, **kwargs):
        raise AssertionError("a MIDI transport was opened")

    monkeypatch.setattr(runtime, "WinMMTransport", forbidden)


class SimTransport(DeviceTransport):
    """The simulated FL host behind the reviewed protocol (labelled test double; never in production code)."""

    def send(self, raw):
        message = protocol.decode(raw)
        self.world.sent.append(message)
        self.reply = self.world.device.handle(self.world.device_protocol.encode(message), self.world.host,
                                              self.world.history)  # fmt: skip


def simulated(directory):
    device, device_protocol = load_device("v2")
    world = SimpleNamespace(host=persistent_host(directory), device=device, device_protocol=device_protocol,
                            history={}, sent=[], lose_next_reply=False, now=0.0)  # fmt: skip
    return world, (lambda: SimTransport(world))


# --- the production clock and process-unique IDs ----------------------------------------------------------------


def test_the_system_clock_is_monotonic_utc_and_cancellable():
    clock = SystemClock()
    first, second = clock.read(), clock.read()
    assert isinstance(first, ClockReading) and second.monotonic_seconds >= first.monotonic_seconds
    assert first.utc.utcoffset().total_seconds() == 0 and not first.cancelled
    clock.cancel()
    assert clock.read().cancelled


def test_unique_ids_fit_every_id_rule_and_never_repeat_across_processes():
    source = UuidIdentitySource()
    tokens = {source.next_token() for _ in range(2000)}
    assert len(tokens) == 2000 and all(re.fullmatch(r"[0-9a-f]{32}", t) for t in tokens)
    assert recovery_ids(next(iter(tokens)))  # every recovery ID of a token fits (ADR-038)
    inspect_ids = {runtime.unique_inspect_id() for _ in range(2000)}
    assert len(inspect_ids) == 2000 and all(re.fullmatch(r"[A-Za-z0-9_-]{8,64}", i) for i in inspect_ids)
    code = ("from autonomous_mixing.drivers.daw_runtime import UuidIdentitySource as U, unique_inspect_id as i;"
            "u=U(); print(' '.join([u.next_token() for _ in range(50)] + [i() for _ in range(50)]))")  # fmt: skip
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    outputs = [subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
               .stdout.split() for _ in range(2)]  # fmt: skip
    assert not set(outputs[0]) & set(outputs[1])


# --- configuration -------------------------------------------------------------------------------------------------


def test_a_valid_configuration_loads_and_invalid_ones_name_the_key(tmp_path):
    config = load_config(write_config(tmp_path))
    assert config.run_store_dir == tmp_path / "stores" and dict(config.roots)["inputs"] == str(tmp_path / "inputs")
    for overrides, key in (
        ({"surprise": 1}, "surprise"),
        ({"ground_truth": "x"}, "ground_truth"),  # no field for hidden evaluation data (SR-10)
        ({"schema": 2}, "schema"),
        ({"midi": {"output_port": "o", "input_port": "i", "timeout_s": -1}}, "midi.timeout_s"),
        ({"fl_journal": {"path": "x.db"}}, "fl_journal.session_key"),
        ({"run_store_dir": str(tmp_path / "inputs")}, "run_store_dir"),
        ({"run_store_dir": "\\\\server\\share\\stores"}, "run_store_dir: network paths are unsupported"),
    ):
        with pytest.raises(ConfigError, match=re.escape(key)):
            load_config(write_config(tmp_path, **overrides))


# --- continue with missing options: a usage error before anything is opened (start/continue: test_daw_cli_* files) --


def test_continue_without_its_required_options_opens_nothing(tmp_path, capsys, monkeypatch, no_midi):
    config = write_config(tmp_path)
    before = sorted(p.name for p in tmp_path.rglob("*"))
    monkeypatch.setattr(SqliteRunJournal, "create", lambda *a, **k: pytest.fail("a run store was created"))
    monkeypatch.setattr(SqliteRunJournal, "open", lambda *a, **k: pytest.fail("a run store was opened"))
    with pytest.raises(SystemExit) as raised:
        main(["daw", "continue", "--run-id", RUN, "--attestation", "x", "--config", str(config)])
    assert raised.value.code == 2
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


def test_the_daw_commands_are_registered(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["daw", "--help"])
    assert raised.value.code == 0
    out = capsys.readouterr().out
    assert all(name in out for name in ("start", "continue", "status", "recover"))


# --- status: read-only -----------------------------------------------------------------------------------------------


def test_status_classifies_an_interrupted_run_without_writing_or_opening_midi(tmp_path, capsys, no_midi):
    config = crashed(tmp_path)
    before = digests(tmp_path)
    assert main(["daw", "status", "--config", str(config), "--run-id", RUN, "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["classification"] == "interrupted_after_write" and shown["durability"] == "local_sqlite_process_crash"
    assert shown["authoritative"]["sequence"] == 0 and shown["fl"]["unresolved"] == []
    assert digests(tmp_path) == before  # byte-identical: nothing was written anywhere
    assert main(["daw", "status", "--config", str(config)]) == 0  # every run in the directory, one line each
    assert RUN in capsys.readouterr().out


def test_status_shows_a_store_held_by_another_process_as_in_use(tmp_path, capsys, no_midi):
    config = crashed(tmp_path)
    holder = SqliteRunJournal.open(tmp_path / "stores" / f"{RUN}.sqlite")
    try:
        assert main(["daw", "status", "--config", str(config), "--run-id", RUN]) == 4
        assert "in use" in capsys.readouterr().out
    finally:
        holder.close()


# --- recover: phase 1 read-only, phase 2 one fingerprint-bound operation -------------------------------------------


def phase_one(config, capsys, *args):
    assert main(["daw", "recover", "--config", str(config), *args, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_recover_shows_state_target_and_options_before_authorization_and_writes_nothing(tmp_path, capsys, no_midi):
    config = crashed(tmp_path)
    before = digests(tmp_path)
    shown = phase_one(config, capsys, "--run-id", RUN)
    assert shown["classification"] == "interrupted_after_write"
    assert shown["proposed_target"] == shown["saved_target"] and shown["exact"] is True
    operations = [o["operation"] for o in shown["options"]]
    assert operations == ["restore", "inspect_integrity", "close"]
    assert re.fullmatch(r"[0-9a-f]{64}", shown["fingerprint"])
    assert digests(tmp_path) == before
    assert {o.value for o in OperatorOperation} == {"reconcile", "restore", "resolve", "inspect_integrity", "close"}


def apply(config, run_id, fingerprint, index, factory, *, operator="labelled-operator"):
    return daw_commands.recover_apply(load_config(config), run_id, fingerprint, index, operator,
                                      transport_factory=factory)  # fmt: skip


def test_an_authorized_restoration_then_close_restores_exactly_once_and_claims_nothing(tmp_path, capsys):
    config = crashed(tmp_path)
    shown = phase_one(config, capsys, "--run-id", RUN)
    world, factory = simulated(tmp_path)
    restore = next(i for i, o in enumerate(shown["options"]) if o["operation"] == "restore")
    outcome = apply(config, RUN, shown["fingerprint"], restore, factory)
    assert isinstance(outcome, DawRestoreConfirmed)
    assert [m["type"] for m in world.sent].count("restore_gain") == 1
    assert world.host.volumes[2] == 0.76
    trace = SqliteRunJournal.open(tmp_path / "stores" / f"{RUN}.sqlite")
    try:
        authorization = [e.payload for e in trace.snapshot().value.events
                         if isinstance(e.payload, OperatorRecoveryAuthorization)]  # fmt: skip
    finally:
        trace.close()
    assert len(authorization) == 1 and authorization[0].operator == "labelled-operator"
    again = phase_one(config, capsys, "--run-id", RUN)
    close = next(i for i, o in enumerate(again["options"]) if o["operation"] == "close")
    terminal = apply(config, RUN, again["fingerprint"], close, factory)
    assert isinstance(terminal.payload, TerminalSnapshot)
    assert terminal.payload.run.final_project_state.value == "unknown" and terminal.payload.verified is None
    assert [m["type"] for m in world.sent].count("restore_gain") == 1  # closing restored nothing
    assert phase_one(config, capsys, "--run-id", RUN)["classification"] == "finished"


def test_a_stale_fingerprint_or_an_unshown_option_is_refused_without_any_effect(tmp_path, capsys):
    config = crashed(tmp_path)
    shown = phase_one(config, capsys, "--run-id", RUN)
    world, factory = simulated(tmp_path)
    before = digests(tmp_path)
    with pytest.raises(daw_commands.RecoveryRefused, match="changed"):
        apply(config, RUN, "0" * 64, 0, factory)
    with pytest.raises(daw_commands.RecoveryRefused, match="option"):
        apply(config, RUN, shown["fingerprint"], 99, factory)
    assert digests(tmp_path) == before and world.sent == []
    assert main(["daw", "recover", "--config", str(config), "--run-id", RUN, "--authorize", "0" * 64,
                 "--operation", "0", "--operator", "x"]) == 4  # fmt: skip


def test_an_unresolved_write_is_reconciled_then_resolved_only_as_its_evidence_permits(tmp_path, capsys):
    config = crashed(tmp_path, point="after_fl_reservation")  # the apply is reserved, never sent
    shown = phase_one(config, capsys, "--run-id", RUN)
    assert shown["classification"] == "interrupted_write_unresolved"
    assert [o["operation"] for o in shown["options"]][0] == "reconcile"
    assert "restore" in [o["operation"] for o in shown["options"]]
    world, factory = simulated(tmp_path)
    finding = apply(config, RUN, shown["fingerprint"], 0, factory)
    assert finding.value.finding.value == "at_recorded_start"
    after = phase_one(config, capsys, "--run-id", RUN)
    resolve = [o for o in after["options"] if o["operation"] == "resolve"]
    assert [o["resolution_kind"] for o in resolve] == ["not_applied_or_reverted"]
    apply(config, RUN, after["fingerprint"], after["options"].index(resolve[0]), factory)
    fl = SqliteCommandJournal(tmp_path / "fl.db", session_key="session-1")
    try:
        assert fl.control_state().unresolved == ()
    finally:
        fl.close()
    assert [m["type"] for m in world.sent].count("set_gain_db") == 0  # nothing was written to the fader


def test_orphaned_fl_commands_are_shown_and_never_adopted(tmp_path, capsys, no_midi):
    config = write_config(tmp_path)
    fl = SqliteCommandJournal(tmp_path / "fl.db", session_key="session-1")
    journal_apply(fl, "apply_orphan_01", pending=True)  # a command no run explains
    fl.close()
    before = digests(tmp_path)
    shown = phase_one(config, capsys, "--orphans")
    assert [o["operation"] for o in shown["options"]] == ["reconcile", "restore"]
    assert shown["run"] is None and digests(tmp_path) == before


def test_status_and_phase_one_use_only_read_only_opens(tmp_path, capsys, monkeypatch, no_midi):
    config = crashed(tmp_path)
    real = SqliteCommandJournal.__init__

    def fl_init(self, path, *, session_key, read_only=False):
        assert read_only, "a writable FL journal open during a read-only command"
        real(self, path, session_key=session_key, read_only=read_only)

    monkeypatch.setattr(SqliteCommandJournal, "__init__", fl_init)
    monkeypatch.setattr(SqliteRunJournal, "open", lambda *a, **k: pytest.fail("a writable run-store open"))
    assert main(["daw", "status", "--config", str(config), "--run-id", RUN]) == 0
    assert main(["daw", "recover", "--config", str(config), "--run-id", RUN]) == 0
    assert "fingerprint" in capsys.readouterr().out


def test_the_driver_modules_import_no_evaluation_code_and_no_test_doubles():
    import ast

    for name in ("cli", "daw_runtime", "daw_commands"):
        source = (ROOT / "src" / "autonomous_mixing" / "drivers" / f"{name}.py").read_text(encoding="utf-8")
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        project = {m for m in imported if m.startswith("autonomous_mixing")}
        assert not {m for m in project if ".evaluation" in m or m.startswith("autonomous_mixing.evaluation")}
        assert all(m.split(".")[1] in ("core", "adapters", "drivers") for m in project), name
        assert not {m for m in imported if m.startswith(("test", "fl_", "daw_continuation", "restart_"))}, name


# --- review fixes (2026-11-20) -----------------------------------------------------------------------------------


def test_a_committed_acceptance_is_never_offered_or_executed_as_a_restoration(tmp_path, capsys):
    """Finding 1: a crash after the acceptance commit; the accepted fader must never be reverted."""
    from dataclasses import replace as _replace

    from autonomous_mixing.core.loop.operator_recovery import perform, run_view
    from autonomous_mixing.core.loop.restart import restart_facts
    from autonomous_mixing.core.records.run_restart import OperatorRecoveryOption

    config = crashed(tmp_path, phase="resume", point="after_acceptance_commit")
    shown = phase_one(config, capsys, "--run-id", RUN)
    assert shown["classification"] == "interrupted_after_acceptance" and shown["authoritative"]["sequence"] == 1
    assert "restore" not in [o["operation"] for o in shown["options"]]
    world, factory = simulated(tmp_path)
    accepted_fader = world.host.volumes[2]
    store = SqliteRunJournal.open(tmp_path / "stores" / f"{RUN}.sqlite")
    journal, controller = runtime.open_fl(load_config(config), read_only=False, transport_factory=factory)
    try:
        snapshot = store.snapshot().value
        view = run_view(snapshot, restart_facts(snapshot, controller))
        root = view.lineages[0].root_command_id
        forged = OperatorRecoveryOption(OperatorOperation.RESTORE, root, None, None, "forged")
        with pytest.raises(ValueError, match="accepted"):
            perform(_replace(view, options=(*view.options, forged)), forged, operator="x", view_fingerprint="0" * 64,
                    snapshot=snapshot, journal=store, recovery_controller=controller, daw_controller=controller,
                    integrity_inspector=None, identities=runtime.UuidIdentitySource(), clock=runtime.SystemClock())  # fmt: skip
    finally:
        journal.close()
        store.close()
    assert [m["type"] for m in world.sent].count("restore_gain") == 0 and world.host.volumes[2] == accepted_fader


def orphan_world(tmp_path, *, off_target=False):
    """An FL journal holding one confirmed (or off-target) write that no run store records."""
    from test_fl_runtime_recovery_controller import World, live_curve

    config = write_config(tmp_path)
    fl = SqliteCommandJournal(tmp_path / "fl.db", session_key="session-1")
    world = World(fl, persistent_host(tmp_path))
    if off_target:
        world.host.curve = lambda v: live_curve(v) + (0.0 if v == 0.76 else 0.2)
    outcome = world.apply("apply_orphan_02")
    fl.close()
    return config, outcome


@pytest.mark.parametrize("off_target, kind", [(False, "DawWriteConfirmed"), (True, "DawWriteOffTarget")])
def test_a_confirmed_or_off_target_orphan_is_shown_with_its_target(tmp_path, capsys, no_midi, off_target, kind):
    """Finding 2: genuinely unexplained roots in these states are visible, with their journaled target."""
    config, outcome = orphan_world(tmp_path, off_target=off_target)
    assert type(outcome).__name__ == kind
    before = digests(tmp_path)
    shown = phase_one(config, capsys, "--orphans")
    assert shown["proposed_target"] == {"normalized": 0.76, "db": outcome.before.db}
    assert [(o["operation"], o["subject_command_id"]) for o in shown["options"]] == [("restore", "apply_orphan_02")]
    assert digests(tmp_path) == before  # still read-only


def test_a_root_a_run_store_records_is_explained_and_never_an_orphan(tmp_path, capsys, no_midi):
    config = crashed(tmp_path, phase="resume", point="after_acceptance_commit")  # its root is recorded and accepted
    shown = phase_one(config, capsys, "--orphans")
    assert shown["options"] == [] and shown["proposed_target"] is None


def test_orphans_are_refused_while_any_run_store_cannot_be_read(tmp_path, capsys, no_midi):
    config = crashed(tmp_path)
    holder = SqliteRunJournal.open(tmp_path / "stores" / f"{RUN}.sqlite")
    try:
        assert main(["daw", "recover", "--config", str(config), "--orphans"]) == 4  # fail closed: cannot tell
        assert "orphans cannot be established" in capsys.readouterr().err
    finally:
        holder.close()
