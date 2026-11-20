# DAW Exact-Target Recovery and Explicit Reconciliation — Implementation Plan

> **Status: revision 2 approved by the owner (2026-11-18), with one final amendment to resolution evidence, incorporated below as *Amendment R*.** Execute task by task with tests first, and leave everything uncommitted for owner and Codex review. No installation, live FL calls, saves, exports, commits or pushes.

**Revision 2** answers the review of revision 1. The six decisions are kept in principle, with budget charging corrected. The changes:

| Review point | Change | Section |
| --- | --- | --- |
| 1 | Explicit lineage; source, binding, profile and conversion agreement; a defined target basis | Lineage and target |
| 2 | One ledger orders write reservations, recovery charges and evidence | Journal |
| 3 | Conversion clearing is **unavailable** in this slice | Decision 5 |
| 4 | Reconciliation classified from the fresh reading's two matches; the wrong 2 × 0.05 dB claim removed | Reconciliation |
| 5 | Recovery charge persisted before dispatch, once per stable ID, never refunded | Budget charge |
| 6 | Only ledger and events are append-only, command completion keeps its UPDATE; device-script isolation defined; "unchanged rows" defined | Journal; Device script |

**Goal:**
- restore the exact previously recorded normalized fader value, including upward, under a recovery authorization separate from cuts-only actions;
- reconcile an unresolved command from explicit evidence, and record a resolution before any further action;
- keep every original request, outcome and uncertain receipt, appending new records only;
- leave conversion blocks in place.

**Builds on:**
- [adapter design](../specs/2026-11-18-fl-studio-action-adapter-design.md) ("Recovery");
- [DAW control contract](2026-11-18-daw-control-contract.md);
- [transport adapter](2026-11-18-fl-studio-transport-adapter.md);
- [ADR-030](../../decisions/0030-daw-parameter-control-boundary.md) and [ADR-031](../../decisions/0031-fl-studio-control-journal.md), both Proposed.

**Baseline:** HEAD `033b76d`, with the coordinate, control and transport slices uncommitted (reported: 5,785 passed, 4 skipped; 630 focused).

## Global constraints

- **Out of this slice:** export continuation, coordinator integration, runtime adoption, CLI/driver exposure, recalibration and conversion clearing, and any live trial. D15, FR-14, ADR-001 and ADR-009 are unchanged.
- **`probe_gain` is not a production API.** It stays calibration infrastructure; the runtime never sends it, and no recovery record refers to it.
- **Existing contracts are unchanged:** `DawParameterController.inspect/apply`, `DawApplyRequest` (cuts only), `gate_daw_apply`, the existing outcomes, and the offline rendering, `ExecutionReceipt`, `RollbackReceipt` and `ExecutionStatus` contracts. Recovery types are additions.
- **Restoring one fader proves only that fader's reported parameter readings.** It proves nothing about routing, plugins, the complete project, saved state or audio.
- **Layering:** records are pure, ports are protocols plus validators, admission is pure, and the adapter owns I/O. Initializers stay docstring-only.
- **Checks:** `uv run --locked …`, plus mutation probes reverted byte-identically.

## Decisions for review

1. **A new additive wire command, `restore_gain`, and a device script v2 owned by the adapter component.** Existing command bytes are unchanged and the frozen research scripts stay byte-identical.
   - **Safe against the installed v1 script:** `restore_gain` is refused as `invalid_command`/`type` before any FL call, which maps to `DawRestoreRefused(UNSUPPORTED)`. A test pins this against the frozen script with a fake host.
   - **Storage and isolation:** see "Device script v2 and import isolation".
   - **Record:** ADR-032 (Proposed).
2. **Exact-restoration criterion.** *Confirmed* requires both read-back readings to equal the target exactly. Landing within tolerance but not exactly is `DawRestoreMismatch`: a known write that keeps actions blocked.
3. **Upward and downward restoration, inside the calibrated range and on the curve.**
   - The target must lie inside both profile ranges, and its dB on the curve within 0.01 dB (checked by the adapter and the device).
   - The current start may lie anywhere in [0, 1].
   - There are no cut limits.
4. **Reconciliation only records evidence; clearing needs a separate, authorized resolution.**
5. **Conversion clearing is unavailable in this slice** *(changed by review point 3)*.
   - No method, record or journal transition clears a conversion block.
   - An off-target block persists across restoration, reconciliation, resolution and reopen, until a later, separately reviewed recalibration-evidence workflow defines a validated clearance.
   - Tests assert that no public API clears it.
6. **Recovery authorization carries a `RECOVERY_OPERATION` budget grant, separate from `AdmittedAction`, and the charge is persisted before dispatch** *(corrected by review point 5; see "Budget charge")*. A `BudgetGrant` remains a proposed charge: core cannot prove it follows any configured limits.

## Lineage and target (review point 1)

**Roots.** Every recovery belongs to exactly one **root**: an `apply` command journaled by this adapter.
- A restoration's `source_command_id` is always the root, never another restoration.
- Repeated attempts after a failed, mismatched or uncertain restoration cite the same root.
- The journal records each restoration's root and an attempt number (1, 2, …, in ledger order).
- "Earlier restorations of the same command" means exactly the restore rows with the same root.

**Agreement checks** before a restoration is reserved, with any failure refused before sending:

| What is compared | Requirement |
| --- | --- |
| root | exists, is an `apply` row in this journal, and is not itself a restoration |
| restore request's binding | equal to the root's canonical request binding: reference, project ID, track ID, title, insert index and name, baseline gain and baseline fader |
| restore request's profile | equal to the root request's profile, and to the adapter's configured profile |
| `authorization.target.conversion` | the root request's `profile.conversion` |
| `authorization.target.source_command_id` | the root's ID |
| `authorization.target.reading` | exactly the reading defined by the target basis below, recomputed from the journal row |

**Target basis.** The `before` the restoration returns to:

| Root outcome | Target | `basis` |
| --- | --- | --- |
| `DawWriteConfirmed`, `DawWriteOffTarget`, or `DawWriteUncertain` with `before` | the outcome's `before`: what the device read immediately before its write | `OUTCOME_BEFORE` |
| pending, or `DawWriteUncertain` with `before = None` | the root request's `inspection.fader`, the reading the gate approved (within start tolerance of what the device would have read) | `REQUEST_INSPECTION` |
| `DawWriteRefused` | none: nothing was written. `restore_target` returns `PortFailure(INVALID_INPUT)` | — |

`DawRestoreTarget` records the basis, so the evidence states which journaled reading was used.

## Records (`core/records/daw_recovery.py`, new)

**Edges:** `identifiers`, `invariants`, `daw`, `daw_control`, `budgets`. **IDs:** `[A-Za-z0-9_-]{8,64}`, in one journal namespace shared by commands, inspections, restorations, reconciliations, resolutions and authorizations.

```python
DawCommandKind(Enum): APPLY | RESTORE
DawTargetBasis(Enum): OUTCOME_BEFORE | REQUEST_INSPECTION
DawRestoreTarget(source_command_id: str, basis: DawTargetBasis, reading: DawFaderReading, conversion: VersionRef)
DawRecoveryAuthorization(authorization_id: str, execution_id: ExecutionId, authorized_by: VersionRef,
                         target: DawRestoreTarget, budget_grant: BudgetGrant)   # grant: RECOVERY_OPERATION
DawRestoreRequest(recovery_command_id: str, authorization: DawRecoveryAuthorization,
                  binding: DawTrackBinding, profile: DawControlProfile, inspection: DawInspection)
DawRestorePermitted(request) | DawRestoreNoWrite(request) | DawRestoreRejected(code: DawRestoreGateCode, explanation)

DawRestoreConfirmed(recovery_command_id, before, after, target: DawFaderReading, replayed: bool)
DawRestoreMismatch(recovery_command_id, before, after, target, replayed: bool)
DawRestoreRefused(recovery_command_id, code: DawRefusalCode, explanation, observed: DawFaderReading | None)
DawRestoreUncertain(recovery_command_id, reason: DawUncertainReason, before | None, after | None, explanation)
DawRestoreOutcome = DawRestoreConfirmed | DawRestoreMismatch | DawRestoreRefused | DawRestoreUncertain

DawUnresolvedCommand(command_id: str, kind: DawCommandKind,
                     root_command_id: str,          # == command_id for an apply; the root for a restoration
                     attempt: int | None,           # None for an apply; 1, 2, ... for restorations of the root
                     ledger_seq: int,               # its reservation's position in the ledger
                     recorded_start: DawFaderReading, commanded: DawFaderReading,
                     outcome: DawApplyOutcome | DawRestoreOutcome | None)   # None = pending, no outcome
DawRecoveryCharge(authorization_id: str, recovery_command_id: str, ledger_seq: int, budget_grant: BudgetGrant)
DawControlState(unresolved: tuple[DawUnresolvedCommand, ...],     # ordered by ledger_seq
                blocked_conversions: frozenset[VersionRef],
                recovery_charges: tuple[DawRecoveryCharge, ...])  # ordered by ledger_seq

DawReconcileRequest(reconciliation_id: str, subject_command_id: str, binding: DawTrackBinding,
                    profile: DawControlProfile)
DawReconcileFinding(Enum): AT_COMMANDED | AT_RECORDED_START | AMBIGUOUS | NEITHER | IDENTITY_UNVERIFIED
DawReconciliation(reconciliation_id, subject_command_id, ledger_seq: int, finding: DawReconcileFinding,
                  inspection: DawInspection | None, recorded_start, commanded,
                  matches_start: bool, matches_commanded: bool, replayed: bool)

DawResolutionKind(Enum): APPLIED_AS_COMMANDED | NOT_APPLIED_OR_REVERTED | RESTORED_TO_RECORDED_START
DawResolutionAuthorization(authorization_id: str, authorized_by: VersionRef)
DawResolutionRequest(resolution_id: str, subject_command_id: str, kind: DawResolutionKind,
                     evidence_id: str, authorization: DawResolutionAuthorization)
DawResolutionRecorded(resolution_id, subject_command_id, root_command_id, kind, resolved_reading,
                      evidence_id, ledger_seq: int, replayed: bool)
DawResolutionRefused(resolution_id, code: DawResolutionRefusalCode, explanation)
```

**Invariants** (`__post_init__`, `ValueError`):
- **Types, IDs and readings:** finite, non-bool numbers.
- **`DawRecoveryAuthorization`:** the grant's operation is `RECOVERY_OPERATION`.
- **`DawUnresolvedCommand`:**
  - for an `APPLY`, `root_command_id == command_id` and `attempt is None`;
  - for a `RESTORE`, the root differs from the command ID and `attempt ≥ 1`;
  - `ledger_seq ≥ 1`.
- **`DawRestoreRequest`:**
  - the inspection answers `DawInspectRequest(binding, profile)`;
  - the profile covers the binding;
  - the target lies inside both ranges;
  - the profile's conversion equals the target's conversion;
  - the recovery command ID is distinct from the authorization and root IDs.
- **Restore outcomes:** `Confirmed` requires both readings exactly equal to `target`; `Mismatch` requires them not both exactly equal.
- **`DawReconciliation`:** the finding equals the classification of `(matches_start, matches_commanded)` defined below; `inspection` is `None` exactly when the finding is `IDENTITY_UNVERIFIED` because the read failed.
- **`DawResolutionRecorded`:** the kind is paired with its evidence type, a reconciliation finding or a confirmed restoration.

## Reconciliation (review point 4)

**Inputs:** the subject's `recorded_start` S and `commanded` C, and the fresh reading R.
- For an apply, C is the journaled commanded normalized position, with dB `S.db + requested_change_db`.
- For a restoration, C is its target.
- If the read failed, or the identity or build does not match, the result is `IDENTITY_UNVERIFIED` and both matches are false.

**Matches**, computed from R alone, with inclusive edges and exact floats:
- `matches_start` = `|R.normalized − S.normalized| ≤ start_normalized_tolerance` and `|R.db − S.db| ≤ start_db_tolerance`;
- `matches_commanded` = `|R.normalized − C.normalized| ≤ readback_normalized_tolerance` and `|R.db − C.db| ≤ success_db_tolerance`.

**Classification:**

| `matches_start` | `matches_commanded` | Finding |
| --- | --- | --- |
| yes | yes | `AMBIGUOUS` |
| no | yes | `AT_COMMANDED` |
| yes | no | `AT_RECORDED_START` |
| no | no | `NEITHER` |

Ambiguity is decided per reading, never from how close S and C are. It can occur for any small change, including a 0.1 dB cut: 0.1 dB does **not** exceed 2 × 0.05 dB, which corrects revision 1.

**Resolvable findings:**
- `AT_COMMANDED` → `APPLIED_AS_COMMANDED`;
- `AT_RECORDED_START` → `NOT_APPLIED_OR_REVERTED`.

**Amendment R — resolution evidence is subject-specific and the latest relevant.**
1. **Subject-specific interpretation.** A reconciliation can resolve only the command it names: its `subject_command_id` must equal the resolution's subject. A finding about a child restoration is never transferred to its parent root.
   - *Example:* apply A goes from 0 dB toward −1 dB. Restoration R of A tries to return to 0 dB but leaves the fader at −1 dB. Reconciling R finds `AT_RECORDED_START`, meaning R's own start of −1 dB. That finding must not resolve A as `NOT_APPLIED_OR_REVERTED`.
   - A root may instead be resolved by citing a **confirmed restoration of that root**, as `RESTORED_TO_RECORDED_START`. That is the only cross-command evidence permitted, because a confirmed restoration's target *is* the root's recorded start.
2. **Latest relevant evidence.** The cited evidence must be the latest relevant evidence in the lineage, in ledger order, **and** no reservation may follow it.
   - *Relevant evidence* for subject S: every `RECONCILED` row whose subject is S, plus, when S is a root, every restoration of S that has an outcome. Each restoration counts at its reservation `seq`.
   - A newer reconciliation of S, of any finding including `NEITHER`, `AMBIGUOUS` or `IDENTITY_UNVERIFIED`, supersedes an older favourable one.
   - A newer restoration of the root supersedes an older reconciliation of the root, and the reverse also holds.
   - Refusal code: `STALE_EVIDENCE` (newer relevant evidence exists) or `RESERVATION_AFTER_EVIDENCE`.

`AMBIGUOUS`, `NEITHER` and `IDENTITY_UNVERIFIED` cannot be resolved from a reading. They need an authorized restoration of the root, confirmed exactly, then `RESTORED_TO_RECORDED_START` citing it; or an operator restoration followed by a new reconciliation finding `AT_RECORDED_START`.

**What a finding establishes:** the current reading of this fader only. It does not establish whether, when or how often the device executed the command, nor any external movement, routing, plugins, project or audio.

## Budget charge (review point 5)

- **Charged in the reservation transaction.** `reserve_restore` appends, in **one transaction committed before any byte is sent**:
  - the ledger `RESERVED` row and the restore command row (pending);
  - a ledger `RECOVERY_CHARGED` row with `authorization_id`, `recovery_command_id` and the canonical `budget_grant`.
- **One charge per stable ID.** An identical retry of the same `recovery_command_id` replays and appends no charge. A different request under that ID, or reuse of the same `authorization_id` for another recovery command, is refused (`DUPLICATE_ID_CONFLICT`) without a charge.
- **Never refunded.** Uncertain, mismatched, refused-on-the-wire and failed restorations keep their charge. Only a refusal **before** reservation (gate, agreement checks, local wire validation) charges nothing, because nothing can have been sent.
- **The coordinator's obligation, later:**
  - treat `control_state().recovery_charges` as the authoritative record of charges incurred;
  - derive its `BudgetUsage` from the recorded `usage_after_charge` of the latest charge for its run;
  - never re-charge a replayed ID.

  A `BudgetGrant` is a proposed charge, and its presence does not prove that limits were enforced.

## Port (`core/ports/daw_recovery.py`, new protocol; `DawParameterController` unchanged)

```python
class DawRecoveryController(Protocol):
    def control_state(self) -> PortResult[DawControlState]: ...
    def restore_target(self, source_command_id: str) -> PortResult[DawRestoreTarget]: ...
    def reconcile(self, request: DawReconcileRequest) -> PortResult[DawReconciliation]: ...
    def restore(self, request: DawRestoreRequest) -> DawRestoreOutcome: ...
    def resolve(self, request: DawResolutionRequest) -> DawResolutionRecorded | DawResolutionRefused: ...

def validate_daw_restore_outcome(request: DawRestoreRequest, outcome: DawRestoreOutcome) -> None
def validate_daw_reconciliation(request: DawReconcileRequest, result: PortResult[DawReconciliation]) -> None
def validate_daw_resolution(request: DawResolutionRequest, outcome) -> None
def validate_daw_control_state(result: PortResult[DawControlState]) -> None
```

There is no `clear_conversion` method (decision 5).

**Obligations** (docstring): the same pending-before-dispatch, replay, conflict and uncertainty rules as `apply`, plus:
- the recovery charge is committed before dispatch;
- `restore_target` returns only a target recomputed from the adapter's own journal under the basis rule;
- `reconcile` performs its own fresh read and appends its finding;
- nothing clears without `resolve`;
- conversion blocks never clear in this version.

**Gate** (`core/admission/daw.py`, addition): `gate_daw_restore(authorization, *, binding, inspection, profile, recovery_command_id, control_state) -> DawRestorePermitted | DawRestoreNoWrite | DawRestoreRejected`. The checks run in this order:
1. **Scope:** the profile covers the binding (`PROFILE_SCOPE`).
2. **Conversion:** the profile's conversion equals the target's (`CONVERSION_MISMATCH`).
3. **Identity and build** (`IDENTITY_MISMATCH`, `UNSUPPORTED_BUILD`).
4. **Lineage** (`UNRESOLVED_OTHER`): every unresolved command must belong to the authorization's root, meaning the root itself or a restoration of it.
5. **Target range** (`TARGET_OUT_OF_RANGE`).
6. **No change needed:** an exact current reading equal to the target gives `DawRestoreNoWrite`, with nothing sent and nothing charged.
7. Otherwise `DawRestorePermitted`.

The journal repeats the lineage and agreement checks authoritatively. `gate_daw_apply` is unchanged.

## Journal (adapter, schema v2; review points 2 and 6)

**Migration v1 → v2**, in one transaction after the integrity check:
- `commands` gains `kind TEXT NOT NULL DEFAULT 'apply'`, `root_command_id TEXT` (NULL for an apply) and `attempt INTEGER` (NULL for an apply);
- a new `ledger(seq INTEGER PRIMARY KEY AUTOINCREMENT, entry_kind TEXT NOT NULL, entry_id TEXT NOT NULL, subject_command_id TEXT NOT NULL, record_json TEXT NOT NULL, fingerprint TEXT NOT NULL, UNIQUE(entry_kind, entry_id))`;
- existing apply rows receive `RESERVED` ledger rows in `commands.rowid` order. That order is their v1 insertion order; v1 never runs `VACUUM`, and the migration records the order it used;
- `user_version=2`.

**"Unchanged rows"** means unchanged existing field contents: every existing `command_id`, `request_json`, `command_json`, `fingerprint` and `outcome_json` is identical before and after migration, compared field by field in tests. It does not mean an unchanged database file. An unknown version, a malformed ledger row, a fingerprint mismatch or an unknown entry kind fails closed.

**Shared ordering.** The ledger is the single ordering of everything that matters for "after":

| `entry_kind` | Appended in the same transaction as |
| --- | --- |
| `RESERVED` | every apply or restore reservation, together with its command row |
| `RECOVERY_CHARGED` | a restore reservation |
| `RECONCILED` | a reconciliation finding |
| `RESOLVED` | a resolution |

Completion (`finish`) updates only its command's `outcome_json`. It appends no ledger row, because the ordering questions concern reservations and evidence, not completion time. "No write reserved after this evidence" means no `RESERVED` row with a `seq` greater than the evidence's `seq`.

**Immutability:**
- The **`ledger` table is append-only**: no `UPDATE` or `DELETE` statement anywhere in the module targets it, which a source-scan test checks, and its row count only grows.
- **`commands` keeps exactly one permitted `UPDATE`:** `finish`, from `outcome_json IS NULL` to an outcome, under a `WHERE outcome_json IS NULL` guard that is tested.
- No other column of an existing command row ever changes after insert.

**Derived state.** Recomputed from all rows on every call; no cached authority.
- `unresolved` = commands where:
  - (an apply is pending or uncertain, **or** a restore is anything other than confirmed);
  - **and** there is no `RESOLVED` row for that command.

  A `RESOLVED` row for the root also resolves every restoration of that root that preceded it in `seq`. A later restoration reserved after the resolution is a new lineage member and is treated on its own.
- `blocked_conversions` = conversions of `DawWriteOffTarget` applies. Nothing removes them in this slice.

**Transitions:**

| Operation | Preconditions (journal, authoritative) | Appends, in one transaction |
| --- | --- | --- |
| `reserve` (apply, existing) | as now: no unresolved command, conversion not blocked, ID unused | `RESERVED` and the command row |
| `reserve_restore` | ID and authorization unused; lineage and agreement checks pass; every unresolved command belongs to the root | `RESERVED`, `RECOVERY_CHARGED`, and the restore row (root, next attempt) |
| `finish` | a pending row for that ID | the outcome (guarded `UPDATE`) |
| `record_reconciliation` | the subject exists and is unresolved; ID unused | `RECONCILED(finding, matches, reading)` |
| `record_resolution` | the subject is unresolved; the evidence is **subject-specific** (a reconciliation of the subject itself, or a confirmed restoration of the subject when it is a root) and resolvable for the requested kind; it is the **latest relevant evidence** for the subject; **no `RESERVED` row has a `seq` above it**; ID and authorization unused (Amendment R) | `RESOLVED(kind, resolved_reading, evidence_id, root, authorization)` |

**Replay:** an identical request with the same ID returns the recorded result, with `replayed=True` where the record has that field, and appends nothing. A different request under the same ID is refused with a conflict code. A pending restoration is never resent.

**Durability:** as ADR-031. Process-crash tests only, with no power-loss claim.

## Wire command

```json
{"v":1,"id":"…","type":"restore_gain","project_title":"…","insert":{"index":2,"name":"Bass"},
 "expected":{"normalized":…,"db":…},"target":{"normalized":…,"db":…}}
```

**Runtime protocol validation:** exact fields; the target inside the calibrated range and on the curve within 0.01 dB; finite, non-bool values. No conversion field and no cut bounds.

**Device v2 checks, in order:**
1. API;
2. title;
3. insert;
4. build (`uncalibrated_build`);
5. transport stopped;
6. `safeToEdit`;
7. start within 0.001 / 0.05 dB of `expected` (`start_gain`);
8. target range and curve (`range`, `uncalibrated_start`).

Then one `setTrackVolume(target.normalized)` and a read-back. The reply is `applied` with before and after when normalized is within 0.001, otherwise `uncertain`/`readback_disagrees`. The same uncertain-boundary and duplicate-history rules as `set_gain_db` apply.

**Runtime mapping:**

| Wire reply | Restore outcome |
| --- | --- |
| `applied`, both readings exactly equal to the target | `DawRestoreConfirmed` |
| `applied`, otherwise | `DawRestoreMismatch` |
| `precondition_failed` / `identity_mismatch` / `invalid_command` (including `type` from a v1 script) / `api_too_old` / `unsupported` | `DawRestoreRefused` with the matching code; `type` and `unsupported` give `UNSUPPORTED` |
| wire `uncertain`, timeout, malformed or contradictory reply, interruption, commit failure | `DawRestoreUncertain` |

A wire-level refusal comes after reservation, so it is charged.

## Device script v2 and import isolation (review point 6)

- **Location: outside the importable package**, in `device/fl_studio/thesis_bridge_v2/`, a directory with no `__init__.py`. It is never on `sys.path` and never imported by runtime code. It holds:
  - `device_thesis_bridge.py`, the controller script, with header `# name=Thesis Bridge v2`;
  - `thesis_bridge_protocol.py`, a byte-identical copy of the runtime `adapters/fl_studio/protocol.py`, which the device imports by its plain module name inside FL.
- **FL host modules** (`general`, `mixer`, `transport`, `ui`, `device`) are imported only **inside** `OnInit`/`OnSysEx` and the host wrapper, as in the reviewed v1 script. The module's top level imports only `thesis_bridge_protocol`, which itself imports only `json`, `math` and `re`. This mirrors how FL loads scripts.
- **Tests** load the device script with `importlib` from its file path in an isolated namespace: the protocol copy is placed in a dedicated `sys.modules` entry for the test, the host is a fake, and the original module state is restored afterwards. Ordinary Python therefore never needs FL modules.
- **Architecture tests:**
  - no module under `src/` imports anything from `device/`;
  - the device protocol copy is byte-identical to the runtime protocol;
  - the device script's top-level imports are exactly the protocol module;
  - layout and import scans cover `src/` only, which is unchanged, and a new scan covers `device/`.
- **Ownership and installation:** owned by `adapters.fl_studio` in documentation (ADR-032). Installing it into FL is a separately authorized step.

## Controller (adapter; implements `DawRecoveryController` beside the existing port)

The order follows `apply`:
- canonicalize;
- lookup and replay;
- the journal's lineage and agreement preconditions;
- local wire and profile checks;
- **commit the reservation and charge before sending**;
- one exchange;
- map the reply, then run the core validator;
- finish before returning.

Any failure after intent may have committed is uncertain, and a commit failure poisons the controller. `reconcile` runs a fresh inspect exchange with its own reserved inspection ID, computes both matches with the core rule, appends `RECONCILED` and returns it. Inspect itself stays read-only.

**`resolve` flow (Amendment R):**
1. lookup and replay;
2. the subject is unresolved;
3. the evidence exists and is subject-specific;
4. the kind matches the evidence: `AT_COMMANDED` → `APPLIED_AS_COMMANDED`, `AT_RECORDED_START` → `NOT_APPLIED_OR_REVERTED`, a confirmed root restoration → `RESTORED_TO_RECORDED_START`;
5. it is the latest relevant evidence;
6. no later reservation;
7. append `RESOLVED` in one transaction that re-checks steps 2–6.

Each failure gives `DawResolutionRefused` with its code and appends nothing.

## Failure classification

| Situation | Classification | Charge | Blocks |
| --- | --- | --- | --- |
| gate, lineage, agreement or local wire check fails | `DawRestoreRejected` / `DawRestoreRefused`; nothing sent | none | unchanged |
| v1 device script | `DawRestoreRefused(UNSUPPORTED)`, pre-write (pinned) | charged (after reservation) | yes |
| exact restoration | `DawRestoreConfirmed` | charged | until `resolve`, if the root was unresolved |
| lands, but not exactly | `DawRestoreMismatch`, a known write | charged | yes |
| any unknown after intent | `DawRestoreUncertain` | charged, never refunded | yes: reconcile the restoration next |
| identical retry | replayed outcome, no send | no new charge | unchanged |
| reconciliation read fails or mismatches | `IDENTITY_UNVERIFIED`, recorded | none | yes |
| resolution with evidence that is stale (a reservation after it), foreign or not resolvable | `DawResolutionRefused` | none | yes |
| off-target conversion | stays blocked | — | yes, permanently in this slice |

## Later coordinator responsibilities (not implemented here)

- **Recovery budget:** call `check_budget(BudgetOperation.RECOVERY_OPERATION, …)`. On a `BudgetGrant`, build `DawRecoveryAuthorization(…, budget_grant=grant)` from `restore_target(root)`, then gate and restore. Treat the journal's `RECOVERY_CHARGED` rows as the charges incurred. On a `BudgetBlock`, issue no authorization and stop with a typed result.
- **Resolution authorization:** issue `DawResolutionAuthorization` only after validating fresh evidence.
- **Gate inputs:** supply `gate_daw_apply`'s `unresolved_command_id` and `blocked_conversions` from `control_state()`.
- **Next state:** derive the next `DawTrackState` from the resolution or confirmed restoration. The selected gain follows the resolution kind.
- **Candidates:** nothing makes a candidate ready or accepted.

## Tasks (each tests first)

1. **Records and validators.**
   - Every invariant above.
   - The reconciliation classification table, at each tolerance edge and one float step beyond, for applies and restorations, including an `AMBIGUOUS` reading for a 0.1 dB cut.
   - The target basis: each root outcome type gives the expected basis and reading; a refused root has no target.
   - A budget grant for another operation rejected.
   - Port validators.
   - Inventories and public-contract pins, additions only.
2. **`gate_daw_restore`.**
   - Each code, in order.
   - Upward and downward targets.
   - A start outside the range permitted, and a target outside it refused.
   - No-write on exact equality, without a charge.
   - Lineage: permitted while only the root or its restorations are unresolved; refused for any other unresolved command.
3. **Wire command and device v2.**
   - Parity of existing bytes.
   - `restore_gain` validation.
   - Device v2 on a fake host: each refusal with zero writes; exactly one write; an exact upward restoration; uncertain paths.
   - The frozen v1 script refuses `restore_gain` pre-write with zero host calls.
   - Import isolation: no `src` module imports `device/`; the protocol copy is byte-identical; the device's top-level imports are exactly the protocol module; loading it in ordinary Python with a fake host works and restores `sys.modules`.
4. **Journal v2.**
   - Migration: unchanged field contents for every outcome type; ledger order equal to rowid order; reopen idempotent; corrupt rows fail closed.
   - Ordering: a resolution refused when any reservation follows its evidence, and accepted when none does; an apply reserved between a reconciliation and its resolution makes the resolution stale.
   - *Amendment R:*
     - the reviewed A/R case: a child's `AT_RECORDED_START` cannot resolve its root (`EVIDENCE_NOT_FOR_SUBJECT`);
     - a confirmed restoration resolves its root as `RESTORED_TO_RECORDED_START`;
     - a newer `NEITHER` or `IDENTITY_UNVERIFIED` reconciliation blocks a resolution citing an older favourable one (`STALE_EVIDENCE`);
     - a newer restoration attempt supersedes an older reconciliation of the root.
   - Lineage: attempt numbering; a resolution of the root resolves only earlier restorations; a foreign root refused.
   - Agreement: a binding, profile, conversion or basis mismatch is refused.
   - Charges: one per reservation, none on replay, a conflicting authorization reuse refused, kept after an uncertain outcome, none for a pre-reservation refusal.
   - Append-only: a source scan shows no `UPDATE`/`DELETE` on `ledger` and exactly one guarded `UPDATE` on `commands`; row counts only grow.
   - A subprocess crash after a reserved and charged restoration: reopen shows it pending, unresolved and charged, and it is never resent.
   - Conversion blocks persist through restoration, reconciliation, resolution and reopen.
5. **Controller.** Real exchange, protocol and journal over a controlled byte transport.
   - Recovery of an unresolved apply via restoration then resolution.
   - Rollback of a confirmed apply.
   - An uncertain restoration, then reconciliation of it, then resolution.
   - Mismatch keeps the block.
   - A v1 script yields `UNSUPPORTED`, charged and still blocking.
   - Reconciliation appends and never clears.
   - Inspect clears nothing.
   - Replay with zero sends and no new charge; conflicts refused; poisoning on commit failure.
6. **ADR, documentation and evidence.**
   - **ADR-032 (Proposed):** the ledger and lineage; the recovery charge before dispatch; `restore_gain` and device v2 ownership and isolation; the exact-confirmation rule; conversion clearing unavailable.
   - **Other docs:** index row; overview; engineering-conventions layout line (`device/`); research log; this plan's outcome.
   - **Mutation probes**, each reverted byte-identically:
     - confirm a restoration within tolerance;
     - clear unresolved state on reconciliation;
     - clear a conversion block on restoration;
     - allow a restoration under another root;
     - skip the reservation-after-evidence check;
     - charge after send rather than before;
     - charge again on replay;
     - `UPDATE` a ledger row;
     - send `probe_gain` for recovery.
   - **Locked checks:** the focused and full suites, ruff, CLI help, `uv lock --check`, `git diff --check`, and the frozen research hashes rechecked.

## Remaining after this slice

- Installation and live tests of device v2 and runtime recovery: separately reviewed and authorized.
- A recalibration-evidence workflow that can define a validated conversion clearance.
- Export continuation and verified import.
- Coordinator integration: recovery budgeting from journal charges, resolution authority, gate inputs.
- D15 and adoption decisions.

## Outcome (2026-11-18, Claude Code; uncommitted for review)

**Implemented, tests first.** Each red run was recorded before its implementation:
- **Core:** `core/records/daw_recovery.py`, `core/ports/daw_recovery.py`, and `gate_daw_restore` in `core/admission/daw.py`.
- **Adapter:**
  - runtime `protocol.py` v2 (`restore_gain`, additive);
  - `serialization.py` (restore requests, grants, restore outcomes);
  - `journal.py` v2 (ledger, lineage, charges, reconciliation and resolution transitions, v1 migration);
  - the `controller.py` recovery methods.
- **Device:** `device/fl_studio/thesis_bridge_v2/`.
- **Tests:**
  - new files `tests/unit/test_daw_recovery_contract.py`, `test_fl_runtime_restore_protocol.py`, `test_fl_runtime_recovery_journal.py` and `test_fl_runtime_recovery_controller.py`, with support helpers `fl_device_support.py` and `fl_recovery_support.py`;
  - additive architecture inventories and public-contract pins.

**One existing test changed deliberately.** `test_fl_runtime_protocol.py::test_protocol_snapshot_is_exact` no longer asserts that the runtime protocol is byte-identical to the research snapshot, because `restore_gain` was added. It keeps the research hash pin and now asserts the bytes differ. Behavioural parity of every existing command and reply, plus the new snapshot hash, is checked in `test_fl_runtime_restore_protocol.py`.

**Decisions taken during implementation:**
- A restoration refused locally, before its reservation, is cached in memory only. Under the approved rule "a restoration other than confirmed is unresolved", journaling it would make a never-sent, never-charged refusal block. Its ID is therefore not reserved across a restart (ADR-032).
- Restoration refusals by the journal before reservation map to core refusal codes: `duplicate_id_conflict` → `DUPLICATE_ID_CONFLICT`; `unresolved_other` → `UNAVAILABLE`; `agreement`, `unknown_root` and `no_target` → `UNSUPPORTED`.
- Reconciliation records keep the observed identity and reading, so an identical replay reconstructs the same `DawReconciliation`.

**Mutation probes.** Eleven, each reverted byte-identically and caught by a permanent test:
1. confirm a restoration within tolerance;
2. clear unresolved state on reconciliation;
3. clear a conversion block after restoration;
4. allow a restoration under another root;
5. skip the reservation-after-evidence check;
6. transfer a child's finding to its parent;
7. skip the latest-evidence check;
8. no charge before dispatch;
9. re-reserve on an identical retry;
10. update a ledger row;
11. send `probe_gain` for recovery.

Probe 9 first **survived**, because the in-memory cache answered the retry. A regression now retries through a fresh controller on the same journal, which proves replay from the journal itself, with no send and no new charge.

**Locked checks:**

| Check | Result |
| --- | --- |
| focused suites (`-W error`) | 739 passed |
| full suite | 5,894 passed, 4 skipped |
| `ruff check .` | clean |
| `ruff format --check src tests device` | clean |
| `autonomous-mixing --help` | runs |
| `uv lock --check` | consistent |
| `git diff --check` | clean (line-ending notices only) |
| frozen research script hashes | unchanged |

**Not done:** installation, live calls, saves, exports, commits and pushes; coordinator integration; conversion clearing; export continuation.

## Review fixes (2026-11-18, after Codex review of the implementation)

1. **Newer child evidence did not invalidate older root evidence.** Freshness compared findings for the same subject only. An older root `AT_RECORDED_START`, followed by a newer `NEITHER` about a restoration of that root, still let the root resolve the whole lineage.
   - **Fix:** freshness is now judged across the whole lineage, meaning the root and every restoration of it: findings about any member, plus restoration outcomes.
   - **Regressions:** a newer child finding makes older root evidence stale, and a newer root finding makes older child evidence stale.
2. **Resolution authorization IDs escaped the shared namespace.** They lived only inside resolution payloads, so a new apply could take a consumed authorization's ID.
   - **Fix:** each consumed authorization is registered as a `RESOLUTION_AUTHORIZED` ledger entry.
   - **Regression:** after a resolution, that authorization's ID is refused for an apply, an inspection, a reconciliation, a recovery authorization and a recovery command.
3. **Missing reservations silently erased pending authority.** Any command without a reservation counted as a local refusal.
   - **Fix:** every command must carry exactly one marker, a `RESERVED` row or an explicit `LOCAL_REFUSAL` row (only for a refused apply recorded by the local refusal path). Every reserved restoration must carry exactly one `RECOVERY_CHARGED` row, and every marker, charge and consumed authorization must refer to an existing entry. Otherwise the journal fails closed on open.
   - **Regressions:** a pending command whose reservation was deleted; a local refusal whose marker was deleted; a restoration whose charge was deleted; a refusal marker added to a written command.

**Mutation probes.** Five, each reverted byte-identically and caught:
- freshness judged on the subject only;
- an unregistered authorization (with its consistency check also removed, caught by the namespace regression alone);
- a missing reservation read as a local refusal;
- a restoration without its charge accepted;
- an unknown marker kind.

**Locked checks after the fixes:**

| Check | Result |
| --- | --- |
| focused suites (`-W error`) | 746 passed |
| full suite | 5,901 passed, 4 skipped |
| ruff check and format | clean |
| CLI help | runs |
| lock | consistent |
| `git diff --check` | clean |
| runtime and research protocol hashes | unchanged (`68fb70dd…`, `03637001…`) |

