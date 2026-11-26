# ADR-044 — Explicit selection between two calibrated DAW profiles (2026-11-26)

- **Status:** Proposed. Implemented tests-first and uncommitted for owner review; nothing is installed or run live.
- **Source:** the owner approved D1–D6 of the [HT01 production-profile plan](../superpowers/plans/2026-11-26-ht01-production-profile-plan.md) on 2026-11-26, with three amendments (section 10 of that plan).
- **Relation:** extends [ADR-030](0030-daw-parameter-control-boundary.md), [ADR-032](0032-daw-exact-recovery-and-reconciliation.md) and [ADR-043](0043-validated-daw-agent-run-configuration.md) (all Proposed) without editing them. ADR-043's "the DAW control profile, taken from the tool configuration's calibrated `profile_reference`" now means the profile selected as described here.

## Context

`calibrated_profile(reference=…)` declared exactly one scope (`Thesis Bass Bridge Test`, insert 2, normalized 0.62–0.86) and ignored the reference name. The live calibration `ht01_live_20261126_01` (plan `ht01-mixer-cal-v1`, verdict `H_accepted`, re-evaluated independently) supports the same curve H for `HT01 Live Calibration v001`, insert 5 `Bass`, build 5654 / API 45, normalized 0.78–0.92. The R6 trial needs that scope without relabelling or widening the existing one.

## Decision

1. **Exactly two literal profiles, no registry.**
   - `calibrated_profile` (unchanged) and `ht01_calibrated_profile` are the only declarations.
   - `supported_profile(name, reference)` accepts exactly `bass-bridge-test-v1` and `ht01-live-calibration-v001`.
   - A profile is supported only if it equals one of the two; the reference is a label.
   - Configurable or derived profiles do not exist.
2. **A separate conversion per calibration.**
   - HT01 uses wire conversion `fl-mixer-h125-ht01-v1` and profile conversion `fl-mixer-h125-ht01`/`v1`, following the protocol's rule that a new calibration gets a new ID.
   - The conversion is derived from the command's exact title and insert, never chosen by a caller.
   - An off-target write blocks only its own conversion.
3. **Exact HT01 bounds.** HT01 ranges compare exactly in protocol, device and core admission. The existing scope keeps its 1e-9 rounding. Staging points 0.76 and 0.94 and the calibration start grant nothing.
4. **HT01 pins API 45 at the device.**
   - The device refuses an HT01 `set_gain_db` or `restore_gain` on another API (`calibration_api`, mapped to `UNCALIBRATED_BUILD`) before the setter.
   - The existing scope's device checks are unchanged.
5. **Explicit selection in the tool configuration.**
   - Schema 2 requires `daw_profile` (one of the two names).
   - Schema 1 is still accepted, refuses `daw_profile`, and always means the existing profile.
   - Unknown keys stay refused by name.
6. **Enforced FL-journal scope.**
   - When a controller is constructed, before any transport exists, a journal whose recorded apply or restore requests carry a profile with another scope, conversion, range, limit or tolerance is refused (`JournalRefusal("incompatible_scope")`).
   - Reference labels are ignored, empty journals are compatible, and existing journals keep their meaning.
7. **Stored-run compatibility.**
   - `create`/`check` already require the run's DAW profile to equal the selected one.
   - Reopening a stored run now also refuses a recorded DAW profile that differs from the selection, before any provider or MIDI transport is constructed.
   - Recorded runtime settings are unchanged, so existing stored runs do not drift.

## Consequences

- The runtime/device protocol snapshot changes (new SHA-256). Wire version 1 and the existing scope's command bytes are unchanged.
- An older installed device fails closed:
  - an HT01 `set_gain_db` is answered `invalid_command`/`conversion`;
  - an HT01 `restore_gain` is answered `conversion_scope`.
- Installing the new device pair, a reload and a fresh read-only inspect of the HT01 copy are separate, authorized steps.
- A start at 0.78 is range-eligible but admits no cut. This is accepted and documented rather than widened.
- Profile selection is a declaration, not proof that the open project, routing or audio match the calibration (unchanged limitation).
- The hidden reference and injected defects never enter the profile, conversion or configuration. Baseline export preparation and agent-run criteria remain separate work.
