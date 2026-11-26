"""Calibration runner/evaluator on the real device handler and a fake host only."""

import copy
import json

import pytest
from fl_device_support import load_device
from test_ht01_calibration_protocol import host

from autonomous_mixing.adapters.fl_studio import calibration
from autonomous_mixing.adapters.fl_studio import protocol as p


class Transport:
    def __init__(self, change=None):
        self.device, _ = load_device("v2")
        self.host, self.history, self.sent = host(), {}, []
        self.change = change

    def send(self, raw):
        command = p.decode(raw)
        self.sent.append(command)
        if self.change:
            self.change(self, command)
        self.raw = self.device.handle(raw, self.host, self.history)

    def receive(self, timeout_s):
        return self.raw

    def close(self):
        pass


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def run(tmp_path, transport=None):
    t = transport or Transport()
    path = tmp_path / "cal.jsonl"
    result = calibration.run_ht01(t, path=path, run_id="ht01_test_run", timeout_s=5.0)
    return t, path, result


def test_complete_run_restores_and_evaluates(tmp_path):
    t, path, result = run(tmp_path)
    assert result["status"] == "completed"
    assert t.host.reading(5) == p.HT01_START
    assert [c["step"] for c in t.sent if c["type"] == "probe_gain"] == [*range(36), "restore"]
    assert calibration.evaluate_ht01(records(path))["verdict"] == "H_accepted"
    assert len(t.host.writes) == 37


def test_pending_is_flushed_before_dispatch(tmp_path):
    path = tmp_path / "cal.jsonl"

    def spy(t, c):
        if c["type"] == "probe_gain":
            assert records(path)[-1] == {"kind": "pending", "command": c}

    calibration.run_ht01(Transport(spy), path=path, run_id="ht01_test_run", timeout_s=5.0)


def test_existing_log_refused_without_sending(tmp_path):
    path = tmp_path / "cal.jsonl"
    path.write_text("original")
    t = Transport()
    with pytest.raises(FileExistsError):
        calibration.run_ht01(t, path=path, run_id="ht01_test_run", timeout_s=5.0)
    assert not t.sent
    assert path.read_text() == "original"


def test_changed_initial_reading_stops_without_writes(tmp_path):
    t = Transport()
    t.host.volumes[5] = 0.90
    _, path, result = run(tmp_path, t)
    assert result["status"] == "stopped"
    assert not t.host.writes
    assert calibration.evaluate_ht01(records(path))["verdict"] != "H_accepted"


def test_known_refusal_stops_probes_and_restores(tmp_path):
    def change(t, c):
        t.host.playing = c.get("step") == 3

    t, path, result = run(tmp_path, Transport(change))
    assert result["status"] == "stopped"
    assert [c["step"] for c in t.sent if c["type"] == "probe_gain"] == [0, 1, 2, 3, "restore"]
    assert t.host.reading(5) == p.HT01_START
    assert calibration.evaluate_ht01(records(path))["verdict"] != "H_accepted"


@pytest.mark.parametrize("mode", ["send", "uncertain", "malformed"])
def test_unknown_outcome_stops_without_restore(tmp_path, mode):
    def change(t, c):
        if c.get("step") == 2:
            if mode == "send":
                raise OSError("send failed")
            if mode == "uncertain":
                t.host.fail_after_set = RuntimeError("setter may have run")
            if mode == "malformed":
                t.device.handle = lambda *args: p.encode({"id": c["id"]})

    t, path, result = run(tmp_path, Transport(change))
    assert result["status"] == "stopped"
    assert t.sent[-1]["step"] == 2
    assert not any(c.get("step") == "restore" for c in t.sent)
    assert calibration.evaluate_ht01(records(path))["verdict"] != "H_accepted"


@pytest.mark.parametrize(
    "tamper", ["order", "target", "chain", "identity", "missing_pending", "reply", "digest", "restore", "duplicate"]
)
def test_evaluator_validates_whole_record_stream(tmp_path, tamper):
    _, path, _ = run(tmp_path)
    rows = copy.deepcopy(records(path))
    pending = [i for i, r in enumerate(rows) if r["kind"] == "pending"]
    i = pending[1]
    if tamper == "order":
        rows[i : i + 2], rows[i + 2 : i + 4] = rows[i + 2 : i + 4], rows[i : i + 2]
    if tamper == "target":
        rows[i]["command"]["target_normalized"] = 0.79
    if tamper == "chain":
        rows[i]["command"]["expected"]["db"] += 0.01
    if tamper == "identity":
        rows[1]["command"]["project_title"] = "other"
    if tamper == "missing_pending":
        del rows[i]
    if tamper == "reply":
        rows[i + 1]["reply"]["observed"]["db_change"] += 0.1
    if tamper == "digest":
        rows[0]["plan_digest"] = "0" * 64
    if tamper == "restore":
        rows[pending[-1]]["command"]["target_normalized"] = 0.9
    if tamper == "duplicate":
        rows.insert(i, copy.deepcopy(rows[i]))
    result = calibration.evaluate_ht01(rows)
    assert result["verdict"] == "invalid_records"
    assert result["problems"]


def test_curve_failure_is_not_accepted(tmp_path):
    t = Transport()
    original = t.host.curve
    t.host.curve = lambda v: original(v) + (0.02 if v == 0.88 else 0.0)
    _, path, _ = run(tmp_path, t)
    assert calibration.evaluate_ht01(records(path))["verdict"] == "H_rejected"


def test_timeout_stops_without_restore(tmp_path):
    t = Transport()
    tick = [0.0]

    def clock():
        tick[0] += 1.0
        return tick[0]

    result = calibration.run_ht01(
        t, path=tmp_path / "timeout.jsonl", run_id="ht01_test_run", timeout_s=0.5, clock=clock
    )
    assert result["status"] == "stopped"
    assert not t.host.writes


def test_programming_error_propagates_after_pending_record(tmp_path):
    error = RuntimeError("unexpected")

    def change(t, c):
        if c.get("step") == 0:
            raise error

    path = tmp_path / "unexpected.jsonl"
    with pytest.raises(RuntimeError) as caught:
        calibration.run_ht01(Transport(change), path=path, run_id="ht01_test_run")
    assert caught.value is error
    assert records(path)[-2]["kind"] == "pending"
    assert records(path)[-1]["status"] == "unknown"


def test_log_failure_prevents_dispatch(tmp_path, monkeypatch):
    t = Transport()

    def fail(fd):
        raise OSError("journal unavailable")

    monkeypatch.setattr(calibration.os, "fsync", fail)
    with pytest.raises(OSError):
        run(tmp_path, t)
    assert not t.sent


@pytest.mark.parametrize("stage", ["probe", "restore", "final"])
def test_lost_reply_stops_without_retry(tmp_path, stage):
    t = Transport()
    now = [0.0]
    original = t.receive

    def receive(timeout):
        c = t.sent[-1]
        lost = (
            (stage == "probe" and c.get("step") == 2)
            or (stage == "restore" and c.get("step") == "restore")
            or (stage == "final" and c["id"].endswith("-final"))
        )
        if lost:
            now[0] += 10
            return None
        return original(timeout)

    t.receive = receive
    result = calibration.run_ht01(
        t, path=tmp_path / "lost.jsonl", run_id="ht01_test_run", timeout_s=5, clock=lambda: now[0]
    )
    assert result["status"] == "stopped"
    assert len(t.sent) == ({"probe": 4, "restore": 38, "final": 39}[stage])
    assert calibration.evaluate_ht01(records(tmp_path / "lost.jsonl"))["verdict"] != "H_accepted"


def test_final_movement_refuses_completion(tmp_path):
    def change(t, c):
        if c["id"].endswith("-final"):
            t.host.volumes[5] -= 0.001

    _, path, result = run(tmp_path, Transport(change))
    assert result["status"] == "stopped" and not result["restored_exact"]
    assert calibration.evaluate_ht01(records(path))["verdict"] == "invalid_records"


def test_repeat_failure_rejects_curve(tmp_path):
    t = Transport()
    original = t.host.curve

    def change(t, c):
        if c.get("step") == 23:
            t.host.curve = lambda v: original(v) + (0.00001 if v == 0.88 else 0.0)

    t.change = change
    _, path, _ = run(tmp_path, t)
    result = calibration.evaluate_ht01(records(path))
    assert result["verdict"] == "H_rejected"


@pytest.mark.parametrize("n", [0, 1, 2, 3, 30, 76])
def test_truncated_run_never_accepted(tmp_path, n):
    _, path, _ = run(tmp_path)
    assert calibration.evaluate_ht01(records(path)[:n])["verdict"] == "incomplete"


@pytest.mark.parametrize("replacement", [None, [], 42])
def test_malformed_reply_is_invalid_not_exception(tmp_path, replacement):
    _, path, _ = run(tmp_path)
    rows = records(path)
    rows[1]["reply"] = replacement
    assert calibration.evaluate_ht01(rows)["verdict"] == "invalid_records"


def test_other_command_reply_cannot_validate(tmp_path):
    _, path, _ = run(tmp_path)
    rows = records(path)
    rows[3]["reply"]["id"] = "different_command"
    assert calibration.evaluate_ht01(rows)["verdict"] == "invalid_records"


@pytest.mark.parametrize("failed", [False, True])
def test_run_decision_is_preserved_in_log(tmp_path, failed):
    t = Transport()
    if failed:
        t.host.volumes[5] = 0.9
    _, path, result = run(tmp_path, t)
    assert records(path)[-1] == {"kind": "result", "result": result}


@pytest.mark.parametrize("tamper", ["before", "restore"])
def test_tolerance_does_not_replace_exact_chain_or_restore(tmp_path, tamper):
    _, path, _ = run(tmp_path)
    rows = records(path)
    if tamper == "before":
        row = next(r for r in rows if r["kind"] == "exchange" and r["command"].get("step") == 1)
        observed = row["reply"]["observed"]
        observed["before"]["normalized"] += 0.00001
        observed["before"]["db"] = p.h_db(observed["before"]["normalized"])
    else:
        row = next(r for r in rows if r["kind"] == "exchange" and r["command"].get("step") == "restore")
        observed = row["reply"]["observed"]
        observed["after"]["normalized"] += 0.00000000001
    observed["db_change"] = observed["after"]["db"] - observed["before"]["db"]
    assert calibration.evaluate_ht01(rows)["verdict"] == "invalid_records"


def _deliver_twice(stage):
    """The device handles the command once before the observed delivery: the client then sees a replay."""

    def change(t, c):
        if c.get("step") == stage:
            t.device.handle(p.encode(c), t.host, t.history)

    return change


@pytest.mark.parametrize("stage", [2, "restore"])
def test_replayed_write_reply_stops_without_further_writes(tmp_path, stage):
    t, path, result = run(tmp_path, Transport(_deliver_twice(stage)))
    assert result == {"status": "stopped", "reason": "replayed_reply", "restored_exact": False}
    assert t.sent[-1]["step"] == stage  # nothing (no restore, no final inspect) follows the replay
    assert len(t.host.writes) == (3 if stage == 2 else 37)  # the one real write per step, never a second
    assert records(path)[-1] == {"kind": "result", "result": result}
    assert calibration.evaluate_ht01(records(path))["verdict"] != "H_accepted"


def test_rerun_with_same_id_after_lost_restore_is_never_reported_completed(tmp_path):
    t = Transport()
    now, lost = [0.0], [True]
    original = t.receive

    def receive(timeout):
        if lost[0] and t.sent[-1].get("step") == "restore":
            now[0] += 10
            return None
        return original(timeout)

    t.receive = receive
    first = calibration.run_ht01(
        t, path=tmp_path / "a.jsonl", run_id="ht01_test_run", timeout_s=5, clock=lambda: now[0]
    )
    assert first["reason"] == "restore_not_confirmed"
    lost[0], writes = False, len(t.host.writes)
    second = calibration.run_ht01(
        t, path=tmp_path / "b.jsonl", run_id="ht01_test_run", timeout_s=5, clock=lambda: now[0]
    )
    assert second == {"status": "stopped", "reason": "replayed_reply", "restored_exact": False}
    assert len(t.host.writes) == writes
    assert [c["step"] for c in t.sent[-1:]] == [0]


def _set_commands(rows, key, value, step=None):
    for row in rows:
        command = row.get("command")
        if command is not None and (step is None or command.get("step") == step):
            command[key] = value


TYPE_SUBSTITUTIONS = {
    "step_false": lambda rows: _set_commands(rows, "step", False, step=0),
    "step_true": lambda rows: _set_commands(rows, "step", True, step=1),
    "version_true": lambda rows: _set_commands(rows, "v", True),
    "index_float": lambda rows: [r["command"]["insert"].update(index=5.0) for r in rows if "command" in r],
    "restored_exact_int": lambda rows: rows[-1]["result"].update(restored_exact=1),
    "diagnostics_dict": lambda rows: rows[1].update(diagnostics={"anything": "goes"}),
    "diagnostics_non_string": lambda rows: rows[1].update(diagnostics=[1]),
    "diagnostics_null": lambda rows: rows[1].update(diagnostics=None),
}


@pytest.mark.parametrize("name", sorted(TYPE_SUBSTITUTIONS))
def test_evaluator_compares_records_type_strictly(tmp_path, name):
    _, path, _ = run(tmp_path)
    rows = records(path)
    assert calibration.evaluate_ht01(copy.deepcopy(rows))["verdict"] == "H_accepted"
    TYPE_SUBSTITUTIONS[name](rows)
    result = calibration.evaluate_ht01(rows)
    assert result["verdict"] == "invalid_records"
    assert result["problems"]
