# DAW Inspect/Apply Contract and Calibrated Capability Gate — Implementation Plan

> **Status: approved by the owner 2026-11-18 with five amendments, incorporated below (marked *Amendment N*). No further approval round is needed.** Execute task by task with tests first, make no live FL Studio calls, and leave everything uncommitted for owner and Codex review.

**Goal:** typed, I/O-free core contracts for inspecting and applying one DAW fader change, and a pure pre-dispatch gate that permits a write only inside the tested, calibrated scope. Fader confirmation stays strictly separate from candidate audio.

**Spec:** [FL Studio action adapter design](../specs/2026-11-18-fl-studio-action-adapter-design.md), increment 1. **Builds on:** [DAW gain coordinates](2026-11-18-daw-gain-coordinates.md) (implemented, uncommitted; final verification 5,514 passed, 4 skipped). **Evidence:** [bridge contract](../../research/fl-studio-bridge-contract.md), [dB conversion design](../../research/fl-studio-db-conversion-design.md) §7–9.

## Global constraints

- **Not in this slice:**
  - no FL transport adapter, MIDI, research-script import, installed-script change, live FL call, save or export;
  - no coordinator change;
  - no recovery operation: exact-target recovery is the next plan.
- **Existing offline contracts are preserved unchanged:** `SetTrackGain`, `TrackGainState`, `RenderPlan`, `Renderer`, `RunRenderer`, `ExecutionReceipt`/`ExecutionStatus`, `RendererCapabilities`, `build_action_capability_view`. No DAW outcome converts into, or is accepted by, them.
- **The calibration curve stays out of core.** Core compares declared ranges in both coordinates (normalized and fader dB, as supplied by the profile); only the adapter evaluates the conversion function and the normalized target.
- **The bridge's rules are carried as contract obligations of the port:** pending intent before dispatch; the same command ID and payload replays the recorded result; a reused ID with different content is refused; every post-dispatch unknown is uncertain; history is lost on reload. Core never claims exactly-once execution.
- **Layering:** records import only records and the standard library; ports import only records; admission imports only records and ports. Initializers stay docstring-only.
- **Checks:** `uv run --locked …` throughout. Expected outcomes are typed results; wiring errors raise `ValueError`.

## Decisions for approval

1. **The gate consumes an `AdmittedAction`, not a raw `Action`.** `admit_action` (policy, protections, decision checks) must already have run; the DAW gate adds runtime capability and state checks on top and never replaces admission.
2. **The profile declares both coordinate ranges.** It carries the normalized range [0.62, 0.86] *and* the fader-dB range the adapter derives from it with its conversion. Core checks the start (both coordinates) and `target_fader_db` against them; the adapter re-checks the normalized target with the actual conversion. A profile is configuration supplied by the adapter, with no defaults in core.
   - *Amendment 3:* the dB bounds shown anywhere in documents (about −5.33 and +1.70 dB for `fl-mixer-h125-v1`) are **illustrative only**. Adapter configuration must derive the actual bounds from the versioned conversion at full float precision, with no rounding. Tests exercise both endpoints exactly and one float step beyond each.
3. **No-change is its own outcome (`DawNoWrite`), not a refusal.** A delta of exactly 0.0 dispatches nothing. Any positive delta is refused as an unsupported gain increase. A cut smaller than the minimum is refused, not rounded.
4. **Requirements and scope are proposed, not changed.**
   - ADR-030 is drafted as **Proposed**.
   - The requirement and scope change is written as a proposal in the ADR and as an entry under the requirements' *Open decisions*. Approved requirement rows, FR-14 (withdrawn), the `.flp` exclusion and ADR-009 stay untouched until a separate owner decision.

## File map

| File | Change |
| --- | --- |
| `src/autonomous_mixing/core/records/daw_control.py` | **new**: profile, inspect and apply requests, outcomes, gate results (Task 1–2) |
| `src/autonomous_mixing/core/ports/daw.py` | **new**: `DawParameterController` protocol and response validators (Task 3) |
| `src/autonomous_mixing/core/admission/daw.py` | **extend**: `daw_gain_bounds`, `gate_daw_apply` beside the existing `resolve_daw_gain` (Task 4) |
| `tests/unit/test_daw_control_contract.py` | **new**: records, gate, port validators, separation from candidate audio |
| `tests/architecture/test_core_records.py` | `daw_control` inventory and edges `{identifiers, invariants, actions, daw, admission}` |
| `tests/architecture/test_core_layers.py` | add `ports.daw`; extend `admission.daw` inventory |
| `tests/unit/test_public_contracts.py` | add the new protocol, outcome union and gate signatures (additions only) |
| `docs/decisions/0030-daw-parameter-control-boundary.md` + index | **new** ADR, Proposed (Task 6) |
| `docs/project/requirements.md` | *Open decisions*: proposed requirement/scope entry only (Task 6) |
| `docs/architecture/overview.md`, `docs/research/research-log.md`, this plan, the spec's status line | inventories, limits, evidence (Task 6) |

## Task 1 — Calibrated control profile (records)

```python
DawBuild(product_version: str, scripting_api: int)              # exact text from inspection, api built-in int >= 1
DawRange(minimum: float, maximum: float)                         # inclusive, finite, non-bool, minimum < maximum
DawControlProfile(
    reference: VersionRef,             # this profile's identity
    build: DawBuild,                   # the tested build
    project_title: str, insert_index: int, insert_name: str,     # the tested project title and insert
    conversion: VersionRef,            # e.g. VersionRef("fl-mixer-h125", "v1")
    normalized_range: DawRange,        # within [0, 1]
    fader_db_range: DawRange,          # declared by the adapter from its conversion; not computed in core
    minimum_cut_db: float, maximum_cut_db: float,                # 0 < min <= max (0.1, 3.0)
    start_normalized_tolerance: float, start_db_tolerance: float, # >= 0 (0.001, 0.05)
    success_db_tolerance: float,       # >= 0 (0.05)
    readback_normalized_tolerance: float,                        # >= 0 (0.001)
)
```

- **Semantics.** The profile describes declared tested scope only, not proof of the open file, routing or audio.
- **No defaults.** The values in parentheses are the bridge's reviewed settings, supplied by tests and, later, by adapter configuration; none is a core default.
- **Tests first:** every field's type, bool, non-finite, empty text, index < 1, inverted or degenerate ranges, a normalized range outside [0, 1], cut limits ≤ 0 or inverted, negative tolerances, frozen behaviour.

## Task 2 — Inspect/apply requests, outcomes and gate results (records)

**Inspect.**
- `DawInspectRequest(binding: DawTrackBinding, profile: DawControlProfile)`: the binding's title and insert must equal the profile's, otherwise `ValueError` (wiring).
- `DawInspection(request: DawInspectRequest, build: DawBuild, project_title: str, insert_index: int, insert_name: str | None, fader: DawFaderReading | None)`. These are observed values: a mismatching identity is representable and reported, never silently accepted. `fader` is `None` exactly when the identity does not match, as on the bridge.
- *Property:* `matches_binding` is true only if title, index and name equal the request.

**Apply request.** *Amendment 2:* a public record cannot guarantee that it was "constructed only through the gate". So `DawApplyRequest.__post_init__` independently validates everything that does not depend on controller state, raising `ValueError`:
- the field types and the `command_id` pattern;
- `admitted.action == resolution.action`;
- the inspection belongs to this binding and this profile, matches the binding's identity, reports the profile's build, and its fader equals `resolution.observed_fader`;
- the binding lies inside the profile's scope (title and insert);
- drift: the observed fader is within the profile's start tolerances of `resolution.state.confirmed_fader`;
- a permitted cut: `requested_change_db == resolution.delta_db`, the delta is negative, and its magnitude is within [minimum, maximum] cut;
- the observed start is inside both profile ranges, and `resolution.target_fader_db` is inside `fader_db_range`.

The checks are one shared records-level function, `daw_apply_rejection(...)`, which returns the first `DawApplyRejected` or `None`. The record raises on any rejection, and the gate returns the same typed rejection, so the two cannot diverge. **The unresolved-operation and conversion-block checks stay controller and gate responsibilities:** they depend on run state that a record cannot see.

```python
DawApplyRequest(
    execution_id: ExecutionId,
    command_id: str,                   # stable; [A-Za-z0-9_-]{8,64}; a retry reuses it with the identical request
    admitted: AdmittedAction,
    resolution: DawGainResolution,     # from resolve_daw_gain; action equals admitted.action
    profile: DawControlProfile,
    inspection: DawInspection,         # matching identity and build; its fader equals resolution.observed_fader
    requested_change_db: float,        # == resolution.delta_db, within -maximum_cut..-minimum_cut
)
```

**Apply outcome union** `DawApplyOutcome`. Each variant carries the `command_id`; none carries a revision, artifact or `ExecutionStatus`.

| Variant | Meaning | Fields beyond `command_id` |
| --- | --- | --- |
| `DawWriteRefused` | **pre-write refusal**: nothing written | `code: DawRefusalCode`, `explanation`, `observed: DawFaderReading \| None` |
| `DawWriteConfirmed` | **confirmed application**: fader at the commanded position and the dB change within tolerance | `before`, `after`, `commanded_normalized`, `achieved_change_db`, `replayed: bool` |
| `DawWriteOffTarget` | **known write that missed** the requested dB | same as confirmed |
| `DawWriteUncertain` | the effect is **not established** | `reason: DawUncertainReason`, `before \| None`, `after \| None`, `explanation` |

- **`DawRefusalCode`** mirrors the reviewed bridge's refusals: `IDENTITY_MISMATCH`, `TRANSPORT_PLAYING`, `EDIT_UNAVAILABLE`, `START_GAIN`, `OUT_OF_RANGE`, `UNCALIBRATED_BUILD`, `UNCALIBRATED_START`, `CONVERSION`, `CONVERSION_MISMATCH`, `BOUNDS`, `DUPLICATE_ID_CONFLICT`, `API_TOO_OLD`, `UNSUPPORTED`, `UNAVAILABLE`. `UNAVAILABLE` is for failures before the pending intent was recorded, such as ports that could not be opened; after that point the outcome is uncertain.
- **`DawUncertainReason`:** `TIMEOUT`, `INVALID_REPLY`, `READBACK_DISAGREES`, `EXCEPTION_AFTER_WRITE`, `DISPATCH_INTERRUPTED`, `PENDING_UNRESOLVED`.
- **Record invariants:**
  - finite, non-bool numbers;
  - `achieved_change_db == after.db − before.db` exactly;
  - `replayed` is a bool;
  - uncertain records require an explanation.

**Gate results.**
- `DawApplyPermitted(request: DawApplyRequest)`.
- `DawNoWrite(resolution: DawGainResolution)`: delta exactly 0.0; nothing to dispatch.
- `DawApplyRejected(code: DawGateCode, explanation: str)`, with `DawGateCode` = `PROFILE_SCOPE`, `IDENTITY_MISMATCH`, `UNSUPPORTED_BUILD`, `STALE_REVISION`, `DRIFT`, `GAIN_INCREASE_UNSUPPORTED`, `CUT_OUT_OF_BOUNDS`, `START_OUT_OF_RANGE`, `TARGET_OUT_OF_RANGE`, `CONVERSION_BLOCKED`, `UNRESOLVED_OPERATION`.

## Task 3 — Port and response validators

```python
class DawParameterController(Protocol):
    def inspect(self, request: DawInspectRequest) -> PortResult[DawInspection]: ...
    def apply(self, request: DawApplyRequest) -> DawApplyOutcome: ...

def validate_daw_inspection(request: DawInspectRequest, result: PortResult[DawInspection]) -> None
def validate_daw_apply_outcome(request: DawApplyRequest, outcome: DawApplyOutcome) -> None
```

**Docstring obligations** (the reviewed bridge rules, binding on any adapter):
- **Pending intent:** journal the pending intent before dispatch.
- **Retries:** an identical retry with the same `command_id` must never write again and must return the recorded outcome: a written outcome (confirmed or off-target) with `replayed=True`, and a refused or uncertain outcome unchanged, since those records have no `replayed` field *(clarified after review, 2026-11-18)*; a different request under the same `command_id` must be `DawWriteRefused(DUPLICATE_ID_CONFLICT)`.
- **Uncertainty:** anything unknown after dispatch is `DawWriteUncertain`.
- **Durability:** reload loses duplicate history, so exactly-once delivery is not claimed.
- **Scope of a confirmation:** it confirms only the parameter reading, not routing, plugins, project state or audio.

**`validate_daw_apply_outcome`** (structure and consistency against the request; raises `ValueError` on adapter contract violations):
- the type is one of the four variants, with the request's `command_id`;
- for refusals, `observed` is `None` or a reading;
- for confirmed and off-target outcomes:
  - `before` within the profile's start tolerances of the request's expected start;
  - `after.normalized` within the read-back tolerance of `commanded_normalized`;
  - `commanded_normalized` inside the profile's normalized range;
  - `|achieved − requested| ≤ success_db_tolerance` for `Confirmed` and `>` for `OffTarget`, exactly the bridge's side of the boundary;
  - *Amendment 4:* these checks are **structural**. Core cannot prove that `commanded_normalized` is the position that produces the requested dB target, because that needs the conversion curve. The port documents `commanded_normalized` as an **adapter claim**: the adapter must recompute the target from the expected start dB plus the requested change with its versioned conversion, and check it against its own range, **before dispatch**. Core only checks that the claimed position is inside the profile's normalized range and that the read-back agrees with it;
- uncertain outcomes are accepted as reports and not judged.

**`validate_daw_inspection`:**
- a `Success` whose payload's request equals the input;
- a `PortFailure` is allowed (inspect is read-only, so failure means "nothing known").

## Task 4 — Calibrated capability gate (admission, pure)

```python
def daw_gain_bounds(*, profile, state: DawTrackState, permission: TrackGainPermission) -> GainBounds | None
```

*Amendment 1:* the function first validates its references and raises `ValueError` for a permission naming another track than `state.binding.track_id`, and for a profile whose project title and insert differ from the binding's. Otherwise it could advertise a capability for the wrong insert.

Advertised availability in **application** coordinates, derived from the **confirmed** state (not a live reading). Let `c = g0 + (state.confirmed_fader.db − f0)` be the application gain equivalent to the current fader. The bounds are the exact intersection of:
- the policy permission;
- `[c − maximum_cut, c − minimum_cut]`;
- the absolute window whose fader targets stay inside `fader_db_range`: `[g0 + (db_lo − f0), g0 + (db_hi − f0)]`.

If the intersection is empty, or the confirmed start is outside either range, the result is `None`. No clamping or rounding. This is a view, not permission, as with `build_action_capability_view`, which stays unchanged.

```python
def gate_daw_apply(admitted: AdmittedAction, *, state: DawTrackState, inspection: DawInspection,
                   profile: DawControlProfile, execution_id: ExecutionId, command_id: str,
                   blocked_conversions: frozenset[VersionRef], unresolved_command_id: str | None
                   ) -> DawApplyPermitted | DawNoWrite | DawApplyRejected
```

The checks run in this order, each a typed rejection; `ValueError` is only for wiring errors such as wrong types or an inspection for another request:
1. An unresolved operation exists → `UNRESOLVED_OPERATION`. *(No new action follows an uncertain write until the controller resolves it.)*
2. `profile.conversion ∈ blocked_conversions` → `CONVERSION_BLOCKED`. *(An off-target write blocks further dB actions under that conversion.)*
3. The binding's title and insert differ from the profile's → `PROFILE_SCOPE`.
4. The inspection identity does not match → `IDENTITY_MISMATCH`.
5. The inspection build ≠ profile build → `UNSUPPORTED_BUILD`.
6. The action's revision ≠ the state's revision, or the project or track differ → `STALE_REVISION` (checked here, so `resolve_daw_gain`'s `ValueError` stays a wiring guard).
7. The observed fader is outside the profile's start tolerances of the confirmed fader → `DRIFT` (checked here for the same reason).
8. `resolve_daw_gain(…, normalized_tolerance=…, db_tolerance=…)` with the profile's start tolerances.
9. Delta exactly 0.0 → `DawNoWrite`; delta > 0 → `GAIN_INCREASE_UNSUPPORTED`; `−delta` outside [minimum, maximum] cut → `CUT_OUT_OF_BOUNDS`.
10. The observed start is outside either profile range → `START_OUT_OF_RANGE`; `target_fader_db` outside `fader_db_range` → `TARGET_OUT_OF_RANGE`.
11. → `DawApplyPermitted(DawApplyRequest(...))`.

All range and limit comparisons are inclusive. They use the exact values given; any rounding policy is the adapter's and is documented there.

## Task 5 — Tests first (`tests/unit/test_daw_control_contract.py`)

Write all of these before the code, and record the red run (collection failure, then assertion failures).

- **Records:** every invalid field of Tasks 1–2; consistency checks (achieved arithmetic; request internals equal the resolution, admitted action and inspection).
- **Gate, one test per code:**
  - wrong title or insert in the profile scope;
  - inspected identity mismatch (title, index, name);
  - a different build string, and a different API;
  - stale revision, wrong project, wrong track;
  - drift on normalized only and on dB only, with exact tolerance edges;
  - increase; no-change (exact 0.0) giving `DawNoWrite`; cuts of 0.1 and 3.0 accepted; 0.09, 3.01 and a tiny non-zero delta refused;
  - start just inside, on and just outside each range edge in both coordinates; target on and outside `fader_db_range`;
  - *Amendment 3:* full-precision endpoints derived in the test fixture from the reviewed conversion function, which the fixture loads from `scripts/fl_studio/bridge_protocol.py` (a test-only import, never a runtime one). The cases are exactly on each endpoint, accepted, and `math.nextafter` beyond each, refused;
  - *Amendment 1:* `daw_gain_bounds` with a permission for another track, and with a profile for another title or insert, raises `ValueError`;
  - *Amendment 2:* `DawApplyRequest` constructed directly, bypassing the gate, with each invariant violated (foreign inspection or profile, mismatching identity or build, observed ≠ resolution, drift beyond tolerance, wrong `requested_change_db`, positive or zero delta, cut outside limits, start or target out of range), raises `ValueError`;
  - blocked conversion; unresolved operation;
  - the gate's evaluation order (the first failing check wins).
- **Coordinates through the gate:**
  - a nonzero baseline (+2 / −4) with a sequential second action resolved against the original binding;
  - `requested_change_db == resolution.delta_db`;
  - `daw_gain_bounds` intersection cases, including empty → `None` and a confirmed start outside the range.
- **Outcome validation:**
  - a confirmed outcome at success tolerance exactly and an off-target outcome just beyond it;
  - each wrong side refused;
  - a `before` outside the start tolerance, `after` away from `commanded_normalized`, `commanded_normalized` outside the range, a wrong `command_id`, a wrong type;
  - uncertain outcomes accepted.
- **Inspection validation:** a foreign request refused; a `PortFailure` accepted.
- **Bridge obligations, through a test-only simulated controller** (in the test file; it is not an adapter). *Amendment 5:* these tests **demonstrate the intended contract**; they do not show that any future adapter journals, replays or refuses as required. The same checks must be repeated against the real FL adapter in subsequent plan 1, and the docs say so.
  - pending intent before dispatch;
  - an identical retry replays with no second write; a conflicting reuse is refused;
  - timeout or dispatch interruption is uncertain, and so are failures after dispatch;
  - a reload (fresh fake) loses history.
- **Separation from candidate audio:**
  - DAW outcome records have no revision, artifact or `ExecutionStatus` fields;
  - `derive_candidate_checkpoint`, `validate_receipt_against_checkpoint` and `validate_rendered_candidate` reject DAW outcomes with `ValueError`/`TypeError` (each pinned);
  - `ExecutionStatus` members are unchanged.
- **Mutation probes**, each reverted byte-identically and required to fail a permanent test:
  - remove the drift check;
  - flip `>`/`≥` at the success-tolerance boundary;
  - allow a positive delta;
  - compute the target from the observed fader instead of the baseline;
  - skip the build comparison.

## Task 6 — ADR, requirement/scope proposal, inventories, documentation

- **ADR-030, *DAW parameter-control boundary beside the offline renderer* (Proposed).**
  - **Decision:** DAW parameter control is a separate core port (`DawParameterController`) with fader-only outcomes; it is not a `Renderer` or `RunRenderer` and never yields `CANDIDATE_READY` evidence. Candidate audio for a DAW change can only come from a later, separately verified export import.
  - **Adapters:** they own MIDI, protocol, conversion, logs and their configured profile. Writes are gated by the calibrated profile (build, project title, insert, conversion, ranges, cut limits).
  - **Bridge rules:** pending, duplicate and uncertain rules are port obligations.
  - **Consequences:** two execution paths with distinct evidence; recovery and export continuation are still missing; identity rests on metadata title plus operator confirmation.
  - **Relationship to earlier decisions:** relates to ADR-001 and ADR-027. ADR-009 is unchanged; FR-14 stays withdrawn.
  - **Not adopted:** runtime adoption requires owner approval of the requirement proposal and the later tasks.
- **Requirement and scope proposal**, written in the ADR and as an *Open decisions* entry in `requirements.md`, with no table or status change:
  - **Proposed new FR (ID assigned at approval):** "The system can apply an admitted gain action to one owner-confirmed DAW insert through a calibrated parameter-control adapter, report pre-write refusal, confirmed, off-target and uncertain outcomes, and never treat a parameter change as verified audio."
  - **Proposed scope wording:** live parameter control of one calibrated FL Studio insert through its scripting API, for operator-attended trials; `.flp` manipulation stays excluded.
- **Inventories:** records (`daw_control`), layers (`ports.daw`, `admission.daw` additions), public contracts (additions only). Adapter inventories are unchanged.
- **Documentation:**
  - overview: rows plus a "DAW control contract" note with its limits;
  - research log: an appended entry with baseline HEAD, red/green, mutation evidence and the remaining gaps;
  - this plan: checkboxes and outcome;
  - the spec: a status-line pointer only.
- **Final checks:**
  - `uv run --locked pytest tests/unit/test_daw_control_contract.py tests/unit/test_daw_gain_coordinates.py tests/architecture tests/unit/test_public_contracts.py -q -W error`;
  - `uv run --locked pytest -q -rs`; `uv run --locked ruff check .`; `uv run --locked ruff format --check src tests`; `uv run --locked autonomous-mixing --help`; `uv lock --check`.
  - Inspect the diff: no existing assertion weakened; no change to offline contracts, scripts or initializers.

## Outcome (2026-11-18, Claude Code; uncommitted for review)

- **Tasks 1–6 done.**
  - **Code:** `core/records/daw_control.py`, `core/ports/daw.py`, and the `core/admission/daw.py` additions (`daw_gain_bounds`, `gate_daw_apply`).
  - **Tests:** `tests/unit/test_daw_control_contract.py`.
  - **Inventories:** the architecture inventories and the public-contract pins.
  - **Documents:** ADR-030 (Proposed) and its index row; *Open decisions* D15 (first entered as D14, an ID already used by a resolved decision; renumbered after review); the overview note; the spec's status line; the research log.
- **Red, then green.** The test file was written first, and its first run failed at collection: `core.records.daw_control` did not exist. Green: 136 tests in the new file.
- **Locked checks:**

  | Check | Result |
  | --- | --- |
  | focused suites (`-W error`) | 496 passed |
  | full suite (`pytest -q -rs`) | 5,651 passed, 4 skipped (the four existing platform skips) |
  | `ruff check .` | clean, after one import-order fix in the new test file |
  | `ruff format --check src tests` | clean |
  | `autonomous-mixing --help` | runs |
  | `uv lock --check` | consistent |

- **Mutation probes.** Each was reverted byte-identically and caught by a permanent test:

  | Probe | Caught by |
  | --- | --- |
  | drift check removed | `test_drift_is_checked_on_both_coordinates_with_inclusive_edges` |
  | success boundary `<=` → `<` | `test_confirmed_and_off_target_are_decided_on_the_exact_side_of_the_success_tolerance` |
  | positive delta allowed | `test_any_gain_increase_is_refused` |
  | target from the observed fader | `test_drift_is_checked_on_both_coordinates_with_inclusive_edges` |
  | build comparison skipped | `test_unsupported_build_is_refused` |

- **Decision taken during implementation:** the gate compares the binding with the profile's scope (`PROFILE_SCOPE`) *before* it checks which request the inspection answers. Otherwise that refusal would be unreachable, because `DawInspectRequest` itself rejects an out-of-scope binding.
- **Review fixes (2026-11-18, after Codex review):**
  1. *Advertised bounds vs gate (P2).* With fader and application baselines of −4 dB and 0, the advertised upper endpoint −0.1 dB resolved to a 0.09999999999999964 dB cut, which the gate refused. Fix:
     - a single records-level rule, `daw_change_rejection`, is used by `daw_cut_rejection` and by the bounds;
     - the baseline mapping is written once, in `_mapped_target`;
     - `daw_gain_bounds` checks each endpoint with the gate's arithmetic and moves it inward one float step at a time, at most 64 steps, until it is permitted. It never moves outward.

     End-to-end regressions over seven baselines check that both endpoints are permitted by the gate, that they lie within the naive bounds, and that any moved endpoint is tight: the next float outward is refused.
  2. *Decision ID (P2).* The adoption decision is **D15**: D14 was already used by a resolved decision, which is preserved. ADR-030, the overview and this plan now use D15.
  3. *Replay wording (P3).* An identical retry never writes again. Written outcomes return with `replayed=True`; refused and uncertain outcomes return unchanged, since they have no such field. The port, the records, ADR-030 and this plan now say so, and the simulated controller has tests replaying an uncertain outcome (after a write, and without one) and a refusal unchanged, with no further write.

  4. *Endpoint search exhausted near zero (P2, second review).* With baseline +0.1 dB at fader −4 dB, the naive upper endpoint is 0.0. The 64-step walk stayed among subnormal floats near zero and reported no capability, although −0.9 dB passes the gate as a −1 dB cut.
     - **Fix:** the walk is replaced by a bounded bisection on the floats' ordered bit patterns, at most 64 halvings. It starts from a permitted anchor: the window's midpoint, else whichever endpoint is permitted. This is valid because the gate's predicate is monotone along the gain axis, so the permitted values form one contiguous run.
     - **Kept:** endpoints still only move inward and are tight. If neither endpoint nor the midpoint is permitted, the result is `None`, which is conservative and only possible when the window is within float rounding of being empty.
     - **Tests:** the reviewed case and two further zero-crossing baselines were added to the end-to-end and tightness regressions. A dedicated regression checks that −0.9 dB is advertised and that the new endpoint lies just below zero. Single-value policy windows are kept when permitted and reported as `None` when the gate refuses them.
     - **Mutation probes:** reinstating the 64-step walk, and returning the refused side of the boundary, are each caught.

     After this fix: 164 tests; locked full suite 5,679 passed, 4 skipped.

  **After fixes 1–3:** 154 tests in the file; two further mutation probes (naive endpoints, outward shift) are each caught; full locked suite 5,669 passed, 4 skipped; ruff, format and lock clean.
- **Not done (as planned):** FL adapter, MIDI, dispatch, recovery, export continuation and coordinator integration. No script was installed, FL Studio was not touched, and nothing was saved, exported, committed or pushed.

## Subsequent plans (not in this slice)

1. FL transport adapter: move the reviewed protocol and transport into `adapters/`, keeping the research scripts reproducible; implement the port and profile configuration; run a live test of the gate's refusals.
   - *Amendment 3:* derive the profile's `fader_db_range` from the versioned conversion at full precision.
   - *Amendment 4:* recompute and check `commanded_normalized` before dispatch.
   - *Amendment 5:* repeat the journaling, replay, conflict, timeout and reload checks against the real adapter.
2. Exact-target recovery capability with a separate authorization and budget, including upward restoration and live restore tests. The calibration `probe_gain` restore is not production rollback.
3. Pending-export request, operator continuation and verified artifact import.
4. Coordinator integration: DAW state beside audio authority; unresolved-operation and conversion-block state supplied to the gate; no fader-only candidate-ready path.

## Self-review

- **Coverage of the task's points:**
  - typed requests and outcomes: Tasks 2–3;
  - binding and revision checks plus use of `resolve_daw_gain`: Task 4, steps 3–8;
  - tested scope (build, project, insert, conversion, range): Tasks 1 and 4;
  - four distinct outcomes: Task 2;
  - fader versus audio separation: Tasks 2 and 5 and the ADR;
  - ADR and requirement proposal: Task 6.
- **No change to existing contracts.**
- **Thresholds:** no new scientific threshold is introduced; every number is the reviewed bridge's trial setting, supplied as configuration.
