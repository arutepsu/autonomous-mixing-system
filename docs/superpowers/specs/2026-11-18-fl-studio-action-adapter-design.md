# FL Studio action adapter and manual export handoff

Date: 2026-11-18. Status: proposed design for owner review; no implementation authorized by this document alone. *Implementation status (2026-11-18): the coordinate slice and the DAW inspect/apply contract with its calibrated gate are implemented in core and not integrated; see [plan](../plans/2026-11-18-daw-control-contract.md) and [ADR-030](../../decisions/0030-daw-parameter-control-boundary.md) (Proposed).*

## Purpose and evidence

Connect an admitted agent gain action to the live-tested FL Studio bridge, then obtain verified audio for the existing observe/diagnose/decide/evaluate loop. FL Studio remains the correction environment. The system owns admission, constraints, evidence and acceptance; the agent proposes actions through defined tools.

The existing bridge has live evidence for inspect, bounded normalized writes, calibration and dB cuts on FL Studio Producer Edition 26.1.7 build 5654, metadata title `Thesis Bass Bridge Test`, insert 2 `Bass`, normalized range 0.62–0.86. Requested −1 and −2 dB cuts measured −0.999595 and −1.999212 dB; the final restore matched the initial readings exactly. See [conversion design](../../research/fl-studio-db-conversion-design.md). This evidence does not cover arbitrary projects, instruments, inserts or builds.

## Approaches and selected direction

1. **Two increments: action adapter, then manual export handoff (selected by owner).** Keeps the tested write boundary small while making a complete measured iteration possible with an operator.
2. **Automate actions, saves and exports together.** Fewer manual steps eventually, but save/export automation lacks feasibility evidence and would mix several failure modes into this increment.
3. **Wrap the existing offline Renderer unchanged.** Rejected: its fixed-stem processing and linear summation semantics do not describe a DAW project with routing and effects.

The two increments are a semi-automated research workflow. Full unattended DAW operation is later work.

## Existing contracts that must stay truthful

`SetTrackGain.value_db` is absolute. `RenderPlan.requested_gain_db` describes an absolute additional gain on a fixed input, and `Renderer` promises that processed stem plus a summed mix. `RunRenderer` returns complete rendered artifacts synchronously. None represents a fader-only write followed by a pending human export.

Do not implement the DAW path by returning fabricated `RenderedCandidate` evidence or weakening those contracts. Do not put a live MIDI call in the agent or core records. The existing offline path remains available as its separately labelled implementation.

## Increment 1: admitted action to verified DAW read-back

### Binding and gain coordinates

An explicit session binding maps project/revision and stable TrackId to the configured FL metadata title, insert index/name, FL build/API, conversion ID and baseline fader readings. The owner confirms the disposable project copy and routing. File hash and metadata title are recorded, but neither authenticates the currently open unsaved project. Initial scope is one controlled operator session; unattended project identity is unsupported.

Keep the application gain coordinate separate from the FL fader coordinate. For baseline application gain `g0` and corresponding observed FL fader dB `f0`, absolute action `g` resolves to absolute FL target `f0 + (g - g0)`. This is a parameter-coordinate mapping, not a claim that exported audio is a scaled fixed stem. The existing `TrackGainState` explicitly describes gain on a fixed input stem: do not reuse it for DAW parameter state without a separately reviewed contract change. At dispatch, derive the relative bridge cut from that target minus the fresh observed FL dB. The observed state must match the last confirmed selected session state within the declared tolerances; do not silently adopt drift or redefine the baseline. Record both coordinates and the binding version. This avoids interpreting an absolute action as another cumulative cut.

The deterministic admission path checks the current revision, policy, protections and declared capabilities before dispatch. Actual capabilities are the intersection of policy and the tested bridge scope/range, including its 0.1–3 dB cut restriction. A no-change action performs no write. Gain increases, EQ and dynamics are unavailable in this increment. Recovery to an earlier value is a separately authorized operation and must not be advertised as a general gain-increase tool.

### Execution and evidence

1. Resolve the admitted absolute action and session binding; assign stable execution and command IDs.
2. Inspect identity, build, stopped transport, editing availability and expected starting readings. Recheck them on the script side immediately before writing.
3. Persist dispatch intent before sending. Reuse the same ID/payload for a retry; never convert a transport retry into a new action.
4. Dispatch one absolute normalized target using the calibrated conversion.
5. Record command, reply, before/after readings, conversion, bindings and achieved dB. A confirmed write means only that the DAW parameter moved as observed.

Distinguish refusal before writing, confirmed on-target change, confirmed off-target change, and uncertain outcome. An off-target change is not automatically accepted and blocks further dB actions under that conversion. Timeout, malformed matching reply, interruption or post-write exception keeps the operation unresolved. An inspect can reconcile the fader reading; it cannot certify unchanged routing, plugins or the complete DAW project. No new action follows until the controller explicitly resolves the outstanding operation.

The bridge's current append-only log and in-memory duplicate cache have limited durability. Do not claim crash-proof exactly-once execution; reload loses duplicate history. Stable IDs, pending intent and expected-state checks remain mandatory.

### Recovery

Capture the exact before normalized/dB pair before dispatch. Restoration may raise the fader and therefore needs a dedicated recovery capability with an exact recorded target, separate budget and the same identity/precondition/read-back protections. The calibration `probe_gain` restore is trial infrastructure, not a production rollback API. Until a general recovery operation exists and passes live tests, the operator restores and the system inspects; a refused/failed/uncertain restore ends unresolved. Restoring one fader alone does not prove full project restoration or accepted audio authority.

## Increment 2: manual export handoff and verification

After a confirmed action, create an immutable export request containing execution/candidate IDs, selected before revision, session binding, expected fader state, required affected stems and master, timeline, format and fresh output workspace. Store it as awaiting operator export. Preserve original projects and baseline exports. Save/export remain explicit operator tasks.

An operator supplies the manifest and files in a separate continuation step. No long-running hidden prompt or fake success from `RunRenderer`. Resume only after re-inspecting the session, checking the pending request and verifying immutable inputs and supplied output files. If identity/state continuity is not established, terminate unresolved and start a new controlled attempt.

Validate hashes, supported encoding, sample rate, channels, frame counts, timeline/alignment and complete affected-artifact coverage. Matching filenames and durations do not prove correct routing or provenance. Record the operator's export attestation and remaining limits. Where processing is nonlinear or stochastic, do not assume non-target stems are bit-identical or that the master is the sum of supplied stems. Declare routing/effect dependencies; unsupported or unknown dependencies require context or abstention.

Only validated exported artifacts can form a candidate. Re-analyze baseline and candidate under the same declared measurement profile, assess all predeclared criteria and guards, then accept or restore through the authority/journal workflow. A successful write, export, model rationale or listening preference alone does not confer acceptance.

FL exports may be stereo; the dist10 profile remains mono 44.1 kHz int16. Original stereo evidence is preserved. Any analysis conversion must be explicit, versioned and identically applied to before/candidate with its own provenance; no silent fold-down or profile widening. Keep stereo level guards separate from derived mono masking observations.

## Component boundaries and integration sequence

Proposed responsibility map (names provisional): core records hold immutable DAW session/action/export evidence; core ports describe inspect/apply/reconcile and exact-target recovery; an FL adapter owns MIDI, protocol, calibration and logs; a driver manages operator export continuation. The controller retains admission, budgets, cancellation, verification, state selection and acceptance. Runtime code never imports research scripts as its permanent implementation.

First implementation should cover session bindings, gain-coordinate resolution and the action result contract with simulated tests, without modifying the current coordinator. Then implement the FL transport adapter and live recovery trial. Finally add pending export records, verified artifact import and explicit controller continuation. A proposed ADR must define the new DAW boundary and relationship to the offline contracts before runtime integration. ADR-009 remains unchanged and FR-14 withdrawn until a separately reviewed scope decision changes them.

## Required checks before integration

- Absolute gain translation from a nonzero baseline, sequential actions and retries; external fader drift refused.
- Track/project/revision mismatch, unsupported build/conversion, protected targets and range/cut limits refused before a write.
- Pending-before-send, faithful replay, conflicting ID, reload, timeout and every post-write failure retain their stated outcomes.
- Exact-target recovery including upward restoration, uncertain restoration and exhausted recovery budget.
- Fader-only success cannot become candidate-ready; pending exports survive a controlled continuation without reapplying the action.
- Wrong/stale/missing exports and inconsistent state rejected; original artifacts preserved; all verification criteria and guards computed before acceptance.
- Architecture rules keep MIDI/filesystem dependencies outside core and decision-provider code.

## Approval and remaining decisions

Owner approved the two-increment direction; this document is the concrete design for review. The coordinate mapping, explicit pending-export continuation and dedicated recovery capability are proposed engineering decisions, not existing functionality. Exact record names and signatures belong to the implementation plan. No code, requirement status, accepted ADR, installed script or live DAW state changes in this documentation task.
