# ADR-040 — Operator CLI for the DAW path: start, continue, status and authorized recovery (2026-11-20)

- **Status:** Proposed, for owner and Codex review. It records the Increment 6 plan of the [coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md). *Implemented on 2026-11-20 with the owner's choice of H1; uncommitted for review. The first version was a proposal only.* **Operator tooling ready; autonomous runs unavailable.** `daw status` and the operator-authorized `daw recover` work. `daw start` and `daw continue` refuse before opening anything, because no `DecisionMaker` or `DiagnosisSource` implementation exists yet. No live FL Studio trial has been run.
- **Builds on:**
  - ADR-027: drivers compose adapters;
  - ADR-030 to 039, all Proposed.
- **Outside this ADR:**
  - saving, exporting and FL script installation, which stay manual;
  - live trials, which stay separately authorized;
  - any decision or diagnosis capability.

## Context

The reviewed DAW path exists as core functions and adapters, but the only CLI command is the offline `analyze`.

**No production implementation exists of:**
- `DecisionMaker` (`decision_agent` is planned);
- `DiagnosisSource`;
- `RunClock`;
- `IdentitySource`.

**Process-unique IDs are a driver obligation.** Increment 5 showed that FL inspection IDs must be unique per process.

## Decision (proposed)

1. **Commands.** `autonomous-mixing daw start | continue | status | recover`, sharing one validated configuration file (JSON schema v1, parsed into core records through their constructors) and fixed exit codes:

   | Code | Meaning |
   | --- | --- |
   | 0 | finished |
   | 1 | finished, failed |
   | 2 | usage or configuration error |
   | 3 | suspended |
   | 4 | refused |
   | 5 | interrupted, awaiting recovery |
2. **Composition.**
   - **One composition function** builds every adapter from the configuration: the durable run store per run, the FL journal and controller with the MIDI transport, the export handoff, the analyzer and the integrity inspector.
   - **Process-unique IDs:** a UUID identity source, and UUID FL inspection IDs.
   - **Clock:** a system clock whose cancellation is set by Ctrl+C.
   - **Only implemented adapters are wired** (H1). `start` and `continue` refuse until a decision maker and a diagnosis source exist. Tests drive the same composition function with labelled doubles and a simulated FL host; no test switch exists in configuration or `main`.
3. **`start` and `continue`** keep every reviewed guarantee:
   - the run-start check;
   - store ownership (busy is refused);
   - classification after a restart, where only a recorded suspension continues;
   - the Increment 3 admission (duplicates, conflicting manifests, request substitution);
   - budgets, elapsed segments and the committed acceptance as the authority.

   **The manifest** is built only from the awaiting request's declared output names, with a required operator attestation, and the verified import still decides.

   **On a first FL session**, the operator must repeat the inspected fader value exactly to declare the starting state.
4. **`status` is read-only by default.**
   - **Stores:** both are opened read-only (no lock, no write).
   - **DAW:** no MIDI transport is created.
   - **Recording:** only `--record-classification` records the classification, and it requires ownership.
5. **`recover` is two-phase and explicitly authorized.**
   - **Phase 1** is read-only and shows the interrupted state, the FL facts, the proposed target (journaled restore target against saved target), the permitted operations and a state fingerprint.
   - **Phase 2** requires that fingerprint, an operation and an operator identity. It records the authorization in the trace, then performs exactly one operation through the existing contracts.
   - **Operations:**
     - reconcile;
     - exact restore with `own_lineage_only`;
     - resolve, where the evidence permits;
     - inspect-integrity;
     - close, which records facts only.
   - **Never:** adoption of unexplained fader state, automatic restoration, clearing a conversion block, or resubmitting an acceptance.
   - **`--orphans`** covers FL commands that no run explains.
6. **Charges.** Operator restorations are charged as `RECOVERY_OPERATION` and recorded, but are not limited by an exhausted run budget, because the operator's explicit authorization is the limit (H6, for review).

## Rationale

- **No stand-in for missing capabilities.** A driver that wires only implemented adapters keeps ADR-006 (no temporary features).
- **Authority over recovery.** Showing the exact state and target before any authorization, and binding the authorization to a fingerprint of what was shown, keeps recovery decisions with the operator. Each decision becomes one auditable trace entry.
- **Read-only status** can be run at any time, including while another process owns the run.

## Consequences

- **Live runs stay blocked** until a decision maker and a diagnosis source are implemented (roadmap). Status and recovery are usable as soon as this increment lands.
- **Two adapter additions:** read-only opens of both SQLite stores.
- **Decisions H1–H9 and open questions Q1–Q3** are listed in the plan for review.

## Sources

- [Coordinator-integration plan](../superpowers/plans/2026-11-18-daw-coordinator-integration.md): the Increment 6 plan.
- [ADR-038](0038-coordinator-daw-recovery-routing.md) and [ADR-039](0039-durable-run-journal-and-restart.md).

## Implementation notes (2026-11-20)

**Owner decision (H1).** Wire existing adapters and refuse unavailable capabilities explicitly:
- the production clock and the unique ID source are implemented;
- `status` and the operator-authorized `recover` are functional;
- `start` and `continue` refuse before opening any store or MIDI connection, naming the missing `DecisionMaker` and `DiagnosisSource`;
- labelled doubles stay in tests, and no decision-file adapter is added.

**What exists.**
- **Runtime** (`drivers.daw_runtime`):
  - `SystemClock`, whose cancellation is set by Ctrl+C through `interrupt_cancels`;
  - `UuidIdentitySource`, giving 32-hex tokens unique across processes;
  - `unique_inspect_id`;
  - the configuration loader (`load_config`, JSON schema 1);
  - `open_fl`.
- **Commands** (`drivers.daw_commands`): `status`, `recover_view` (phase 1) and `recover_apply` (phase 2), with a SHA-256 fingerprint over the canonical encoding of the shown view.
- **Core:**
  - `core.loop.operator_recovery` (`run_view`, `orphan_view`, `orphan_facts`, `perform`);
  - `operator_recovery_options` in `core.admission.daw`;
  - the records `OperatorOperation`, `OperatorRecoveryOption`, `OperatorRecoveryView` and `OperatorRecoveryAuthorization` (a trace payload).
- **Adapters:** read-only opens of `SqliteRunJournal` and `SqliteCommandJournal` (SQLite `mode=ro`, no lock, no write).

**Deviations from the plan, each pinned by a test.**
- **Configuration scope.** It covers what operator tooling uses: stores, FL journal, profile reference, MIDI ports, roots and read limits. The run-configuration schema (criteria, measurements, budgets), the export manifest built by `continue`, and `start --accept-current-fader` are not built, because `start` and `continue` are unavailable.
- **Exit codes.**

  | Command | Codes |
  | --- | --- |
  | `start`, `continue` | 2 |
  | `status` | 0, or 4 when a store is in use |
  | `recover` phase 1 | 0 |
  | `recover` phase 2 | 0, or 4 when refused (stale fingerprint, unshown option, busy store) |
  | configuration errors | 2 |

  Exit codes 1, 3 and 5 belong to the unavailable run commands.
- **`close`.** It always records the final state as `UNKNOWN`, with verified `None`. Upgrading from recorded evidence is not implemented; the closing never claims more than that.
- **Accepted edits are never undone** *(review fix 2026-11-20; the first implementation offered and performed a restoration of a root whose execution a committed acceptance held, reverting the accepted fader)*. The options rule excludes every execution that a committed acceptance holds from restoration, and `perform` refuses such a restoration again at execution.
- **Orphan discovery** *(review fix 2026-11-20; the first implementation found roots only through unresolved commands, so confirmed and off-target orphans were invisible)*.
  - **What it lists:** every journaled apply root that may have written (pending, uncertain, confirmed or off target), through the new read-only port method `apply_roots`, minus every root that a run store in the directory records.
  - **A run's root, accepted or not, is never an orphan.**
  - **If any run store cannot be read,** orphan analysis is refused (fail closed).
  - **Restoration** is offered only where the lineage is the latest and has no open restoration.
- **`--orphans`.** Resolution is offered only through a root's own confirmed restoration. A reconciliation finding of an orphan is journaled only in the FL journal, and its value is not shown to the options rule, so the rule offers reconcile and restore only.
- **Operator restorations** use `own_lineage_only`, and are charged and recorded as `RECOVERY_OPERATION` without being limited by the run's budget (H6).

**Evidence.**
- **Tests:** 20 CLI tests (15 before the review fixes) over stores produced by real crashed child processes and the simulated FL host. They cover:
  - clock and IDs, across processes;
  - configuration validation;
  - start and continue refusing with nothing opened or created;
  - read-only status and phase 1: byte-identical stores, read-only opens only, no MIDI;
  - "in use";
  - phase 2: restore then close, stale fingerprint and unshown option refused, reconcile then resolve, orphans;
  - the driver import boundary.
- **Mutation probes:** 15 new (10, plus 5 for the review fixes), all caught; the Increment 5 set (15) is still caught.
- **Not live FL evidence.**
