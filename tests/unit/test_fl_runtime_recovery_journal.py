"""Journal schema v2: shared ledger, lineage, agreement, recovery charges and resolution evidence (Amendment R)."""

import json
import re
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from fl_recovery_support import (
    journal_apply,
    journal_restore,
    restore_request,
    restore_wire,
    with_target,
)

from autonomous_mixing.adapters.fl_studio.journal import JournalError, JournalRefusal, SqliteCommandJournal
from autonomous_mixing.adapters.fl_studio.serialization import grant_json, restore_request_json
from autonomous_mixing.core.records.daw_binding import DawFaderReading
from autonomous_mixing.core.records.daw_control import DawRefusalCode, DawUncertainReason
from autonomous_mixing.core.records.daw_recovery import (
    DawCommandKind,
    DawRestoreConfirmed,
    DawRestoreMismatch,
    DawRestoreRefused,
    DawRestoreUncertain,
    DawTargetBasis,
)

SOURCE = Path(__file__).resolve().parents[2] / "src/autonomous_mixing/adapters/fl_studio/journal.py"
ROOT = "apply_root_0001"


def open_journal(path):
    return SqliteCommandJournal(path, session_key="session-1")


@pytest.fixture
def journal(tmp_path):
    instance = open_journal(tmp_path / "journal.db")
    yield instance
    instance.close()


def confirmed(request):
    return DawRestoreConfirmed(request.recovery_command_id, request.inspection.fader, request.target, request.target,
                               False)  # fmt: skip


def mismatch(request):
    near = DawFaderReading(request.target.normalized + 1e-9, request.target.db)
    return DawRestoreMismatch(request.recovery_command_id, request.inspection.fader, near, request.target, False)


def uncertain(request):
    return DawRestoreUncertain(request.recovery_command_id, DawUncertainReason.TIMEOUT, request.inspection.fader,
                               None, "no reply")  # fmt: skip


def wire_refused(request):
    return DawRestoreRefused(request.recovery_command_id, DawRefusalCode.UNSUPPORTED, "v1 script", None)


def unresolved_ids(journal):
    return [item.command_id for item in journal.control_state().unresolved]


def reconcile(journal, reconciliation_id, subject, finding, reading=(0.76, -1.1527416706085205)):
    return journal.record_reconciliation(
        reconciliation_id, subject, {"finding": finding, "reading": {"normalized": reading[0], "db": reading[1]}}
    )


def resolve(journal, resolution_id, subject, kind, evidence, authorization="resolve_auth_0001"):
    return journal.record_resolution(resolution_id, subject, kind, evidence, authorization_id=authorization)


# --- schema v2 and migration ------------------------------------------------------------------------------------------


def _v1_database(path):
    """A v1 journal with apply rows of each outcome type, written by the v1 schema exactly."""
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE inspections (command_id TEXT PRIMARY KEY)")
    connection.execute("CREATE TABLE metadata (session_key TEXT NOT NULL)")
    connection.execute("INSERT INTO metadata VALUES ('session-1')")
    connection.execute("""CREATE TABLE commands (
        command_id TEXT PRIMARY KEY, request_json TEXT NOT NULL, command_json TEXT NOT NULL,
        fingerprint TEXT NOT NULL, outcome_json TEXT)""")
    connection.execute("PRAGMA user_version=1")
    connection.commit()
    connection.close()


def test_a_v1_journal_migrates_in_place_with_unchanged_field_contents(tmp_path):
    path = tmp_path / "journal.db"
    _v1_database(path)
    # fill the v1 schema through a v1-compatible writer: the v2 code must accept and preserve these rows
    with sqlite3.connect(path) as connection:
        from fl_recovery_support import apply_written
        from fl_runtime_support import make_request

        from autonomous_mixing.adapters.fl_studio.journal import _fingerprint
        from autonomous_mixing.adapters.fl_studio.profile import make_apply_command
        from autonomous_mixing.adapters.fl_studio.serialization import outcome_json, request_json

        for command_id, kind in (("v1_confirmed_01", "confirmed"), ("v1_offtarget_1", "off_target"),
                                 ("v1_refused_001", "refused"), ("v1_uncertain_1", "uncertain")):  # fmt: skip
            request = make_request(command_id)
            req, cmd = request_json(request), json.dumps(make_apply_command(request), sort_keys=True)
            connection.execute("INSERT INTO commands VALUES (?,?,?,?,?)",
                               (command_id, req, cmd, _fingerprint(req, cmd), outcome_json(apply_written(request,
                                                                                                         kind))))  # fmt: skip
    before = (
        sqlite3.connect(path)
        .execute("SELECT command_id,request_json,command_json,fingerprint,outcome_json FROM commands ORDER BY rowid")
        .fetchall()
    )
    journal = open_journal(path)
    try:
        after = journal._connection.execute(
            "SELECT command_id,request_json,command_json,fingerprint,outcome_json FROM commands ORDER BY rowid"
        ).fetchall()
        assert after == before  # unchanged field contents, field by field
        assert journal._connection.execute("PRAGMA user_version").fetchone()[0] == 2
        ledger = journal._connection.execute("SELECT seq,entry_kind,entry_id FROM ledger ORDER BY seq").fetchall()
        assert [(kind, entry) for _, kind, entry in ledger] == [("RESERVED", row[0]) for row in before]  # rowid order
        assert unresolved_ids(journal) == ["v1_uncertain_1"]
    finally:
        journal.close()
    reopened = open_journal(path)  # idempotent reopen
    assert reopened._connection.execute("SELECT count(*) FROM ledger").fetchone()[0] == len(before)
    reopened.close()


def test_unknown_versions_and_tampered_ledger_rows_fail_closed(tmp_path):
    path = tmp_path / "journal.db"
    journal = open_journal(path)
    journal_apply(journal, ROOT)
    journal.close()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE ledger SET record_json='{\"x\":1}' WHERE entry_id=?", (ROOT,))
    with pytest.raises(JournalError):
        open_journal(path)
    other = tmp_path / "other.db"
    with sqlite3.connect(other) as connection:
        connection.execute("PRAGMA user_version=3")
        connection.execute("CREATE TABLE x (y)")
    with pytest.raises(JournalError):
        open_journal(other)


# --- append-only ledger, guarded completion --------------------------------------------------------------------------


def test_source_has_no_update_or_delete_on_the_ledger_and_one_guarded_update_on_commands():
    source = SOURCE.read_text(encoding="utf-8")
    statements = re.findall(r"\b(UPDATE|DELETE\s+FROM)\s+(\w+)", source)
    assert ("UPDATE", "ledger") not in statements and ("DELETE FROM", "ledger") not in statements
    updates = [table for verb, table in statements if verb == "UPDATE"]
    assert updates == ["commands"]
    assert "WHERE command_id=? AND outcome_json IS NULL" in source


def test_ledger_rows_only_grow(journal):
    counts = []

    def count():
        counts.append(journal._connection.execute("SELECT count(*) FROM ledger").fetchone()[0])

    journal_apply(journal, ROOT, kind="uncertain")
    count()
    journal_restore(journal, ROOT, "restore_cmd_0001", "recovery_auth_0001", outcome=uncertain)
    count()
    reconcile(journal, "reconcile_0001", "restore_cmd_0001", "at_commanded")
    count()
    assert counts == sorted(counts) and counts[0] < counts[-1]


def test_finish_cannot_overwrite_an_outcome(journal):
    request = journal_apply(journal, ROOT)
    with pytest.raises(JournalError):
        from fl_recovery_support import apply_written

        journal.finish(ROOT, apply_written(request, "off_target"))


# --- lineage, agreement and charges ----------------------------------------------------------------------------------


def test_target_basis_follows_the_recorded_outcome(journal):
    journal_apply(journal, "apply_confirmed1")
    basis, reading, _ = journal.restore_target("apply_confirmed1")
    assert basis is DawTargetBasis.OUTCOME_BEFORE and reading == DawFaderReading(0.76, -1.1527416706085205)
    journal2_id = "apply_pending_01"
    journal_apply(journal, "apply_uncertain1", kind="uncertain_no_before")
    basis, _, _ = journal.restore_target("apply_uncertain1")
    assert basis is DawTargetBasis.REQUEST_INSPECTION
    with pytest.raises(JournalRefusal):
        journal.restore_target(journal2_id)  # unknown root


def test_a_refused_root_has_no_restoration_target(journal):
    journal_apply(journal, ROOT, kind="refused")
    with pytest.raises(JournalRefusal) as info:
        journal.restore_target(ROOT)
    assert info.value.code == "no_target"


def test_restoration_attempts_are_numbered_within_their_root(journal):
    journal_apply(journal, ROOT, kind="uncertain")
    journal_restore(journal, ROOT, "restore_cmd_0001", "recovery_auth_0001", outcome=mismatch)
    journal_restore(journal, ROOT, "restore_cmd_0002", "recovery_auth_0002", outcome=uncertain)
    items = {item.command_id: item for item in journal.control_state().unresolved}
    assert items["restore_cmd_0001"].attempt == 1 and items["restore_cmd_0002"].attempt == 2
    assert (
        items["restore_cmd_0002"].kind is DawCommandKind.RESTORE and items["restore_cmd_0002"].root_command_id == ROOT
    )


def test_restoration_of_another_root_is_refused_while_a_foreign_command_is_unresolved(journal):
    journal_apply(journal, "apply_other_001")
    journal_apply(journal, ROOT, kind="uncertain")
    with pytest.raises(JournalRefusal) as info:
        journal_restore(journal, "apply_other_001", "restore_cmd_0001", "recovery_auth_0001")
    assert info.value.code == "unresolved_other"


@pytest.mark.parametrize("what", ["binding", "profile", "reading", "basis", "conversion"])
def test_restore_request_must_agree_with_its_root(journal, what):
    journal_apply(journal, ROOT)
    request = restore_request(journal, ROOT, "restore_cmd_0001", "recovery_auth_0001")
    if what == "binding":
        binding = replace(request.binding, baseline_gain_db=3.0)
        inspection = replace(request.inspection, request=replace(request.inspection.request, binding=binding))
        request = replace(request, binding=binding, inspection=inspection)
    elif what == "profile":
        profile = replace(request.profile, reference=replace(request.profile.reference, version="2"))
        inspection = replace(request.inspection, request=replace(request.inspection.request, profile=profile))
        request = replace(request, profile=profile, inspection=inspection)
    elif what == "reading":
        request = with_target(request, DawFaderReading(0.75, -1.4433227))
    elif what == "basis":
        request = with_target(request, request.target, DawTargetBasis.REQUEST_INSPECTION)
    else:
        target = replace(request.authorization.target, conversion=replace(request.profile.conversion, version="v0"))
        profile = replace(request.profile, conversion=target.conversion)
        inspection = replace(request.inspection, request=replace(request.inspection.request, profile=profile))
        request = replace(request, profile=profile, inspection=inspection,
                          authorization=replace(request.authorization, target=target))  # fmt: skip
    with pytest.raises(JournalRefusal) as info:
        journal.reserve_restore("restore_cmd_0001", restore_request_json(request), json.dumps(restore_wire(request)),
                                authorization_id="recovery_auth_0001",
                                grant_json=grant_json(request.authorization.budget_grant))  # fmt: skip
    assert info.value.code == "agreement"
    assert journal.control_state().recovery_charges == ()  # refused before reservation: nothing charged


def test_a_restoration_is_charged_once_in_its_reservation_transaction(journal):
    journal_apply(journal, ROOT, kind="uncertain")
    request = journal_restore(journal, ROOT, "restore_cmd_0001", "recovery_auth_0001")
    charges = journal.control_state().recovery_charges
    assert [(c.authorization_id, c.recovery_command_id) for c in charges] == [("recovery_auth_0001",
                                                                                "restore_cmd_0001")]  # fmt: skip
    reserved = journal._connection.execute("SELECT seq FROM ledger WHERE entry_kind='RESERVED' AND entry_id=?",
                                           ("restore_cmd_0001",)).fetchone()[0]  # fmt: skip
    assert charges[0].ledger_seq == reserved + 1  # same transaction, directly after the reservation
    journal.finish("restore_cmd_0001", uncertain(request))
    assert len(journal.control_state().recovery_charges) == 1  # an uncertain outcome is never refunded


def test_authorization_and_id_reuse_are_refused_without_another_charge(journal):
    journal_apply(journal, ROOT, kind="uncertain")
    journal_restore(journal, ROOT, "restore_cmd_0001", "recovery_auth_0001", outcome=mismatch)
    with pytest.raises(JournalRefusal) as info:
        journal_restore(journal, ROOT, "restore_cmd_0002", "recovery_auth_0001")  # same authorization
    assert info.value.code == "duplicate_id_conflict"
    with pytest.raises(JournalRefusal):
        journal_restore(journal, ROOT, "restore_cmd_0001", "recovery_auth_0003")  # same recovery ID
    with pytest.raises(JournalRefusal):
        journal_restore(journal, ROOT, "recovery_auth_0001", "recovery_auth_0004")  # ID reused across namespaces
    assert len(journal.control_state().recovery_charges) == 1


# --- derived unresolved state ---------------------------------------------------------------------------------------


def test_unresolved_derivation_for_each_outcome(journal):
    journal_apply(journal, "apply_confirm_1")
    journal_apply(journal, "apply_refused_1", kind="refused")
    assert unresolved_ids(journal) == []
    journal_apply(journal, ROOT, kind="uncertain")
    assert unresolved_ids(journal) == [ROOT]
    for outcome, rid, aid in ((wire_refused, "restore_cmd_0001", "recovery_auth_0001"),
                              (mismatch, "restore_cmd_0002", "recovery_auth_0002"),
                              (uncertain, "restore_cmd_0003", "recovery_auth_0003")):  # fmt: skip
        journal_restore(journal, ROOT, rid, aid, outcome=outcome)
    # failed, mismatched and uncertain restorations stay unresolved beside their root
    assert unresolved_ids(journal) == [ROOT, "restore_cmd_0001", "restore_cmd_0002", "restore_cmd_0003"]
    journal_restore(journal, ROOT, "restore_cmd_0004", "recovery_auth_0004", outcome=confirmed)
    assert "restore_cmd_0004" not in unresolved_ids(journal)  # a confirmed restoration is resolved by itself
    assert journal.pending_or_uncertain() is True


def test_off_target_blocks_persist_through_restoration_resolution_and_reopen(tmp_path):
    path = tmp_path / "journal.db"
    journal = open_journal(path)
    journal_apply(journal, ROOT, kind="off_target")
    blocked = journal.blocked_conversions()
    journal_restore(journal, ROOT, "restore_cmd_0001", "recovery_auth_0001", outcome=confirmed)
    assert journal.blocked_conversions() == blocked != frozenset()
    journal.close()
    assert open_journal(path).blocked_conversions() == blocked


# --- resolution evidence (Amendment R) -------------------------------------------------------------------------------


def _uncertain_root_with_restore(journal, outcome):
    journal_apply(journal, ROOT, kind="uncertain")
    return journal_restore(journal, ROOT, "restore_cmd_0001", "recovery_auth_0001", outcome=outcome)


def test_a_childs_finding_never_resolves_its_root(journal):
    # A goes from 0 dB toward -1 dB; restoration R tries to return to 0 dB but leaves -1 dB. Reconciling R finds
    # AT_RECORDED_START, meaning R's own start (-1 dB): it must not resolve A as NOT_APPLIED_OR_REVERTED.
    _uncertain_root_with_restore(journal, uncertain)
    reconcile(journal, "reconcile_0001", "restore_cmd_0001", "at_recorded_start")
    with pytest.raises(JournalRefusal) as info:
        resolve(journal, "resolve_0001", ROOT, "not_applied_or_reverted", "reconcile_0001")
    assert info.value.code == "evidence_not_for_subject"
    assert ROOT in unresolved_ids(journal)


def test_a_confirmed_restoration_resolves_its_root(journal):
    _uncertain_root_with_restore(journal, confirmed)
    seq, root, reading = resolve(journal, "resolve_0001", ROOT, "restored_to_recorded_start", "restore_cmd_0001")
    assert root == ROOT and reading == DawFaderReading(0.76, -1.1527416706085205)
    assert unresolved_ids(journal) == []


def test_a_mismatched_restoration_cannot_resolve_its_root(journal):
    _uncertain_root_with_restore(journal, mismatch)
    with pytest.raises(JournalRefusal) as info:
        resolve(journal, "resolve_0001", ROOT, "restored_to_recorded_start", "restore_cmd_0001")
    assert info.value.code == "kind_not_supported_by_evidence"


@pytest.mark.parametrize("newer", ["neither", "identity_unverified", "ambiguous"])
def test_a_newer_unfavourable_finding_makes_an_older_favourable_one_stale(journal, newer):
    journal_apply(journal, ROOT, kind="uncertain")
    reconcile(journal, "reconcile_0001", ROOT, "at_recorded_start")
    reconcile(journal, "reconcile_0002", ROOT, newer)
    with pytest.raises(JournalRefusal) as info:
        resolve(journal, "resolve_0001", ROOT, "not_applied_or_reverted", "reconcile_0001")
    assert info.value.code == "stale_evidence"


def test_a_newer_restoration_supersedes_an_older_reconciliation_and_vice_versa(journal):
    journal_apply(journal, ROOT, kind="uncertain")
    reconcile(journal, "reconcile_0001", ROOT, "at_recorded_start")
    journal_restore(journal, ROOT, "restore_cmd_0001", "recovery_auth_0001", outcome=confirmed)
    with pytest.raises(JournalRefusal) as info:
        resolve(journal, "resolve_0001", ROOT, "not_applied_or_reverted", "reconcile_0001")
    assert info.value.code == "stale_evidence"
    reconcile(journal, "reconcile_0002", ROOT, "at_commanded")  # newer than the restoration
    with pytest.raises(JournalRefusal) as info:
        resolve(journal, "resolve_0002", ROOT, "restored_to_recorded_start", "restore_cmd_0001")
    assert info.value.code == "stale_evidence"
    resolve(journal, "resolve_0003", ROOT, "applied_as_commanded", "reconcile_0002")
    assert unresolved_ids(journal) == []


def test_a_reservation_after_the_evidence_makes_it_unusable(journal):
    journal_apply(journal, ROOT, kind="uncertain")
    reconcile(journal, "reconcile_0001", ROOT, "at_recorded_start")
    journal_restore(journal, ROOT, "restore_cmd_0001", "recovery_auth_0001")  # pending, no outcome yet
    with pytest.raises(JournalRefusal) as info:
        resolve(journal, "resolve_0001", ROOT, "not_applied_or_reverted", "reconcile_0001")
    assert info.value.code == "reservation_after_evidence"


def test_resolution_kind_must_match_the_finding(journal):
    journal_apply(journal, ROOT, kind="uncertain")
    reconcile(journal, "reconcile_0001", ROOT, "at_recorded_start")
    with pytest.raises(JournalRefusal) as info:
        resolve(journal, "resolve_0001", ROOT, "applied_as_commanded", "reconcile_0001")
    assert info.value.code == "kind_not_supported_by_evidence"
    for finding, rid in (("neither", "reconcile_0002"),):
        reconcile(journal, rid, ROOT, finding)
        with pytest.raises(JournalRefusal):
            resolve(journal, "resolve_0002", ROOT, "not_applied_or_reverted", rid)


def test_resolution_resolves_earlier_restorations_of_the_root_only(journal):
    _uncertain_root_with_restore(journal, mismatch)
    reconcile(journal, "reconcile_0001", ROOT, "at_recorded_start")
    resolve(journal, "resolve_0001", ROOT, "not_applied_or_reverted", "reconcile_0001")
    assert unresolved_ids(journal) == []  # the earlier mismatched restoration is resolved with its root
    journal_apply(journal, "apply_second_01", kind="uncertain")
    journal_restore(journal, "apply_second_01", "restore_cmd_0002", "recovery_auth_0002", outcome=mismatch)
    assert unresolved_ids(journal) == ["apply_second_01", "restore_cmd_0002"]


def test_resolution_refusals_for_ids_subjects_and_unknown_evidence(journal):
    journal_apply(journal, ROOT, kind="uncertain")
    reconcile(journal, "reconcile_0001", ROOT, "at_recorded_start")
    with pytest.raises(JournalRefusal) as info:
        resolve(journal, "resolve_0001", ROOT, "not_applied_or_reverted", "reconcile_9999")
    assert info.value.code == "unknown_evidence"
    resolve(journal, "resolve_0001", ROOT, "not_applied_or_reverted", "reconcile_0001")
    with pytest.raises(JournalRefusal) as info:
        resolve(journal, "resolve_0002", ROOT, "not_applied_or_reverted", "reconcile_0001", "resolve_auth_0002")
    assert info.value.code == "subject_not_unresolved"
    journal_apply(journal, "apply_second_01", kind="uncertain")
    reconcile(journal, "reconcile_0002", "apply_second_01", "at_recorded_start")
    with pytest.raises(JournalRefusal) as info:
        resolve(journal, "resolve_0003", "apply_second_01", "not_applied_or_reverted", "reconcile_0002")
    assert info.value.code == "authorization_reused"
    with pytest.raises(JournalRefusal) as info:
        reconcile(journal, "reconcile_0003", "apply_confirmed9", "at_recorded_start")
    assert info.value.code == "subject_not_unresolved"


# --- crash persistence ------------------------------------------------------------------------------------------------


def test_a_crash_after_a_reserved_and_charged_restoration_leaves_it_pending_and_charged(tmp_path):
    path = tmp_path / "journal.db"
    journal = open_journal(path)
    journal_apply(journal, ROOT, kind="uncertain")
    journal.close()
    script = f"""
import sys
sys.path[:0] = {[str(Path(__file__).parent), str(Path(__file__).resolve().parents[2] / "src")]!r}
from pathlib import Path
from fl_recovery_support import journal_restore
from autonomous_mixing.adapters.fl_studio.journal import SqliteCommandJournal
journal = SqliteCommandJournal(Path({str(path)!r}), session_key="session-1")
journal_restore(journal, {ROOT!r}, "restore_cmd_0001", "recovery_auth_0001")
import os
os._exit(0)  # no close, no outcome
"""
    subprocess.run([sys.executable, "-c", script], check=True)
    reopened = open_journal(path)
    state = reopened.control_state()
    assert [item.command_id for item in state.unresolved] == [ROOT, "restore_cmd_0001"]
    assert state.unresolved[1].outcome is None and len(state.recovery_charges) == 1
    reopened.close()


# --- review findings 2026-11-18 (second review) --------------------------------------------------------------------


def test_newer_child_evidence_makes_older_root_evidence_stale(journal):
    # the restoration exists first; then an older root AT_RECORDED_START, then a newer child NEITHER
    _uncertain_root_with_restore(journal, uncertain)
    reconcile(journal, "reconcile_0001", ROOT, "at_recorded_start")
    reconcile(journal, "reconcile_0002", "restore_cmd_0001", "neither")
    with pytest.raises(JournalRefusal) as info:
        resolve(journal, "resolve_0001", ROOT, "not_applied_or_reverted", "reconcile_0001")
    assert info.value.code == "stale_evidence"
    assert unresolved_ids(journal) == [ROOT, "restore_cmd_0001"]


def test_newer_root_evidence_makes_older_child_evidence_stale(journal):
    _uncertain_root_with_restore(journal, uncertain)
    reconcile(journal, "reconcile_0001", "restore_cmd_0001", "at_commanded")
    reconcile(journal, "reconcile_0002", ROOT, "neither")
    with pytest.raises(JournalRefusal) as info:
        resolve(journal, "resolve_0001", "restore_cmd_0001", "applied_as_commanded", "reconcile_0001")
    assert info.value.code == "stale_evidence"


def test_a_consumed_resolution_authorization_id_is_reserved_in_the_shared_namespace(journal):
    journal_apply(journal, ROOT, kind="uncertain")
    reconcile(journal, "reconcile_0001", ROOT, "at_recorded_start")
    resolve(journal, "resolve_0001", ROOT, "not_applied_or_reverted", "reconcile_0001", "resolve_auth_0001")
    with pytest.raises(JournalError):
        journal_apply(journal, "resolve_auth_0001")  # an apply cannot take the consumed authorization's ID
    with pytest.raises(JournalError):
        journal._claim_inspect_id("resolve_auth_0001")
    journal_apply(journal, "apply_second_01", kind="uncertain")
    with pytest.raises(JournalRefusal) as info:
        reconcile(journal, "resolve_auth_0001", "apply_second_01", "at_recorded_start")
    assert info.value.code == "duplicate_id_conflict"
    with pytest.raises(JournalRefusal) as info:
        journal_restore(journal, "apply_second_01", "restore_cmd_0001", "resolve_auth_0001")
    assert info.value.code == "duplicate_id_conflict"
    with pytest.raises(JournalRefusal) as info:
        journal_restore(journal, "apply_second_01", "resolve_auth_0001", "recovery_auth_0001")
    assert info.value.code == "duplicate_id_conflict"


def _tamper(path, statement, params=()):
    with sqlite3.connect(path) as connection:
        connection.execute(statement, params)


def test_a_pending_command_without_its_reservation_fails_closed(tmp_path):
    path = tmp_path / "journal.db"
    journal = open_journal(path)
    journal_apply(journal, ROOT, pending=True)
    journal.close()
    _tamper(path, "DELETE FROM ledger WHERE entry_kind='RESERVED' AND entry_id=?", (ROOT,))
    with pytest.raises(JournalError):
        open_journal(path)  # never reported as "nothing unresolved"


def test_only_an_explicitly_marked_local_refusal_may_lack_a_reservation(tmp_path):
    from fl_recovery_support import apply_written
    from fl_runtime_support import make_request

    from autonomous_mixing.adapters.fl_studio.profile import make_apply_command
    from autonomous_mixing.adapters.fl_studio.serialization import request_json

    path = tmp_path / "journal.db"
    journal = open_journal(path)
    request = make_request("apply_local_0001")
    journal._remember_refusal(
        "apply_local_0001",
        request_json(request),
        json.dumps(make_apply_command(request), sort_keys=True),
        apply_written(request, "refused"),
    )
    assert unresolved_ids(journal) == []
    journal.close()
    reopened = open_journal(path)
    assert reopened.lookup("apply_local_0001").outcome is not None  # the marked refusal reopens fine
    reopened.close()
    _tamper(path, "DELETE FROM ledger WHERE entry_kind='LOCAL_REFUSAL' AND entry_id=?", ("apply_local_0001",))
    with pytest.raises(JournalError):
        open_journal(path)


def test_each_reserved_restoration_needs_exactly_one_matching_charge(tmp_path):
    path = tmp_path / "journal.db"
    journal = open_journal(path)
    journal_apply(journal, ROOT, kind="uncertain")
    journal_restore(journal, ROOT, "restore_cmd_0001", "recovery_auth_0001")
    journal.close()
    _tamper(path, "DELETE FROM ledger WHERE entry_kind='RECOVERY_CHARGED'")
    with pytest.raises(JournalError):
        open_journal(path)


def test_a_reservation_marker_on_the_wrong_kind_or_a_refusal_marker_on_a_write_fails_closed(tmp_path):
    from autonomous_mixing.adapters.fl_studio.journal import _ledger_fingerprint

    path = tmp_path / "journal.db"
    journal = open_journal(path)
    journal_apply(journal, ROOT, kind="uncertain")
    journal.close()
    record = '{"kind":"apply"}'
    _tamper(path, "INSERT INTO ledger (entry_kind,entry_id,subject_command_id,record_json,fingerprint) "
                  "VALUES ('LOCAL_REFUSAL',?,?,?,?)",
            (ROOT, ROOT, record, _ledger_fingerprint("LOCAL_REFUSAL", ROOT, ROOT, record)))  # fmt: skip
    with pytest.raises(JournalError):
        open_journal(path)  # a written (uncertain) command cannot also be a local refusal
