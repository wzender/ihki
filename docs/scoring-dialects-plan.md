# Plan: dialect acceptance and Hebrew-speaker rules in the scorer

Status: implemented 2026-10-15, except where "Implementation notes" at the end says otherwise. Source rules: [pronunciation-rules.md](../static/assets/pronunciation-rules.md).

Two layers on top of today's scoring path:

- **Layer A, dialect acceptance:** decides what counts as *correct*. Runs inside GOP: every correct realisation of a letter is pooled into one class, and members of the class never count as competitors.
- **Layer B, Hebrew-speaker rules:** decides how strictly to judge *real errors*. Runs after GOP: per-pair margins, severity, feedback, exemptions.

The native baseline and the teacher calibration floors stay where they are, but are keyed on the abstract symbol instead of the sound actually said.

Final order of the pipeline:

1. `Vocab.merge` pools spelling variants (exists)
2. Layer A: class columns → alignment → GOP against the class
3. Layer B exemptions (6.3)
4. Native baseline normalisation (exists, keyed on the abstract symbol)
5. Layer B margins, severity, feedback
6. Teacher DB floors (exists, last, manual override)

## Phase 0: Rule loader (`app/rules.py`, new)

- Pull the YAML blocks out of `pronunciation-rules.md` (fenced ` ```yaml `, split on `---`). Load them into:
  - `archiphonemes`, `dialects_enabled`, `optional_realisations_enabled`
  - `scoring` (margins, floors)
  - `checks` (target, heard, severity, feedback), collected from every rule block
  - `heard_labels`
- Pass every symbol through `base()` when loading, so `ʁ` is stored as `ɣ`, `χ` as `x`, and so on. Warn about rules that become **undetectable**:
  - target and heard end up the same sound after `base()`;
  - a heard symbol is in the same accepted set as its target;
  - a symbol is missing from the model vocab.
- Add `pyyaml` to `requirements.txt`. It's already installed through `huggingface_hub`, but it should be listed explicitly.
- Consider moving the rules file to `docs/` as the doc itself suggests. The student sections are served from `static/assets/`, so either keep it there or copy it at build time.
- **Tests:** every block parses; every `heard:` symbol has a label in section 8; the undetectable-rule check fires on a deliberately bad rule.

## Phase 1: Layer A core, with `Q` only

### 1a. Converting the phrases at load time, not in the source files

`app/workbook.py` has ~930 ق and `app/grammar.py` has more, so rewriting the IPA by hand isn't realistic.

- Add `archify(ar, ipa)` in a new `app/archi.py`. For each word whose Arabic contains ق, replace the matching `ʔ`/`q`/`k` with `Q`.
  - Match by consonant order. `word_letters` in `app/translit.py` already lines up ق with its phoneme, so reuse that logic.
- **Ambiguous words** (both ء and ق, or ك next to ق) need an explicit marker written in the phrase. Add a test that fails on any ق word it can't resolve.
- After conversion, merge duplicate variants. Today's separate `q` and `k` variants (e.g. `i l q u d s`) become the same as the `ʔ` one.
- Run it inside `PHRASES` / `workbook_phrases()` so every consumer sees `Q`, including references and tokenisation at startup in `load_scorer`.

### 1b. `Vocab` classes (`app/scorer.py`, `Vocab`)

- `Vocab(token_to_id, classes=rules.archiphonemes)` creates one **virtual id** per abstract symbol, numbered after the real vocabulary (V, V+1, …). It also keeps `members[vid]`, the real representative ids of the accepted realisations.
- `merge()` returns `logp` with extra columns: column `vid` = `logaddexp` over its members.
- `resolve("Q")` returns `[vid]`. `i2t[vid] = "Q"`.
- Anything that ranges over "real" symbols has to ignore virtual columns: `greedy_decode`, `_heard_hebrew`, the heatmap's "heard" track, and `edit_distance` for the unrelated check. Easiest is a `vocab.real(logp)` view of `logp[:, :V]`.

### 1c. GOP (`app/align.py`, `score_tokens`)

- `score_tokens(..., accept: list[set[int]] | None)`. For a class token, mask all its members from the competitor search, and always mask every virtual column.
- Add `TokenScore.realised`: the member with the highest peak inside the window (`None` for plain tokens).
- The alignment itself doesn't change. It already runs on the virtual column.

### 1d. Results and display

- Each phoneme entry gets `realised`. `heard` comes from the competitor as before, so an accepted realisation never shows up as "heard".
- `_heard_hebrew`: when the frame is inside a class token's span and the greedy symbol is one of its members, write the **realised** letter (כּ, א or ק) uncoloured.
- `to_hebrew` / `word_letters`: `Q` is written ק in the answer (today's ق rule already does this).
- `app/tips.py`: add `Q` to `NO_DELETION_TIP` (6.3 #2). Remove the `ʔ>k` qaf tip.

### 1e. Baseline and calibrations

- References need no change. `Reference.sounds` now holds `(w, "Q")`, so the baseline is the same whatever realisation the TTS voice used. The cache is in memory and rebuilds on restart.
- Calibration lookup in `score_logp`: try `(archi, heard)` first, then fall back to `(realised target, heard)` so existing DB rows still apply. Add a teacher-panel note or a one-off SQL to re-key `ʔ` rows that came from ق phrases.

### 1f. Tests (`tests/test_scoring.py`)

- Rewrite `test_k_for_urban_qaf_picks_closest_variant_and_tips`: `k` for `Q` scores ≥ 90, has no tip, and `realised == "k"`.
- New tests:
  - `q` for `Q` is accepted;
  - `Q` dropped between vowels is not flagged;
  - `ʔ` from ء said as `k` **is** flagged (e.g. اهلا → kahla);
  - a reference voiced with `q` doesn't penalise a learner who says `ʔ`;
  - `archify` on a sample of workbook phrases, plus the ambiguity test.

## Phase 2: The other five abstract symbols

- Extend `archify`: ك→`K`, ث→`TH`, ذ→`DH`, ظ→`ZH`, ج→`J`.
- **Per-word override syntax** for learned words, e.g. `TH{t,s}` (ثقافة) or a plain `t` to lock it. Parse it in `tokenize`, which creates a one-off class for that position.
- **Vocab gaps:** if `θ`, `ð`, `ðˤ` or `tʃ` aren't in the model, the class quietly shrinks. Log it, and expose it in `/api/health` under `rules` (section 7 of the rules doc).
- Add `tʃ` (צ'), `θ` (ת'), `ð` (ד״) to `CONSONANTS` in `app/translit.py` so rural realisations can be written in the heard line.
- Add `θ`, `ð`, `tʃ`, `ðˤ`, `dʒ` to the test vocab `SYMS`. Tests for each class, plus one mixed-dialect sentence that isn't penalised.

## Phase 3: Positional classes and diphthongs

- **`E`** for a final ة/ه vowel → {a, e, i}. Derived automatically from the Arabic ending (`word_letters` already detects it).
- **`O`** for the vowel of a pronoun ending → {o, u}. This one needs an explicit marker in the phrase, because it can't be told reliably from the Arabic.
- **`EY` / `OW`**: expand into extra **variants** (`eː` | `a j`, `oː` | `a w`) inside `archify`, before variants are merged. `_fit` already picks the best variant. These can't be a per-token class, because one token stands against two.
- `near_vowels` stays for other positions until the vowel-length decision is made (see "Open decisions").
- Sounds that natives also drop (ʔ at the start or between vowels, il- after a vowel) are **not** Layer A: an optional token would need a separate alignment with and without it. They stay as Layer B exemptions.

## Phase 4: Layer B, the rule engine

### 4a. Competitor set

- Limit competitors to the Arabic sounds plus the Hebrew-interference sounds (every `heard:` symbol in `checks`). Report any other winner as `competitor=None, other=True` ("not clear").
- **Watch out:** this lowers `comp_peak` in the deletion test (`deleted_comp`). Re-check the deletion tests.

### 4b. Flagging by margin

- In `score_logp`, flag a sound when `score < flag_below` **and** the competitor is outside the target's accepted set **and** `−gop ≥ margin(target, heard)`. `gop` is already `log P(target) − log P(competitor)`.
- The issues list keeps its threshold (`issue_below`) but also respects the margin.

### 4c. Feedback

- `tip_for` looks up `checks[(target, heard)]` first, then falls back to `TIPS`. Keep the key format `expected>heard` so the front end doesn't change.
- Sort tips by severity, then by score. `max_tips` comes from the rules file.

### 4d. Exemptions (6.3)

- Move `NO_DELETION_TIP`, `elided_article` and `near_vowels` into a predicate registry in `rules.py`: `(name, applies(toks, i, ar_words, sc) -> bool, action)`.
- Add the doc's cases 1–3, 4 (il- not assimilated) and 12 (`mixed_dialect` as info only).

### 4e. Letters that can only be heard, never expected

- A `HEARD_ONLY` table in `translit.py`: `p`→פּ, `v`→ב/ו, `ts`→צ. Include it in `HEBREW_SOUNDS` so `_heard_hebrew` stops dropping these sounds. Today a student who says *pinjaan* sees the first letter missing instead of פּ.
- `sound_name` / `letter_reason` labels come from `heard_labels`.
- **Tests:** `f→p` (פִנְג'אן), `w→v`, `b→v`, `sˤ→ts`, `r→ʁ`. Emphatic just under the margin is not flagged, just over it is.

## Phase 5: Teacher-facing parts

- `dialects_enabled` / `optional_realisations_enabled` as teacher settings, stored wherever teacher settings live today. Pass them into `Scorer.score` per request, so different classes can differ.
- The taught dialect only changes hints and info, never what's accepted.
- Teacher panel: show `realised` and `mixed_dialect` in the diagnostics view. Show abstract symbols by their Hebrew letter, never as "Q".
- `/api/health`: a `rules` field listing each rule and whether it can be detected.

## Phase 6: Rollout check

- If recordings are stored, add a `scripts/rescore.py` that compares old and new scores per phrase. Review the biggest changes before switching over, especially ק phrases and emphatics.
- Ship Phase 1 on its own: it fixes ק/א and tests the class mechanism end to end.

## Open decisions (outside this plan)

1. **Vowel length:** the doc's `vowel_length` checks (severity 1, margin 1.1) vs. today's `near_vowel_floor`, which forgives length completely.
2. **Sentence formula:** the doc's `mean(word_min)` and severity weights vs. today's 0.5/0.5 blend. Keep it behind a config flag until decided.
3. **Bedouin `g` for `Q`:** on or off by default.
4. **Extra sounds:** inserted sounds aren't detected, since forced alignment only scores expected sounds.

## Main risks

- The virtual columns change the `logp` width. Anything that indexes `logp` by a real id is fine, but anything that iterates over all columns (heatmap, greedy) must use the real-only view.
- `archify` mismatches in the workbook. That's why the ambiguity test fails the build instead of guessing.
- Old calibration rows keyed on `ʔ` until they're re-keyed. That's why the lookup falls back to the old key.

## Implementation notes

Code: `app/rules.py` (loader, detectability, exemptions), `app/archi.py` (symbols, letter alignment, diphthong variants), `app/phones.py` (`base()`, moved out of the scorer), `app/scorer.py` / `app/align.py` (classes, GOP, margins, tips), `scripts/rescore.py`. Tests: `tests/test_rules.py` and the "dialects and Hebrew-speaker rules" section of `tests/test_scoring.py`.

Where it differs from the plan above:

- **`archify` runs inside the scorer, not on `PHRASES`.** `Scorer._variants` converts each variant when scoring (cached). Display, TTS, transliteration and the verb game keep the plain IPA. Symbols replace phonemes one for one, so positions in the IPA still line up.
- **Token format** is `SYMBOL:written` (`Q:ʔ`), or `SYMBOL{a,b}` for a hand-written set. A phrase that writes a symbol itself is left alone.
- **Ambiguity:** across the bank (~11.6k variants) no ق is unresolved. 5 × ث merged with ت into one geminate (بعثت) and 1 × ج (جوز) stay plain phonemes; only an unresolved ق fails the test.
- **Learned-only realisations** (`TH` as s, `DH` as z) are accepted where the phrase writes them, with no override syntax needed.
- **Missing model symbols:** a class member the model lacks uses the same `FALLBACKS` stand-in as a plain phoneme. With the base model, `ZH` is `{dˤ, z}`, so "ظ heard as z" shows as `accepted` (undetectable) in `/api/health` → `rules`.
- **Competitor naming (4a)** keeps the GOP against the strongest symbol of any kind, so scores and deletions don't change. Only the *name* is withheld (`other`) when that symbol isn't Arabic or a Hebrew-interference sound.
- **Tips:** `max_tips` is now 3 (from the rules file), sorted by severity then score. Every tip carries `severity`.
- **Results** gain `phonemes[].sound` (`Q`), `phonemes[].realised` (`k`), and `dialect: urban|rural` or `mixed_dialect: true`. None of them affect the score.
- **Teacher view:** the expected/heard phone rows compare an accepted realisation with what was said, and show the dialect.

Not done:

- **Per-teacher dialect settings (Phase 5).** The accepted set comes from the rules file for everyone (`dialects_enabled`, `optional_realisations_enabled`). Since the taught dialect was only meant to change hints, nothing in scoring depends on it yet.
- **Re-keying old calibration rows.** The lookup falls back to the written phoneme, so old rows keep working. Rows proposed from the page are still keyed on `phonemes[].ipa`.
- **The open decisions** above (vowel length, sentence formula, Bedouin g, insertions) are unchanged. Vowel-length rules show as undetectable in `/api/health`.
