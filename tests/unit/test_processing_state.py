"""Focused tests for fixed-input gain processing state and composite project state.

Gain values are structural fixtures, not recommendations. These are metadata transitions only: no audio
is generated, rendered or read, and nothing here demonstrates processing fidelity.
"""

import dataclasses
import math
from datetime import datetime, timedelta, timezone

import pytest

from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    CheckpointId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)

UTC_NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
TRACKS = (("t1", "Room mic"), ("t2", "Voice"), ("t3", "Synth pad"))


def _artifact(aid, tracks=("t1",), *, revision="r0", project="p1", kind=ArtifactKind.STEM, frames=48000 * 60):
    return AudioArtifact(
        ArtifactId(aid),
        ProjectId(project),
        Revision(revision),
        tuple(TrackId(t) for t in tracks),
        kind,
        f"inputs/{aid}.wav",
        0.0,
        48000,
        2,
        frames,
        "f" * 64,
        VersionRef("render", "1"),
    )


INPUT_T1, INPUT_T2, INPUT_T3 = _artifact("a-t1", ("t1",)), _artifact("a-t2", ("t2",)), _artifact("a-t3", ("t3",))
INPUT_MIX = _artifact("a-mix", ("t1", "t2", "t3"), kind=ArtifactKind.MIX)


def _snapshot(revision="r1", project="p1", tracks=TRACKS) -> ProjectSnapshot:
    return ProjectSnapshot(
        ProjectId(project), Revision(revision), UTC_NOW, tuple(Track(TrackId(t), n) for t, n in tracks)
    )


def _gain(track="t2", gain_db=0.0, input_artifact=None) -> TrackGainState:
    return TrackGainState(TrackId(track), input_artifact or {"t1": INPUT_T1, "t2": INPUT_T2}[track], gain_db)


def _processing(revision="r1", gains=None, project="p1") -> ProjectProcessingState:
    if gains is None:
        gains = (_gain("t1"), _gain("t2"))  # t3 is explicitly unrepresented, not unity gain
    return ProjectProcessingState(ProjectId(project), Revision(revision), gains)


def _audio(revision="r1", artifacts=None, project="p1", cid="cp1") -> ArtifactCheckpoint:
    if artifacts is None:
        artifacts = (INPUT_T1, INPUT_T2, INPUT_T3, INPUT_MIX)
    return ArtifactCheckpoint(CheckpointId(cid), ProjectId(project), Revision(revision), artifacts)


def _state(**overrides) -> ProjectState:
    fields = dict(snapshot=_snapshot(), processing=_processing(), audio=_audio())
    fields.update(overrides)
    return ProjectState(**fields)


# --- records ---------------------------------------------------------------------------


def test_processing_state_records():
    gain = _gain("t2", -3.0)
    assert (gain.track_id, gain.input_artifact, gain.gain_db) == (TrackId("t2"), INPUT_T2, -3.0)
    processing = _processing()
    assert [g.track_id for g in processing.track_gains] == [TrackId("t1"), TrackId("t2")]
    state = _state()
    assert state.project_id == ProjectId("p1")
    assert state.revision == Revision("r1")
    # Input provenance (production revision r0) is independent of the represented state revision r1.
    assert {g.input_artifact.revision for g in state.processing.track_gains} == {Revision("r0")}


def test_zero_gain_is_representable_but_never_implied():
    assert _gain("t1", 0.0).gain_db == 0.0
    assert _gain("t1", 0).gain_db == 0
    empty = _processing(gains=())
    assert empty.track_gains == ()
    _state(processing=empty)  # explicit empty coverage is valid
    with pytest.raises(TypeError):
        TrackGainState(TrackId("t1"), INPUT_T1)  # no default gain


@pytest.mark.parametrize("gain_db", [True, False, math.nan, math.inf, -math.inf, "-3", None, 10**400])
def test_gain_must_be_a_finite_float_representable_number(gain_db):
    with pytest.raises(ValueError):
        TrackGainState(TrackId("t1"), INPUT_T1, gain_db)


@pytest.mark.parametrize(
    "track, input_artifact",
    [
        ("t1", _artifact("a-mix", ("t1",), kind=ArtifactKind.MIX)),
        ("t1", _artifact("a-group", ("t1",), kind=ArtifactKind.GROUP)),
        ("t1", _artifact("a-t12", ("t1", "t2"))),
        ("t1", INPUT_T2),
        ("t1", "a-t1"),
    ],
    ids=["mix-input", "group-input", "multi-track-stem", "other-track-stem", "untyped"],
)
def test_fixed_input_must_be_a_stem_of_exactly_that_track(track, input_artifact):
    with pytest.raises(ValueError):
        TrackGainState(TrackId(track), input_artifact, 0.0)
    with pytest.raises(ValueError):
        TrackGainState("t1", INPUT_T1, 0.0)


@pytest.mark.parametrize(
    "gains, match",
    [
        ((_gain("t1"), _gain("t1", -1.0)), "unique"),
        ([_gain("t1")], "tuple"),
        (("t1",), "type"),
        ((_gain("t1", input_artifact=_artifact("a-t1", ("t1",), project="p9")),), "project"),
        (
            (
                _gain("t1", input_artifact=_artifact("a-x", ("t1",))),
                _gain("t2", input_artifact=_artifact("a-x", ("t2",))),
            ),
            "a-x",
        ),
    ],
    ids=["duplicate-track", "mutable", "untyped", "foreign-project-input", "conflicting-same-id"],
)
def test_processing_state_rejects_invalid_collections_and_inputs(gains, match):
    with pytest.raises(ValueError, match=match):
        _processing(gains=gains)


@pytest.mark.parametrize(
    "processing, match",
    [
        (lambda: _processing(project="p9", gains=()), "project"),
        (lambda: _processing(revision="r2"), "revision"),
        (lambda: _processing(gains=(_gain("t1"),)), None),
    ],
    ids=["project", "revision", "valid-partial"],
)
def test_processing_state_validates_against_snapshot(processing, match):
    if match is None:
        processing().validate_against(_snapshot())
        return
    with pytest.raises(ValueError, match=match):
        processing().validate_against(_snapshot())


def test_processing_state_rejects_unknown_tracks():
    processing = _processing(gains=(_gain("t1"),))
    with pytest.raises(ValueError, match="t1"):
        processing.validate_against(_snapshot(tracks=(("t2", "Voice"),)))


def test_project_state_rejects_mixed_revisions():
    with pytest.raises(ValueError, match="revision"):
        _state(processing=_processing(revision="r2"))
    with pytest.raises(ValueError, match="revision"):
        _state(audio=_audio(revision="r2"))
    with pytest.raises(ValueError, match="revision"):
        _state(snapshot=_snapshot(revision="r2"))


def test_project_state_rejects_project_mismatch_and_unknown_tracks():
    with pytest.raises(ValueError, match="project"):
        _state(processing=_processing(project="p9", gains=()))
    with pytest.raises(ValueError, match="t3"):
        _state(snapshot=_snapshot(tracks=TRACKS[:2]))  # audio references t3


def test_baseline_need_not_be_current_audio_member():
    rendered_t2 = _artifact("c-t2", ("t2",), revision="r1")
    state = _state(audio=_audio(artifacts=(INPUT_T1, rendered_t2)))
    assert INPUT_T2.artifact_id not in state.audio.artifact_ids
    assert state.processing.track_gains[1].input_artifact == INPUT_T2


def test_baseline_present_in_audio_must_have_identical_metadata():
    forged = dataclasses.replace(INPUT_T2, frame_count=48000)
    with pytest.raises(ValueError, match="a-t2"):
        _state(audio=_audio(artifacts=(INPUT_T1, forged)))


@pytest.mark.parametrize(
    "overrides",
    [
        {"snapshot": "p1@r1"},
        {"processing": (_gain("t1"),)},
        {"audio": (INPUT_T1,)},
    ],
    ids=["snapshot", "processing", "audio"],
)
def test_project_state_rejects_wrong_types(overrides):
    with pytest.raises(ValueError):
        _state(**overrides)


def test_records_are_immutable_and_have_no_duplicate_identity_fields():
    state = _state()
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.processing = _processing(gains=())
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.processing.track_gains[0].gain_db = -1.0
    assert isinstance(state.processing.track_gains, tuple)
    assert {f.name for f in dataclasses.fields(ProjectState)} == {"snapshot", "processing", "audio"}


def test_snapshot_timestamp_is_part_of_state_identity():
    later = dataclasses.replace(_snapshot(), captured_at=UTC_NOW + timedelta(minutes=5))
    assert _state(snapshot=later) != _state()


# --- candidate state succession (action component) ----------------------------------------

from autonomous_mixing.core.loop.transitions import derive_candidate_project_state  # noqa: E402
from autonomous_mixing.core.records.actions import (  # noqa: E402
    Action,
    ExecutionIssue,
    ExecutionReceipt,
    ExecutionStatus,
    SetTrackGain,
    derive_candidate_checkpoint,
)
from autonomous_mixing.core.records.identifiers import ActionId, ExecutionId  # noqa: E402

CANDIDATE_AT = UTC_NOW + timedelta(minutes=1)


def _render_step(state: ProjectState, gain_db, n: int, target="t2", **receipt_overrides):
    """Receipt and action for one candidate render: a new target stem and mix produced at revision r<n>."""
    revision = f"r{n}"
    stem = _artifact(f"c{n}-{target}", (target,), revision=revision)
    mix = _artifact(f"c{n}-mix", ("t1", "t2", "t3"), revision=revision, kind=ArtifactKind.MIX)
    superseded = tuple(
        a.artifact_id
        for a in state.audio.artifacts
        if a.kind is ArtifactKind.MIX or a.source_track_ids == (TrackId(target),)
    )
    fields = dict(
        execution_id=ExecutionId(f"e{n}"),
        action_id=ActionId(f"x{n}"),
        project_id=ProjectId("p1"),
        before_revision=state.revision,
        status=ExecutionStatus.CANDIDATE_READY,
        candidate_revision=Revision(revision),
        candidate_artifacts=(stem, mix),
        superseded_artifact_ids=superseded,
        issue=None,
    )
    fields.update(receipt_overrides)
    action = Action(ActionId(f"x{n}"), ProjectId("p1"), state.revision, SetTrackGain(TrackId(target), gain_db))
    return ExecutionReceipt(**fields), action


def _derive(state, receipt, action, n=2, captured_at=CANDIDATE_AT):
    return derive_candidate_project_state(state, receipt, action, CheckpointId(f"cp{n}"), captured_at)


def test_candidate_derivation_preserves_baseline_and_other_tracks():
    previous = _state()
    receipt, action = _render_step(previous, -3.0, 2)
    candidate = _derive(previous, receipt, action)

    assert candidate.revision == Revision("r2")
    assert candidate.snapshot.tracks == previous.snapshot.tracks  # complete inventory, same order
    assert candidate.snapshot.captured_at == CANDIDATE_AT
    assert candidate.audio == derive_candidate_checkpoint(previous.audio, receipt, CheckpointId("cp2"))
    old_t1, old_t2 = previous.processing.track_gains
    new_t1, new_t2 = candidate.processing.track_gains
    assert new_t1 is old_t1  # non-target setting unchanged
    assert (new_t2.track_id, new_t2.input_artifact, new_t2.gain_db) == (TrackId("t2"), INPUT_T2, -3.0)
    assert INPUT_T2.artifact_id not in candidate.audio.artifact_ids  # baseline survives supersession
    assert previous == _state()  # pure: the previous state is unchanged


def test_gain_sequence_is_absolute():
    # Metadata transitions only: each setting is relative to the same fixed input, never to the last output.
    state = _state()
    original_t1 = state.processing.track_gains[0]
    observed = []
    for n, gain_db in enumerate((-3.0, -3.0, -6.0, 0.0), start=2):
        receipt, action = _render_step(state, gain_db, n)
        state = _derive(state, receipt, action, n)
        t2 = state.processing.track_gains[1]
        assert t2.input_artifact is INPUT_T2
        assert state.processing.track_gains[0] is original_t1
        observed.append(t2.gain_db)
    assert observed == [-3.0, -3.0, -6.0, 0.0]
    assert state.revision == Revision("r5")
    # Untouched artifacts keep their original production revisions across all candidate states.
    retained = {str(a.artifact_id): str(a.revision) for a in state.audio.artifacts}
    assert retained["a-t1"] == "r0" and retained["a-t3"] == "r0"
    assert retained["c5-t2"] == "r5" and "c4-t2" not in retained


def test_target_without_gain_state_rejected():
    previous = _state()  # t3 has no gain state: no zero-gain baseline may be invented
    receipt, action = _render_step(previous, 0.0, 2, target="t3")
    with pytest.raises(ValueError, match="t3"):
        _derive(previous, receipt, action)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda r, a: (dataclasses.replace(r, action_id=ActionId("x9")), a), "action"),
        (lambda r, a: (r, dataclasses.replace(a, project_id=ProjectId("p9"))), "project"),
        (lambda r, a: (r, dataclasses.replace(a, expected_revision=Revision("r0"))), "revision"),
        (lambda r, a: (dataclasses.replace(r, before_revision=Revision("r0")), a), "revision"),
    ],
    ids=["receipt-action-id", "action-project", "action-expected-revision", "receipt-before-revision"],
)
def test_receipt_action_mismatch_rejected(mutate, match):
    previous = _state()
    receipt, action = mutate(*_render_step(previous, -3.0, 2))
    with pytest.raises(ValueError, match=match):
        _derive(previous, receipt, action)


def test_derivation_requires_candidate_ready_receipt_and_utc_time():
    previous = _state()
    receipt, action = _render_step(previous, -3.0, 2)
    failed = ExecutionReceipt(
        ExecutionId("e2"),
        ActionId("x2"),
        ProjectId("p1"),
        Revision("r1"),
        ExecutionStatus.FAILED,
        None,
        (),
        (),
        ExecutionIssue("render", "failed"),
    )
    with pytest.raises(ValueError, match="CANDIDATE_READY"):
        _derive(previous, failed, action)
    with pytest.raises(ValueError):
        _derive(previous, receipt, action, captured_at=datetime(2026, 10, 21, 9, 1))
    with pytest.raises(ValueError):
        derive_candidate_project_state(previous.audio, receipt, action, CheckpointId("cp2"), CANDIDATE_AT)


def test_derived_state_is_only_a_candidate_record():
    previous = _state()
    candidate = _derive(previous, *_render_step(previous, -3.0, 2))
    assert isinstance(candidate, ProjectState)
    assert {f.name for f in dataclasses.fields(candidate)} == {"snapshot", "processing", "audio"}


# --- audit F1: gain settings require consistent artifact replacement ------------------------
# Metadata consistency only: none of this proves that any audio was rendered correctly.

from autonomous_mixing.core.loop.transitions import validate_gain_execution  # noqa: E402


def _receipt(state: ProjectState, candidates, superseded, n=2, action="x2") -> ExecutionReceipt:
    return ExecutionReceipt(
        ExecutionId(f"e{n}"),
        ActionId(action),
        ProjectId("p1"),
        state.revision,
        ExecutionStatus.CANDIDATE_READY,
        Revision(f"r{n}"),
        tuple(candidates),
        tuple(ArtifactId(a) for a in superseded),
        None,
    )


def _gain_action(state: ProjectState, value=-4.0, target="t2", action="x2") -> Action:
    return Action(ActionId(action), ProjectId("p1"), state.revision, SetTrackGain(TrackId(target), value))


NEW_T2 = _artifact("n-t2", ("t2",), revision="r2")
NEW_MIX = _artifact("n-mix", ("t1", "t2", "t3"), revision="r2", kind=ArtifactKind.MIX)
GROUP_T23 = _artifact("g-t23", ("t2", "t3"), kind=ArtifactKind.GROUP)
NEW_GROUP_T23 = _artifact("n-g-t23", ("t2", "t3"), revision="r2", kind=ArtifactKind.GROUP)


def test_audit_f1_gain_change_with_only_a_new_mix_is_rejected():
    # Audit reproduction F1: the receipt replaces only the mix, so the t2 stem would stay the old render.
    state = _state()
    receipt = _receipt(state, (NEW_MIX,), ("a-mix",))
    with pytest.raises(ValueError, match="a-t2"):
        _derive(state, receipt, _gain_action(state))
    with pytest.raises(ValueError, match="a-t2"):
        validate_gain_execution(state, receipt, _gain_action(state))


@pytest.mark.parametrize(
    "candidates, superseded, match",
    [
        ((NEW_T2,), ("a-t2",), "a-mix"),
        ((NEW_T2,), ("a-t2", "a-mix"), "replace"),
        ((NEW_T2, NEW_MIX, _artifact("n-t1", ("t1",), revision="r2")), ("a-t2", "a-mix", "a-t1"), "unrelated"),
        ((NEW_T2, NEW_MIX, _artifact("n-t3", ("t3",), revision="r2")), ("a-t2", "a-mix"), "replace"),
        (
            (NEW_T2, _artifact("n-grp", ("t1", "t2", "t3"), revision="r2", kind=ArtifactKind.GROUP)),
            ("a-t2", "a-mix"),
            "replace",
        ),
        (
            (NEW_T2, _artifact("n-mix", ("t1", "t2"), revision="r2", kind=ArtifactKind.MIX)),
            ("a-t2", "a-mix"),
            "replace",
        ),
    ],
    ids=[
        "stale-mix-retained",
        "superseded-without-replacement",
        "supersedes-unrelated",
        "unrelated-candidate-added",
        "wrong-replacement-kind",
        "wrong-replacement-tracks",
    ],
)
def test_gain_execution_requires_exact_replacement_of_target_dependent_artifacts(candidates, superseded, match):
    state = _state()
    with pytest.raises(ValueError, match=match):
        validate_gain_execution(state, _receipt(state, candidates, superseded), _gain_action(state))


def test_replacement_multiplicity_is_preserved():
    state = _state(audio=_audio(artifacts=(INPUT_T1, INPUT_T2, INPUT_T3, INPUT_MIX, GROUP_T23)))
    superseded = ("a-t2", "a-mix", "g-t23")
    with pytest.raises(ValueError, match="replace"):  # two affected outputs cannot be replaced by one
        validate_gain_execution(state, _receipt(state, (NEW_T2, NEW_MIX), superseded), _gain_action(state))
    with pytest.raises(ValueError, match="replace"):  # a second mix is not a group replacement
        second_mix = _artifact("n-mix2", ("t1", "t2", "t3"), revision="r2", kind=ArtifactKind.MIX)
        validate_gain_execution(state, _receipt(state, (NEW_T2, NEW_MIX, second_mix), superseded), _gain_action(state))
    # Structural group coverage is representable in the domain; it does not mean groups are processable.
    validate_gain_execution(state, _receipt(state, (NEW_T2, NEW_MIX, NEW_GROUP_T23), superseded), _gain_action(state))


def test_two_same_shape_outputs_cannot_be_replaced_by_one():
    # Two current full mixes both depend on t2; the shape sets match, but one new mix cannot replace both.
    alt_mix = _artifact("alt-mix", ("t1", "t2", "t3"), kind=ArtifactKind.MIX)
    state = _state(audio=_audio(artifacts=(INPUT_T1, INPUT_T2, INPUT_T3, INPUT_MIX, alt_mix)))
    superseded = ("a-t2", "a-mix", "alt-mix")
    with pytest.raises(ValueError, match="replace"):
        validate_gain_execution(state, _receipt(state, (NEW_T2, NEW_MIX), superseded), _gain_action(state))
    second_mix = _artifact("n-mix2", ("t1", "t2", "t3"), revision="r2", kind=ArtifactKind.MIX)
    validate_gain_execution(state, _receipt(state, (NEW_T2, NEW_MIX, second_mix), superseded), _gain_action(state))


def test_replacement_contributors_compare_as_sets():
    state = _state()
    reordered_mix = _artifact("n-mix", ("t3", "t1", "t2"), revision="r2", kind=ArtifactKind.MIX)
    validate_gain_execution(state, _receipt(state, (NEW_T2, reordered_mix), ("a-t2", "a-mix")), _gain_action(state))


def test_gain_execution_requires_a_current_target_stem():
    mix_only = _state(audio=_audio(artifacts=(INPUT_T1, INPUT_T3, INPUT_MIX)))
    receipt = _receipt(mix_only, (NEW_MIX,), ("a-mix",))
    with pytest.raises(ValueError, match="STEM"):
        validate_gain_execution(mix_only, receipt, _gain_action(mix_only))


def test_valid_gain_execution_and_derivation():
    state = _state()
    receipt = _receipt(state, (NEW_T2, NEW_MIX), ("a-t2", "a-mix"))
    validate_gain_execution(state, receipt, _gain_action(state))
    candidate = _derive(state, receipt, _gain_action(state))
    assert {str(a.artifact_id): str(a.revision) for a in candidate.audio.artifacts} == {
        "a-t1": "r0",  # unchanged artifacts keep their older production revisions
        "a-t3": "r0",
        "n-t2": "r2",
        "n-mix": "r2",
    }


# --- audit F5: locally known revisions cannot be reused ------------------------------------

BASELINE_RB = _artifact("a-t2", ("t2",), revision="rb")  # baseline produced at rb, no longer an audio member


def _rendered_state() -> ProjectState:
    """State r1 whose t2 audio is a rendered stem; its fixed input (produced at rb) is not an audio member."""
    rendered_t2 = _artifact("c-t2", ("t2",), revision="r1")
    gains = (_gain("t1"), TrackGainState(TrackId("t2"), BASELINE_RB, -2.0))
    return _state(
        processing=_processing(gains=gains), audio=_audio(artifacts=(INPUT_T1, rendered_t2, INPUT_T3, INPUT_MIX))
    )


def test_project_state_exposes_locally_known_revisions():
    state = _rendered_state()
    assert state.known_revisions == frozenset({Revision("r1"), Revision("r0"), Revision("rb")})
    assert state.audio.known_revisions == frozenset({Revision("r1"), Revision("r0")})


def _candidate_receipt(state: ProjectState, revision: str) -> ExecutionReceipt:
    stem = _artifact("n-t2", ("t2",), revision=revision)
    mix = _artifact("n-mix", ("t1", "t2", "t3"), revision=revision, kind=ArtifactKind.MIX)
    return ExecutionReceipt(
        ExecutionId("e2"),
        ActionId("x2"),
        ProjectId("p1"),
        state.revision,
        ExecutionStatus.CANDIDATE_READY,
        Revision(revision),
        (stem, mix),
        (ArtifactId("c-t2"), ArtifactId("a-mix")),
        None,
    )


@pytest.mark.parametrize(
    "revision, checkpoint_rejects",
    [("r0", True), ("rb", False)],
    ids=["retained-artifact-production-revision", "superseded-baseline-production-revision"],
)
def test_candidate_revision_must_not_reuse_a_locally_known_revision(revision, checkpoint_rejects):
    state = _rendered_state()
    receipt = _candidate_receipt(state, revision)
    with pytest.raises(ValueError, match="revision"):
        _derive(state, receipt, _gain_action(state))
    # Generic checkpoint derivation sees only checkpoint and member revisions, not fixed baselines.
    if checkpoint_rejects:
        with pytest.raises(ValueError, match="revision"):
            derive_candidate_checkpoint(state.audio, receipt, CheckpointId("cp2"))
    else:
        derive_candidate_checkpoint(state.audio, receipt, CheckpointId("cp2"))


def test_current_revision_cannot_be_a_candidate_revision():
    state = _rendered_state()
    with pytest.raises(ValueError, match="revision"):
        _candidate_receipt(state, "r1")  # rejected when the receipt is constructed


def test_genuinely_new_candidate_revision_passes():
    state = _rendered_state()
    candidate = _derive(state, _candidate_receipt(state, "r2"), _gain_action(state))
    assert candidate.revision == Revision("r2")
