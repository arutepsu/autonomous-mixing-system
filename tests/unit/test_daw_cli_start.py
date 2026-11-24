"""R5C increment C2: ``daw start`` through ``main``.

Only the external MIDI and provider transports are replaced (labelled doubles: the simulated FL host behind the
reviewed device script and the labelled provider responses). Preparation, measurement, diagnosis, the audited adapter,
admission, verification, the run store and the FL journal are the real implementations. These are software tests with
simulated external services on synthetic numerical files, not live mixing results.
"""

import math
from types import SimpleNamespace

import pytest
from agent_runtime_support import Setup
from fl_device_support import FakeHost, load_device
from fl_recovery_support import journal_apply
from restart_support import persistent_host
from run_config_support import run_config_data, write_run_config
from test_daw_cli import SimTransport
from test_fl_runtime_recovery_controller import World, live_curve

from autonomous_mixing.adapters.fl_studio.journal import SqliteCommandJournal
from autonomous_mixing.adapters.journal.record_codec import encode
from autonomous_mixing.adapters.journal.sqlite_run import SqliteRunJournal
from autonomous_mixing.core.admission.daw import exact_fader
from autonomous_mixing.core.records.daw_binding import DawFaderReading
from autonomous_mixing.core.records.identifiers import VersionRef
from autonomous_mixing.core.records.observation import MeasuredScalar
from autonomous_mixing.drivers import agent_runtime, daw_runtime
from autonomous_mixing.drivers.cli import main

RUN = "run-1"
SENTINEL = "sk-ant-SENTINEL-c2-credential-0123456789"
NORMALIZED, DB = "0.76", "-1.1527416706085205"  # the simulated host's exact reading = the declared baseline
NEXT_DB = repr(math.nextafter(float(DB), 0.0))  # the adjacent double: a different reading
NEXT_NORMALIZED = repr(math.nextafter(float(NORMALIZED), 1.0))
FORBIDDEN = ("no write", "nothing written", "successful correction")
REFUSED_CLAUSE = "no provider call or fader write was made by this command"


def simulated(directory, host=None):
    device, device_protocol = load_device("v2")
    world = SimpleNamespace(host=host or persistent_host(directory), device=device, device_protocol=device_protocol,
                            history={}, sent=[], lose_next_reply=False, now=0.0, opened=0)  # fmt: skip

    def factory(*args):
        world.opened += 1
        return SimTransport(world)

    return world, factory


@pytest.fixture
def env(tmp_path, monkeypatch):
    return make_env(tmp_path, monkeypatch)


def make_env(tmp_path, monkeypatch, *, host=None, data=None, setup=None, credential=SENTINEL):
    setup = setup or Setup(tmp_path)
    run_path = write_run_config(setup, data)
    world, factory = simulated(tmp_path, host)
    monkeypatch.setattr(daw_runtime, "WinMMTransport", factory)
    monkeypatch.setattr(agent_runtime, "WinMMTransport", factory)
    constructed = []

    def provider_factory(config, api_key):
        constructed.append(api_key)
        return setup.provider

    monkeypatch.setattr(agent_runtime, "ClaudeMessagesTransport", provider_factory)
    if credential is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", credential)
    return SimpleNamespace(setup=setup, root=tmp_path, tool=setup.config.path, run=run_path, world=world,
                           constructed=constructed, provider=setup.provider)  # fmt: skip


def start(env, *extra, accept=None, run_id=RUN):
    argv = ["daw", "start", "--config", str(env.tool), "--run-config", str(env.run)]
    if run_id is not None:
        argv += ["--run-id", run_id]
    if accept is not None:
        argv += ["--accept-current-fader", *accept]
    try:
        return main([*argv, *extra])
    except SystemExit as exit_:  # argparse usage errors
        return exit_.code


def stores(env):
    return sorted(p.name for p in env.setup.config.run_store_dir.iterdir())


def everything(env):
    return [p for p in env.root.rglob("*") if p.is_file()]


def assert_no_credential_anywhere(env, captured):
    assert SENTINEL not in captured.out and SENTINEL not in captured.err
    for path in everything(env):
        assert SENTINEL.encode() not in path.read_bytes(), path


def assert_factual(text):
    lowered = text.lower()
    assert not any(phrase in lowered for phrase in FORBIDDEN), text


def device_messages(env):
    return [m["type"] for m in env.world.sent]


def assert_nothing_created(env, *, fl_journal):
    assert stores(env) == []
    assert env.constructed == []
    assert env.setup.config.fl_journal.exists() is fl_journal
    assert not [t for t in device_messages(env) if t != "inspect"]


# --- the shared exact comparison (core, pure) -----------------------------------------------------------------------


def test_exact_fader_compares_both_coordinates_and_the_sign_of_zero():
    a = DawFaderReading(0.0, -6.0)
    assert exact_fader(a, DawFaderReading(0.0, -6.0))
    assert not exact_fader(a, DawFaderReading(-0.0, -6.0))
    assert not exact_fader(DawFaderReading(0.5, 0.0), DawFaderReading(0.5, -0.0))
    assert not exact_fader(a, DawFaderReading(0.0, -6.000000000000001))


# --- the parser, the run ID and the credential (exit 2, nothing opened) ----------------------------------------------


@pytest.mark.parametrize("literal", ["nan", "inf", "1e400", "0x1p-1", "1_0", "+0.76", ".76", "0.76 ", "076", "1e"])
def test_unsupported_or_non_finite_fader_literals_exit_2_before_anything(env, capsys, literal):
    assert start(env, accept=(literal, DB)) == 2
    assert start(env, accept=(NORMALIZED, literal)) == 2
    assert_nothing_created(env, fl_journal=False)
    assert env.world.opened == 0


@pytest.mark.parametrize("literal", ["-inf", "-nan", "-1e400", "-0x1p-1"])
def test_negative_non_decimal_literals_exit_2(env, capsys, literal):
    assert start(env, accept=(NORMALIZED, literal)) == 2
    assert_nothing_created(env, fl_journal=False)


def test_a_missing_or_invalid_run_id_exits_2(env, capsys):
    assert start(env, run_id=None) == 2
    assert start(env, run_id="bad id") == 2
    assert start(env, run_id="x" * 65) == 2
    assert_nothing_created(env, fl_journal=False)


@pytest.mark.parametrize("credential", [None, "", "   "])
def test_an_absent_or_blank_credential_exits_2_after_configuration_and_before_anything(tmp_path, monkeypatch, capsys,
                                                                                         credential):  # fmt: skip
    env = make_env(tmp_path, monkeypatch, credential=credential)
    assert start(env, accept=(NORMALIZED, DB)) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
    assert_nothing_created(env, fl_journal=False)
    assert env.world.opened == 0


def test_configuration_errors_are_reported_before_the_credential_is_checked(tmp_path, monkeypatch, capsys):
    env = make_env(tmp_path, monkeypatch, credential=None)
    data = run_config_data(env.setup)
    data["run"]["comment"] = "x"
    write_run_config(env.setup, data)
    assert start(env) == 2
    err = capsys.readouterr().err
    assert "run.comment: unknown key" in err and "ANTHROPIC_API_KEY" not in err


# --- S2/S5 configuration errors (exit 2), S4 state refusal (exit 4) ------------------------------------------------


def test_an_invalid_run_file_exits_2_without_opening_anything(tmp_path, monkeypatch, capsys):
    env = make_env(tmp_path, monkeypatch)
    env.run.write_text('{"schema": 1, "schema": 1}', encoding="utf-8")
    assert start(env, accept=(NORMALIZED, DB)) == 2
    assert "duplicate key" in capsys.readouterr().err
    assert_nothing_created(env, fl_journal=False)


@pytest.mark.parametrize("field", ["frame_count", "start_seconds", "sample_rate_hz", "render_spec"])
def test_invalid_native_export_metadata_is_a_configuration_error_exit_2(tmp_path, monkeypatch, capsys, field):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    session = data["run"]["daw_session"]
    if field == "render_spec":
        session["render_spec"] = encode(VersionRef("another-render", "1"))
    else:
        timeline = session["export_timeline"]["f"]
        timeline[field] = {"frame_count": timeline["frame_count"] + 1, "start_seconds": 1.0,
                           "sample_rate_hz": 48000}[field]  # fmt: skip
    env = make_env(tmp_path, monkeypatch, setup=setup, data=data)
    assert start(env, accept=(NORMALIZED, DB)) == 2
    assert "native export" in capsys.readouterr().err
    assert_nothing_created(env, fl_journal=False)


def test_an_existing_run_is_refused_with_exit_4_and_never_overwritten(env, capsys):
    (env.setup.config.run_store_dir / f"{RUN}.runtime").mkdir()
    assert start(env, accept=(NORMALIZED, DB)) == 4
    err = capsys.readouterr().err
    assert "REFUSED" in err and "already exist" in err and REFUSED_CLAUSE in err
    assert stores(env) == [f"{RUN}.runtime"] and env.constructed == []
    assert not env.setup.config.fl_journal.exists()


# --- S6: journal checks, fresh inspection and the first-session confirmation ------------------------------------------


def test_first_session_without_confirmation_shows_the_exact_fresh_reading_and_creates_no_run(env, capsys):
    assert start(env) == 4
    captured = capsys.readouterr()
    assert f"normalized {NORMALIZED}" in captured.err and f"dB {DB}" in captured.err
    assert "--accept-current-fader" in captured.err and REFUSED_CLAUSE in captured.err
    assert_nothing_created(env, fl_journal=True)
    assert device_messages(env) == ["inspect"]


@pytest.mark.parametrize("accept", [(NORMALIZED, NEXT_DB), (NEXT_NORMALIZED, DB), (NORMALIZED, "-1e-05"),
                                    (NORMALIZED, "-0"), ("0", DB)])  # fmt: skip
def test_a_confirmation_that_differs_from_the_fresh_reading_is_refused(env, capsys, accept):
    assert start(env, accept=accept) == 4
    err = capsys.readouterr().err
    assert "REFUSED" in err and "differs" in err and REFUSED_CLAUSE in err
    assert_nothing_created(env, fl_journal=True)


def test_a_negative_normalized_confirmation_is_invalid_input(env, capsys):
    assert start(env, accept=("-0.76", DB)) == 2
    assert_nothing_created(env, fl_journal=False)


def test_the_declared_state_must_equal_the_fresh_reading_even_when_the_operator_repeats_it(
    tmp_path, monkeypatch, capsys
):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    track = data["run"]["initial_state"]["f"]["daw"]["f"]["tracks"][0]["f"]
    track["confirmed_fader"]["f"]["db"] = float(DB) + 1e-12
    env = make_env(tmp_path, monkeypatch, setup=setup, data=data)
    assert start(env, accept=(NORMALIZED, DB)) == 4
    assert "declared" in capsys.readouterr().err
    assert_nothing_created(env, fl_journal=True)


@pytest.mark.parametrize("accept", [(NORMALIZED, DB), ("7.6e-01", "-11.527416706085205e-1"),
                                    ("76E-2", "-1.1527416706085205e0")])  # fmt: skip
def test_exact_first_session_confirmation_runs_to_a_suspension_with_manual_export_instructions(env, capsys, accept):
    assert start(env, accept=accept) == 0
    captured = capsys.readouterr()
    out = captured.out
    assert out.splitlines()[0] == f"SUSPENDED: awaiting your manual export (run {RUN})"
    store = SqliteRunJournal.open_read_only(env.setup.config.store_path(RUN))
    try:
        snapshot = store.snapshot().value
    finally:
        store.close()
    checkpoint = snapshot.events[-1]
    awaiting, write = checkpoint.payload.awaiting, checkpoint.payload.write
    export = awaiting.request
    for output in export.outputs:
        assert output.file_name in out
    for text in (awaiting.workspace_directory, export.export_id, f"{export.format.container}/{export.format.subtype}",
                 str(export.timeline.sample_rate_hz), str(export.timeline.frame_count), repr(write.after.normalized),
                 repr(write.after.db), export.binding.project_title, export.binding.insert_name):  # fmt: skip
        assert text in out, text
    limits = snapshot.events[0].payload.configuration.budget_limits
    usage = checkpoint.usage
    assert f"DAW writes {usage.daw_writes}/{limits.max_daw_writes}" in out and usage.daw_writes == 1
    assert f"decision calls {usage.decision_calls}/{limits.max_decision_calls}" in out
    assert "acceptances: 0" in out
    assert "daw continue" in out and "R5C C3" in out and "not available" in out
    assert "saving and exporting are manual" in out.lower()
    assert len(env.provider.calls) == 1 and env.constructed == [SENTINEL]
    assert [t for t in device_messages(env) if t != "inspect"] == ["set_gain_db"]
    assert_factual(out + captured.err)
    assert_no_credential_anywhere(env, captured)


def anchored(env, *, restore):
    """A real journaled write on the configured FL journal (and, if ``restore``, its exact confirmed restoration)."""
    journal = SqliteCommandJournal(env.setup.config.fl_journal, session_key=env.setup.config.session_key)
    try:
        world = World(journal, host=env.world.host)
        world.apply()
        if restore:
            world.restore()
    finally:
        journal.close()


def test_an_anchored_journal_at_the_declared_state_runs_without_confirmation(env, capsys):
    anchored(env, restore=True)
    assert start(env) == 0
    assert capsys.readouterr().out.startswith("SUSPENDED")


def test_the_confirmation_flag_is_refused_on_an_anchored_journal(env, capsys):
    anchored(env, restore=True)
    assert start(env, accept=(NORMALIZED, DB)) == 2
    assert "only for an FL journal without a recorded fader state" in capsys.readouterr().err
    assert stores(env) == [] and env.constructed == []


def test_a_live_fader_that_differs_from_its_journaled_state_is_refused(env, capsys):
    anchored(env, restore=True)
    env.world.host.volumes[2] = 0.70
    assert start(env) == 4
    err = capsys.readouterr().err
    assert "differs from its journaled state" in err and REFUSED_CLAUSE in err
    assert stores(env) == [] and env.constructed == []


def test_an_anchor_that_is_not_the_declared_state_is_refused(env, capsys):
    anchored(env, restore=False)
    assert start(env) == 4
    assert "not exactly the configured confirmed fader" in capsys.readouterr().err
    assert stores(env) == [] and env.constructed == []


@pytest.mark.parametrize(("kind", "text"), [("uncertain", "unresolved FL commands"), ("off_target", "blocked")])
def test_unresolved_commands_or_a_blocked_conversion_are_refused(env, capsys, kind, text):
    journal = SqliteCommandJournal(env.setup.config.fl_journal, session_key=env.setup.config.session_key)
    try:
        journal_apply(journal, "apply_root_0001", kind=kind)
    finally:
        journal.close()
    assert start(env, accept=(NORMALIZED, DB)) == 4
    err = capsys.readouterr().err
    assert text in err and REFUSED_CLAUSE in err
    if kind == "uncertain":
        assert "apply_root_0001" in err and "daw recover" in err
    assert stores(env) == [] and env.constructed == [] and device_messages(env) == []


@pytest.mark.parametrize(
    "host",
    [dict(title="Another Project"), dict(names=("Kick", "Bus")), dict(build="Producer Edition v26.1.8 [build 5700]")],
)
def test_a_wrong_project_insert_or_build_is_refused_after_inspection(tmp_path, monkeypatch, capsys, host):
    env = make_env(tmp_path, monkeypatch, host=FakeHost(normalized=0.76, curve=live_curve, **host))
    code = start(env, accept=(NORMALIZED, DB))
    err = capsys.readouterr().err
    assert code == 4 and "REFUSED" in err and REFUSED_CLAUSE in err
    assert "not the calibrated build/project/insert of this run" in err  # identity, not a downstream symptom
    assert stores(env) == [] and env.constructed == []
    assert not [t for t in device_messages(env) if t != "inspect"]


# --- outcomes printed from the records (exit 0) ----------------------------------------------------------------------


def test_initial_criteria_met_prints_no_change_needed_without_a_provider_call(tmp_path, monkeypatch, capsys):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    data["run"]["criteria"]["f"]["targets"][0]["f"]["threshold"] = encode(MeasuredScalar(0.0, "dBFS"))
    env = make_env(tmp_path, monkeypatch, setup=setup, data=data)
    assert start(env, accept=(NORMALIZED, DB)) == 0
    captured = capsys.readouterr()
    first, *rest = captured.out.splitlines()
    assert first == f"NO CHANGE NEEDED: the initial state already met the declared criteria (run {RUN})"
    assert "DAW writes 0/3" in captured.out and "final project state: unchanged" in captured.out
    assert env.provider.calls == [] and "uncertain" not in captured.out
    assert_factual(captured.out)


def test_a_genuine_abstention_prints_the_recorded_block(tmp_path, monkeypatch, capsys):
    setup = Setup(tmp_path, target_counts=32767)
    env = make_env(tmp_path, monkeypatch, setup=setup)
    assert start(env, accept=(NORMALIZED, DB)) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"ABSTAINED: insufficient_evidence (run {RUN})"
    for line in ("usage: decision calls 1/4", "DAW writes 0/3", "acceptances: 0", "authoritative: r1 (sequence 0)",
                 "verified:", "final project state:"):  # fmt: skip
        assert line in out, line
    assert_factual(out)


# --- failure reporting across the lifecycle: type only, never a "nothing happened" after effects -----------------------


def test_a_provider_construction_failure_is_reported_by_type_only(env, monkeypatch, capsys):
    def failing(config, api_key):
        raise RuntimeError(f"could not build transport for key {api_key}")

    monkeypatch.setattr(agent_runtime, "ClaudeMessagesTransport", failing)
    assert start(env, accept=(NORMALIZED, DB)) == 4
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err and REFUSED_CLAUSE in captured.err and f"{RUN}.runtime" in captured.err
    assert "could not build transport" not in captured.err
    assert_no_credential_anywhere(env, captured)
    assert [t for t in device_messages(env) if t != "inspect"] == []


def test_a_provider_failure_after_dispatch_is_stopped_and_never_a_refusal(env, monkeypatch, capsys):
    def failing_send(body, *, timeout_seconds):
        env.provider.calls.append("dispatched")
        raise ConnectionError(f"connection reset while sending with {SENTINEL}")

    monkeypatch.setattr(env.provider, "send", failing_send)
    assert start(env, accept=(NORMALIZED, DB)) == 4
    captured = capsys.readouterr()
    assert "STOPPED" in captured.err and "ConnectionError" in captured.err
    assert REFUSED_CLAUSE not in captured.err and "connection reset" not in captured.err
    assert "may have" in captured.err and "daw status" in captured.err
    assert_no_credential_anywhere(env, captured)
    assert_factual(captured.out + captured.err)


def test_an_exception_after_a_fader_write_is_stopped_with_type_only(env, monkeypatch, capsys):
    from autonomous_mixing.adapters.rendering.export_handoff import FileExportHandoff

    def failing_prepare(self, request):
        raise OSError("RAW-TEXT-MARKER export preparation failed")

    monkeypatch.setattr(FileExportHandoff, "prepare", failing_prepare)
    assert start(env, accept=(NORMALIZED, DB)) == 4
    captured = capsys.readouterr()
    assert "STOPPED" in captured.err and "OSError" in captured.err and "RAW-TEXT-MARKER" not in captured.err
    assert REFUSED_CLAUSE not in captured.err
    assert "set_gain_db" in device_messages(env)
    assert "last recorded" in captured.err and "DAW writes 1/3" in captured.err


def test_a_cleanup_failure_after_an_outcome_keeps_the_outcome_and_exits_4(env, monkeypatch, capsys):
    def failing_close():
        raise RuntimeError(f"close failed for {SENTINEL}")

    monkeypatch.setattr(env.provider, "close", failing_close)
    assert start(env, accept=(NORMALIZED, DB)) == 4
    captured = capsys.readouterr()
    assert captured.out.startswith("SUSPENDED")
    assert "cleanup failed (RuntimeError)" in captured.err and "the recorded outcome above stands" in captured.err
    assert REFUSED_CLAUSE not in captured.err and "REFUSED" not in captured.err
    assert_no_credential_anywhere(env, captured)


def test_a_cleanup_failure_after_a_stopped_run_names_both_types_only(env, monkeypatch, capsys):
    def failing_send(body, *, timeout_seconds):
        raise ConnectionError(SENTINEL)

    def failing_close():
        raise ValueError(SENTINEL)

    monkeypatch.setattr(env.provider, "send", failing_send)
    monkeypatch.setattr(env.provider, "close", failing_close)
    assert start(env, accept=(NORMALIZED, DB)) == 4
    captured = capsys.readouterr()
    assert (
        "STOPPED" in captured.err
        and "ConnectionError" in captured.err
        and "cleanup failed (ValueError)" in captured.err
    )
    assert_no_credential_anywhere(env, captured)


# --- unchanged commands ----------------------------------------------------------------------------------------------


def test_continue_still_refuses_and_names_c3(env, capsys):
    code = main(["daw", "continue", "--config", str(env.tool), "--run-id", RUN, "--attestation", "x"])
    err = capsys.readouterr().err
    assert code == 2 and "R5C C3" in err and "not yet available" in err
    assert stores(env) == []


def test_start_help_lists_the_new_options(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["daw", "start", "--help"])
    out = capsys.readouterr().out
    assert raised.value.code == 0
    assert all(option in out for option in ("--config", "--run-config", "--run-id", "--accept-current-fader"))
    assert "ANTHROPIC_API_KEY" in out


# --- review fix (2026-11-24): an unreleased pre-check MIDI transport stops the command before runtime creation --------


class CloseFailsOnce(SimTransport):
    """A labelled MIDI transport double whose first close fails (the native handle may still be held)."""

    closes = []

    def close(self):
        CloseFailsOnce.closes.append(self)
        if len(CloseFailsOnce.closes) == 1:
            raise OSError("injected close failure")


def test_an_unreleased_precheck_transport_stops_before_runtime_creation(env, monkeypatch, capsys):
    from autonomous_mixing.drivers import daw_commands

    CloseFailsOnce.closes = []
    opened = []

    def factory(*args):
        opened.append(CloseFailsOnce(env.world))
        return opened[-1]

    monkeypatch.setattr(daw_runtime, "WinMMTransport", factory)
    monkeypatch.setattr(agent_runtime, "WinMMTransport", factory)
    monkeypatch.setattr(daw_commands, "_UNRELEASED_CONTROLLERS", [])
    assert start(env, accept=(NORMALIZED, DB)) == 4
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err and "could not release" in captured.err and REFUSED_CLAUSE in captured.err
    assert env.constructed == [] and env.provider.calls == []  # no provider construction or call
    assert len(opened) == 1 and CloseFailsOnce.closes == opened  # no further MIDI transport, no second close
    assert device_messages(env) == ["inspect"]  # one inspection, no gain write
    assert stores(env) == []
    (controller,) = daw_commands._UNRELEASED_CONTROLLERS  # ownership of the unreleased transport is kept
    assert controller.blocked and opened[0] in controller._retained_transports


def test_a_blocked_controller_refuses_even_when_the_inspection_itself_failed(env, monkeypatch, capsys):
    from autonomous_mixing.drivers import daw_commands

    CloseFailsOnce.closes = []

    class CloseFailsAfterTimeout(CloseFailsOnce):
        def receive(self, remaining):
            self.world.now += remaining
            return None

    monkeypatch.setattr(daw_runtime, "WinMMTransport", lambda *a: CloseFailsAfterTimeout(env.world))
    monkeypatch.setattr(daw_commands, "_UNRELEASED_CONTROLLERS", [])
    assert start(env, accept=(NORMALIZED, DB)) == 4
    err = capsys.readouterr().err
    assert "could not release" in err and env.constructed == [] and stores(env) == []
    assert len(daw_commands._UNRELEASED_CONTROLLERS) == 1


@pytest.mark.parametrize("second_run_id", [RUN, "run-2"])
def test_a_retained_blocked_controller_refuses_every_later_start_in_the_same_process(env, monkeypatch, capsys,
                                                                                      second_run_id):  # fmt: skip
    from autonomous_mixing.drivers import daw_commands

    CloseFailsOnce.closes = []
    opened = []

    def factory(*args):
        opened.append(CloseFailsOnce(env.world))
        return opened[-1]

    monkeypatch.setattr(daw_runtime, "WinMMTransport", factory)
    monkeypatch.setattr(agent_runtime, "WinMMTransport", factory)
    monkeypatch.setattr(daw_commands, "_UNRELEASED_CONTROLLERS", [])
    assert start(env, accept=(NORMALIZED, DB)) == 4
    (controller,) = daw_commands._UNRELEASED_CONTROLLERS
    capsys.readouterr()
    assert start(env, accept=(NORMALIZED, DB), run_id=second_run_id) == 4
    err = capsys.readouterr().err
    assert "REFUSED" in err and "unreleased MIDI transport" in err and REFUSED_CLAUSE in err
    assert len(opened) == 1 and CloseFailsOnce.closes == opened  # no further transport; closed (attempted) once
    assert device_messages(env) == ["inspect"]  # only the original inspection; no gain write
    assert env.constructed == [] and env.provider.calls == []
    assert stores(env) == []  # no run store or runtime directory for either ID
    assert daw_commands._UNRELEASED_CONTROLLERS == [controller]
    assert controller.blocked and opened[0] in controller._retained_transports


def test_a_healthy_process_with_no_retained_controller_starts_normally(env, monkeypatch, capsys):
    from autonomous_mixing.drivers import daw_commands

    monkeypatch.setattr(daw_commands, "_UNRELEASED_CONTROLLERS", [])
    assert start(env, accept=(NORMALIZED, DB)) == 0
    assert capsys.readouterr().out.startswith("SUSPENDED")
    assert daw_commands._UNRELEASED_CONTROLLERS == []
