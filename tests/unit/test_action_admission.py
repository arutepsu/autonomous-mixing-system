"""Focused tests for the explicit gain-action admission policy (Task 3B).

Permission bounds and gain values are explicit test configuration, not recommended or researched limits.
"""

import ast
import copy
import dataclasses
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autonomous_mixing.application.admission import (
    ActionAdmissionPolicy,
    ActionAdmissionRequest,
    AdmissionRejectionCode,
    AdmittedAction,
    RejectedAction,
    TrackGainPermission,
    admit_action,
)
from autonomous_mixing.application.decision import DecisionRequest
from autonomous_mixing.domain.action import Action, SetTrackGain
from autonomous_mixing.domain.context import AssertionSource, MusicalContext, TrackRole
from autonomous_mixing.domain.decision import Abstain, AbstentionReason, ControllerIdentity, Decision, ProposeAction
from autonomous_mixing.domain.observation import MeasuredScalar, Observation
from autonomous_mixing.domain.project import (
    AnalysisScope,
    ArtifactCheckpoint,
    ArtifactKind,
    AudioArtifact,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    TimeRange,
    Track,
    TrackGainState,
)
from autonomous_mixing.domain.run import BudgetLimits, BudgetUsage
from autonomous_mixing.domain.shared import (
    ActionId,
    ArtifactId,
    CheckpointId,
    ContextId,
    DecisionId,
    ObservationId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)

UTC_NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
CONTROLLER = ControllerIdentity("deterministic-baseline", "0.1")
POLICY_REF = VersionRef("admission-policy", "1")
_D = object()


def _snapshot(project="p1", revision="r2") -> ProjectSnapshot:
    names = ("Field recording", "Choir", "Tape loop")
    tracks = tuple(Track(TrackId(f"t{i}"), name) for i, name in enumerate(names, start=1))
    return ProjectSnapshot(ProjectId(project), Revision(revision), UTC_NOW, tracks)


def _artifact(aid, tracks, revision="r2", project="p1") -> AudioArtifact:
    return AudioArtifact(
        ArtifactId(aid),
        ProjectId(project),
        Revision(revision),
        tuple(TrackId(t) for t in tracks),
        ArtifactKind.MIX,
        f"outputs/{aid}.wav",
        0.0,
        48000,
        2,
        48000 * 60,
        "e" * 64,
        VersionRef("render", "1"),
    )


def _context(protected=frozenset(), roles=None) -> MusicalContext:
    source = AssertionSource("producer", UTC_NOW)
    assignments = roles if roles is not None else [("t1", "ambience"), ("t2", "lead"), ("t3", "texture")]
    track_roles = tuple(TrackRole(TrackId(t), role, source) for t, role in assignments)
    return MusicalContext(ContextId("c1"), "1", track_roles, (), frozenset(TrackId(t) for t in protected))


def _decision_request(protected=frozenset(), roles=None, project="p1", revision="r2") -> DecisionRequest:
    snapshot = _snapshot(project, revision)
    mix = _artifact("a-mix", ("t1", "t2", "t3"), revision, project)
    checkpoint = ArtifactCheckpoint(CheckpointId("cp"), ProjectId(project), Revision(revision), (mix,))
    # Explicit 0 dB gain states on fixed input stems; admission itself never reads processing state.
    gains = tuple(
        TrackGainState(
            TrackId(t),
            dataclasses.replace(
                _artifact(f"in-{t}", (t,), "r0", project), kind=ArtifactKind.STEM, source_track_ids=(TrackId(t),)
            ),
            0.0,
        )
        for t in ("t1", "t2", "t3")
    )
    state = ProjectState(snapshot, ProjectProcessingState(ProjectId(project), Revision(revision), gains), checkpoint)
    observation = Observation(
        ObservationId("o1"),
        VersionRef("metric-a", "1"),
        (ArtifactId("a-mix"),),
        AnalysisScope((TrackId("t1"), TrackId("t3")), TimeRange(0.0, 60.0)),
        MeasuredScalar(-14.0, "unit-a"),
        VersionRef("spec", "1"),
        VersionRef("analyzer", "0.1"),
        UTC_NOW,
    )
    return DecisionRequest(
        state,
        _context(protected, roles),
        CONTROLLER,
        (observation,),
        BudgetUsage(1, 0, 1, 0, 2.0),
        BudgetLimits(4, 2, 6, 1, 300.0),
    )


def _decision(
    value_db=-3.0,
    target="t2",
    *,
    evidence=("o1",),
    revision="r2",
    project="p1",
    context=("c1", "1"),
    controller=CONTROLLER,
    outcome=_D,
) -> Decision:
    if outcome is _D:
        action = Action(ActionId("x1"), ProjectId(project), Revision(revision), SetTrackGain(TrackId(target), value_db))
        outcome = ProposeAction(action, "set track gain")
    return Decision(
        DecisionId("d1"),
        ProjectId(project),
        Revision(revision),
        ContextId(context[0]),
        context[1],
        tuple(ObservationId(o) for o in evidence),
        "structured rationale",
        controller,
        outcome,
    )


def _policy(*permissions, project="p1") -> ActionAdmissionPolicy:
    return ActionAdmissionPolicy(POLICY_REF, ProjectId(project), tuple(permissions))


def _permit(track="t2", low=-12.0, high=0.0) -> TrackGainPermission:
    return TrackGainPermission(TrackId(track), low, high)


def _admit(decision=None, policy=None, decision_request=None):
    request = ActionAdmissionRequest(
        decision_request if decision_request is not None else _decision_request(),
        decision if decision is not None else _decision(),
        policy if policy is not None else _policy(_permit()),
    )
    return admit_action(request)


def _rejected(result, code: AdmissionRejectionCode) -> None:
    assert isinstance(result, RejectedAction), result
    assert result.code is code, (result.code, result.explanation)
    assert result.decision_id == DecisionId("d1")


# --- admission -----------------------------------------------------------------------


def test_valid_gain_proposal_is_admitted_unchanged():
    decision = _decision(-3.0)
    result = _admit(decision)
    assert isinstance(result, AdmittedAction)
    assert result.action is decision.outcome.action
    assert result.action.operation.value_db == -3.0
    assert result.policy_reference == POLICY_REF


@pytest.mark.parametrize("value_db", [-12.0, 0.0, -12, 0, -6.5])
def test_bounds_are_inclusive(value_db):
    result = _admit(_decision(value_db))
    assert isinstance(result, AdmittedAction)
    assert result.action.operation.value_db == value_db


def test_equal_bounds_permit_exactly_one_setting():
    policy = _policy(_permit(low=-4.5, high=-4.5))
    assert isinstance(_admit(_decision(-4.5), policy), AdmittedAction)
    _rejected(_admit(_decision(-4.4), policy), AdmissionRejectionCode.OUT_OF_BOUNDS)


@pytest.mark.parametrize("value_db", [-12.0001, 0.0001, 6.0, -60.0])
def test_out_of_bounds_values_are_rejected_without_clamping(value_db):
    decision = _decision(value_db)
    result = _admit(decision)
    _rejected(result, AdmissionRejectionCode.OUT_OF_BOUNDS)
    assert decision.outcome.action.operation.value_db == value_db
    assert str(value_db) in result.explanation


def test_empty_policy_denies_every_gain_action():
    _rejected(_admit(policy=_policy()), AdmissionRejectionCode.NO_GAIN_PERMISSION)


def test_permission_for_another_track_does_not_authorize_the_target():
    _rejected(_admit(policy=_policy(_permit("t1"), _permit("t3"))), AdmissionRejectionCode.NO_GAIN_PERMISSION)


def test_protected_track_is_rejected_even_with_permission():
    result = _admit(decision_request=_decision_request(protected={"t2"}))
    _rejected(result, AdmissionRejectionCode.PROTECTED_TRACK)


def test_protection_of_other_tracks_does_not_block_the_target():
    assert isinstance(_admit(decision_request=_decision_request(protected={"t1", "t3"})), AdmittedAction)


@pytest.mark.parametrize(
    "decision",
    [
        _decision(target="t9"),
        _decision(revision="r1"),
        _decision(evidence=("o1", "o-missing")),
        _decision(context=("c9", "1")),
        _decision(context=("c1", "2")),
        _decision(controller=ControllerIdentity("other-controller", "1")),
    ],
    ids=["unknown-track", "stale-revision", "missing-evidence", "wrong-context", "wrong-context-version", "controller"],
)
def test_reference_failures_become_invalid_references(decision):
    result = _admit(decision)
    _rejected(result, AdmissionRejectionCode.INVALID_REFERENCES)
    assert result.explanation.strip()


def test_policy_for_another_project_is_rejected():
    _rejected(_admit(policy=_policy(_permit(), project="p9")), AdmissionRejectionCode.POLICY_PROJECT_MISMATCH)


def test_valid_abstention_has_no_action_to_admit():
    abstention = _decision(outcome=Abstain(AbstentionReason.MISSING_CONTEXT, ("role of t2",)), evidence=())
    _rejected(_admit(abstention), AdmissionRejectionCode.NOT_AN_ACTION)


@pytest.mark.parametrize(
    "decision, policy, decision_request, code",
    [
        # invalid references win over everything else
        (
            _decision(9.0, revision="r1"),
            _policy(project="p9"),
            _decision_request(protected={"t2"}),
            "INVALID_REFERENCES",
        ),
        # project mismatch wins over abstention, protection, permission and bounds
        (
            _decision(outcome=Abstain(AbstentionReason.NO_JUSTIFIED_ACTION), evidence=()),
            _policy(project="p9"),
            None,
            "POLICY_PROJECT_MISMATCH",
        ),
        (_decision(9.0), _policy(project="p9"), _decision_request(protected={"t2"}), "POLICY_PROJECT_MISMATCH"),
        # protection wins over missing permission and bounds
        (_decision(9.0), _policy(), _decision_request(protected={"t2"}), "PROTECTED_TRACK"),
        # missing permission wins over bounds
        (_decision(9.0), _policy(_permit("t1")), None, "NO_GAIN_PERMISSION"),
    ],
    ids=[
        "references-first",
        "project-before-abstention",
        "project-before-protection",
        "protection-before-permission",
        "permission-before-bounds",
    ],
)
def test_rejection_precedence_is_deterministic(decision, policy, decision_request, code):
    _rejected(_admit(decision, policy, decision_request), AdmissionRejectionCode[code])


def test_names_and_roles_do_not_affect_authorization():
    roles = [("t1", "kick"), ("t2", "bass"), ("t3", "lead vocal")]
    result = _admit(decision_request=_decision_request(roles=roles))
    assert isinstance(result, AdmittedAction)
    _rejected(
        _admit(decision_request=_decision_request(roles=roles), policy=_policy()),
        AdmissionRejectionCode.NO_GAIN_PERMISSION,
    )


def test_target_may_differ_from_observed_tracks():
    # The fixture observation covers t1 and t3 only; evidence sufficiency is not admission's responsibility.
    assert isinstance(_admit(_decision(target="t2")), AdmittedAction)


def test_admission_does_not_mutate_request_or_action():
    decision_request, decision, policy = _decision_request(), _decision(-3.0), _policy(_permit())
    request = ActionAdmissionRequest(decision_request, decision, policy)
    before = copy.deepcopy(request)
    admit_action(request)
    admit_action(ActionAdmissionRequest(decision_request, _decision(9.0), policy))
    assert request == before
    assert decision.outcome.action.operation.value_db == -3.0


def test_programming_errors_are_not_converted_into_rejections(monkeypatch):
    # Patch the global that admit_action actually resolves: its implementation module, not the interface.
    import autonomous_mixing.application.admission.admission as admission

    def broken(request, response):
        raise TypeError("programming error")

    monkeypatch.setattr(admission, "validate_decision_response", broken)
    with pytest.raises(TypeError):
        _admit()


# --- records -------------------------------------------------------------------------


def test_rejection_codes_are_exactly_the_specified_set():
    assert {code.name for code in AdmissionRejectionCode} == {
        "INVALID_REFERENCES",
        "POLICY_PROJECT_MISMATCH",
        "NOT_AN_ACTION",
        "PROTECTED_TRACK",
        "NO_GAIN_PERMISSION",
        "OUT_OF_BOUNDS",
    }


@pytest.mark.parametrize(
    "track, low, high",
    [
        ("t1", -1.0, 0.0),
        (TrackId("t1"), 1.0, 0.0),
        (TrackId("t1"), True, 0.0),
        (TrackId("t1"), -1.0, False),
        (TrackId("t1"), "-1", 0.0),
        (TrackId("t1"), -math.inf, 0.0),
        (TrackId("t1"), -1.0, math.nan),
        (TrackId("t1"), None, 0.0),
    ],
)
def test_gain_permission_rejects_invalid_bounds_and_types(track, low, high):
    with pytest.raises(ValueError):
        TrackGainPermission(track, low, high)


@pytest.mark.parametrize(
    "reference, project, permissions",
    [
        ("admission-policy@1", ProjectId("p1"), ()),
        (POLICY_REF, "p1", ()),
        (POLICY_REF, ProjectId("p1"), [_permit()]),
        (POLICY_REF, ProjectId("p1"), (_permit(), _permit(low=-1.0))),
        (POLICY_REF, ProjectId("p1"), (("t2", -1.0, 0.0),)),
    ],
    ids=["reference", "project", "mutable-permissions", "duplicate-track", "untyped-permission"],
)
def test_policy_rejects_wrong_types_mutable_collections_and_duplicate_permissions(reference, project, permissions):
    with pytest.raises(ValueError):
        ActionAdmissionPolicy(reference, project, permissions)


def test_admission_request_checks_field_types_only():
    mismatched = ActionAdmissionRequest(_decision_request(), _decision(revision="r1"), _policy(project="p9"))
    assert isinstance(mismatched, ActionAdmissionRequest)
    for args in [
        (None, _decision(), _policy()),
        (_decision_request(), _decision().outcome, _policy()),
        (_decision_request(), _decision(), (_permit(),)),
    ]:
        with pytest.raises(ValueError):
            ActionAdmissionRequest(*args)


def test_result_records_validate_types_and_explanation():
    action = _decision().outcome.action
    assert AdmittedAction(action, POLICY_REF).action is action
    with pytest.raises(ValueError):
        AdmittedAction(_decision().outcome, POLICY_REF)
    with pytest.raises(ValueError):
        AdmittedAction(action, "admission-policy@1")
    for explanation in ("", "   ", None):
        with pytest.raises(ValueError):
            RejectedAction(DecisionId("d1"), AdmissionRejectionCode.OUT_OF_BOUNDS, explanation)
    with pytest.raises(ValueError):
        RejectedAction("d1", AdmissionRejectionCode.OUT_OF_BOUNDS, "out of bounds")
    with pytest.raises(ValueError):
        RejectedAction(DecisionId("d1"), "OUT_OF_BOUNDS", "out of bounds")


def test_records_are_immutable():
    permission, policy = _permit(), _policy(_permit())
    with pytest.raises(dataclasses.FrozenInstanceError):
        permission.maximum_db = 12.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.gain_permissions = ()
    assert isinstance(policy.gain_permissions, tuple)


# --- architecture --------------------------------------------------------------------


def test_admission_module_imports_only_stdlib_application_and_component_interfaces():
    # The recursive real-tree checks in test_domain_component_boundaries.py and
    # test_application_infrastructure_boundaries.py also scan this package.
    package_dir = Path(__file__).resolve().parents[2] / "src" / "autonomous_mixing" / "application" / "admission"
    paths = sorted(package_dir.glob("*.py"))
    assert {path.name for path in paths} == {"__init__.py", "policy.py", "contracts.py", "admission.py"}
    components = {"shared", "project", "context", "observation", "action", "decision", "validation", "run"}
    nodes = [node for path in paths for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))]
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, "use absolute imports"
            name = node.module or ""
        elif isinstance(node, ast.Import):
            name = node.names[0].name
        else:
            continue
        parts = name.split(".")
        allowed = (
            parts[0] in sys.stdlib_module_names
            or (parts[:2] == ["autonomous_mixing", "application"])
            or (len(parts) == 3 and parts[:2] == ["autonomous_mixing", "domain"] and parts[2] in components)
            # ADR-027 migration step M1: records moved to the core records package.
            or (len(parts) == 4 and parts[:3] == ["autonomous_mixing", "core", "records"])
        )
        assert allowed, name
