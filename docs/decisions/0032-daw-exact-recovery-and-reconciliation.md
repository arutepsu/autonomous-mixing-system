# ADR-032 — DAW exact-target recovery, explicit reconciliation and the shared command ledger (2026-11-18)

- **Status:** Proposed, for owner and Codex review. It implements the owner-approved plan [2026-11-18-daw-recovery-reconciliation](../superpowers/plans/2026-11-18-daw-recovery-reconciliation.md) (revision 2, with Amendment R) as unintegrated feasibility infrastructure.
  - It does not accept ADR-030 or ADR-031, and does not resolve scope decision D15. FR-14, ADR-001 and ADR-009 are unchanged.
  - It extends ADR-031 (Proposed) in two respects, both stated below: the runtime protocol is no longer byte-identical to the research snapshot, and the journal gains a ledger.

## Decision

1. **A separate recovery port.**
   - `core.ports.daw_recovery.DawRecoveryController`: `control_state`, `restore_target`, `reconcile`, `restore` and `resolve`. `DawParameterController` is unchanged.
   - A restoration needs a `DawRecoveryAuthorization` that carries a `RECOVERY_OPERATION` `BudgetGrant`. It is separate from the cuts-only `AdmittedAction`.
   - `gate_daw_restore` permits upward or downward restoration to a target inside the calibrated range, from any current reading. It also checks scope, conversion, identity, build and lineage, and returns `DawRestoreNoWrite` on an exact current match.
2. **Exact confirmation.** `DawRestoreConfirmed` requires both read-back readings to equal the journaled target exactly. Anything else that landed is `DawRestoreMismatch`: a known write that keeps actions blocked. Failed, refused and uncertain restorations stay unresolved.
3. **Targets from the journal only.** `restore_target` recomputes the target from the root apply's journal row:
   - the outcome's `before` when one is recorded (`OUTCOME_BEFORE`);
   - otherwise the root request's inspected reading (`REQUEST_INSPECTION`);
   - none for a refused root.

   The journal repeats every lineage and agreement check: binding, profile, conversion, basis and reading against the root's request.
4. **Explicit reconciliation; only resolution clears.**
   - `reconcile` performs its own fresh read and classifies it from the reading alone:
     - matches both references → `AMBIGUOUS`;
     - the commanded value only → `AT_COMMANDED`;
     - the recorded start only → `AT_RECORDED_START`;
     - neither → `NEITHER`;
     - unverifiable identity, build or read → `IDENTITY_UNVERIFIED`.

     It then appends the finding and clears nothing.
   - `resolve` requires a resolution authorization and evidence that is:
     - **subject-specific:** a reconciliation of that command, or, for a root, a confirmed restoration of it (Amendment R). A child's finding never resolves its parent;
     - **the latest relevant evidence across the whole lineage**, with no write reserved after it. The lineage is the root and every restoration of it: a newer finding about any member, or a newer restoration outcome, makes older evidence stale *(revised after review on 2026-11-18; the first version compared findings for the same subject only)*.
   - Inspect remains observational.
5. **One ordering.** The journal (schema v2) gains an append-only `ledger`. A single `seq` orders every write reservation, recovery charge, reconciliation and resolution, so "after" is well defined.
   - The ledger is insert-only. The one permitted change to an existing command row is completion: filling a still-empty outcome, guarded in SQL.
   - v1 journals migrate in place. Existing field contents are unchanged, and their reservations are ledgered in v1 rowid order.
   - **Markers** *(revised after review on 2026-11-18)*:
     - every command carries exactly one marker: a `RESERVED` row, or an explicit `LOCAL_REFUSAL` row, allowed only for a refused apply recorded by the local refusal path;
     - every reserved restoration carries exactly one `RECOVERY_CHARGED` row;
     - every consumed resolution authorization is registered as a `RESOLUTION_AUTHORIZED` ledger entry, so its ID belongs to the shared namespace that commands, inspections, restorations, reconciliations and resolutions also use.

     A journal that violates any of these fails closed on open. A missing reservation never reads as "nothing unresolved".
6. **Charges before dispatch.** A restoration's `RECOVERY_CHARGED` row is committed in the same transaction as its reservation, before any byte is sent.
   - It is charged once per stable recovery ID: a replay appends nothing, and reusing an authorization is refused.
   - It is never refunded: uncertain, mismatched and post-reservation refusals keep their charge.
   - A refusal before reservation sends and charges nothing, and is cached in memory only, not journaled. It therefore does not block, and its ID is not reserved across a restart.
7. **Conversion blocks have no clearing transition in this version.** An off-target block persists through restoration, reconciliation, resolution and reopen, until a separately reviewed recalibration-evidence workflow defines a validated clearance.
8. **The wire command `restore_gain`, a runtime protocol v2 snapshot, and device script v2.**
   - **Protocol:** the runtime protocol adds `restore_gain`: exact fields, the target inside the calibrated range and on the curve, no conversion field, no cut bounds. Every existing command and reply behaves exactly as the frozen research protocol, a parity verified on a corpus. The research file stays pinned at `03637001…`; the runtime snapshot is pinned at `68fb70dd…`.
   - **Device script v2:** `device/fl_studio/thesis_bridge_v2/` holds `device_thesis_bridge.py` and a byte-identical copy of the runtime protocol. The script is derived from the reviewed v1 script by explicit substitutions.
   - **Isolation:** the directory is outside the importable package, with no `__init__.py`. FL host modules are imported only inside functions, and no `src/` module imports it.
   - **Safety against the installed v1 script:** it refuses `restore_gain` before any FL call (`invalid_command`/`type`). The runtime records that as `DawRestoreRefused(UNSUPPORTED)`, charged and still blocking.
   - **Installation:** installing v2 is a separately authorized step.

## Rationale

- **Recovery and rollback.** Rollback after an unaccepted DAW change, and recovery from an uncertain write, both need a raise to an exact recorded value. A cuts-only action cannot express that, and the calibration probe is trial infrastructure.
- **Clearing.** Clearing must be evidence-based and explicit, and must be refused for stale or transferred evidence. That needs one ordering of reservations and evidence.
- **Charging.** Persisting the charge with the intent makes budget accounting survive crashes and retries without double charging.

## Consequences

- **Readings only.** Every recovery or resolution record concerns this fader's reported parameter readings. None proves routing, plugins, the complete project, saved state or audio; a confirmed restoration is never candidate-ready or accepted audio.
- **The later coordinator** must:
  - derive its recovery budget usage from the journal's charges, and never charge again on a replay;
  - issue recovery and resolution authorizations only after validating fresh evidence;
  - supply `gate_daw_apply`'s unresolved-command and conversion-block inputs from `control_state()`.
- **Evidence status.** Tests run the real runtime protocol, exchange, journal and controller against device v2 on a fake host. That is not live FL evidence, and live validation of v2 and of runtime recovery needs separate authorization.
- **Durability:** process-crash tests only, as ADR-031. No power-loss claim.

## Sources

- [Recovery and reconciliation plan](../superpowers/plans/2026-11-18-daw-recovery-reconciliation.md), approved revision 2 with Amendment R.
- [ADR-030](0030-daw-parameter-control-boundary.md) and [ADR-031](0031-fl-studio-control-journal.md), Proposed.
- [Approved adapter design](../superpowers/specs/2026-11-18-fl-studio-action-adapter-design.md), "Recovery".
