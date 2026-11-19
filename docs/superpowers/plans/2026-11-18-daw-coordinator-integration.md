# DAW Control, Recovery and Export Handoff in the Coordinator — Implementation Plan

> **Status: revision 2, for owner review (2026-11-18).**
> - **Increment 0 is done as documentation:** the owner decided D15, and ADR-034 is proposed.
> - **Increment 1 is implemented** (owner approval 2026-11-19; ADR-035 proposed; uncommitted; see "Increment 1 outcome").
> - **Increment 2 is implemented** (owner approval 2026-11-19; ADR-036 proposed; uncommitted; see "Increment 2 outcome").
> - **Increment 3 is implemented** (owner approval 2026-11-19, with amendment A; ADR-037 proposed; uncommitted; see "Increment 3 outcome").
> - **Increments 4–6 are not implemented and not yet authorized.** Each needs its own approval, and then runs tests-first and stays uncommitted for owner and Codex review.
> - **Never in scope here:** installation, live FL Studio calls, and automated saving or exporting.

**Revision 3 (2026-11-19, owner amendment):**
- **A resolution is not a rollback.** A resolution may clear uncertainty, but it never establishes a verified selected-state rollback.
- **What that rollback requires:** retained-audio integrity, and exact equality of both fader readings to the saved target, supported by recorded evidence.
- **Changes in this revision:** G6, step 7 and Increments 1 and 4 are updated in place, and the regression is specified under "Rollback evidence".

**Revision 2** records the owner's decision of 2026-11-18:
- **D15 adopted:** bounded, operator-assisted FL Studio runtime integration, initially limited to gain changes and verified manual exports.
- **G4 → Option A:** explicit DAW parameter state inside `ProjectState`, separate from `TrackGainState`.
- **G6:** fader restoration is distinguished from complete project rollback.

The scope-conflict section, G4, G6, Increment 0 and the open questions are updated in place. What they replace is stated in each.

**Goal:** let the controlled coordinator run one agent correction through the DAW path end to end:
- admission;
- a calibrated fader write;
- an explicit pause for the operator's manual export;
- a separate continuation that verifies the export and the DAW state;
- the existing analysis, verification and acceptance flow.

Failed or uncertain corrections go through authorized exact-target recovery. Unresolved commands and conversion blocks are preserved, and the offline workflow is unchanged.

**Builds on:**
- the committed DAW contracts and FL adapter (ADR-030 to 032, Proposed, in `335e904`);
- the uncommitted export handoff (ADR-033, Proposed);
- the controlled coordinator (`core.loop.coordinator`, NON_DURABLE).

## Scope conflict (resolved 2026-11-18)

**Resolution.**
- **The decision:** the owner adopted D15. [ADR-034](../../decisions/0034-bounded-operator-assisted-fl-studio-integration.md) (Proposed) records it and supersedes, in part, ADR-009's clause that FL Studio integration "is not scheduled". The ADR-009 file is unchanged.
- **Requirements:** `requirements.md` v1.2 adds FR-19 and FR-20 (Planned) and marks D15 decided.
- **Scope and roadmap:** both record the optional, bounded path. `.flp` manipulation stays excluded and FR-14 withdrawn.

**The conflict as first stated (superseded).** This integration makes operator-attended FL Studio control a runtime capability of the coordinator, and three documents stood against that:
- **D15** in `requirements.md`, the scope decision on adopting that capability, is still open;
- **ADR-009** (Accepted) says FL Studio integration "is deferred and optional: it is not scheduled";
- **The roadmap** lists DAW integration as optional.

The FL work so far was unintegrated feasibility infrastructure. Wiring it into the coordinator is a different step. **I do not select between these documents:** Increment 0 asks the owner to decide D15. If integration is adopted, a new ADR states the relationship to ADR-001 and ADR-009; ADR-009 is not edited.

## What exists (inspected 2026-11-18)

**Coordinator** (`core.loop`):

`run()` is one synchronous, non-resumable call ("this API does not resume runs"). Its stages run in this order:
1. `decision_round`: decision, gate, `admit_action`, the capability-bounds check, `preflight_execution`, which builds a `RenderPlan`, then `PreparedCorrection(plan: RenderPlan)`.
2. `correction.render`: the `RunRenderer` dispatch.
3. `verify_candidate`:
   - builds an `ExecutionReceipt` from the `RenderPlan`;
   - calls `derive_candidate_project_state`, which writes the requested gain into the target's `TrackGainState` on its fixed input;
   - runs one candidate analysis (candidate artifacts located through the plan's workspace root key) and the `ATTEMPT` verification;
   - takes a route.
4. The route leads to one of:
   - `acceptance_flow.accept`;
   - `recovery.restore_after_rejection`, a logical retention of the saved state plus an integrity inspection;
   - `recovery.failed_attempt`.

**Records and ports:**

| Area | Current shape |
| --- | --- |
| Trace | `OperationKind` covers ANALYZE, DIAGNOSE, DECIDE, RENDER, QUERY_ACCEPTANCE and INSPECT_STATE. `StageRequest` is the matching union. `DispatchIntent` is recorded before each dispatch. |
| Budgets | DECISION_CALL, ATTEMPT, RENDER_CALL and RECOVERY_OPERATION. `elapsed_seconds` comes from a monotonic `RunClock`. |
| Run records | `RunState` and `RunOutcome`. A `RunReport` requires a FINISHED run. `AttemptDisposition` ROLLED_BACK means "restoration of the complete before state is verified". |
| Journals | `InMemoryRunJournal` and the in-memory acceptance journal, both NON_DURABLE. No durable run journal exists. |
| Capabilities | `RunRequest.renderer_capabilities.gain_bounds` is one static declared range. |

**DAW pieces ready for composition:**
- **Parameter control:** `DawParameterController` (`inspect`, `apply`), `resolve_daw_gain`, `daw_gain_bounds` and `gate_daw_apply`.
- **Recovery:** `DawRecoveryController`, with `control_state`, `restore_target`, `reconcile`, `restore`, `resolve`, `state_anchor` and `continuity_snapshot`, plus `gate_daw_restore`.
- **Export:** `DawExportHandoff` (`prepare`, `import_files`) and `assess_daw_import`.
- **FL journal:** SQLite, durable across process crashes, with exactly one marker per command and charges committed before dispatch.

## Missing contracts (identified before proposing anything new)

| # | Gap | Why the existing contract cannot be reused | Proposed resolution (scope in ADR-034; contracts in ADR-035 with Increment 1) |
| --- | --- | --- | --- |
| G1 | The correction context is tied to `RenderPlan` | `PreparedCorrection` and `AssessedCorrection` require `plan: RenderPlan` and check its offline fields | A `DawCorrectionPlan` record. `PreparedCorrection.plan` becomes `RenderPlan \| DawCorrectionPlan`, with variant-specific invariants. The offline variant's checks are unchanged. |
| G2 | DAW session configuration | `RunRequest` holds no binding, profile, permission or `DawTrackState` | An optional `RunRequest.daw_session: DawSessionConfig \| None`. `None` keeps the offline run byte-identical. |
| G3 | The capability view is static | DAW bounds depend on the confirmed fader (`daw_gain_bounds`) | In a DAW session, the view uses `daw_gain_bounds` of the last confirmed `DawTrackState` instead of `renderer_capabilities`. It is a view only; the gate decides. |
| G4 | DAW parameter state in `ProjectState` | `TrackGainState` means gain on a fixed input stem; the approved design forbids reusing it for DAW state | **Decided: Option A** (owner, ADR-034). `ProjectState` gains an explicit DAW parameter state, holding the binding reference and the selected and confirmed fader readings per DAW-controlled track. `TrackGainState` and `processing` keep their ADR-019 meaning and are never written by the DAW path. Offline states carry none, and the offline renderer refuses a state that does. Exact names and invariants: Increment 1. *(Option B is withdrawn.)* |
| G5 | Receipt validation | `validate_gain_execution` requires superseding every artifact whose sources include the target, and nothing else. A declared dependent's stem does not contain the target, so it would be "unrelated". A group containing the target must be superseded, but `DawExportRequest` refuses `GROUP` outputs. | `daw_execution_receipt(verified, action)` and `validate_daw_execution`: the supersession set equals `DawImportVerified.replacements` and covers the target, the declared dependents and the master. A before state containing a group of the target is refused at preparation (`UNSUPPORTED_ROUTING`) until groups are covered. |
| G6 | Rollback meaning | ROLLED_BACK and `RollbackReceipt.VERIFIED` claim complete before-state restoration, which a fader restore cannot prove | **Decided** (ADR-034): three claims are kept apart in records.<br>**Fader restoration:** one fader's readings, returned exactly (ADR-032).<br>**Selected-state rollback:** the saved accepted state stays selected, its audio is retained and passes integrity inspection, and both fader readings are shown **exactly** equal to the saved target by recorded evidence (see "Rollback evidence"). A resolution clears uncertainty only and is never such evidence. *Revision 2 also accepted "a `NOT_APPLIED` resolution"; that is withdrawn.*<br>**Complete project rollback:** routing, plugins, automation, saved file and fresh exports. It is never claimed.<br>Increment 1 adds an explicit rollback scope to `RollbackReceipt` (offline: the complete saved state, as today; DAW: the selected state, with the project-level remainder unverified), so a report can never read a DAW rollback as a project rollback. |
| G7 | A suspended run | A `RunReport` requires FINISHED; `RunState` has no waiting state; no result type means "paused" | `RunState.AWAITING_OPERATOR_EXPORT`, plus a `RunSuspended` result (not a `RunReport`) carrying the `DawCorrectionSuspended` checkpoint. A suspension is not a termination, so no `TerminationReason` is added for it. |
| G8 | Resumption | `run()` refuses a journal with events | `resume(checkpoint, manifest, …)`. It rebuilds the progress from the journal snapshot and the checkpoint event, and refuses unless that event is the last one in the trace. |
| G9 | Trace kinds | No DAW or export operations | Six new `OperationKind`s, `StageRequest` members and outcome types: `DAW_INSPECT`, `DAW_APPLY`, `DAW_RECOVERY` (reconcile, restore, resolve), `DAW_SNAPSHOT`, `EXPORT_PREPARE` and `EXPORT_IMPORT`. |
| G10 | Budget for a fader write | RENDER_CALL means an offline render | A new `BudgetOperation.DAW_WRITE` with its own limit. Recovery restorations use the existing RECOVERY_OPERATION through `DawRecoveryAuthorization.budget_grant`, counted from the FL journal's charges. |
| G11 | Elapsed time across the pause | The monotonic clock does not survive a restart, and the operator pause should not count as controller time | The checkpoint records the elapsed time consumed so far. Resumption adds a fresh monotonic segment. Pause time is excluded and reported separately. |
| G12 | Termination reasons | None describe DAW outcomes | `DAW_COMMAND_UNRESOLVED`, `DAW_CONVERSION_BLOCKED`, `DAW_CONTINUITY_FAILED`, `EXPORT_IMPORT_REJECTED` and `DAW_WRITE_LIMIT`. Existing reasons are reused where they fit (`RESTORATION_FAILED`, `RESTORATION_UNCERTAIN`, `REQUIRED_RECOVERY_NOT_PERFORMED`, `RUNTIME_CONTRACT_FAILED`). |
| G13 | Durable run journal | NON_DURABLE only, so a process restart loses the trace | A file-backed (SQLite) `RunJournal` and acceptance journal (Increment 5, its own ADR). Until then, suspension works in-process only and is labelled so. |
| G14 | Orphaned DAW commands | A crash between the FL reservation and the run-trace record leaves an FL command the run never recorded | A run-start check: `control_state()` must have nothing unresolved, and the last anchor must equal the configured confirmed state. Otherwise the run refuses to start, and a separate driver step reconciles, restores or resolves under operator authorization. |
| G15 | Authorization issuance | ADR-032 makes the coordinator issue recovery and resolution authorizations only after validating fresh evidence; no rule exists for which evidence admits which authorization | A pure rule `daw_recovery_decision(control_state, reconciliation)` (Increment 4). The mapping is in "Outcome routing". |

## The sequence, mapped onto the coordinator

**Fader confirmation and candidate readiness stay separate throughout.**
- **What a confirmed write produces:** a confirmed write is recorded only as DAW-observation trace events. No `ExecutionReceipt` exists until `assess_daw_import` returns `DawImportVerified`, and only then is a `CANDIDATE_READY` receipt built.
- **Attempt state:** between the write and the verified import, the attempt is `PENDING` and the run is `AWAITING_OPERATOR_EXPORT`.

### 1. Admit, inspect and gate (`decision_round`, DAW branch)
- **Capability view (G3):** built from `daw_gain_bounds(profile, confirmed DawTrackState, permission)`. The agent sees only that range.
- **Admission:** `admit_action` exactly as today, then the advertised-bounds check as today.
- **Preflight variant:** `preflight_daw_execution` replaces `preflight_execution`. It allocates nothing: it checks that the before state has exactly one current stem of the target and no group of it (G5), that routing is declared, and that the export outputs are fully specified. It returns a `DawCorrectionPlan` with the execution ID, the candidate revision, the output artifact IDs, the export timeline and format, and the workspace assignment.
- **Run-level guard:** before inspecting, `control_state()` must show nothing unresolved (G14).
- **Inspection:** a fresh `DawParameterController.inspect`, recorded as `DAW_INSPECT`. It is not charged to a budget.
- **Gate:** `gate_daw_apply(admitted, state, inspection, profile, execution_id, command_id, blocked_conversions, unresolved_command_id)`, with the last two taken from `control_state()`.
  - `DawNoWrite`: no write happens. It is recorded as a not-executed outcome with feedback, and the loop continues, as for a preflight rejection.
  - `DawApplyRejected`: likewise not executed, with feedback; the loop continues.

### 2. Dispatch with a stable command ID (`correction`, DAW branch)
- **Command ID:** derived deterministically from the attempt's execution ID (`apply-<execution token>`, inside the 8–64 character ID rule), so every retry and every replay after a restart uses the same request.
- **Order:**
  1. the budget gate for `DAW_WRITE`;
  2. a run-trace `DispatchIntent(DAW_APPLY)`, recorded before the call;
  3. `apply(request)`.

  The FL journal independently commits its own intent before any byte is sent.
- **Retries:** a transport-level retry is an identical `apply` call, which the FL journal replays. A retry is never a new action.

### 3. Outcome routing (each outcome separate)
| Outcome | Meaning | Coordinator action |
| --- | --- | --- |
| `DawWriteRefused` | Nothing written | Record it. The attempt is `FAILED` and not executed; no recovery is needed. Feedback reaches the agent and the loop continues. |
| `DawWriteConfirmed` | The fader reads the target; nothing about audio | Record `DAW_APPLY` observed. Continue to step 4. |
| `DawWriteOffTarget` | A known write that missed; the conversion is now blocked permanently | Recovery (step 7): an exact restoration to the outcome's `before`. Then terminate with `DAW_CONVERSION_BLOCKED`, whatever the restoration result; the block is never cleared. |
| `DawWriteUncertain` | Effect unknown; the command stays unresolved | Recovery (step 7): reconcile first. Restore and resolve only as the evidence permits. Otherwise terminate with `DAW_COMMAND_UNRESOLVED` and leave the command unresolved in the FL journal. |
| Invalid response (`validate_daw_apply_outcome` raises) | Treated as uncertain | As for uncertain. |

### 4. Export handoff and suspension
- **Anchor:** `state_anchor(command_id)`. Its source must be the apply just confirmed: `basis CONFIRMED_WRITE`, with the reading equal to `DawWriteConfirmed.after`. Anything else is a contract failure, followed by recovery.
- **Request:** build the `DawExportRequest` from the plan, the anchor and the routing, then call `prepare(request)`. Record `EXPORT_PREPARE` before and after the call.
- **Preparation failure:** recovery restores the fader. The attempt is `FAILED` and the run terminates with `EXECUTION_PREPARATION_FAILED`.
- **Suspension:** record the `DawCorrectionSuspended` checkpoint event, then return `RunSuspended`. Nothing is candidate-ready, and the agent sees nothing new.

### 5. Continuation (`resume`)
1. **Load and check.** Load the journal snapshot. The last event must be the `DawCorrectionSuspended` checkpoint for this `export_id`; see "Duplicate continuation attempts" below.
2. **Record intent.** Record an `EXPORT_IMPORT` intent before anything else, so a crash mid-continuation is visible.
3. **Before snapshot.** `continuity_snapshot(anchor.source_id)`, recorded as `DAW_SNAPSHOT`.
4. **File import.** `import_files(DawImportRequest(awaiting, manifest))`, recorded.
5. **After snapshot.** `continuity_snapshot` again.
6. **Assess.** `assess_daw_import(awaiting, files, before_import=…, after_import=…)`.
7. **Act on the result.**
   - `DawImportRejected`: recovery restores the fader. The attempt is `FAILED` and the run terminates with `EXPORT_IMPORT_REJECTED` or `DAW_CONTINUITY_FAILED`.
   - A snapshot port failure: retry once if it is retryable (the coherent capture already refuses mixed states). Otherwise treat it as a continuity failure.

### 6. Candidate audio through the existing flow
- **Receipt:** `daw_execution_receipt(verified, action)` (G5) yields a `CANDIDATE_READY` receipt whose candidates are `verified.candidate_artifacts` and whose superseded IDs are the replaced artifacts.
- **Candidate state:** `derive_daw_candidate_state` (G4) builds it. Audio membership comes from the unchanged `derive_candidate_checkpoint`, and the DAW parameter state from the anchored reading.
- **Shared tail:** `verify_candidate` is split so that both paths share it: the receipt record, candidate analysis, `ATTEMPT` verification and routing. Only the receipt and candidate construction differ. Candidate artifacts are bound to the export workspace's root key, as offline outputs are today.
- **Analysis profile:** the run's declared measurement profile applies unchanged. Stereo exports reach the analyzer as stereo artifacts. If the profile requires mono, analysis fails explicitly; no implicit fold-down happens. A versioned derived artifact for that case is out of scope (see open questions).
- **Routes:** accept, restore after rejection (DAW variant, step 7), or inconclusive, all exactly as today.
- **Acceptance:** accepting changes the run's confirmed `DawTrackState` to the anchored reading, and the next round's capability view starts there.

### 7. Recovery through authorization
- **Restoration:**
  - the target comes only from `restore_target(root)`;
  - authorization is `DawRecoveryAuthorization` with a `RECOVERY_OPERATION` grant from `check_budget`;
  - then `gate_daw_restore`, then `restore`.
  - IDs (authorization and recovery command) derive deterministically from the execution ID and an attempt counter, so a replay is charged once.
- **Uncertain outcomes:** reconcile first (a fresh read; it clears nothing). Then the pure rule `daw_recovery_decision` (G15) decides:
  - `AT_RECORDED_START`: resolve as `NOT_APPLIED_OR_REVERTED`, citing the reconciliation. This clears the uncertainty only.
    - **What the finding does not establish:** it matches within the start tolerances, so it shows neither that the write never ran nor that the fader is at the saved target exactly.
    - **Next step:** rollback evidence is still required. If the reconciled reading equals the target exactly in both coordinates, that recorded reading is the evidence. Otherwise an exact restoration follows (`gate_daw_restore`, then `restore`), and only its `DawRestoreConfirmed` is the evidence.
    - **Disposition:** without that evidence the rollback is not VERIFIED. The attempt is `UNRESOLVED`, not `FAILED`, and is never labelled "not executed".
  - `AT_COMMANDED` or `NEITHER`: restore exactly to the recorded start. A confirmed restoration is then cited in `resolve(RESTORED_TO_RECORDED_START)` (Amendment R: a root's own confirmed restoration). Any other restoration outcome leaves the command unresolved.
  - `AMBIGUOUS`: restore as well. If the reading already equals the start exactly, the gate returns `DawRestoreNoWrite`, which journals nothing, and an `AMBIGUOUS` finding supports no resolution kind. The command therefore stays unresolved. This is the intended fail-closed edge, not a gap to paper over.
  - `IDENTITY_UNVERIFIED`: no restoration (the restore gate requires the bound identity).
  - **Every case that stays unresolved** terminates with `DAW_COMMAND_UNRESOLVED`. The command stays unresolved in the FL journal and blocks every later write until an operator-level resolution (the G14 driver step).
- **Root resolution after a restoration:** a confirmed restoration of an unresolved root is followed by `resolve(RESTORED_TO_RECORDED_START, evidence = the restoration)`. A confirmed write's rollback (off-target, or a rejected candidate) needs no resolution, because nothing is unresolved.
- **Rollback status (G6):**
  - VERIFIED only with both of the following, as defined in "Rollback evidence":
    - recorded exact fader evidence;
    - a verified integrity inspection of the retained accepted audio, through the existing `recover_saved`.

    A resolution, a reconciliation finding or a reading within tolerance never suffices.
  - otherwise FAILED or UNCERTAIN, with the existing terminal-reason precedence (`recovery_stop`).
### Rollback evidence (revision 3)

**Resolution and rollback are independent.**
- **Resolution** answers "is this command still uncertain?" in the FL journal. It may succeed on evidence within tolerance (ADR-032).
- **Verified selected-state rollback** answers "is the saved accepted state re-established?" It needs both of:
  1. **Retained-audio integrity:** the saved accepted state's artifacts pass the integrity inspection (`recover_saved` returns VERIFIED).
  2. **Exact fader evidence:** a recorded reading whose normalized value and dB value both equal the saved target exactly. The saved target is the confirmed fader reading of the selected state's DAW parameter state (G4), which is also the journaled recorded start of the command being rolled back. Accepted evidence is one of:
     - `EXACT_RESTORE`: a journaled `DawRestoreConfirmed` of this lineage whose target is the saved target (ADR-032 already requires both read-back readings to equal it exactly);
     - `EXACT_OBSERVATION`: a reading recorded in the run trace whose both coordinates equal the saved target exactly. It is either the reconciliation's fresh reading, or the inspection on which `gate_daw_restore` returned `DawRestoreNoWrite`. It must be taken after the last write reservation of the lineage, which `control_state` and the ledger watermark establish.
- **Not evidence:** a resolution ID, a resolution's resolved reading, a reconciliation finding, `matches_start`, or any comparison within tolerance.

**Record contract (Increment 1).**
- The DAW rollback scope on `RollbackReceipt` carries the saved target, the evidence kind, the evidence reference (recovery command ID or trace event ID), the evidenced reading, and the integrity result.
- **Construction invariant:** VERIFIED requires the evidenced reading to equal the saved target exactly in both coordinates, an evidence kind other than a resolution, and integrity VERIFIED. Otherwise only FAILED or UNCERTAIN can be built.

**Regression: resolution succeeds, verified rollback is refused** (written first in its increment):
- **Record level (Increment 1):**
  - a DAW rollback receipt citing a resolution as its only fader evidence cannot be built as VERIFIED;
  - one citing an observation 1 ulp off the target in normalized only, and separately in dB only (both within tolerance), cannot be built as VERIFIED;
  - with the exact reading it can be built.
- **Coordinator level (Increment 4),** with the real FL controller and journal against device v2 on the fake host:
  1. An apply's reply is lost, so the write is uncertain.
  2. The host fader is set to the recorded start plus an offset inside the start tolerances but not zero: normalized offset half the normalized tolerance, with the dB on the curve and inside the dB tolerance.
  3. Reconciliation reports `AT_RECORDED_START`, and the resolution is recorded: `control_state().unresolved` is empty.
  4. The restoration is made impossible. In the main case the recovery budget is exhausted after the resolution; in a second case device script v1 refuses `restore_gain`.
  5. **Assert:**
     - the rollback is not VERIFIED (UNCERTAIN, or FAILED for a known refusal) even though the resolution succeeded;
     - the attempt is `UNRESOLVED`, the terminal reason follows `recovery_stop` (`RESTORATION_UNCERTAIN` or `RESTORATION_FAILED`), and the next round never starts from that state;
     - no report claims "not executed".
  6. **Control case:** with recovery available, the same setup restores exactly, `DawRestoreConfirmed` is cited as `EXACT_RESTORE`, and the rollback is VERIFIED.
- **Mutation probes:**
  - accepting the resolution as evidence;
  - comparing within tolerance instead of exactly;
  - dropping the integrity half.

  Each must make the regression fail.

- **Preserved, never cleared by the coordinator:**
  - unresolved commands, which block through the gate and `control_state`;
  - conversion blocks, which have no clearing transition.

  A blocked conversion ends DAW actions for every later run under that conversion, until a reviewed recalibration workflow exists.

## Persistence across the operator pause and a process restart

**Must persist before `RunSuspended` is returned:**

| Item | Where | Status |
| --- | --- | --- |
| The FL command, outcome and ledger (anchor source, charges, blocks) | FL SQLite journal | exists |
| The export request file and workspace identity | export workspace on disk, plus `DawExportAwaiting` | exists |
| The run trace up to and including the `DawCorrectionSuspended` event | run journal | **durable journal missing (G13)** |
| The acceptance history and current authoritative reference | acceptance journal | **durable journal missing (G13)** |
| The checkpoint contents, listed below | inside the `DawCorrectionSuspended` event | new record (G7) |

**Checkpoint contents** (one immutable record):
- run ID, decision call ID, decision, attempt ID and the `DawCorrectionPlan`;
- the before `ProjectState` and the accepted reference;
- the evidence bundles for the before and initial states. `ATTEMPT` verification needs them, and they must not be recomputed differently after the pause;
- the feedback history and the observations used by the decision;
- budget usage, elapsed controller time consumed (G11), and every identity token issued, for freshness after resumption;
- the `DawWriteConfirmed` outcome, the anchor and `DawExportAwaiting`;
- the confirmed `DawTrackState` before the write.

**Not persisted:**
- the manifest, which the operator supplies at continuation;
- no live DAW state is assumed. The continuation re-observes everything through the coherent snapshots.

**Restart rules:**
- **Restart during the pause:** resumption reads the durable journals and the checkpoint. It never calls `apply` again.
- **Restart before the checkpoint is durable:** the run cannot be resumed. The FL journal still holds the confirmed command, so a new run's start check (G14) refuses, because the anchor no longer equals the configured confirmed state. The operator-level driver step must then restore or adopt that state explicitly.
- **Restart during the continuation:** the trace shows an `EXPORT_IMPORT` intent without an outcome. Because the import only reads and the snapshots only observe, the continuation is repeated from step 3 under the same `export_id`. If the FL ledger moved in the meantime, continuity fails, which is the conservative anchor rule the owner accepted.

## Duplicate continuation attempts
- **Concurrent:** the durable run journal is single-owner and exclusively locked, as the FL journal already is. A second process cannot open it, and inside one process `resume` holds a run lock.
- **After an outcome was recorded:** a later `resume` for the same `export_id` returns the recorded result from the trace. It re-imports nothing, takes no snapshot, applies nothing and charges nothing. A different manifest for a decided export is refused with `INVALID_INPUT`.
- **After a rejection:** the attempt is over and its recovery has run. A new correction needs a new decision round, a new execution ID, a new workspace and a new write. No re-import with a corrected manifest is allowed in the first version; see open questions.
- **With an `export_id` that does not match the checkpoint:** refused before any call.

## Offline workflow preservation
- **Unchanged behaviour:** with `daw_session=None`, every existing path, record and test behaves identically.
- **New tests:**
  - an architecture test pins that the offline modules do not import the DAW ports;
  - a regression runs the existing coordinator suite unchanged.
- **Unchanged contracts:** `Renderer`, `RunRenderer`, `RenderPlan`, `RenderedCandidate`, `RenderFailure`, `TrackGainState` semantics and `derive_candidate_project_state`.
- **Gating, not adding:** the `RenderPlan`-specific checks in `PreparedCorrection` are kept and applied only to the offline variant.

## Increments (each reviewable on its own; tests first; no live calls)

0. **Scope decision and ADR, no code: done as documentation on 2026-11-18.**
   - **Owner decisions:** D15 adopted, Option A, and fader restoration distinguished from complete project rollback.
   - **ADR-034 (Proposed):** the scope, the relationship to ADR-001, ADR-004 and ADR-009 (partly superseded, file unchanged), the rule that fader confirmation is never candidate readiness, and the three rollback claims.
   - **Updated documents:** `requirements.md` v1.2 (FR-19, FR-20, D15 decided), the project-scope core boundaries, roadmap priority 4, and the research log.
   - **Scoping change:** ADR-034 is a scope ADR. The engineering decisions that revision 1 assigned to it (G1, G5, G7, G10, G11, G12) are settled instead with Increment 1, in its own architecture ADR-035 "Coordinator DAW execution path" (to be proposed with that increment). *Revision 1 placed them in ADR-034.*
1. **Pure contracts, no loop change (with ADR-035, Proposed).**
   - **New records:**
     - `DawSessionConfig`, `DawCorrectionPlan` and the `PreparedCorrection` plan union;
     - the DAW parameter state as an explicit `ProjectState` member (Option A, G4);
     - `daw_execution_receipt` and `validate_daw_execution`;
     - `derive_daw_candidate_state`, which writes only the DAW parameter state and never `TrackGainState`;
     - `DawCorrectionSuspended` and `RunSuspended`.
   - **Additions to existing records:**
     - the trace kinds and stage-request members;
     - `RunState.AWAITING_OPERATOR_EXPORT`;
     - `BudgetOperation.DAW_WRITE`;
     - the new termination reasons;
     - a rollback scope on `RollbackReceipt` (G6).
   - **Tests:** a contract test per invariant:
     - offline states carry no DAW parameter state, and the offline variant is unchanged;
     - `TrackGainState` is untouched by DAW derivation;
     - a group of the target is refused;
     - supersession equals the replacements;
     - a DAW rollback can never be built or reported as a complete project rollback;
     - VERIFIED DAW rollback needs exact recorded fader evidence plus integrity, and is refused for a resolution or a reading within tolerance (the record-level regression under "Rollback evidence");

     plus architecture inventories.
2. **Write path up to suspension (in-process).** The DAW branch of `decision_round` and `correction`: the start guard, inspection, gate, stable command ID, outcome routing for refused and confirmed, anchor, prepare, checkpoint, `RunSuspended`.
   - **Collaborators:** the real FL controller and journal against device v2 on the fake host; the real `FileExportHandoff` on temporary directories; controlled doubles for decision and analysis.
   - **Tests:** refused, no-write and gate-rejected outcomes continue the loop; a confirmed outcome suspends with no receipt and nothing candidate-ready; the command ID is stable on retry.
3. **Continuation (in-process).** `resume`: intent, snapshots, import, assessment, receipt, candidate state, the shared `verify_candidate` tail, acceptance or rejection routing.
   - **Tests:** a verified import reaches acceptance; each rejection code terminates after recovery; a duplicate `resume` replays; a different manifest is refused; continuity failures (a ledger entry during the pause, fader drift) are rejected.
4. **Recovery routing.** `daw_recovery_decision`, DAW variants of `failed_attempt` and `restore_after_rejection`, deterministic recovery IDs, and recovery budget from the FL charges.
   - **Tests:**
     - off-target: restored, then terminated, and the block persists;
     - uncertain: one test for each finding;
     - the coordinator-level regression under "Rollback evidence": the resolution succeeds, verified rollback is refused, and the control case is verified;
     - a rejected candidate: restored exactly, and the next round starts from the restored state;
     - recovery-budget exhaustion;
     - an uncertain restoration remains unresolved.
5. **Durable persistence and restart (own ADR).** A SQLite `RunJournal` and acceptance journal, the exclusive lock, and checkpoint reload.
   - **Tests:** subprocess crash tests (a restart during the pause, during the continuation, and before the checkpoint), duplicate processes, and the orphaned-command start refusal (G14). No power-loss claim.
6. **Operator driver.** `autonomous-mixing daw start` and `daw continue --export-id … --manifest …`, which build the adapters from explicit configuration, plus `daw recover` (G14) under explicit operator authorization.
   - **Tests:** CLI registration and argument validation only; no live calls.
   - Live use stays a separately authorized step.

Every increment runs the locked checks (focused and full suites, ruff, format, CLI help, `uv lock --check`, `git diff --check`), plus mutation probes for its new rules, each reverted byte-identically.

## Open questions
- *Resolved 2026-11-18:* D15 and ADR-009 (in scope, bounded; ADR-034) and G4 (Option A).
- **DAW-path results in the evaluation:** whether they enter the main evaluation, and under which comparison conditions (SR-01). This is a methodology question, raised by ADR-034.
- **Corrected manifests:** whether a file-level import rejection with intact continuity may be retried with a corrected manifest, and under which limit. The first version says no.
- **Analysis profile:** how stereo exports are analysed when the declared profile is mono dist10. This needs an explicit, versioned derived artifact, identically applied to before and candidate.
- **Orphaned commands:** whether the operator-level `daw recover` may adopt a confirmed orphaned write as the new confirmed state, or must always restore.
- **Pause time:** whether operator pause time should have its own reported limit.
- **Device-refused writes:** whether such a write should keep an anchor current (currently fail closed; accepted for the export slice).

## Increment 1 outcome (2026-11-19, tests-first; uncommitted for review)

**Implemented as planned**, recorded in [ADR-035](../../decisions/0035-coordinator-daw-execution-path.md) (Proposed):
- **Correction contracts:**
  - `core.records.daw_correction` (`DawSessionConfig`, `DawCorrectionPlan`, `DawStateQuery`, `DawCorrectionSuspended`);
  - the `PreparedCorrection` plan union;
  - `RunRequest.daw_session`.
- **State and rollback:**
  - Option A: `DawTrackParameter`, `DawParameterState` and `ProjectState.daw`, with offline refusal in preflight (`DAW_CONTROLLED_STATE`) and in the offline derivation;
  - `RollbackScope`, `DawRollbackFaderEvidence` and `daw_rollback_evidence_problem`.
- **Receipt and derivation:** `core.loop.daw_transitions` and the shared `daw_export_coverage_problem`.
- **Trace and run records:** the trace kinds, `RunSuspended`, `RunState.AWAITING_OPERATOR_EXPORT`, `BudgetOperation.DAW_WRITE` and the new termination reasons.

**Deviations, all stated in ADR-035:**
- **Module move:** `DawFaderReading` and `DawTrackBinding` move to the new `core.records.daw_binding`, to avoid an import cycle.
- **Trailing defaulted fields:** new fields of existing records keep offline constructors identical, with the field and enum pins updated explicitly.
- **Trace naming:** `DAW_STATE` (with `DawStateQuery`) replaces the plan's `DAW_SNAPSHOT`, because it also covers journal-only anchor queries.
- **G5 enforced in the coverage rule:** groups containing the target, and members of the target left unreplaced, are refused both in the export request and in the plan.

**Exact-reading evidence**, per the owner's condition: it is bound to the same fader by its complete binding and to the saved target (the selected state's confirmed reading), and ordered by ledger position after the latest relevant write reservation.

**Regressions written first** (`tests/unit/test_daw_coordinator_contracts.py`, 59 tests):
- resolution only;
- a one-ULP mismatch in normalized and in dB, each in both directions and for both evidence kinds;
- the exact reading;
- failed or uncertain retained-audio integrity;
- evidence bound to another fader or another saved target;
- evidence ordered before the latest reservation;
- a DAW write without a free recovery slot.

**Mutation probes:** 20, each reverted byte-identically and caught.

**Locked checks:** focused suites with warnings as errors, the full suite, ruff, format, CLI help, the lock and `git diff --check`; results are in the research log. **Not done here:** coordinator integration (increments 2–6), installation, live calls.

### Review fixes (2026-11-19, after Codex review of Increment 1)

1. **The candidate could claim another gain than the executed action.**
   - **Cause:** the derivation took a free `Action`. Codex substituted a −99 dB action on the same track: the receipt was created, and the candidate recorded −99 dB beside the unchanged confirmed fader.
   - **Fix:** receipt creation, validation and candidate derivation take the journaled `DawApplyRequest`, and the action comes from it. `daw_apply_export_problem` binds the apply to the export's anchor (command, `CONFIRMED_WRITE`), execution, binding, profile, before state and track, and to its resolved target (success tolerance). The apply must also have been resolved from the selected state's DAW parameter state.
   - **Checkpoint:** `DawCorrectionSuspended` gains the `apply` field, which must execute the decision's action.
   - **Regressions:** substituting another gain on the same command, the same gain under another command, another execution, or another command ID; an apply resolved from another selected gain or confirmed fader; the recorded gain equal to the executed one; and checkpoint cases for another apply and for a decision whose action differs from the apply's.
2. **The suspension checkpoint accepted conflicting export settings.**
   - **Cause:** `_check_export` omitted the timeline, format and render spec.
   - **Fix:** they are compared too.
   - **Regressions:** one rejection per field.

**Mutation probes.** Nine new, each reverted byte-identically and caught:
- ignoring the anchor's command, the execution or the resolved target;
- skipping the apply binding or the selected-state check;
- ignoring the checkpoint's apply action;
- dropping each of the three settings.

The checkpoint-action probe first survived and was caught after the decision-action regression was added. The 20 original probes still pass, so 29 are caught in all.

### Second review fix (2026-11-19)

**Finding.** The first fix still identified the apply request through the success tolerance, which checks the achieved gain, not request identity. Codex substituted a request with the same command ID and a selected gain of 0.99 dB instead of 1.00 dB: the original verified export was accepted, and the candidate recorded 0.99 dB.

**Fix:**
- **The anchor carries the request.** `DawStateAnchor` gains `apply`: the exact journaled `DawApplyRequest` of a `CONFIRMED_WRITE` source, and `None` otherwise. Its command, execution, binding and profile must be the anchor's own.
- **The FL adapter rebuilds it exactly.** The journal returns the stored request text, and `serialization.load_apply_request` rebuilds the record, requiring that it re-serializes to exactly that text.
- **Identity is exact agreement.** `daw_apply_export_problem` requires `anchor.apply == apply`. The success-tolerance check stays as a separate rule on the achieved result.
- **Redundant checks removed.** The earlier command and execution comparisons are now implied by the record invariants, so they were removed and their two probes retired.

**Regressions:**
- a substitute 0.01 dB away under the same command ID, inside the 0.05 dB tolerance, is refused by the receipt, the derivation and the checkpoint;
- the tolerance is still checked for the exact apply;
- anchor invariants: no apply on a write, another command, another execution, another binding, an apply on a restoration or resolution anchor;
- FL: the anchor carries exactly the journaled request; stored text with equal content but other bytes is refused, and so is an edited request.

**Mutation probes.** Six new, all caught. One, for the anchor's binding and profile, first survived and was caught after the binding regression was added. With the 20 and 7 earlier probes, 33 are caught in all.

**Anchor field.** `apply` is a required trailing field, so every anchor names it explicitly.

## Increment 2 design note (2026-11-19, written before implementation)

**Interim termination while Increment 4 recovery routing is absent.** Every outcome below that leaves the fader moved, or possibly moved, ends the run at once. Each of them:
- writes no restoration, reconciliation or resolution;
- builds no `RollbackReceipt`;
- clears nothing in the FL journal, so an unresolved command and a conversion block remain and keep blocking every later write through `control_state` and `gate_daw_apply`;
- leaves the run's verified reference cleared, so the final state is `UNKNOWN` and never `UNCHANGED` or `RESTORED_TO_LAST_ACCEPTED`. No rollback is claimed;
- records `ActionFeedback` as `UNRESOLVED` at the execution stage, with `ExecutionStatus.UNCERTAIN` (no candidate exists, and the project state relative to the selected state is not established) and no rollback status.

| Outcome | Terminal reason | FL journal afterwards |
| --- | --- | --- |
| `DawWriteOffTarget` | `DAW_CONVERSION_BLOCKED` | known write; the conversion is blocked permanently |
| `DawWriteUncertain`, or a write response that fails validation | `DAW_COMMAND_UNRESOLVED` | the command stays unresolved |
| Confirmed write, then the anchor query fails or disagrees, the export request cannot be built, `prepare` fails, the checkpoint cannot be recorded, or the run is cancelled or out of time | `REQUIRED_RECOVERY_NOT_PERFORMED` (the original cause in the explanation) | confirmed write; the fader is away from the selected state until an operator restores it |

**Reasons survive a lost terminal append.** `DAW_COMMAND_UNRESOLVED` and `DAW_CONVERSION_BLOCKED` join `REQUIRED_RECOVERY_NOT_PERFORMED` among the reasons that `finalize` and `RunReport` preserve when the terminal event is not acknowledged.

**Nothing written means nothing to recover:**
- **Gate rejection:** recorded as `REJECTED` at the preflight stage; the loop continues.
- **`DawNoWrite`:** recorded as `NOT_EXECUTED` at the preflight stage; the loop continues.
- **`DawWriteRefused`, a pre-write refusal (ADR-030):** recorded as `FAILED` at the execution stage with `NOT_STARTED`. The verified reference is re-established and the loop continues.
- **Inspection or control state unavailable before the write:** `NOT_EXECUTED`, and the run terminates with `EXECUTION_PREPARATION_FAILED`.

**API.**
- **`run_daw(...)`** returns `RunReport | RunSuspended`. It takes the DAW parameter and recovery controllers and the export handoff in place of the offline renderer.
- **`run(...)`** refuses a request with a DAW session.
- **Offline behaviour** is otherwise byte-identical.

## Increment 2 outcome (2026-11-19, tests-first; uncommitted for review)

Implemented as designed in the note above, recorded in [ADR-036](../../decisions/0036-coordinator-daw-write-path-to-suspension.md) (Proposed).

**Code:**
- `run_daw` (with `run` refusing DAW requests);
- `core.loop.daw_execution`: `daw_track_state`, `daw_capabilities`, `prepare_daw`, `execute_daw`;
- `core.admission.daw_correction.preflight_daw_correction`;
- the `DawPlanReady` record and the trace payloads for the control state and the gate verdict;
- the preserved DAW reasons in `finalize` and `RunReport`.

**Deviations:**
- `ordinary` takes the execution step as a `functools.partial`, because the coordinator structure test forbids nested closures.
- Outputs are declared with the replaced artifact's channel count, an open question below.
- A group of the target is refused by the plan's shared coverage rule, not by a second check in the preflight.

**Tests:** 15 in `tests/unit/test_daw_coordinator.py`:
- **Confirmed write:** suspension with no receipt, analysis, render or acceptance; one stable command ID from plan to FL journal.
- **Capability window:** `[-1.0, 0.0]` dB for the confirmed 2 dB with policy `[-6, 0]`.
- **Nothing written:** pre-write refusal, gate rejection with the unresolved command preserved, no-write, the exhausted `DAW_WRITE` budget, and the DAW-preflight refusal of a group of the target.
- **Off-target:** the conversion is blocked, nothing is restored, no further action follows.
- **Uncertain:** the command stays unresolved, nothing is restored.
- **After a confirmed write:** preparation failure, anchor failure, and an anchor that disagrees (refused before any export); the unresolved reason surviving a lost terminal append.
- **Entry points:** each refuses the other's requests.

**Mutation probes:** 11, each reverted byte-identically and caught. Two first survived:
- **The anchor-reading check.** It is now pinned by "refused before any export".
- **A duplicate group check in the preflight.** It was removed as redundant with the coverage rule.

A defensive no-master check is unreachable through a valid `RunRequest` and was not probed.

**Open question:** whether exports may declare another channel count than the artifacts they replace (for example stereo exports replacing mono baselines). Currently they must match, and a mismatch is refused at import.

### Increment 2 review fix (2026-11-19)

**Finding.** Codex reproduced the defect on a simulated host. After a confirmed write, export preparation raised `OSError`, and the terminal record said `RUNTIME_CONTRACT_FAILED`, losing `REQUIRED_RECOVERY_NOT_PERFORMED`. The final state was correctly `UNKNOWN`.

**Fix: phase-aware exception handling.**
- `RuntimeProgress.pending_effect` names the reason an unexpected exception must keep:
  - `DAW_COMMAND_UNRESOLVED` from just before the apply, since it may have dispatched;
  - `DAW_CONVERSION_BLOCKED` after an off-target write;
  - `REQUIRED_RECOVERY_NOT_PERFORMED` after a confirmed write;
  - cleared after a pre-write refusal.
- `_run`'s best-effort terminal recording uses it, names the exception, and re-raises the original exception. It restores nothing and clears nothing. Offline behaviour and its explanation are unchanged.

**Regressions, written first:**
- an exception after the apply dispatched keeps `DAW_COMMAND_UNRESOLVED`, the original exception propagates, and nothing is restored;
- an `OSError` during preparation after a confirmed write keeps `REQUIRED_RECOVERY_NOT_PERFORMED`, with nothing added to or cleared from the FL journal;
- an exception before any dispatch stays `RUNTIME_CONTRACT_FAILED` with the state `UNCHANGED`;
- after a refused write, a later exception is again `RUNTIME_CONTRACT_FAILED`.

**Mutation probes.** Four new, all caught. Two Increment 2 probes were re-anchored to the rewritten lines and are still caught.

**Not covered by a dedicated test:** an exception inside the off-target termination itself (the marker is set to `DAW_CONVERSION_BLOCKED` just before it).

### Increment 2 second review fix (2026-11-19)

**Finding.** The marker was set after `observed()` recorded the apply reply. Codex reproduced a confirmed write whose observation append raised: the terminal reason was `DAW_COMMAND_UNRESOLVED` instead of `REQUIRED_RECOVERY_NOT_PERFORMED`.

**Fix.** Immediately after the reply is validated, and before it is recorded:
- **Confirmed:** recovery is required.
- **Off-target:** the conversion is blocked.
- **Refused:** the marker is cleared and the verified reference re-established.
- **Uncertain or invalid:** the reply keeps the unresolved marker set before the apply.

**Regressions, written first, all three failing before the fix.** In each, appending the apply observation raises:
- after a confirmed reply, the reason is `REQUIRED_RECOVERY_NOT_PERFORMED` and the state `UNKNOWN`;
- after an off-target reply, the reason is `DAW_CONVERSION_BLOCKED` and the block is in the FL journal;
- after a refused reply, the reason is `RUNTIME_CONTRACT_FAILED` and the state `UNCHANGED`.

**Mutation probes.** The marker probes were re-anchored and one was added for the off-target marker; all five are caught, and so are all earlier sets. A separate ordering probe was dropped, because removing the confirmed-branch marker already covers it.

**Previously untested case, now tested.** The off-target marker noted after the first fix is now pinned by the off-target regression above.

## Increment 3 plan: continuation to analysis, verification and acceptance (proposed 2026-11-19; not started)

> Amended by "Increment 3 plan, amendment A" below. Where they differ, the amendment governs.

**Scope.**
- **In scope:** an in-process continuation of a `RunSuspended` that captures continuity, imports and assesses the export, derives the candidate, and passes it through the existing analysis, verification and acceptance flow.
- **Out of scope:** durable restart (Increment 5), recovery routing (Increment 4), CLI wiring (Increment 6), installation and live FL Studio calls.
- **Implementation does not start until this section is reviewed.**

### Channel count and analysis profiles (resolves the Increment 2 open question)

**Native channels are preserved; nothing is converted.**
- Every replaced artifact must itself be a native FL export with the session's export settings: its `render_spec`, `sample_rate_hz`, `timeline_start_seconds` and `frame_count` must equal the session's `render_spec` and `export_timeline`. Its own `channel_count` (1 or 2) is the native channel count the output declares.
- **Change:** `preflight_daw_correction` replaces the Increment 2 rule (output channels copied from the replaced artifact, nothing else checked) with this full metadata equality.
- **Refusal:** any mismatch is `INCOMPATIBLE_AUDIO` before any write. A stereo export is never planned against a mono baseline, and the reverse.
- **Import side:** an export that differs from its declaration is still refused with `DIMENSIONS`.
- **Why:** verification compares the candidate with the before and initial states through one fixed `MeasurementDefinition` per criterion. Its `SourceRequirement` names exact metadata (render spec, rate, channels, start, frames), so before, initial and candidate must be like-for-like. Converting one of them would compare unlike audio.

**Supported analysis profile in this increment: only `FileAudioAnalyzer`** (full-band sample peak and RMS in dBFS):
- one source per job;
- mono or stereo, pooled over channels, never folded down;
- the source selected by exact metadata.

Criteria for a DAW run must therefore declare source requirements whose render spec, channel count, rate, start and frame count are the FL export settings. A request whose definitions name other settings fails initial analysis as today, before any write.

**The mono masking profile is unchanged and unsupported here.** dist10 front-end profile v1 (mono, 44.1 kHz, PCM_16; ADR-029) is not an `AudioAnalyzer` and is not wired into the coordinator. DAW candidates are never folded down, resampled or requantized to reach it. Using exports for masking would need a later profile with an explicit, versioned derived artifact applied identically to before and candidate: a separate decision.

### Missing contracts (proposed additions, each test-pinned)

| # | Gap | Proposal |
| --- | --- | --- |
| C1 | The checkpoint lacks the progress selection fields resumption needs | `DawCorrectionSuspended` gains `stems`, `mix_id` and `roots` (required). Its validation requires them to match the before state, exactly as `RunRequest` requires for the initial state. |
| C2 | Elapsed time across the pause | `RuntimeProgress` gains `elapsed_before: float = 0.0` and `segment_start: ClockReading or None = None`. `runtime.checkpoint` computes `elapsed_before + (reading - segment_start)` within a segment. `progress.first` stays the run's original first reading, so `RunRecord.created_at` is unchanged. The monotonic-decrease check applies within a segment; a new segment is a fresh baseline. |
| C3 | The continuation start is not recorded | A trace payload `DawContinuationStarted(run_id, checkpoint_event_id, export_id, manifest, paused_at_utc, resumed_at_utc)`, recorded first. The pause duration is derived from the two UTC times and is informational, never charged. |
| C4 | The assessment is not recorded | `DawImportVerified` and `DawImportRejected` become trace payloads. The continuity snapshots use the existing `DAW_STATE` kind with `DawStateQuery(source, fresh_inspection=True)`; the import uses `EXPORT_IMPORT`. |
| C5 | An expected refusal of a continuation | A result record `DawContinuationRefused(run_id, export_id, code, explanation)` with codes `ALREADY_CONTINUED`, `MANIFEST_CONFLICT`, `CONTINUATION_IN_PROGRESS` and `NOT_SUSPENDED`. It is returned, not raised; nothing is dispatched or recorded. |
| C6 | Shared candidate tail | `correction.verify_candidate` is split so the offline path keeps its receipt and candidate construction and its exact behaviour. A new `assess_candidate(progress, ctx, receipt, candidate, candidate_roots, ...)` holds the receipt record, the single candidate analysis, the `ATTEMPT` verification and routing. |
| C7 | `promote` assumes exactly two outputs (target stem, mix) and the offline output root | A DAW promotion updates the stem binding of every exported stem, selects the exported master as `mix_id`, and binds every candidate artifact to the session's `export_root_key`. Offline `promote` is unchanged. |
| C8 | `accept` routes pre-commit failures through offline `failed_attempt`, which would record a logical rollback of a state whose fader moved | `accept` and the verification routing take the unaccepted-effect handler as a parameter: offline `failed_attempt`, as today, or the DAW interim handler below. `restore_after_rejection` is never called for a DAW candidate. |

### The continuation, step by step (`resume_daw(suspended, manifest, *, collaborators...)`)

1. **Admission of the call.** `suspended` must be a `RunSuspended` of a request with a DAW session, and the journal must be bound to its run. The journal's current trace decides:
   - the checkpoint is the last event: continue;
   - a `DawContinuationStarted` for this export exists with the same manifest and a terminal event follows it: `ALREADY_CONTINUED`;
   - the same exists with another manifest: `MANIFEST_CONFLICT`;
   - a `DawContinuationStarted` exists with no terminal event after it (an interrupted continuation in this process): `CONTINUATION_IN_PROGRESS`;
   - the checkpoint is not in the trace: `NOT_SUSPENDED`.

   Nothing is dispatched for any refusal.
2. **Rebuild the progress** from the trace snapshot and the checkpoint:
   - `accepted`, `tokens`, `history`, `observations`, `stems`, `mix_id` and `roots` from the checkpoint;
   - `usage` from the checkpoint event;
   - `known` and `commits` from the snapshot;
   - `verified=None`, because the fader is away from the selected state;
   - `pending_effect=REQUIRED_RECOVERY_NOT_PERFORMED`;
   - a new clock segment (C2).
3. **Record** `DawContinuationStarted`.
4. **Snapshot before the import:** `DAW_STATE` with a fresh inspection, `continuity_snapshot(anchor.source_id)`.
5. **Import:** `EXPORT_IMPORT` with `import_files(DawImportRequest(awaiting, manifest))`.
6. **Snapshot after the import:** `DAW_STATE` again.
7. **Assess:** `assess_daw_import(...)`, then record the result.
8. **If verified:**
   - `daw_execution_receipt(before, verified, apply)` and `derive_daw_candidate_state(..., captured_at=progress.last.utc)`;
   - `assess_candidate` with candidate roots bound to `export_root_key`;
   - routing as today: accept (with C7 promotion) and finish or iterate, or the DAW interim handler for rejection or inconclusive.
   - **Iteration** may lead to another write and another `RunSuspended` in the same process.
9. **After an acknowledged commit,** `pending_effect` is cleared and the verified reference comes from the existing post-commit integrity inspection. The accepted state's DAW parameter state is the anchored reading, so the next capability view starts there.

### Failure handling while Increment 4 recovery is absent

**Every outcome below ends the run at once.** Each one:
- **restores and clears nothing:** no restoration, reconciliation or resolution, and nothing cleared in the FL journal;
- **claims no rollback:** the verified reference stays `None`, so the final state is `UNKNOWN`, never `UNCHANGED` or `RESTORED_TO_LAST_ACCEPTED`;
- **records the attempt as `UNRESOLVED`:** when a receipt exists, as a `CorrectionAttempt` whose `RollbackReceipt` has scope `SELECTED_STATE_WITH_FADER`, status `UNCERTAIN`, `retained_audio=UNCERTAIN`, no fader evidence, and an explanation that the fader was not restored. Such a receipt can never be VERIFIED (ADR-035).

| Failure | Terminal reason |
| --- | --- |
| Continuity rejection (`STATE_DISCONTINUITY`, `UNRESOLVED_COMMAND`, `CONVERSION_BLOCKED`), or a snapshot port failure (one retry if it is retryable) | `DAW_CONTINUITY_FAILED` |
| File rejection (any other `DawImportRejectionCode`), or an invalid import response | `EXPORT_IMPORT_REJECTED` |
| Receipt or candidate derivation refused, candidate analysis failed, verification inconclusive, candidate rejected by verification, acceptance refused or confirmed absent, post-commit integrity failure before selection, cancellation or time limit, recording failure, or any unexpected exception (`pending_effect`) | `REQUIRED_RECOVERY_NOT_PERFORMED`, with the cause in the explanation |
| Acceptance authority unknown | `ACCEPTANCE_COMMIT_UNCERTAIN` (existing; no rollback claimed) |

**Decision for review:** representing "the selected state is not re-established" as a DAW-scope `RollbackReceipt` with status `UNCERTAIN`. The alternative is a new `RollbackStatus.NOT_PERFORMED`, which would change a pinned enum. I recommend `UNCERTAIN`: it is truthful (the restoration is not established) and needs no enum change.

### Pause-time accounting

- **Elapsed budget** (`max_elapsed_seconds`): controller time only, the sum of clock segments (C2). The operator pause is never charged.
- **Pause duration:** recorded in `DawContinuationStarted` from UTC times, for the report only. It is not a budget, and no pause limit exists in this increment (open question).
- **Cancellation** is read from the continuation's clock as usual.

### Tests (written first; real adapters, simulated FL host, temporary WAV files)

**Collaborators:**
- **FL and export:** the real FL runtime protocol, exchange, journal and controller against device script v2 on the fake host; the real `FileExportHandoff`.
- **Analysis and integrity:** the real `FileAudioAnalyzer` and the real `FileStateIntegrityInspector` over temporary synthetic WAVs (unit-test data only). The baseline files are written by the test, with their hashes in the initial state.
- **Operator export:** simulated by the test, which writes the stereo PCM_24 output WAVs into the prepared workspace with chosen levels, plus a manifest.
- **Doubles:** labelled doubles for decisions and diagnosis only.

**Scenarios:**
1. **Accept:** the candidate meets the target. The run finishes `CANDIDATE_CRITERIA_MET`, `ACCEPTED_EDITS_RETAINED`, with the receipt superseding exactly the replaced IDs. The accepted state's DAW parameter state holds the anchored reading, and the stem, mix and root bindings point at the exports.
2. **Iterate:** an intermediate improvement is accepted, then a second write suspends again in the same process.
3. **Rejected by verification:** `REQUIRED_RECOVERY_NOT_PERFORMED`, `UNKNOWN`, with the attempt `UNRESOLVED` and an `UNCERTAIN` DAW-scope rollback. No VERIFIED or complete-state rollback, and no restore sent.
4. **Inconclusive verification and candidate-analysis failure:** the same safe termination.
5. **File rejection:** a stray file, a hash mismatch, and a mono file declared stereo, each giving `EXPORT_IMPORT_REJECTED`, `UNKNOWN`.
6. **Continuity:** the fader moved during the pause, and a ledger entry appended during the pause, each giving `DAW_CONTINUITY_FAILED` with the FL journal unchanged.
7. **Duplicates:** the same manifest again gives `ALREADY_CONTINUED`; another manifest gives `MANIFEST_CONFLICT`; a continuation interrupted by an exception, then retried, gives `CONTINUATION_IN_PROGRESS`. In every case no further FL or file call is made.
8. **Pause accounting:** a monotonic jump far beyond `max_elapsed_seconds` during the pause does not hit the limit, and the elapsed time after resumption is the sum of the segments. The pause is recorded from UTC.
9. **Exceptions during the continuation:** in the import, the analysis and the acceptance commit, each keeps `REQUIRED_RECOVERY_NOT_PERFORMED`, with the original exception re-raised.
10. **Channel profile:** a session against a baseline with another render spec, rate, start or frame count is refused before any write with `INCOMPATIBLE_AUDIO`. A stereo end-to-end run passes, and no fold-down appears anywhere.
11. **Offline unchanged:** the full existing coordinator suite passes unchanged after the C6 split. A pin checks that the offline trace order is identical for a fixed scenario.

**Mutation probes**, each reverted byte-identically:
- skip either continuity snapshot;
- accept a duplicate continuation;
- charge the pause to the elapsed budget;
- promote with the offline root key;
- route a DAW rejection through `restore_after_rejection`;
- claim VERIFIED rollback;
- relax the preflight metadata equality.

### Open questions for review

1. **Rollback representation:** an `UNCERTAIN` DAW-scope receipt (recommended) or a new `NOT_PERFORMED` status.
2. **Pause limit:** whether the operator pause needs a reported limit.
3. **Corrected manifests:** whether a file-level rejection should allow a corrected manifest instead of ending the run. The plan says no until recovery exists.
4. **Faithful replay of a decided continuation:** returning the original `RunReport` needs durable storage (Increment 5). In-process, the duplicate is refused with `ALREADY_CONTINUED` and a reference to the recorded terminal event.

### Increment 3 plan, amendment A (owner review, 2026-11-19; still not started)

This amendment supersedes three parts of the plan above:
- **C5's codes** (`CONTINUATION_INTERRUPTED` is added);
- **C8** (a dedicated DAW acceptance path replaces parameterizing `accept`);
- **the acceptance and integrity rows of the failure table, plus step 1 and scenarios 7 and 9.**

Everything else stands.

#### A1. Acceptance uncertainty is separate from recovery failure

**Finding in today's code.**
- **Commit exceptions:** `acceptance_flow.accept` calls `journal.commit(event)` with the authority still marked known. An exception there propagates to `_run`, which would record the post-write `REQUIRED_RECOVERY_NOT_PERFORMED` marker. Yet the commit may already have happened.
- **Absent acceptance:** offline reconciliation turns a confirmed-absent acceptance into `VERIFIED_PREVIOUS_STATE`, setting `restored_last`, so the final state reads `RESTORED_TO_LAST_ACCEPTED`. That is false for a DAW candidate, whose fader is still moved.
- **Correction:** the plan above listed "post-commit integrity failure" under recovery. It belongs to acceptance authority instead; superseded below.

**Decision: a dedicated `accept_daw` and `reconcile_daw_acceptance`** (in `core.loop.daw_execution` or a sibling DAW module). The offline `accept`, `reconcile_unknown` and `failed_attempt` stay byte-identical; superseding C8's parameterization.

`RuntimeProgress.pending_effect` gains `ACCEPTANCE_COMMIT_UNCERTAIN` as a permitted value.

| Phase | Before it | Outcome |
| --- | --- | --- |
| Pre-commit: cancellation or time, acceptance-ID failure, acceptance-event recording failure, cancellation after recording | the candidate is not accepted and the fader is moved | DAW unaccepted handler: `REQUIRED_RECOVERY_NOT_PERFORMED`, attempt `UNRESOLVED` with an `UNCERTAIN` DAW-scope rollback receipt |
| **Commit dispatch** | **immediately before `journal.commit`:** `authority_known=False`, `verified=None`, `pending_effect=ACCEPTANCE_COMMIT_UNCERTAIN` | see the rows below |
| `commit` raises | the authority stays unknown | `_run` finalizes with `ACCEPTANCE_COMMIT_UNCERTAIN` (already preserved on a lost terminal append) and re-raises the original exception. Final state `UNKNOWN`; nothing restored or cleared. |
| `AcceptanceCommitted` | **acceptance established:** commits appended, `authority_known=True`, `pending_effect=None`, then DAW promotion (C7) setting `accepted` and `verified` to the new reference | `finish_acceptance` as today. **Every later failure preserves the accepted authority:** a recording failure gives `RECORDING_FAILED` with `ACCEPTED_EDITS_RETAINED`, and an unexpected exception gives `RUNTIME_CONTRACT_FAILED`, also with the edit retained. Neither ever routes to the unaccepted handler. |
| `AcceptanceNotCommitted` (a definitive refusal) | `authority_known=True` (the previous reference), `pending_effect=REQUIRED_RECOVERY_NOT_PERFORMED` | DAW unaccepted handler: `REQUIRED_RECOVERY_NOT_PERFORMED` |
| `AcceptanceCommitUnknown` or an invalid response | the authority stays unknown | `reconcile_daw_acceptance`: one authority query (`QUERY_ACCEPTANCE`, charged as today) |
| query answer `AcceptanceFound` | **acceptance established:** `accepted` is the found reference, `authority_known=True`, commits updated, `pending_effect=None` | integrity inspection of the found state, as today. `CONFIRMED_ACCEPTANCE` leads to DAW promotion, then the route. `ACCEPTED_STATE_FAILED` or `UNCERTAIN` give the existing accepted-state reasons, with the found reference kept as authoritative: never the unaccepted handler, never recovery. |
| query answer `AcceptanceAbsent` | `authority_known=True` (the previous reference); **no** `restored_last` and no verified reference, because the fader is not restored | DAW unaccepted handler: `REQUIRED_RECOVERY_NOT_PERFORMED`. No integrity-based restoration claim. |
| query answer `AcceptanceAuthorityUnknown`, the query not performed (budget, time, recording), or an exception before the answer | the authority stays unknown | `ACCEPTANCE_COMMIT_UNCERTAIN` |

**Offline path.** It has the same latent commit-exception issue: an exception from `commit` is recorded as `RUNTIME_CONTRACT_FAILED` with the authority still known. It is left unchanged here to preserve offline behaviour, and is proposed as a separate follow-up.

**Tests added (written first):**
- **The commit succeeds, then raises:** a journal stand-in commits to the real journal and then raises. The terminal reason is `ACCEPTANCE_COMMIT_UNCERTAIN`, the authoritative and verified references are `None`, and the final state is `UNKNOWN`. The real journal's acceptance history holds the commit, the original exception propagates, and no restore is sent.
- **Unknown commit responses:**
  - `AcceptanceCommitUnknown` after a real write, then the query finds it: DAW promotion, `CANDIDATE_CRITERIA_MET`, `ACCEPTED_EDITS_RETAINED`, with the bindings pointing at the exports;
  - before the write, so the query reports absent: `REQUIRED_RECOVERY_NOT_PERFORMED`, `UNKNOWN`, never `RESTORED_TO_LAST_ACCEPTED`;
  - the query answers unknown: `ACCEPTANCE_COMMIT_UNCERTAIN`.
- **Failure after an acknowledged acceptance:**
  - recording `AcceptanceCommitted` is refused: `RECORDING_FAILED`, `ACCEPTED_EDITS_RETAINED`, verified equal to the accepted reference;
  - an exception while recording the accepted attempt's feedback: `RUNTIME_CONTRACT_FAILED`, the edit retained, no `REQUIRED_RECOVERY_NOT_PERFORMED`;
  - the found state fails integrity after reconciliation: `ACCEPTED_STATE_INTEGRITY_FAILED`, with the found reference authoritative.
- **Pre-commit failure:** cancellation before the commit gives `REQUIRED_RECOVERY_NOT_PERFORMED`, with no acceptance in the journal.

#### A2. Duplicate continuations when a continuation reaches another suspension

**Completion evidence.** A new trace payload, `DawContinuationFinished(run_id, started_event_id, export_id, disposition)`, with disposition `ACCEPTED` or `ENDED`. It is recorded exactly once per continuation:
- **Accepted:** after `finish_acceptance` is acknowledged, and before any next decision round. A later write and suspension of a new export therefore always follow it.
- **Ended:** as the last event before the terminal snapshot, when the continuation ends the run.

**A continuation counts as complete when** its `DawContinuationStarted` is followed by any of:
- its `DawContinuationFinished`;
- a terminal snapshot (covering an exception that skipped the marker but not finalization);
- a later `DawCorrectionSuspended` (defensive: unreachable when the marker is recorded first, but still sufficient).

**Admission rules (superseding step 1)**, decided from the journal snapshot taken under the lock. For the requested `export_id`:

| Trace state | Result |
| --- | --- |
| The checkpoint of this export is the trace's last event | continue |
| A `DawContinuationStarted` for this export with the same manifest, and the continuation is complete | `ALREADY_CONTINUED`. The refusal names the completion evidence's event ID, and the run's later state (suspended again, or terminated). |
| The same, with a different manifest | `MANIFEST_CONFLICT`, whether complete or not |
| Started, same manifest, not complete, and the lock was acquired | `CONTINUATION_INTERRUPTED` (new code): an earlier continuation stopped without completion evidence, for example because finalization also failed. Refused; recovery of such a run needs Increment 5. |
| The per-run lock is held by another call | `CONTINUATION_IN_PROGRESS` |
| The checkpoint is not in the trace | `NOT_SUSPENDED` |

**Per-run exclusive lock.**
- **New port:** `core.ports.run_journal.RunLock`, with `try_acquire(run_id) -> bool` and `release(run_id)`.
- **In-process adapter:** `adapters.journal.run_memory.InMemoryRunLock`, with one non-reentrant `threading.Lock` per run ID. It is NON_DURABLE, like the journal; a durable journal supplies its own exclusive lock in Increment 5.
- **Scope:** `resume_daw` acquires the lock before reading the snapshot for admission and holds it through admission and the whole execution. That includes any next write and suspension, and finalization. It is released in a `finally`, so admission and execution can never interleave with another continuation.
- **Refusal:** when the lock is not acquired, the result is `CONTINUATION_IN_PROGRESS`, before any read that could admit.
- **Scope of the lock:** `run_daw` does not take it, because a run's first segment has no continuation to race with.

**Tests added (written first):**
1. A continuation accepts the candidate, then suspends at the next export: one `DawContinuationFinished(ACCEPTED)` precedes the second checkpoint.
   - a duplicate of the first continuation with the same manifest gives `ALREADY_CONTINUED`, citing the finished marker;
   - with another manifest, it gives `MANIFEST_CONFLICT`;
   - neither makes an FL, file, analysis or journal-append call.
   - The continuation of the second export is then admitted normally.
2. A continuation that ends the run: its duplicate gives `ALREADY_CONTINUED`, citing the `ENDED` marker. Separately, a run whose marker append was refused and whose terminal snapshot exists also gives `ALREADY_CONTINUED`, citing the terminal snapshot.
3. A continuation with `DawContinuationStarted` but no completion evidence, built by a journal stand-in refusing every append after the start, including the terminal one: `CONTINUATION_INTERRUPTED`.
4. A second `resume_daw` issued from inside a collaborator call of the first, while the lock is held: `CONTINUATION_IN_PROGRESS`, with no read or dispatch. After the first returns, the lock is free.
5. `InMemoryRunLock` unit tests: exclusivity per run ID, independence across run IDs, release after an exception, and refusal of a release without ownership.

**Superseded scenarios.** Scenario 7's "interrupted by an exception then retried gives `CONTINUATION_IN_PROGRESS`" is replaced by A2 tests 2 and 3: finalization after an exception is completion evidence. Scenario 9 is extended by A1: an exception from the commit gives `ACCEPTANCE_COMMIT_UNCERTAIN`, not `REQUIRED_RECOVERY_NOT_PERFORMED`.

**Additional mutation probes:**
- leave the authority known while committing;
- treat confirmed-absent as a restoration;
- route a post-acceptance failure to the unaccepted handler;
- skip the `DawContinuationFinished` marker;
- read the snapshot before taking the lock;
- release the lock before execution.

## Increment 3 outcome (2026-11-19, tests-first; uncommitted for review)

Implemented as planned with amendment A and the approval's constraints, recorded in [ADR-037](../../decisions/0037-coordinator-daw-continuation.md) (Proposed).
- **Accepted authority before promotion:** on `AcceptanceCommitted`, or a validated `AcceptanceFound`, the new reference is selected before promotion, recording or other fallible work, and `verified` stays `None` until established.
- **One lock authority per run:** a per-run-ID registry in the in-memory adapter, shared by every journal instance of that run.
- **Rollback wording:** the `UNCERTAIN` DAW-scope rollback receipt carries "recovery not attempted".

**Deviations:**
- **C6, the shared tail:** not a split of the offline `verify_candidate`. The DAW tail lives in `core.loop.daw_continuation`, keeping the offline path byte-identical.
- **A contract found during implementation:** `FileExportHandoff` was bound to one before state, so a run could not prepare its second export. The adapter now accepts a driver-supplied state `inventory`. The port is unchanged.
- **Found-state inspection:** reconciling a found acceptance binds the candidate exports' root keys (location facts, not authority) before the integrity inspection.
- **No reconciliation record:** reconciliation records no `ReconciliationResult` for DAW, because the offline `reconcile_acceptance` validates with the offline derivation. The authority answer and the integrity report are recorded as operation observations.
- **Increment 2 test fixture:** its session named a render spec its baselines did not have. It now uses the baselines' own, as the stricter preflight requires.

**Tests:**
- **Continuation** (`tests/unit/test_daw_continuation.py`, 27 tests):
  - accept; iterate to a second suspension, with duplicate and conflict refusals afterwards;
  - rejected and inconclusive candidates;
  - three file rejections;
  - continuity breaks: a fader moved during the pause, only during the import, or back during the import, and a ledger entry during the pause;
  - pause accounting;
  - commit succeeds then raises;
  - unknown commits: found, absent and unknown;
  - refused recording after an acknowledged acceptance, and an exception during promotion;
  - interrupted, concurrent and foreign continuations;
  - the one-authority lock;
  - an exception during the import;
  - export-settings refusal before any write;
  - the export-root promotion.
- **Handoff:** two inventory tests.

**Mutation probes:** 14 new, each reverted byte-identically and caught. One survived first (an inventory state that is not the request's) and was caught after the adapter regression was added. All earlier sets are still caught.

### Increment 3 review fixes (2026-11-19)

1. **A found acceptance was established only after recording it.** In `reconcile_daw_acceptance`, `observed()` ran before `_established()`. Codex reproduced a valid `AcceptanceFound` followed by an exception appending its observation: the journal held the acceptance, but the terminal record reported unknown authority.
   - **Fix:** the answer is validated against the submitted event and its reference established before it is recorded. A confirmed-absent answer sets the recovery-required marker first.
   - **Regressions:** an exception while recording a found answer keeps the found reference authoritative. An exception while recording an absent answer keeps `REQUIRED_RECOVERY_NOT_PERFORMED`, with the previous reference.
2. **Resume did not bind the supplied request to the recorded one.** Only the run ID and initial state were checked. A changed write budget went through import, analysis and commit, and was refused only when the final report was built.
   - **Fix:** `admit_continuation` requires the supplied `RunRequest` to equal the trace's recorded one exactly, returning `REQUEST_MISMATCH` before anything is appended or dispatched.
   - **Order of checks:** a trace without the checkpoint is still `NOT_SUSPENDED`.
   - **Regressions:** substituted budget, criteria and DAW-session settings are each refused with no FL call, import or append, and the recorded request then still continues.

**Mutation probes.** Two new, both caught: recording the answer before establishing it, and dropping the recorded-request check. All earlier sets are still caught.
