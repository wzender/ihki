# GOP frame pooling

How `score_tokens` ([app/align.py](../app/align.py)) turns a token window's frames into one target score and one
competitor score, and why short vowels need something other than the max. Benchmark:
`python scripts/gop_pooling_bench.py`. Tests: [tests/test_gop_pooling.py](../tests/test_gop_pooling.py).

## Notation

- Window of token *i*: `W = [a, b)`, from the end of token *i−1* to the start of token *i+1*. `n = |W|`.
- `p_t(k)` is the posterior of column *k* at frame *t*, and `ℓ_t(k) = log p_t(k)`. *y* is the target.
- *C* is the set of competitors: every column except blank, the target, its two neighbours, and their accepted realisations.
- A pool `P(k)` maps the window to one posterior-like value per column. The score is:

```
GOP   = log P(y) − max(log P(y), max_{c∈C} log P(c))          ≤ 0
p̂     = max_t p_t(y)                                           (always the max, whatever P is)
score = 100 · exp(λ · GOP) · sqrt(min(1, p̂ / κ))               λ = 0.5, κ = 0.30
```

The change is the split between the two terms. **`ratio_pool` changes only `P`, the relative evidence. The
presence term `p̂` and the deletion test stay max-pooled.** The reason is below (result 1).

## The pools

| `ratio_pool` | `P(k)` | Limits |
|---|---|---|
| `max` (old behaviour) | `max_t p_t(k)` | — |
| `mean` | `(1/n) Σ_t p_t(k)` | — |
| `topk` | `(1/K) Σ_{t ∈ top-K of k} p_t(k)` (each column uses its own best K frames) | K=1 → max, K=n → mean |
| `lse` | `(1/n Σ_t p_t(k)^{1/τ})^τ`, which is `τ·(LSE_t(ℓ_t(k)/τ) − log n)` | τ→0 → max, τ=1 → mean |
| `energy` | `Σ_t w_t p_t(k)`, with `w_t ∝ ẽ_t^γ` | γ=0 → mean |
| `gated` | mean over `{t : ẽ_t ≥ θ}` | θ=0 → mean |

`ẽ_t` is the frame's RMS, max-filtered over ±`energy_spread` frames and divided by the loudest frame in the
window. The token's own aligned span always gets `w = 1`: if the gate dropped the frame where the token was
emitted, the target would fall to ≈0 (3.7/100 in the benchmark without this guard).

All pools work in the **probability** domain. The literal alternative, a weighted mean of frame log-ratios
`Σ w_t (ℓ_t(y) − ℓ_t(c))`, breaks on CTC output. On blank frames both posteriors are ~1e-4, so their log-ratio
is noise of order ±5. A single frame where the target is at its floor adds −9 to the sum. Mixing in the
probability domain caps what any one frame can contribute at `w_t`.

## Results

**1. A symmetric pool cannot tell two single-frame spikes apart.** If the target and the competitor each occupy
one frame, with heights `h_y` and `h_c`, every pool above gives `P(y)/P(c) = h_y/h_c`: the dilution factor
(`1/n`, `1/K`, `n^{−τ}`) is the same for both. A pool only helps when the two differ in **support** (how many
frames they cover) or in **location** (loud or quiet frames). A correct CTC vowel is itself a 1–2 frame spike,
so its *presence* has to be read with the max. That is why `p̂` is never pooled.

**2. The mass ratio does not depend on window length; the presence term does.** For `mean`:
`P(y)/P(c) = Σ p_t(y) / Σ p_t(c)`, so the `1/n` cancels. Unweighted averaging hurts only when the averaged
value also feeds the confidence. A 0.92 one-frame emission in a 10-frame window gives `P = 0.092`, so
`sqrt(0.092/0.30) = 0.55`: score 55, which is the "naive" column in the benchmark.

**3. Mass pooling accumulates haze.** A faint competitor at ε per frame adds up to `n·ε`, while the target stays one
peak. When a hesitation stretches the window, `mean` loses (a 0.05 schwa haze: 100 at 20 frames, 83 at 30, 63
at 50). `gated` and `energy` drop the silent frames. `topk` caps the competitor at its K best frames.

**4. Spike sensitivity.** For a correct /a/ with frames .2/.4/.35 and a one-frame /i/ spike of height *h* inside the window:

| h | max | mean / energy / gated | top2 | top3 | lse τ=.5 | lse τ=.25 |
|---|---|---|---|---|---|---|
| .5 | 89 | 100 | 100 | 100 | 100 | 95 |
| .9 | 67 | 100 | 91 | 100 | 79 | 71 |
| .98 | 64 | 98 | 88 | 98 | 76 | 68 |

LSE moves smoothly between max and mean. With r = 1/τ = 2 it still prefers one 0.9 frame to three moderate
ones, so it absorbs a tall spike only partly.

**5. Substitutions still fail.** Every pool scores the sustained /i/-for-/a/ case at about 20, and the one-frame
substitution at about 6. The one-frame substitution is also a single spike, but there the target is *absent*
(`p̂` ≈ .03), so both the ratio and the presence term are low. A pool only rescues a target that is clearly present.

**6. Separation.** Margin between the worst correct vowel and the best substitution in the synthetic set: max 46,
lse 59, top2 70, mean / energy / gated / top3 80. These are synthetic numbers. Use them to rank the pools, not to set thresholds.

## Class-based windows

`compute_token_gop` reads each token from a window chosen by its class (`token_window`):

- **Consonants: wide**, `[end(i−1), start(i+1))`. When the alignment forces a path through a consonant that was
  substituted, the sound actually said peaks in the blanks around the forced frame
  (`consonant: competitor in blanks`: wide 15.8, competitor q; read narrow, q is lost).
- **Vowels: `GOP_VOWEL_WINDOW`** (`GopConfig.vowel_window`). `wide` (the default), or the aligned span ± pad
  (`narrow` = ±1), clamped to the wide window. Only the **competitor ratio** uses the narrow window. Presence
  (`peak`) and `deleted` are always read from the wide window, so a vowel aligned to a single frame is still found
  deleted, and a strong competitor anywhere in the wide window still blocks a deletion (it is a substitution, not a
  deletion).
- **Glides**: for a vowel next to j or w, the vowels that glide is made of (j: i iː e eː; w: u uː o oː;
  `GLIDE_KIN` in scorer.py) do not count as competitors **on the frames between the vowel's span and the glide**,
  for the ratio and for the deletion test. Inside the vowel's own span they still count, so /ji/ said for /ja/ fails.
  This is always on and does not depend on the window setting.

CTC Viterbi gives every token at least one frame, so a span of 0 frames cannot happen; a deleted vowel gets one.

### Why the vowel window defaults to wide

From `python scripts/gop_window_bench.py` (spans from `ctc_viterbi`, no audio):

| case | want | wide max | pad 1 max | wide mean |
|---|---|---|---|---|
| spike in transition | pass | 74.5 | 100 | 100 |
| displaced substitution (/a/ .35, /i/ .85 held 2 frames later) | fail | 64.2 | **100** | 46.1 |
| margin (worst pass − best fail) | | 10.3 | **0.0** | 53.9 |

The narrow window drops the transition spike by not looking at those frames, so it also stops seeing a real
vowel held outside span ± pad. Each pad goes blind beyond its reach: pad 0 at 1 frame away, pad 1 at 2, pad 2 at 3.
A pooled ratio over the wide window drops the same spike by its support (one frame against several), and still
sees the held substitution. A pooled ratio over a narrow window inherits the blind spot (pad1 mean: 100).
So the default is wide plus a pooled ratio. Use `narrow` when the lab shows vowel false flags whose competitor
sits in the transitions and pooling does not clear them.

The displaced case needs the target to be weakly present elsewhere. In a plain substitution the aligner usually
puts the target on the substituted frames, where every pad sees the competitor (`sustained substitution`: 21
everywhere).

## Failure modes

- **Voiceless neighbours.** RMS measures loudness, not voicing. A vowel next to /s ʃ f/ shares its window
  with sibilant frication, which can be as loud as the vowel. `energy` then puts weight on the fricative frames,
  and a competitor spike there counts in full. A vowel before /t k q ʔ/ ends in a quiet devoiced tail; the gate
  drops it, which is usually the intent. A voicing feature (low-band energy, or autocorrelation periodicity) would
  be a better weight than RMS.
- **Pharyngeals and emphatics.** Next to ʕ ħ sˤ tˤ, /a/ backs to [ɑ], which is a *sustained* competitor across the
  whole nucleus. That is coarticulation, but it looks like a real substitution to every pool. Pooling cannot fix it;
  the near-vowel floor and the exemptions in `rules.py` handle it.
- **Fast speech.** Windows of 1–3 frames: `n == 1` returns the max; with small *n*, `topk` and `mean` agree, and
  the energy nucleus may be a single frame that the ±1 spread blends with the consonants. All pools converge to
  roughly the max, which is fine: there are no frames for a spike to hide in.
- **The spread cuts both ways.** `energy_spread` forgives a CTC emission one frame off the nucleus, but it also
  lifts a competitor spike one frame off the nucleus over the gate. The span guard already protects the target,
  so `energy_spread=0` rejects spikes better; keep 1 until real data shows CTC peaks landing outside the alignment.
- **Short-vowel deletion** (colloquial elision of unstressed /i/, /u/). `deleted` still reads the max peaks, so it
  does not change. The ratio of a deleted vowel is target floor vs. whatever the consonants leak into the window. The gate keeps the
  consonant frames because they are loud, so a deleted vowel should score about the same as before (not covered
  by the synthetic set yet).
- **A real one-frame vowel that is in between** (target 0.4, competitor 0.9, both one frame) gives the same
  ratio under every pool (result 1). It is irreducible: the model is unsure which vowel it heard.
- **Quiet consonants** (ʔ h ʕ). Energy pools would discard exactly the frames these live in, so
  `pool_vowels_only` (default on) keeps the max for every consonant.
- **No audio.** `energy` and `gated` fall back to `mean` when `score_logp` gets no `energy` (for example a rescore
  from cached posteriors), and inherit its haze problem. Use `topk` there, or pass the energy along.

## Rollout

The pool comes from the env var `GOP_RATIO_POOL` (empty: `gated`; see `.env.example`). `max` reproduces the old
scores exactly (`test_max_pool_is_the_previous_scorer`). The lab shows the pool beside the version, since the version
hashes only the code.
Switching changes the GOP distribution for vowels, which moves toward 0, so everything tuned on it has to be refreshed:

1. the margins in `rules` (`Scorer._margin`) and the native-reference baselines (`_baseline`): re-record the references;
2. the calibration floors (`set_calibrations`): rerun `scripts/rescore.py` on the labelled set;
3. the teacher trace still reports `p_target` and `p_competitor` as maxima, so with a pooled ratio a competitor
   can show a higher peak than the target while the GOP is 0.

Suggested setting: `ratio_pool="gated"` (θ = 0.3, about −10 dB), `pool_vowels_only=True`, with `topk` K=3 as the
setting when there is no audio.
