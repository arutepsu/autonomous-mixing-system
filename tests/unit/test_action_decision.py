"""Focused tests for typed action proposals and decision outcomes (Task 2A).

Gain values and identifiers are arbitrary structural fixtures, not mixing parameters or limits.
"""

import dataclasses
import math
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from autonomous_mixing.domain.action import Action, SetTrackGain
from autonomous_mixing.domain.context import AssertionSource, MusicalContext, TrackRole
from autonomous_mixing.domain.decision import (
    Abstain,
    AbstentionReason,
    ControllerIdentity,
    Decision,
    ProposeAction,
    validate_decision_references,
)
from autonomous_mixing.domain.observation import MeasuredScalar, Observation
from autonomous_mixing.domain.project import AnalysisScope, ProjectSnapshot, TimeRange, Track
from autonomous_mixing.domain.shared import (
    ActionId,
    ArtifactId,
    ContextId,
    DecisionId,
    ObservationId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)

UTC_NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
INVALID_NUMBERS = [True, False, math.nan, math.inf, -math.inf, "-3.0", None]


def _snapshot() -> ProjectSnapshot:
    names = ("Field recording", "Choir", "Tape loop")
    tracks = tuple(Track(TrackId(f"t{i}"), name) for i, name in enumerate(names, start=1))
    return ProjectSnapshot(ProjectId("p1"), Revision("r1"), UTC_NOW, tracks)


def _context(context_id: str = "c1", version: str = "1", protected: frozenset = frozenset()) -> MusicalContext:
    source = AssertionSource("producer", UTC_NOW)
    assignments = [("t1", "ambience"), ("t2", "lead"), ("t3", "texture")]
    roles = tuple(TrackRole(TrackId(t), role, source) for t, role in assignments)
    return MusicalContext(ContextId(context_id), version, roles, (), protected)


def _observation(observation_id: str = "o1", track_ids: tuple[str, ...] = ("t1", "t2")) -> Observation:
    return Observation(
        observation_id=ObservationId(observation_id),
        metric=VersionRef("metric", "1"),
        artifact_ids=(ArtifactId("a1"),),
        scope=AnalysisScope(tuple(TrackId(t) for t in track_ids), TimeRange(0.0, 4.0)),
        result=MeasuredScalar(-18.0, "dBFS"),
        analysis_spec=VersionRef("spec", "1"),
        analyzer=VersionRef("analyzer", "0.1"),
        created_at=UTC_NOW,
    )


def _action(track_id: str = "t2", value_db: float = -3.0, *, project_id: str = "p1", revision: str = "r1") -> Action:
    return Action(ActionId("x1"), ProjectId(project_id), Revision(revision), SetTrackGain(TrackId(track_id), value_db))


def _decision(outcome, evidence: tuple[str, ...] = ("o1",), **overrides) -> Decision:
    fields = dict(
        decision_id=DecisionId("d1"),
        project_id=ProjectId("p1"),
        expected_revision=Revision("r1"),
        context_id=ContextId("c1"),
        context_version="1",
        evidence_ids=tuple(ObservationId(o) for o in evidence),
        rationale="structured rationale",
        controller=ControllerIdentity("deterministic-baseline", "0.1"),
        outcome=outcome,
    )
    fields.update(overrides)
    return Decision(**fields)


def _proposal(**action_kwargs) -> ProposeAction:
    return ProposeAction(_action(**action_kwargs), "lower the lead track level")


# --- identifiers and actions -----------------------------------------------------


@pytest.mark.parametrize("cls", [ActionId, DecisionId])
@pytest.mark.parametrize("raw", ["", "  ", 1, None])
def test_new_identifiers_reject_blank_and_non_string(cls, raw):
    with pytest.raises(ValueError):
        cls(raw)


def test_new_identifiers_are_distinct_kinds():
    assert ActionId("x") != DecisionId("x")
    assert DecisionId("x") != ObservationId("x")


@pytest.mark.parametrize("value", [0, -6, 2.5, -0.0])
def test_set_track_gain_holds_absolute_finite_value_unchanged(value):
    operation = SetTrackGain(TrackId("t1"), value)
    assert operation.value_db == value
    assert type(operation.value_db) is type(value)
    assert not hasattr(operation, "delta_db")


@pytest.mark.parametrize("value", INVALID_NUMBERS)
def test_set_track_gain_rejects_nonfinite_boolean_and_string_values(value):
    with pytest.raises(ValueError):
        SetTrackGain(TrackId("t1"), value)


def test_set_track_gain_requires_track_id():
    with pytest.raises(ValueError):
        SetTrackGain("t1", -3.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"action_id": "x1"},
        {"project_id": "p1"},
        {"expected_revision": "r1"},
        {"operation": {"track_id": "t1", "value_db": -3.0}},
        {"operation": None},
    ],
)
def test_action_rejects_untyped_fields_and_operations(kwargs):
    fields = dict(
        action_id=ActionId("x1"),
        project_id=ProjectId("p1"),
        expected_revision=Revision("r1"),
        operation=SetTrackGain(TrackId("t1"), -3.0),
    )
    fields.update(kwargs)
    with pytest.raises(ValueError):
        Action(**fields)


# --- outcomes and decisions ------------------------------------------------------


def test_valid_proposal_for_arbitrary_tracks_and_roles():
    decision = _decision(_proposal(track_id="t3"), evidence=("o1", "o2"))
    validate_decision_references(decision, _snapshot(), _context(), (_observation("o1"), _observation("o2", ("t3",))))
    assert decision.outcome.action.operation.track_id == TrackId("t3")


def test_propose_action_requires_typed_action_and_expected_effect():
    with pytest.raises(ValueError):
        ProposeAction(_action(), " ")
    with pytest.raises(ValueError):
        ProposeAction(SetTrackGain(TrackId("t1"), -3.0), "effect")


@pytest.mark.parametrize("reason", list(AbstentionReason))
def test_every_abstention_reason_is_valid_without_observations(reason):
    decision = _decision(Abstain(reason), evidence=())
    validate_decision_references(decision, _snapshot(), _context(), ())
    assert decision.outcome.reason is reason
    assert decision.evidence_ids == ()


def test_abstention_reasons_are_exactly_the_specified_set():
    assert {r.name for r in AbstentionReason} == {
        "INSUFFICIENT_EVIDENCE",
        "MISSING_CONTEXT",
        "CONFLICTING_CONSTRAINTS",
        "NO_JUSTIFIED_ACTION",
    }


def test_abstain_may_cite_evidence_and_request_context():
    abstain = Abstain(AbstentionReason.MISSING_CONTEXT, ("role of t3", "protected tracks"))
    decision = _decision(abstain, evidence=("o1",))
    validate_decision_references(decision, _snapshot(), _context(), (_observation(),))
    assert decision.outcome.requested_context == ("role of t3", "protected tracks")


@pytest.mark.parametrize(
    "reason, requested",
    [
        ("MISSING_CONTEXT", ()),
        (None, ()),
        (AbstentionReason.MISSING_CONTEXT, ("",)),
        (AbstentionReason.MISSING_CONTEXT, ("a", "a")),
        (AbstentionReason.MISSING_CONTEXT, ["a"]),
        (AbstentionReason.MISSING_CONTEXT, (1,)),
    ],
)
def test_abstain_rejects_untyped_reason_and_invalid_requests(reason, requested):
    with pytest.raises(ValueError):
        Abstain(reason, requested)


@pytest.mark.parametrize(
    "outcome",
    [None, "abstain", _action(), SetTrackGain(TrackId("t1"), -3.0), AbstentionReason.NO_JUSTIFIED_ACTION, {}],
)
def test_decision_rejects_outcomes_other_than_proposal_or_abstention(outcome):
    with pytest.raises(ValueError):
        _decision(outcome)


def test_decision_has_exactly_one_outcome_field():
    fields = {f.name for f in dataclasses.fields(Decision)}
    assert "outcome" in fields
    assert not fields & {"action", "abstention", "confidence", "callback"}


@pytest.mark.parametrize("action_kwargs", [{"project_id": "p2"}, {"revision": "r0"}])
def test_proposal_must_match_decision_project_and_revision(action_kwargs):
    with pytest.raises(ValueError):
        _decision(_proposal(**action_kwargs))


@pytest.mark.parametrize(
    "overrides",
    [
        {"rationale": ""},
        {"rationale": "   "},
        {"context_version": ""},
        {"decision_id": "d1"},
        {"context_id": "c1"},
        {"evidence_ids": [ObservationId("o1")]},
        {"evidence_ids": ("o1",)},
    ],
)
def test_decision_rejects_blank_text_and_untyped_fields(overrides):
    with pytest.raises(ValueError):
        _decision(_proposal(), **overrides)


@pytest.mark.parametrize("name, version", [("", "1"), ("controller", " "), (None, "1")])
def test_controller_identity_requires_name_and_version(name, version):
    with pytest.raises(ValueError):
        ControllerIdentity(name, version)


def test_decision_rejects_duplicate_evidence_and_proposal_without_evidence():
    with pytest.raises(ValueError):
        _decision(_proposal(), evidence=("o1", "o1"))
    with pytest.raises(ValueError):
        _decision(_proposal(), evidence=())


def test_records_and_collections_are_immutable():
    decision = _decision(Abstain(AbstentionReason.MISSING_CONTEXT, ("role of t1",)), evidence=("o1",))
    assert isinstance(decision.evidence_ids, tuple)
    assert isinstance(decision.outcome.requested_context, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.outcome = _proposal()
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.outcome.reason = AbstentionReason.NO_JUSTIFIED_ACTION
    proposal = _proposal()
    with pytest.raises(dataclasses.FrozenInstanceError):
        proposal.action.operation.value_db = 0.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        proposal.action.expected_revision = Revision("r2")


# --- reference validation --------------------------------------------------------


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"project_id": ProjectId("p2")}, "project"),
        ({"expected_revision": Revision("r0")}, "revision"),
        ({"context_id": ContextId("c2")}, "context"),
        ({"context_version": "2"}, "context"),
    ],
)
def test_references_reject_snapshot_and_context_mismatch(overrides, match):
    decision = _decision(Abstain(AbstentionReason.NO_JUSTIFIED_ACTION), evidence=(), **overrides)
    with pytest.raises(ValueError, match=match):
        validate_decision_references(decision, _snapshot(), _context(), ())


def test_references_validate_context_against_snapshot():
    decision = _decision(Abstain(AbstentionReason.NO_JUSTIFIED_ACTION), evidence=())
    context = _context(protected=frozenset({TrackId("t9")}))
    with pytest.raises(ValueError, match="t9"):
        validate_decision_references(decision, _snapshot(), context, ())


def test_references_reject_missing_and_duplicate_supplied_observations():
    decision = _decision(_proposal(), evidence=("o1", "o2"))
    with pytest.raises(ValueError, match="missing"):
        validate_decision_references(decision, _snapshot(), _context(), (_observation("o1"),))
    with pytest.raises(ValueError, match="duplicate"):
        validate_decision_references(
            decision, _snapshot(), _context(), (_observation("o1"), _observation("o2"), _observation("o1"))
        )


def test_references_reject_observation_scope_with_unknown_track():
    decision = _decision(_proposal(), evidence=("o1",))
    with pytest.raises(ValueError, match="t9"):
        validate_decision_references(decision, _snapshot(), _context(), (_observation("o1", ("t1", "t9")),))


def test_references_reject_action_targeting_unknown_track():
    decision = _decision(_proposal(track_id="t9"), evidence=("o1",))
    with pytest.raises(ValueError, match="t9"):
        validate_decision_references(decision, _snapshot(), _context(), (_observation(),))


def test_references_ignore_unreferenced_supplied_observations():
    decision = _decision(_proposal(), evidence=("o1",))
    validate_decision_references(decision, _snapshot(), _context(), (_observation("o1"), _observation("o2")))


def test_references_do_not_enforce_protection_or_gain_policy():
    # Protected IDs and gain bounds are enforced by later application code, not by reference validation.
    decision = _decision(_proposal(track_id="t2", value_db=-60.0), evidence=("o1",))
    context = _context(protected=frozenset({TrackId("t2")}))
    validate_decision_references(decision, _snapshot(), context, (_observation(),))


# --- dependency boundary --------------------------------------------------------


def test_new_modules_load_only_standard_library_and_domain_modules():
    # Complements the AST scan in test_domain_foundation.py, which covers every module in the domain package.
    script = (
        "import sys\n"
        "before = set(sys.modules)\n"
        "import autonomous_mixing.domain.action, autonomous_mixing.domain.decision\n"
        "for name in sorted(set(sys.modules) - before):\n"
        "    top = name.split('.')[0]\n"
        "    ok = top in sys.stdlib_module_names or name == 'autonomous_mixing' \\\n"
        "        or name == 'autonomous_mixing.domain' or name.startswith('autonomous_mixing.domain.') \\\n"
        # ADR-027 migration step M1: domain records moved to the core records package.
        "        or name in ('autonomous_mixing.core', 'autonomous_mixing.core.records') \\\n"
        "        or name.startswith('autonomous_mixing.core.records.')\n"
        "    if not ok: print(name)\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert result.stdout == ""
