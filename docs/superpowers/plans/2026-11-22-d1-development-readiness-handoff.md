# D1 first development-project readiness audit — handoff

**Goal:** Make one owner-created FL Studio project ready for a small, explicitly limited development study before approving any P1 qualification or diagnosis thresholds.

**Scope:** Read-only inventory and evidence review, then a concise report and concrete operator checklist. No implementation, provider/FL Studio calls, audio conversion, threshold fitting, calibration run, automatic source adoption or changes to original exports/projects. Preserve the uncommitted R2A work. Do not expand this into the optional six-project/three-listener/grid study.

**Context:** R4 is committed at bb3e0a5. R2A is implemented and uncommitted: it truthfully reports incomplete P1 and no candidates. R2B positive diagnosis requires approved numerical/qualification policy and genuine qualified support/metrics. The owner is developing music in FL Studio; dataset work can now replace further placeholder infrastructure.

**References:** accepted diagnosis/decision plan and P1 method specification, especially sections 6 and 8; calibration registration revision 3 is optional/unapproved, not authority to execute its recipes or select its numerical values. Existing dataset plan quantities are provisional, not a mandatory workload.

## Facts already checked (2026-11-22)

- Development folder: C:/uni/Bachelor-Thesis/test/HT01_reference_v001.
- Headers of reference v002 Kick, Bass and Master; repeat-reference Kick; bass-up v002 Bass; kick-up v002 Kick all report 44,100 Hz, 2 channels, PCM_16 and 1,365,677 frames. This is a six-file header check, not a complete inventory or alignment/signal check.
- No non-WAV/non-FLP file was listed directly in that folder. No manifest, role/rights declaration or export-settings record was found there by that check; do not infer they are absent elsewhere without searching the relevant documentation.
- v001/v002 and repeat/modified exports coexist. Filenames suggesting reference/injection roles are private development/evaluation metadata, never runtime decision inputs.
- No audio samples were evaluated or sent to a provider in this preliminary check. Shared frame count proves neither common origin nor physical alignment.

## Task 1 — Inventory one underlying project

- Record the actual files, byte sizes, SHA-256, container/subtype, sample rate, channels and frame count for the intended v002 set and its repeat. Identify earlier versions separately; do not combine them merely because role names match.
- Locate existing HT01 notes/manifests/logs before requesting information. Use the existing shared verifier/export-check capabilities where sufficient; no new framework or experimental runner.
- Record what is known versus unknown about roles, timeline start/end, export selection, tempo and render settings. Equal frames alone are not verified alignment.
- Keep originals read-only. New metadata/report artifacts go to a separate analysis directory. Do not alter, rename or re-export any WAV/FLP.

## Task 2 — Record data provenance and allocation

- Ask only for missing owner facts: source/ownership or license record for each supplied sample/plugin source where relevant, and whether intended uses are authorized. Do not infer rights from file names, downloadability or the assertion that the FL project was composed by the owner. Mark unresolved rights as unknown; do not send unknown material to an AI provider or include it in a research run by default. This audit does not issue a legal opinion.
- Propose HT01 as development-only if the owner chooses it. All variants/repeats of that musical project share that allocation. Prior manual choices or inspected outcomes prevent calling those same cases unseen held-out evidence.
- Keep reference exports, injection descriptions, condition-bearing names and manual targets in private evaluation metadata. A later runtime manifest contains only the selected permitted inputs with opaque IDs, declared roles/intent and verified operational metadata. Do not copy the reference/variant directory listing into an agent packet.
- Do not fabricate missing producer intent, listening judgments, render settings or provenance. One complete documented project is the immediate target; no panel or total corpus size is adopted here.

## Task 3 — Resolve the stereo/profile gap explicitly

- Report that the current R1 native mono 44.1 kHz PCM_16 profile refuses these stereo headers. Do not invoke the masking analyzer and treat a failure as a zero/healthy result.
- Check existing export procedure and what the installed FL Studio renderer supports before giving exact UI instructions. Do not promise a mono-file export option without verification.
- Present the smallest feasible input path for owner decision: separately retained producer-created mono-compatible analysis exports, or a separately reviewed explicit stereo-derived profile. Any conversion/profile extension requires its own recipe, hashes, channel/phase implications and validation before implementation/use.
- Do not average channels, discard a channel, change rounding, accept dual-mono containers implicitly, quantize float/24-bit sources, or weaken R1 validation in this task. Masters may remain native stereo for level guards; masking-profile eligibility is a separate question.
- The earlier trap pilot's conversion and low-cancellation finding are case-specific and do not authorize conversion of HT01.

## Task 4 — Feasible development checks and policy proposal

- After rights and input profile are resolved, list the minimum next checks: original hashes, verified headers, declared timeline plus appropriate alignment evidence, baseline/repeat reproducibility, target/masker level effects, and native master guards. Separate planned checks from those actually run.
- A permitted local repeat check may compare decoded samples; differing WAV metadata hashes alone do not prove different audio. Do not claim perceptual equivalence from matching levels or near-zero residuals. If rights/profile remain unknown, stop the research analysis at the inventory and state the blocker.
- Prepare a small operational-policy proposal tied to an explicit producer goal and development evidence, clearly labelled experimental model-risk semantics rather than perceptual masking detection. List the missing numerical reproduction/quantization evidence and qualification/sufficiency choices; do not set or fit their values here.
- Numerical controls may be planned independently of music collection, using the accepted method's questions. The optional registration's particular recipes, grid values, listener counts and selection rules remain unapproved unless separately adopted. No empirical maximum (including 0.005552 dB) becomes a default deadband by implication.
- Owner preference can be recorded as an informal development judgment with its limitations. Independent volunteer listening may be supplementary; paid/professional listeners are not assumed. No claim beyond available evidence.

## Deliverable and stopping point

Write docs/research/ht01-development-readiness.md with: file inventory/evidence locations; known/unknown provenance; proposed split; profile mismatch; alignment/repeat status; and a short ordered operator checklist. Append a research-log entry for checks actually performed. Preserve all R2A changes and leave new work uncommitted.

End with the smallest unresolved owner decisions, if any. Do not implement R2B, run the optional calibration study, activate a policy or wire R5. Readiness auditing is not a calibration result. Only after the input/provenance gap and limited operational policy are explicitly resolved should qualified-support implementation be planned.
