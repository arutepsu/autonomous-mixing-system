# HT01 production-profile extension — implementation plan (2026-11-26)

Status: **Planned**; owner approved D1–D5 and D6 (with an enforced check) on 2026-11-26, with the amendments in section 10. Implementation proceeds tests-first and stays uncommitted for review. The original text below is kept; section 10 supersedes it where they differ. No installation, MIDI, provider call, export, commit or live write is part of this plan. Baseline export preparation and agent-run criteria are the following task.

Goal: enable calibrated `set_gain_db` and exact recovery (`restore_gain`) for exactly one additional scope, through the existing protocol, device script, profile, controller and runtime configuration. The existing calibrated scope (`Thesis Bass Bridge Test`, insert 2, 0.62–0.86) keeps its behaviour. The frozen research scripts under `scripts/fl_studio/` stay byte-identical.

## 1. Independent re-evaluation of the live evidence

Inputs, outside the repository, in `C:/uni/Bachelor-Thesis/test/HT01-live-calibration-v001/`:

| File | SHA-256 |
|---|---|
| `calibration-live-20261126-01.jsonl` | `8c6280c9504a514728b2d80471aac113c52cce0100b66e9d1ee6fb18c408bb84` (78 records) |
| `calibration-live-20261126-01-evaluation.json` | `8e13e2a3c4c941c3d8e76426f824cd10b2be6e3ad5e4d09271711b8018701e72` |

Two independent checks:
- The committed evaluator (`python -m autonomous_mixing.drivers.ht01_calibration evaluate`, opens no MIDI) returned `H_accepted`, exit 0.
- A separate recomputation was written for this review. It uses only `json`/`math` and none of the repository's evaluator code.

The recomputation confirmed:
- **Structure:** 1 header, 39 exchanges, 37 pending records. Each pending command equals its exchange's command, in order.
- **Replies:** all 36 probes and the restore have `replied` / `applied` replies, with their own command ID and `replayed: false`. Every reply reports title `HT01 Live Calibration v001`, insert 5 `Bass`, `Producer Edition v26.1.7 [build 5654]`, API 45.
- **Chain and sequence:** every `before` and `expected` equals the previous `after`. Steps 0–35 carry exactly the pinned sequence targets.
- **Stored targets:** maximum |after − target| normalized is 4.1e-10 (criterion 1e-6).
- **Curve at the primaries:** maximum |H(after) − after dB| is 1.873e-6 dB (criterion 0.01 dB). The worst point is 0.91 (−1.87e-6); per-primary errors range from −1.87e-6 to +2.6e-7 dB.
- **Repeatability:** the two visits of every primary read identical dB (difference 0, criterion 1e-6).
- **Staging points (descriptive only):** 0.76 at −1.0e-6 dB and 0.94 at +8.0e-7 dB. They do not widen the range.
- **Restoration:** the restore's after-reading, the final inspect and the initial inspect are all exactly 0.9056249996647239 / 2.982499599456787. The terminal record is `completed`, `restored_exact: true`.
- **Installed code:** the installed device pair (`f03cf509…`, `e4b8cf81…`) matches the current repository files.

Conclusion: the evidence supports `fl-mixer-h125` on this scope for normalized [0.78, 0.92], which spans −0.5745 to +3.3827 dB.

Limits that stay explicit:
- One session and one run.
- Time drift and perceptual effects are not estimated.
- Values between primary points rest on the hypothesis H, as for the existing scope.
- The script console was not captured.
- Project-title metadata does not authenticate the open file.
- The device script installed for production will differ from the one used during calibration, so a fresh inspect is required after installation (§6).

## 2. Accepted scope (exact; nothing else)

| Field | Value |
|---|---|
| Project title | `HT01 Live Calibration v001` |
| Insert | 5, `Bass` |
| Build / API | `Producer Edition v26.1.7 [build 5654]` / 45 |
| Conversion | H (`fl-mixer-h125`), calibrated by plan `ht01-mixer-cal-v1`, run `ht01_live_20261126_01` |
| Normalized range (start and target) | [0.78, 0.92], inclusive at the existing 1e-9 rounding |
| Cut limits | 0.1–3.0 dB (existing `DB_CUT_RANGE`, unchanged) |
| Start/success tolerances | existing values (0.001 normalized, 0.05 dB start; 0.05 dB success; 0.001 read-back), unchanged |

Staging points 0.76 and 0.94, the calibration start `HT01_START`, and the calibration-only probe plan grant no production permission. `HT01_START` is a calibration precondition, not a runtime or mix target. Production restore targets come only from journaled readings, as today.

## 3. Design

### 3.1 Protocol (`adapters/fl_studio/protocol.py` plus its byte-identical device copy)

- Add one fixed HT01 conversion declaration next to the existing one, without changing the existing constants (`CONVERSION_ID`, `CONVERSION_RANGE`, `CONVERSION_SCOPE`): an HT01 conversion ID, range = `HT01_RANGE`, scope = HT01 title, insert, build and API, and plan = `HT01_PLAN_ID`.
- Add one closed, literal two-entry mapping from conversion ID to (scope, range). It is a private constant tested to contain exactly these two entries, not a registry.
- **Lookup by scope, not caller choice.** `set_gain_db_command` derives the conversion ID from the exact (title, insert) of the command, so callers cannot choose a conversion. An unknown scope makes the command invalid, as it is today.
  - `_validate_db` checks: the conversion ID is known; title and insert equal that conversion's scope; the start and target lie in that conversion's range; the start is on the curve. The target-agreement check is unchanged.
  - `_validate_restore` checks: title and insert equal one of the two scopes; the target lies in that scope's range and on the curve. The target ≠ expected check is unchanged.
- Keep the module-level `in_conversion_range(v)` behaviour for the existing scope. Add a scope-taking variant used by the new paths.
- Existing scope: command bytes, validation and error codes stay identical.
- Calibration-only probe plans (`fl-mixer-cal-v1`, `ht01-mixer-cal-v1`) stay as they are, separate from production writes.

### 3.2 Device script (`device/fl_studio/thesis_bridge_v2/device_thesis_bridge.py`)

- **Build check:** `set_gain_db`/`restore_gain` currently compare the build against `CONVERSION_SCOPE["fl_version"]`. They will compare it against the build of the conversion selected by the validated command's scope. The value is identical for both scopes, but it is now derived rather than shared.
- **API check:** for the HT01 conversion only, also require the scope's exact API (45) before writing, matching the calibration evidence. The existing scope keeps its current checks; see decision D3.
- **Start-range check:** `in_conversion_range(before)` becomes the selected scope's range.
- No change to the uncertain boundary, duplicate history, replay, identity checks or calibration probe handling.

### 3.3 Profile (`adapters/fl_studio/profile.py`)

- Keep `calibrated_profile(*, reference)` unchanged for the existing scope.
- Add `ht01_calibrated_profile(*, reference)` with:
  - build `DawBuild("Producer Edition v26.1.7 [build 5654]", 45)`;
  - title `HT01 Live Calibration v001`, insert 5 / `Bass`;
  - the HT01 conversion VersionRef (D2);
  - `DawRange(0.78, 0.92)` and `DawRange(h_db(0.78), h_db(0.92))` at full precision;
  - the same cut limits and tolerances as the existing profile.
- Add one explicit selector over exactly two literal names (D1): `supported_profile(name, *, reference)` returns one of the two or raises `ValueError`. `is_supported_profile(profile)` is true only if the profile equals one of the two built with its own reference.
- `make_apply_command` replaces its existing-scope equality check with `is_supported_profile`. The wire target is still recomputed from the observed fader; caller declarations never widen support.

### 3.4 Controller (`adapters/fl_studio/controller.py`)

- **Construction:** `profile != calibrated_profile(...)` becomes `not is_supported_profile(profile)`. One controller still serves exactly one profile, and requests with another profile keep their existing `UNSUPPORTED` refusals.
- **Reply mapping:** the build/API comparison against `self.profile.build` already covers HT01.
- **Blocking:** an off-target write blocks the profile's conversion. With a separate HT01 conversion ID (D2), it blocks only HT01.
- Restore, reconciliation, anchors, snapshots and resolution are unchanged; they already use `self.profile`.

### 3.5 Drivers and runtime configuration (`drivers/daw_runtime.py`, `drivers/agent_runtime.py`)

- **Tool configuration, schema 2 (D1):** adds one required key, `daw_profile`, from the closed set `{"bass-bridge-test-v1", "ht01-live-calibration-v001"}`. `profile_reference` stays as the record label.
- **Schema 1** stays accepted and always means the existing profile, so existing configs, tests and stored runs are unaffected. Unknown keys and unknown profile names are refused by name.
- **Selection:** both `calibrated_profile(reference=config.profile_reference)` call sites in `daw_runtime.py` and the `_preflight` check in `agent_runtime.py` use `supported_profile(config.daw_profile, reference=config.profile_reference)`.
- **Continuation and status:** a run started under one profile cannot continue under a tool configuration that selects the other. The existing equality in `_preflight` and the journal lineage scope already refuse this; tests make it explicit.
- No runtime-config field may carry hidden-reference or injected-defect data. The existing unknown-key refusal stays, and a test asserts the new schema adds only `daw_profile`.

## 4. Compatibility and versioning

| Item | Change |
|---|---|
| Wire protocol `v` | stays 1. Additive: HT01 `set_gain_db` carries the new conversion ID, and existing command bytes are unchanged. |
| Runtime/device protocol snapshot | new SHA-256, repinned in `test_fl_runtime_restore_protocol.py`. Old-scope command/reply parity corpus stays pinned. |
| Older installed device (`e4b8cf81…` protocol) | fails closed. An HT01 `set_gain_db` with the new conversion ID is answered `invalid_command`/`conversion` (mapped to the existing refusal); `restore_gain` gets `conversion_scope` (`UNSUPPORTED`). There is no handshake; installation verification is by hash (§6). |
| DAW conversion VersionRef | new `fl-mixer-h125-ht01`/`v1` (D2); the existing `fl-mixer-h125`/`v1` is unchanged. |
| Tool configuration | schema 2 adds `daw_profile`; schema 1 means the existing profile. |
| Run store / FL journal | unchanged serialization. Stored profiles keep their full fields; a stored run under one profile is refused under the other. |
| Frozen research files | `scripts/fl_studio/` unchanged; existing byte-identity tests remain. |

## 5. Tests first (all simulated; real device handler with a fake host)

Where a test exists for the existing scope, parametrize it over both scopes rather than duplicating it. The fake host gains an HT01 configuration (title, insert 5 named `Bass`, curve H). Each group below must fail before implementation.

1. **Scope, build and API mismatches** (protocol and device), for HT01 `set_gain_db` and `restore_gain`:
   - wrong title, insert index, insert name, build, or API (44 / 46);
   - the existing scope's title used with the HT01 conversion and the reverse;
   - an HT01 command carrying the existing conversion ID;
   - unknown conversion ID.

   Each is refused before any setter call (`host.writes` empty).
2. **Range boundaries:**
   - start and target exactly 0.78 and 0.92 accepted;
   - 0.78 − 1e-6 and 0.92 + 1e-6 refused;
   - staging values 0.76 and 0.94 refused as start or target;
   - a cut landing below 0.78 refused (`range`);
   - existing-scope values (for example 0.70) refused for HT01, and HT01 values (for example 0.90) refused for the existing scope.
3. **Conversion agreement:**
   - the client target equals the device recomputation within `TARGET_AGREEMENT`, and a perturbed target is refused (`conversion_mismatch`);
   - a start off the curve is refused (`uncalibrated_start`);
   - cut limits −0.1 and −3.0 accepted, −0.09 and −3.01 refused;
   - core admission (`gate_daw_apply`) refuses out-of-profile changes before the adapter.
4. **Exact recovery:**
   - `restore_gain` back to a journaled HT01 reading lands exactly (both coordinates) through the controller;
   - targets outside [0.78, 0.92] or off the curve are refused;
   - restore from an arbitrary current reading stays allowed, as today;
   - reconciliation and continuity snapshots work for the HT01 profile.
5. **Replay and conflict:**
   - an identical command ID and payload replays the recorded reply with no second setter call;
   - the same ID with a different payload is refused as `duplicate_id_conflict`;
   - controller and journal replay for HT01 apply and restore.
6. **Uncertain outcomes:**
   - a setter exception or read-back disagreement gives `uncertain`, an unresolved journal entry, and further applies blocked;
   - an off-target write blocks only the HT01 conversion and leaves the existing one unblocked (D2);
   - a lost reply gives no automatic retry.
7. **Profile selection and configuration:**
   - `supported_profile` accepts exactly two names and refuses others;
   - `is_supported_profile` refuses a modified HT01 profile (each field changed);
   - schema 1 configs still select the existing profile;
   - schema 2 requires `daw_profile` and refuses unknown values, extra keys and hidden-data keys;
   - a run started under one profile is refused for continuation under the other;
   - `make_apply_command` refuses a widened HT01 profile.
8. **Existing-scope regressions:**
   - the existing command/reply parity corpus is unchanged;
   - existing-scope controller, CLI and recovery suites pass unchanged;
   - frozen-file tests pass;
   - the calibration-only HT01 probe plan, digest and tests are unchanged;
   - HT01 probes and production writes do not share device history state.
9. **Architecture:** no new module or import edge. The `tests/architecture` inventory is unchanged except for the repinned snapshot.

Verification: run sequential mutation probes on the new checks (scope lookup, range, API, selector, profile equality, schema-2 key), each followed by a hash-verified restore. Then format and lint `src`, `tests` and `device`, and run the clean full suite alone.

## 6. Gates after implementation (each separately authorized)

1. Owner code review of the implementation.
2. Installation of the new v2 device pair, with backup and hashes recorded, followed by a script reload and a fresh read-only inspect of the HT01 copy that matches the expected identity. No write.
3. Only then the following task: baseline export preparation and agent-run criteria. The hidden reference and the known injection never become runtime targets.

## 7. Affected files

- `src/autonomous_mixing/adapters/fl_studio/protocol.py` and `device/fl_studio/thesis_bridge_v2/thesis_bridge_protocol.py` (byte-identical copy)
- `device/fl_studio/thesis_bridge_v2/device_thesis_bridge.py`
- `src/autonomous_mixing/adapters/fl_studio/profile.py`
- `src/autonomous_mixing/adapters/fl_studio/controller.py` (profile-support check only)
- `src/autonomous_mixing/drivers/daw_runtime.py` (schema 2, selection) and `src/autonomous_mixing/drivers/agent_runtime.py` (preflight selection)
- Tests:
  - updated: `tests/unit/fl_device_support.py`, `tests/unit/fl_runtime_support.py`, `test_fl_runtime_protocol.py`, `test_fl_runtime_restore_protocol.py` (repin), `test_fl_runtime_controller.py`, `test_fl_runtime_recovery_controller.py`, `test_daw_cli*.py`, `test_agent_runtime.py`, `test_daw_control_contract.py`;
  - new: `tests/unit/test_fl_ht01_profile.py` for selection, boundaries and isolation.
- Docs:
  - `docs/research/fl-studio-bridge-contract.md` (new section);
  - this plan;
  - `docs/research/research-log.md` (append);
  - a new ADR if D5 is accepted;
  - `docs/superpowers/plans/2026-11-24-r6-readiness-and-trial-plan.md`, only once the change exists.

## 8. Unresolved decisions (owner)

| ID | Decision | Recommendation |
|---|---|---|
| D1 | How the profile is selected | Tool-config schema 2 with a required closed `daw_profile` key; schema 1 keeps meaning the existing profile. Alternative: reserve an exact `profile_reference` value for HT01 in schema 1. That is smaller, but selection stays implicit in a label that is free-form today. |
| D2 | Conversion identity | New wire ID `fl-mixer-h125-ht01-v1` and profile conversion `fl-mixer-h125-ht01`/`v1`. This follows the protocol's existing rule ("a new calibration … gets a new CONVERSION_ID") and isolates conversion blocking per scope. Alternative: reuse `fl-mixer-h125-v1` (same curve), accepting that an off-target write in one scope blocks the other. |
| D3 | Exact-API check on the device | Check API 45 before writing for HT01 only, as the calibration did. Extending it to the existing scope would change old behaviour and needs its own decision. |
| D4 | HT01 lower-edge usability | A start near 0.78 cannot be cut (any cut leaves the range). Accept and document it; do not widen with staging data. |
| D5 | ADR | Add a short new ADR for explicit profile selection and a second calibrated scope, referencing ADR-030/032/043 (all Proposed), rather than editing them. |
| D6 | FL journal per scope | Require a distinct `fl_journal` path per selected profile in operator guidance. Optionally refuse a journal whose recorded profiles differ from the selected one; the existing lineage-scope equality already refuses mixing per command. |

## 9. Open questions

- Whether a production run should also require a fresh calibration-identity inspect each session (title/build/API) beyond the existing per-command identity checks. This plan relies on the existing checks.
- Time stability of the HT01 curve across sessions is not measured. The single-run evidence remains the only basis.

## 10. Owner amendments (2026-11-26; binding for implementation)

1. **D6, enforced journal scope (supersedes "operator guidance" in D6).** Before any MIDI, an existing FL journal whose recorded calibration scope or conversion differs from the selected profile is refused. Every recorded apply or restore request stores its full profile, and every field except the `reference` label is compared. A different profile must not silently reuse another scope's authority, unresolved state or conversion blocks. Existing journals for the existing scope stay usable unchanged. Empty journals are compatible. No general registry is introduced.
2. **Stored-run compatibility.** Reopening a stored run (`daw continue`) compares the run's recorded DAW profile with the profile selected by the tool configuration. A mismatch is refused before the provider transport or any MIDI transport is constructed. Schema-1 configurations keep meaning the existing profile. Existing stored runs keep their meaning, and recorded runtime settings are unchanged, so they do not drift.
3. **Boundary tests.** Range eligibility and an admissible cut are tested separately:
   - A start at exactly 0.78 is range-eligible but admits no cut, and every cut from it is refused.
   - Endpoint acceptance is shown through valid operations: a cut landing at 0.78, a start at 0.92, and restorations to 0.78 and 0.92.
   - For HT01, the immediately adjacent floats outside the range (`math.nextafter(0.78, 0)` and `math.nextafter(0.92, 1)`) are tested.
   - The HT01 range is checked without the existing 1e-9 rounding tolerance (see 3.1 below), so its calibrated bounds are not silently widened.
4. Unchanged: the separate HT01 conversion ID, the exact API-45 check for HT01, and existing-scope behaviour (including its rounding tolerance).

Consequence for 3.1: the existing `in_conversion_range` keeps its 1e-9 rounding for the existing scope. HT01 range checks compare exactly, `0.78 <= v <= 0.92`, at the device, in protocol validation and in core admission (`DawRange`). An HT01 profile therefore never accepts values outside its calibrated bounds.

## 11. Implementation outcome (2026-11-26; uncommitted, for review)

Status: **Partial** for the operational path. The code and tests exist; installation, reload and the fresh read-only inspect (section 6) have not happened, so no live HT01 production write is possible or authorized.

- **Protocol:** `HT01_CONVERSION_*` constants are added. `conversion_for_scope` derives the conversion from the exact title and insert; `conversion_scope` and `conversion_admits` are new, and the HT01 range is checked exactly. `_validate_db`, `_validate_restore` and `set_gain_db_command` use them. The existing constants, rounding and command bytes are unchanged.
- **Device copy:** byte-identical, SHA-256 `7d5ddd6f540474157340dd713f0d618af33b7a5ee1fd4cbdb85544460ca96d9e`. The device script (`b4b816b4…`) checks the build from the selected conversion, API 45 for HT01 only, and the start against the selected range.
- **Profile:** `ht01_calibrated_profile`, `supported_profile`, `is_supported_profile`, `same_calibrated_scope` and `PROFILE_NAMES`. `make_apply_command` accepts only the two declarations.
- **Controller:** accepts either supported profile and refuses an incompatible journal scope before any transport (`JournalRefusal("incompatible_scope")`). It maps `calibration_api` to `UNCALIBRATED_BUILD`. The journal adds `recorded_profiles()`.
- **Drivers:**
  - Tool-configuration schema 2 adds `daw_profile`; schema 1 keeps meaning the existing profile.
  - `open_fl` selects the profile and closes the journal if construction is refused.
  - Run-config session profile and `_preflight` use the selection.
  - `reopen` refuses a stored run whose recorded profile differs, before provider or MIDI construction.
- **Superseded test:** `test_ht01_does_not_enable_production_db_or_restore_commands` (calibration stage) is replaced by `test_ht01_production_commands_are_limited_to_the_approved_profile_scope`, as approved. The runtime protocol snapshot is repinned.
- **Decision record:** [ADR-044](../../decisions/0044-explicit-calibrated-daw-profile-selection.md) (Proposed), indexed. The bridge contract has a new section.
- **Tests first:**
  - 61 of 65 new protocol, device, profile, admission, controller and journal tests failed before implementation. The 4 that passed are existing-scope regressions.
  - The configuration and stored-run module failed at collection.
  - Two harness defects were corrected: a replay built from the moved fader, and a restore binding built from the current reading.
  - Six same-title journal regressions were added after a mutation (scope compared by title only) survived.
- **Mutation probes:** 13 sequential probes, each followed by a hash-verified restore, are all caught:
  - rounded HT01 range;
  - missing scope check;
  - restore range ignored;
  - no API check;
  - old device range;
  - API pinned for the existing scope;
  - any profile supported;
  - scope compared by title only;
  - no journal-scope check;
  - no API refusal mapping;
  - schema 1 accepting a selection;
  - `open_fl` leaking the journal;
  - no stored-run check.
- **Evidence:**
  - Clean full suite run alone: `uv run --locked --extra agent pytest -q -p no:cacheprovider -rf`, 7,609 passed, 4 skipped in 440.30s.
  - Ruff format and check on `src`, `tests` and `device`: 325 files formatted, all checks pass.
  - The frozen `scripts/fl_studio/` files are unchanged.
