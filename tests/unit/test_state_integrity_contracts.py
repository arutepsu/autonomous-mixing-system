"""Contract tests for state-integrity records and the inspector port (plan docs/superpowers/plans/2026-11-09-state-integrity-contract-plan.md).

All records are labelled metadata fixtures: identities, hashes and root keys are structural examples,
not evaluation data and not real files. Records check structure only; a VERIFIED result records an
inspector's claim, and the file-backed inspector that produces real evidence follows in the next task.
"""

import dataclasses
import typing
from datetime import datetime, timezone

import pytest

from autonomous_mixing.core.ports.integrity import StateIntegrityInspector, validate_state_integrity_response
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact
from autonomous_mixing.core.records.identifiers import (
    ArtifactId,
    CheckpointId,
    ProjectId,
    Revision,
    TrackId,
    VersionRef,
)
from autonomous_mixing.core.records.integrity import (
    ArtifactIntegrityResult,
    ArtifactRootBinding,
    IntegrityStatus,
    StateIntegrityReport,
    StateIntegrityRequest,
)
from autonomous_mixing.core.records.project import (
    ArtifactCheckpoint,
    ProjectProcessingState,
    ProjectSnapshot,
    ProjectState,
    Track,
    TrackGainState,
)

UTC_NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
RENDER = VersionRef("render", "1")
T1, T2, T3 = TrackId("t1"), TrackId("t2"), TrackId("t3")
VERIFIED, FAILED, UNCERTAIN = IntegrityStatus.VERIFIED, IntegrityStatus.FAILED, IntegrityStatus.UNCERTAIN


def _hash(digit: str) -> str:
    return digit * 64


def _artifact(aid, revision, tracks, kind=ArtifactKind.STEM, digest="a") -> AudioArtifact:
    return AudioArtifact(
        ArtifactId(aid), ProjectId("p1"), Revision(revision), tuple(TrackId(t) for t in tracks), kind,
        f"outputs/{aid}.wav", 0.0, 48000, 2, 48000 * 60, _hash(digest), RENDER,
    )  # fmt: skip


# State r2: s1 was produced at r1 and retained; in-t2 is both a current member and t2's fixed input;
# the fixed inputs of t3 and t1 are superseded (not current members) but still required.
S1 = _artifact("s1", "r1", ("t1",), digest="1")
IN_T2 = _artifact("in-t2", "r0", ("t2",), digest="2")
MIX = _artifact("mix", "r2", ("t1", "t2", "t3"), ArtifactKind.MIX, digest="3")
IN_T3 = _artifact("in-t3", "r0", ("t3",), digest="4")
IN_T1 = _artifact("in-t1", "r0", ("t1",), digest="5")


def _state(members=(S1, IN_T2, MIX), gains=((T3, IN_T3), (T1, IN_T1), (T2, IN_T2)), revision="r2") -> ProjectState:
    tracks = (Track(T1, "track 1"), Track(T2, "track 2"), Track(T3, "track 3"))
    snapshot = ProjectSnapshot(ProjectId("p1"), Revision(revision), UTC_NOW, tracks)
    processing = ProjectProcessingState(
        ProjectId("p1"), Revision(revision), tuple(TrackGainState(t, a, 0.0) for t, a in gains)
    )
    return ProjectState(
        snapshot, processing, ArtifactCheckpoint(CheckpointId(f"cp-{revision}"), ProjectId("p1"), Revision(revision), members)
    )  # fmt: skip


STATE = _state()
REQUIRED = (S1, IN_T2, MIX, IN_T3, IN_T1)


def _bindings(artifacts=REQUIRED, root="exports") -> tuple[ArtifactRootBinding, ...]:
    return tuple(ArtifactRootBinding(a.artifact_id, root) for a in artifacts)


REQUEST = StateIntegrityRequest(STATE, _bindings())


def _result(artifact, status=VERIFIED, observed="match", explanation="inspected") -> ArtifactIntegrityResult:
    sha = artifact.content_hash if observed == "match" else observed
    return ArtifactIntegrityResult(artifact, status, sha, explanation)


def _report(statuses=None, request=REQUEST) -> StateIntegrityReport:
    statuses = statuses or [VERIFIED] * len(request.required_artifacts)
    return StateIntegrityReport(
        request, tuple(_result(a, s) for a, s in zip(request.required_artifacts, statuses, strict=True))
    )


# --- record shapes -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "record, fields",
    [
        (ArtifactRootBinding, ["artifact_id", "root_key"]),
        (StateIntegrityRequest, ["state", "bindings"]),
        (ArtifactIntegrityResult, ["artifact", "status", "observed_sha256", "explanation"]),
        (StateIntegrityReport, ["request", "artifacts"]),
    ],
)
def test_record_fields_are_exact_required_and_frozen(record, fields):
    assert [f.name for f in dataclasses.fields(record)] == fields
    assert all(f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
               for f in dataclasses.fields(record))  # fmt: skip
    assert record.__dataclass_params__.frozen


def test_status_members_are_exact():
    assert [(s.name, s.value) for s in IntegrityStatus] == [
        ("VERIFIED", "verified"),
        ("FAILED", "failed"),
        ("UNCERTAIN", "uncertain"),
    ]


@pytest.mark.parametrize("record, field", [(REQUEST, "bindings"), (_report(), "artifacts"), (_result(S1), "status")])
def test_records_are_immutable(record, field):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(record, field, getattr(record, field))


# --- root bindings -----------------------------------------------------------------------------------


@pytest.mark.parametrize("args", [("s1", "exports"), (ArtifactId("s1"), " "), (ArtifactId("s1"), None)])
def test_root_binding_validates_types_and_nonblank_key(args):
    with pytest.raises(ValueError):
        ArtifactRootBinding(*args)


# --- required artifacts ------------------------------------------------------------------------------


def test_required_artifacts_are_audio_then_new_fixed_inputs_in_order():
    assert REQUEST.required_artifacts == REQUIRED


def test_required_artifacts_keep_exact_metadata_including_older_production():
    required = {a.artifact_id: a for a in REQUEST.required_artifacts}
    assert required[ArtifactId("s1")] is S1 and S1.revision == Revision("r1") != STATE.revision
    assert required[ArtifactId("in-t3")] is IN_T3 and IN_T3.artifact_id not in STATE.audio.artifact_ids


def test_a_fixed_input_that_is_also_a_member_is_required_once_at_its_member_position():
    ids = [a.artifact_id for a in REQUEST.required_artifacts]
    assert ids.count(IN_T2.artifact_id) == 1 and ids.index(IN_T2.artifact_id) == 1


def test_a_state_whose_inputs_are_all_current_requires_only_the_audio():
    state = _state(members=(S1, IN_T2, MIX, IN_T3, IN_T1))
    assert StateIntegrityRequest(state, _bindings()).required_artifacts == (S1, IN_T2, MIX, IN_T3, IN_T1)


def test_required_artifacts_is_read_only():
    with pytest.raises(AttributeError):
        REQUEST.required_artifacts = ()


# --- request bindings --------------------------------------------------------------------------------


def test_binding_order_is_unrestricted_and_roots_may_differ():
    bindings = tuple(reversed(_bindings(REQUIRED[:3], "exports"))) + _bindings(REQUIRED[3:], "inputs")
    assert StateIntegrityRequest(STATE, bindings).bindings == bindings


@pytest.mark.parametrize(
    "bindings, message",
    [
        (_bindings(REQUIRED[:-1]), "in-t1"),  # missing superseded fixed input
        (_bindings(REQUIRED[1:]), "s1"),  # missing current member
        (_bindings() + _bindings((S1,), "other"), "duplicate"),
        (_bindings() + (ArtifactRootBinding(ArtifactId("stray"), "exports"),), "stray"),
        ((), "missing"),
    ],
    ids=["missing-input", "missing-member", "duplicate", "extra", "empty"],
)
def test_bindings_must_name_every_required_artifact_exactly_once(bindings, message):
    with pytest.raises(ValueError, match=message):
        StateIntegrityRequest(STATE, bindings)


@pytest.mark.parametrize(
    "state, bindings",
    [
        (STATE.audio, _bindings()),
        (STATE, list(_bindings())),
        (STATE, (*_bindings()[:-1], "in-t1")),
    ],
)
def test_request_validates_types(state, bindings):
    with pytest.raises(ValueError):
        StateIntegrityRequest(state, bindings)


# --- artifact results --------------------------------------------------------------------------------


def test_verified_requires_the_exact_recorded_hash():
    assert _result(S1).observed_sha256 == S1.content_hash
    with pytest.raises(ValueError, match="VERIFIED"):
        _result(S1, VERIFIED, _hash("9"))
    with pytest.raises(ValueError, match="VERIFIED"):
        _result(S1, VERIFIED, None)


@pytest.mark.parametrize("status", [FAILED, UNCERTAIN])
@pytest.mark.parametrize("observed", ["match", _hash("9"), None], ids=["matching", "different", "absent"])
def test_failed_and_uncertain_may_carry_any_valid_hash_or_none(status, observed):
    assert _result(S1, status, observed, "metadata differs").status is status


def test_matching_hash_with_failed_format_or_metadata_stays_failed():
    failed = _result(S1, FAILED, "match", "sample rate in file header differs from the record")
    assert failed.observed_sha256 == S1.content_hash and failed.status is FAILED


@pytest.mark.parametrize(
    "observed",
    ["A" * 64, "a" * 63, "a" * 65, "g" * 64, " " + "a" * 63, "a" * 64 + "\n", 0],
    ids=["uppercase", "short", "long", "non-hex", "leading-space", "trailing-newline", "int"],
)
@pytest.mark.parametrize("status", [VERIFIED, FAILED, UNCERTAIN])
def test_observed_hash_must_be_lowercase_sha256(status, observed):
    with pytest.raises(ValueError):
        ArtifactIntegrityResult(S1, status, observed, "inspected")


@pytest.mark.parametrize(
    "args",
    [
        (S1.artifact_id, VERIFIED, S1.content_hash, "inspected"),
        (S1, "verified", S1.content_hash, "inspected"),
        (S1, FAILED, None, " "),
        (S1, FAILED, None, None),
    ],
)
def test_result_validates_types_and_explanation(args):
    with pytest.raises(ValueError):
        ArtifactIntegrityResult(*args)


# --- reports -----------------------------------------------------------------------------------------


def test_report_with_every_artifact_verified():
    report = _report()
    assert [r.artifact for r in report.artifacts] == list(REQUIRED)
    assert report.status is VERIFIED


@pytest.mark.parametrize(
    "statuses, aggregate",
    [
        ([VERIFIED, VERIFIED, VERIFIED, VERIFIED, VERIFIED], VERIFIED),
        ([VERIFIED, UNCERTAIN, VERIFIED, VERIFIED, VERIFIED], UNCERTAIN),
        ([VERIFIED, VERIFIED, FAILED, VERIFIED, VERIFIED], FAILED),
        ([UNCERTAIN, FAILED, UNCERTAIN, UNCERTAIN, UNCERTAIN], FAILED),  # failure dominates uncertainty
        ([FAILED, VERIFIED, VERIFIED, VERIFIED, UNCERTAIN], FAILED),
        ([UNCERTAIN] * 5, UNCERTAIN),
    ],
)
def test_aggregate_status_precedence(statuses, aggregate):
    assert _report(statuses).status is aggregate


def test_aggregate_status_is_derived_and_not_a_field():
    assert "status" not in {f.name for f in dataclasses.fields(StateIntegrityReport)}
    with pytest.raises(AttributeError):
        _report().status = VERIFIED


def test_report_keeps_every_result_after_a_failure():
    report = _report([FAILED, UNCERTAIN, UNCERTAIN, UNCERTAIN, UNCERTAIN])
    assert len(report.artifacts) == len(REQUIRED) and report.status is FAILED


def _results(artifacts) -> tuple[ArtifactIntegrityResult, ...]:
    return tuple(_result(a) for a in artifacts)


@pytest.mark.parametrize(
    "results",
    [
        _results(REQUIRED[:-1]),  # missing a retained fixed input
        _results(REQUIRED + (S1,)),  # duplicate
        _results((REQUIRED[1], REQUIRED[0], *REQUIRED[2:])),  # wrong order
        _results((*REQUIRED[:-1], _artifact("stray", "r0", ("t1",)))),  # foreign artifact
        _results((dataclasses.replace(S1, location="outputs/elsewhere.wav"), *REQUIRED[1:])),  # altered metadata
        _results((dataclasses.replace(S1, content_hash=_hash("9")), *REQUIRED[1:])),  # altered recorded hash
        (),
    ],
    ids=["missing", "duplicate", "order", "foreign", "location", "hash", "empty"],
)
def test_report_requires_complete_ordered_exact_coverage(results):
    with pytest.raises(ValueError):
        StateIntegrityReport(REQUEST, results)


@pytest.mark.parametrize(
    "args",
    [(STATE, _results(REQUIRED)), (REQUEST, list(_results(REQUIRED))), (REQUEST, (*_results(REQUIRED[:-1]), IN_T1))],
)
def test_report_validates_types(args):
    with pytest.raises(ValueError):
        StateIntegrityReport(*args)


# --- port --------------------------------------------------------------------------------------------


def test_inspector_protocol_shape():
    assert typing.Protocol in StateIntegrityInspector.__mro__
    assert not getattr(StateIntegrityInspector, "_is_runtime_protocol", False)
    assert typing.get_type_hints(StateIntegrityInspector.inspect) == {
        "request": StateIntegrityRequest,
        "return": StateIntegrityReport,
    }


class _ScriptedInspector:
    """A test double returning a prepared report; it establishes the response contract only."""

    def __init__(self, report):
        self.report = report

    def inspect(self, request):
        return self.report


@pytest.mark.parametrize(
    "statuses", [[VERIFIED] * 5, [VERIFIED, FAILED, UNCERTAIN, UNCERTAIN, UNCERTAIN], [UNCERTAIN] * 5]
)
def test_valid_responses_with_any_outcome(statuses):
    inspector: StateIntegrityInspector = _ScriptedInspector(_report(statuses))
    assert validate_state_integrity_response(REQUEST, inspector.inspect(REQUEST)) is None


OTHER_ROOTS = StateIntegrityRequest(STATE, _bindings(root="elsewhere"))
REORDERED = StateIntegrityRequest(STATE, tuple(reversed(_bindings())))
OTHER_STATE = StateIntegrityRequest(_state(revision="r3", members=(S1, IN_T2, _artifact("mix", "r3", ("t1", "t2", "t3"), ArtifactKind.MIX, digest="3"))), _bindings())  # fmt: skip


@pytest.mark.parametrize(
    "response_request",
    [OTHER_ROOTS, REORDERED, OTHER_STATE],
    ids=["other-roots", "reordered-bindings", "other-state"],
)
def test_response_must_answer_exactly_the_submitted_request(response_request):
    response = StateIntegrityReport(response_request, tuple(_result(a) for a in response_request.required_artifacts))
    with pytest.raises(ValueError, match="request"):
        validate_state_integrity_response(REQUEST, response)


def test_an_equal_separately_built_request_is_accepted():
    rebuilt = StateIntegrityRequest(_state(), _bindings())
    assert rebuilt == REQUEST and rebuilt is not REQUEST
    validate_state_integrity_response(REQUEST, _report(request=rebuilt))


@pytest.mark.parametrize(
    "args", [("request", _report()), (REQUEST, "report"), (REQUEST, REQUEST), (_report(), _report())]
)
def test_response_validator_checks_types(args):
    with pytest.raises(ValueError):
        validate_state_integrity_response(*args)
