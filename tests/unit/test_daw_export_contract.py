"""Core contract of the pending-export handoff and verified import (plan 2026-11-18, revision 3). Records only."""

import math
from dataclasses import replace

import pytest
from daw_export_support import (
    APPLY,
    BINDING,
    CHECKPOINT,
    PROFILE,
    READING,
    T1,
    T2,
    T3,
    anchor,
    awaiting,
    before_artifact,
    export_request,
    files_verified,
    manifest,
    outputs,
    routing,
    snapshot,
)
from fl_runtime_support import make_request

from autonomous_mixing.core.admission.daw_export import assess_daw_import
from autonomous_mixing.core.records.actions import ExecutionReceipt
from autonomous_mixing.core.records.artifacts import ArtifactKind
from autonomous_mixing.core.records.daw_binding import DawFaderReading
from autonomous_mixing.core.records.daw_control import DawBuild
from autonomous_mixing.core.records.daw_export import (
    DawAlignmentStatus,
    DawContinuitySnapshot,
    DawDependencyStatus,
    DawExportOutput,
    DawFileIdentity,
    DawFilesVerified,
    DawImportedFileEvidence,
    DawImportRejected,
    DawImportRejectionCode,
    DawImportVerified,
    DawStateBasis,
    RoutingUnknownError,
    daw_continuity_rejection,
    daw_export_artifact,
    daw_export_file_name_problem,
)
from autonomous_mixing.core.records.daw_recovery import DawCommandKind, DawControlState, DawUnresolvedCommand
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    CheckpointId,
    ExecutionId,
    ProjectId,
    Revision,
    VersionRef,
)
from autonomous_mixing.core.records.project import ArtifactCheckpoint
from autonomous_mixing.core.records.rendering import ExecutionWorkspace, RenderedCandidate

Code = DawImportRejectionCode


def output(index, **changes):
    return replace(outputs()[index], **changes)


# --- the state anchor ------------------------------------------------------------------------------------------------


def test_a_confirmed_write_anchor_is_the_latest_reservation():
    assert anchor().profile.conversion == PROFILE.conversion
    later = anchor(source_seq=5, reservation_watermark=5, resolution_watermark=3, ledger_watermark=9)
    assert later.ledger_watermark == 9


def test_a_resolution_anchor_is_the_latest_resolution_with_no_reservation_after_it():
    resolved = anchor(basis=DawStateBasis.RESOLVED, source_seq=4, reservation_watermark=2, resolution_watermark=4,
                      ledger_watermark=5, apply=None)  # fmt: skip
    assert resolved.basis is DawStateBasis.RESOLVED


def test_a_confirmed_write_anchor_carries_exactly_its_journaled_apply_request():
    assert anchor().apply == APPLY and anchor().apply.command_id == anchor().source_id
    for changes in (
        {"apply": None},  # a confirmed write always carries its request
        {"apply": make_request("apply_other_0001")},  # another command
        {"apply": replace(APPLY, execution_id=ExecutionId("e2"))},
        {"binding": replace(BINDING, baseline_gain_db=2.5)},  # the apply was resolved under another binding
        {"basis": DawStateBasis.CONFIRMED_RESTORE, "source_seq": 1},  # only a write anchor carries an apply
        {
            "basis": DawStateBasis.RESOLVED,
            "source_seq": 2,
            "reservation_watermark": 1,
            "resolution_watermark": 2,
            "ledger_watermark": 2,
        },  # fmt: skip
    ):
        with pytest.raises(ValueError):
            anchor(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"reservation_watermark": 2, "ledger_watermark": 2},  # a write reserved after the source
        {"resolution_watermark": 2, "ledger_watermark": 2},  # a resolution after the write source
        {"basis": DawStateBasis.CONFIRMED_RESTORE, "reservation_watermark": 2, "ledger_watermark": 2},
        {
            "basis": DawStateBasis.RESOLVED,
            "source_seq": 3,
            "resolution_watermark": 3,
            "reservation_watermark": 4,
            "ledger_watermark": 4,
        },  # fmt: skip
        {
            "basis": DawStateBasis.RESOLVED,
            "source_seq": 3,
            "resolution_watermark": 5,
            "reservation_watermark": 1,
            "ledger_watermark": 5,
        },  # fmt: skip
        {"ledger_watermark": 0},
        {"source_seq": 2, "reservation_watermark": 2, "ledger_watermark": 1},
        {"resolution_watermark": -1},
        {"source_id": "short"},
        {"binding": replace(BINDING, insert_index=3)},  # outside the profile's scope
    ],
)
def test_an_anchor_that_is_not_current_or_consistent_cannot_be_built(changes):
    with pytest.raises(ValueError):
        anchor(**changes)


# --- request identity ---------------------------------------------------------------------------------------------------


def test_a_complete_request_derives_its_project_and_names_the_replaced_members():
    request = export_request()
    assert request.project_id == ProjectId("p1")
    assert [(o.artifact_id.value, o.replaces.value) for o in request.outputs] == [("x1", "s1"), ("x2", "s2"),
                                                                                   ("xm", "m1")]  # fmt: skip


@pytest.mark.parametrize(
    "changes",
    [
        {"execution_id": ExecutionId("e2")},
        {"workspace": ExecutionWorkspace(ExecutionId("e2"), "exports", "export_ws_0001")},
        {"anchor": anchor(execution_id=ExecutionId("e2"), apply=replace(APPLY, execution_id=ExecutionId("e2")))},
        {"candidate_revision": Revision("r1")},
    ],
)
def test_execution_ids_must_agree_and_the_candidate_revision_must_be_new(changes):
    with pytest.raises(ValueError):
        export_request(**changes)


@pytest.mark.parametrize(
    "binding_changes",
    [
        {"project_id": ProjectId("p2")},
        {"track_id": T3},
        {"baseline_gain_db": 2.5},
        {"baseline_fader": DawFaderReading(0.75, -1.2)},
    ],  # fmt: skip
)
def test_a_binding_with_the_same_reference_but_other_contents_is_refused(binding_changes):
    changed = replace(BINDING, **binding_changes)
    assert changed.reference == BINDING.reference
    with pytest.raises(ValueError, match="binding"):
        export_request(binding=changed)


@pytest.mark.parametrize(
    "profile_changes",
    [
        {"start_db_tolerance": 0.06},
        {"maximum_cut_db": 2.0},
        {"build": DawBuild("other", 45)},
        {"conversion": VersionRef("fl-mixer-h125", "v2")},
    ],  # fmt: skip
)
def test_a_profile_with_the_same_reference_but_other_settings_is_refused(profile_changes):
    changed = replace(PROFILE, **profile_changes)
    assert changed.reference == PROFILE.reference
    with pytest.raises(ValueError, match="profile"):
        export_request(profile=changed)


def test_a_request_matching_a_changed_anchor_is_still_tied_to_its_checkpoint_project():
    other = replace(BINDING, project_id=ProjectId("p2"))
    with pytest.raises(ValueError, match="another project"):
        # a resolution anchor carries no apply request, so only the request's own project check can refuse it
        resolved = anchor(binding=other, basis=DawStateBasis.RESOLVED, source_seq=2, reservation_watermark=1,
                          resolution_watermark=2, ledger_watermark=2, apply=None)  # fmt: skip
        export_request(binding=other, anchor=resolved)


# --- replacement and coverage ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "replaced",
    [
        (output(0, replaces=ArtifactId("m1")), outputs()[1], outputs()[2]),  # stem replacing the master
        (output(0, replaces=ArtifactId("s2")), output(1, replaces=ArtifactId("s1")), outputs()[2]),  # other tracks
        (output(0, replaces=ArtifactId("nope")), outputs()[1], outputs()[2]),  # not a before member
        (outputs()[0], output(1, replaces=ArtifactId("s1"), source_track_ids=(T1,)), outputs()[2]),  # twice
        (output(0, artifact_id=ArtifactId("s3")), outputs()[1], outputs()[2]),  # reuses a before ID
        (outputs()[0], output(1, artifact_id=ArtifactId("x1")), outputs()[2]),  # duplicate output ID
    ],
)
def test_each_output_replaces_exactly_one_matching_before_member(replaced):
    with pytest.raises(ValueError):
        export_request(outputs=replaced)


def test_coverage_is_exactly_target_dependents_and_one_master():
    x3 = DawExportOutput(ArtifactId("x3"), ArtifactId("s3"), ArtifactKind.STEM, (T3,), 2, "kick.wav")
    with pytest.raises(ValueError, match="master"):
        export_request(outputs=outputs()[:2])
    with pytest.raises(ValueError, match="dependents"):
        export_request(outputs=(outputs()[0], outputs()[2]))  # the declared dependent is missing
    with pytest.raises(ValueError, match="dependents"):
        export_request(outputs=(*outputs(), x3))  # an unaffected track
    with pytest.raises(ValueError, match="own dependent"):
        export_request(routing=routing(dependents=(T1, T2)))
    assert len(export_request(routing=routing(dependents=(T2, T3)), outputs=(*outputs(), x3)).outputs) == 4


def test_two_masters_and_group_outputs_are_outside_the_affected_set():
    m2 = before_artifact("m2", (T1, T2, T3), ArtifactKind.MIX)
    g1 = before_artifact("g1", (T1, T2), ArtifactKind.GROUP)
    checkpoint = ArtifactCheckpoint(
        CheckpointId("c1"), ProjectId("p1"), Revision("r1"), (*CHECKPOINT.artifacts, m2, g1)
    )
    second = DawExportOutput(ArtifactId("xn"), ArtifactId("m2"), ArtifactKind.MIX, (T1, T2, T3), 2, "master2.wav")
    group = DawExportOutput(ArtifactId("xg"), ArtifactId("g1"), ArtifactKind.GROUP, (T1, T2), 2, "group.wav")
    with pytest.raises(ValueError, match="exactly one master"):
        export_request(before_checkpoint=checkpoint, outputs=(*outputs(), second))
    with pytest.raises(ValueError, match="affected set"):
        export_request(before_checkpoint=checkpoint, outputs=(*outputs(), group))


def test_unknown_routing_is_refused_with_its_own_error():
    with pytest.raises(RoutingUnknownError):
        export_request(routing=routing(status=DawDependencyStatus.UNKNOWN))


# --- file names -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "export-request.json",
        "EXPORT-Request.JSON",
        "a/b.wav",
        "a\\b.wav",
        "a:b.wav",
        "a*b.wav",
        "a?b.wav",
        'a"b.wav',
        "a<b.wav",
        "a|b.wav",
        "ba\x01ss.wav",
        "bass.wav.",
        "bass.wav ",
        "CON.wav",
        "con",
        "Lpt1.wav",
        "COM9.backup.wav",
        "nul .wav",
        "bass.mp3",
        "bass",
        ".wav",
        "bass.wav\x7f",
    ],  # fmt: skip
)
def test_invalid_output_file_names_are_refused(name):
    assert daw_export_file_name_problem(name) is not None
    with pytest.raises(ValueError, match="file_name"):
        output(0, file_name=name)


@pytest.mark.parametrize("name", ["bass.wav", "Bass Mix.WAV", "éclair.wav", "COM10.wav", "console.wav"])
def test_valid_output_file_names(name):
    assert daw_export_file_name_problem(name) is None


@pytest.mark.parametrize("pair", [("Bass.wav", "bass.WAV"), ("é.wav", "é.wav"), ("STRASSE.wav", "straße.wav")])
def test_output_names_colliding_after_normalization_and_case_folding_are_refused(pair):
    first, second = pair
    with pytest.raises(ValueError, match="collide"):
        export_request(outputs=(output(0, file_name=first), output(1, file_name=second), outputs()[2]))


# --- continuity -----------------------------------------------------------------------------------------------------------


def unresolved():
    return DawUnresolvedCommand("pending_cmd_01", DawCommandKind.APPLY, "pending_cmd_01", None, 2, READING, READING,
                                None)  # fmt: skip


def test_continuity_passes_on_exact_observed_agreement():
    assert daw_continuity_rejection(anchor(), snapshot()) is None


@pytest.mark.parametrize(
    "case, code",
    [
        (lambda: snapshot(anchor(ledger_watermark=2)), Code.STATE_DISCONTINUITY),  # e.g. a reconciliation appended
        (lambda: snapshot(anchor(reading=DawFaderReading(0.72, -2.2))), Code.STATE_DISCONTINUITY),
        (lambda: snapshot(state_record=DawControlState((unresolved(),), frozenset(), ())), Code.UNRESOLVED_COMMAND),
        (
            lambda: snapshot(state_record=DawControlState((), frozenset({PROFILE.conversion}), ())),
            Code.CONVERSION_BLOCKED,
        ),  # fmt: skip
        (lambda: snapshot(fader=None, title="Another project"), Code.STATE_DISCONTINUITY),
        (lambda: snapshot(build=DawBuild("Producer Edition v26.2", 45)), Code.STATE_DISCONTINUITY),
        (
            lambda: snapshot(fader=DawFaderReading(math.nextafter(READING.normalized, 1), READING.db)),
            Code.STATE_DISCONTINUITY,
        ),  # fmt: skip
        (
            lambda: snapshot(fader=DawFaderReading(READING.normalized, math.nextafter(READING.db, 0))),
            Code.STATE_DISCONTINUITY,
        ),  # fmt: skip
    ],
)
def test_each_continuity_failure(case, code):
    rejection = daw_continuity_rejection(anchor(), case())
    assert rejection is not None and rejection[0] is code


def test_a_snapshot_inspection_must_answer_the_anchor_binding_and_profile():
    good = snapshot()
    with pytest.raises(ValueError, match="another binding or profile"):
        DawContinuitySnapshot(anchor(), replace(good.inspection, request=replace(good.inspection.request,
                              profile=replace(PROFILE, start_db_tolerance=0.06))), good.control_state)  # fmt: skip


# --- the assessment -------------------------------------------------------------------------------------------------------


def test_verified_files_and_two_agreeing_snapshots_become_candidate_evidence():
    record = awaiting()
    files = files_verified(record)
    result = assess_daw_import(record, files, before_import=snapshot(), after_import=snapshot())
    assert isinstance(result, DawImportVerified)
    assert result.alignment is DawAlignmentStatus.DECLARED_TIMELINE_PHYSICAL_UNVERIFIED
    assert [(a.value, b.value) for a, b in result.replacements] == [("x1", "s1"), ("x2", "s2"), ("xm", "m1")]
    for artifact, item, out in zip(result.candidate_artifacts, files.evidence, record.request.outputs, strict=True):
        assert (artifact.artifact_id, artifact.kind, artifact.source_track_ids) == (out.artifact_id, out.kind,
                                                                                    out.source_track_ids)  # fmt: skip
        assert artifact.revision == Revision("r2") and artifact.content_hash == item.verified_sha256
        assert (artifact.timeline_start_seconds, artifact.sample_rate_hz, artifact.frame_count) == (0.0, 44100, 8)
        assert artifact.channel_count == 2 and artifact.location == f"export_ws_0001/{out.file_name}"
    assert ArtifactId("s3") not in {a.artifact_id for a in result.candidate_artifacts}  # retained, not re-exported


def test_a_file_rejection_passes_through_before_any_continuity_check():
    rejected = DawImportRejected("export_0001", Code.HASH_MISMATCH, "bad hash")
    stale = snapshot(anchor(ledger_watermark=4))
    assert assess_daw_import(awaiting(), rejected, before_import=stale, after_import=stale) is rejected


def test_before_continuity_is_checked_before_after_continuity():
    record = awaiting()
    unresolved_state = DawControlState((unresolved(),), frozenset(), ())
    result = assess_daw_import(record, files_verified(record), before_import=snapshot(anchor(ledger_watermark=3)),
                               after_import=snapshot(state_record=unresolved_state))  # fmt: skip
    assert result.code is Code.STATE_DISCONTINUITY and result.explanation.startswith("before")
    result = assess_daw_import(record, files_verified(record), before_import=snapshot(),
                               after_import=snapshot(state_record=unresolved_state))  # fmt: skip
    assert result.code is Code.UNRESOLVED_COMMAND and result.explanation.startswith("after")


def test_the_after_import_snapshot_alone_can_reject():
    record = awaiting()
    moved = snapshot(fader=DawFaderReading(READING.normalized, math.nextafter(READING.db, 1)))
    result = assess_daw_import(record, files_verified(record), before_import=snapshot(), after_import=moved)
    assert isinstance(result, DawImportRejected) and result.code is Code.STATE_DISCONTINUITY


def test_wiring_errors_raise():
    record = awaiting()
    with pytest.raises(ValueError):
        assess_daw_import(record, files_verified(awaiting(fingerprint="b" * 64)), before_import=snapshot(),
                          after_import=snapshot())  # fmt: skip
    with pytest.raises(ValueError):
        assess_daw_import(record, DawImportRejected("export_9999", Code.HASH_MISMATCH, "x"), before_import=snapshot(),
                          after_import=snapshot())  # fmt: skip


def test_a_verified_import_cannot_be_built_with_other_artifacts_mapping_or_failing_snapshots():
    record = awaiting()
    good = assess_daw_import(record, files_verified(record), before_import=snapshot(), after_import=snapshot())
    moved = replace(good.candidate_artifacts[0], timeline_start_seconds=0.5)
    for changes in ({"candidate_artifacts": (moved, *good.candidate_artifacts[1:])},
                    {"replacements": good.replacements[:2]},
                    {"after_import": snapshot(anchor(ledger_watermark=2))}):  # fmt: skip
        with pytest.raises(ValueError):
            replace(good, **changes)


def test_verified_files_alone_are_never_candidate_evidence():
    files = files_verified()
    assert not hasattr(files, "candidate_artifacts")
    assert not isinstance(files, (DawImportVerified, ExecutionReceipt, RenderedCandidate))
    assert not issubclass(DawImportVerified, (ExecutionReceipt, RenderedCandidate))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: (replace(e[0], verified_sha256="f" * 64), *e[1:]),  # differs from the manifest
        lambda e: (replace(e[0], subtype="PCM_16"), *e[1:]),
        lambda e: (replace(e[0], container="RF64"), *e[1:]),
        lambda e: (replace(e[0], finite_samples=False), *e[1:]),
        lambda e: (e[0], replace(e[1], file_identity=e[0].file_identity), e[2]),  # one physical file twice
        lambda e: (replace(e[0], file_identity=DawFileIdentity(7, 2)), *e[1:]),  # the request file itself
        lambda e: e[:2],
        lambda e: (e[1], e[0], e[2]),  # out of output order
    ],
)
def test_file_evidence_invariants(mutate):
    files = files_verified()
    with pytest.raises(ValueError):
        DawFilesVerified(files.request, mutate(files.evidence))


def test_a_manifest_for_another_fingerprint_cannot_be_verified():
    record = awaiting()
    other = manifest(awaiting(fingerprint="b" * 64))
    files = files_verified(record)
    with pytest.raises(ValueError, match="another request"):
        DawFilesVerified(replace(files.request, manifest=other), files.evidence)


def test_the_candidate_artifact_is_derived_only_for_the_requests_own_outputs():
    request = export_request()
    foreign = DawExportOutput(ArtifactId("zz"), ArtifactId("s3"), ArtifactKind.STEM, (T3,), 1, "kick.wav")
    with pytest.raises(ValueError):
        daw_export_artifact(request, foreign, "0" * 64)
    assert daw_export_artifact(request, request.outputs[0], "0" * 64).channel_count == 2


def test_evidence_records_validate_their_fields():
    with pytest.raises(ValueError):
        DawImportedFileEvidence(ArtifactId("x1"), "A" * 64, "WAV", "PCM_24", True, DawFileIdentity(1, 1))
    with pytest.raises(ValueError):
        DawFileIdentity(-1, 0)
    with pytest.raises(ValueError):
        DawFileIdentity(True, 0)
