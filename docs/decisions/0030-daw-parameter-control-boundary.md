# ADR-030 — DAW parameter-control boundary beside the offline renderer (2026-11-18)
- **Status:** Proposed, for owner and Codex review. Not adopted: runtime adoption needs owner approval of the requirement and scope proposal below, and the later plans (FL transport adapter, recovery, export continuation, coordinator integration). ADR-001, ADR-009 and ADR-027 are unchanged, and FR-14 stays withdrawn.

## Decision

1. **A separate core port.** DAW parameter control is the port `core.ports.daw.DawParameterController`, with `inspect` and `apply`. It is not a `Renderer` or `RunRenderer`, and its outcomes never carry candidate revisions, artifacts or an `ExecutionStatus`.
2. **Four distinct outcomes, all fader-only:**
   - `DawWriteRefused`: refused before writing; nothing was written;
   - `DawWriteConfirmed`: the parameter reads as commanded and the dB change is within tolerance;
   - `DawWriteOffTarget`: a known write that missed the requested dB;
   - `DawWriteUncertain`: the effect is not established.

   A confirmation certifies only the reported fader reading, not routing, plugins, project state or audio. Candidate audio for a DAW change can come only from a later, separately verified export import.
3. **A pure gate in admission, before dispatch.** `core.admission.daw.gate_daw_apply` takes an already **admitted** action (it never replaces `admit_action`) and permits a write only inside a declared, calibrated `DawControlProfile`. The profile covers:
   - the tested build, project metadata title, insert and conversion;
   - both coordinate ranges;
   - the cut limits and tolerances.

   It also refuses while an earlier operation is unresolved, and under a conversion blocked by an off-target write. A change of exactly 0 dB is `DawNoWrite`; increases are unsupported. `daw_gain_bounds` derives the advertised range from the confirmed state, after validating that the permission and profile belong to the bound insert.
4. **Self-validating requests.** `DawApplyRequest` validates its references, the matching inspection and profile, drift, the permitted cut and both ranges, using the same records-level checks as the gate. Only run-state checks (unresolved operation, conversion block) remain the gate's and controller's.
5. **The calibration curve stays in the adapter.**
   - The profile declares the fader-dB range that the adapter derives from its versioned conversion at full precision.
   - The commanded normalized position is an adapter claim: the adapter recomputes and checks it before dispatch.
   - Core checks the declared ranges and the read-back's structural consistency only.
6. **The reviewed bridge's rules are port obligations:**
   - pending intent recorded before dispatch;
   - an identical retry with the same command ID never writes again and returns the recorded outcome: a written outcome (confirmed or off-target) with `replayed` set to true, a refused or uncertain outcome unchanged (those records have no `replayed` field);
   - a different request under that ID is refused (`DUPLICATE_ID_CONFLICT`);
   - every unknown after dispatch is uncertain.

   Duplicate history may be lost on reload, so exactly-once execution is not claimed.
7. **Adapters own I/O.** A future FL adapter owns MIDI, the protocol, the conversion, logs and its profile configuration, and lives under `adapters/`. Runtime code never imports `scripts/fl_studio/`; those research scripts stay reproducible.

## Requirement and scope proposal (not adopted; owner decision D15)

- **Proposed new functional requirement** (ID assigned at approval): *The system can apply an admitted gain action to one owner-confirmed DAW insert through a calibrated parameter-control adapter, report pre-write refusal, confirmed, off-target and uncertain outcomes, and never treat a parameter change as verified audio.*
- **Proposed scope wording:**
  - In scope, optional: live parameter control of one calibrated FL Studio insert through its scripting API, for operator-attended trials.
  - Unchanged: the exclusion of `.flp` manipulation, and the withdrawal of FR-14.
- **Until approved:** the requirement tables, the scope and the exclusions stay as they are. The contract exists as preparatory, unintegrated code.

## Source

- [FL Studio action adapter design](../superpowers/specs/2026-11-18-fl-studio-action-adapter-design.md), increment 1;
- [implementation plan](../superpowers/plans/2026-11-18-daw-control-contract.md), approved with five amendments;
- [bridge contract](../research/fl-studio-bridge-contract.md);
- [dB conversion design](../research/fl-studio-db-conversion-design.md) §7–9: calibration accepted for normalized 0.62–0.86 on one build, project title and insert; live −1 and −2 dB cuts measured −0.999595 and −1.999212 dB.

## Rationale

- **Why not the existing contracts.** The offline contracts describe gain on a fixed input stem, and a synchronously rendered, linearly summed mix. A DAW fader write followed by a pending human export does not satisfy them. Reusing them would either fabricate `CANDIDATE_READY` evidence or weaken their meaning.
- **Why a gate.** A separate port with fader-only outcomes keeps both paths truthful. A pure gate keeps the tested, calibrated scope enforceable in core without putting I/O or the curve there.

## Consequences

- **Two execution paths with distinct evidence:** offline rendering (audio candidates) and DAW control (parameter readings only).
- **New inventories:** `core.records.daw_control`, `core.ports.daw` and the `admission.daw` gate appear in the architecture inventories and the public-contract baseline. Adapter inventories are unchanged; no adapter exists yet.
- **Identity:** it rests on the metadata title, the insert name and operator confirmation. It does not authenticate the open file.
- **Missing pieces:** exact-target recovery, pending-export continuation and coordinator integration (including where unresolved-operation and conversion-block state lives).
- **Evidence still owed by the real adapter:** the simulated-controller tests demonstrate the intended contract only. The real adapter must repeat the pending, replay, conflict, timeout and reload checks.
