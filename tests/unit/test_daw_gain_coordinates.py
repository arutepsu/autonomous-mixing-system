"""DAW parameter coordinates stay separate from fixed-input audio gain."""

from dataclasses import FrozenInstanceError, replace

import pytest

from autonomous_mixing.core.admission.daw import resolve_daw_gain
from autonomous_mixing.core.records.actions import Action, SetTrackGain
from autonomous_mixing.core.records.daw import (
    DawFaderReading,
    DawGainResolution,
    DawTrackBinding,
    DawTrackState,
)
from autonomous_mixing.core.records.identifiers import ActionId, ProjectId, Revision, TrackId, VersionRef


@pytest.fixture
def binding():
    return DawTrackBinding(
        VersionRef("session-binding", "1"),
        ProjectId("p1"),
        TrackId("t1"),
        "Disposable project",
        2,
        "Track",
        2.0,
        DawFaderReading(0.7, -4.0),
    )


@pytest.fixture
def state(binding):
    return DawTrackState(binding, Revision("r1"), 2.0, binding.baseline_fader)


@pytest.fixture
def action():
    return Action(ActionId("a1"), ProjectId("p1"), Revision("r1"), SetTrackGain(TrackId("t1"), 1.0))


@pytest.fixture
def resolution(action, state):
    return DawGainResolution(action, state, state.confirmed_fader, -5.0, -1.0)


@pytest.mark.parametrize("value", [True, False, None, "1", float("nan"), float("inf"), -float("inf"), 10**400])
@pytest.mark.parametrize("field", ["normalized", "db"])
def test_reading_rejects_invalid_numbers(value, field):
    with pytest.raises(ValueError):
        replace(DawFaderReading(0.5, 0), **{field: value})


@pytest.mark.parametrize("value", [-0.001, 1.001])
def test_normalized_outside_unit_interval(value):
    with pytest.raises(ValueError):
        DawFaderReading(value, 0)


@pytest.mark.parametrize("value", [0, 1, 0.5])
def test_normalized_endpoints_and_integer_values_are_preserved(value):
    reading = DawFaderReading(value, -4)
    assert reading.normalized is value
    assert type(reading.db) is int


@pytest.mark.parametrize(
    "field,value",
    [
        ("reference", "binding"),
        ("project_id", "p1"),
        ("track_id", "t1"),
        ("project_title", " "),
        ("project_title", None),
        ("insert_name", ""),
        ("insert_name", None),
        ("insert_index", True),
        ("insert_index", 2.0),
        ("insert_index", 0),
        ("insert_index", -1),
        ("baseline_fader", None),
        ("baseline_gain_db", True),
        ("baseline_gain_db", float("inf")),
        ("baseline_gain_db", float("nan")),
        ("baseline_gain_db", "2"),
    ],
)
def test_binding_validates_fields(binding, field, value):
    with pytest.raises(ValueError):
        replace(binding, **{field: value})


def test_insert_index_requires_exact_builtin_int(binding):
    class Index(int):
        pass

    with pytest.raises(ValueError):
        replace(binding, insert_index=Index(2))


@pytest.mark.parametrize(
    "field,value",
    [
        ("binding", None),
        ("revision", "r1"),
        ("confirmed_fader", None),
        ("selected_gain_db", True),
        ("selected_gain_db", float("nan")),
        ("selected_gain_db", float("inf")),
        ("selected_gain_db", "2"),
    ],
)
def test_state_validates_fields(state, field, value):
    with pytest.raises(ValueError):
        replace(state, **{field: value})


def test_confirmed_reading_can_differ_from_ideal_mapping(state):
    measured = replace(state, selected_gain_db=1.0, confirmed_fader=DawFaderReading(0.68, -4.999))
    assert measured.confirmed_fader.db == -4.999
    assert measured.binding is state.binding


@pytest.mark.parametrize(
    "field,value",
    [
        ("action", None),
        ("state", None),
        ("observed_fader", None),
        ("target_fader_db", True),
        ("target_fader_db", float("nan")),
        ("target_fader_db", float("inf")),
        ("target_fader_db", -4.0),
        ("delta_db", True),
        ("delta_db", float("inf")),
        ("delta_db", 0.0),
    ],
)
def test_resolution_rejects_invalid_fields(resolution, field, value):
    with pytest.raises(ValueError):
        replace(resolution, **{field: value})


@pytest.mark.parametrize("field,value", [("project_id", ProjectId("p2")), ("expected_revision", Revision("r2"))])
def test_resolution_rejects_action_reference_mismatch(action, state, field, value):
    with pytest.raises(ValueError):
        DawGainResolution(replace(action, **{field: value}), state, state.confirmed_fader, -5, -1)


def test_resolution_rejects_other_track(action, state):
    with pytest.raises(ValueError):
        DawGainResolution(
            replace(action, operation=SetTrackGain(TrackId("t2"), 1)), state, state.confirmed_fader, -5, -1
        )


def test_resolution_preserves_references_and_exact_formulas(resolution):
    assert resolution.target_fader_db == -5
    assert resolution.delta_db == -1
    assert resolution.observed_fader is resolution.state.confirmed_fader


def test_resolution_rejects_intermediate_overflow(action, state):
    extreme = replace(state, binding=replace(state.binding, baseline_gain_db=-1e308))
    action = replace(action, operation=SetTrackGain(action.operation.track_id, 1e308))
    with pytest.raises(ValueError):
        DawGainResolution(action, extreme, extreme.confirmed_fader, -5, -1)


def test_resolution_rejects_delta_overflow(action, state):
    extreme = replace(state, binding=replace(state.binding, baseline_gain_db=0, baseline_fader=DawFaderReading(0.5, 0)))
    action = replace(action, operation=SetTrackGain(action.operation.track_id, 1e308))
    with pytest.raises(ValueError):
        DawGainResolution(action, extreme, DawFaderReading(0.5, -1e308), 1e308, 0)


@pytest.mark.parametrize("kind", ["reading", "binding", "state", "resolution"])
def test_records_are_frozen(binding, state, resolution, kind):
    record, field = {
        "reading": (state.confirmed_fader, "db"),
        "binding": (binding, "insert_index"),
        "state": (state, "selected_gain_db"),
        "resolution": (resolution, "delta_db"),
    }[kind]
    with pytest.raises(FrozenInstanceError):
        setattr(record, field, 0)


@pytest.mark.parametrize(
    "baseline_gain,baseline_db,selected_gain,confirmed_db,value,observed_db,target,delta",
    [
        (2, -4, 2, -4, 1, -4, -5, -1),
        (2, -4, 1, -5, 0, -5, -6, -1),
        (2, -4, 1, -4.999, 0, -4.999, -6, -1.001),
        (0, 0, -2, -2, -2, -2, -2, 0),
        (0, 0, -2, -2, -1, -2, -1, 1),
    ],
)
def test_absolute_coordinate_examples(
    action, state, baseline_gain, baseline_db, selected_gain, confirmed_db, value, observed_db, target, delta
):
    state = replace(
        state,
        binding=replace(
            state.binding, baseline_gain_db=baseline_gain, baseline_fader=DawFaderReading(0.7, baseline_db)
        ),
        selected_gain_db=selected_gain,
        confirmed_fader=DawFaderReading(0.68, confirmed_db),
    )
    action = replace(action, operation=SetTrackGain(action.operation.track_id, value))
    observed = DawFaderReading(0.68, observed_db)
    result = resolve_daw_gain(action, state=state, observed_fader=observed, normalized_tolerance=0, db_tolerance=0)
    assert result.target_fader_db == target
    assert result.delta_db == pytest.approx(delta)
    assert result.action is action
    assert result.state is state
    assert result.observed_fader is observed


def test_sequential_absolute_actions_and_retries_keep_original_baseline(action, state):
    first = resolve_daw_gain(
        action, state=state, observed_fader=state.confirmed_fader, normalized_tolerance=0, db_tolerance=0
    )
    retry = resolve_daw_gain(
        action, state=state, observed_fader=state.confirmed_fader, normalized_tolerance=0, db_tolerance=0
    )
    assert retry == first
    next_state = replace(state, revision=Revision("r2"), selected_gain_db=1, confirmed_fader=DawFaderReading(0.65, -5))
    next_action = replace(
        action, expected_revision=Revision("r2"), operation=SetTrackGain(action.operation.track_id, 0)
    )
    second = resolve_daw_gain(
        next_action, state=next_state, observed_fader=next_state.confirmed_fader, normalized_tolerance=0, db_tolerance=0
    )
    assert (second.target_fader_db, second.delta_db) == (-6, -1)
    assert next_state.binding is state.binding
    assert state.selected_gain_db == 2
    assert state.confirmed_fader.db == -4


@pytest.mark.parametrize("normalized,db", [(0.75, -4), (0.7, -3.5), (0.75, -3.5)])
def test_external_drift_is_refused(action, state, normalized, db):
    with pytest.raises(ValueError, match="observed"):
        resolve_daw_gain(
            action,
            state=state,
            observed_fader=DawFaderReading(normalized, db),
            normalized_tolerance=0.01,
            db_tolerance=0.1,
        )


@pytest.mark.parametrize("sign", [-1, 1])
def test_both_tolerance_boundaries_are_inclusive(action, state, sign):
    observed = DawFaderReading(0.5 + sign * 0.125, -4 + sign * 0.25)
    state = replace(state, confirmed_fader=DawFaderReading(0.5, -4))
    result = resolve_daw_gain(
        action, state=state, observed_fader=observed, normalized_tolerance=0.125, db_tolerance=0.25
    )
    assert result.delta_db == -1 - sign * 0.25


@pytest.mark.parametrize("field", ["normalized_tolerance", "db_tolerance"])
@pytest.mark.parametrize("value", [True, False, None, "0", -0.01, float("nan"), float("inf"), 10**400])
def test_invalid_tolerances_are_rejected(action, state, field, value):
    arguments = dict(state=state, observed_fader=state.confirmed_fader, normalized_tolerance=0, db_tolerance=0)
    arguments[field] = value
    with pytest.raises(ValueError):
        resolve_daw_gain(action, **arguments)


@pytest.mark.parametrize("field", ["action", "state", "observed_fader"])
def test_resolver_checks_input_types(action, state, field):
    arguments = dict(
        action=action, state=state, observed_fader=state.confirmed_fader, normalized_tolerance=0, db_tolerance=0
    )
    arguments[field] = None
    with pytest.raises(ValueError):
        resolve_daw_gain(**arguments)


@pytest.mark.parametrize("reference", ["project", "track", "revision"])
def test_resolver_rejects_action_reference_mismatch_before_arithmetic(action, state, reference):
    if reference == "project":
        action = replace(action, project_id=ProjectId("other"))
    elif reference == "revision":
        action = replace(action, expected_revision=Revision("other"))
    else:
        action = replace(action, operation=SetTrackGain(TrackId("other"), 1))
    # Overflowing mapping would hide the reference failure if evaluated first.
    state = replace(state, binding=replace(state.binding, baseline_gain_db=-1e308))
    action = replace(action, operation=replace(action.operation, value_db=1e308))
    with pytest.raises(ValueError, match=reference):
        resolve_daw_gain(
            action, state=state, observed_fader=state.confirmed_fader, normalized_tolerance=0, db_tolerance=0
        )


@pytest.mark.parametrize("case", ["target", "delta", "observed_difference"])
def test_resolver_rejects_nonfinite_arithmetic(action, state, case):
    if case == "target":
        state = replace(state, binding=replace(state.binding, baseline_gain_db=-1e308))
        action = replace(action, operation=replace(action.operation, value_db=1e308))
        observed = state.confirmed_fader
    else:
        state = replace(
            state,
            binding=replace(state.binding, baseline_gain_db=0, baseline_fader=DawFaderReading(0.7, 0)),
            confirmed_fader=DawFaderReading(0.7, -1e308 if case == "delta" else 1e308),
        )
        action = replace(action, operation=replace(action.operation, value_db=1e308))
        observed = DawFaderReading(0.7, -1e308)
    with pytest.raises(ValueError):
        resolve_daw_gain(action, state=state, observed_fader=observed, normalized_tolerance=0, db_tolerance=0)


def test_resolver_requires_explicit_keyword_arguments(action, state):
    with pytest.raises(TypeError):
        resolve_daw_gain(action, state, state.confirmed_fader, 0, 0)
    with pytest.raises(TypeError):
        resolve_daw_gain(action, state=state, observed_fader=state.confirmed_fader)


@pytest.mark.parametrize("case", ["target", "delta"])
def test_resolution_large_integer_arithmetic_raises_value_error(action, state, case):
    large = 10**308
    binding = replace(
        state.binding, baseline_gain_db=0, baseline_fader=DawFaderReading(0.5, large if case == "target" else 0)
    )
    state = replace(state, binding=binding)
    action = replace(action, operation=replace(action.operation, value_db=large))
    observed = DawFaderReading(0.5, 0 if case == "target" else -large)
    with pytest.raises(ValueError):
        DawGainResolution(action, state, observed, 0, 0)


@pytest.mark.parametrize("case", ["target", "delta", "drift"])
def test_resolver_large_integer_arithmetic_raises_value_error(action, state, case):
    large = 10**308
    baseline = large if case == "target" else 0
    observed_db = 0 if case == "target" else -large
    confirmed_db = large if case == "drift" else observed_db
    state = replace(
        state,
        binding=replace(state.binding, baseline_gain_db=0, baseline_fader=DawFaderReading(0.5, baseline)),
        confirmed_fader=DawFaderReading(0.5, confirmed_db),
    )
    action = replace(action, operation=replace(action.operation, value_db=large))
    with pytest.raises(ValueError):
        resolve_daw_gain(
            action,
            state=state,
            observed_fader=DawFaderReading(0.5, observed_db),
            normalized_tolerance=0,
            db_tolerance=0,
        )
