"""R5C increment C3: ``daw continue`` through ``main``.

Only the external MIDI and provider transports are replaced (the simulated FL host behind the reviewed v2 device
script; labelled provider replies). Preparation, measurement, diagnosis, the audited adapter, admission, verification,
recovery, the run store, the decision-exchange store and the FL journal are the real implementations. Export files are
synthetic numerical test files written from the stored checkpoint. Software tests with simulated external services,
not live mixing results.
"""

import dataclasses
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_runtime_support import Setup
from run_config_support import run_config_data
from test_daw_cli_start import (
    DB,
    NORMALIZED,
    REFUSED_CLAUSE,
    RUN,
    SENTINEL,
    CloseFailsOnce,
    assert_factual,
    assert_no_credential_anywhere,
    device_messages,
    make_env,
    start,
)

from autonomous_mixing.adapters.decision_agent.config import ClaudeConfig
from autonomous_mixing.adapters.decision_agent.prompt import config_from_identity, provider_identity
from autonomous_mixing.adapters.journal.record_codec import encode
from autonomous_mixing.adapters.journal.sqlite_run import SqliteRunJournal
from autonomous_mixing.adapters.rendering import export_handoff
from autonomous_mixing.core.records.decision_exchange import ProviderRequestIdentity, canonical_json
from autonomous_mixing.core.records.masking_measurement import BandRegion
from autonomous_mixing.core.records.observation import MeasuredScalar
from autonomous_mixing.drivers import agent_runtime, daw_commands, daw_runtime
from autonomous_mixing.drivers.cli import main

OPERATOR, ATTESTATION = "labelled-operator", "test files exported as declared"
CHILD = Path(__file__).parent / "daw_cli_child.py"


# --- helpers ----------------------------------------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    return make_env(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def no_retained_controllers(monkeypatch):
    monkeypatch.setattr(daw_commands, "_UNRELEASED_CONTROLLERS", [])


def proceed(env, *, export_id=None, run_id=RUN, operator=OPERATOR, attestation=ATTESTATION, extra=()):
    export_id = export_id if export_id is not None else awaiting(env).request.export_id
    argv = ["daw", "continue", "--config", str(env.tool), "--run-id", run_id, "--export-id", export_id,
            "--operator", operator, "--attestation", attestation, *extra]  # fmt: skip
    try:
        return main(argv)
    except SystemExit as exit_:
        return exit_.code


def snapshot(env, run_id=RUN):
    store = SqliteRunJournal.open_read_only(env.setup.config.store_path(run_id))
    try:
        return store.snapshot().value
    finally:
        store.close()


def awaiting(env):
    return snapshot(env).events[-1].payload.awaiting


def export(env):
    """Write the declared outputs of the stored suspension as the operator would (synthetic numerical files)."""
    env.setup.export(SimpleNamespace(checkpoint_event=snapshot(env).events[-1]))
    return Path(awaiting(env).workspace_directory)


def suspended_env(tmp_path, monkeypatch, *, setup=None, data=None):
    env = make_env(tmp_path, monkeypatch, setup=setup, data=data)
    assert start(env, accept=(NORMALIZED, DB)) == 0
    return env


def gain_writes(env):
    return device_messages(env).count("set_gain_db")


def restores(env):
    return device_messages(env).count("restore_gain")


def spy_opens(monkeypatch):
    opened = []
    real = os.open

    def spy(path, *args, **kwargs):
        opened.append(Path(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(export_handoff.os, "open", spy)
    return opened


def output_paths(env):
    request = awaiting(env)
    return {Path(request.workspace_directory) / o.file_name for o in request.request.outputs}


def intermediate_setup(tmp_path, gains=(0.0, -2.0)):
    """A predeclared long single-band fixture whose first accepted candidate does not yet meet the target."""
    setup = Setup(tmp_path, frames=69120, region=BandRegion("main-tone-test-band", (5,)), gains=gains)
    data = run_config_data(setup)
    data["run"]["criteria"]["f"]["targets"][0]["f"]["threshold"] = encode(MeasuredScalar(-15.8, "dBFS"))
    return setup, data


# --- recorded provider identity -> ClaudeConfig ------------------------------------------------------------------------


def identity(**changes):
    base = provider_identity(ClaudeConfig("explicit-test-model", "2023-06-01", 1024, 0.0, 20.0, 200000, 50000))
    parameters = json.loads(base.parameters)
    parameters.update(changes.pop("parameters", {}))
    for key in changes.pop("drop", ()):
        del parameters[key]
    return dataclasses.replace(base, parameters=canonical_json(parameters), **changes)


def test_config_from_identity_is_the_exact_inverse():
    config = ClaudeConfig("explicit-test-model", "2023-06-01", 1024, 0.25, 20.0, 200000, 50000)
    assert config_from_identity(provider_identity(config)) == config


@pytest.mark.parametrize(
    "bad",
    [
        dict(provider="another"),
        dict(parameters={"extra": 1}),
        dict(drop=("max_tokens",)),
        dict(parameters={"transport_profile": "messages-exact-body-v2"}),
        dict(parameters={"sdk_retries": 1}),
        dict(parameters={"sdk_retries": True}),
        dict(parameters={"sdk_retries": False}),  # False == 0: refused by the type check and by the round trip
        dict(parameters={"max_tokens": True}),
        dict(parameters={"temperature": 2.0}),
        dict(api_version="2099-01-01"),
    ],
)
def test_config_from_identity_refuses_unsupported_or_tampered_identities(bad):
    with pytest.raises(ValueError):
        config_from_identity(identity(**bad))


def test_config_from_identity_requires_a_provider_identity_record():
    with pytest.raises(ValueError):
        config_from_identity(ProviderRequestIdentity("anthropic", "2023-06-01", "m", "{}"))


# --- input errors (exit 2) and the process-local block ---------------------------------------------------------------


@pytest.mark.parametrize(
    "case", ["no_export_id", "blank_operator", "blank_attestation", "bad_run_id", "run_config", "no_credential"]
)
def test_invalid_input_exits_2_before_opening_anything(tmp_path, monkeypatch, capsys, case):
    env = suspended_env(tmp_path, monkeypatch)
    before = (tmp_path / "stores" / f"{RUN}.sqlite").read_bytes()
    capsys.readouterr()
    if case == "no_export_id":
        code = proceed(env, export_id=" ")
    elif case == "blank_operator":
        code = proceed(env, operator="  ")
    elif case == "blank_attestation":
        code = proceed(env, attestation=" ")
    elif case == "bad_run_id":
        code = proceed(env, run_id="bad id", export_id="x")
    elif case == "run_config":
        code = proceed(env, extra=("--run-config", str(env.run)))
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        code = proceed(env)
    assert code == 2
    assert (tmp_path / "stores" / f"{RUN}.sqlite").read_bytes() == before
    assert gain_writes(env) == 1 and len(env.provider.calls) == 1


def test_a_missing_export_id_option_is_a_usage_error(env, capsys):
    try:
        code = main(["daw", "continue", "--config", str(env.tool), "--run-id", RUN, "--operator", OPERATOR,
                     "--attestation", ATTESTATION])  # fmt: skip
    except SystemExit as exit_:
        code = exit_.code
    assert code == 2


def test_continue_refuses_while_this_process_retains_a_blocked_controller(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    blocked = SimpleNamespace(blocked=True)
    daw_commands._UNRELEASED_CONTROLLERS.append(blocked)
    export(env)
    opened = spy_opens(monkeypatch)
    sends = len(env.provider.calls)
    assert proceed(env) == 4
    err = capsys.readouterr().err
    assert "unreleased MIDI transport" in err and REFUSED_CLAUSE in err
    assert opened == [] and len(env.provider.calls) == sends and gain_writes(env) == 1
    assert daw_commands._UNRELEASED_CONTROLLERS == [blocked]


# --- state refusals before effects (exit 4) ----------------------------------------------------------------------------


def test_a_missing_run_is_refused(env, capsys):
    assert proceed(env, export_id="export-x") == 4
    assert "REFUSED" in capsys.readouterr().err


def test_a_busy_store_is_refused(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    export_id = awaiting(env).request.export_id
    holder = SqliteRunJournal.open(env.setup.config.store_path(RUN))
    try:
        assert proceed(env, export_id=export_id) == 4
    finally:
        holder.close()
    err = capsys.readouterr().err
    assert "in use" in err and REFUSED_CLAUSE in err
    assert gain_writes(env) == 1 and len(env.provider.calls) == 1


def test_configuration_drift_is_refused(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    settings = json.loads(env.tool.read_text(encoding="utf-8"))
    settings["midi"]["output_port"] = "another-port"
    env.tool.write_text(json.dumps(settings), encoding="utf-8")
    assert proceed(env) == 4
    assert "drifted" in capsys.readouterr().err
    assert gain_writes(env) == 1 and len(env.provider.calls) == 1


def test_an_unusable_stored_manifest_is_refused_and_never_regenerated(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    (manifest,) = (env.setup.config.run_store_dir / f"{RUN}.runtime" / "plans").glob("*.json")
    manifest.unlink()
    assert proceed(env) == 4
    assert "manifest is unusable" in capsys.readouterr().err
    assert not manifest.exists() and gain_writes(env) == 1


def test_a_wrong_export_id_is_refused_before_any_output_is_opened(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    opened = spy_opens(monkeypatch)
    assert proceed(env, export_id="export-not-this-one") == 4
    err = capsys.readouterr().err
    assert awaiting(env).request.export_id in err and REFUSED_CLAUSE in err
    assert not set(opened) & output_paths(env)
    assert len(env.provider.calls) == 1 and gain_writes(env) == 1


# --- safe manifest construction (exit 4, nothing recorded, suspension still resumable) --------------------------------


def assert_refused_then_resumable(env, capsys, code, *, repair, opened=None):
    events = len(snapshot(env).events)
    assert proceed(env) == 4
    err = capsys.readouterr().err
    assert f"REFUSED: {code}" in err and REFUSED_CLAUSE in err
    assert len(snapshot(env).events) == events and gain_writes(env) == 1
    if opened is not None:
        assert not set(opened) & output_paths(env)  # refused before any declared output was opened
    repair()
    assert proceed(env) == 0
    assert capsys.readouterr().out.startswith("ACCEPTED")


def test_a_stray_file_is_refused_before_hashing(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    workspace = export(env)
    stray = workspace / "notes.txt"
    stray.write_text("x", encoding="utf-8")
    opened = spy_opens(monkeypatch)
    assert proceed(env) == 4
    assert not set(opened) & output_paths(env)
    capsys.readouterr()
    stray.unlink()
    assert proceed(env) == 0


def test_a_hard_linked_output_is_refused_before_hashing(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    target = sorted(output_paths(env))[0]
    outside = tmp_path / "outside-link.wav"
    try:
        os.link(target, outside)
    except OSError as error:
        pytest.skip(f"the OS refuses to create a hard link here: {error}")
    opened = spy_opens(monkeypatch)
    assert_refused_then_resumable(env, capsys, "linked_file", repair=outside.unlink, opened=opened)


def test_a_symlinked_output_is_refused_without_opening_its_target(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    target = sorted(output_paths(env))[0]
    outside = tmp_path / "outside.wav"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"the OS refuses to create a symbolic link here: {error}")
    opened = spy_opens(monkeypatch)
    assert proceed(env) == 4
    assert not set(opened) & (output_paths(env) | {outside})


def test_a_replaced_workspace_is_refused(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    workspace = export(env)
    moved = workspace.with_name(workspace.name + "-moved")
    workspace.rename(moved)
    workspace.mkdir()
    for path in moved.iterdir():
        (workspace / path.name).write_bytes(path.read_bytes())
    opened = spy_opens(monkeypatch)

    def repair():
        for path in workspace.iterdir():
            path.unlink()
        workspace.rmdir()
        moved.rename(workspace)

    assert_refused_then_resumable(env, capsys, "workspace_replaced", repair=repair, opened=opened)


def test_a_workspace_directory_replaced_around_the_same_files_is_refused(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    workspace = export(env)
    moved = workspace.with_name(workspace.name + "-moved")
    workspace.rename(moved)
    workspace.mkdir()
    for path in list(moved.iterdir()):
        path.rename(workspace / path.name)  # the same physical files (identities preserved) in another directory
    opened = spy_opens(monkeypatch)

    def repair():
        for path in list(workspace.iterdir()):
            path.rename(moved / path.name)
        workspace.rmdir()
        moved.rename(workspace)

    assert_refused_then_resumable(env, capsys, "workspace_replaced", repair=repair, opened=opened)


def test_an_altered_request_file_is_refused(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    workspace = export(env)
    request = workspace / "export-request.json"
    original = request.read_bytes()
    request.write_bytes(original.replace(b'"v":1', b'"v":2'))
    assert_refused_then_resumable(env, capsys, "request_mismatch", repair=lambda: request.write_bytes(original))


def test_an_oversized_output_is_refused_after_a_bounded_read(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    target = sorted(output_paths(env))[0]
    original = target.read_bytes()
    limit = env.setup.config.max_file_bytes
    target.write_bytes(original + b"\0" * (limit + 1 - len(original) + 1000))
    reads = []
    real = os.read

    def spy(descriptor, size):
        chunk = real(descriptor, size)
        reads.append(len(chunk))
        return chunk

    monkeypatch.setattr(export_handoff.os, "read", spy)
    others = sum(p.stat().st_size for p in target.parent.iterdir() if p != target)
    first_attempt = []

    def repair():
        first_attempt.extend(reads)
        target.write_bytes(original)

    assert_refused_then_resumable(env, capsys, "resource_limit", repair=repair)
    assert sum(first_attempt) <= limit + 1 + others  # the oversized file was never read past its bound


def test_a_file_swapped_between_inspection_and_open_is_refused(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    target = sorted(output_paths(env))[0]
    original = target.read_bytes()
    real = os.open
    swapped = []

    def swap_then_open(path, *args, **kwargs):
        if Path(path) == target and not swapped:
            swapped.append(True)
            target.unlink()
            target.write_bytes(original)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(export_handoff.os, "open", swap_then_open)
    assert proceed(env) == 4
    err = capsys.readouterr().err
    assert "REFUSED: workspace_changed" in err and "changed before it was opened" in err and swapped
    monkeypatch.setattr(export_handoff.os, "open", real)
    assert proceed(env) == 0


def test_a_file_changed_during_its_read_is_refused(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    target = sorted(output_paths(env))[0]
    original = target.read_bytes()
    real_open, real_read = os.open, os.read
    target_fds, changed = [], []

    def track_open(path, *args, **kwargs):
        descriptor = real_open(path, *args, **kwargs)
        if Path(path) == target:
            target_fds.append(descriptor)
        return descriptor

    def change_after_first_chunk(descriptor, size):  # the declared output changes while it is being hashed
        chunk = real_read(descriptor, size)
        if descriptor in target_fds and not changed:
            changed.append(True)
            with open(target, "ab") as handle:
                handle.write(b"\0")
        return chunk

    monkeypatch.setattr(export_handoff.os, "open", track_open)
    monkeypatch.setattr(export_handoff.os, "read", change_after_first_chunk)
    assert proceed(env) == 4
    assert "REFUSED: workspace_changed" in capsys.readouterr().err and changed
    monkeypatch.setattr(export_handoff.os, "open", real_open)
    monkeypatch.setattr(export_handoff.os, "read", real_read)
    target.write_bytes(original)
    assert proceed(env) == 0


# --- outcomes (exit 0) -------------------------------------------------------------------------------------------------


def test_an_exported_suspension_is_continued_to_an_accepted_candidate(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    capsys.readouterr()
    assert proceed(env) == 0
    captured = capsys.readouterr()
    out = captured.out
    assert out.splitlines()[0] == f"ACCEPTED: the latest candidate met the declared criteria (run {RUN})"
    assert "acceptances: 1" in out and "DAW writes 1/3" in out and "verified: " in out and "not verified" not in out
    assert "engineering criteria only, not a quality judgement" in out
    assert gain_writes(env) == 1 and restores(env) == 0 and len(env.provider.calls) == 1
    assert_factual(out + captured.err)
    assert_no_credential_anywhere(env, captured)


def test_a_guard_rejected_candidate_is_restored_before_the_next_suspension(tmp_path, monkeypatch, capsys):
    from autonomous_mixing.core.records.identifiers import ConditionId
    from autonomous_mixing.core.records.verification import BaselineReference, DeltaGuard
    from autonomous_mixing.core.records.verification_assessment import ConditionMeasurement

    setup = Setup(tmp_path, gains=(-1.0, 0.0))
    original = setup.request

    def stricter(plan, definitions):
        request = original(plan, definitions)
        criteria = request.configuration.criteria
        guard = DeltaGuard(ConditionId("stem-guard"), criteria.targets[0].selector, BaselineReference.INITIAL,
                           MeasuredScalar(2.5, "dBFS"))  # fmt: skip
        measurement = ConditionMeasurement(guard.condition_id, request.measurements[0].definition)
        return dataclasses.replace(
            request,
            configuration=dataclasses.replace(
                request.configuration, criteria=dataclasses.replace(criteria, guards=(*criteria.guards, guard))
            ),
            measurements=(*request.measurements, measurement),
        )

    setup.request = stricter
    env = suspended_env(tmp_path, monkeypatch, setup=setup)
    export(env)
    capsys.readouterr()
    assert proceed(env) == 0
    out = capsys.readouterr().out
    usage = snapshot(env).events[-1].usage  # read from the record, never assumed
    assert out.startswith("SUSPENDED") and "acceptances: 0" in out and "DAW writes 2/3" in out
    assert usage.recovery_operations >= 1 and f"recovery operations {usage.recovery_operations}/" in out
    assert restores(env) == 1 and gain_writes(env) == 2 and len(env.provider.calls) == 2
    export(env)
    assert proceed(env) == 0
    assert capsys.readouterr().out.startswith("ACCEPTED")


def test_an_intermediate_acceptance_suspends_again_and_a_stale_export_id_is_refused(tmp_path, monkeypatch, capsys):
    setup, data = intermediate_setup(tmp_path)
    env = suspended_env(tmp_path, monkeypatch, setup=setup, data=data)
    first_export = awaiting(env).request.export_id
    export(env)
    capsys.readouterr()
    assert proceed(env) == 0
    out = capsys.readouterr().out
    second = awaiting(env).request.export_id
    assert out.startswith("SUSPENDED") and "acceptances: 1" in out and second != first_export
    assert f"--export-id {second}" in out and "not available" not in out
    opened = spy_opens(monkeypatch)
    assert proceed(env, export_id=first_export) == 4  # the operator repeats the old command
    assert not opened and len(env.provider.calls) == 2 and gain_writes(env) == 2
    capsys.readouterr()
    export(env)
    assert proceed(env) == 0
    out = capsys.readouterr().out
    assert out.startswith("ACCEPTED") and "acceptances: 2" in out and "DAW writes 2/3" in out


@pytest.mark.parametrize("ending", ["abstained", "limit"])
def test_an_accepted_intermediate_candidate_then_abstention_or_limit_shows_earlier_effects(tmp_path, monkeypatch,
                                                                                          capsys, ending):  # fmt: skip
    setup, data = intermediate_setup(tmp_path, gains=(0.0,))
    if ending == "limit":
        data["run"]["budget_limits"]["f"]["max_decision_calls"] = 1
    env = suspended_env(tmp_path, monkeypatch, setup=setup, data=data)
    export(env)
    capsys.readouterr()
    assert proceed(env) == 0
    out = capsys.readouterr().out
    first = out.splitlines()[0]
    assert first.startswith("ABSTAINED" if ending == "abstained" else "LIMIT REACHED"), out
    assert "acceptances: 1" in out and "DAW writes 1/" in out and "(sequence 1)" in out
    assert_factual(out)


def test_a_finished_run_is_not_continued_again(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export_id = awaiting(env).request.export_id
    export(env)
    assert proceed(env) == 0
    capsys.readouterr()
    assert proceed(env, export_id=export_id) == 4
    err = capsys.readouterr().err
    assert "not resumable: finished" in err and REFUSED_CLAUSE in err
    assert len(env.provider.calls) == 1 and gain_writes(env) == 1 and restores(env) == 0


def test_an_import_rejection_after_a_valid_manifest_is_a_recorded_outcome(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    target = sorted(output_paths(env))[0]
    real = daw_commands.DawExportManifest

    def swap_after_hashing(*args, **kwargs):  # between hashing (K6) and import (K8)
        target.write_bytes(target.read_bytes()[:-8] + b"\1" * 8)
        return real(*args, **kwargs)

    monkeypatch.setattr(daw_commands, "DawExportManifest", swap_after_hashing)
    capsys.readouterr()
    assert proceed(env) == 0
    out = capsys.readouterr().out
    assert out.startswith("FAILED: export_import_rejected") or out.startswith("FAILED: daw_continuity_failed"), out


def test_a_lost_acceptance_acknowledgement_keeps_authority(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    commit = SqliteRunJournal._commit
    lost = []

    def lose_acceptance_ack(self):
        accepted = self._connection.execute("SELECT count(*) FROM acceptances").fetchone()[0]
        commit(self)
        if accepted and not lost:
            lost.append(True)
            raise sqlite3.OperationalError("labelled lost acceptance acknowledgement")

    monkeypatch.setattr(SqliteRunJournal, "_commit", lose_acceptance_ack)
    capsys.readouterr()
    assert proceed(env) == 0
    out = capsys.readouterr().out
    assert lost and "(sequence 1)" in out and restores(env) == 0
    assert len(snapshot(env).acceptances) == 1


# --- failure reporting and cleanup --------------------------------------------------------------------------------------


def test_provider_construction_failure_during_reopen_is_type_only(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)

    def failing(config, api_key):
        raise RuntimeError(f"could not build transport for {api_key}")

    monkeypatch.setattr(agent_runtime, "ClaudeMessagesTransport", failing)
    capsys.readouterr()
    assert proceed(env) == 4
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err and "could not build" not in captured.err and REFUSED_CLAUSE in captured.err
    assert_no_credential_anywhere(env, captured)
    assert gain_writes(env) == 1


def test_a_provider_failure_during_continuation_is_stopped(tmp_path, monkeypatch, capsys):
    setup, data = intermediate_setup(tmp_path)
    env = suspended_env(tmp_path, monkeypatch, setup=setup, data=data)
    export(env)

    def failing_send(body, *, timeout_seconds):
        raise ConnectionError(f"reset while sending with {SENTINEL}")

    monkeypatch.setattr(env.provider, "send", failing_send)
    capsys.readouterr()
    assert proceed(env) == 4
    captured = capsys.readouterr()
    assert "STOPPED" in captured.err and "ConnectionError" in captured.err and REFUSED_CLAUSE not in captured.err
    assert_no_credential_anywhere(env, captured)


def test_a_cleanup_failure_after_acceptance_keeps_the_outcome(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)

    def failing_close():
        raise RuntimeError(f"close failed for {SENTINEL}")

    monkeypatch.setattr(env.provider, "close", failing_close)
    capsys.readouterr()
    assert proceed(env) == 4
    captured = capsys.readouterr()
    assert captured.out.startswith("ACCEPTED") and "cleanup failed (RuntimeError)" in captured.err
    assert "REFUSED" not in captured.err
    assert_no_credential_anywhere(env, captured)


def test_a_runtime_controller_blocked_during_continuation_blocks_later_commands(tmp_path, monkeypatch, capsys):
    setup, data = intermediate_setup(tmp_path)
    env = suspended_env(tmp_path, monkeypatch, setup=setup, data=data)
    export(env)
    CloseFailsOnce.closes = []
    opened = []

    def factory(*args):
        opened.append(CloseFailsOnce(env.world))
        return opened[-1]

    monkeypatch.setattr(agent_runtime, "WinMMTransport", factory)
    monkeypatch.setattr(daw_runtime, "WinMMTransport", factory)
    capsys.readouterr()
    proceed(env)
    (controller,) = daw_commands._UNRELEASED_CONTROLLERS
    assert controller.blocked and opened[0] in controller._retained_transports
    count = len(opened)
    capsys.readouterr()
    assert start(env, accept=(NORMALIZED, DB), run_id="run-2") == 4
    assert proceed(env, export_id="any") == 4
    assert len(opened) == count


def test_a_runtime_controller_blocked_during_start_blocks_later_commands(env, monkeypatch, capsys):
    count = []

    class FailsOnSecondClose(CloseFailsOnce):
        def close(self):
            count.append(self)
            if len(count) == 2:  # the pre-check closes cleanly; the runtime's first transport does not
                raise OSError("injected close failure")

    monkeypatch.setattr(agent_runtime, "WinMMTransport", lambda *a: FailsOnSecondClose(env.world))
    monkeypatch.setattr(daw_runtime, "WinMMTransport", lambda *a: FailsOnSecondClose(env.world))
    start(env, accept=(NORMALIZED, DB))
    assert len(daw_commands._UNRELEASED_CONTROLLERS) == 1
    capsys.readouterr()
    assert start(env, accept=(NORMALIZED, DB), run_id="run-2") == 4
    assert "unreleased MIDI transport" in capsys.readouterr().err


# --- a new process continues without a second dispatch ----------------------------------------------------------------


def child(directory, argv, *, crash=None):
    env = {**os.environ, "ANTHROPIC_API_KEY": SENTINEL}
    if crash is not None:
        env["DAW_CLI_CHILD_CRASH"] = crash
    return subprocess.run([sys.executable, str(CHILD), str(directory), json.dumps(argv)], capture_output=True,
                          text=True, env=env, cwd=str(Path(__file__).parents[2]))  # fmt: skip


def test_a_new_process_continues_without_a_second_dispatch_or_write(tmp_path, capsys):
    setup = Setup(tmp_path)
    from run_config_support import write_run_config

    run_path = write_run_config(setup)
    tool = str(setup.config.path)
    first = child(tmp_path, ["daw", "start", "--config", tool, "--run-config", str(run_path), "--run-id", RUN,
                             "--accept-current-fader", NORMALIZED, DB])  # fmt: skip
    assert first.returncode == 0, first.stderr
    assert first.stdout.startswith("SUSPENDED")
    store = SqliteRunJournal.open_read_only(setup.config.store_path(RUN))
    try:
        checkpoint = store.snapshot().value.events[-1]
    finally:
        store.close()
    setup.export(SimpleNamespace(checkpoint_event=checkpoint))
    calls_before = json.loads((tmp_path / "provider_calls.json").read_text())
    second = child(tmp_path, ["daw", "continue", "--config", tool, "--run-id", RUN, "--export-id",
                              checkpoint.payload.awaiting.request.export_id, "--operator", OPERATOR,
                              "--attestation", ATTESTATION])  # fmt: skip
    assert second.returncode == 0, second.stderr
    assert second.stdout.startswith("ACCEPTED")
    assert json.loads((tmp_path / "provider_calls.json").read_text()) == calls_before == 1
    host = json.loads((tmp_path / "host.json").read_text())
    assert len(host["writes"]) == 1  # one fader write in total: the first process's, never resent
    assert SENTINEL not in first.stdout + first.stderr + second.stdout + second.stderr


# --- help ---------------------------------------------------------------------------------------------------------------


def test_continue_help_lists_the_options(capsys):
    with pytest.raises(SystemExit):
        main(["daw", "continue", "--help"])
    out = capsys.readouterr().out
    assert all(o in out for o in ("--config", "--run-id", "--export-id", "--operator", "--attestation"))
    assert "ANTHROPIC_API_KEY" in out


# --- the recorded provider identity decides; admission refusals are refusals ----------------------------------------


def test_a_run_recorded_with_non_default_provider_settings_continues_with_exactly_those(tmp_path, monkeypatch, capsys):
    setup = Setup(tmp_path)
    data = run_config_data(setup)
    data["provider"]["max_tokens"] = 777
    data["provider"]["temperature"] = 0.25
    setup.provider.config = dataclasses.replace(setup.provider.config, max_tokens=777, temperature=0.25)
    env = suspended_env(tmp_path, monkeypatch, setup=setup, data=data)
    export(env)
    seen = []
    original = agent_runtime.ClaudeMessagesTransport

    def recording(config, api_key):
        seen.append(config)
        return original(config, api_key)

    monkeypatch.setattr(agent_runtime, "ClaudeMessagesTransport", recording)
    capsys.readouterr()
    assert proceed(env) == 0
    assert capsys.readouterr().out.startswith("ACCEPTED")
    assert [(c.max_tokens, c.temperature) for c in seen] == [(777, 0.25)]


def test_an_in_process_concurrent_continuation_is_refused_by_admission(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    monkeypatch.setattr(SqliteRunJournal, "try_acquire_exclusive", lambda self: False)  # labelled: another holder
    capsys.readouterr()
    assert proceed(env) == 4
    err = capsys.readouterr().err
    assert "REFUSED: continuation_in_progress" in err and REFUSED_CLAUSE in err
    assert gain_writes(env) == 1 and restores(env) == 0 and len(env.provider.calls) == 1


# --- review fixes (2026-11-24): per-output fail-fast hashing; interrupted continuation through the CLI ----------------


def test_a_change_to_one_output_is_refused_before_the_next_output_is_opened(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    request = awaiting(env).request
    workspace = Path(awaiting(env).workspace_directory)
    first, later = workspace / request.outputs[0].file_name, [workspace / o.file_name for o in request.outputs[1:]]
    assert later  # the fixture declares a stem and a mix
    original = first.read_bytes()
    real_open, real_read = os.open, os.read
    opened, first_fds, changed = [], [], []

    def track_open(path, *args, **kwargs):
        descriptor = real_open(path, *args, **kwargs)
        opened.append(Path(path))
        if Path(path) == first:
            first_fds.append(descriptor)
        return descriptor

    def change_first_while_reading(descriptor, size):
        chunk = real_read(descriptor, size)
        if descriptor in first_fds and not changed:
            changed.append(True)
            with open(first, "ab") as handle:
                handle.write(b"\0")
        return chunk

    monkeypatch.setattr(export_handoff.os, "open", track_open)
    monkeypatch.setattr(export_handoff.os, "read", change_first_while_reading)
    assert proceed(env) == 4
    err = capsys.readouterr().err
    assert changed and "REFUSED: workspace_changed" in err and request.outputs[0].file_name in err
    assert not set(opened) & set(later)  # fail fast: no later declared output was opened or read
    monkeypatch.setattr(export_handoff.os, "open", real_open)
    monkeypatch.setattr(export_handoff.os, "read", real_read)
    first.write_bytes(original)
    assert proceed(env) == 0


def test_an_interrupted_continuation_is_refused_and_shown_by_the_read_only_recovery_view(tmp_path, capsys):
    from run_config_support import write_run_config

    setup = Setup(tmp_path)
    run_path = write_run_config(setup)
    tool = str(setup.config.path)
    first = child(tmp_path, ["daw", "start", "--config", tool, "--run-config", str(run_path), "--run-id", RUN,
                             "--accept-current-fader", NORMALIZED, DB])  # fmt: skip
    assert first.returncode == 0, first.stderr
    store = SqliteRunJournal.open_read_only(setup.config.store_path(RUN))
    try:
        checkpoint = store.snapshot().value.events[-1]
    finally:
        store.close()
    setup.export(SimpleNamespace(checkpoint_event=checkpoint))
    export_id = checkpoint.payload.awaiting.request.export_id
    argv = ["daw", "continue", "--config", tool, "--run-id", RUN, "--export-id", export_id, "--operator", OPERATOR,
            "--attestation", ATTESTATION]  # fmt: skip
    crashed = child(tmp_path, argv, crash="after_continuation_started")
    assert crashed.returncode == 77, crashed.stderr  # ended abruptly right after the continuation marker committed
    calls = json.loads((tmp_path / "provider_calls.json").read_text())
    writes = len(json.loads((tmp_path / "host.json").read_text())["writes"])
    again = child(tmp_path, argv)
    assert again.returncode == 4
    assert "continuation_interrupted" in again.stderr and REFUSED_CLAUSE in again.stderr
    view = child(tmp_path, ["daw", "recover", "--config", tool, "--run-id", RUN, "--json"])
    assert view.returncode == 0, view.stderr
    assert json.loads(view.stdout)["classification"] == "continuation_interrupted"
    assert json.loads((tmp_path / "provider_calls.json").read_text()) == calls  # nothing dispatched afterwards
    assert len(json.loads((tmp_path / "host.json").read_text())["writes"]) == writes  # no write resent
    assert SENTINEL not in again.stdout + again.stderr + view.stdout + view.stderr


def test_an_earlier_output_changed_while_a_later_one_is_hashed_is_caught_by_the_final_check(tmp_path, monkeypatch,
                                                                                               capsys):  # fmt: skip
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    request = awaiting(env).request
    workspace = Path(awaiting(env).workspace_directory)
    first, last = workspace / request.outputs[0].file_name, workspace / request.outputs[-1].file_name
    original = first.read_bytes()
    real_open, real_read = os.open, os.read
    last_fds, changed = [], []

    def track_open(path, *args, **kwargs):
        descriptor = real_open(path, *args, **kwargs)
        if Path(path) == last:
            last_fds.append(descriptor)
        return descriptor

    def change_first_while_reading_last(descriptor, size):  # the first output already passed its per-output check
        chunk = real_read(descriptor, size)
        if descriptor in last_fds and not changed:
            changed.append(True)
            with open(first, "ab") as handle:
                handle.write(b"\0")
        return chunk

    monkeypatch.setattr(export_handoff.os, "open", track_open)
    monkeypatch.setattr(export_handoff.os, "read", change_first_while_reading_last)
    assert proceed(env) == 4
    err = capsys.readouterr().err
    assert changed and "REFUSED: workspace_changed" in err and f"{request.outputs[0].file_name} changed while" in err
    monkeypatch.setattr(export_handoff.os, "open", real_open)
    monkeypatch.setattr(export_handoff.os, "read", real_read)
    first.write_bytes(original)
    assert proceed(env) == 0
