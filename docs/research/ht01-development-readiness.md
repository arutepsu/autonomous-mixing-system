# HT01 development readiness audit

Recorded 22 November 2026. **HT01 v002 is a documented development case with two measured gain-imbalance conditions and a separate processing-extension case.** Current measurements reproduce the supplied record. This is an inventory/technical check, not an autonomous correction trial, calibration result or perceptual-masking validation.

The selected Kick and Bass exports are stereo containers with **sample-identical left and right channels in all five selected conditions**. A separately reviewed lossless dual-mono input-preparation path is therefore possible without another musical render. R1 still rejects these stereo files directly; no conversion or profile change was made.

## Evidence and scope

Originals: `C:/uni/Bachelor-Thesis/test/HT01_reference_v001/`; source samples: `C:/uni/Bachelor-Thesis/test/house_samples/`. Folder naming does not select v001: v002 is the producer-selected reference.

Private evidence: `C:/uni/Bachelor-Thesis/test/ht01-development-readiness/`:

- `ht01-inventory-2026-11-22.json`: complete actual-file inventory, SHA-256, bytes, WAV formats/levels/silence, condition comparisons, original-manifest comparison and original-preservation checks. SHA-256: `783adb405ae2bbf97b05648bcfd410cf000e7166877699e7b97b462f256b1b3e`.
- `provenance-2026-11-22.json`: source identities, license-evidence status, owner statements and missing fields.
- `verification-2026-11-22.json`: independent integer-square-sum level check over all 30 selected WAVs. Maximum RMS disagreement: 3.55e-15 dB.
- `reproduce_inventory.py`: private local reproduction command below; no runtime registration.
- `context/`: byte-identical copies of the supplied pre-extension README, manifest and latest owner context. Original documentation is unchanged.

The existing README/manifest were read from the owner-specified ChatGPT project mirror. The newer owner context establishes the ducking-off extension, intended processing, licensing evidence and listening judgment. The thesis source of truth at `C:/goconut/AI_Mixing_Agent_Project_Source_of_Truth.docx` was read without editing; its constrained-agent/tool-loop and reference-independent validation boundaries apply. Context file hashes are in the inventory.

The owner explicitly requested current decoded levels and comparisons. These technical checks extend the earlier header-only D1 planning check; they do not activate stereo P1 analysis. No original FLP, WAV, source sample or source document was edited. No FL Studio/provider call, audio output, resampling, channel averaging, quantization or correction occurred.

## Measured inventory

| Category | WAVs | FLPs | Use |
| --- | ---: | ---: | --- |
| Selected reference, gain variants, processing extension and reference repeat | 30 | 5 | Development only; repeat is a technical check |
| Historical v001 reference/gain experiments | 18 | 3 | Earlier development history, excluded from selected v002 comparisons |
| Backup directory | 0 | 4 | Saved-project history, not new cases |
| Original source samples | 3 | 0 | Provenance; not additional mix cases |
| Total | 51 | 12 | 63 original files |

All **30 selected WAVs** are WAV, 44,100 Hz, stereo, PCM_16, **1,365,677 frames / 30.9677324263 seconds**. Every selected condition has Bass, Chords, Clap, Hi Hat, Kick and Master. All five selected FLPs exist, including `HT01_reference_v002_repeat.flp`; its presence was checked, not assumed.

The three historical v001 WAV groups each contain six exports of 1,707,097 frames / 38.7096825397 seconds. They are not interchangeable with v002. The ducking-off prefix ends in v001 but belongs to the explicitly selected extension and matches v002 length; version text alone does not classify it as a legacy render.

**31/31 identities recorded in the earlier manifest match current files**, including its WAV/project/source entries. The corrected bass-up export is the current selected artifact; the earlier disabled-controller export is not treated as an unresolved defect. Its former bytes/retention cannot be reconstructed from reused filenames. The new inventory separately includes the ducking-off files absent from the old manifest.

All 63 original hashes and the source-folder file list matched before/after the audit. Two complete inventory runs produced byte-identical JSON. This is a snapshot check, not a filesystem lock or a guarantee against future changes.

## Recomputed reference levels and activity

Levels are decoded whole-file pooled-channel RMS and maximum absolute sample level, not LUFS, true peak or a stereo average waveform. Null dB in the JSON means exact silence where labelled; silence is not assigned a fabricated finite level.

| Reference role | Sample peak dBFS | RMS dBFS | First activity above -60 dBFS, file-relative | Left equals right exactly? |
| --- | ---: | ---: | ---: | --- |
| Bass | -15.767938 | -25.985352 | 7.741950 s | Yes |
| Chords | Undefined: exact silence | Undefined: exact silence | None | Yes |
| Clap | -9.431540 | -32.543413 | 0.483900 s | Yes |
| Hi Hat | -13.381019 | -44.756548 | 15.725828 s | No |
| Kick | -8.134317 | -19.150823 | 0.000091 s, frame 4 | Yes |
| Master | -1.981848 | -18.194982 | 0.000091 s, frame 4 | No |

The first-activity threshold is a descriptive inventory convention, not a qualification gate. The earlier approximate kick onset of zero is consistent with frame 4 at this resolution. Chords are exactly silent in every selected condition.

No selected sample reaches magnitude 32767/32768 or higher. This includes separate positive-maximum/negative-full-scale counts in the inventory; it does not certify intersample/true peaks. Kick-up's sample peak leaves less headroom than the reference, but no acceptance limit is chosen here.

## Recomputed condition comparisons

Comparisons use both stereo channels at the same sample indices. Gain is a pooled-channel least-squares scalar fit; residual ratio is remaining energy divided by candidate energy. No lag search or mono fold-down was used. The previously supplied zero-lag finding remains prior evidence, not a rerun claim.

| Condition | Changed stem | Gain fit relative to reference | Stem RMS change | Gain-fit residual ratio | Master sample peak |
| --- | --- | ---: | ---: | ---: | ---: |
| Reference v002 | Baseline | 0 dB | 0 dB | 0 | -1.981848 dBFS |
| Bass-up v002 | Bass | +3.075623908 dB | +3.075624016 dB | 2.4967254e-8 | -1.646088 dBFS |
| Kick-up v002 | Kick | +2.954614907 dB | +2.954614927 dB | 4.5307664e-9 | -0.473363 dBFS |
| Ducking-off v001 extension | Bass | +0.699589861 dB | +1.260710391 dB | 0.121204252 | -2.284780 dBFS |
| Reference repeat | Instruments unchanged | 0 dB for sounding stems | 0 dB for instruments | 0 for instruments | -1.981848 dBFS |

- Bass-up: Kick, Clap, Hi Hat and silent Chords are decoded sample-identical to reference.
- Kick-up: Bass, Clap, Hi Hat and silent Chords are decoded sample-identical to reference.
- Ducking-off: Kick, Clap, Hi Hat and silent Chords are decoded sample-identical to reference. Bass is not a simple scaled copy; the substantial fit residual corroborates a processing-change case, without identifying exact plugin parameters.
- Reference repeat: all five instrument stems are decoded sample-identical. Master differs by at most **one PCM16 count**. This supports that repeat, not universal deterministic rendering.
- In each selected condition, Master differs from the sum of exported instrument stems by at most **two PCM16 counts / 6.103515625e-5** normalized amplitude. This is descriptive evidence, not a runtime assumption that every master must equal its stems.

File hashes may differ with WAV title metadata even when samples match. Exact decoded identity is reported separately; no reason for a differing hash is assumed without chunk-level evidence.

## Producer statements and evidence limits

Owner-provided facts: HT01 is Minimal House at **124 BPM**, intended to give the kick space while retaining bass weight. The measured duration is consistent with 16 bars in 4/4 at 124 BPM within one sample; this does not independently recover the Playlist selection, export origin or meter. Bars 5–16 remain a suggested region, not a frozen runtime window.

The producer reports an original 3x Osc bass patch/line; kick Fruity Soft Clipper followed by Fruity Peak Controller; and kick-driven reduction of a bass EQ band around 136 Hz using Fruity Parametric EQ 2. This is frequency-specific ducking, not whole-bass volume modulation. Earlier screenshots were described as supporting effect names/order; those screenshots were not re-inspected in this audit. Exact controller/link mapping, bandwidth, gain range and filter types/slopes remain unknown. The description of bass filtering as "20–50" and clap/hat filtering below about 200 Hz is not promoted to exact settings.

The owner confirms intended separate +3 dB mixer-fader injections, with effects held fixed. Absolute fader readings were not captured; measured audio gains above remain separate facts. The Bass staying identical in kick-up supports unchanged bass output/ducking in that export comparison, not independent decoding of every controller setting.

The owner identified and regenerated an earlier bass-up export whose Peak Controller was accidentally off, reusing names. Current measurements and hashes corroborate the corrected gain condition. No FLP plugin/link/fader values were decoded here; project hashes establish identity only.

Producer screening: reference preferred; both gain variants have unwanted balance. For ducking-off, reference has a clearer kick but the difference was somewhat difficult to determine. These are informal unblinded development judgments. Playback conditions, verified level matching and controlled independent listening are absent. The prior 60–100/100–180/180–300/300–1000 Hz exploratory changes were supplied as context and not rerun; they neither calibrate masking nor prove exact 136 Hz modulation.

Gain-only tools can act on the two gain-imbalance conditions. They cannot be claimed to restore a disabled dynamic-EQ process exactly. No gain-only correction of ducking-off has been demonstrated; retain it as a separate processing extension, not a primary recoverability success case.

## Source provenance

| Role | Source | Evidence status |
| --- | --- | --- |
| Kick | Johnnie_Holiday, [sound 666812](https://freesound.org/people/Johnnie_Holiday/sounds/666812/) | CC0 label independently visible in this audit's retrieved page content; tool labels crawl six months old. Uploader describes Spire synthesis, F, 128 BPM; those are sample facts, not HT01 tempo. |
| Clap | pomeroyjoshua, [sound 513421](https://freesound.org/people/pomeroyjoshua/sounds/513421/) | Producer-confirmed CC0. Independent retrieval unavailable again; not evidence that the page is broken. |
| Hi Hat role | jsa.dj, [Cymbal sound 803732](https://freesound.org/people/jsa.dj/sounds/803732/) | Producer-confirmed CC0. Correct account is jsa.dj; existing filename removes punctuation. Independent retrieval unavailable. |
| Bass | Producer's original 3x Osc patch and line | Producer-confirmed original synthesis; patch parameters not independently decoded. |
| Chords | Selected rendered stem exactly silent | Instrument/source identity not established; document it before using a sounding version. |

The original kick source is FLOAT stereo, clap is PCM_16 mono, and cymbal is PCM_16 stereo, all 44.1 kHz. These source samples are not the exported analysis inputs and need not share their encoding. Actual source hashes match the old manifest; individual hashes and URLs are retained in the private evidence.

Download dates and archived clap/cymbal license pages are still missing. Filesystem timestamps do not establish download dates. No sample replacement is requested on the available record. These are provenance observations and owner confirmations, not a blanket rights warranty or legal opinion.

## Partition and runtime boundary

HT01 is explicitly **development/calibration only**. All versions, sections, modified conditions and repeats stay in that partition. Repeats are technical checks, not independent musical projects. Existing inspected conditions and producer judgments are not unseen held-out evidence.

The inventory, source README/context, reference audio, injection labels/amounts and manual judgments are private development/evaluation material. None is registered as a runtime root or sent to a controller/provider. A later runtime input must expose only its selected permitted current artifacts, opaque identifiers, declared musical context and prospective operational objectives/guards. Reference differences are useful for this audit and later evaluation; they must not become the runtime success oracle. An intended +3 dB injection does not imply exactly -3 dB is the only acceptable action.

## Input-profile finding and smallest next proposal

R1's existing native mono 44.1 kHz PCM_16 boundary correctly refuses all 30 selected stereo-container exports. Sample peak/RMS inventory remains meaningful on native stereo; no P1 measurement or qualified diagnosis was attempted.

**Measured opportunity:** Kick and Bass are exactly dual mono in every selected condition. A separate reviewed preparation task could verify 44.1 kHz PCM_16, require exact L==R for every frame, and create separately identified mono analysis copies with those unchanged int16 counts. It would do no averaging, resampling or requantization, retain source/derived hashes and timing, and refuse non-identical channels. Original stereo exports and the runtime profile would remain untouched; provenance and later current-state binding still require explicit review. This is a proposal only: no mono file or adapter was created here.

Hat and Master are genuinely non-identical stereo in all selected conditions. Do not extend that lossless proposal to them or silently discard a channel. Native stereo masters remain appropriate for level guards. The existing trap-pilot averaging recipe is not authorization to convert HT01.

Image-Line's current [export manual](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/fformats_save_export.htm) documents a Channels control including Mono (merged) and left/right-only output. Its availability in this installed dialog was not inspected. Producer-created mono analysis exports would be another explicitly declared representation, with consequences for stereo claims; they are not required to finish this documentation audit. No extra recovery or musical renders are requested.

## Small operational-policy proposal and remaining work

Start the eventual gain-only development trial with the selected bass-up and kick-up conditions, preserving the reference/labels outside runtime inputs. The producer goal is kick space with bass weight. A prospective policy must state its allowed action, exact technical objective, activity/low-energy qualification, native master/stem guards, budget and uncertainty wording before any agent run. This audit selects no values, thresholds or analysis window.

P1 remains descriptive/uncalibrated. Numerical reproduction/quantization sensitivity, a justified deadband and qualification regime, and evidence-sufficiency choices remain unresolved. A small approved experimental model-risk heuristic could support a bounded thesis claim; neither these gain injections nor informal preference establish a perceptual detector. The optional six-project/three-listener/grid proposal remains unadopted. No paid panel is assumed.

Ordered checklist:

1. Keep the original exports/projects and the frozen audit snapshot; no re-render is needed for dataset documentation.
2. Review the narrow lossless dual-mono analysis-copy proposal for Kick/Bass if P1 development analysis is next. Keep all non-identical stereo and masters native. Do not change R1 validation implicitly.
3. Fill exact export selection/settings, fader readings and controller details when available from existing records; otherwise keep them explicitly unknown. No redundant recovery render is needed to repair documentation.
4. Retain source/license evidence as it becomes available; preserve producer-confirmed versus independently visible distinctions.
5. Before execution, approve a limited operational policy and region, numerical/qualification evidence, and the real qualified-support/metric path. R2B and R5 are not activated by this audit.

## Reproduction and verification

From the repository environment, with the source locations retained:

```powershell
uv run --locked --extra agent python C:/uni/Bachelor-Thesis/test/ht01-development-readiness/reproduce_inventory.py
```

The reproduction script uses the existing export checker's `describe` function for headers/hash/profile facts and computes the requested technical statistics without using its mono-correlation helper. It refuses to overwrite differing existing inventory evidence. The output was reproduced byte-for-byte in two runs, and all 30 selected RMS values were independently checked using integer square sums. Original audio/project hashes were checked before and after each inventory run. No software behavior was changed, so no full product-suite rerun is claimed for this documentation/data audit.


## Subsequent input-path decision and descriptive run (2026-11-22)

The owner superseded the separate mono-copy proposal above with an explicit exact-dual-mono extension of the existing reader/preparer/analyzer. It preserves original stereo artifacts and selects an unchanged channel only after whole-clip exact equality. The extension is implemented and tested, uncommitted for review; no derived audio was created. The later owner-authorized [descriptive P1 run](ht01-p1-descriptive-measurements.md) now measures the selected Kick/Bass in both directions across all five conditions. Earlier statements that P1 had not been run describe the D1 audit's scope, not the later state. Hat/Master remain unsupported as P1 stereo inputs. Qualification and positive diagnosis remain unavailable.
