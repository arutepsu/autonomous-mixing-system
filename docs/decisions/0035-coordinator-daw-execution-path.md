# ADR-035 — Coordinator DAW execution path: contracts (2026-11-19)

- **Status:** Proposed, for owner and Codex review. It records the contracts of Increment 1 of the [coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md) (revision 3, owner-approved on 2026-11-19), implemented tests-first and uncommitted.
  - **No loop behaviour uses them yet.** Coordinator integration (increments 2 to 6), installation and live FL Studio calls are outside this ADR.
  - **Scope comes from [ADR-034](0034-bounded-operator-assisted-fl-studio-integration.md)** (Proposed). ADR-019, ADR-030 to 033, ADR-001 and ADR-004 are unchanged except where stated below.

## Decision

1. **Two execution plans (G1).** `PreparedCorrection.plan` is `RenderPlan | DawCorrectionPlan`. The offline variant's checks are unchanged.
   - `DawCorrectionPlan` (`core.records.daw_correction`) carries the execution ID and the stable apply command ID, the before and candidate revisions, the target and requested gain, the complete binding and profile, the declared routing, and the export outputs, timeline, format, render spec and workspace.
   - `validate_against(before)` requires the before state to hold the plan's binding as DAW parameter state, a new candidate revision, and exact coverage of the target's affected set.
2. **An optional DAW session (G2).** `RunRequest.daw_session: DawSessionConfig | None`. It holds the binding, profile, policy gain permission, routing, export settings and the export root key.
   - With a session, the initial state must hold the session's binding as DAW parameter state.
   - Without one, the initial state must hold none.
3. **Option A: DAW parameter state in `ProjectState` (G4; ADR-034).**
   - **New records:** `DawTrackParameter(binding, selected_gain_db, confirmed_fader)` and `DawParameterState(project_id, revision, tracks)`, validated against the snapshot. `ProjectState.daw` holds the latter.
   - **`TrackGainState` keeps its ADR-019 meaning** and is never written by the DAW path.
   - **The offline path refuses a DAW-controlled state** in two places: `preflight_execution` rejects it with `DAW_CONTROLLED_STATE`, and `derive_candidate_project_state` raises.
   - **A module move:** to avoid the import cycle project → daw → actions → project, `DawFaderReading` and `DawTrackBinding` move unchanged from `core.records.daw` to the new dependency-free `core.records.daw_binding`, and every import is rewritten.
4. **DAW receipt and candidate (G5).** `core.loop.daw_transitions` is pure and not called by any stage yet.
   - **`daw_execution_receipt`** builds the `CANDIDATE_READY` receipt from a `DawImportVerified` only. Its candidates are the import's artifacts, and its superseded IDs are exactly the import's explicit replacements.
   - **Bound to the journaled apply** *(revised twice after review on 2026-11-19. The first version took a free `Action`. The second identified the request through the success tolerance, so a substitute 0.01 dB away under the same command ID passed)*.
     - **Input:** every derivation function takes the `DawApplyRequest` that was dispatched, and the action comes from it.
     - **The anchor carries the request:** a `CONFIRMED_WRITE` `DawStateAnchor` carries the exact journaled request, rebuilt by the FL adapter from its stored text, which the rebuilt record must serialize back to exactly. Its command, execution, binding and profile must be the anchor's own. Restoration and resolution anchors carry none.
     - **`daw_apply_export_problem` requires:**
       - exact agreement with that request, which by the anchor and request invariants also fixes the command, execution, binding and profile;
       - the action to act on the export's before state and bound track;
       - separately, the anchored reading to lie within the success tolerance of the apply's resolved target. That tolerance checks the achieved result, never request identity.

     The apply must also have been resolved from the selected state's DAW parameter state: the same revision, selected gain and confirmed fader.
   - **`validate_daw_execution`** replaces `validate_gain_execution` for the DAW path. The fixed-input rule would call a declared dependent's stem unrelated.
   - **`derive_daw_candidate_state`** keeps `processing` (apart from its revision), takes audio membership from the unchanged `derive_candidate_checkpoint`, and writes only the DAW parameter state: the selected gain, plus the anchored reading as the confirmed fader.
   - **One coverage rule** for `DawExportRequest` and `DawCorrectionPlan`: `daw_export_coverage_problem`. It adds two refusals (G5): a before member containing the target that is not replaced, and any group containing the target.
5. **Rollback: three claims kept apart (G6; ADR-034, revision 3).**
   - **New fields on `RollbackReceipt`:** `scope` (`COMPLETE_SAVED_STATE` for offline, `SELECTED_STATE_WITH_FADER` for DAW), `retained_audio` and `fader_evidence`.
   - **A complete saved-state rollback** is never claimed for a state with DAW parameter state.
   - **A VERIFIED selected-state rollback requires:**
     - verified retained-audio integrity;
     - for every DAW-controlled track, one `DawRollbackFaderEvidence` item that:
       - is bound to the same fader (equal complete binding, not only an equal reference) and to that track's saved target (its confirmed reading);
       - has a reading equal to the target exactly in both coordinates, with signed zeros distinguished;
       - follows the latest relevant write reservation. An `EXACT_RESTORE` must be that reservation itself; an `EXACT_OBSERVATION` must have been captured at or after it, by ledger position.
   - **A resolution clears uncertainty only.** It is representable as evidence (`RESOLUTION`) so that it can be refused, and it never supports VERIFIED.
   - **No receipt claims complete project rollback** (routing, plugins, automation, saved file, fresh exports).
6. **Suspension (G7).**
   - `DawCorrectionSuspended` is a run-trace payload: the checkpoint written when a run waits for the operator's export. It binds:
     - the prepared correction's parts and the selected state, which equals the accepted reference's state;
     - the issued tokens, feedback history and observations;
     - the journaled apply request, which must execute the decision's action and pass `daw_apply_export_problem`, and its confirmed write;
     - the awaiting export, whose anchor must be that write and its reading, and whose settings must all equal the plan's: binding, profile, routing, outputs, timeline, format, render spec and workspace *(revised after review on 2026-11-19; the first version omitted timeline, format and render spec)*.
   - `RunSuspended` is a run result, not a terminal report. Its trace ends with the checkpoint, and its run has a DAW session.
   - `RunState.AWAITING_OPERATOR_EXPORT` is added.
   - Resumption itself (G8) is Increment 3.
7. **Trace kinds (G9):** `DAW_INSPECT`, `DAW_APPLY`, `DAW_RECOVERY`, `DAW_STATE` (with the new `DawStateQuery`), `EXPORT_PREPARE` and `EXPORT_IMPORT`, each with its exact request and outcome types.
   - All DAW intents must name the accepted project.
   - DAW apply intents carry the call, attempt and request execution IDs, and act on exactly the accepted revision.
   - Export intents carry the call, attempt and export execution IDs.
8. **Budget (G10).**
   - **New operation:** `BudgetOperation.DAW_WRITE`, with `BudgetLimits.max_daw_writes` (default 0, so an offline run permits none), `BudgetUsage.daw_writes` and `BudgetBlockReason.DAW_WRITE_LIMIT`.
   - **Recovery slot:** like a render, a DAW write needs an unused recovery slot, because its effect may need an exact restoration.
   - **Usage history:** trace usage may not decrease in `daw_writes`.
9. **Termination reasons (G12):**
   - `DAW_WRITE_LIMIT` maps to `LIMIT_REACHED`;
   - `DAW_COMMAND_UNRESOLVED`, `DAW_CONVERSION_BLOCKED`, `DAW_CONTINUITY_FAILED` and `EXPORT_IMPORT_REJECTED` map to `FAILED`.
10. **A deliberate exception to "exact required fields".** The new fields of existing records are trailing, with offline defaults, so that every offline constructor and value stays exactly as before: `ProjectState.daw`, `RollbackReceipt.scope`, `.retained_audio` and `.fader_evidence`, `BudgetLimits.max_daw_writes`, `BudgetUsage.daw_writes` and `RunRequest.daw_session`. The tests that pin fields, signatures and enum members are updated explicitly to name these additions.

## Rationale

- **Correction context.** The correction context and receipt validation were tied to fixed-stem rendering. A separate plan variant and a separate validation keep both paths truthful without weakening the offline contracts.
- **Parameter state.** Holding it in `ProjectState` makes it part of the selected state's identity, so rollback references and acceptance compare it exactly.
- **Rollback scope.** An explicit scope and typed evidence make it impossible to build, and so to report, a fader restoration or a resolution as a project rollback.

## Consequences

- **Not yet available:**
  - **Capability view (G3), the decision-round branch and the dispatch:** Increment 2.
  - **Resumption (G8) and elapsed-time segments across the pause (G11):** Increment 3.
  - **Recovery routing, `daw_recovery_decision` (G15) and the coordinator-level rollback regression:** Increment 4.
  - **Durable journals (G13) and the orphaned-command start check (G14):** Increment 5.
- **FL journal identity.** The canonical identity of FL restoration requests and charges now includes `daw_writes: 0`, because `BudgetUsage` gained a field. A v2 FL journal that already held restorations would refuse to replay those IDs, reporting them as conflicts. That is a safe failure. No runtime FL journal has been persisted (device script v2 was never installed or run live), and older charges still load.
- **Evidence.**
  - **Contract tests:** 73 in `tests/unit/test_daw_coordinator_contracts.py` (59 before the review fixes), including the owner's regressions: resolution only, a one-ULP mismatch in each coordinate, the exact reading, and failed retained-audio integrity. They also cover evidence bound to another fader or saved target, and evidence ordered before the latest write reservation.
  - **Mutation probes:** 33 (20, plus 7 and 6 for the two review rounds), each reverted byte-identically and caught.
  - **None of this is live FL evidence.**

## Sources

- [Coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md), revision 3; [ADR-034](0034-bounded-operator-assisted-fl-studio-integration.md); [ADR-019](0019-fixed-input-gain-and-composite-project-state.md); [ADR-032](0032-daw-exact-recovery-and-reconciliation.md); [ADR-033](0033-daw-export-handoff-and-verified-import.md).
- Owner approval of 2026-11-19: Increment 1 tests-first; exact-reading evidence bound to the same fader and saved target, ordered after the latest relevant write reservation.
