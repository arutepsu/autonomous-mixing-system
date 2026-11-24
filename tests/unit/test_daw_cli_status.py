"""R5C increment C4: read-only ``daw status`` (and the read-only recovery view) for provider-bound runs.

Real run stores and FL journals from the existing synthetic fixtures (``daw start``/``daw continue`` through ``main`` with
only external transports simulated). Status must stay read-only: no writable or exclusive store open, no MIDI, no
provider transport, no credential, no decision-audit store, no analysis-evidence or preparation file. It reports what is
recorded, clearly unverified; ``daw continue`` performs the provenance checks under ownership.
"""

import hashlib
import json
import shutil
import sqlite3
from dataclasses import replace

from test_daw_cli import crashed
from test_daw_cli_continue import (
    ATTESTATION,
    OPERATOR,
    awaiting,
    child,
    export,
    proceed,
    suspended_env,
)
from test_daw_cli_start import DB, NORMALIZED, RUN, SENTINEL

from autonomous_mixing.adapters.dsp_analysis import directional_analysis, masking_preparation
from autonomous_mixing.adapters.fl_studio.journal import SqliteCommandJournal
from autonomous_mixing.adapters.journal.decision_exchange import SqliteDecisionExchangeStore
from autonomous_mixing.adapters.journal.sqlite_run import SqliteRunJournal
from autonomous_mixing.core.loop.operator_recovery import run_view
from autonomous_mixing.core.records.provider_run import ProviderRunBinding
from autonomous_mixing.drivers import agent_runtime, daw_commands, daw_runtime
from autonomous_mixing.drivers.cli import main

# --- read-only guards: any forbidden open fails the test ---------------------------------------------------------------


def apply_guards(monkeypatch):
    """Fail on MIDI, provider transport, writable stores/journals, audit stores, evidence/preparation files.

    Applied after the run was produced with simulated transports; ``status`` and the recovery view then run with
    production code paths under these guards and without a credential.
    """

    def fail(what):
        def raiser(*args, **kwargs):
            raise AssertionError(f"status/recover view opened {what}")

        return raiser

    for module in (daw_runtime, agent_runtime):
        monkeypatch.setattr(module, "WinMMTransport", fail("a MIDI transport"))
    monkeypatch.setattr(agent_runtime, "ClaudeMessagesTransport", fail("a provider transport"))
    monkeypatch.setattr(SqliteRunJournal, "open", fail("a writable run store"))
    monkeypatch.setattr(SqliteRunJournal, "create", fail("a new run store"))
    monkeypatch.setattr(SqliteDecisionExchangeStore, "open", fail("the decision-audit store"))
    monkeypatch.setattr(SqliteDecisionExchangeStore, "create", fail("a decision-audit store"))
    monkeypatch.setattr(directional_analysis.DirectionalMaskingAnalyzer, "__init__", fail("the evidence analyzer"))
    monkeypatch.setattr(masking_preparation, "load_prepared_plan", fail("the preparation manifest"))
    monkeypatch.setattr(agent_runtime, "load_prepared_plan", fail("the preparation manifest"))
    real_journal = SqliteCommandJournal.__init__

    def read_only_journal(self, path, *, session_key, read_only=False):
        if not read_only:
            raise AssertionError("status/recover view opened the FL journal writable")
        real_journal(self, path, session_key=session_key, read_only=read_only)

    monkeypatch.setattr(SqliteCommandJournal, "__init__", read_only_journal)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # status needs no credential


def files(root):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def status(tool, *args):
    code = main(["daw", "status", "--config", str(tool), *args])
    return code


def binding_of(env):
    store = SqliteRunJournal.open_read_only(env.setup.config.store_path(RUN))
    try:
        return store.snapshot().value.events[0].payload.provider_binding
    finally:
        store.close()


# --- provider-bound runs: suspended, finished, interrupted ---------------------------------------------------------------


def test_a_suspended_provider_run_shows_its_recorded_state_and_provider_unverified(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    binding = binding_of(env)
    expected_export = awaiting(env)
    before = files(tmp_path)
    capsys.readouterr()
    monkeypatch.undo()  # status runs with production modules, under the read-only guards
    apply_guards(monkeypatch)
    assert status(env.tool, "--run-id", RUN, "--json") == 4
    shown = json.loads(capsys.readouterr().out)
    assert shown["classification"] == "provider_evidence_unavailable"
    assert "not verified" in shown["explanation"] and "daw continue" in shown["explanation"]
    assert "resumable" not in shown["explanation"]
    recorded = shown["recorded_state"]
    assert recorded == {
        "verified": False,
        "classification": "suspended_resumable",
        "explanation": recorded["explanation"],
    }
    assert "manual export" in recorded["explanation"]
    assert shown["awaiting_export"]["export_id"] == expected_export.request.export_id
    assert shown["awaiting_export"]["files"] == [o.file_name for o in expected_export.request.outputs]
    assert shown["authoritative"] == {"revision": "r1", "sequence": 0}
    assert shown["usage"]["daw_writes"] == 1 and shown["limits"]["max_daw_writes"] == 3
    assert shown["fl"]["available"] is True and shown["fl"]["unresolved"] == []
    provider = shown["provider"]
    assert provider["binding"] == "DawAgentBinding"
    assert provider["provider"] == "anthropic" and provider["api_version"] == binding.provider.api_version
    assert provider["model"] == binding.provider.requested_model
    assert provider["parameters"] == json.loads(binding.provider.parameters)
    assert provider["prompt"] == f"{binding.prompt.name}@{binding.prompt.version}"
    assert provider["response_schema"] == f"{binding.response_schema.name}@{binding.response_schema.version}"
    method = binding.diagnosis_policy.method
    assert provider["diagnosis_method"] == f"{method.name}@{method.version}"
    assert provider["prepared_plan_sha256"] == binding.diagnosis_policy.measurement_plan.prepared_digest
    assert provider["runtime_settings_sha256"] == hashlib.sha256(binding.runtime_settings.encode()).hexdigest()
    text = json.dumps(shown)
    assert binding.runtime_settings not in text and str(env.setup.config.run_store_dir) not in text
    assert str(env.setup.config.fl_journal) not in text and SENTINEL not in text
    assert files(tmp_path) == before  # byte-identical stores, journals, evidence and audit files


def test_plain_status_shows_the_useful_provider_information(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export_id = awaiting(env).request.export_id
    capsys.readouterr()
    monkeypatch.undo()
    apply_guards(monkeypatch)
    assert status(env.tool, "--run-id", RUN) == 4
    out = capsys.readouterr().out
    assert out.startswith(f"{RUN}: provider_evidence_unavailable - ")
    for text in ("recorded state (unverified): suspended_resumable", "provider: anthropic 2023-06-01",
                 "prompt autonomous_mixing.claude_prompt@1", "prepared plan sha256 ", "runtime settings sha256 ",
                 "authoritative: r1 (sequence 0)", "DAW writes 1/3", f"awaiting export {export_id}",
                 "daw continue --config", "FL facts: available"):  # fmt: skip
        assert text in out, text
    assert "unavailable decision maker" not in out


def test_a_finished_provider_run_shows_its_recorded_acceptance(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    export(env)
    assert proceed(env) == 0
    capsys.readouterr()
    before = files(tmp_path)
    monkeypatch.undo()
    apply_guards(monkeypatch)
    assert status(env.tool, "--run-id", RUN, "--json") == 4
    shown = json.loads(capsys.readouterr().out)
    assert shown["classification"] == "provider_evidence_unavailable"
    assert shown["recorded_state"]["classification"] == "finished" and shown["recorded_state"]["verified"] is False
    assert shown["authoritative"]["sequence"] == 1 and "awaiting_export" not in shown
    assert files(tmp_path) == before


def test_an_interrupted_provider_run_shows_continuation_interrupted(tmp_path, monkeypatch, capsys):
    from agent_runtime_support import Setup
    from run_config_support import write_run_config

    setup = Setup(tmp_path)
    run_path = write_run_config(setup)
    tool = str(setup.config.path)
    assert child(tmp_path, ["daw", "start", "--config", tool, "--run-config", str(run_path), "--run-id", RUN,
                            "--accept-current-fader", NORMALIZED, DB]).returncode == 0  # fmt: skip
    store = SqliteRunJournal.open_read_only(setup.config.store_path(RUN))
    try:
        checkpoint = store.snapshot().value.events[-1]
    finally:
        store.close()
    setup.export(type("S", (), {"checkpoint_event": checkpoint})())
    crashed_child = child(tmp_path, ["daw", "continue", "--config", tool, "--run-id", RUN, "--export-id",
                                     checkpoint.payload.awaiting.request.export_id, "--operator", OPERATOR,
                                     "--attestation", ATTESTATION], crash="after_continuation_started")  # fmt: skip
    assert crashed_child.returncode == 77
    before = files(tmp_path)
    apply_guards(monkeypatch)
    assert status(tool, "--run-id", RUN, "--json") == 4
    shown = json.loads(capsys.readouterr().out)
    assert shown["classification"] == "provider_evidence_unavailable"
    assert shown["recorded_state"]["classification"] == "continuation_interrupted"
    assert "daw recover" in shown["recorded_state"]["explanation"]
    assert files(tmp_path) == before


# --- base ProviderRunBinding (no local runtime settings) -----------------------------------------------------------------


def test_a_base_provider_binding_is_shown_without_runtime_settings(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    source = SqliteRunJournal.open_read_only(env.setup.config.store_path(RUN))
    try:
        snapshot = source.snapshot().value
    finally:
        source.close()
    request = snapshot.events[0].payload
    full = request.provider_binding
    base = ProviderRunBinding(full.provider, full.prompt, full.response_schema, full.diagnosis_policy)
    assert type(base) is ProviderRunBinding
    other_stores = tmp_path / "base-stores"
    other_stores.mkdir()
    settings = json.loads(env.tool.read_text(encoding="utf-8"))
    settings["run_store_dir"] = str(other_stores)
    tool = tmp_path / "base-tool.json"
    tool.write_text(json.dumps(settings), encoding="utf-8")
    store = SqliteRunJournal.create(other_stores / f"{RUN}.sqlite", snapshot.run_id, snapshot.initial)
    try:  # the same recorded run, its request carrying only the base provider binding (a real store)
        store.append(replace(snapshot.events[0], payload=replace(request, provider_binding=base)))
        for event in snapshot.events[1:]:
            store.append(event)
    finally:
        store.close()
    capsys.readouterr()
    monkeypatch.undo()
    apply_guards(monkeypatch)
    assert status(tool, "--run-id", RUN, "--json") == 4
    shown = json.loads(capsys.readouterr().out)
    assert shown["classification"] == "provider_evidence_unavailable"
    assert shown["provider"]["binding"] == "ProviderRunBinding"
    assert "runtime_settings_sha256" not in shown["provider"]
    assert shown["provider"]["prepared_plan_sha256"] == full.diagnosis_policy.measurement_plan.prepared_digest
    assert shown["recorded_state"]["classification"] == "suspended_resumable"


# --- missing, busy, legacy and unreadable stores; unavailable FL facts ----------------------------------------------------


def test_store_problems_are_shown_as_unavailable_never_healthy(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    stores = env.setup.config.run_store_dir
    legacy = stores / "legacy.sqlite"
    shutil.copyfile(env.setup.config.store_path(RUN), legacy)
    connection = sqlite3.connect(legacy)
    try:
        column = connection.execute("PRAGMA table_info(metadata)").fetchone()[1]
        connection.execute(f"UPDATE metadata SET {column} = 1")
        connection.commit()
    finally:
        connection.close()
    (stores / "garbage.sqlite").write_bytes(b"not a database")
    holder = SqliteRunJournal.open(env.setup.config.store_path(RUN))
    capsys.readouterr()
    try:
        before = files(tmp_path)
        apply_guards(monkeypatch)
        assert status(env.tool, "--json") == 4
        shown = {s["run_id"]: s for s in json.loads(capsys.readouterr().out)}
        assert status(env.tool, "--run-id", "missing", "--json") == 4
        missing = json.loads(capsys.readouterr().out)
    finally:
        holder.close()
    assert shown[RUN]["classification"] == "in_use"
    assert shown["legacy"]["classification"] == "unsupported"
    assert shown["garbage"]["classification"] in ("unavailable", "unreadable")
    assert missing["classification"] in ("unavailable", "unreadable")
    assert all("recorded_state" not in s and "provider" not in s for s in (*shown.values(), missing))
    assert files(tmp_path) == before


def test_unavailable_fl_facts_stay_unavailable_never_an_empty_healthy_state(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    fl = env.setup.config.fl_journal
    fl.write_bytes(b"not an FL journal")
    capsys.readouterr()
    before = files(tmp_path)
    monkeypatch.undo()
    apply_guards(monkeypatch)
    assert status(env.tool, "--run-id", RUN, "--json") == 4
    shown = json.loads(capsys.readouterr().out)
    assert shown["fl"]["available"] is False and shown["fl"]["problem"]
    assert shown["recorded_state"]["classification"] != "suspended_resumable"
    assert shown["recorded_state"]["verified"] is False
    assert files(tmp_path) == before


# --- corrected guidance and unchanged recovery behaviour ---------------------------------------------------------------


def test_a_non_provider_suspension_points_to_the_manual_export_and_continue_flow(tmp_path, capsys, monkeypatch):
    config = crashed(tmp_path, point="none")
    apply_guards(monkeypatch)
    assert status(config, "--run-id", "run-1", "--json") == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["classification"] == "suspended_resumable"
    assert "daw continue" in shown["explanation"] and "unavailable" not in shown["explanation"]
    assert main(["daw", "recover", "--config", str(config), "--run-id", "run-1", "--json"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["options"] == [] and "daw continue" in view["explanation"]
    assert "decision maker" not in view["explanation"]


def test_the_read_only_recovery_view_of_a_provider_run_matches_the_same_facts_without_a_binding(tmp_path,
                                                                                                monkeypatch,
                                                                                                capsys):  # fmt: skip
    from agent_runtime_support import Setup
    from run_config_support import write_run_config

    setup = Setup(tmp_path)
    run_path = write_run_config(setup)
    tool = str(setup.config.path)
    assert child(tmp_path, ["daw", "start", "--config", tool, "--run-config", str(run_path), "--run-id", RUN,
                            "--accept-current-fader", NORMALIZED, DB]).returncode == 0  # fmt: skip
    store = SqliteRunJournal.open_read_only(setup.config.store_path(RUN))
    try:
        checkpoint = store.snapshot().value.events[-1]
    finally:
        store.close()
    setup.export(type("S", (), {"checkpoint_event": checkpoint})())
    assert child(tmp_path, ["daw", "continue", "--config", tool, "--run-id", RUN, "--export-id",
                            checkpoint.payload.awaiting.request.export_id, "--operator", OPERATOR, "--attestation",
                            ATTESTATION], crash="after_continuation_started").returncode == 77  # fmt: skip
    before = files(tmp_path)
    apply_guards(monkeypatch)
    assert main(["daw", "recover", "--config", tool, "--run-id", RUN, "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["classification"] == "continuation_interrupted" and shown["fingerprint"]
    config = daw_runtime.load_config(setup.config.path)
    view, store, snapshot, journal, _ = daw_commands._view(config, RUN, read_only=True)
    try:
        request = snapshot.events[0].payload
        unbound = replace(snapshot, events=(replace(snapshot.events[0], payload=replace(request, provider_binding=None)),
                                            *snapshot.events[1:]))  # fmt: skip
        _, _, facts, _ = daw_commands._fl_facts(snapshot, config, read_only=True)
        plain_view = run_view(unbound, facts)
    finally:
        for item in (store, journal):
            if item is not None:
                item.close()
    assert view.options == plain_view.options and view.run.code == plain_view.run.code
    assert [o["operation"] for o in shown["options"]] == [o.operation.value for o in view.options]
    assert files(tmp_path) == before


# --- review fix (2026-11-24): a snapshot that fails after a successful read-only open -----------------------------------


def _reader_spy(monkeypatch, *, after_open):
    """Wrap the real read-only open: ``after_open(path, reader)`` runs before status calls ``snapshot``."""
    real_open = SqliteRunJournal.open_read_only.__func__
    readers = []

    def opened(cls, path, *, run_id=None):
        reader = real_open(cls, path, run_id=run_id)
        close = reader.close

        def tracked_close():
            reader.closed_by_status = True
            close()

        reader.closed_by_status = False
        reader.close = tracked_close
        readers.append(reader)
        after_open(path, reader)
        return reader

    monkeypatch.setattr(SqliteRunJournal, "open_read_only", classmethod(opened))
    return readers


def _no_downstream_access(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("status continued past a failed snapshot")

    monkeypatch.setattr(daw_commands, "_fl_facts", fail)
    monkeypatch.setattr(daw_commands, "classify_run", fail)
    monkeypatch.setattr(daw_commands, "_provider", fail)


def test_a_store_that_becomes_busy_after_the_read_only_open_is_shown_unavailable(tmp_path, monkeypatch, capsys):
    env = suspended_env(tmp_path, monkeypatch)
    capsys.readouterr()
    monkeypatch.undo()
    real_exclusive = SqliteRunJournal.open.__func__  # captured before the guards forbid writable opens by status
    apply_guards(monkeypatch)
    holders = []

    def take_ownership(path, reader):
        holders.append(real_exclusive(SqliteRunJournal, path))

    before = files(tmp_path)
    with monkeypatch.context() as guarded:  # the ownership-taking reader and downstream guards end with this block
        readers = _reader_spy(guarded, after_open=take_ownership)
        _no_downstream_access(guarded)
        try:
            assert status(env.tool, "--run-id", RUN, "--json") == 4
            shown = json.loads(capsys.readouterr().out)
        finally:
            for holder in holders:
                holder.close()
    assert shown["classification"] in ("unavailable", "in_use") and shown["run_id"] == RUN
    assert "provider" not in shown and "recorded_state" not in shown
    assert readers and all(r.closed_by_status for r in readers)
    assert files(tmp_path) == before
    assert status(env.tool, "--run-id", RUN, "--json") == 4  # released: the normal provider status again
    assert json.loads(capsys.readouterr().out)["recorded_state"]["classification"] == "suspended_resumable"


def test_a_returned_unavailable_snapshot_is_shown_unavailable_without_relying_on_message_text(tmp_path, monkeypatch,
                                                                                               capsys):  # fmt: skip
    from autonomous_mixing.core.records.results import PortFailure, PortFailureCode

    env = suspended_env(tmp_path, monkeypatch)
    capsys.readouterr()
    monkeypatch.undo()
    apply_guards(monkeypatch)

    def failing_snapshot(path, reader):
        reader.snapshot = lambda: PortFailure(PortFailureCode.UNAVAILABLE, "labelled snapshot failure", False)

    readers = _reader_spy(monkeypatch, after_open=failing_snapshot)
    before = files(tmp_path)
    with monkeypatch.context() as guarded:
        _no_downstream_access(guarded)
        assert status(env.tool, "--run-id", RUN, "--json") == 4
        shown = json.loads(capsys.readouterr().out)
        assert status(env.tool, "--run-id", RUN) == 4
        plain = capsys.readouterr().out
    assert shown["classification"] == "unavailable" and "provider" not in shown and "recorded_state" not in shown
    assert plain.startswith(f"{RUN}: unavailable - ") and "recorded state" not in plain
    assert readers and all(r.closed_by_status for r in readers)
    assert files(tmp_path) == before
