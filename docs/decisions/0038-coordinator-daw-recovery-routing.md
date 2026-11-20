# ADR-038 — Coordinator DAW recovery routing (2026-11-19)

- **Status:** Proposed, for owner and Codex review. It records Increment 4 of the [coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md), revision 1, with the owner's decisions D1–D6. It was implemented tests-first and is uncommitted.
- **Builds on:** ADR-030 to 037, all Proposed.
- **Outside this ADR:** durable restart (Increment 5), CLI wiring and `daw recover` (Increment 6), installation, live FL Studio calls, and the offline commit-exception issue (a separate task).

## Decision

1. **One recovery routine replaces the interim stops.** `recover_daw` (in `core.loop.daw_recovery`) replaces the stops of ADR-036 and ADR-037 that ended a run with "recovery not attempted". It covers:
   - off-target and uncertain or invalid writes;
   - every failure between a confirmed write and the checkpoint (export preparation included);
   - rejected imports and continuity rejections;
   - unaccepted candidates.

   It enters as `CONFIRMED_WRITE`, `OFF_TARGET` or `UNCERTAIN_WRITE` (`DawRecoveryEntry`). An uncertain or invalid reply takes its effective entry from the journal's lineage.
2. **Established acceptance stays authoritative.** `require_recovery_authority` refuses (`ValueError`) unless the acceptance authority is known and the selected reference is the attempt's own before reference.
   - **After acceptance:** a committed or found acceptance, and any failure after it, never reaches recovery.
   - **Unknown authority:** `ACCEPTANCE_COMMIT_UNCERTAIN` never reaches it either.
3. **A recovery-lineage query (F8).**
   - **Port:** `DawRecoveryController` gains `recovery_lineage(root)` and `recovery_snapshot(root)`.
   - **Records:** they return `DawRecoveryLineage` and `DawRecoverySnapshot`, read in one locked journal pass. The snapshot is coherent, like `continuity_snapshot`: the lineage and control state are read before and after one fresh inspection.
   - **Scope:** the query describes a root whatever its outcome (confirmed, off target, uncertain or pending). A refused root has no lineage.
   - **Anchors unchanged:** `state_anchor` and `continuity_snapshot` keep their confirmed-only rules, and export anchors are untouched.
   - **Precondition:** recovery runs only when:
     - the lineage's exact apply request is this attempt's;
     - no foreign reservation or resolution follows the root;
     - every unresolved command is the lineage's own.

     Otherwise recovery is withheld (the only remaining "recovery not attempted").
   - **Enforced atomically at the reservation** *(revised after review on 2026-11-20; the first version checked the lineage only before preparing the restoration, so foreign history appearing in between was caught only by the final snapshot, after `restore_gain` had been sent)*.
     - **The request:** the coordinator's `DawRestoreRequest` carries `own_lineage_only=True`, a new trailing field that defaults to `False` and is passed through by `gate_daw_restore`.
     - **The journal:** for such a request, the FL journal refuses the reservation (`foreign_history`) when any reservation or resolution outside the root's lineage follows the root. It decides this inside the same transaction that would reserve and charge.
     - **The coordinator:** treats a refusal with no journaled charge as withheld: nothing written, nothing charged.
     - **Unchanged:** requests without the flag behave as before (ADR-032).
4. **The steps.**
   1. **Reconciliation:** an uncertain root gets one reconciliation. `daw_recovery_decision` (G15, `core.admission.daw`) maps the finding to the steps:
      - `AT_RECORDED_START`: resolve, then restore if not exact;
      - `AT_COMMANDED`, `NEITHER` or `AMBIGUOUS`: restore, then resolve citing the restoration;
      - `IDENTITY_UNVERIFIED`: nothing.

      A known write is restored without a reconciliation.
   2. **Restoration:** at most one exact restoration to the journaled target, through `gate_daw_restore`. It is charged once, with one grant shared by the run, the authorization and the FL journal. That agreement is checked after the call; a disagreement is `RUNTIME_CONTRACT_FAILED`.
   3. **An unconfirmed restoration:** reconciled once, and resolved only where its finding permits.
5. **Fader evidence: only the final fresh reading (F10).**
   - **Source:** one final recovery snapshot, taken after the last step.
   - **Requirement:** its fresh reading must equal the saved target exactly, in both coordinates with signed zeros distinguished. The lineage must be the attempt's own with nothing unresolved.
   - **`EXACT_RESTORE`:** additionally needs the confirmed restoration to be the latest reservation, and its `after` reading to equal the fresh reading exactly.
   - **`EXACT_OBSERVATION`:** only when no restoration was written (`NoWrite`).
   - **Never evidence:** a resolution, a reconciliation, a tolerance match or an unconfirmed restoration.
   - **VERIFIED:** the retained-audio integrity inspection (`recover_saved`) runs only after the evidence passes, and VERIFIED needs both.
6. **D1.** The restore target is the journaled start. It can differ from the saved target within the start tolerance, and such a restoration is reported `FAILED` (`RESTORATION_FAILED`), never VERIFIED.
7. **D2 (revised).** A `DAW_WRITE` needs three free `RECOVERY_OPERATION` slots (`DAW_WRITE_RECOVERY_RESERVE`). That covers the worst complete path: the authority query after an unknown commit, one restoration and one integrity inspection. A render keeps one (`RENDER_RECOVERY_RESERVE`), so offline is unchanged.
8. **D3.** Cancellation and the elapsed limit still block recovery, failing closed.
9. **D4.** A recovered uncertain or invalid DAW write terminates with `RENDER_FAILED` or `INVALID_RENDER_RESPONSE`. On the DAW path these reasons mean "the DAW fader write (the render stage) had an uncertain or invalid outcome", not an offline render. Their explanation begins "DAW write:".
10. **D5.** Only a candidate rejected by verification, with a VERIFIED rollback, continues the loop. Its continuation is marked `DawContinuationDisposition.ROLLED_BACK`. A recovered uncertain write terminates.
11. **D6: the terminal reason by precedence.** The first that applies:
    1. a command unresolved: `DAW_COMMAND_UNRESOLVED`;
    2. the conversion blocked: `DAW_CONVERSION_BLOCKED`;
    3. nothing performed or withheld: `REQUIRED_RECOVERY_NOT_PERFORMED`, or `DAW_CONTINUITY_FAILED` for a continuity entry;
    4. a contract disagreement: `RUNTIME_CONTRACT_FAILED`;
    5. rollback `FAILED`: `RESTORATION_FAILED`;
    6. rollback `UNCERTAIN`: `RESTORATION_UNCERTAIN`;
    7. VERIFIED: the original cause.
12. **Pending effect.** `RESTORATION_UNCERTAIN` joins the permitted markers. Each marker is set from the validated reply before the reply is recorded:
    - before a restoration: `DAW_COMMAND_UNRESOLVED`;
    - after a confirmed one, or the root's validated resolution: `RESTORATION_UNCERTAIN` (or the block);
    - when exact evidence exists: the recovery counts as performed;
    - after any validated resolution, whether of a root or of a restoration: the remaining facts are settled before the reply is recorded *(revised after review on 2026-11-20; the first version settled them for root resolutions only, so a recording exception after an off-target restoration's resolution reported `DAW_COMMAND_UNRESOLVED` although nothing was unresolved)*.

    Exceptions never trigger recovery.
13. **Records and trace.**
    - **New payloads:** `DawRecoveryDecision`, `DawRestoreTarget` and the restore-gate verdicts.
    - **`DawRollbackRecord`** (new module `core.records.daw_rollback`): the standalone selected-state rollback of an attempt without an execution receipt, validated against its before state.
    - **New `DAW_STATE` request:** `DawLineageQuery`.
    - **Recovery intents** carry call, attempt and execution IDs. A restoration intent's event usage must equal its authorization's charge.
    - **Recovery IDs** derive from the execution token (`recovery_ids`). A token that cannot carry them is refused before any write.

## Rationale

- **Lineage, not anchor.** Recovery needs facts about a root whatever its outcome. Weakening the export anchor to cover off-target or uncertain writes would let an export rest on an unconfirmed source.
- **Fresh reading only.** A historical restoration reading cannot show a later manual move. Only the final coherent fresh reading, compared exactly, can.
- **Three slots.** Reserving the complete worst path before the write means recovery can never stop on the count limit halfway through.

## Consequences

- **Superseded expectations, recorded in the plan:**
  - The interim-stop tests of Increments 2 and 3 now expect recovery.
  - Four earlier mutation probes target code that no longer exists, and Increment 4 probes replace them.
  - The plan's test 5 expected `DAW_COMMAND_UNRESOLVED` for an off-target write with a lost restoration reply. With the restoration reconciled and resolved as step 3 specifies, the result is `DAW_CONVERSION_BLOCKED` with an `UNCERTAIN` rollback. The unresolved case is pinned by a restoration whose reply is lost while the fader moves to neither reference.
- **The `AMBIGUOUS` finding cannot be produced by an admissible write under the calibrated profile.** The smallest advertised cut (0.1 dB) exceeds the start dB tolerance (0.05 dB). It is pinned by the decision-rule contract tests and by a test of the same fail-closed `NoWrite` edge.
- **Not exercised in-process:** the reuse of an already journaled restoration charge (the replay rule for Increment 5). A controller-level replay is tested and charged once.
- **Evidence.**
  - **Tests:** 43 recovery tests (`tests/unit/test_daw_recovery.py`; 41 before the review fixes) and 15 lineage and contract tests (`tests/unit/test_daw_recovery_lineage.py`; 14 before). They use the real FL controller and SQLite journal against device script v2 (and v1) on the fake host, the real export handoff, analyzer and integrity inspector over temporary synthetic WAVs, with labelled doubles for decisions and diagnosis.
  - **Mutation probes:** 21 new (17, plus 4 for the review fixes), all caught. Every earlier probe set is still caught, except the four superseded ones. One adapter probe's anchor had been stale since an earlier reformat; it was rerun with a corrected anchor and is caught.
  - **None of this is live FL evidence.**

## Sources

- [Coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md): the Increment 4 plan, revision 1.
- Owner approval of 2026-11-19: the revised plan with D1–D6; export-anchor semantics unchanged; established acceptance preserved.
