"""DAW inspect/apply contract and calibrated capability gate (plan 2026-11-18-daw-control-contract).

Everything here is simulated. ``FakeDawController`` is a test-only stand-in written to the port's documented
obligations: it demonstrates the intended contract, not that any future adapter journals, replays or refuses
as required; those checks must be repeated against the real FL adapter (plan amendment 5).

The calibrated fader-dB endpoints are derived at full precision from the reviewed conversion function in
``scripts/fl_studio/bridge_protocol.py``, loaded here as a test-only fixture (never by runtime code).
"""

import dataclasses
import importlib.util
import math
import pathlib
from dataclasses import FrozenInstanceError, replace

import pytest

from autonomous_mixing.core.admission.daw import daw_gain_bounds, gate_daw_apply, resolve_daw_gain
from autonomous_mixing.core.ports.daw import validate_daw_apply_outcome, validate_daw_inspection
from autonomous_mixing.core.records.actions import (
    Action,
    ExecutionReceipt,
    ExecutionStatus,
    SetTrackGain,
    derive_candidate_checkpoint,
    validate_receipt_against_checkpoint,
)
from autonomous_mixing.core.records.admission import AdmittedAction, TrackGainPermission
from autonomous_mixing.core.records.capabilities import GainBounds
from autonomous_mixing.core.records.daw import DawFaderReading, DawGainResolution, DawTrackBinding, DawTrackState
from autonomous_mixing.core.records.daw_control import (
    DawApplyPermitted,
    DawApplyRejected,
    DawApplyRequest,
    DawBuild,
    DawControlProfile,
    DawGateCode,
    DawInspection,
    DawInspectRequest,
    DawNoWrite,
    DawRange,
    DawRefusalCode,
    DawUncertainReason,
    DawWriteConfirmed,
    DawWriteOffTarget,
    DawWriteRefused,
    DawWriteUncertain,
)
from autonomous_mixing.core.records.identifiers import (
    ActionId,
    CheckpointId,
    ExecutionId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.project import ArtifactCheckpoint
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success

_BRIDGE = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "fl_studio" / "bridge_protocol.py"
_spec = importlib.util.spec_from_file_location("bridge_protocol_fixture", _BRIDGE)
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)

TITLE, INSERT, NAME = "Thesis Bass Bridge Test", 2, "Bass"
BUILD = DawBuild("Producer Edition v26.1.7 [build 5654]", 45)
CONVERSION = VersionRef("fl-mixer-h125", "v1")
DB_LO = bridge.h_db(0.62)  # full precision, not rounded (amendment 3)
DB_HI = bridge.h_db(0.86)
COMMAND = "cmd_20261118_0001"


def make_profile(**changes):
    profile = DawControlProfile(
        reference=VersionRef("fl-control-profile", "1"),
        build=BUILD,
        project_title=TITLE,
        insert_index=INSERT,
        insert_name=NAME,
        conversion=CONVERSION,
        normalized_range=DawRange(0.62, 0.86),
        fader_db_range=DawRange(DB_LO, DB_HI),
        minimum_cut_db=0.1,
        maximum_cut_db=3.0,
        start_normalized_tolerance=0.001,
        start_db_tolerance=0.05,
        success_db_tolerance=0.05,
        readback_normalized_tolerance=0.001,
    )
    return replace(profile, **changes)


def make_binding(baseline=(0.75, -1.0), baseline_gain=0.0, **changes):
    binding = DawTrackBinding(
        VersionRef("session-binding", "1"), ProjectId("p1"), TrackId("t1"), TITLE, INSERT, NAME, baseline_gain,
        DawFaderReading(*baseline),
    )  # fmt: skip
    return replace(binding, **changes)


def make_state(binding=None, confirmed=None, selected=0.0, revision="r1"):
    binding = binding or make_binding()
    fader = DawFaderReading(*confirmed) if confirmed else binding.baseline_fader
    return DawTrackState(binding, Revision(revision), selected, fader)


def make_admitted(value_db=-1.0, revision="r1", project="p1", track="t1"):
    action = Action(ActionId("a1"), ProjectId(project), Revision(revision), SetTrackGain(TrackId(track), value_db))
    return AdmittedAction(action, VersionRef("policy", "1"))


def make_inspection(binding=None, profile=None, fader=None, build=BUILD, title=TITLE, index=INSERT, name=NAME):
    binding, profile = binding or make_binding(), profile or make_profile()
    reading = binding.baseline_fader if fader is None else DawFaderReading(*fader)
    matches = (title, index, name) == (binding.project_title, binding.insert_index, binding.insert_name)
    return DawInspection(DawInspectRequest(binding, profile), build, title, index, name, reading if matches else None)


def gate(admitted=None, *, state=None, inspection=None, profile=None, blocked=frozenset(), unresolved=None):
    state = state or make_state()
    profile = profile or make_profile()
    inspection = inspection or make_inspection(state.binding, profile, fader=_pair(state.confirmed_fader))
    return gate_daw_apply(
        admitted or make_admitted(), state=state, inspection=inspection, profile=profile,
        execution_id=ExecutionId("e1"), command_id=COMMAND, blocked_conversions=blocked,
        unresolved_command_id=unresolved,
    )  # fmt: skip


def _pair(reading):
    return (reading.normalized, reading.db)


def permitted(**kwargs):
    result = gate(**kwargs)
    assert isinstance(result, DawApplyPermitted), result
    return result.request


def rejected(code, **kwargs):
    result = gate(**kwargs)
    assert isinstance(result, DawApplyRejected), result
    assert result.code is code, (result.code, result.explanation)
    return result


# --- Task 1: profile ------------------------------------------------------------------------------------------------


def test_profile_holds_the_tested_scope_exactly():
    profile = make_profile()
    assert profile.fader_db_range == DawRange(DB_LO, DB_HI)
    assert profile.fader_db_range.minimum == bridge.h_db(0.62)  # no rounding (amendment 3)
    with pytest.raises(FrozenInstanceError):
        profile.minimum_cut_db = 0.2


@pytest.mark.parametrize(
    "field,value",
    [
        ("reference", "profile"),
        ("build", "build"),
        ("project_title", ""),
        ("project_title", 7),
        ("insert_index", 0),
        ("insert_index", True),
        ("insert_index", 2.0),
        ("insert_name", " "),
        ("conversion", "fl-mixer-h125-v1"),
        ("normalized_range", (0.62, 0.86)),
        ("normalized_range", DawRange(-0.1, 0.86)),
        ("normalized_range", DawRange(0.62, 1.1)),
        ("fader_db_range", None),
        ("minimum_cut_db", 0.0),
        ("minimum_cut_db", -0.1),
        ("minimum_cut_db", True),
        ("maximum_cut_db", 0.05),  # below the minimum
        ("maximum_cut_db", float("inf")),
        ("start_normalized_tolerance", -0.001),
        ("start_db_tolerance", float("nan")),
        ("success_db_tolerance", -1.0),
        ("readback_normalized_tolerance", False),
    ],
)
def test_profile_rejects_invalid_fields(field, value):
    with pytest.raises(ValueError):
        make_profile(**{field: value})


@pytest.mark.parametrize("bounds", [(1.0, 1.0), (2.0, 1.0), (float("nan"), 1.0), (0.0, True), ("0", 1.0)])
def test_range_rejects_degenerate_inverted_or_non_numeric_bounds(bounds):
    with pytest.raises(ValueError):
        DawRange(*bounds)


@pytest.mark.parametrize("value", [("", 45), ("v", 0), ("v", True), ("v", 45.0), (None, 45)])
def test_build_rejects_invalid_fields(value):
    with pytest.raises(ValueError):
        DawBuild(*value)


# --- Task 2: inspection ---------------------------------------------------------------------------------------------


def test_inspect_request_must_lie_inside_the_profile_scope():
    with pytest.raises(ValueError):
        DawInspectRequest(make_binding(insert_name="Sub"), make_profile())
    with pytest.raises(ValueError):
        DawInspectRequest(make_binding(project_title="Other"), make_profile())
    with pytest.raises(ValueError):
        DawInspectRequest(make_binding(), "profile")


def test_inspection_represents_an_empty_observed_title():
    inspection = make_inspection(title="")  # an unsaved FL project may report no metadata title
    assert inspection.project_title == "" and inspection.matches_binding is False


def test_inspection_reports_a_mismatching_identity_without_gain():
    inspection = make_inspection(name="Sub")
    assert inspection.matches_binding is False and inspection.fader is None
    assert make_inspection().matches_binding is True


def test_inspection_fader_is_present_exactly_when_identity_matches():
    request = DawInspectRequest(make_binding(), make_profile())
    with pytest.raises(ValueError):
        DawInspection(request, BUILD, TITLE, INSERT, NAME, None)
    with pytest.raises(ValueError):
        DawInspection(request, BUILD, TITLE, INSERT, "Sub", DawFaderReading(0.75, -1.0))
    assert DawInspection(request, BUILD, TITLE, INSERT, None, None).matches_binding is False  # index not an insert


@pytest.mark.parametrize(
    "position,value", [(1, "build"), (2, 7), (2, None), (3, 0), (3, True), (4, 7), (5, (0.75, -1.0))]
)  # fmt: skip
def test_inspection_rejects_invalid_fields(position, value):
    args = [DawInspectRequest(make_binding(), make_profile()), BUILD, TITLE, INSERT, NAME, DawFaderReading(0.75, -1.0)]
    args[position] = value
    with pytest.raises(ValueError):
        DawInspection(*args)


# --- Task 4: gate, in order -----------------------------------------------------------------------------------------


def test_gate_permits_a_calibrated_cut_and_builds_a_consistent_request():
    request = permitted()
    assert request.command_id == COMMAND and request.execution_id == ExecutionId("e1")
    assert request.requested_change_db == request.resolution.delta_db == -1.0
    assert request.resolution.target_fader_db == -2.0
    assert request.admitted == make_admitted() and request.profile == make_profile()


def test_unresolved_operation_is_refused_first():
    rejected(DawGateCode.UNRESOLVED_OPERATION, unresolved="cmd_previous_01", admitted=make_admitted(5.0))


def test_blocked_conversion_is_refused():
    rejected(DawGateCode.CONVERSION_BLOCKED, blocked=frozenset({CONVERSION}))
    permitted(blocked=frozenset({VersionRef("fl-mixer-h125", "v0")}))  # another conversion does not block


@pytest.mark.parametrize("changes", [{"project_title": "Other"}, {"insert_index": 3}, {"insert_name": "Sub"}])
def test_profile_scope_mismatch_is_refused(changes):
    # The gate compares the binding with the profile's scope before it checks which request the inspection
    # answers, so an out-of-scope profile is a typed refusal even though no in-scope inspection can exist for it.
    profile = make_profile(**changes)
    state = make_state()
    with pytest.raises(ValueError):
        DawInspectRequest(state.binding, profile)  # a request outside scope is itself a wiring error
    result = gate_daw_apply(
        make_admitted(), state=state, inspection=make_inspection(state.binding, make_profile()), profile=profile,
        execution_id=ExecutionId("e1"), command_id=COMMAND, blocked_conversions=frozenset(),
        unresolved_command_id=None,
    )  # fmt: skip
    assert isinstance(result, DawApplyRejected) and result.code is DawGateCode.PROFILE_SCOPE


@pytest.mark.parametrize("changes", [{"title": "Other"}, {"index": 3}, {"name": "Sub"}, {"name": None}])
def test_inspected_identity_mismatch_is_refused(changes):
    rejected(DawGateCode.IDENTITY_MISMATCH, inspection=make_inspection(**changes))


@pytest.mark.parametrize(
    "build", [DawBuild("Producer Edition v26.2.0 [build 6000]", 45), DawBuild(BUILD.product_version, 46)]
)
def test_unsupported_build_is_refused(build):
    rejected(DawGateCode.UNSUPPORTED_BUILD, inspection=make_inspection(build=build))


@pytest.mark.parametrize(
    "admitted", [make_admitted(revision="r0"), make_admitted(project="p2"), make_admitted(track="t2")]
)
def test_stale_revision_or_foreign_references_are_refused(admitted):
    rejected(DawGateCode.STALE_REVISION, admitted=admitted)


def _dyadic_profile():
    return make_profile(start_normalized_tolerance=0.0625, start_db_tolerance=0.125, success_db_tolerance=0.0625,
                        readback_normalized_tolerance=0.0625)  # fmt: skip


@pytest.mark.parametrize(
    "observed,code",
    [
        ((0.75 + 0.0625, -1.0), None),  # exactly on the normalized tolerance: allowed
        ((0.75, -1.0 + 0.125), None),  # exactly on the dB tolerance: allowed
        ((0.75 + 0.125, -1.0), DawGateCode.DRIFT),  # normalized only
        ((0.75, -1.0 - 0.25), DawGateCode.DRIFT),  # dB only
    ],
)
def test_drift_is_checked_on_both_coordinates_with_inclusive_edges(observed, code):
    profile = _dyadic_profile()
    state = make_state()
    inspection = make_inspection(state.binding, profile, fader=observed)
    result = gate(state=state, profile=profile, inspection=inspection)
    if code is None:
        assert isinstance(result, DawApplyPermitted)
    else:
        assert isinstance(result, DawApplyRejected) and result.code is code


def test_no_change_dispatches_nothing():
    result = gate(admitted=make_admitted(0.0))
    assert isinstance(result, DawNoWrite) and result.resolution.delta_db == 0.0


@pytest.mark.parametrize("value_db", [0.5, 1e-12])
def test_any_gain_increase_is_refused(value_db):
    rejected(DawGateCode.GAIN_INCREASE_UNSUPPORTED, admitted=make_admitted(value_db))


@pytest.mark.parametrize("value_db", [-0.1, -1.0, -2.0, -3.0])
def test_cuts_within_the_limits_are_permitted(value_db):
    assert permitted(admitted=make_admitted(value_db)).requested_change_db == pytest.approx(value_db, abs=1e-12)


@pytest.mark.parametrize("value_db", [-0.09, -3.01, -1e-12, -10.0])
def test_cuts_outside_the_limits_are_refused_not_rounded(value_db):
    rejected(DawGateCode.CUT_OUT_OF_BOUNDS, admitted=make_admitted(value_db))


def _start(normalized, db):
    binding = make_binding(baseline=(normalized, db))
    return make_state(binding)


@pytest.mark.parametrize(
    "normalized,db,code",
    [
        (0.86, -1.0, None),  # upper normalized endpoint, exactly
        (math.nextafter(0.86, 1.0), -1.0, DawGateCode.START_OUT_OF_RANGE),
        (0.62, -1.0, None),  # lower normalized endpoint, exactly
        (math.nextafter(0.62, 0.0), -1.0, DawGateCode.START_OUT_OF_RANGE),
        (0.75, DB_HI, None),  # upper dB endpoint, full precision
        (0.75, math.nextafter(DB_HI, math.inf), DawGateCode.START_OUT_OF_RANGE),
        (0.75, DB_LO, DawGateCode.TARGET_OUT_OF_RANGE),  # start passes on the edge; any cut leaves the range
        (0.75, math.nextafter(DB_LO, -math.inf), DawGateCode.START_OUT_OF_RANGE),
    ],
)
def test_start_range_endpoints_in_both_coordinates(normalized, db, code):
    state = _start(normalized, db)
    result = gate(state=state)
    if code is None:
        assert isinstance(result, DawApplyPermitted), result
    else:
        assert isinstance(result, DawApplyRejected) and result.code is code, result


@pytest.mark.parametrize(
    "baseline_db,code", [(DB_LO, None), (math.nextafter(DB_LO, -math.inf), DawGateCode.TARGET_OUT_OF_RANGE)]
)
def test_target_range_endpoint_at_full_precision(baseline_db, code):
    # baseline gain 0 at fader DB_LO: action 0 dB targets exactly DB_LO; the current fader sits 1 dB higher
    binding = make_binding(baseline=(0.62, baseline_db))
    state = make_state(binding, confirmed=(0.65, DB_LO + 1.0), selected=1.0)
    result = gate(admitted=make_admitted(0.0), state=state)
    if code is None:
        assert isinstance(result, DawApplyPermitted) and result.request.resolution.target_fader_db == DB_LO
    else:
        assert isinstance(result, DawApplyRejected) and result.code is code


def test_nonzero_baseline_and_sequential_actions_resolve_against_the_original_binding():
    # application gain +2 dB corresponds to fader -1 dB; absolute actions map from that baseline every time
    binding = make_binding(baseline=(0.80, -1.0), baseline_gain=2.0)
    first = permitted(admitted=make_admitted(1.0), state=make_state(binding, selected=2.0))
    assert (first.resolution.target_fader_db, first.requested_change_db) == (-2.0, -1.0)
    after_first = make_state(binding, confirmed=(0.77, -2.0), selected=1.0)
    second = permitted(admitted=make_admitted(0.0), state=after_first)
    assert (second.resolution.target_fader_db, second.requested_change_db) == (-3.0, -1.0)


def test_gate_uses_resolve_daw_gain_with_the_profile_start_tolerances():
    state, profile = make_state(), make_profile()
    request = permitted(state=state, profile=profile)
    expected = resolve_daw_gain(make_admitted().action, state=state, observed_fader=state.confirmed_fader,
                                normalized_tolerance=0.001, db_tolerance=0.05)  # fmt: skip
    assert request.resolution == expected


def test_gate_rejects_wiring_errors_with_value_error():
    with pytest.raises(ValueError):
        gate(admitted=make_admitted().action)  # an unadmitted action
    other = make_inspection(make_binding(reference=VersionRef("session-binding", "2")))
    with pytest.raises(ValueError):
        gate(inspection=other)  # an inspection for another binding
    with pytest.raises(ValueError):
        gate(blocked={CONVERSION})  # not a frozenset


# --- capability bounds ------------------------------------------------------------------------------------------------


def _permission(low, high, track="t1"):
    return TrackGainPermission(TrackId(track), low, high)


@pytest.mark.parametrize(
    "permission,expected",
    [
        ((-10.0, 10.0), GainBounds(-3.0, -0.1)),
        ((-0.5, 5.0), GainBounds(-0.5, -0.1)),
        ((-10.0, -2.0), GainBounds(-3.0, -2.0)),
        ((0.0, 5.0), None),  # only increases: disjoint
    ],
)
def test_daw_gain_bounds_intersects_policy_cut_limits_and_calibrated_range(permission, expected):
    assert daw_gain_bounds(profile=make_profile(), state=make_state(), permission=_permission(*permission)) == expected


def test_daw_gain_bounds_near_the_calibrated_floor():
    state = make_state(confirmed=(0.64, -4.5), selected=-3.5)
    bounds = daw_gain_bounds(profile=make_profile(), state=state, permission=_permission(-10.0, 10.0))
    assert bounds.minimum_db == 0.0 + (DB_LO - (-1.0))  # the calibrated floor limits it, exactly
    # the naive -3.6 resolves to a cut just under 0.1 dB; the endpoint moves inward by float steps only
    assert -3.6 - 1e-12 < bounds.maximum_db <= -3.6
    assert isinstance(gate(admitted=make_admitted(bounds.maximum_db), state=state), DawApplyPermitted)


@pytest.mark.parametrize("confirmed", [(0.87, -1.0), (0.75, DB_HI + 0.5)])
def test_daw_gain_bounds_is_none_when_the_confirmed_start_is_outside_the_range(confirmed):
    state = make_state(confirmed=confirmed)
    assert daw_gain_bounds(profile=make_profile(), state=state, permission=_permission(-10.0, 10.0)) is None


def test_daw_gain_bounds_validates_its_references():
    with pytest.raises(ValueError):
        daw_gain_bounds(profile=make_profile(), state=make_state(), permission=_permission(-10.0, 10.0, track="t2"))
    for changes in ({"project_title": "Other"}, {"insert_index": 3}, {"insert_name": "Sub"}):
        with pytest.raises(ValueError):
            daw_gain_bounds(profile=make_profile(**changes), state=make_state(), permission=_permission(-10.0, 10.0))


# --- amendment 2: the request validates itself, independently of the gate ------------------------------------------


def _resolution(state, value_db, observed):
    action = make_admitted(value_db).action
    target = state.binding.baseline_fader.db + (value_db - state.binding.baseline_gain_db)
    return DawGainResolution(action, state, observed, target, target - observed.db)


def _request(**changes):
    return replace(permitted(), **changes)


def test_a_permitted_request_can_be_reconstructed_field_by_field():
    request = permitted()
    assert DawApplyRequest(**{f.name: getattr(request, f.name) for f in dataclasses.fields(request)}) == request


@pytest.mark.parametrize("command_id", ["short", "has space here", "", 7, "x" * 65])
def test_request_rejects_a_bad_command_id(command_id):
    with pytest.raises(ValueError):
        _request(command_id=command_id)


def test_request_rejects_a_foreign_admitted_action():
    with pytest.raises(ValueError):
        _request(admitted=make_admitted(-2.0))


def test_request_rejects_a_foreign_or_mismatching_inspection_or_profile():
    request = permitted()
    with pytest.raises(ValueError):
        _request(inspection=make_inspection(make_binding(reference=VersionRef("session-binding", "2"))))
    with pytest.raises(ValueError):
        _request(profile=make_profile(reference=VersionRef("fl-control-profile", "2")))
    with pytest.raises(ValueError):
        _request(inspection=make_inspection(name="Sub"))
    with pytest.raises(ValueError):
        _request(inspection=make_inspection(build=DawBuild("other", 45)))
    with pytest.raises(ValueError):
        _request(inspection=make_inspection(fader=(0.7505, -1.0)))  # differs from the resolution's observation
    assert request.inspection.fader == request.resolution.observed_fader


def test_request_rejects_drift_beyond_the_profile_tolerances():
    state = make_state()
    observed = DawFaderReading(0.75, -1.2)  # 0.2 dB from the confirmed fader
    resolution = _resolution(state, -1.0, observed)
    with pytest.raises(ValueError):
        _request(resolution=resolution, inspection=make_inspection(fader=(0.75, -1.2)),
                 requested_change_db=resolution.delta_db)  # fmt: skip


@pytest.mark.parametrize("value_db", [0.0, 0.5, -0.05, -3.5])
def test_request_rejects_a_change_that_is_not_a_permitted_cut(value_db):
    state = make_state()
    resolution = _resolution(state, value_db, state.confirmed_fader)
    with pytest.raises(ValueError):
        _request(resolution=resolution, admitted=make_admitted(value_db), requested_change_db=resolution.delta_db)


def test_request_rejects_a_requested_change_other_than_the_resolution_delta():
    with pytest.raises(ValueError):
        _request(requested_change_db=-1.5)


@pytest.mark.parametrize("start", [(0.87, -1.0), (0.75, DB_HI + 1.0)])
def test_request_rejects_a_start_outside_the_range(start):
    state = make_state(make_binding(baseline=start))
    resolution = _resolution(state, -1.0, state.confirmed_fader)
    with pytest.raises(ValueError):
        DawApplyRequest(ExecutionId("e1"), COMMAND, make_admitted(-1.0), resolution, make_profile(),
                        make_inspection(state.binding, fader=start), resolution.delta_db)  # fmt: skip


def test_request_rejects_a_target_outside_the_range():
    state = make_state(make_binding(baseline=(0.62, DB_LO)), confirmed=(0.65, DB_LO + 1.0), selected=1.0)
    resolution = _resolution(state, -0.5, state.confirmed_fader)  # target DB_LO - 0.5
    with pytest.raises(ValueError):
        DawApplyRequest(ExecutionId("e1"), COMMAND, make_admitted(-0.5), resolution, make_profile(),
                        make_inspection(state.binding, fader=(0.65, DB_LO + 1.0)), resolution.delta_db)  # fmt: skip


def test_request_rejects_wrong_field_types():
    for field, value in (("execution_id", "e1"), ("admitted", make_admitted().action), ("profile", None),
                         ("requested_change_db", True)):  # fmt: skip
        with pytest.raises(ValueError):
            _request(**{field: value})


def test_no_write_and_permitted_records_check_their_content():
    with pytest.raises(ValueError):
        DawNoWrite(permitted().resolution)  # a non-zero delta is not a no-change
    with pytest.raises(ValueError):
        DawApplyPermitted("request")
    with pytest.raises(ValueError):
        DawApplyRejected("DRIFT", "x")
    with pytest.raises(ValueError):
        DawApplyRejected(DawGateCode.DRIFT, "")


# --- outcomes and the response validator ---------------------------------------------------------------------------


def _dyadic_request():
    return permitted(profile=_dyadic_profile(), inspection=make_inspection(profile=_dyadic_profile()))


def _written(cls, request, achieved=-1.0, before=None, after_normalized=0.7, commanded=0.7, replayed=False):
    before = before or request.inspection.fader
    after = DawFaderReading(after_normalized, before.db + achieved)
    return cls(request.command_id, before, after, commanded, after.db - before.db, replayed)


def test_confirmed_and_off_target_are_decided_on_the_exact_side_of_the_success_tolerance():
    request = _dyadic_request()
    validate_daw_apply_outcome(request, _written(DawWriteConfirmed, request, achieved=-1.0))
    validate_daw_apply_outcome(request, _written(DawWriteConfirmed, request, achieved=-0.9375))  # exactly 0.0625
    with pytest.raises(ValueError):
        validate_daw_apply_outcome(request, _written(DawWriteOffTarget, request, achieved=-0.9375))
    validate_daw_apply_outcome(request, _written(DawWriteOffTarget, request, achieved=-0.875))
    with pytest.raises(ValueError):
        validate_daw_apply_outcome(request, _written(DawWriteConfirmed, request, achieved=-0.875))


def test_written_outcomes_must_start_where_the_request_expected():
    request = _dyadic_request()
    far = DawFaderReading(0.75, -1.25)  # 0.25 dB from the inspected start
    with pytest.raises(ValueError):
        validate_daw_apply_outcome(request, _written(DawWriteConfirmed, request, before=far))


def test_written_outcomes_must_read_back_at_the_claimed_commanded_position():
    request = _dyadic_request()
    with pytest.raises(ValueError):
        validate_daw_apply_outcome(request, _written(DawWriteConfirmed, request, after_normalized=0.7, commanded=0.8))
    with pytest.raises(ValueError):  # the claimed position itself lies outside the calibrated range
        validate_daw_apply_outcome(request, _written(DawWriteConfirmed, request, after_normalized=0.6, commanded=0.6))


def test_outcome_command_id_and_type_must_match():
    request = _dyadic_request()
    with pytest.raises(ValueError):
        validate_daw_apply_outcome(request, replace(_written(DawWriteConfirmed, request), command_id="cmd_other_0001"))
    with pytest.raises(ValueError):
        validate_daw_apply_outcome(request, "applied")


def test_refused_and_uncertain_outcomes_are_reports_and_are_accepted():
    request = _dyadic_request()
    validate_daw_apply_outcome(
        request, DawWriteRefused(request.command_id, DawRefusalCode.TRANSPORT_PLAYING, "x", None)
    )
    validate_daw_apply_outcome(
        request, DawWriteRefused(request.command_id, DawRefusalCode.START_GAIN, "moved", DawFaderReading(0.5, -9.0))
    )
    validate_daw_apply_outcome(
        request, DawWriteUncertain(request.command_id, DawUncertainReason.TIMEOUT, None, None, "no reply")
    )
    validate_daw_apply_outcome(
        request,
        DawWriteUncertain(request.command_id, DawUncertainReason.READBACK_DISAGREES, request.inspection.fader,
                          DawFaderReading(0.1, -30.0), "fader elsewhere"),
    )  # fmt: skip


@pytest.mark.parametrize("cls", [DawWriteConfirmed, DawWriteOffTarget])
def test_written_outcome_records_check_arithmetic_and_types(cls):
    before, after = DawFaderReading(0.75, -1.0), DawFaderReading(0.7, -2.0)
    assert cls(COMMAND, before, after, 0.7, -1.0, False).replayed is False
    for args in (
        (COMMAND, before, after, 0.7, -1.5, False),  # achieved differs from after - before
        (COMMAND, before, after, 0.7, -1.0, 1),  # replayed not a bool
        (COMMAND, before, after, 1.5, -1.0, False),  # commanded outside [0, 1]
        (COMMAND, before, after, True, -1.0, False),
        ("short", before, after, 0.7, -1.0, False),
        (COMMAND, None, after, 0.7, -1.0, False),
    ):
        with pytest.raises(ValueError):
            cls(*args)


def test_refused_and_uncertain_records_check_their_fields():
    with pytest.raises(ValueError):
        DawWriteRefused(COMMAND, "start_gain", "x", None)
    with pytest.raises(ValueError):
        DawWriteRefused(COMMAND, DawRefusalCode.START_GAIN, "", None)
    with pytest.raises(ValueError):
        DawWriteUncertain(COMMAND, DawUncertainReason.TIMEOUT, None, None, "")
    with pytest.raises(ValueError):
        DawWriteUncertain(COMMAND, "timeout", None, None, "x")


def test_refusal_codes_and_uncertain_reasons_mirror_the_reviewed_bridge():
    assert {c.name for c in DawRefusalCode} == {
        "IDENTITY_MISMATCH", "TRANSPORT_PLAYING", "EDIT_UNAVAILABLE", "START_GAIN", "OUT_OF_RANGE",
        "UNCALIBRATED_BUILD", "UNCALIBRATED_START", "CONVERSION", "CONVERSION_MISMATCH", "BOUNDS",
        "DUPLICATE_ID_CONFLICT", "API_TOO_OLD", "UNSUPPORTED", "UNAVAILABLE",
    }  # fmt: skip
    assert {r.name for r in DawUncertainReason} == {
        "TIMEOUT", "INVALID_REPLY", "READBACK_DISAGREES", "EXCEPTION_AFTER_WRITE", "DISPATCH_INTERRUPTED",
        "PENDING_UNRESOLVED",
    }  # fmt: skip


def test_inspection_response_validation():
    request = DawInspectRequest(make_binding(), make_profile())
    validate_daw_inspection(request, Success(make_inspection()))
    validate_daw_inspection(request, PortFailure(PortFailureCode.UNAVAILABLE, "ports missing", True))
    other = make_inspection(make_binding(reference=VersionRef("session-binding", "2")))
    with pytest.raises(ValueError):
        validate_daw_inspection(request, Success(other))
    with pytest.raises(ValueError):
        validate_daw_inspection(request, Success("inspection"))
    with pytest.raises(ValueError):
        validate_daw_inspection(request, make_inspection())  # not wrapped in a PortResult


# --- the bridge obligations, demonstrated on a test-only controller (amendment 5) ----------------------------------


class FakeDawController:
    """Test-only stand-in that follows the port's documented obligations. It is not an adapter."""

    def __init__(self, fader=(0.75, -1.0), lands=lambda target_db: target_db, fail_after_write=False,
                 lose_reply=False, refuse=None):  # fmt: skip
        self.fader = DawFaderReading(*fader)
        self.journal, self.writes, self.history = [], [], {}
        self.lands, self.fail_after_write, self.lose_reply = lands, fail_after_write, lose_reply
        self.refuse = refuse

    def inspect(self, request):
        binding = request.binding
        inspection = DawInspection(request, BUILD, binding.project_title, binding.insert_index, binding.insert_name,
                                   self.fader)  # fmt: skip
        return Success(inspection)

    def apply(self, request):
        prior = self.history.get(request.command_id)
        if prior is not None:
            if prior[0] != request:
                return DawWriteRefused(request.command_id, DawRefusalCode.DUPLICATE_ID_CONFLICT, "reused id", None)
            return dataclasses.replace(prior[1], replayed=True) if hasattr(prior[1], "replayed") else prior[1]
        self.journal.append(("pending", request.command_id))  # before dispatch
        before = self.fader
        if self.refuse is not None:
            outcome = DawWriteRefused(request.command_id, self.refuse, "refused before writing", before)
        elif self.lose_reply:
            outcome = DawWriteUncertain(request.command_id, DawUncertainReason.TIMEOUT, before, None, "no reply")
        else:
            target_db = before.db + request.requested_change_db
            self.writes.append(request.command_id)
            self.fader = DawFaderReading(0.7, self.lands(target_db))
            if self.fail_after_write:
                outcome = DawWriteUncertain(request.command_id, DawUncertainReason.EXCEPTION_AFTER_WRITE, before,
                                            None, "setter raised")  # fmt: skip
            else:
                achieved = self.fader.db - before.db
                miss = abs(achieved - request.requested_change_db)
                cls = DawWriteConfirmed if miss <= request.profile.success_db_tolerance else DawWriteOffTarget
                outcome = cls(request.command_id, before, self.fader, 0.7, achieved, False)
        self.history[request.command_id] = (request, outcome)
        return outcome


def test_simulated_controller_journals_before_dispatch_and_replays_without_a_second_write():
    controller = FakeDawController()
    request = permitted()
    first = controller.apply(request)
    validate_daw_apply_outcome(request, first)
    assert controller.journal == [("pending", COMMAND)] and controller.writes == [COMMAND]
    again = controller.apply(request)
    assert again.replayed is True and again.after == first.after and controller.writes == [COMMAND]
    validate_daw_apply_outcome(request, again)


def test_simulated_controller_refuses_a_conflicting_reuse_of_the_command_id():
    controller = FakeDawController()
    controller.apply(permitted())
    conflicting = permitted(admitted=make_admitted(-2.0))
    outcome = controller.apply(conflicting)
    assert isinstance(outcome, DawWriteRefused) and outcome.code is DawRefusalCode.DUPLICATE_ID_CONFLICT
    validate_daw_apply_outcome(conflicting, outcome)
    assert len(controller.writes) == 1


def test_simulated_controller_reports_timeout_and_post_write_failures_as_uncertain():
    for controller in (FakeDawController(lose_reply=True), FakeDawController(fail_after_write=True)):
        request = permitted()
        outcome = controller.apply(request)
        assert isinstance(outcome, DawWriteUncertain)
        validate_daw_apply_outcome(request, outcome)
        assert controller.journal == [("pending", COMMAND)]


def test_simulated_controller_off_target_is_a_known_write():
    controller = FakeDawController(lands=lambda target_db: target_db - 0.08)
    request = permitted()
    outcome = controller.apply(request)
    assert isinstance(outcome, DawWriteOffTarget) and controller.writes == [COMMAND]
    validate_daw_apply_outcome(request, outcome)


def test_simulated_reload_loses_duplicate_history():
    controller = FakeDawController()
    request = permitted()
    controller.apply(request)
    reloaded = FakeDawController(fader=_pair(controller.fader))  # same fader, empty history
    second = reloaded.apply(request)
    assert second.replayed is False and reloaded.writes == [COMMAND]  # only the start check could stop this resend
    with pytest.raises(ValueError):
        validate_daw_apply_outcome(request, second)  # it no longer starts where the request expected


def test_gate_then_simulated_controller_end_to_end():
    controller = FakeDawController()
    inspection = controller.inspect(DawInspectRequest(make_binding(), make_profile())).value
    result = gate(inspection=inspection)
    outcome = controller.apply(result.request)
    assert isinstance(outcome, DawWriteConfirmed) and outcome.achieved_change_db == -1.0


# --- fader confirmation is not candidate audio --------------------------------------------------------------------


@pytest.mark.parametrize("cls", [DawWriteConfirmed, DawWriteOffTarget, DawWriteRefused, DawWriteUncertain])
def test_daw_outcomes_carry_no_candidate_fields(cls):
    names = {f.name for f in dataclasses.fields(cls)}
    assert not names & {"candidate_revision", "candidate_artifacts", "superseded_artifact_ids", "status"}
    assert not issubclass(cls, ExecutionReceipt)


def test_candidate_derivation_and_receipt_checks_reject_daw_outcomes():
    request = permitted()
    confirmed = FakeDawController().apply(request)
    checkpoint = ArtifactCheckpoint.__new__(ArtifactCheckpoint)  # never reached: the receipt is rejected first
    with pytest.raises((AttributeError, TypeError, ValueError)):
        derive_candidate_checkpoint(checkpoint, confirmed, CheckpointId("c2"))
    with pytest.raises((AttributeError, TypeError, ValueError)):
        validate_receipt_against_checkpoint(confirmed, checkpoint)


def test_execution_status_is_unchanged():
    assert [s.name for s in ExecutionStatus] == ["NOT_STARTED", "FAILED", "CANDIDATE_READY", "UNCERTAIN"]


# --- review findings 2026-11-18 ---------------------------------------------------------------------------------------

BOUNDARY_CASES = [
    # (baseline fader dB f0, baseline application gain g0, confirmed fader dB); normalized readings stay in range
    (-4.0, 0.0, -4.0),  # the reviewed case: the naive upper endpoint -0.1 resolves to a 0.09999999999999964 dB cut
    (-1.1527416706085205, 0.0, -1.1527416706085205),  # the live calibration start
    (-1.0, 2.0, -2.5),
    (-4.0, -3.3, -4.7),
    (DB_HI - 0.05, 0.0, DB_HI - 0.05),  # just below the calibrated ceiling
    (-1.0, 0.0, DB_LO + 1.3),  # a 3 dB cut would leave the range: the floor limits the lower endpoint
    (0.3, 1.7, -0.45),
    (-4.0, 0.1, -4.0),  # review: the naive upper endpoint is 0.0; the permitted one is a few ulps below zero
    (-4.0, 3.0, -4.0),  # the naive lower endpoint (a 3 dB cut) is 0.0, crossing zero from the other side
    (-4.0, -0.1, -4.0),  # cancellation just below zero at the upper endpoint
]


def _naive_bounds(f0, g0, confirmed_db, profile):
    current = g0 + (confirmed_db - f0)
    return (
        max(current - profile.maximum_cut_db, g0 + (profile.fader_db_range.minimum - f0)),
        min(current - profile.minimum_cut_db, g0 + (profile.fader_db_range.maximum - f0)),
    )


@pytest.mark.parametrize("f0,g0,confirmed_db", BOUNDARY_CASES)
def test_advertised_endpoints_are_permitted_by_the_gate_end_to_end(f0, g0, confirmed_db):
    profile = make_profile()
    state = make_state(make_binding(baseline=(0.75, f0), baseline_gain=g0), confirmed=(0.7, confirmed_db))
    bounds = daw_gain_bounds(profile=profile, state=state, permission=_permission(-100.0, 100.0))
    assert bounds is not None
    for endpoint in (bounds.minimum_db, bounds.maximum_db):
        result = gate(admitted=make_admitted(endpoint), state=state, profile=profile)
        assert isinstance(result, DawApplyPermitted), (endpoint, result)


@pytest.mark.parametrize("f0,g0,confirmed_db", BOUNDARY_CASES)
def test_advertised_endpoints_are_tight_and_never_widen_the_cut_range(f0, g0, confirmed_db):
    profile = make_profile()
    state = make_state(make_binding(baseline=(0.75, f0), baseline_gain=g0), confirmed=(0.7, confirmed_db))
    bounds = daw_gain_bounds(profile=profile, state=state, permission=_permission(-100.0, 100.0))
    naive_low, naive_high = _naive_bounds(f0, g0, confirmed_db, profile)
    assert naive_low <= bounds.minimum_db <= bounds.maximum_db <= naive_high  # only ever shrunk, never widened
    # where an endpoint was moved, it moved minimally: the next float outward is the one the gate refuses
    moved = [(bounds.maximum_db, math.inf, naive_high), (bounds.minimum_db, -math.inf, naive_low)]
    for endpoint, outward_direction, naive in moved:
        if endpoint != naive:
            outward = math.nextafter(endpoint, outward_direction)
            result = gate(admitted=make_admitted(outward), state=state, profile=profile)
            assert not isinstance(result, DawApplyPermitted), outward


def test_the_reviewed_upper_endpoint_is_moved_inward_by_float_steps_only():
    profile = make_profile()
    state = make_state(make_binding(baseline=(0.75, -4.0)), confirmed=(0.7, -4.0))
    naive = gate(admitted=make_admitted(-0.1), state=state, profile=profile)
    assert isinstance(naive, DawApplyRejected) and naive.code is DawGateCode.CUT_OUT_OF_BOUNDS  # the finding
    bounds = daw_gain_bounds(profile=profile, state=state, permission=_permission(-100.0, 100.0))
    assert -0.1 - 1e-12 < bounds.maximum_db < -0.1


def test_policy_endpoints_inside_the_cut_window_are_kept_exactly():
    state = make_state(make_binding(baseline=(0.75, -4.0)), confirmed=(0.7, -4.0))
    # from -4 dB the calibrated floor caps the window near -1.33; a policy window inside it is kept exactly
    bounds = daw_gain_bounds(profile=make_profile(), state=state, permission=_permission(-1.25, -0.5))
    assert bounds == GainBounds(-1.25, -0.5)


def test_simulated_controller_replays_an_uncertain_outcome_unchanged_without_another_write():
    for controller, writes in ((FakeDawController(fail_after_write=True), 1), (FakeDawController(lose_reply=True), 0)):
        request = permitted()
        first = controller.apply(request)
        again = controller.apply(request)
        assert isinstance(again, DawWriteUncertain) and again == first  # unchanged: uncertain has no replayed flag
        assert len(controller.writes) == writes and controller.journal == [("pending", COMMAND)]
        validate_daw_apply_outcome(request, again)


def test_simulated_controller_replays_a_refusal_unchanged():
    controller = FakeDawController(refuse=DawRefusalCode.TRANSPORT_PLAYING)
    request = permitted()
    first = controller.apply(request)
    assert isinstance(first, DawWriteRefused) and controller.apply(request) == first
    assert controller.writes == []


def test_cancellation_near_zero_still_advertises_the_permitted_range():
    # review finding: baseline +0.1 dB at fader -4 dB; the naive upper endpoint 0.0 resolves to a cut just under
    # 0.1 dB, and walking subnormal floats from zero never reaches a permitted value
    profile = make_profile()
    state = make_state(make_binding(baseline=(0.75, -4.0), baseline_gain=0.1), confirmed=(0.7, -4.0), selected=0.1)
    assert isinstance(gate(admitted=make_admitted(-0.9), state=state, profile=profile), DawApplyPermitted)
    bounds = daw_gain_bounds(profile=profile, state=state, permission=_permission(-10.0, 10.0))
    assert bounds is not None and bounds.minimum_db <= -0.9 <= bounds.maximum_db
    assert -1e-12 < bounds.maximum_db < 0.0  # moved inward across the subnormal range, not stopped near zero
    for endpoint in (bounds.minimum_db, bounds.maximum_db):
        assert isinstance(gate(admitted=make_admitted(endpoint), state=state, profile=profile), DawApplyPermitted)
    outward = math.nextafter(bounds.maximum_db, math.inf)
    assert not isinstance(gate(admitted=make_admitted(outward), state=state, profile=profile), DawApplyPermitted)


@pytest.mark.parametrize("value", [-1.0, -1.25])  # targets -5.0 and -5.25 dB, inside the calibrated floor
def test_a_single_value_policy_window_is_kept_when_the_gate_permits_it(value):
    state = make_state(make_binding(baseline=(0.75, -4.0)), confirmed=(0.7, -4.0))
    bounds = daw_gain_bounds(profile=make_profile(), state=state, permission=_permission(value, value))
    assert bounds == GainBounds(value, value)


def test_a_single_value_policy_window_the_gate_refuses_is_reported_as_none():
    state = make_state(make_binding(baseline=(0.75, -4.0)), confirmed=(0.7, -4.0))
    assert not isinstance(gate(admitted=make_admitted(-0.1), state=state), DawApplyPermitted)  # the reviewed case
    assert daw_gain_bounds(profile=make_profile(), state=state, permission=_permission(-0.1, -0.1)) is None
