# Tasks

## 1. Baseline measurement (before)

- [x] 1.1 Run `python scripts/minerva_benchmark.py --refs --json /tmp/before-refs.json` on the current code and keep the output. Verify: the JSON exists and reports per-sound false flags and the ħ→x detection rate.
- [x] 1.2 Score the ح→خ demo takes (`scripts/demo_video/takes/m892.wav` and its correct take `c892.wav`) through `/api/score` and record the scores. Verify: noted in the change's notes (the expected "before": m892 ≈ 71, c892 = 100).

## 2. References record what took each sound's place

- [x] 2.1 Add `heard: list[str | None]` (default empty) to `Reference`, and fill it in `reference_logp` with the same rule the learner side uses for `heard_ph` ("_" deleted, competitor beyond margin, None kept), factored into one helper used by both. Verify: a new test in `tests/test_scoring.py` builds a reference from emissions where ʕ is heard as ʔ, and asserts its `heard` has "ʔ" at that sound and None elsewhere.
- [x] 2.2 Make sure `app/reference.py` keeps building references unchanged apart from the new field. Verify: `pytest tests/test_scoring.py -k reference` passes.

## 3. Compensation only for the same failure, and protected pairs

- [x] 3.1 Add `baseline_protected` to the scoring block of `static/assets/pronunciation-rules.md` with `{target: ħ, heard: x}`, and read it in `app/rules.py` (missing means empty). Verify: a rules test asserts the pair is loaded, and an empty rules file gives an empty set.
- [x] 3.2 Rework `_baseline` and its use in `score_logp`: per token, a reference counts only when the learner's sound was not a substitution, or the reference lost the sound too (any substitute, or deleted), or the reference's `heard` is unknown; no reference counts for a protected pair. Verify with new tests:
  - the existing `test_native_reference_forgives_what_the_model_mishears` still passes (ع→ء)
  - a reference heard ħ as x and the learner ħ as h (not protected) → forgiven
  - a reference heard ħ as h and the learner dropped ħ → forgiven (the reference lost it too)
  - a reference kept ħ and the learner said x → not forgiven
  - both the reference and the learner heard ħ as x → not forgiven (protected), tip shown
  - a learner's weak but correct sound is still compensated
- [x] 3.3 Record in the trace's baseline stage each reference's `heard` and `refused` ("protected" / "kept" / null), and show it in `static/trace.html` as text. Verify: a test on `diagnostics=True` output for the protected case asserts `refused == "protected"`. Manual browser check: open `/trace` with a mock trace and see the refusal reason on the baseline stage.

## 4. Evidence before a substitution tip

- [x] 4.1 Gate substitution flags (and with them the tip and the letter's `why`) on `comp_frames >= 1` or `exp(comp_peak) >= confident_peak`; deletions keep their existing evidence rule. Verify with new tests:
  - a token whose competitor has p 0.08 and no frame won, against a target at 0.06 → no tip, no `why`, same score
  - a competitor that wins frames → tip shown
- [x] 4.2 Update the "Native reference compensation" and tip-evidence wording in `static/assets/pronunciation-rules.md` (its prose section on the baseline) to match the new behavior. Verify: the text names the same-failure rule, the protected pairs and the evidence rule.

## 5. Integration and regression

- [x] 5.1 Run the focused tests, then the full suite: `pytest tests/test_scoring.py tests/test_rules.py` and then `pytest`. Verify: all pass, apart from failures that exist on main without this change (list any).
- [x] 5.2 Generate the TTS references for the benchmark's test lines into the local cache (both baseline voices, not uploaded to the bucket; a paid TTS call per uncached line and voice, approved), then run `python scripts/minerva_benchmark.py --refs` on the code before and after this change and compare them with `--compare`. Verify: ħ→x detection with references rises; the ħ native false-flag rate stays at or below about 2%, plus or minus noise; no other sound's false flags rise by more than 2 points (report any that do).
- [x] 5.3 Score the demo takes again through `/api/score`, plus the reported "אנחנו עזרנו" case if its audio is available. Verify: the ح→خ take scores lower with a "ħ>x" tip; the correct take stays at 95 or more; the doubtful initial ء gives no tip.
- [x] 5.4 Manual browser check at phone width: a speaking exercise with a ح→خ attempt shows the red ح and its tip, and a correct attempt shows no false letter reason.

## Workflow follow-up

- After approval, archive the change and sync the "Native reference compensation" requirement into `openspec/specs/pronunciation-practice/spec.md`.
- Re-record the promo video's practice scene, because the demo takes' scores will change.
