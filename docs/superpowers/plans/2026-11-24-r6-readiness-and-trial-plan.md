# R6 readiness assessment and first operator-assisted trial plan

**Status:** Proposed, planning only (2026-11-24). It authorizes **no** live provider call, FL Studio operation, MIDI connection, calibration, installation, saving, exporting, recovery or trial execution.

**Software checkpoint:** `7c0b781` (R5C C4 committed; working tree clean when this assessment began). Integrated evidence so far uses simulated FL/provider transports on synthetic numerical files only.

**Scope:** one small run through the existing `daw start` → manual export → `daw continue` path, using real development audio and complete evidence records. No new framework, adapter, UI or production feature, unless an actual blocker is separately approved.

**Draft run configuration:** [2026-11-24-r6-draft-run-config.json](2026-11-24-r6-draft-run-config.json). It is a review draft and deliberately not loadable: its `kind` is `…DRAFT`, and it contains `MISSING` markers.

## 1. Conclusion

**The trial is blocked.** No currently available project satisfies the calibrated DAW scope and the P1 input profile at the same time:

| Candidate | Calibrated FL scope | P1 input profile (Kick/Bass) | Result |
| --- | --- | --- | --- |
| `bass-bridge-test.flp`: a copy of `trap-baseline.flp`, title "Thesis Bass Bridge Test", insert 2 "Bass" | **Yes**: this is the reviewed calibration project | **No**: Kick, Bass and Master exports are non-identical stereo (L≠R, measured below). P1 accepts only mono or exact dual-mono. | Blocked by input profile |
| HT01 v002 (owner's Minimal House development project) | **No**: different project and title; its Bass insert index/name and fader baseline are unknown; no calibration exists | **Yes**: Kick and Bass are exact dual-mono PCM_16 44.1 kHz (measured) | Blocked by calibration |

**Preparation update (2026-11-25):** Route A / corrected HT01 bass-up v002 is selected for preparation. Live copying/inspection/calibration and execution remain separately gated. See [the HT01 preparation plan](2026-11-25-ht01-calibration-preparation.md). The alternatives below remain historical options:
- **Route A (recommended, if HT01 must be the material):** a separately authorized live calibration of an HT01 copy (its title and Bass insert). That has to be followed by a separately reviewed code change adding that calibrated profile, because `calibrated_profile` now declares exactly one scope. The existing profile is **not** relabelled or widened.
- **Route B:** use the calibrated trap copy, with an owner-declared representation that P1 accepts, such as FL's mono export. This changes the analysed representation (an explicit fold-down at export) for the baseline **and** every candidate export, P1 constrains the analyzed Kick/Bass pair; the native master need not be folded down merely to satisfy P1. The export contract must be checked separately. It also needs the trap sample licensing documented. It must not be done silently.

## 2. Readiness table

| Item | State | Evidence or gap |
| --- | --- | --- |
| Operator software (`start`/`continue`/`status`/`recover`) | **Ready** | `7c0b781`; clean full suite 7,382 passed, 4 skipped; simulated transports only |
| Run-file validation (C1) | **Ready** | `load_run_config`, provisional request and `DawAgentRuntime.check` run without MIDI or provider |
| Calibrated FL scope | **Ready only for** `bass-bridge-test.flp`, insert 2 "Bass", Producer Edition v26.1.7 build 5654 / API 45, normalized 0.62–0.86, conversion `fl-mixer-h125/v1` | `adapters/fl_studio/profile.py`; `fl-studio-db-conversion-design.md` |
| HT01 calibration | **Blocked** | No HT01 calibration exists. Owner confirmed insert5, exact name Bass on2026-11-25; fresh normalized/dB readings, build and copy identity remain unrecorded. |
| P1-eligible inputs, HT01 | **Ready** (Kick/Bass) | Exact L=R measured on bass-up, kick-up and reference v002 Kick/Bass (all 1,365,677 frames, PCM_16, 44.1 kHz); P1 profile v2 (exact dual-mono) |
| P1-eligible inputs, trap | **Blocked** | `trap-baseline_{Bass,Kick,Master}.wav` are stereo PCM_16, 302,400 frames, L≠R |
| Native export contract | **Missing** | Baseline must be a native export **from the calibrated session**, with the session's exact render spec, rate, start and frame count. The existing HT01/trap exports came from other project files (`HT01_*.flp`, `trap-baseline.flp`), not from a calibrated session. A fresh baseline export from the eligible session is required. |
| Device script on disk | **Missing (v2)** | Installed `Thesis Bridge/device_bridge.py` `54fb39aa…` and `bridge_protocol.py` `0220b3e9…` are the reviewed **v1 calibration** script. Runtime v2 (`device_thesis_bridge.py` `859d8376…`, `thesis_bridge_protocol.py` `68fb70dd…`) is not installed. v1 cannot perform `restore_gain` recovery. Installation is manual and needs owner action. |
| MIDI ports, FL MIDI settings | **Missing** | Port names for the tool configuration are not recorded for runtime use; set up by the owner |
| Live FL journal / run-store locations | **Missing** | No runtime tool configuration exists. A fresh run-store directory, FL journal path, input root (only the selected current files), export root and session key must be chosen. |
| Provider model and credential | **Missing** | Model ID and generation limits are owner choices; the credential goes in `ANTHROPIC_API_KEY` only, never in files |
| Producer intent | **Ready (owner statement)** | HT01: "give the kick space while retaining bass weight", Minimal House, 124 BPM (owner-provided, recorded 2026-11-22) |
| Verification criteria values | **Missing (owner decision)** | Structure proposed in §4; no values chosen; level-only criteria (D3) cannot express "kick space" directly |
| Diagnosis region/time range | **Missing (owner decision)** | "Bars 5–16" is an owner *suggestion*, not a frozen window; the band region is undecided |
| Source licensing, HT01 | **Partly documented** | Kick CC0 independently visible; Clap and Hi Hat CC0 producer-confirmed only; Bass original synthesis (producer-confirmed); Chords stem exactly silent, source unidentified. Download dates and archived licence pages are missing. |
| Source licensing, trap | **Missing** | `distorted-808-bass-kick-type_C_major.wav` and `trap-basic-kick.wav`: no source or licence record found in the repository docs |
| Hidden-reference boundary | **Needs a fresh input root** | HT01's folder also holds reference/injection-labelled files. The runtime input root must contain only the selected current files, under neutral names (decision R6-D5). |

## 3. Eligibility facts (measured read-only, 2026-11-24)

Read with `soundfile` and SHA-256 only; no file was written. HT01 hashes agree with the frozen private inventory `ht01-inventory-2026-11-22.json` (`783adb40…`).

**HT01 bass-up v002** (the proposed starting condition; the reference and injection labels stay outside runtime inputs):

| File | Format | Frames | SHA-256 |
| --- | --- | ---: | --- |
| `HT01_bass_up_v002_Bass.wav` | 44.1 kHz stereo PCM_16, L=R | 1,365,677 | `eb8abcbe921c572ee685703390dcd1f627aa41e85f29b8d2edc87b50c86514b9` |
| `HT01_bass_up_v002_Kick.wav` | 44.1 kHz stereo PCM_16, L=R | 1,365,677 | `0b9e6ad9a6c679a0aeb7e972fa1153fc73cb99b9c855adfe156bce526d85e717` |
| `HT01_bass_up_v002_Clap.wav` | 44.1 kHz stereo PCM_16 | 1,365,677 | `566e87a9ab000624da43e5f913b8d3249d7af9ae96262b7251431f0e4668a096` |
| `HT01_bass_up_v002_Hi Hat.wav` | 44.1 kHz stereo PCM_16, L≠R | 1,365,677 | `d28e79b6d037e40e8831300789a68f3f75b29423b0f2d5cbc5b9d4f99cf2be66` |
| `HT01_bass_up_v002_Chords.wav` | 44.1 kHz stereo PCM_16, exactly silent | 1,365,677 | `3266fd97b793b56efcdd602833f0c7b630ee297ef41bbbcbeeefc76ed7f31c1b` |
| `HT01_bass_up_v002_Master.wav` | 44.1 kHz stereo PCM_16, L≠R | 1,365,677 | `18aa14cade1142592317d0aece60164f95903a5da2019c5d39158772bceff079` |
| `HT01_bass_up_v002.flp` | project | — | `e6878bc3fe3d6a363938be95386611a5dec1f1f0f05a0cf82bac5253f6972567` |

- **Common export interval:** 1,365,677 frames = 30.9677324263 s at 44.1 kHz (whole-clip exports; Playlist selection and export origin not independently recovered).
- **The kick-up v002 condition** (an alternative) has the same format; its hashes are in the draft configuration.
- **Trap (calibrated project):** `trap-baseline_Bass.wav` `74c82cd4…`, `trap-baseline_Kick.wav` `e445daeb…` and `trap-baseline_Master.wav` `c3badaf0…` are each 302,400 frames, stereo PCM_16, L≠R. `bass-bridge-test.flp` `71a6c6a9…` (48,215 bytes); `trap-baseline.flp` `ac1880d9…`.

**Licensing.** Owner declarations are distinguished from independently verified evidence in the HT01 audit. Nothing here is a legal opinion.

## 4. Draft run configuration (summary; see the JSON draft)

Using HT01 bass-up v002 under Route A. Every value marked MISSING must come from the owner or the calibration record. Nothing is tuned to reach the write branch; abstention or incomplete coverage is a valid result.

- **Producer intent and roles:** owner statement "give the kick space while retaining bass weight". Owner-declared roles: kick, bass, clap, hi-hat, chords (silent). No preference beyond the statement.
- **Permitted action:** `set_track_gain` on Bass only. The application-gain permission range is **MISSING**. Map it through the declared baseline and `daw_gain_bounds`; do not compare application dB directly with normalized or physical fader-dB endpoints. Kick is protected.
- **Experimental policy:** `p1.experimental_screen@1` (40 / 0.02 / 0.02 / 100 / 0.25 / 4), **uncalibrated engineering configuration**, unchanged.
- **P1 preparation:**
  - profile `p1.dist10_mono_pcm16_44100@2` (exact dual-mono);
  - one job: masker Bass → target Kick;
  - the time range and band region are **MISSING** (owner decision, not chosen here);
  - alignment declared as the common export selection.
- **Verification criteria** (level-only scope D3; all values MISSING). Proposed structure, with rationale:
  - **Master sample-peak absolute guard:** no clipping risk introduced. The value needs owner choice.
  - **Bass RMS delta guard against the initial state:** "retain bass weight"; bounds how far a cut may go. The value needs owner choice.
  - **Kick RMS delta guard:** Kick must be unchanged, as a regression check, because only Bass may move.
  - **Target/improvement:** **open (R6-D3).** No level metric measures "kick space". A Bass-level target would encode the action and risk mirroring the hidden +3 dB injection. Options: (a) choose a declared proxy with written rationale; (b) accept that the run tests the safe engineering path, and that acceptance means only "declared criteria passed".
- **Budgets** (engineering proposals, for the owner to confirm): decision calls 3; attempts 2; render calls 0; recovery operations 7 (a guard-rejected attempt recorded 2 operations, restoration plus resolution; this reserves capacity for two rejected attempts plus margin); DAW writes 2; decision retries 1. Elapsed seconds are **MISSING**; the operator pause is not charged. Decision calls × `max_tokens` bounds requested output tokens only; the cost budget must also cover input tokens and the selected provider pricing. No price is assumed here.
- **Provider:** model ID **MISSING** (owner selects); temperature 0.0 proposed; `max_tokens`, timeout and request/response byte limits **MISSING**. The credential is supplied only via `ANTHROPIC_API_KEY`.
- **Locations:** fresh run-store directory, FL journal, input root and export root are all **MISSING**. They are new, empty, local, non-synced folders; the run store lies outside every audio root.

## 5. Operator procedure (after every blocker is cleared and execution is authorized)

**Prerequisites.** These are manual owner actions, not done by tooling:
1. Route A calibration (or the Route B decision) is completed and reviewed, and any profile code change is committed with tests.
2. Device script v2 is installed manually, and its hashes are recorded and match the repository files. FL Studio's MIDI settings and the loopMIDI ports are configured.
3. In FL Studio: the calibrated project copy is open, the transport stopped, and no unsaved edits beyond those recorded.
4. A fresh input root holds only the selected current files (neutral names, hashes re-verified). Fresh store, journal and export locations exist.
5. Pin and record:
   - `git rev-parse HEAD` (clean tree);
   - the SHA-256 of `uv.lock`, the tool configuration and the run file;
   - the FL build string;
   - the device script hashes.
6. **Offline validation (no MIDI, no provider):** load the run file with `load_run_config`, build `provisional_request`, and run `DawAgentRuntime.check`. Any refusal stops preparation.
7. Set `ANTHROPIC_API_KEY` in the shell only; never write it to a file or transcript.

**Start.** The first attempt has no confirmation flag:

```powershell
uv run --locked --extra agent autonomous-mixing daw start --config <TOOL.json> --run-config <RUN.json> --run-id <NEW-ID>
```

- **Expected exit 4:** "first session … reads normalized X, dB Y". This writes only an inspect-ID claim to the FL journal.
- **Operator check:** X and Y are the intended starting fader, and they equal the run file's declared `confirmed_fader` exactly. If the declaration differs, stop and establish that the intended project/fader/processing and baseline exports match before making and recording a new declaration. Do not edit the declaration merely to pass the gate.
- **Then repeat with** `--accept-current-fader X Y`, using the exact printed text.

**At suspension** (exit 0, `SUSPENDED`):
- Save the full stdout, and the output of `daw status --config <TOOL.json> --run-id <ID> --json`.
- Check in FL Studio that the Bass fader shows the written reading and nothing else changed.
- Export exactly the listed files, with the listed format, start, frame count and render settings, into the printed workspace. Do not rename the files, add other files or delete `export-request.json`.
- Saving the project is optional and manual. If saving, use Save As to a new copy and record its hash.

**Continue:**

```powershell
uv run --locked --extra agent autonomous-mixing daw continue --config <TOOL.json> --run-id <ID> --export-id <EXPORT-ID> --operator <NAME> --attestation "<what was exported>"
```

Repeat export → continue for each new suspension, within budgets.

**Stop conditions.** On any of the following, stop and take no further step:
- any `REFUSED` or `STOPPED` output, or exit 2;
- an FL dialog, transport movement or fader state differing from the printed reading;
- an unexpected device or provider behaviour;
- any doubt about which files were exported.

Then run `daw status` and the read-only `daw recover --config … --run-id …` view. **Performing a recovery operation needs separate authorization.** Never edit stores or journals by hand.

## 6. Evidence record and report template

**Pinned identities:** git commit, `uv.lock` SHA-256, run-file and tool-configuration SHA-256, device script hashes, FL build, OS, and the provider model ID as recorded in the run binding.

**Evidence:**
- **Audio and files:** input hashes, before and after; every exported file's hash (from the import evidence).
- **Records:** the run store, decision-audit store (request/response records; credential excluded by design), plans and evidence directories, and FL journal. These are copied read-only after the run into a private evidence bundle with a manifest of hashes.
- **Outputs:** CLI stdout/stderr transcripts and `status --json` snapshots.

**Report sections:**
1. Purpose, development partition and authorizations.
2. Pinned identities.
3. Inputs and provenance status.
4. Configuration, with its uncalibrated labels.
5. Timeline: start, first-session reading, each decision (diagnosis coverage and candidates, proposal or abstention, admission result), each write (before/after readings), each export and import, verification per condition, acceptance or recovery, termination.
6. Budget use against limits, and timing (excluding operator pauses).
7. Refusals and anomalies.
8. Engineering conclusion.
9. Limitations.
10. Optional listening notes.

**Interpretation rules:**
- **Engineering completion is reported separately from musical quality.** "Accepted" means only that the declared engineering criteria passed on the recorded measurements. It does not establish audible masking reduction, better mixing or model competence.
- **Listening notes,** if any, are labelled informal, unblinded, by whom, and on what playback.
- **HT01 results remain development-partition evidence,** not held-out evaluation.

## 7. Decisions and authorizations needed before execution

| ID | Decision or authorization |
| --- | --- |
| R6-D1 | Choose Route A (calibrate an HT01 copy; then a reviewed profile code change) or Route B (trap project with a declared mono export representation, plus licensing). |
| R6-D2 | Authorize the live calibration session (Route A), including who operates FL Studio, when, and on which copy. |
| R6-D3 | Verification criteria values and the target/proxy question (§4). |
| R6-D4 | Diagnosis time range and band region. |
| R6-D5 | Input-root assembly: byte-identical copies of the selected current files under neutral names (no reference or injection labels), hash-verified. Originals untouched. |
| R6-D6 | Budgets, elapsed-time limit, provider model and generation limits, cost ceiling. |
| R6-D7 | Manual installation of device script v2 by the owner; record its hashes. |
| R6-D8 | Execution authorization for the live run (FL Studio operation, MIDI, provider calls), and who operates. |
| R6-D9 | Whether any recovery operation may be performed if the run stops, or whether to stop at the read-only view. |
| R6-D10 | Whether informal listening notes are collected, and how they are labelled. |

No calibration, threshold change, installation or trial execution is authorized by this plan.


## 8. HT01 preparation update — 2026-11-25

Owner supplied the fuller House dataset context and instructed preparation to start. [The dated inventory](../../research/ht01-readiness-inventory-2026-11-25.json) recomputes35 selected artifacts and source identities read-only. All24 artifacts covered by the older manifest match it; ducking-off and newly found/unlisted files extend the record without becoming new independent musical cases. Reference-repeat FLP is present. Selected originals were rehashed unchanged.

HT01 is one Minimal House development case at124 BPM. Use corrected bass-up v002 as the proposed gain-only starting condition; preserve enabled ducking/static processing. Ducking-off v001 is a separate processing challenge, not another gain injection or evidence of successful gain correction. Reference approval/listening statements remain informal unblinded producer evidence. Source licence statuses remain as supplied, not newly independently verified.

Bass is insert5, exact name Bass, confirmed by the owner in this chat. One instrument per insert does not establish absence of sends, buses or controller dependencies. No fresh fader, build, project title or routing is inferred.

The current configuration is still incomplete: targets and improvements are required by the existing criteria constructor; guards alone are not executable. No hidden injection amount becomes a target. The first live fader may be outside the old0.62–0.86 range, and the existing probe/client/evaluator scope also rejects HT01. These must be planned before any live writes, not solved by relabelling the old profile or adjusting the music to fit it.

[The HT01 calibration preparation plan](2026-11-25-ht01-calibration-preparation.md) records the proposed disposable location/title, an inspect-only next phase, the exact restoration target still to be measured, the code-impact map and the later calibration/trial gates. No copy, Save As, MIDI access, installation, calibration, provider call, audio change or production-code change took place.
