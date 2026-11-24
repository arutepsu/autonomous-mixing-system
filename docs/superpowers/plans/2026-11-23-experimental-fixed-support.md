# Experimental fixed-support measurement — implementation slice

Owner-approved scope (2026-11-23): extend existing preparation/analyzer tests-first; preserve current thresholds and legacy behavior; uncommitted, no provider/live FL calls. Governing method: `docs/research/p1-experimental-policy-pilot.md` and its current-value engineering continuation in the diagnosis/decision plan. No positive diagnosis or coordinator composition in this slice.

1. Add explicit immutable experimental policy (method + current configurable thresholds) and frozen screened cells bound to complete baseline geometry/source hashes, policy and noise-reference digest in existing measurement records. Optional trailing fields preserve old constructors. Keep scientific qualification UNCALIBRATED and U geometric. Validate sorted unique cells within U; policies and every support must agree.
2. Extend canonical manifests only when opted in (schema2); legacy schema1/evidence goldens stay byte-identical. Missing/changed plan never regenerates. Bound bytes before write/reload.
3. Promote unchanged reviewed noise/quiet helpers to existing preparation ownership, with research wrapper compatibility and no production import of scripts. Prepare from verified full-count clips, one read per member, with history and timing/cancellation guards. Freeze cells once with current 40 dB energy / 0.02 dB quiet checks; zero/finite predicates explicit.
4. Add `p1.experimental.*@1` fixed-support measurements in existing analyzer. Size is a factual frozen count; other metrics unavailable below minimum support or unusable current evidence. Same denominator/cells across candidates. Exact-silent masker + exact quiet floor has its explicit exception; target silence/low-energy nonzero source is unavailable. Robust M>configured deadband and longest consecutive-call statistic follow existing grid convention. No diagnoses.
5. Experimental evidence schema3 adds policy/cell/noise identities, baseline source identities, current flags and masker energy; raw/qualified legacy metrics keep their meanings. Test serialization/restart, source/policy/noise mismatches, empty support, target muting, quiet masker, cancellation/limits, exact boundaries and unchanged legacy bytes. Run focused -Werror, full suite, fixture verifier, lint/format/lock/CLI/diff, faithful mutations and one fresh read-only final review. No automatic commit.


## Outcome (2026-11-23)

Implemented in the three existing production modules only: masking records, preparation and directional analysis. The research script imports/re-exports the promoted helpers. Configuration remains explicit with no new calibrated status, defaults, adapter, port or positive diagnosis. Legacy schema1 manifest/native-mono evidence goldens remain exact; opt-in manifests use schema2 and experimental raw evidence schema3.

Tests were written before the implementation. The one fresh read-only review found source grouping by track/time could mix distinct current artifacts, and inclusive screen boundaries could fail through logarithmic subtraction. Regressions reproduced both before fixes. Grouping now includes both current artifact IDs; power-domain comparisons pin equality and the immediately lower float for energy and quiet thresholds. Exact quiet floor stays excluded even with zero configured elevation. The old helper's raw pre-change values are pinned independently in p1_rounding_noise_reference_v1.json.

Verification: 43 experimental tests; full suite 6,964 passed, 4 skipped. Twelve faithful deliberate-error probes were all caught, covering Nyquist, strict deadband, call gaps, silent masker exception, both energy screens, artifact separation, inclusive thresholds, noise identity, masker evidence, initial quiet-floor exclusion and frozen support. Every mutated source was restored byte-identically. Fixture verification, lint, formatting, CLI and dependency lock checks pass. No historical music/control artifacts were regenerated, no provider/FL call was made and everything remains uncommitted.

Next separate increment: deterministic experimental diagnosis using this exact persisted support/policy and honest unavailable outcomes; then composition into the existing coordinator. Neither is implemented here. Scientific policy validation and held-out musical evaluation remain pending.

Final restored-source focused check: 511 passed with warnings treated as errors; git diff --check is clean.

**Follow-up (2026-11-23):** the next increment named above, deterministic experimental diagnosis, is implemented and uncommitted for review. See the diagnosis plan's "Experimental fixed-support diagnosis" note. Composition into the coordinator remains separate and is not implemented.
