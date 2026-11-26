# HT01 calibration preparation plan — 2026-11-25

**Status:** Preparation only, for review. Route A is selected for preparation following the owner's instruction to proceed. No FL operation, file copying, Save As, MIDI connection, installation, probe write, provider call or live trial has been performed or authorized by this document.

**Goal:** Prepare one disposable HT01 Minimal House development case for a separately authorized fader calibration and, later, one bounded agent trial.

**Architecture:** Extend the existing calibration and FL adapter responsibility only where a measured blocker requires it. Preserve the old scope, frozen research scripts, evidence and runtime contracts. Do not add a general profile registry, new adapter or control loop for this preparation.

**Tech stack:** Existing Python protocol/WinMM bridge, FL Studio scripting and recorded calibration evaluator; documentation/JSON only in this increment.

**Spec:** [R6 readiness](2026-11-24-r6-readiness-and-trial-plan.md), the owner's 25 November HT01 context, and [fader conversion design](../../research/fl-studio-db-conversion-design.md). Starting software checkpoint: `7c0b781`.

## Global constraints

- HT01 is one Minimal House project at producer-reported 124 BPM, development partition only. Versions, conditions, regions and repeats are not independent musical cases.
- Originals, source samples, reference and ground truth remain unchanged. Reference/injection information stays outside runtime inputs and the provider packet.
- Preserve static processing, the kick's Soft Clipper -> Peak Controller chain and kick-driven bass EQ ducking. Exact link/filter settings remain unknown; no plugin parameter decoding is claimed.
- Current experimental masking policy is unchanged and uncalibrated. Fader calibration checks a control mapping, not musical quality or masking thresholds.
- Production supports only the recorded old build/project/insert. A new configuration reference does not create HT01 support.
- No extra recovery renders are requested for dataset documentation. A state-matched live baseline is a separate, later trial prerequisite.

## 1. Current evidence and case selection

Read-only inventory: [ht01-readiness-inventory-2026-11-25.json](../../research/ht01-readiness-inventory-2026-11-25.json). This inventory is planning/evaluation context, not runtime input.

All 35 selected files exist: six WAVs and an FLP for each of reference v002, corrected bass-up v002, kick-up v002, ducking-off v001 and reference-repeat v002. The repeat FLP is present; it was checked rather than assumed absent. All 24 files covered by the older manifest match it; new/unlisted files are not described as mismatches. Selected original hashes were reread after analysis and unchanged. Three named source sample files were found and hashed without editing them.

Selected starting source: `C:/uni/Bachelor-Thesis/test/HT01_reference_v001/HT01_bass_up_v002.flp`, SHA-256 `e6878bc3fe3d6a363938be95386611a5dec1f1f0f05a0cf82bac5253f6972567`. It is the corrected version, not the superseded render with Peak Controller disabled. The source's Bass/Kick WAVs are exact dual-mono PCM16 at 44,100 Hz, 1,365,677 frames. The master remains ordinary stereo, which is acceptable for native-channel level guards; P1 analyzes only its declared pair.

Fresh offline comparisons reproduce the supplied values: Bass increase +3.07562390774684 dB with residual-energy ratio 2.4967254196682453e-8; kick-up Bass is sample-identical; ducking-off Bass gain-fit residual 0.12120425177974996 distinguishes processing change from a scalar injection. Reference-repeat instrument stems are identical; its master differs by one PCM16 count. These are file comparisons, not controller results or perceptual validation. They are never a runtime correction target.

Owner confirmed on 25 November: Bass is mixer insert **5**, exact name **Bass**; each instrument has its own insert. This is an owner statement, not a live inspection. Sends, buses and controller links are not inferred from that statement.

| Field | Established now | Still needed |
| --- | --- | --- |
| Musical case | HT01, Minimal House,124 BPM, development | Nothing for selection |
| Source project | Corrected bass-up v002, hash above | Operator opens intended source later |
| Bass insert | 5, exact label Bass (owner) | Fresh device identity confirmation |
| Proposed disposable directory | `C:/uni/Bachelor-Thesis/test/HT01-live-calibration-v001/` was absent when checked | Creation/Save As is not done |
| Proposed copy file | `HT01-live-calibration-v001.flp` inside that directory | Operator Save As, then hash |
| Proposed metadata title | `HT01 Live Calibration v001` | Owner sets/accepts it; metadata is not file-path authentication |
| Starting normalized/dB fader | Unknown | Fresh inspect of the proposed copy |
| Starting application-gain coordinate | Undeclared | Declare consistently against that current baseline; do not insert the hidden injection amount |
| FL build/API | Old calibration recorded26.1.7 build5654/API45 | Fresh observation of HT01 session |
| Processing/routing | Production description and dedicated inserts | Confirm links, dependencies and no unintended edits |

## 2. Phase A — preparation completed here

- [x] Read the existing private dataset documentation before inventorying.
- [x] Recompute file identities, WAV geometry, peak/RMS, silence, dual-mono status and offline reference comparisons.
- [x] Rehash selected originals after measurements; no original modified.
- [x] Record owner-confirmed insert 5/Bass, without inventing current gain or live build.
- [x] Map scope restrictions and preserve the unknowns below.

## 3. Phase B — proposed copy and inspect-only session (pending separate authorization)

1. Owner opens the selected source and uses Save As into the new disposable location, preserving processing, links and faders. Do not overwrite the source or flatten/render anything.
2. Owner sets the proposed Project Info metadata title, or chooses another neutral exact title before code/plan scope is pinned. Record the source/copy paths, copy hash and FL build/API. A title match still does not authenticate the path.
3. Confirm insert 5 is named Bass, playback is stopped and no unrelated edits were introduced. Declare routing/dependencies explicitly.
4. Record the actual command/reply MIDI ports. The existing generic `inspect` command can inspect another title/insert; it does not require pretending the old calibrated profile covers HT01. Existing installed v1 may suffice for this inspection; it does not suffice for runtime recovery.
5. After inspect-only authorization, use the existing research client's generic inspect with explicit ports and a fresh log. Command template only, not executed here:

```powershell
uv run --locked --extra agent python scripts/fl_studio/bridge_client.py inspect --project-title "HT01 Live Calibration v001" --insert-index 5 --insert-name "Bass" --out "<CMD_PORT>" --in "<REPLY_PORT>" --timeout 5.0 --log "<NEW_INSPECT_LOG>"
```

6. Preserve the exact normalized and dB values from the fresh reply, identity/build and log hash. These are the restoration target for the later calibration. Do not borrow the trap trial's 0.76 value.
7. If the start is outside 0.62–0.86, stop and predeclare a suitable calibration range and points before any probes. Never move the musical fader merely to fit the old range. If the reading is zero/non-finite, identity mismatches or MIDI cleanup is unresolved, stop. No probe or gain command follows this inspect-only session.

## 4. Phase C — calibration-only software preparation (not implemented or authorized)

Current code will reject HT01 probe/production commands. The following restrictions must be addressed through a small separately reviewed tests-first change, rather than editing only a profile label:

| Location | Restriction / preparation requirement |
| --- | --- |
| `adapters/fl_studio/protocol.py` | `CALIBRATION_PLAN_ID`, project title, insert, sequence and conversion scope are pinned. Add a distinct explicitly bounded HT01 probe plan without altering the old plan. Do not enable HT01 production `set_gain_db`/`restore_gain` until its evidence is accepted. |
| `device/fl_studio/thesis_bridge_v2/thesis_bridge_protocol.py` | Byte-identical runtime protocol copy must be kept in sync and tested. |
| `device/fl_studio/thesis_bridge_v2/device_thesis_bridge.py` | Existing probe and restore handlers enforce protocol scope/build. Any necessary change stays inside this device/adapter boundary and preserves pre-write checks. |
| `scripts/fl_studio/bridge_client.py`, `evaluate_calibration.py`, `bridge_protocol.py` | These research artifacts are frozen to the old scope; their current calibrate/evaluate commands do not automatically calibrate HT01. Preserve them byte-identically. Plan the smallest non-frozen adapter/driver helper needed to send/evaluate the new pinned plan; no broad CLI or new adapter is assumed. |
| `adapters/fl_studio/profile.py` | `calibrated_profile` and `make_apply_command` only accept old scope. Add HT01 production support only after accepted evidence, with its own identity. |
| `adapters/fl_studio/controller.py` | Constructor checks exact supported profile. A new VersionRef alone cannot pass. Preserve cleanup block, journaling, duplicate and uncertain-effect handling. |
| `drivers/daw_runtime.py`, `drivers/agent_runtime.py` | Profile construction/preflight derive the old scope through `calibrated_profile`. Make explicit supported selection only after evidence; do not allow arbitrary configuration declarations. |
| Tests / snapshots | Pin the new plan/scope/criteria before live writes; keep old command bytes, unsupported-scope refusals and frozen script hashes. Test exact start restoration, replay/conflict, uncertain stop and retained cleanup ownership. |

No implementation design is finalized until the proposed title/build and fresh start/range are known. This is an actual readiness blocker; a full profile registry or generalized calibration service is unnecessary.

## 5. Phase D — proposed live calibration design (pending approval and software)

If the fresh start and required cut targets fit 0.62–0.86, propose reusing the already reviewed primary points and 27-step shuffled numerical order, with a **new** HT01 plan identity/digest bound to the exact title, insert 5/Bass, build and criteria. The old digest 8b4512b6… is not an HT01 registration. Wider points remain descriptive and do not expand the declared operating range. If another range is needed, its complete plan is reviewed and pinned first.

Acceptance proposed unchanged from the prior calibration: every stored normalized target within 1e-6; every primary point within 0.01 dB of H evaluated at read-back; repeat visits agree within 1e-6 dB. Restore to the exact fresh starting normalized reading, then confirm both coordinates exactly by final inspect. The old 3 discovery points are not newly counted as independent HT01 evidence. All commands, pending records, replies, replay flags, identity checks, stop decisions and hashes must be preserved. No provider calls or audio export/listening are part of calibration.

A calibration authorization must cover the pinned probe writes **and planned return to the exact start**, including its uncertainty/cleanup handling. Do not seek a new approval at every already-authorized step. Unknown outcomes stop further writes until the reviewed reconciliation/restore procedure permits them. An evaluator failure leaves HT01 unsupported; do not relax tolerance, reinterpret a wide point as a new range or automatically adopt a fallback conversion.

Installing or reloading a script is a separate explicit action with backups and hashes. The currently installed Thesis Bridge files are still v1 (`54fb39aa…`, `0220b3e9…`); runtime v2-named files are absent there. This was checked on disk only, not in FL memory. No installation occurred.

## 6. Phase E — later runtime trial (still blocked)

After calibration evidence and production-profile support are reviewed, prepare state-matched native baseline inputs from the eligible copy with explicit export selection/settings. No extra render is needed merely for this inventory. Preserve the stereo master; P1's mono/dual-mono restriction applies to the declared Bass/Kick pair, not all tracks. All current HT01 artifacts are stereo already.

Verification remains a separate decision. `AcceptanceCriteria` requires nonempty targets and improvements; guards alone cannot instantiate it. If a declared target already passes, initial verification legitimately ends without an AI call. Do not bypass this gate or manufacture a Bass-level objective from the private injection. Either obtain an actual producer-approved level objective for an explicitly labelled engineering trial, or separately plan support for a prepared experimental P1 proxy criterion; the current CLI allows level-only declared definitions. Neither is a perceptual guarantee.

Use neutral runtime names and a fresh root containing only selected current artifacts. The reference, condition labels, fitted injection gain and this inventory stay outside it. Freeze a diagnosis window in integer sample positions (seconds expressed as sample_index/44100); nominal bar positions need explicit sample alignment and are not silently rounded. Budget input as well as output tokens; calls times max_tokens only bounds requested output. No price is assumed here.

## Review focus and immediate handoff

1. Copy/title/insert: no original edit or claim that a metadata title authenticates an FLP path.
2. Start/range: no assumed fader value or adjustment just to fit the old range.
3. Wire/profile/device: no accidental enabling of HT01 writes before calibration acceptance, or reuse of frozen old evidence.
4. Restoration: exact before/after evidence, pending intent and no speculative retry after an unknown result.
5. Dataset boundary: one development case, isolated reference/labels and no gain-only claim for the ducking-off processing extension.

**Next step:** review this preparation, then separately authorize only Phase B (copy and inspect), if desired. No calibration writes or code implementation are authorized by the preparation instruction. Phase C is a subsequent tests-first handoff once actual live identity/start information exists.
