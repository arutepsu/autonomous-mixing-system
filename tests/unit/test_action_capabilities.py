"""Action capability records and the pure capability-view builder (action-capabilities plan, 2B).

All records are metadata fixtures. Gain values are software-boundary fixtures, not musical thresholds or
default bounds; track names deliberately carry no role. A capability view is not execution permission.
"""

import dataclasses
import inspect
import math
from datetime import datetime, timezone

import pytest

from autonomous_mixing.core.admission.capabilities import build_action_capability_view
from autonomous_mixing.core.records.admission import ActionAdmissionPolicy, TrackGainPermission
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.capabilities import (
    ActionCapabilityView,
    AvailableTrackGain,
    GainBounds,
    RendererCapabilities,
)
from autonomous_mixing.core.records.context import AssertionSource, MusicalContext, TrackRole
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    CheckpointId,
    ContextId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.observation import AnalysisScope, TimeRange
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
)

UTC_NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
POLICY_REF = VersionRef("policy", "1")
RENDERER_REF = VersionRef("renderer", "1")


def _scope(tracks=("t1", "t2", "t3"), start=0.0, end=60.0) -> AnalysisScope:
    return AnalysisScope(tuple(TrackId(t) for t in tracks), TimeRange(start, end))


def _gain(track, low, high) -> AvailableTrackGain:
    return AvailableTrackGain(TrackId(track), GainBounds(low, high))


def _view(gain_actions=(), *, scope=None, **overrides) -> ActionCapabilityView:
    values = dict(
        project_id=ProjectId("p1"),
        revision=Revision("r2"),
        context_id=ContextId("ctx"),
        context_version="v1",
        policy_reference=POLICY_REF,
        renderer_reference=RENDERER_REF,
        scope=_scope() if scope is None else scope,
        gain_actions=gain_actions,
    )
    return ActionCapabilityView(**{**values, **overrides})


# --- records ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "fields"),
    [
        (GainBounds, [("minimum_db", float), ("maximum_db", float)]),
        (RendererCapabilities, [("reference", VersionRef), ("gain_bounds", GainBounds | None)]),
        (AvailableTrackGain, [("track_id", TrackId), ("bounds", GainBounds)]),
        (
            ActionCapabilityView,
            [
                ("project_id", ProjectId),
                ("revision", Revision),
                ("context_id", ContextId),
                ("context_version", str),
                ("policy_reference", VersionRef),
                ("renderer_reference", VersionRef),
                ("scope", AnalysisScope),
                ("gain_actions", tuple[AvailableTrackGain, ...]),
            ],
        ),
    ],
)
def test_record_fields_are_exact_and_required(record, fields):
    assert [(f.name, f.type) for f in dataclasses.fields(record)] == fields
    assert all(
        f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
        for f in dataclasses.fields(record)
    )


@pytest.mark.parametrize(
    ("instance", "field", "value"),
    [
        (GainBounds(-1.0, 1.0), "maximum_db", 2.0),
        (RendererCapabilities(RENDERER_REF, None), "gain_bounds", GainBounds(0.0, 1.0)),
        (_gain("t1", 0.0, 1.0), "track_id", TrackId("t2")),
        (_view(), "gain_actions", ()),
    ],
)
def test_records_are_frozen(instance, field, value):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field, value)


@pytest.mark.parametrize(
    ("low", "high"), [(-6.0, 2.0), (0.0, 0.0), (-3.5, -3.5), (-12, 6), (-0.1, 0.30000000000000004)]
)
def test_gain_bounds_accept_ordered_finite_values_unchanged(low, high):
    bounds = GainBounds(low, high)
    assert (bounds.minimum_db, bounds.maximum_db) == (low, high)
    assert type(bounds.minimum_db) is type(low) and type(bounds.maximum_db) is type(high)


@pytest.mark.parametrize(
    ("low", "high"),
    [
        (1.0, 0.0),
        (0.0, -1e-12),
        (True, 1.0),
        (0.0, False),
        ("0", 1.0),
        (None, 1.0),
        (math.nan, 1.0),
        (0.0, math.inf),
        (-math.inf, 0.0),
        (-(10**400), 0.0),
    ],
)
def test_gain_bounds_reject_invalid_values(low, high):
    with pytest.raises(ValueError):
        GainBounds(low, high)


def test_renderer_capabilities_accept_declared_or_absent_gain_support():
    assert RendererCapabilities(RENDERER_REF, None).gain_bounds is None
    bounds = GainBounds(-6.0, 2.0)
    assert RendererCapabilities(RENDERER_REF, bounds).gain_bounds is bounds


@pytest.mark.parametrize(
    ("reference", "bounds"),
    [
        (("renderer", "1"), None),
        ("renderer", None),
        (RENDERER_REF, (-6.0, 2.0)),
        (RENDERER_REF, _gain("t1", 0.0, 1.0)),
    ],
)
def test_renderer_capabilities_reject_wrong_types(reference, bounds):
    with pytest.raises(ValueError):
        RendererCapabilities(reference, bounds)


@pytest.mark.parametrize(("track", "bounds"), [("t1", GainBounds(0.0, 1.0)), (TrackId("t1"), (0.0, 1.0))])
def test_available_track_gain_rejects_wrong_types(track, bounds):
    with pytest.raises(ValueError):
        AvailableTrackGain(track, bounds)


def test_view_accepts_empty_and_scoped_gain_actions():
    assert _view().gain_actions == ()
    actions = (_gain("t3", 0.0, 2.0), _gain("t1", -6.0, -2.0))
    assert _view(actions).gain_actions == actions


@pytest.mark.parametrize(
    "overrides",
    [
        {"project_id": "p1"},
        {"revision": "r2"},
        {"context_id": "ctx"},
        {"context_version": ""},
        {"context_version": "  "},
        {"context_version": 1},
        {"policy_reference": "policy"},
        {"renderer_reference": None},
        {"scope": TimeRange(0.0, 60.0)},
        {"gain_actions": [_gain("t1", 0.0, 1.0)]},
        {"gain_actions": (GainBounds(0.0, 1.0),)},
        {"gain_actions": (_gain("t1", 0.0, 1.0), _gain("t1", -1.0, 0.0))},
        {"gain_actions": (_gain("t4", 0.0, 1.0),)},
    ],
    ids=[
        "project",
        "revision",
        "context",
        "version-empty",
        "version-blank",
        "version-type",
        "policy-ref",
        "renderer-ref",
        "scope",
        "list",
        "element-type",
        "duplicate-track",
        "out-of-scope",
    ],
)
def test_view_rejects_invalid_fields(overrides):
    with pytest.raises(ValueError):
        _view(**overrides)


# --- build_action_capability_view: fixtures --------------------------------------------------------


def _state(project="p1", revision="r2") -> ProjectState:
    tracks = tuple(Track(TrackId(f"t{i}"), name) for i, name in enumerate(("Field recording", "Choir", "Tape"), 1))
    snapshot = ProjectSnapshot(ProjectId(project), Revision(revision), UTC_NOW, tracks)
    processing = ProjectProcessingState(ProjectId(project), Revision(revision), ())
    mix = AudioArtifact(
        ArtifactId("mix"),
        ProjectId(project),
        Revision(revision),
        (TrackId("t1"), TrackId("t2"), TrackId("t3")),
        ArtifactKind.MIX,
        "outputs/mix.wav",
        0.0,
        48000,
        2,
        48000 * 60,
        "d" * 64,
        VersionRef("render", "1"),
    )
    audio = ArtifactCheckpoint(CheckpointId("cp"), ProjectId(project), Revision(revision), (mix,))
    return ProjectState(snapshot, processing, audio)


def _context(protected=("t2",), roles=()) -> MusicalContext:
    source = AssertionSource("producer", UTC_NOW)
    return MusicalContext(
        ContextId("ctx"),
        "v1",
        tuple(TrackRole(TrackId(t), role, source) for t, role in roles),
        (),
        frozenset(TrackId(t) for t in protected),
    )


DEFAULT_PERMISSIONS = (("t2", -4.0, 4.0), ("t1", -8.0, -2.0), ("t3", 0.0, 5.0))


def _policy(permissions=DEFAULT_PERMISSIONS, project="p1") -> ActionAdmissionPolicy:
    return ActionAdmissionPolicy(
        POLICY_REF, ProjectId(project), tuple(TrackGainPermission(TrackId(t), lo, hi) for t, lo, hi in permissions)
    )


def _renderer(bounds=(-6.0, 2.0)) -> RendererCapabilities:
    return RendererCapabilities(RENDERER_REF, None if bounds is None else GainBounds(*bounds))


def _build(*, state=None, scope=None, context=None, policy=None, renderer=None) -> ActionCapabilityView:
    return build_action_capability_view(
        state=_state() if state is None else state,
        scope=_scope(("t3", "t1", "t2")) if scope is None else scope,
        context=_context() if context is None else context,
        policy=_policy() if policy is None else policy,
        renderer=_renderer() if renderer is None else renderer,
    )


def _gains(view: ActionCapabilityView) -> list[tuple[str, float, float]]:
    return [(str(a.track_id), a.bounds.minimum_db, a.bounds.maximum_db) for a in view.gain_actions]


# --- build_action_capability_view: behavior --------------------------------------------------------


def test_builder_signature_is_exact():
    params = inspect.signature(build_action_capability_view).parameters.values()
    assert [(p.name, p.kind) for p in params] == [
        (name, inspect.Parameter.KEYWORD_ONLY) for name in ("state", "scope", "context", "policy", "renderer")
    ]


def test_reference_example_intersects_in_scope_order_and_omits_protected_tracks():
    assert _gains(_build()) == [("t3", 0.0, 2.0), ("t1", -6.0, -2.0)]


def test_protected_track_is_omitted_even_when_policy_and_renderer_permit_it():
    unprotected = _build(context=_context(protected=()))
    assert _gains(unprotected) == [("t3", 0.0, 2.0), ("t1", -6.0, -2.0), ("t2", -4.0, 2.0)]
    assert "t2" not in [track for track, _, _ in _gains(_build())]


def test_touching_intervals_yield_one_exact_setting():
    view = _build(policy=_policy((("t1", -2.0, 0.0),)), renderer=_renderer((0.0, 3.0)), context=_context(()))
    assert _gains(view) == [("t1", 0.0, 0.0)]


def test_disjoint_intervals_omit_the_track_instead_of_clamping():
    view = _build(policy=_policy((("t1", -8.0, -6.5), ("t3", 0.0, 1.0))), context=_context(()))
    assert _gains(view) == [("t3", 0.0, 1.0)]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"renderer": _renderer(None)},
        {"policy": _policy(())},
        {"context": _context(("t1", "t2", "t3"))},
        {"policy": _policy((("t1", 3.0, 4.0), ("t3", 2.5, 9.0))), "context": _context(())},
    ],
    ids=["no-gain-support", "no-permissions", "all-protected", "all-disjoint"],
)
def test_empty_views_keep_their_provenance(kwargs):
    view = _build(**kwargs)
    assert view.gain_actions == ()
    assert (view.project_id, view.revision, view.context_id, view.context_version) == (
        ProjectId("p1"),
        Revision("r2"),
        ContextId("ctx"),
        "v1",
    )
    assert (view.policy_reference, view.renderer_reference, view.scope) == (
        POLICY_REF,
        RENDERER_REF,
        _scope(("t3", "t1", "t2")),
    )


def test_known_permission_outside_scope_is_omitted():
    assert _gains(_build(scope=_scope(("t1",)))) == [("t1", -6.0, -2.0)]


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"renderer": _renderer(None)}, {"scope": _scope(("t2",))}],
    ids=["ordinary", "no-gain-support", "otherwise-empty"],
)
def test_unknown_permission_track_fails_even_outside_scope(kwargs):
    policy = _policy((("t1", -8.0, -2.0), ("t9", -1.0, 1.0)))
    with pytest.raises(ValueError, match="t9"):
        _build(policy=policy, **kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"scope": _scope(("t1", "t9"))},
        {"context": _context(("t9",))},
        {"context": _context((), roles=(("t9", "declared role"),))},
        {"policy": _policy(project="p2")},
    ],
    ids=["scope-track", "protected-track", "role-track", "policy-project"],
)
def test_unknown_references_and_foreign_policy_fail(kwargs):
    with pytest.raises(ValueError):
        _build(**kwargs)


def test_declared_roles_do_not_change_availability():
    assert _gains(_build(context=_context(roles=(("t1", "declared role"),)))) == _gains(_build())


def test_provenance_survives_exactly_and_inputs_stay_unchanged():
    state, context, policy, renderer = _state(revision="r7"), _context(), _policy(), _renderer()
    scope = _scope(("t3", "t1", "t2"), 1.5, 30.25)
    before = [dataclasses.astuple(x) for x in (state, context, policy, renderer, scope)]
    view = build_action_capability_view(state=state, scope=scope, context=context, policy=policy, renderer=renderer)
    assert (view.project_id, view.revision) == (state.project_id, state.revision)
    assert (view.context_id, view.context_version) == (context.context_id, context.version)
    assert view.policy_reference is policy.reference and view.renderer_reference is renderer.reference
    assert view.scope is scope
    assert [dataclasses.astuple(x) for x in (state, context, policy, renderer, scope)] == before


def test_decimal_bounds_are_selected_exactly():
    policy = _policy((("t1", -0.1, 0.30000000000000004), ("t3", -0.7, 0.2)))
    view = _build(policy=policy, renderer=_renderer((-0.30000000000000004, 0.1)), context=_context(()))
    assert _gains(view) == [("t3", -0.30000000000000004, 0.1), ("t1", -0.1, 0.1)]


def test_integer_bounds_are_preserved_without_conversion():
    view = _build(policy=_policy((("t1", -8, -2),)), renderer=_renderer((-6, 2)), context=_context(()))
    (action,) = view.gain_actions
    assert (action.bounds.minimum_db, action.bounds.maximum_db) == (-6, -2)
    assert type(action.bounds.minimum_db) is int


def test_reordered_policy_yields_the_same_scope_ordered_view():
    assert _build(policy=_policy(tuple(reversed(DEFAULT_PERMISSIONS)))) == _build()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"state": _state().snapshot},
        {"scope": TimeRange(0.0, 60.0)},
        {"context": "ctx"},
        {"policy": _renderer()},
        {"renderer": GainBounds(-6.0, 2.0)},
    ],
    ids=["state", "scope", "context", "policy", "renderer"],
)
def test_wrong_input_types_raise(kwargs):
    with pytest.raises(ValueError):
        _build(**kwargs)
