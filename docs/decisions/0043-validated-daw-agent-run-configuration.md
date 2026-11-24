# ADR-043 — Validated run configuration for the DAW agent runtime (2026-11-24)

- **Status:** Proposed. R5C increment C1 implementation reviewed for the 2026-11-24 checkpoint; C2–C4 remain unimplemented.
- **Source:** the owner approved C1 of the [R5C plan](../superpowers/plans/2026-11-24-r5c-operator-cli.md), including its three review corrections.

## Decision

1. **A separate run file.** The tool configuration (schema 1) is unchanged and still serves `status`/`recover`.
   - `load_run_config` reads a second file: a JSON envelope with schema 1 and kind `autonomous_mixing.daw_agent_run`.
   - The envelope is allowlisted at every plain level, with no optional fields and no defaults.
   - Complex values are existing `core.records` records in the exact codec format, decoded through their constructors. Each is type-checked by key path.
2. **Derived values cannot be declared.** The envelope has no key for any of these, so declaring one is an "unknown key" error:
   - the analysis specification and its prepared digest;
   - the diagnosis jobs and their metric IDs;
   - the DAW control profile, taken from the tool configuration's calibrated `profile_reference`;
   - the provider binding;
   - the read limits;
   - the output root key, which equals `daw_session.export_root_key`;
   - the qualification, always `UNCALIBRATED`;
   - the preparation state and roots, which equal the run's.

   Declared measurement jobs omit their analysis specification. `measurements` name them by a local label that is never recorded. Roots come only from the tool configuration.
3. **Two-phase request construction, which removes the digest cycle.**
   - **Before any write:** `provisional_request` builds the complete `RunRequest` with a provisional specification (`autonomous_mixing.daw_agent_analysis@1+unprepared`). Construction runs every existing structural invariant; the provisional request is discarded and never recorded.
   - **Pre-write check:** `DawAgentRuntime.check` runs the existing preflight rules on the provisional request, before any directory exists. These rules cover the calibrated profile, export root, analyzers, native baseline export metadata and baseline encoding.
   - **After preparation:** `request_factory` supplies `create` with the real prepared specification. It appends the diagnosis definitions as analysis jobs only, never as conditions, and copies no diagnosis threshold into the criteria. `create`'s own checks still apply.
4. **Strict, bounded reading.** Every refusal is a `ConfigError` raised before anything is created. These bounds are resource limits, not scientific thresholds:
   - the file must be a regular file, not a link or reparse point, and its identity is checked after opening;
   - at most 4 MiB, enforced by a bounded read;
   - strict UTF-8 without a byte-order mark;
   - nesting depth at most 64, checked by a linear pre-scan (the example's depth is 14);
   - no duplicate keys;
   - no `NaN`/`Infinity` and no overflowing floats;
   - integers of at most 20 digits, refused before conversion.
5. **Scope choices.**
   - **Level-only acceptance:** declared verification and guard jobs must use the level analyzer. This is a scope choice for the first CLI version, **not** a scientific requirement. An accepted level-criteria run is not evidence of reduced masking or perceptual improvement. Criteria on the experimental P1 metrics remain an open owner decision.
   - **Reviewed experimental policy only:** the run file must repeat the reviewed policy exactly (`p1.experimental_screen@1`, 40 / 0.02 / 0.02 / 100 / 0.25 / 4). It is still explicit and recorded, but changing it needs a reviewed code change.
6. **Evaluation and hidden inputs.** Any envelope key containing `reference`, `hidden`, `evaluation`, `ground_truth`, `clean`, `target_mix` or `injected` is refused by name. The codec cannot name anything outside `core.records`. Free text in `context` is public provider-visible content and is not sanitized.
7. **Typed state refusals.** `RuntimeRefusal(ValueError)` marks state refusals: existing artifacts, drift from the recorded run, an unresumable run and unverifiable stored evidence. A later CLI increment maps it to exit 4. It stays a `ValueError`, so existing callers are unaffected.
8. **Preflight profile source.** `_preflight` takes the P1 ingress profile explicitly. `create` now requires the prepared plan's profile to equal the preparation request's profile.

## Rationale

The run file reuses the exact codec and constructors rather than adding a parallel configuration language or placeholders. Leaving derived values out of the envelope makes them impossible to misdeclare. The provisional request satisfies "invalid configuration refused before writes" without inventing a digest. Strict parsing closes ambiguities that `json.loads` would accept silently.

## Consequences

- `daw start` and `daw continue` still refuse. CLI activation, first-session fader confirmation, manual export output, manifest construction and the provider-run status additions are later increments C2–C4.
- The run file is verbose, because identifiers are codec records.
- The tool-configuration loader keeps its existing lenient JSON reading (duplicate keys: last wins). Adopting the strict reader there is an open question, because it would change existing behaviour.
- File checks are not race-free against a hostile local process.
- No threshold, requirement status, store schema or accepted ADR changes.

## Supersedes

None. Extends ADR-042 (Proposed) and refines the run-command part of ADR-040 (Proposed) without editing either.

## Review correction (2026-11-24)

- **Manifest failures.** On reopen, expected stored-preparation-manifest failures (missing, altered, unreadable, or decoding to another plan) are `RuntimeRefusal`. They were previously `PreparationStoreError` or a raw `OSError`. The manifest is never regenerated.
- **Read errors.** `OSError` raised while reading a run file becomes `ConfigError`, and the descriptor is still closed. Other exceptions propagate unchanged.
