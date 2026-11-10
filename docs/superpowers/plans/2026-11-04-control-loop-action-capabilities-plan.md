# Control Loop Action Capability View Implementation Plan (Increment 2B)

> **For agentic workers:** Use superpowers:executing-plans serially. Implement this increment only, test first, and stop uncommitted for Codex review. Preserve unrelated work; the owner commits after review.

**Goal:** Derive an immutable, gain-only action view for a later Situation, reflecting declared renderer support, admission policy, run scope and producer protections.

**Architecture:** Lower-level frozen data lives in `core.records.capabilities`, which never imports decision/admission/run records. A pure builder in `core.admission.capabilities` can import authoritative admission configuration and derive the view. This avoids the decision → admission → decision cycle identified in the approved design.

**Tech stack:** Existing Python >=3.11, stdlib, pytest, Ruff and uv; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-04-control-loop-lifecycle-design.md` and `docs/superpowers/specs/2026-11-04-control-loop-attempt-contract.md`, sections 11, 14 and 28. Parent design approved. Inspected clean HEAD `a8fb623`; diagnose records and the budget gate are committed. Inspect later changes instead of resetting them.

## Global constraints

- The source lifecycle stays fixed. An available action is not a diagnosed problem, a justified proposal, execution permission or proof of safety.
- Only SetTrackGain exists as an action today. Do not advertise EQ, dynamics or stereo; add no tool registry/plugin discovery framework.
- Renderer support is explicit versioned configuration, not inferred by inspecting an implementation. None means no declared gain support. Actual adapter binding/enforcement is later work.
- Bounds are absolute additional gain in dB, not a relative adjustment, default gain or scientific prescription. Do not invent default bounds.
- Derive parameter-range intersection; do not clamp/round proposed values or mutate policy/context/scope.
- The supplied scope restricts available target tracks. It does not turn SetTrackGain into a time-local or band-local action: the supported gain operation affects the complete target stem.
- Records are frozen, required-field dataclasses. Package initializers remain docstring-only. Use existing invariants and ValueError conventions; no global numeric-validation changes.
- Preserve existing TrackGainPermission, ActionAdmissionPolicy, Situation, decisions, admission/preflight behavior and their public signatures. Do not move/re-export existing permission records.
- No filesystem, provider, DSP, adapter calls, live budget checks, action execution or record persistence.

## Review focus

1. Protected tracks must be omitted even when both policy and renderer permit their gain range.
2. Equal intersection endpoints permit one exact setting; disjoint ranges omit the action, not clamp it.
3. Invalid global configuration must not disappear behind an empty scope match, no permissions or unsupported renderer.
4. Output order must depend on supplied scope order, not policy iteration order or incidental set ordering.
5. The lower-level view must remain independent of admission/decision records; later Situation integration must not introduce a records cycle.

## Ownership

- Create `src/autonomous_mixing/core/records/capabilities.py`: four records below and private invariant helpers if needed.
- Create `src/autonomous_mixing/core/admission/capabilities.py`: `build_action_capability_view` and private helpers if needed.
- Create `tests/unit/test_action_capabilities.py`.
- Modify `tests/architecture/test_core_records.py`: add `capabilities` with allowed sibling imports exactly `identifiers`, `invariants`, `observation`, and its four public records to the inventory. No existing allow list is widened.
- Modify `tests/architecture/test_core_layers.py`: add `"admission.capabilities": ("build_action_capability_view",)` to BEHAVIOR_INVENTORY. Preserve boundary rules and fixtures.
- Modify `docs/architecture/overview.md`: focused implemented-data/builder note, including the non-enforcement gap below.
- Modify `docs/research/research-log.md`: append one dated evidence/limitation entry; do not edit earlier entries.
- Update this plan's checkboxes only.

Do not modify existing admission/decision/permission records, admit_action, preflight, other tests, adapters, initializers, dependencies, accepted ADRs or requirements. Stop/report a material conflict before widening scope.

## Task 1: Typed capability view and pure derivation

### Exact records in `core.records.capabilities`

All fields are required; no defaults or package exports:

```python
GainBounds(minimum_db: float, maximum_db: float)

RendererCapabilities(
    reference: VersionRef,
    gain_bounds: GainBounds | None,
)

AvailableTrackGain(track_id: TrackId, bounds: GainBounds)

ActionCapabilityView(
    project_id: ProjectId,
    revision: Revision,
    context_id: ContextId,
    context_version: str,
    policy_reference: VersionRef,
    renderer_reference: VersionRef,
    scope: AnalysisScope,
    gain_actions: tuple[AvailableTrackGain, ...],
)
```

Constructor invariants:
- GainBounds follows existing finite-number acceptance: bool, strings, NaN/infinity and unrepresentable integers are rejected with ValueError; require minimum_db <= maximum_db. Equal endpoints and negative values are valid. Preserve supplied values, with no rounding or conversion.
- RendererCapabilities requires a VersionRef and either None or a typed GainBounds. Bounds describe declared parameter support; they do not guarantee that every signal can be rendered without overflow or that physical resources are ready.
- AvailableTrackGain requires proper types. It describes one potential SetTrackGain target, not an Action with an execution identity.
- ActionCapabilityView type-checks all fields; context_version is non-blank; gain_actions is a tuple, may be empty, contains only AvailableTrackGain, and has unique track IDs. Every advertised track belongs to scope.track_ids.
- Constructors do not verify policy truth, protection state, actual renderer implementation or snapshot membership. The builder validates configuration references; later admission/dispatch independently enforces authoritative rules.

### Exact builder in `core.admission.capabilities`

```python
def build_action_capability_view(
    *,
    state: ProjectState,
    scope: AnalysisScope,
    context: MusicalContext,
    policy: ActionAdmissionPolicy,
    renderer: RendererCapabilities,
) -> ActionCapabilityView:
    ...
```

Validate before filtering:
1. Require the expected domain record types using require_instance, preserving the existing subclass acceptance convention.
2. Validate scope and context against state.snapshot.
3. Require policy.project_id == state.project_id.
4. Validate EVERY policy permission's track against the snapshot, including permissions outside the current scope and even if renderer.gain_bounds is None. Do not silently discard an unknown-track configuration error.

Then derive:
- Iterate tracks in scope.track_ids order, preserving it exactly.
- Omit a protected track or one with no explicit policy gain permission.
- If renderer.gain_bounds is None, advertise no gain actions.
- For a remaining track with permission `[p_min, p_max]` and renderer bounds `[r_min, r_max]`, calculate `lo = max(p_min, r_min)` and `hi = min(p_max, r_max)`. Omit when lo > hi. Otherwise emit AvailableTrackGain(track_id, GainBounds(lo, hi)), including equality.
- Return the exact current project/revision, context identity/version, policy.reference, renderer.reference, supplied scope and derived tuple. No other bounds, defaults or assumptions are inserted.
- Return an empty view normally when no gain operation is available. Empty does not mean no mixing problem, a No Change decision or verified success.

No invalid configuration is converted into an empty view. Wrong types/references raise ValueError; unexpected programming exceptions propagate. The builder neither calls admit_action nor needs a Decision/Situation.

### Scope of enforcement

Current admit_action checks producer protections and ActionAdmissionPolicy bounds; it does NOT yet enforce the new renderer-capability descriptor or the complete advertised scope. This increment does not change that behavior. Future Situation/loop integration must bind the authoritative renderer/policy/scope configuration and reject out-of-view proposals before execution. Do not document this standalone view as closing FR-07/FR-08/FR-11 or as an execution capability token.

The capability descriptor must be supplied by driver configuration matched to the chosen renderer. No numeric capabilities or default renderer profile are fabricated here. Actual gain parameter bounds remain an explicit D5/configuration decision.

## Step 1: Failing record tests, then records

- [x] Read parent specs, this plan, CLAUDE/conventions, existing policy/context records and architecture tests. Record HEAD/status and preserve unrelated work.
- [x] Write constructor tests for all four missing records; pin exact fields/requiredness, frozen behavior, numeric edge cases, enum-free typed references, tuple/track uniqueness and scope subset. Run the focused file before implementation; observe missing-module/name failure.
- [x] Implement the records and narrowly update the records graph/inventory. Run focused constructor and records-architecture tests; require exit 0. Existing signatures stay unchanged.

## Step 2: Failing builder tests, then builder

- [x] Add tests for the missing builder, its exact signature and required behavior, then observe a red run before implementation.
- [x] Use metadata fixtures only. Required cases:
  - Scope order `(t3, t1, t2)`, unordered policy permissions `t2:[-4,4]`, `t1:[-8,-2]`, `t3:[0,5]`, protected `t2`, renderer bounds `[-6,2]`: return `t3:[0,2]`, then `t1:[-6,-2]`.
  - Touching intervals `[-2,0]` and `[0,3]` return `[0,0]`; disjoint intervals omit that track.
  - Renderer gain_bounds None, empty permissions, all protected targets or all disjoint ranges produce an empty view with correct provenance.
  - A known permission track outside scope is omitted. An UNKNOWN permission track fails even outside scope, with no gain support or an otherwise empty result.
  - Unknown scope/context tracks and a foreign policy project fail. Do not infer roles from track names.
  - Actual metadata fields, context/version and configured references survive exactly; input objects/tuples remain unchanged.
  - Decimal bounds are selected exactly without rounding. Test values are boundary fixtures, not musical thresholds.
  - Reordered policies yield the same scope-ordered view.
  - Wrong input types raise ValueError. A malformed standalone view cannot contain duplicate or out-of-scope tracks.
- [x] Implement the pure builder with allowed imports, and add its exact behavior inventory entry. Run focused and architecture tests.
- [x] Temporary mutations: remove protected-track filtering; reverse the lower-bound intersection (`min` instead of `max`); omit validation of unknown out-of-scope permission tracks. Each must fail an appropriate behavioral test. Restore all files, then rerun focused tests. No new mutation tooling.

## Step 3: Documentation, verification and handoff

- [x] Update overview in place and append the research log with observed evidence and the non-enforcement limitations. No new ADR is needed to implement the approved dependency direction and derived input view.
- [x] Run:

```text
uv run --locked pytest tests/unit/test_action_capabilities.py -q
uv run --locked pytest tests/architecture tests/unit/test_public_contracts.py -q
uv run --locked pytest -q -rs
uv run --locked ruff check .
uv run --locked ruff format --check src tests
uv run --locked autonomous-mixing --help
uv lock --check
```

- [x] Check the exact diff: owned paths only, no old record/behavior changes, no earlier log edits, no initializer/dependency changes. Do not format unrelated files. Existing .venv fallback is allowed only if uv genuinely cannot be found; report the missing lock check and do not install tools/dependencies.
- [x] Stop uncommitted with HEAD/status, exact interfaces, red/green and restored-mutation evidence, observed checks/skips, deviations and gaps. Do not add history records, No Change or Situation integration.

## After review

Next separately planned slice: compact decision/action feedback records with acyclic ownership. Situation/outcome integration follows when these supporting contracts are ready.
