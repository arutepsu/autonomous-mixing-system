"""Transactional intent/replay persistence, with subprocess crash probes and no devices."""

import json
import sqlite3
import subprocess
import sys
from dataclasses import replace

import pytest
from fl_runtime_support import make_request

from autonomous_mixing.adapters.fl_studio.journal import JournalError, SqliteCommandJournal
from autonomous_mixing.adapters.fl_studio.profile import make_apply_command
from autonomous_mixing.adapters.fl_studio.serialization import load_outcome, outcome_json, request_json
from autonomous_mixing.core.records.daw_binding import DawFaderReading
from autonomous_mixing.core.records.daw_control import (
    DawRefusalCode,
    DawUncertainReason,
    DawWriteConfirmed,
    DawWriteOffTarget,
    DawWriteRefused,
    DawWriteUncertain,
)
from autonomous_mixing.core.records.identifiers import ExecutionId


def reserve(journal, request):
    journal.reserve(request.command_id, request_json(request), json.dumps(make_apply_command(request), sort_keys=True))


def written(request, cls=DawWriteConfirmed, delta=-1.0):
    before = request.inspection.fader
    after = DawFaderReading(0.7257, before.db + delta)
    return cls(request.command_id, before, after, 0.7257, after.db - before.db, False)


def test_canonical_identity_includes_execution_revision_and_binding():
    request = make_request()
    assert request_json(request) == request_json(request)
    assert request_json(replace(request, execution_id=ExecutionId("other"))) != request_json(request)
    assert json.loads(request_json(request))["v"] == 1


@pytest.mark.parametrize("kind", ["confirmed", "off_target", "refused", "uncertain"])
def test_outcome_roundtrip_uses_validated_core_records(kind):
    request = make_request()
    outcome = {
        "confirmed": written(request),
        "off_target": written(request, DawWriteOffTarget, -0.5),
        "refused": DawWriteRefused(request.command_id, DawRefusalCode.START_GAIN, "drift", None),
        "uncertain": DawWriteUncertain(request.command_id, DawUncertainReason.TIMEOUT, None, None, "timeout"),
    }[kind]
    assert load_outcome(outcome_json(outcome)) == outcome


@pytest.mark.parametrize("value", ["{}", '{"v":true}', '{"v":1,"kind":"future","fields":{}}', "NaN"])
def test_outcome_decode_is_closed_to_unknown_shapes(value):
    with pytest.raises(ValueError):
        load_outcome(value)


def test_pending_and_finished_outcome_survive_reopen(tmp_path):
    path = tmp_path / "control.sqlite"
    request = make_request()
    journal = SqliteCommandJournal(path, session_key="session")
    reserve(journal, request)
    assert journal.pending_or_uncertain()
    journal.close()
    journal = SqliteCommandJournal(path, session_key="session")
    assert journal.lookup(request.command_id).outcome is None
    outcome = written(request)
    journal.finish(request.command_id, outcome)
    assert not journal.pending_or_uncertain()
    journal.close()
    journal = SqliteCommandJournal(path, session_key="session")
    assert journal.lookup(request.command_id).outcome == outcome
    assert journal.settings == {"journal_mode": "delete", "synchronous": 3, "locking_mode": "exclusive"}
    journal.close()


def test_second_owner_is_excluded_and_session_key_cannot_change(tmp_path):
    path = tmp_path / "control.sqlite"
    first = SqliteCommandJournal(path, session_key="session")
    with pytest.raises(JournalError):
        SqliteCommandJournal(path, session_key="session")
    first.close()
    with pytest.raises(JournalError):
        SqliteCommandJournal(path, session_key="another")


def test_off_target_and_uncertainty_blocks_are_persistent(tmp_path):
    path = tmp_path / "control.sqlite"
    request = make_request()
    journal = SqliteCommandJournal(path, session_key="session")
    reserve(journal, request)
    journal.finish(request.command_id, written(request, DawWriteOffTarget, -0.5))
    journal.close()
    journal = SqliteCommandJournal(path, session_key="session")
    assert request.profile.conversion in journal.blocked_conversions()
    with pytest.raises(JournalError):
        reserve(journal, make_request("runtime_command_0002"))
    journal.close()


def test_reserve_and_finish_fail_closed_without_overwriting_history(tmp_path, monkeypatch):
    journal = SqliteCommandJournal(tmp_path / "control.sqlite", session_key="session")
    request = make_request()
    reserve(journal, request)
    with pytest.raises(JournalError):
        reserve(journal, request)
    original = journal._commit
    monkeypatch.setattr(journal, "_commit", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("injected")))
    with pytest.raises(JournalError):
        journal.finish(request.command_id, written(request))
    assert journal.lookup(request.command_id).outcome is None
    monkeypatch.setattr(journal, "_commit", original)
    journal.finish(request.command_id, written(request))
    with pytest.raises(JournalError):
        journal.finish(request.command_id, written(request, DawWriteOffTarget, -0.5))
    journal.close()


@pytest.mark.parametrize("after_send", [False, True])
def test_process_exit_after_intent_keeps_write_pending(tmp_path, after_send):
    path = tmp_path / "control.sqlite"
    marker = tmp_path / "fake_send"
    request = make_request()
    script = """
import os,sys
from pathlib import Path
from autonomous_mixing.adapters.fl_studio.journal import SqliteCommandJournal
j=SqliteCommandJournal(Path(sys.argv[1]),session_key='session')
j.reserve(sys.argv[2],sys.argv[3],sys.argv[4])
if sys.argv[6]=='True': Path(sys.argv[5]).write_text('sent')
os._exit(17)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(path),
            request.command_id,
            request_json(request),
            json.dumps(make_apply_command(request), sort_keys=True),
            str(marker),
            str(after_send),
        ],
        capture_output=True,
    )
    assert result.returncode == 17, result.stderr
    journal = SqliteCommandJournal(path, session_key="session")
    assert journal.pending_or_uncertain()
    assert journal.lookup(request.command_id).outcome is None
    assert marker.exists() is after_send
    journal.close()


def test_corrupt_rows_unknown_schema_and_memory_database_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        SqliteCommandJournal(":memory:", session_key="session")
    path = tmp_path / "control.sqlite"
    journal = SqliteCommandJournal(path, session_key="session")
    reserve(journal, make_request())
    journal.close()
    connection = sqlite3.connect(path)
    connection.execute("UPDATE commands SET fingerprint=?", ("wrong",))
    connection.commit()
    connection.close()
    with pytest.raises(JournalError):
        SqliteCommandJournal(path, session_key="session")


def test_unknown_schema_version_is_not_reinterpreted(tmp_path):
    path = tmp_path / "future.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=99")
    connection.close()
    with pytest.raises(JournalError):
        SqliteCommandJournal(path, session_key="session")
