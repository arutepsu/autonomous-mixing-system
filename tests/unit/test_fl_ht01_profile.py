"""Calibrated HT01 production profile (plan 2026-11-26): protocol, device v2, profile, admission, controller, journal.

Bytes go through the real runtime protocol, exchange, journal and controller to device script v2 on a fake host
(tests/unit/fl_device_support.py). Simulated evidence only; it never touches MIDI or a live FL Studio session.
"""

import math
from dataclasses import fields, replace
from itertools import count

import pytest
from fl_device_support import FakeHost, h_db, load_device
from fl_recovery_support import CONTROLLER, grant
from fl_runtime_support import make_request

from autonomous_mixing.adapters.fl_studio import profile as profiles
from autonomous_mixing.adapters.fl_studio import protocol as p
from autonomous_mixing.adapters.fl_studio.controller import FlStudioParameterController
from autonomous_mixing.adapters.fl_studio.journal import JournalError, SqliteCommandJournal
from autonomous_mixing.core.admission.daw import gate_daw_apply, gate_daw_restore
from autonomous_mixing.core.records.actions import Action, SetTrackGain
from autonomous_mixing.core.records.admission import AdmittedAction
from autonomous_mixing.core.records.daw import DawTrackState
from autonomous_mixing.core.records.daw_binding import DawFaderReading, DawTrackBinding
from autonomous_mixing.core.records.daw_control import (
    DawApplyPermitted,
    DawBuild,
    DawInspection,
    DawInspectRequest,
    DawRange,
    DawRefusalCode,
    DawUncertainReason,
    DawWriteConfirmed,
    DawWriteOffTarget,
    DawWriteRefused,
    DawWriteUncertain,
)
from autonomous_mixing.core.records.daw_recovery import (
    DawRecoveryAuthorization,
    DawRestoreConfirmed,
    DawRestorePermitted,
)
from autonomous_mixing.core.records.identifiers import ActionId, ExecutionId, ProjectId, Revision, TrackId, VersionRef
from autonomous_mixing.core.records.results import Success

TITLE = "HT01 Live Calibration v001"
BUILD = "Producer Edition v26.1.7 [build 5654]"
REFERENCE = VersionRef("fl-control-profile", "1")
LOW, HIGH = 0.78, 0.92
BELOW, ABOVE = math.nextafter(LOW, 0.0), math.nextafter(HIGH, 1.0)  # the adjacent floats just outside the range


def reading(v):
    return {"normalized": v, "db": h_db(v)}


def ht01_profile(reference=REFERENCE):
    return profiles.ht01_calibrated_profile(reference=reference)


def ht01_host(start=0.88, **kwargs):
    host = FakeHost(title=TITLE, names=("Kick", "Snare", "Hat", "Pad", "Bass"), **kwargs)
    host.volumes[5] = start
    return host


def db_command(start, change, command_id="ht01_apply_0001", title=TITLE, index=5, name="Bass"):
    return p.set_gain_db_command(command_id, title, index, name, start, h_db(start), change)


def restore_command(current, target, command_id="ht01_restore_001", title=TITLE, index=5, name="Bass"):
    return p.restore_gain_command(command_id, title, index, name, current, h_db(current), target, h_db(target))


def refusal(build):
    with pytest.raises(p.ProtocolError) as caught:
        build()
    return caught.value.code


# --- 1. protocol: a separate conversion, existing scope unchanged ---------------------------------------------------


def test_ht01_conversion_is_separate_and_the_existing_scope_is_unchanged():
    assert p.HT01_CONVERSION_ID == "fl-mixer-h125-ht01-v1" != p.CONVERSION_ID == "fl-mixer-h125-v1"
    assert p.CONVERSION_RANGE == (0.62, 0.86) and p.HT01_CONVERSION_RANGE == (LOW, HIGH)
    assert p.CONVERSION_SCOPE == {"project_title": "Thesis Bass Bridge Test", "insert": {"index": 2, "name": "Bass"},
                                  "fl_version": BUILD}  # fmt: skip
    assert p.HT01_CONVERSION_SCOPE == {"project_title": TITLE, "insert": {"index": 5, "name": "Bass"},
                                       "fl_version": BUILD, "api": 45}  # fmt: skip
    assert db_command(0.88, -1.0)["conversion"] == p.HT01_CONVERSION_ID
    existing = p.set_gain_db_command("existing_apply_01", "Thesis Bass Bridge Test", 2, "Bass", 0.76, h_db(0.76), -1.0)
    assert existing["conversion"] == p.CONVERSION_ID


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda c: c.update(conversion=p.CONVERSION_ID), "conversion_scope"),
        (lambda c: c.update(conversion="fl-mixer-h125-v2"), "conversion"),
        (lambda c: c.update(project_title="Thesis Bass Bridge Test"), "conversion_scope"),
        (lambda c: c.update(insert={"index": 2, "name": "Bass"}), "conversion_scope"),
        (lambda c: c.update(insert={"index": 5, "name": "Kick"}), "conversion_scope"),
        (lambda c: c.update(project_title="HT01 Live Calibration v002"), "conversion_scope"),
    ],
)
def test_scope_and_conversion_mismatches_are_invalid(mutate, code):
    command = db_command(0.88, -1.0)
    mutate(command)
    assert refusal(lambda: p.validate_command(command)) == code


def test_existing_scope_cannot_use_the_ht01_conversion():
    command = p.set_gain_db_command("existing_apply_01", "Thesis Bass Bridge Test", 2, "Bass", 0.76, h_db(0.76), -1.0)
    command["conversion"] = p.HT01_CONVERSION_ID
    assert refusal(lambda: p.validate_command(command)) == "conversion_scope"


@pytest.mark.parametrize("title,index,name", [(TITLE, 4, "Bass"), (TITLE, 5, "Kick"), ("Other", 5, "Bass")])
def test_restore_outside_both_scopes_is_unsupported(title, index, name):
    assert refusal(lambda: restore_command(0.85, 0.88, title=title, index=index, name=name)) == "conversion_scope"


# --- 2. range eligibility versus an admissible cut ------------------------------------------------------------------


def test_start_at_the_lower_endpoint_is_eligible_but_admits_no_cut():
    assert p.conversion_admits(p.HT01_CONVERSION_ID, LOW)
    for change in (-0.1, -1.0, -3.0):
        assert refusal(lambda change=change: db_command(LOW, change)) == "range"


def test_endpoints_are_accepted_through_valid_operations():
    db_command(HIGH, -0.1)  # a start at the upper endpoint
    db_command(HIGH, -3.0)
    change = h_db(LOW) - h_db(0.86)  # a cut landing on the lower endpoint
    while p.db_target(h_db(0.86), change) < LOW:
        change = math.nextafter(change, 0.0)
    command = db_command(0.86, change)
    assert LOW <= command["target_normalized"] <= LOW + 1e-12
    restore_command(0.85, LOW)
    restore_command(0.85, HIGH)


@pytest.mark.parametrize("value", [BELOW, ABOVE, 0.76, 0.94])
def test_adjacent_floats_and_staging_points_are_outside_the_ht01_range(value):
    assert not p.conversion_admits(p.HT01_CONVERSION_ID, value)
    assert refusal(lambda: db_command(value, -0.1)) in ("range", "uncalibrated_start")
    assert refusal(lambda: restore_command(0.85, value)) == "range"


def test_ht01_bounds_are_exact_while_the_existing_rounding_stays():
    assert p.in_conversion_range(math.nextafter(0.86, 1.0))  # existing scope: unchanged 1e-9 rounding
    assert p.conversion_admits(p.CONVERSION_ID, math.nextafter(0.86, 1.0))
    assert not p.conversion_admits(p.HT01_CONVERSION_ID, ABOVE)
    assert not p.conversion_admits(p.HT01_CONVERSION_ID, BELOW)


def test_ranges_of_the_two_scopes_do_not_mix():
    assert refusal(lambda: db_command(0.70, -0.1)) == "range"  # existing-range value in HT01
    assert (
        refusal(
            lambda: p.set_gain_db_command(
                "existing_apply_01", "Thesis Bass Bridge Test", 2, "Bass", 0.90, h_db(0.90), -0.1
            )
        )
        == "range"
    )  # HT01-range value in the existing scope


# --- 3. conversion agreement and cut limits -------------------------------------------------------------------------


def test_conversion_agreement_curve_and_cut_limits():
    command = db_command(0.88, -1.0)
    command["target_normalized"] += 1e-8
    assert refusal(lambda: p.validate_command(command)) == "conversion_mismatch"
    off_curve = db_command(0.88, -1.0)
    off_curve["expected"]["db"] += 0.02
    off_curve["target_normalized"] = p.db_target(off_curve["expected"]["db"], -1.0)
    assert refusal(lambda: p.validate_command(off_curve)) == "uncalibrated_start"
    for change in (-0.1, -3.0):
        db_command(HIGH, change)
    for change in (-0.09, -3.01, 0.5):
        assert refusal(lambda change=change: db_command(HIGH, change)) == "bounds"


# --- 4. device v2 on a fake host: build, API and its own start-range check ------------------------------------------


def device_answer(host, command, history=None):
    device, device_protocol = load_device("v2")
    return p.decode(device.handle(device_protocol.encode(command), host, {} if history is None else history))


@pytest.mark.parametrize("kwargs,error", [({"build": "Producer Edition v26.1.8 [build 5700]"}, "uncalibrated_build"),
                                         ({"api": 44}, "calibration_api"), ({"api": 46}, "calibration_api")])  # fmt: skip
@pytest.mark.parametrize("kind", ["apply", "restore"])
def test_device_refuses_ht01_writes_on_another_build_or_api(kwargs, error, kind):
    host = ht01_host(0.88, **kwargs)
    command = db_command(0.88, -1.0) if kind == "apply" else restore_command(0.88, 0.90)
    answer = device_answer(host, command)
    assert (answer["status"], answer["error"]) == ("precondition_failed", error)
    assert host.writes == []


def test_existing_scope_keeps_its_device_checks_without_an_api_pin():
    host = FakeHost(normalized=0.76, api=44)
    command = p.set_gain_db_command("existing_apply_01", "Thesis Bass Bridge Test", 2, "Bass", 0.76, h_db(0.76), -1.0)
    assert device_answer(host, command)["status"] == "applied"


@pytest.mark.parametrize("actual,status", [(ABOVE, "precondition_failed"), (HIGH, "applied")])
def test_device_checks_the_actual_start_against_the_exact_range(actual, status):
    host = ht01_host(actual)  # within the start tolerance of the expected endpoint, but possibly just outside
    answer = device_answer(host, db_command(HIGH, -0.5))
    assert answer["status"] == status
    if status == "precondition_failed":
        assert answer["error"] == "range" and host.writes == []


def test_device_applies_an_ht01_cut_and_restores_exactly():
    host, history = ht01_host(0.88), {}
    applied = device_answer(host, db_command(0.88, -1.0), history)
    assert applied["status"] == "applied" and len(host.writes) == 1
    restored = device_answer(host, restore_command(host.volumes[5], 0.88), history)
    assert restored["status"] == "applied" and host.reading(5) == reading(0.88)


def test_calibration_probe_history_does_not_interact_with_production_writes():
    host, history = ht01_host(0.88), {}
    assert device_answer(host, db_command(0.88, -1.0), history)["status"] == "applied"
    assert not any(isinstance(key, tuple) for key in history)  # no calibration progress or start keys


# --- 5. profile selection --------------------------------------------------------------------------------------------


def test_exactly_two_named_profiles_are_supported():
    assert profiles.PROFILE_NAMES == ("bass-bridge-test-v1", "ht01-live-calibration-v001")
    assert profiles.supported_profile("bass-bridge-test-v1", reference=REFERENCE) == profiles.calibrated_profile(
        reference=REFERENCE
    )
    assert profiles.supported_profile("ht01-live-calibration-v001", reference=REFERENCE) == ht01_profile()
    for name in ("ht01", "HT01-live-calibration-v001", "bass-bridge-test-v2", ""):
        with pytest.raises(ValueError):
            profiles.supported_profile(name, reference=REFERENCE)


def test_ht01_profile_declares_exactly_the_calibrated_scope():
    profile = ht01_profile()
    assert profile.build == DawBuild(BUILD, 45)
    assert (profile.project_title, profile.insert_index, profile.insert_name) == (TITLE, 5, "Bass")
    assert profile.conversion == VersionRef("fl-mixer-h125-ht01", "v1")
    assert profile.normalized_range == DawRange(LOW, HIGH)
    assert profile.fader_db_range == DawRange(h_db(LOW), h_db(HIGH))
    assert (profile.minimum_cut_db, profile.maximum_cut_db) == (0.1, 3.0)
    existing = profiles.calibrated_profile(reference=REFERENCE)
    for name in ("start_normalized_tolerance", "start_db_tolerance", "success_db_tolerance",
                 "readback_normalized_tolerance"):  # fmt: skip
        assert getattr(profile, name) == getattr(existing, name)


ALTERED = {
    "build": DawBuild(BUILD, 46),
    "project_title": "HT01 Live Calibration v002",
    "insert_index": 4,
    "insert_name": "Kick",
    "conversion": VersionRef("fl-mixer-h125", "v1"),
    "normalized_range": DawRange(0.76, 0.94),
    "fader_db_range": DawRange(h_db(0.76), h_db(0.94)),
    "minimum_cut_db": 0.05,
    "maximum_cut_db": 6.0,
    "start_normalized_tolerance": 0.01,
    "start_db_tolerance": 0.5,
    "success_db_tolerance": 0.5,
    "readback_normalized_tolerance": 0.01,
}


@pytest.mark.parametrize("name", sorted(ALTERED))
def test_an_altered_ht01_profile_is_not_supported(name):
    assert {f.name for f in fields(ht01_profile())} - {"reference"} == set(ALTERED)
    altered = replace(ht01_profile(), **{name: ALTERED[name]})
    assert not profiles.is_supported_profile(altered)
    assert profiles.is_supported_profile(ht01_profile(VersionRef("any-label", "7")))


# --- 6. core admission with the HT01 profile -------------------------------------------------------------------------


def gate(start_v, change, command_id="ht01_apply_0001", profile=None):
    profile = profile or ht01_profile()
    start = DawFaderReading(start_v, h_db(start_v))
    binding = DawTrackBinding(VersionRef("binding", "1"), ProjectId("p1"), TrackId("t1"), TITLE, 5, "Bass", 2.0, start)
    state = DawTrackState(binding, Revision("r1"), 2.0, start)
    inspection = DawInspection(DawInspectRequest(binding, profile), profile.build, TITLE, 5, "Bass", start)
    admitted = AdmittedAction(
        Action(ActionId("a1"), binding.project_id, state.revision, SetTrackGain(binding.track_id, 2.0 + change)),
        VersionRef("policy", "1"),
    )
    return gate_daw_apply(
        admitted, state=state, inspection=inspection, profile=profile, execution_id=ExecutionId("e1"),
        command_id=command_id, blocked_conversions=frozenset(), unresolved_command_id=None,
    )  # fmt: skip


def test_admission_refuses_every_cut_from_the_lower_endpoint():
    for change in (-0.1, -1.0, -3.0):
        assert not isinstance(gate(LOW, change), DawApplyPermitted)


@pytest.mark.parametrize("start", [BELOW, ABOVE, 0.76, 0.94])
def test_admission_refuses_starts_outside_the_exact_range(start):
    assert not isinstance(gate(start, -0.5), DawApplyPermitted)


def test_admission_permits_valid_operations_at_the_endpoints():
    assert isinstance(gate(HIGH, -0.1), DawApplyPermitted)
    assert isinstance(gate(HIGH, -3.0), DawApplyPermitted)
    change = h_db(LOW) - h_db(0.86)
    while p.db_target(h_db(0.86), change) < LOW:
        change = math.nextafter(change, 0.0)
    assert isinstance(gate(0.86, change), DawApplyPermitted)


def test_apply_command_refuses_a_widened_profile():
    request = gate(0.88, -1.0).request
    p_widened = replace(request.profile, normalized_range=DawRange(0.76, 0.94))
    with pytest.raises(ValueError):
        profiles.make_apply_command(replace(request, profile=p_widened))


# --- 7. controller end to end through device v2 ----------------------------------------------------------------------


class DeviceTransport:
    def __init__(self, world):
        self.world, self.reply = world, None

    def send(self, raw):
        world = self.world
        message = p.decode(raw)
        world.sent.append(message)
        if message["type"] in p.WRITE_TYPES:
            assert world.journal.lookup(message["id"]).outcome is None  # intent committed before the send
        answer = world.device.handle(world.device_protocol.encode(message), world.host, world.history)
        self.reply = None if world.lose_next_reply else answer
        world.lose_next_reply = False

    def receive(self, remaining):
        if self.reply is None:
            self.world.now += remaining
            return None
        reply, self.reply = self.reply, None
        self.world.now += 0.001
        return reply

    def close(self):
        pass


class World:
    def __init__(self, journal, start=0.88, **host_kwargs):
        self.journal, self.host = journal, ht01_host(start, **host_kwargs)
        self.device, self.device_protocol = load_device("v2")
        self.history, self.sent, self.now, self.lose_next_reply = {}, [], 0.0, False
        self.opened = 0
        ids = count(1)

        def factory():
            self.opened += 1
            return DeviceTransport(self)

        self.controller = FlStudioParameterController(
            profile=ht01_profile(), journal=journal, transport_factory=factory,
            timeout_s=1.0, clock=lambda: self.now, next_inspect_id=lambda: f"inspect_{next(ids):08d}",
        )  # fmt: skip

    def apply(self, command_id="ht01_apply_0001", change=-1.0):
        result = gate(self.host.volumes[5], change, command_id)
        assert isinstance(result, DawApplyPermitted), result
        self.request = result.request  # the binding later recovery steps must match
        return self.controller.apply(result.request)

    def inspect(self):
        request = self.request
        result = self.controller.inspect(DawInspectRequest(request.resolution.state.binding, request.profile))
        assert isinstance(result, Success), result
        return result.value

    def restore(self, root="ht01_apply_0001", recovery_id="ht01_restore_001"):
        target = self.controller.restore_target(root)
        assert isinstance(target, Success), target
        authorization = DawRecoveryAuthorization("recovery_auth_0001", ExecutionId("e1"), CONTROLLER, target.value,
                                                 grant())  # fmt: skip
        inspection = self.inspect()
        permitted = gate_daw_restore(authorization, binding=inspection.request.binding, inspection=inspection,
                                     profile=inspection.request.profile, recovery_command_id=recovery_id,
                                     control_state=self.controller.control_state().value)  # fmt: skip
        assert isinstance(permitted, DawRestorePermitted), permitted
        return self.controller.restore(permitted.request)

    def unresolved(self):
        return [item.command_id for item in self.controller.control_state().value.unresolved]


@pytest.fixture
def journal(tmp_path):
    instance = SqliteCommandJournal(tmp_path / "ht01-journal.db", session_key="session-1")
    yield instance
    instance.close()


def test_confirmed_ht01_apply_is_restored_exactly(journal):
    world = World(journal, start=0.88)
    applied = world.apply()
    assert isinstance(applied, DawWriteConfirmed) and applied.before == DawFaderReading(0.88, h_db(0.88))
    assert world.sent[-1]["conversion"] == p.HT01_CONVERSION_ID
    restored = world.restore()
    assert isinstance(restored, DawRestoreConfirmed) and restored.after == applied.before
    assert world.host.reading(5) == reading(0.88)


def test_restore_reaches_the_upper_endpoint_exactly(journal):
    world = World(journal, start=HIGH)
    assert isinstance(world.apply(change=-3.0), DawWriteConfirmed)
    assert isinstance(world.restore(), DawRestoreConfirmed)
    assert world.host.volumes[5] == HIGH


def test_identical_replay_and_conflicting_reuse(journal):
    world = World(journal)
    first = world.apply()
    replay = world.controller.apply(world.request)  # the identical request again
    assert isinstance(replay, DawWriteConfirmed) and replay.replayed and len(world.host.writes) == 1
    restarted = World(journal)  # a new controller over the same journal: replayed from journal evidence
    again = restarted.controller.apply(world.request)
    assert isinstance(again, DawWriteConfirmed) and again.replayed and restarted.host.writes == []
    conflict = world.controller.apply(gate(0.88, -2.0, "ht01_apply_0001").request)
    assert isinstance(conflict, DawWriteRefused) and conflict.code is DawRefusalCode.DUPLICATE_ID_CONFLICT
    assert isinstance(first, DawWriteConfirmed) and len(world.host.writes) == 1


def test_lost_reply_is_uncertain_blocks_and_is_never_resent(journal):
    world = World(journal)
    world.lose_next_reply = True
    lost = world.apply()
    assert isinstance(lost, DawWriteUncertain) and lost.reason is DawUncertainReason.TIMEOUT
    assert world.unresolved() == ["ht01_apply_0001"]
    blocked = world.controller.apply(gate(world.host.volumes[5], -0.5, "ht01_apply_0002").request)
    assert isinstance(blocked, DawWriteRefused) and blocked.code is DawRefusalCode.UNAVAILABLE
    assert len([m for m in world.sent if m["type"] == "set_gain_db"]) == 1


def test_exception_after_setter_is_uncertain_and_blocks(journal):
    world = World(journal)
    world.host.fail_after_set = RuntimeError("setter may have run")
    outcome = world.apply()
    assert isinstance(outcome, DawWriteUncertain) and outcome.reason is DawUncertainReason.EXCEPTION_AFTER_WRITE
    world.host.fail_after_set = None
    assert isinstance(world.controller.apply(gate(0.88, -0.5, "ht01_apply_0002").request), DawWriteRefused)


def test_off_target_blocks_only_the_ht01_conversion(journal):
    world = World(journal)
    original = world.host.curve
    world.host.curve = lambda v: original(v) + (0.3 if v != 0.88 else 0.0)
    assert isinstance(world.apply(), DawWriteOffTarget)
    assert journal.blocked_conversions() == frozenset({VersionRef("fl-mixer-h125-ht01", "v1")})
    assert profiles.calibrated_profile(reference=REFERENCE).conversion not in journal.blocked_conversions()


@pytest.mark.parametrize("api", [44, 46])
def test_controller_maps_an_api_refusal_without_writing(journal, api):
    world = World(journal, api=api)
    outcome = world.apply()
    assert isinstance(outcome, DawWriteRefused) and outcome.code is DawRefusalCode.UNCALIBRATED_BUILD
    assert world.host.writes == [] and world.unresolved() == []


# --- 8. journal scope: an incompatible journal is refused before any MIDI ---------------------------------------------


def existing_controller(journal, factory):
    ids = count(1)
    return FlStudioParameterController(
        profile=make_request().profile, journal=journal, transport_factory=factory, timeout_s=1.0,
        clock=lambda: 0.0, next_inspect_id=lambda: f"inspect_{next(ids):08d}",
    )  # fmt: skip


def record_existing_apply(journal):
    """An existing-scope apply recorded in ``journal`` (a local refusal: no transport is needed)."""
    controller = existing_controller(journal, lambda: (_ for _ in ()).throw(OSError("no ports")))
    outcome = controller.apply(make_request("existing_apply_01"))
    assert isinstance(outcome, DawWriteRefused)
    assert journal.lookup("existing_apply_01") is not None


def test_ht01_profile_refuses_a_journal_of_the_existing_scope_before_midi(journal):
    record_existing_apply(journal)
    opened = []
    with pytest.raises(JournalError, match="scope"):
        FlStudioParameterController(
            profile=ht01_profile(), journal=journal, transport_factory=lambda: opened.append(1),
            timeout_s=1.0, clock=lambda: 0.0, next_inspect_id=lambda: "inspect_00000001",
        )  # fmt: skip
    assert opened == []


def test_existing_profile_refuses_a_journal_of_the_ht01_scope_before_midi(journal):
    world = World(journal)
    assert isinstance(world.apply(), DawWriteConfirmed)
    opened = []
    with pytest.raises(JournalError, match="scope"):
        existing_controller(journal, lambda: opened.append(1))
    assert opened == []


def test_compatible_journals_stay_usable(journal, tmp_path):
    record_existing_apply(journal)
    existing_controller(journal, lambda: None)  # an existing journal keeps its meaning
    relabelled = replace(make_request().profile, reference=VersionRef("another-label", "2"))
    FlStudioParameterController(
        profile=relabelled, journal=journal, transport_factory=lambda: None, timeout_s=1.0,
        clock=lambda: 0.0, next_inspect_id=lambda: "inspect_00000001",
    )  # fmt: skip
    empty = SqliteCommandJournal(tmp_path / "empty.db", session_key="session-1")
    try:
        World(empty)  # an empty journal is compatible with either profile
    finally:
        empty.close()


def test_ht01_world_writes_only_after_the_scope_check(journal):
    world = World(journal)
    assert world.opened == 0
    assert isinstance(world.apply(), DawWriteConfirmed) and world.opened == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("conversion", VersionRef("fl-mixer-h125", "v1")),
        ("normalized_range", DawRange(0.76, 0.94)),
        ("fader_db_range", DawRange(h_db(0.76), h_db(0.94))),
        ("build", DawBuild(BUILD, 46)),
        ("maximum_cut_db", 6.0),
        ("start_db_tolerance", 0.5),
    ],
)
def test_a_journal_recording_the_same_title_with_another_calibration_is_refused(journal, field, value):
    """A row written under an earlier or altered declaration of the HT01 scope (same title and insert) is not
    compatible: the whole calibrated scope, not only its identity, must match."""
    import json

    from autonomous_mixing.adapters.fl_studio.serialization import request_json

    altered = replace(ht01_profile(), **{field: value})
    permitted = gate(0.88, -1.0, profile=altered)
    assert isinstance(permitted, DawApplyPermitted), permitted
    request = permitted.request
    command = db_command(0.88, -1.0)
    journal.reserve(request.command_id, request_json(request), json.dumps(command, sort_keys=True, allow_nan=False))
    opened = []
    with pytest.raises(JournalError, match="scope"):
        FlStudioParameterController(
            profile=ht01_profile(), journal=journal, transport_factory=lambda: opened.append(1),
            timeout_s=1.0, clock=lambda: 0.0, next_inspect_id=lambda: "inspect_00000001",
        )  # fmt: skip
    assert opened == []
