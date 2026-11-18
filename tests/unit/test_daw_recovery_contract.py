"""Core recovery and reconciliation contract (plan 2026-11-18-daw-recovery-reconciliation, revision 2 + Amendment R).

Pure records, validators and the restore gate; nothing here talks to FL Studio or a journal.
"""

import math
from dataclasses import replace

import pytest

from autonomous_mixing.core.admission.daw import gate_daw_restore
from autonomous_mixing.core.ports.daw_recovery import (
    validate_daw_control_state,
    validate_daw_reconciliation,
    validate_daw_resolution,
    validate_daw_restore_outcome,
)
from autonomous_mixing.core.records.budgets import BudgetGrant, BudgetOperation, BudgetUsage
from autonomous_mixing.core.records.daw import DawFaderReading, DawTrackBinding
from autonomous_mixing.core.records.daw_control import (
    DawBuild,
    DawControlProfile,
    DawInspection,
    DawInspectRequest,
    DawRange,
    DawRefusalCode,
    DawUncertainReason,
    DawWriteConfirmed,
)
from autonomous_mixing.core.records.daw_recovery import (
    DawCommandKind,
    DawControlState,
    DawReconcileFinding,
    DawReconcileRequest,
    DawReconciliation,
    DawRecoveryAuthorization,
    DawRecoveryCharge,
    DawResolutionAuthorization,
    DawResolutionKind,
    DawResolutionRecorded,
    DawResolutionRefusalCode,
    DawResolutionRefused,
    DawResolutionRequest,
    DawRestoreConfirmed,
    DawRestoreGateCode,
    DawRestoreMismatch,
    DawRestoreNoWrite,
    DawRestorePermitted,
    DawRestoreRefused,
    DawRestoreRejected,
    DawRestoreTarget,
    DawRestoreUncertain,
    DawTargetBasis,
    DawUnresolvedCommand,
    classify_reconciliation,
)
from autonomous_mixing.core.records.identifiers import ExecutionId, ProjectId, TrackId, VersionRef
from autonomous_mixing.core.records.results import PortFailure, PortFailureCode, Success

TITLE, NAME = "Thesis Bass Bridge Test", "Bass"
BUILD = DawBuild("Producer Edition v26.1.7 [build 5654]", 45)
CONVERSION = VersionRef("fl-mixer-h125", "v1")
ROOT, RECOVERY = "apply_root_0001", "restore_cmd_0001"
START = DawFaderReading(0.76, -1.1527416706085205)


def profile(**changes):
    base = DawControlProfile(VersionRef("fl-control-profile", "1"), BUILD, TITLE, 2, NAME, CONVERSION,
                             DawRange(0.62, 0.86), DawRange(-5.330810795137555, 1.7038931131020334), 0.1, 3.0,
                             0.001, 0.05, 0.05, 0.001)  # fmt: skip
    return replace(base, **changes)


def binding(**changes):
    base = DawTrackBinding(VersionRef("binding", "1"), ProjectId("p1"), TrackId("t1"), TITLE, 2, NAME, 2.0, START)
    return replace(base, **changes)


def inspection(fader=(0.7257, -2.1527), build=BUILD, name=NAME, prof=None, bind=None):
    prof, bind = prof or profile(), bind or binding()
    reading = DawFaderReading(*fader) if name == NAME and fader is not None else None
    return DawInspection(DawInspectRequest(bind, prof), build, TITLE, 2, name, reading)


def grant(operation=BudgetOperation.RECOVERY_OPERATION):
    return BudgetGrant(operation, BudgetUsage(1, 1, 1, 1, 1.0))


def target(reading=START, source=ROOT, conversion=CONVERSION, basis=DawTargetBasis.OUTCOME_BEFORE):
    return DawRestoreTarget(source, basis, reading, conversion)


def authorization(**changes):
    base = DawRecoveryAuthorization("recovery_auth_0001", ExecutionId("e1"), VersionRef("controller", "1"), target(),
                                    grant())  # fmt: skip
    return replace(base, **changes)


def unresolved(command_id=ROOT, kind=DawCommandKind.APPLY, root=ROOT, attempt=None, seq=1):
    return DawUnresolvedCommand(command_id, kind, root, attempt, seq, START, DawFaderReading(0.7257, -2.1527), None)


def state(*items, charges=()):
    return DawControlState(tuple(items), frozenset(), tuple(charges))


def gate(auth=None, *, insp=None, prof=None, bind=None, control=None, command=RECOVERY):
    prof, bind = prof or profile(), bind or binding()
    return gate_daw_restore(auth or authorization(), binding=bind, inspection=insp or inspection(prof=prof, bind=bind),
                            profile=prof, recovery_command_id=command, control_state=control or state())  # fmt: skip


def permitted(**kwargs):
    result = gate(**kwargs)
    assert isinstance(result, DawRestorePermitted), result
    return result.request


# --- records ----------------------------------------------------------------------------------------------------------


def test_authorization_requires_a_recovery_operation_grant():
    with pytest.raises(ValueError):
        authorization(budget_grant=grant(BudgetOperation.RENDER_CALL))
    with pytest.raises(ValueError):
        authorization(budget_grant="grant")
    assert authorization().budget_grant.operation is BudgetOperation.RECOVERY_OPERATION


@pytest.mark.parametrize(
    "changes",
    [{"authorization_id": "short"}, {"execution_id": "e1"}, {"authorized_by": None}, {"target": START}],
)
def test_authorization_rejects_invalid_fields(changes):
    with pytest.raises(ValueError):
        authorization(**changes)


@pytest.mark.parametrize(
    "args",
    [("bad id", DawTargetBasis.OUTCOME_BEFORE, START, CONVERSION), (ROOT, "outcome_before", START, CONVERSION),
     (ROOT, DawTargetBasis.OUTCOME_BEFORE, (0.76, -1.15), CONVERSION), (ROOT, DawTargetBasis.OUTCOME_BEFORE, START, "c")],
)  # fmt: skip
def test_restore_target_rejects_invalid_fields(args):
    with pytest.raises(ValueError):
        DawRestoreTarget(*args)


def test_unresolved_command_lineage_invariants():
    assert unresolved().attempt is None
    assert unresolved(RECOVERY, DawCommandKind.RESTORE, ROOT, 1, 2).root_command_id == ROOT
    for args in (
        (ROOT, DawCommandKind.APPLY, "other_root_01", None, 1),  # an apply is its own root
        (ROOT, DawCommandKind.APPLY, ROOT, 1, 1),  # an apply has no attempt
        (RECOVERY, DawCommandKind.RESTORE, RECOVERY, 1, 1),  # a restoration is never its own root
        (RECOVERY, DawCommandKind.RESTORE, ROOT, 0, 1),
        (RECOVERY, DawCommandKind.RESTORE, ROOT, None, 1),
        (ROOT, DawCommandKind.APPLY, ROOT, None, 0),
        (ROOT, DawCommandKind.APPLY, ROOT, None, True),
    ):
        with pytest.raises(ValueError):
            unresolved(*args)


def test_control_state_orders_by_ledger_and_checks_types():
    first, second = unresolved(seq=1), unresolved(RECOVERY, DawCommandKind.RESTORE, ROOT, 1, 3)
    assert state(first, second).unresolved == (first, second)
    with pytest.raises(ValueError):
        state(second, first)
    with pytest.raises(ValueError):
        DawControlState((first,), {CONVERSION}, ())
    charge = DawRecoveryCharge("recovery_auth_0001", RECOVERY, 3, grant())
    assert state(charges=(charge,)).recovery_charges == (charge,)
    with pytest.raises(ValueError):
        DawRecoveryCharge("recovery_auth_0001", RECOVERY, 3, grant(BudgetOperation.ATTEMPT))


def test_restore_outcomes_decide_exactness_on_both_readings():
    after = START
    assert DawRestoreConfirmed(RECOVERY, DawFaderReading(0.7257, -2.15), after, START, False).after == START
    with pytest.raises(ValueError):  # normalized equal, dB one float step away: not exact
        DawRestoreConfirmed(RECOVERY, START, DawFaderReading(0.76, math.nextafter(START.db, 0)), START, False)
    with pytest.raises(ValueError):
        DawRestoreConfirmed(RECOVERY, START, DawFaderReading(0.7600001, START.db), START, False)
    near = DawFaderReading(0.7600001, START.db)
    assert DawRestoreMismatch(RECOVERY, START, near, START, False).after == near
    with pytest.raises(ValueError):  # an exact landing is not a mismatch
        DawRestoreMismatch(RECOVERY, START, START, START, False)
    with pytest.raises(ValueError):
        DawRestoreConfirmed(RECOVERY, START, START, START, 1)
    assert DawRestoreRefused(RECOVERY, DawRefusalCode.UNSUPPORTED, "v1 script", None).observed is None
    assert DawRestoreUncertain(RECOVERY, DawUncertainReason.TIMEOUT, START, None, "no reply").after is None
    with pytest.raises(ValueError):
        DawRestoreUncertain(RECOVERY, DawUncertainReason.TIMEOUT, START, None, "")


# --- reconciliation classification (Amendment: from the fresh reading) ----------------------------------------------

S = DawFaderReading(0.76, -1.0)
C = DawFaderReading(0.7257, -2.0)
# Dyadic tolerances and readings make the inclusive edges exact in binary floats (core compares exactly).
DYADIC = dict(start_normalized_tolerance=0.0625, start_db_tolerance=0.125, readback_normalized_tolerance=0.0625,
              success_db_tolerance=0.125)  # fmt: skip
S2, C2 = DawFaderReading(0.75, -1.0), DawFaderReading(0.5, -2.0)


@pytest.mark.parametrize(
    "reading,finding,ms,mc",
    [
        (S2, DawReconcileFinding.AT_RECORDED_START, True, False),
        (C2, DawReconcileFinding.AT_COMMANDED, False, True),
        (DawFaderReading(0.25, -3.0), DawReconcileFinding.NEITHER, False, False),
        (DawFaderReading(0.8125, -0.875), DawReconcileFinding.AT_RECORDED_START, True, False),  # on both edges
        (DawFaderReading(math.nextafter(0.8125, 1), -1.0), DawReconcileFinding.NEITHER, False, False),
        (DawFaderReading(0.4375, -2.125), DawReconcileFinding.AT_COMMANDED, False, True),  # on both edges
        (DawFaderReading(0.5, math.nextafter(-2.125, -9)), DawReconcileFinding.NEITHER, False, False),
    ],
)
def test_findings_are_classified_from_the_reading_alone(reading, finding, ms, mc):
    assert classify_reconciliation(profile(**DYADIC), S2, C2, reading) == (finding, ms, mc)


def test_a_reading_matching_both_values_is_ambiguous_for_a_cut_of_twice_the_tolerance():
    # revision 1 claimed a 0.1 dB cut always exceeds 2 x 0.05 dB; it does not (it is equal). With tolerance t, a
    # change of exactly 2t leaves a midpoint reading that matches both the start and the commanded value.
    start, commanded = DawFaderReading(0.75, -1.0), DawFaderReading(0.75, -1.25)  # a 2 x 0.125 dB change
    reading = DawFaderReading(0.75, -1.125)
    result = classify_reconciliation(profile(**DYADIC), start, commanded, reading)
    assert result == (DawReconcileFinding.AMBIGUOUS, True, True)


def _reconciliation(finding, insp, ms=False, mc=False, start=S, commanded=C):
    return DawReconciliation("reconcile_0001", ROOT, 4, finding, insp, start, commanded, ms, mc, False)


def test_reconciliation_record_must_agree_with_its_own_reading():
    at_start = inspection(fader=(0.76, -1.0))
    assert _reconciliation(DawReconcileFinding.AT_RECORDED_START, at_start, ms=True).finding is not None
    with pytest.raises(ValueError):  # the reading says start, the record claims commanded
        _reconciliation(DawReconcileFinding.AT_COMMANDED, at_start, mc=True)
    with pytest.raises(ValueError):  # matches disagree with the reading
        _reconciliation(DawReconcileFinding.AT_RECORDED_START, at_start, ms=True, mc=True)


def test_identity_unverified_records():
    assert _reconciliation(DawReconcileFinding.IDENTITY_UNVERIFIED, None).inspection is None  # the read failed
    mismatch = inspection(name="Sub")
    assert _reconciliation(DawReconcileFinding.IDENTITY_UNVERIFIED, mismatch).inspection is mismatch
    other_build = inspection(fader=(0.76, -1.0), build=DawBuild("other", 45))
    assert _reconciliation(DawReconcileFinding.IDENTITY_UNVERIFIED, other_build).finding.name == "IDENTITY_UNVERIFIED"
    with pytest.raises(ValueError):  # a matching inspection cannot be labelled unverified
        _reconciliation(DawReconcileFinding.IDENTITY_UNVERIFIED, inspection(fader=(0.76, -1.0)))
    with pytest.raises(ValueError):  # unverified never claims a match
        _reconciliation(DawReconcileFinding.IDENTITY_UNVERIFIED, None, ms=True)
    with pytest.raises(ValueError):
        _reconciliation(DawReconcileFinding.NEITHER, None)


def test_reconcile_and_resolution_requests_check_their_ids():
    with pytest.raises(ValueError):
        DawReconcileRequest(ROOT, ROOT, binding(), profile())
    with pytest.raises(ValueError):
        DawReconcileRequest("reconcile_0001", ROOT, binding(insert_name="Sub"), profile())
    auth = DawResolutionAuthorization("resolve_auth_0001", VersionRef("controller", "1"))
    for args in (("resolve_0001", ROOT, DawResolutionKind.APPLIED_AS_COMMANDED, "resolve_0001", auth),
                 ("resolve_0001", ROOT, "applied", "reconcile_0001", auth),
                 ("resolve_0001", ROOT, DawResolutionKind.APPLIED_AS_COMMANDED, "reconcile_0001", None)):  # fmt: skip
        with pytest.raises(ValueError):
            DawResolutionRequest(*args)


def test_resolution_recorded_pairs_kind_with_root():
    recorded = DawResolutionRecorded("resolve_0001", ROOT, ROOT, DawResolutionKind.APPLIED_AS_COMMANDED, C,
                                     "reconcile_0001", 5, False)  # fmt: skip
    assert recorded.root_command_id == ROOT
    with pytest.raises(ValueError):
        replace(recorded, ledger_seq=0)
    with pytest.raises(ValueError):
        DawResolutionRefused("resolve_0001", "stale", "x")


# --- gate ------------------------------------------------------------------------------------------------------------


def test_gate_permits_an_upward_restoration_and_builds_a_consistent_request():
    request = permitted()  # current -2.15 dB, target -1.15 dB: upward
    assert request.authorization.target.reading == START and request.recovery_command_id == RECOVERY
    assert request.inspection.fader.db < START.db


def test_gate_permits_a_downward_restoration():
    lower = target(DawFaderReading(0.7257, -2.1527))
    request = permitted(auth=authorization(target=lower), insp=inspection(fader=(0.76, -1.1527416706085205)))
    assert request.inspection.fader.db > lower.reading.db


def test_gate_permits_a_start_outside_the_calibrated_range():
    permitted(insp=inspection(fader=(0.95, 3.9)))  # recovering from anywhere is the point


def test_gate_refuses_in_order():
    def rejected(code, **kwargs):
        result = gate(**kwargs)
        assert isinstance(result, DawRestoreRejected) and result.code is code, result

    rejected(DawRestoreGateCode.PROFILE_SCOPE, prof=profile(insert_name="Sub"),
             insp=inspection())  # fmt: skip
    rejected(DawRestoreGateCode.CONVERSION_MISMATCH, auth=authorization(target=target(conversion=VersionRef("c", "2"))),
             insp=inspection(name="Sub"))  # conversion is checked before identity  # fmt: skip
    rejected(DawRestoreGateCode.IDENTITY_MISMATCH, insp=inspection(name="Sub"))
    rejected(DawRestoreGateCode.UNSUPPORTED_BUILD, insp=inspection(build=DawBuild("other", 45)))
    rejected(DawRestoreGateCode.UNRESOLVED_OTHER, control=state(unresolved("other_apply_01", root="other_apply_01")))
    rejected(DawRestoreGateCode.TARGET_OUT_OF_RANGE,
             auth=authorization(target=target(DawFaderReading(0.87, 2.0))))  # fmt: skip
    rejected(DawRestoreGateCode.TARGET_OUT_OF_RANGE,
             auth=authorization(target=target(DawFaderReading(0.76, 1.8))))  # dB above the range  # fmt: skip


def test_gate_permits_restoration_while_only_its_own_lineage_is_unresolved():
    lineage = state(unresolved(), unresolved("restore_old_0001", DawCommandKind.RESTORE, ROOT, 1, 2))
    permitted(control=lineage)


def test_gate_no_write_on_an_exact_current_reading():
    result = gate(insp=inspection(fader=(START.normalized, START.db)))
    assert isinstance(result, DawRestoreNoWrite) and result.request.inspection.fader == START


def test_gate_wiring_errors_raise():
    with pytest.raises(ValueError):
        gate(command=ROOT)  # the recovery command reuses the root's ID
    with pytest.raises(ValueError):
        gate(command="recovery_auth_0001")  # reuses the authorization's ID
    with pytest.raises(ValueError):
        gate(insp=inspection(bind=binding(reference=VersionRef("binding", "2"))))  # answers another binding
    with pytest.raises(ValueError):
        gate_daw_restore("auth", binding=binding(), inspection=inspection(), profile=profile(),
                         recovery_command_id=RECOVERY, control_state=state())  # fmt: skip


def test_restore_request_validates_itself_independently_of_the_gate():
    request = permitted()
    with pytest.raises(ValueError):
        replace(request, inspection=inspection(name="Sub"))
    with pytest.raises(ValueError):
        replace(request, inspection=inspection(build=DawBuild("other", 45)))
    with pytest.raises(ValueError):
        replace(request, authorization=authorization(target=target(DawFaderReading(0.87, 2.0))))
    with pytest.raises(ValueError):
        replace(request, authorization=authorization(target=target(conversion=VersionRef("c", "2"))))
    with pytest.raises(ValueError):
        replace(request, recovery_command_id=ROOT)


# --- validators -------------------------------------------------------------------------------------------------------


def test_restore_outcome_validation():
    request = permitted()
    before = request.inspection.fader
    validate_daw_restore_outcome(request, DawRestoreConfirmed(RECOVERY, before, START, START, False))
    validate_daw_restore_outcome(request, DawRestoreMismatch(RECOVERY, before, DawFaderReading(0.7601, -1.15), START,
                                                             False))  # fmt: skip
    validate_daw_restore_outcome(request, DawRestoreRefused(RECOVERY, DawRefusalCode.UNSUPPORTED, "v1", None))
    validate_daw_restore_outcome(request, DawRestoreUncertain(RECOVERY, DawUncertainReason.TIMEOUT, None, None, "x"))
    other = DawFaderReading(0.7, -3.0)
    for bad in (
        DawRestoreConfirmed("restore_other_01", before, START, START, False),  # another command
        DawRestoreConfirmed(RECOVERY, before, other, other, False),  # another target
        DawRestoreConfirmed(RECOVERY, DawFaderReading(0.7, -3.0), START, START, False),  # started elsewhere
        DawWriteConfirmed(RECOVERY, before, START, 0.76, START.db - before.db, False),  # an apply outcome
    ):
        with pytest.raises(ValueError):
            validate_daw_restore_outcome(request, bad)


def test_reconciliation_resolution_and_state_validation():
    request = DawReconcileRequest("reconcile_0001", ROOT, binding(), profile())
    good = _reconciliation(DawReconcileFinding.AT_RECORDED_START, inspection(fader=(0.76, -1.0)), ms=True)
    validate_daw_reconciliation(request, Success(good))
    validate_daw_reconciliation(request, PortFailure(PortFailureCode.INVALID_INPUT, "unknown subject", False))
    with pytest.raises(ValueError):
        validate_daw_reconciliation(request, Success(replace(good, subject_command_id="apply_other_01")))
    with pytest.raises(ValueError):
        validate_daw_reconciliation(replace(request, binding=binding(reference=VersionRef("binding", "2"))),
                                    Success(good))  # fmt: skip
    auth = DawResolutionAuthorization("resolve_auth_0001", VersionRef("controller", "1"))
    resolution = DawResolutionRequest("resolve_0001", ROOT, DawResolutionKind.NOT_APPLIED_OR_REVERTED,
                                      "reconcile_0001", auth)  # fmt: skip
    recorded = DawResolutionRecorded("resolve_0001", ROOT, ROOT, DawResolutionKind.NOT_APPLIED_OR_REVERTED, S,
                                     "reconcile_0001", 5, False)  # fmt: skip
    validate_daw_resolution(resolution, recorded)
    validate_daw_resolution(resolution, DawResolutionRefused("resolve_0001", DawResolutionRefusalCode.STALE_EVIDENCE,
                                                             "newer evidence"))  # fmt: skip
    with pytest.raises(ValueError):
        validate_daw_resolution(resolution, replace(recorded, kind=DawResolutionKind.APPLIED_AS_COMMANDED))
    with pytest.raises(ValueError):
        validate_daw_resolution(resolution, replace(recorded, evidence_id="reconcile_9999"))
    validate_daw_control_state(Success(state(unresolved())))
    validate_daw_control_state(PortFailure(PortFailureCode.UNAVAILABLE, "journal", False))
    with pytest.raises(ValueError):
        validate_daw_control_state(Success("state"))
