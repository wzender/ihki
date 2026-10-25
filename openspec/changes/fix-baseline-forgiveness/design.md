# Design

## Context

See proposal.md (Why). The baseline today:

- `Scorer.reference_logp` turns a native TTS recording into a `Reference`: per sound, `(word, sound)` and its score. A reference is dropped entirely when it is unrelated, or its mean score is under `ref_min_mean` (60). Trust is decided per voice, not per sound.
- `Scorer._baseline` takes, per learner token, the **lowest** score any reference got on the same sound (sounds matched with `SequenceMatcher`).
- In `score_logp` the learner's score becomes `100 * score / max(ref, 1)`, capped at 100. A reference near 0 makes any learner realization 100. The substitute in the learner's recording plays no part.
- Flagging (a tip and a letter reason) requires `score < flag_below[sound]` and `_beyond_margin`. `_beyond_margin` passes whenever the pair's margin is 0, which is the default, so a competitor that never won a frame can still produce a tip. The trace shows `competitor_frames` and `p_competitor`, but flagging ignores them.

Measured facts that shape the approach:

- **Real native speech (lab, 2026-10-19):** ح is heard as خ in only 2% of native tokens, and a real خ in place of ح is caught 64% of the time. ħ→x is reliable model evidence.
- **ع:** the model has a documented false negative (the code's `ayin_deleted_floor`, and `test_native_reference_forgives_what_the_model_mishears`). The native voice's ع heard as ء is a model limitation the baseline should keep forgiving.

## Goals / Non-Goals

**Goals:**
- Compensation is tied to a matching failure in the reference, per sound.
- Pairs proven reliable on natives can't be erased by TTS references.
- No substitution tip without frame-level or confident evidence.
- The trace explains each decision.

**Non-Goals:**
- Replacing the TTS references with human recordings. That's a later data task, noted in Open Questions.
- Fixed per-sound trust thresholds or forgiveness caps. These were considered and rejected (below).
- Changing exemptions, calibration floors or the unrelated-sentence logic.

## Decisions

1. **Record what took each sound's place in the reference.**
   - What: `Reference` gains `heard: list[str | None]`, per sound, computed exactly as the learner's `heard_ph` is ("_" when deleted, the competitor's symbol when it is beyond its margin, None when the sound was kept). Default `[]`, so older references in memory are treated as "unknown". Built in `reference_logp` from the fit's token scores.
   - Why: compensation must compare failures, and the same function defining "heard" for both sides keeps them comparable.
   - Alternative: re-decoding the reference at scoring time. Rejected as wasted work; references are cached once per phrase and voice.

2. **Compensation only when the reference lost the sound too.**
   - What: `_baseline` becomes per token. For each reference that has the sound, its score counts only if one of these holds:
     - (a) the learner's sound was not a substitution (kept, or competitor not beyond margin with evidence): today's behavior for a weak but correct sound
     - (b) the reference lost the sound too (its `heard` is not None: a substitute or deleted), however it lost it
     - (c) the reference's `heard` is unknown (an old cached reference)
     The minimum is then taken over the counting references. A reference that kept the sound while the learner substituted is refused ("kept").
   - Why: a reference that kept the sound is no evidence the model can't hear it. Requiring the *same* failure, as first planned, refused compensation on correct speech: TTS takes heard as ه against references heard as خ dropped from 100 to 86, and ħ→h is the common native false positive (about 13% in the lab). The reported ħ→x case is covered by the protected pairs (decision 3), not by matching failures.
   - Alternatives:
     - A per-sound trust threshold (ignore a reference under about 40 on a sound): it would cancel the deliberate ع compensation, and no data picks the number.
     - Using the maximum over references instead of the minimum: it doesn't help when every voice fails, as in the reported trace.

3. **Protected pairs in the rules.**
   - What: the rules' `scoring` block gains `baseline_protected: [{target: ħ, heard: x}]`. When the learner's `(sound, heard_ph)` is a protected pair, no reference counts for that token.
   - Why: the TTS voices themselves produce a ح that the model hears as خ. That's a property of the voices, not the model, so (b) would wrongly forgive it. The lab result justifies ħ→x specifically.
   - Adding a pair later follows the existing "human review before calibration" practice: lab natives as negatives, swapped-phoneme natives as positives.
   - Alternative: a hard-coded set in `scorer.py`. Rejected, because the rules file already owns per-pair scoring policy (margins).

4. **Evidence requirement for flagging.**
   - What: a substitution flag also requires `comp_frames >= 1` or `exp(comp_peak) >= confident_peak` (0.30). Deletions ("_") keep their own deletion evidence (`deleted_peak` / `deleted_comp`).
   - Implemented as a check next to `_beyond_margin`, so it gates both the tip and the letter's `why`. The phoneme's score is unchanged.
   - Why: the false "ء heard as ب" had p 0.06 against 0.08 and no frame won. Scores stay honest, but the learner isn't told something the model barely suggested.

5. **Trace.**
   - What: the baseline stage records per reference `heard` and, when compensation is refused, `refused: "protected" | "kept" | null`. The dashboard (`static/trace.html`) shows it as text, with no layout change.
   - Why: to keep the stage-by-stage explanation the team already relies on.

The affected code is listed in proposal.md (Impact). The API shape, browser state and Supabase schema are unchanged. Deployment needs only the code and the updated rules file. The browser renders scores and reasons it already understands.

## Risks / Trade-offs

- **[More false tips where the TTS voice kept a sound the model misses in learners]** → only a reference that kept the sound is refused; any failure in it still forgives. We'll measure native false flags on the stored lab recordings before and after (tasks), and hold the change if ħ false flags rise beyond the 2% baseline.
- **[Learners' scores drop on ح]** → intended. Best scores already saved stay, and only new attempts change.
- **[Old cached references lack `heard`]** → treated as unknown: today's behavior until they're rebuilt (in memory, rebuilt on restart).
- **[Evidence gate hides some true substitutions with brief, low-confidence competitors]** → the sound's score still shows it, and the gate only removes the tip and the reason.

## Migration Plan

Deploy with the rules file. There's no data migration. Rollback means reverting the commit. Without `baseline_protected` in the rules file, protected pairs are simply empty.

## Open Questions

- Should human native recordings replace the TTS voices as references for pharyngeals? It's a data task that doesn't change this design.
