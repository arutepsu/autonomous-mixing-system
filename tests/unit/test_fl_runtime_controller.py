"""Tests the actual runtime controller/exchange/protocol/journal with a controlled byte transport."""

import json
import sqlite3
from dataclasses import replace
from itertools import count

import pytest
from fl_runtime_support import make_request

from autonomous_mixing.adapters.fl_studio import protocol
from autonomous_mixing.adapters.fl_studio.controller import FlStudioParameterController
from autonomous_mixing.adapters.fl_studio.journal import SqliteCommandJournal
from autonomous_mixing.core.records.daw_control import (
    DawRefusalCode,
    DawUncertainReason,
    DawWriteConfirmed,
    DawWriteOffTarget,
    DawWriteRefused,
    DawWriteUncertain,
)
from autonomous_mixing.core.records.identifiers import ExecutionId
from autonomous_mixing.core.records.results import PortFailure, Success


class WireTransport:
    def __init__(self, factory):
        self.factory, self.command = factory, None

    def send(self, raw):
        self.command = protocol.decode(raw)
        self.factory.sent.append(self.command)
        if self.command["type"] == "set_gain_db":
            assert self.factory.journal.lookup(self.command["id"]).outcome is None
        if self.factory.mode == "send_failure":
            raise OSError("send interrupted")

    def receive(self, remaining):
        factory, cmd = self.factory, self.command
        factory.now += remaining if factory.mode == "timeout" else 0.001
        if factory.mode == "timeout":
            return None
        if factory.mode == "receive_failure":
            raise OSError("receive interrupted")
        observed = dict(
            api=45,
            fl_version="Producer Edition v26.1.7 [build 5654]",
            project_title=cmd["project_title"],
            insert=cmd["insert"],
        )
        if cmd["type"] == "inspect":
            observed["gain"] = dict(normalized=0.76, db=-1.1527416706085205)
            if factory.mode == "identity":
                observed.update(project_title="Wrong", gain=None)
                return protocol.encode(protocol.reply(cmd["id"], "identity_mismatch", observed, "project_title"))
            if factory.mode == "wrong_build":
                observed["fl_version"] = "Different build"
            if factory.mode == "invalid":
                observed["gain"]["normalized"] = 2
            return protocol.encode(protocol.reply(cmd["id"], "ok", observed))
        if factory.mode in ("unsupported", "api_too_old", "duplicate_wire", "conversion_wire"):
            status, error = {
                "unsupported": ("unsupported", "unsupported"),
                "api_too_old": ("error", "api_too_old"),
                "duplicate_wire": ("invalid_command", "duplicate_id_conflict"),
                "conversion_wire": ("invalid_command", "conversion"),
            }[factory.mode]
            return protocol.encode(protocol.reply(cmd["id"], status, error=error))
        if factory.mode == "write_identity":
            observed.update(project_title="Wrong", before=None, after=None, db_change=None, replayed=False)
            return protocol.encode(protocol.reply(cmd["id"], "identity_mismatch", observed, "project_title"))
        before = cmd["expected"]
        target = cmd["target_normalized"]
        change = cmd["intended_db_change"] + (0.1 if factory.mode == "off_target" else 0)
        if factory.mode == "strict_boundary":
            change += 0.0500000001
        after = dict(normalized=target, db=before["db"] + change)
        observed.update(before=before, after=after, db_change=after["db"] - before["db"], replayed=False)
        status, error = "applied", None
        if factory.mode == "off_target":
            status, error = "applied_off_target", "db_off_target"
        elif factory.mode == "uncertain":
            status, error = "uncertain", "readback_disagrees"
        elif factory.mode.startswith("refuse:"):
            status, error = "precondition_failed", factory.mode.split(":")[1]
            observed.update(after=None, db_change=None)
        elif factory.mode == "invalid":
            observed["after"]["normalized"] = 2
        elif factory.mode == "wrong_build":
            observed["fl_version"] = "Different build"
        elif factory.mode == "unknown_error":
            return protocol.encode(protocol.reply(cmd["id"], "error", error="unexpected_failure"))
        elif factory.mode == "wire_difference":
            observed["db_change"] += 1e-10  # valid wire tolerance, core requires exact difference
        return protocol.encode(protocol.reply(cmd["id"], status, observed, error))

    def close(self):
        self.factory.closed += 1
        if self.factory.mode == "close_failure":
            raise OSError("driver retained buffer")


class Factory:
    def __init__(self, journal, mode="ok"):
        self.journal, self.mode = journal, mode
        self.sent, self.instances = [], []
        self.now, self.closed = 0.0, 0

    def __call__(self):
        if self.mode == "open_failure":
            raise OSError("no ports")
        instance = WireTransport(self)
        self.instances.append(instance)
        return instance

    def clock(self):
        return self.now


def controller(journal, factory):
    identifiers = count(1)
    return FlStudioParameterController(
        profile=make_request().profile,
        journal=journal,
        transport_factory=factory,
        timeout_s=1.0,
        clock=factory.clock,
        next_inspect_id=lambda: f"runtime_inspect_{next(identifiers):04d}",
    )


@pytest.fixture
def setup(tmp_path):
    journal = SqliteCommandJournal(tmp_path / "control.sqlite", session_key="test-session")
    factory = Factory(journal)
    result = controller(journal, factory), journal, factory
    yield result
    journal.close()


def test_inspect_and_actual_apply_are_distinct_from_audio_acceptance(setup):
    ctrl, journal, factory = setup
    request = make_request()
    result = ctrl.inspect(request.inspection.request)
    assert isinstance(result, Success) and result.value.matches_binding
    assert journal.lookup(request.command_id) is None
    outcome = ctrl.apply(request)
    assert isinstance(outcome, DawWriteConfirmed)
    assert outcome.achieved_change_db == outcome.after.db - outcome.before.db
    assert factory.sent[-1]["target_normalized"] == outcome.commanded_normalized
    assert journal.lookup(request.command_id).outcome == outcome
    assert factory.closed == 2


@pytest.mark.parametrize(
    "mode,cls",
    [
        ("ok", DawWriteConfirmed),
        ("off_target", DawWriteOffTarget),
        ("uncertain", DawWriteUncertain),
        ("refuse:start_gain", DawWriteRefused),
    ],
)
def test_replay_never_opens_another_transport(setup, mode, cls):
    ctrl, journal, factory = setup
    factory.mode = mode
    request = make_request()
    first = ctrl.apply(request)
    assert isinstance(first, cls)
    second = ctrl.apply(request)
    assert len(factory.sent) == 1 and len(factory.instances) == 1
    if isinstance(first, (DawWriteConfirmed, DawWriteOffTarget)):
        assert second == replace(first, replayed=True)
    else:
        assert second == first


def test_replay_after_restart_and_device_reload_is_local(setup, tmp_path):
    ctrl, journal, factory = setup
    request = make_request()
    first = ctrl.apply(request)
    journal.close()
    reopened = SqliteCommandJournal(tmp_path / "control.sqlite", session_key="test-session")
    try:
        factory = Factory(reopened, "open_failure")
        assert controller(reopened, factory).apply(request) == replace(first, replayed=True)
        assert not factory.instances
    finally:
        reopened.close()


def test_full_request_conflict_does_not_overwrite_original(setup):
    ctrl, journal, factory = setup
    request = make_request()
    first = ctrl.apply(request)
    conflict = ctrl.apply(replace(request, execution_id=ExecutionId("other")))
    assert conflict.code is DawRefusalCode.DUPLICATE_ID_CONFLICT
    assert journal.lookup(request.command_id).outcome == first
    assert len(factory.sent) == 1


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("timeout", DawUncertainReason.TIMEOUT),
        ("invalid", DawUncertainReason.INVALID_REPLY),
        ("wrong_build", DawUncertainReason.INVALID_REPLY),
        ("strict_boundary", DawUncertainReason.INVALID_REPLY),
        ("send_failure", DawUncertainReason.DISPATCH_INTERRUPTED),
        ("receive_failure", DawUncertainReason.DISPATCH_INTERRUPTED),
        ("unknown_error", DawUncertainReason.INVALID_REPLY),
    ],
)
def test_unknown_after_intent_is_uncertain_and_inspect_cannot_clear_it(setup, mode, reason):
    ctrl, journal, factory = setup
    factory.mode = mode
    outcome = ctrl.apply(make_request())
    assert isinstance(outcome, DawWriteUncertain) and outcome.reason is reason
    factory.mode = "ok"
    assert isinstance(ctrl.inspect(make_request().inspection.request), Success)
    assert journal.pending_or_uncertain()
    refused = ctrl.apply(make_request("runtime_command_0002"))
    assert isinstance(refused, DawWriteRefused)
    assert len([cmd for cmd in factory.sent if cmd["type"] == "set_gain_db"]) == 1


def test_wire_approximately_equal_arithmetic_is_normalized_to_exact_readings(setup):
    ctrl, _, factory = setup
    factory.mode = "wire_difference"
    result = ctrl.apply(make_request())
    assert isinstance(result, DawWriteConfirmed)
    assert result.achieved_change_db == result.after.db - result.before.db


@pytest.mark.parametrize(
    "wire,code",
    [
        ("transport_playing", DawRefusalCode.TRANSPORT_PLAYING),
        ("edit_unavailable", DawRefusalCode.EDIT_UNAVAILABLE),
        ("start_gain", DawRefusalCode.START_GAIN),
        ("range", DawRefusalCode.OUT_OF_RANGE),
        ("uncalibrated_build", DawRefusalCode.UNCALIBRATED_BUILD),
        ("uncalibrated_start", DawRefusalCode.UNCALIBRATED_START),
        ("cut_bounds", DawRefusalCode.BOUNDS),
    ],
)
def test_known_prewrite_refusal_mapping(setup, wire, code):
    ctrl, _, factory = setup
    factory.mode = "refuse:" + wire
    assert ctrl.apply(make_request()).code is code


def test_off_target_block_survives_inspect(setup):
    ctrl, journal, factory = setup
    factory.mode = "off_target"
    assert isinstance(ctrl.apply(make_request()), DawWriteOffTarget)
    factory.mode = "ok"
    ctrl.inspect(make_request().inspection.request)
    assert make_request().profile.conversion in journal.blocked_conversions()
    assert ctrl.apply(make_request("runtime_command_0002")).code is DawRefusalCode.CONVERSION


@pytest.mark.parametrize("when", ["reserve", "finish"])
def test_commit_failure_never_returns_confirmation(setup, monkeypatch, when):
    ctrl, journal, factory = setup
    original = journal._commit
    calls = []

    def commit():
        calls.append(1)
        if len(calls) == (1 if when == "reserve" else 2):
            raise sqlite3.OperationalError("injected commit failure")
        original()

    monkeypatch.setattr(journal, "_commit", commit)
    result = ctrl.apply(make_request())
    assert isinstance(result, DawWriteUncertain)
    assert len(factory.sent) == (0 if when == "reserve" else 1)
    if when == "finish":
        assert journal.lookup(make_request().command_id).outcome is None
    assert ctrl.apply(make_request()) == result  # unpersisted fallback retained in memory


def test_close_failure_keeps_committed_evidence_but_poisons_dispatch(setup):
    ctrl, journal, factory = setup
    factory.mode = "close_failure"
    result = ctrl.apply(make_request())
    assert isinstance(result, DawWriteConfirmed)
    assert journal.lookup(result.command_id).outcome == result
    assert ctrl.apply(make_request("runtime_command_0002")).code is DawRefusalCode.UNAVAILABLE
    assert len(factory.sent) == 1


def test_pending_restart_is_not_resent(setup):
    ctrl, journal, factory = setup
    request = make_request()
    from autonomous_mixing.adapters.fl_studio.profile import make_apply_command
    from autonomous_mixing.adapters.fl_studio.serialization import request_json

    journal.reserve(request.command_id, request_json(request), json.dumps(make_apply_command(request), sort_keys=True))
    outcome = ctrl.apply(request)
    assert outcome.reason is DawUncertainReason.PENDING_UNRESOLVED
    assert not factory.sent


@pytest.mark.parametrize("mode", ["open_failure", "timeout", "invalid"])
def test_read_failure_is_a_port_failure(setup, mode):
    ctrl, _, factory = setup
    factory.mode = mode
    assert isinstance(ctrl.inspect(make_request().inspection.request), PortFailure)


def test_inspection_reports_identity_and_build_mismatches(setup):
    ctrl, _, factory = setup
    factory.mode = "identity"
    result = ctrl.inspect(make_request().inspection.request)
    assert isinstance(result, Success) and not result.value.matches_binding and result.value.fader is None
    factory.mode = "wrong_build"
    result = ctrl.inspect(make_request().inspection.request)
    assert isinstance(result, Success) and result.value.build != make_request().profile.build


@pytest.mark.parametrize(
    "mode,code",
    [
        ("unsupported", DawRefusalCode.UNSUPPORTED),
        ("api_too_old", DawRefusalCode.API_TOO_OLD),
        ("duplicate_wire", DawRefusalCode.DUPLICATE_ID_CONFLICT),
        ("conversion_wire", DawRefusalCode.CONVERSION),
        ("write_identity", DawRefusalCode.IDENTITY_MISMATCH),
        ("open_failure", DawRefusalCode.UNAVAILABLE),
    ],
)
def test_other_known_prewrite_paths_are_refused_and_replayed(setup, mode, code):
    ctrl, journal, factory = setup
    factory.mode = mode
    outcome = ctrl.apply(make_request())
    assert isinstance(outcome, DawWriteRefused) and outcome.code is code
    attempts = len(factory.instances)
    assert ctrl.apply(make_request()) == outcome
    assert len(factory.instances) == attempts


def test_ambiguous_finish_commit_recovers_only_committed_evidence(setup, tmp_path, monkeypatch):
    ctrl, journal, factory = setup
    original = journal._commit
    calls = []

    def ambiguous_commit():
        calls.append(1)
        original()
        if len(calls) == 2:
            raise sqlite3.OperationalError("commit completed but acknowledgement failed")

    monkeypatch.setattr(journal, "_commit", ambiguous_commit)
    outcome = ctrl.apply(make_request())
    assert isinstance(outcome, DawWriteUncertain)
    assert isinstance(journal.lookup(outcome.command_id).outcome, DawWriteConfirmed)
    journal.close()
    reopened = SqliteCommandJournal(tmp_path / "control.sqlite", session_key="test-session")
    try:
        factory = Factory(reopened, "open_failure")
        recovered = controller(reopened, factory).apply(make_request())
        assert isinstance(recovered, DawWriteConfirmed) and recovered.replayed
        assert not factory.sent
    finally:
        reopened.close()


def test_concurrent_requests_cannot_dispatch_through_an_unresolved_intent(setup):
    from concurrent.futures import ThreadPoolExecutor

    ctrl, journal, factory = setup
    factory.mode = "timeout"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(ctrl.apply, [make_request(), make_request("runtime_command_0002")]))
    assert sum(isinstance(result, DawWriteUncertain) for result in results) == 1
    assert sum(isinstance(result, DawWriteRefused) for result in results) == 1
    assert len(factory.sent) == 1


def test_partial_native_construction_cleanup_failure_poisons_new_dispatch(setup):
    from autonomous_mixing.adapters.fl_studio.transport import TransportCleanupError

    ctrl, journal, factory = setup
    retained = object()

    def incomplete_open():
        raise TransportCleanupError("partial open and cleanup failed", retained)

    ctrl.transport_factory = incomplete_open
    result = ctrl.apply(make_request())
    assert result.code is DawRefusalCode.UNAVAILABLE
    ctrl.transport_factory = factory
    assert ctrl.apply(make_request("runtime_command_0002")).code is DawRefusalCode.UNAVAILABLE
    assert retained in ctrl._retained_transports
    assert not factory.sent


def test_inspect_id_cannot_be_reused_for_apply(setup):
    ctrl, _, factory = setup
    assert isinstance(ctrl.inspect(make_request().inspection.request), Success)
    result = ctrl.apply(make_request("runtime_inspect_0001"))
    assert isinstance(result, DawWriteRefused) and result.code is DawRefusalCode.DUPLICATE_ID_CONFLICT
    assert len(factory.sent) == 1 and factory.sent[0]["type"] == "inspect"


def test_repeated_inspect_id_is_refused_without_reopening_transport(setup):
    ctrl, _, factory = setup
    ctrl.next_inspect_id = lambda: "fixed_inspect_0001"
    assert isinstance(ctrl.inspect(make_request().inspection.request), Success)
    assert isinstance(ctrl.inspect(make_request().inspection.request), PortFailure)
    assert len(factory.sent) == 1


def test_inspect_ids_remain_reserved_after_reopening_journal(setup, tmp_path):
    ctrl, journal, factory = setup
    assert isinstance(ctrl.inspect(make_request().inspection.request), Success)
    journal.close()
    reopened = SqliteCommandJournal(tmp_path / "control.sqlite", session_key="test-session")
    try:
        factory = Factory(reopened)
        second = controller(reopened, factory)
        assert isinstance(second.inspect(make_request().inspection.request), PortFailure)
        assert second.apply(make_request("runtime_inspect_0001")).code is DawRefusalCode.DUPLICATE_ID_CONFLICT
        assert not factory.sent
    finally:
        reopened.close()


@pytest.mark.parametrize("case", ["cleanup", "reused_id"])
def test_permanent_inspect_failures_are_not_retryable(setup, case):
    ctrl, journal, factory = setup
    if case == "cleanup":
        from autonomous_mixing.adapters.fl_studio.transport import TransportCleanupError

        def incomplete():
            raise TransportCleanupError("unreleased handles", object())

        ctrl.transport_factory = incomplete
    else:
        ctrl.next_inspect_id = lambda: "permanent_inspect_01"
        assert isinstance(ctrl.inspect(make_request().inspection.request), Success)
    result = ctrl.inspect(make_request().inspection.request)
    assert isinstance(result, PortFailure) and result.retryable is False
