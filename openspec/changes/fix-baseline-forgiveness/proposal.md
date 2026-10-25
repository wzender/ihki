# Proposal

## Why

The native-reference baseline erases real learner errors on the sounds the app exists to teach. In a reported attempt at "אנחנו עזרנו" (احنا ساعدنا), the model heard ح as خ (p 0.39 against 0.11) and ع as ء, yet both scored 100 and the sentence 88 ("מצוין"). The only tip shown was a doubtful glottal stop the learner reported as wrong. The baseline divides a learner's sound score by the lowest score either TTS reference voice got on that sound, and both voices scored ح at about 1/100 in this phrase. So any learner realisation becomes 100, whatever took its place. The same happens across most lessons: a ح said as خ scores 80–100. That contradicts the 2026-10-19 lab finding that the model hears ح→خ correctly in real native speech (2% false flags, 64% detection of a real خ). It also undercuts the product's central promise to Hebrew speakers: the app hears which consonant went wrong.

## What Changes

- **Only when the reference lost the sound too.** A native reference compensates a learner's substitution only when that reference also lost the sound (whatever took its place, or nothing). A reference that kept the sound no longer cancels the learner's substitution. To support this, the reference records, per sound, what took its place.
- **Protected pairs.** For substitution pairs the model is shown to tell apart in native speech, the baseline never raises the score: initially ħ→x, from the lab evaluation. The list lives in the scoring rules, and pairs are added only after the same kind of lab evaluation.
- **Evidence for a tip.** A substitution tip, and the "heard as" reason a letter gives, require the substitute to win at least one frame in the sound's window, or to reach the confident peak (0.30). With weaker evidence the sound keeps its score but gets no substitution tip. This addresses the reported false "ء heard as ب".
- **Regression measurement.** The baseline change is measured on stored native recordings (false flags on ح) and on a set of ح→خ substitutions, before and after.
- **BREAKING (scores).** Learners who say خ for ح get lower scores and a tip where they previously got about 100. That's the intended correction. Saved best scores are not rewritten.

Out of scope: changing the acoustic model, adding new teacher calibration floors, changing the ع deletion floor, rewriting stored attempts or calibrations, and the browser layout of results.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `pronunciation-practice`: "Native reference compensation" narrows from forgiving any sound the reference scored low, to forgiving only the same failure, and never for protected pairs. "Pronunciation scoring" gains an evidence requirement before a substitution tip is given.

## Impact

- **Code:** `app/scorer.py` (`Reference`, `reference_logp`, `_baseline`, the baseline step of `score_logp`, flagging, and the trace's baseline stage), `app/reference.py` (built references carry the new per-sound field), and `static/assets/pronunciation-rules.md` (the protected pairs).
- **Tests:** `tests/test_scoring.py` gains baseline, protected-pair and tip-evidence cases. The existing ع compensation test keeps passing.
- **Data and API:** the `/api/score` response shape is unchanged; only scores, tips and letter reasons change. The trace dashboard's baseline stage shows why compensation applied or was refused. There's no Supabase schema change, and stored attempts are not migrated.
- **Privacy, authorization, paid APIs:** no change. References are still built from the existing TTS voices, which are cached, so no extra TTS calls are needed.
- **Degraded behaviour:** without TTS (no references), scoring is unchanged except for the evidence requirement on tips.
