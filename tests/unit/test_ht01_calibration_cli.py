"""Operator calibration helper tests; only simulated transports."""

import json

import pytest
from test_ht01_calibration import Transport

from autonomous_mixing.adapters.fl_studio import calibration
from autonomous_mixing.adapters.fl_studio import protocol as p
from autonomous_mixing.drivers import ht01_calibration as cli


@pytest.fixture(autouse=True)
def clean_retention(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_HELD", [])
    monkeypatch.setattr(cli, "GUARD_PATH", tmp_path / "state" / "ht01" / "guard.json", raising=False)


def args(tmp_path):
    return [
        "run",
        "--log",
        str(tmp_path / "cal.jsonl"),
        "--run-id",
        "ht01_test_run",
        "--out",
        "cmd",
        "--in",
        "reply",
        "--authorize-plan-digest",
        p.HT01_PLAN_DIGEST,
    ]


def test_operator_helper_runs_and_evaluates_without_provider(tmp_path, monkeypatch, capsys):
    t = Transport()
    closed = []
    t.close = lambda: closed.append(True)
    monkeypatch.setattr(cli, "WinMMTransport", lambda *args: t)
    assert cli.main(args(tmp_path)) == 0
    assert closed == [True]
    assert cli.main(["evaluate", "--log", str(tmp_path / "cal.jsonl")]) == 0
    assert "H_accepted" in capsys.readouterr().out


def test_wrong_digest_and_existing_log_never_open_transport(tmp_path, monkeypatch):
    def forbidden(*args):
        raise AssertionError("opened transport")

    monkeypatch.setattr(cli, "WinMMTransport", forbidden)
    a = args(tmp_path)
    a[-1] = "0" * 64
    assert cli.main(a) == 2
    (tmp_path / "cal.jsonl").write_text("existing")
    assert cli.main(args(tmp_path)) == 2


def test_failed_close_retains_transport_and_blocks_new_run(tmp_path, monkeypatch):
    t = Transport()
    calls = []

    def close():
        calls.append("close")
        raise OSError("still owned")

    t.close = close
    monkeypatch.setattr(cli, "WinMMTransport", lambda *args: t)
    assert cli.main(args(tmp_path)) == 4
    assert cli._HELD == [t]
    sent = list(t.sent)
    a = args(tmp_path)
    a[2] = str(tmp_path / "other.jsonl")
    assert cli.main(a) == 4
    assert calls == ["close"] and t.sent == sent
    assert not (tmp_path / "other.jsonl").exists()


def test_evaluate_malformed_log_refused(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text("{broken")
    assert cli.main(["evaluate", "--log", str(path)]) == 2


# --- persistent restart guard (finding 3) ---------------------------------------------------------------------------


def opener(t):
    opened = []

    def open_transport(*args):
        opened.append(args)
        return t

    return open_transport, opened


def forbidden(*args):
    raise AssertionError("opened transport")


def new_process(monkeypatch):
    """A new operator process: the in-memory retention list starts empty; files persist."""
    monkeypatch.setattr(cli, "_HELD", [])


def guard():
    return cli.GUARD_PATH


def uncertain_at_step_2(t, c):
    t.host.fail_after_set = RuntimeError("setter may have run") if c.get("step") == 2 else None


def test_guard_records_scope_during_run_and_is_released_after_validated_completion(tmp_path, monkeypatch):
    seen = []
    t = Transport(lambda t, c: seen.append(json.loads(guard().read_text())) if not seen else None)
    monkeypatch.setattr(cli, "WinMMTransport", opener(t)[0])
    assert cli.main(args(tmp_path)) == 0
    assert seen[0]["plan_id"] == p.HT01_PLAN_ID and seen[0]["plan_digest"] == p.HT01_PLAN_DIGEST
    assert seen[0]["run_id"] == "ht01_test_run"
    assert seen[0]["log"] == str((tmp_path / "cal.jsonl").resolve())
    assert not guard().exists()
    assert guard().name == "guard.json"  # one fixed file, never derived from the digest, run ID or log


@pytest.mark.parametrize("bypass", ["run_id_and_log", "new_process", "digest"])
def test_unknown_outcome_keeps_guard_and_nothing_bypasses_it(tmp_path, monkeypatch, bypass):
    t = Transport(uncertain_at_step_2)
    monkeypatch.setattr(cli, "WinMMTransport", opener(t)[0])
    assert cli.main(args(tmp_path)) == 4
    held = guard().read_bytes()
    new_process(monkeypatch)
    monkeypatch.setattr(cli, "WinMMTransport", forbidden)
    a = args(tmp_path)
    a[2], a[4] = str(tmp_path / "other.jsonl"), "ht01_other_run"
    if bypass == "digest":
        monkeypatch.setattr(cli.protocol, "HT01_PLAN_DIGEST", "f" * 64)
        a[-1] = "f" * 64
    assert cli.main(a) == 4
    assert guard().read_bytes() == held
    assert not (tmp_path / "other.jsonl").exists()


def test_crash_during_run_keeps_guard(tmp_path, monkeypatch):
    t = Transport()
    closed = []
    t.close = lambda: closed.append(True)
    monkeypatch.setattr(cli, "WinMMTransport", opener(t)[0])

    def crash(*args, **kwargs):
        raise RuntimeError("process failure")

    monkeypatch.setattr(cli, "run_ht01", crash)
    with pytest.raises(RuntimeError):
        cli.main(args(tmp_path))
    assert closed == [True] and guard().exists()
    new_process(monkeypatch)
    monkeypatch.setattr(cli, "WinMMTransport", forbidden)
    assert cli.main(args(tmp_path)[:2] + [str(tmp_path / "x.jsonl")] + args(tmp_path)[3:]) == 4


@pytest.mark.parametrize("content", [b"", b"{broken", b'{"plan_digest": "' + b"0" * 64 + b'"}', "directory"])
def test_malformed_or_foreign_guard_blocks_before_midi(tmp_path, monkeypatch, content):
    guard().parent.mkdir(parents=True)
    if content == "directory":
        guard().mkdir()
    else:
        guard().write_bytes(content)
    monkeypatch.setattr(cli, "WinMMTransport", forbidden)
    assert cli.main(args(tmp_path)) == 4
    assert guard().is_dir() if content == "directory" else guard().read_bytes() == content
    assert not (tmp_path / "cal.jsonl").exists()


def test_cleanup_failure_after_completed_run_keeps_guard(tmp_path, monkeypatch):
    t = Transport()

    def close():
        raise OSError("still owned")

    t.close = close
    monkeypatch.setattr(cli, "WinMMTransport", opener(t)[0])
    assert cli.main(args(tmp_path)) == 4
    assert guard().exists()
    new_process(monkeypatch)
    monkeypatch.setattr(cli, "WinMMTransport", forbidden)
    a = args(tmp_path)
    a[2] = str(tmp_path / "other.jsonl")
    assert cli.main(a) == 4


def test_release_requires_valid_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "WinMMTransport", opener(Transport())[0])
    monkeypatch.setattr(calibration, "evaluate_ht01", lambda rows: {"verdict": "invalid_records", "problems": ["x"]})
    assert cli.main(args(tmp_path)) == 4
    assert guard().exists()


def test_curve_rejection_with_valid_evidence_releases(tmp_path, monkeypatch):
    t = Transport()
    original = t.host.curve
    t.host.curve = lambda v: original(v) + (0.02 if v == 0.88 else 0.0)
    monkeypatch.setattr(cli, "WinMMTransport", opener(t)[0])
    cli.main(args(tmp_path))
    assert not guard().exists()


def test_stop_before_any_pending_write_releases(tmp_path, monkeypatch):
    t = Transport()
    t.host.volumes[5] = 0.9
    monkeypatch.setattr(cli, "WinMMTransport", opener(t)[0])
    assert cli.main(args(tmp_path)) == 4
    assert not t.host.writes and not guard().exists()


def test_refusal_after_applied_writes_keeps_guard(tmp_path, monkeypatch):
    def change(t, c):
        t.host.playing = c.get("step") == 3

    monkeypatch.setattr(cli, "WinMMTransport", opener(Transport(change))[0])
    assert cli.main(args(tmp_path)) == 4
    assert guard().exists()


def test_guard_replaced_by_another_owner_is_not_removed(tmp_path, monkeypatch):
    def change(t, c):
        if c["id"].endswith("-final"):
            guard().write_text("{}")

    monkeypatch.setattr(cli, "WinMMTransport", opener(Transport(change))[0])
    assert cli.main(args(tmp_path)) == 4
    assert guard().read_text() == "{}"


def test_unavailable_guard_location_refuses_before_midi(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "GUARD_PATH", None)
    monkeypatch.setattr(cli, "WinMMTransport", forbidden)
    assert cli.main(args(tmp_path)) == 4


@pytest.mark.parametrize("failure", ["parent_is_file", "fsync"])
def test_failed_guard_creation_opens_no_midi(tmp_path, monkeypatch, failure):
    if failure == "parent_is_file":
        (tmp_path / "state").write_text("not a directory")
    else:

        def fail(fd):
            raise OSError("guard storage unavailable")

        monkeypatch.setattr(cli.os, "fsync", fail)
    monkeypatch.setattr(cli, "WinMMTransport", forbidden)
    assert cli.main(args(tmp_path)) == 4
    assert not (tmp_path / "cal.jsonl").exists()
    if failure == "fsync":
        assert guard().exists()  # a partly created guard is never treated as released


def test_unavailable_midi_after_arming_releases_without_any_log(tmp_path, monkeypatch):
    def unavailable(*args):
        raise OSError("no such port")

    monkeypatch.setattr(cli, "WinMMTransport", unavailable)
    assert cli.main(args(tmp_path)) == 4
    assert not guard().exists() and not (tmp_path / "cal.jsonl").exists()


def _lines(path):
    return path.read_bytes().splitlines(keepends=True)


DAMAGES = {
    "drop_terminal": lambda path: path.write_bytes(b"".join(_lines(path)[:-1])),
    "cut_mid_line": lambda path: path.write_bytes(path.read_bytes()[:-12]),
    "header_only": lambda path: path.write_bytes(_lines(path)[0]),
    "corrupt_json": lambda path: path.write_bytes(path.read_bytes().replace(b'"kind"', b'"kind', 1)),
    "invalid_utf8": lambda path: path.write_bytes(path.read_bytes() + b"\xff\n"),
    "append_row": lambda path: path.write_bytes(path.read_bytes() + b'{"kind": "note"}\n'),
    "drop_initial_exchange": lambda path: path.write_bytes(b"".join(_lines(path)[:1] + _lines(path)[2:])),
    "header_other_run": lambda path: path.write_bytes(path.read_bytes().replace(b"ht01_test_run", b"ht01_else_run", 1)),
    "empty": lambda path: path.write_bytes(b""),
    "deleted": lambda path: path.unlink(),
}


def _damaging_run(monkeypatch, damage):
    real = cli.run_ht01

    def run_then_damage(transport, **kwargs):
        result = real(transport, **kwargs)
        DAMAGES[damage](kwargs["path"])
        return result

    monkeypatch.setattr(cli, "run_ht01", run_then_damage)


@pytest.mark.parametrize("scenario", ["completed", "pre_write_stop"])
@pytest.mark.parametrize("damage", sorted(DAMAGES))
def test_damaged_or_shortened_log_never_releases(tmp_path, monkeypatch, scenario, damage):
    t = Transport()
    if scenario == "pre_write_stop":
        t.host.volumes[5] = 0.9
    monkeypatch.setattr(cli, "WinMMTransport", opener(t)[0])
    _damaging_run(monkeypatch, damage)
    assert cli.main(args(tmp_path)) == 4
    assert guard().exists()


@pytest.mark.parametrize("forgery", ["pending_removed", "reshaped_as_pre_write_stop"])
def test_log_with_writes_cannot_be_shortened_into_a_release(tmp_path, monkeypatch, forgery):
    """A run that dispatched writes stays blocked even if its log loses every pending/write record."""
    real = cli.run_ht01

    def run_then_shorten(transport, **kwargs):
        result = real(transport, **kwargs)
        path = kwargs["path"]
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if forgery == "pending_removed":
            kept = [r for r in rows if r["kind"] != "pending" and r.get("command", {}).get("type") != "probe_gain"]
        else:
            result_row = {
                "kind": "result",
                "result": {"status": "stopped", "reason": "initial_inspect", "restored_exact": False},
            }
            kept = [rows[0], rows[1], result_row]
        path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in kept))
        return result

    monkeypatch.setattr(cli, "run_ht01", run_then_shorten)
    monkeypatch.setattr(cli, "WinMMTransport", opener(Transport(uncertain_at_step_2))[0])
    assert cli.main(args(tmp_path)) == 4
    assert guard().exists()


def _invalid_inspect_reply(t, c):
    t.device.handle = lambda *args: p.encode({"id": c["id"]})


def _failed_inspect_send(t, c):
    raise OSError("send failed")


@pytest.mark.parametrize("stop", ["changed_start", "identity_mismatch", "no_reply", "invalid_reply", "send_failed"])
def test_valid_pre_write_stop_releases_only_after_successful_cleanup(tmp_path, monkeypatch, stop):
    for cleanup_ok in (True, False):
        t = Transport()
        if stop == "changed_start":
            t.host.volumes[5] = 0.9
        elif stop == "identity_mismatch":
            t.host.general.getProjectTitle = lambda: "Another project"
        elif stop == "invalid_reply":
            t.change = _invalid_inspect_reply
        elif stop == "send_failed":
            t.change = _failed_inspect_send
        else:
            t.receive = lambda timeout: None
        events = []

        def close(ok=cleanup_ok):
            events.append(("close", guard().exists()))
            if not ok:
                raise OSError("still owned")

        t.close = close
        new_process(monkeypatch)
        monkeypatch.setattr(cli, "WinMMTransport", opener(t)[0])
        a = args(tmp_path)
        a[2] = str(tmp_path / f"{stop}-{cleanup_ok}.jsonl")
        a += ["--timeout", "0.2"]
        assert cli.main(a) == 4
        assert not t.host.writes
        assert events == [("close", True)]  # the guard is still held while MIDI is being released
        assert guard().exists() is (not cleanup_ok)
        if not cleanup_ok:
            guard().unlink()  # test isolation only: the next iteration starts from a clean state


def _replace_pending(rows):
    rows[1] = {"kind": "pending", "command": p.ht01_probe_command("ht01_test_run-0", p.HT01_START, 0)}


PRE_WRITE_ROW_DAMAGES = {
    "inspect_row_replaced_by_pending": _replace_pending,
    "inspect_command_changed": lambda rows: rows[1]["command"].update(id="ht01_test_run-other"),
    "inspect_row_extra_field": lambda rows: rows[1].update(note="x"),
    "inspect_diagnostics_not_list": lambda rows: rows[1].update(diagnostics={}),
    "terminal_reason_changed": lambda rows: rows[2]["result"].update(reason="unknown_outcome"),
    "terminal_not_result": lambda rows: rows[2].update(kind="note"),
}


@pytest.mark.parametrize("damage", sorted(PRE_WRITE_ROW_DAMAGES))
def test_damaged_pre_write_stop_rows_never_release(tmp_path, monkeypatch, damage):
    real = cli.run_ht01

    def run_then_damage(transport, **kwargs):
        result = real(transport, **kwargs)
        path = kwargs["path"]
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        PRE_WRITE_ROW_DAMAGES[damage](rows)
        path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
        return result

    t = Transport()
    t.host.volumes[5] = 0.9
    monkeypatch.setattr(cli, "run_ht01", run_then_damage)
    monkeypatch.setattr(cli, "WinMMTransport", opener(t)[0])
    assert cli.main(args(tmp_path)) == 4
    assert guard().exists()


# --- second guard review: construction cleanup failure and malformed inspect evidence -------------------------------


def test_partial_midi_construction_cleanup_failure_retains_transport_and_guard(tmp_path, monkeypatch):
    from autonomous_mixing.adapters.fl_studio.transport import TransportCleanupError

    retained = object()

    def partial(*args):
        raise TransportCleanupError("partial construction cleanup failed", retained)

    monkeypatch.setattr(cli, "WinMMTransport", partial)
    assert cli.main(args(tmp_path)) == 4
    assert guard().exists() and cli._HELD == [retained]
    monkeypatch.setattr(cli, "WinMMTransport", forbidden)
    a = args(tmp_path)
    a[2] = str(tmp_path / "other.jsonl")
    assert cli.main(a) == 4  # same process: retained native resources
    new_process(monkeypatch)
    assert cli.main(a) == 4  # new process: the persistent guard
    assert guard().exists() and not (tmp_path / "other.jsonl").exists()


def _valid_ok_reply(rows):
    """The ok reply of a normal run's initial inspect: an exact start, which contradicts a pre-write stop."""
    t = Transport()
    path = rows[0]
    calibration.run_ht01(t, path=path, run_id="ht01_test_run", timeout_s=5.0)
    return json.loads(path.read_text().splitlines()[1])["reply"]


INSPECT_EVIDENCE_DAMAGES = {
    "unknown_status_list_reply": lambda row, ok: row.update(status="not_a_status", reply=[]),
    "replied_list_reply": lambda row, ok: row.update(reply=[]),
    "replied_null_reply": lambda row, ok: row.update(reply=None),
    "replied_other_id": lambda row, ok: row["reply"].update(id="ht01_other_run-initial"),
    "replied_malformed_reply": lambda row, ok: row["reply"].pop("error"),
    "replied_inconsistent_status": lambda row, ok: row["reply"].update(status="applied"),
    "replied_exact_start_contradicts_stop": lambda row, ok: row.update(reply=ok),
    "timeout_with_reply": lambda row, ok: row.update(status="timeout"),
    "invalid_reply_with_reply": lambda row, ok: row.update(status="invalid_reply"),
    "unknown_with_list_reply": lambda row, ok: row.update(status="unknown", reply=[]),
    "unknown_status_valid_reply": lambda row, ok: row.update(status="not_a_status"),
    # Structurally valid and for this command, but an ok reply for another project contradicts the command:
    # the exchange would have classified it invalid_reply (no reply recorded).
    "replied_ok_other_identity": lambda row, ok: row["reply"]["observed"].update(project_title="Another project"),
}


@pytest.mark.parametrize("damage", sorted(INSPECT_EVIDENCE_DAMAGES))
def test_malformed_inspect_evidence_never_releases(tmp_path, monkeypatch, damage):
    ok = _valid_ok_reply([tmp_path / "reference.jsonl"])
    real = cli.run_ht01

    def run_then_damage(transport, **kwargs):
        result = real(transport, **kwargs)
        path = kwargs["path"]
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        INSPECT_EVIDENCE_DAMAGES[damage](rows[1], ok)
        path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
        return result

    t = Transport()
    t.host.volumes[5] = 0.9  # a genuine pre-write stop, then damaged evidence
    monkeypatch.setattr(cli, "run_ht01", run_then_damage)
    monkeypatch.setattr(cli, "WinMMTransport", opener(t)[0])
    assert cli.main(args(tmp_path)) == 4
    assert guard().exists()
