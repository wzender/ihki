"""CTC forced alignment and goodness-of-pronunciation (GOP), in plain numpy.

Inputs are frame-level log-posteriors `logp` of shape (T, V) from a CTC phoneme model.
Nothing here depends on torch, so it is unit-testable with synthetic emissions.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger("ihki.align")

NEG = -1e30
POOLS = ("max", "mean", "topk", "lse", "energy", "gated")
DEFAULT_POOL = "gated"


def env_pool() -> str:
    """The ratio pool GOP_RATIO_POOL names (read when a GopConfig is made, so after main.py loads .env).
    Empty or unset: DEFAULT_POOL. An unknown name is logged and DEFAULT_POOL is used."""
    pool = os.environ.get("GOP_RATIO_POOL", "").strip().lower() or DEFAULT_POOL
    if pool not in POOLS:
        log.warning("GOP_RATIO_POOL=%r is not one of %s: using %s", pool, ", ".join(POOLS), DEFAULT_POOL)
        return DEFAULT_POOL
    return pool


NARROW_PAD = 1


def env_vowel_window() -> int | None:
    """GOP_VOWEL_WINDOW: empty, unset or "wide" (None: a vowel reads the consonants' wide window), "narrow" (its
    aligned span +- NARROW_PAD frames), or that pad as a frame count. Anything else is logged and "wide" used.
    Wide is the default: with a pooled ratio it absorbs a transition spike without the narrow window's blind spot
    (docs/gop-pooling.md, scripts/gop_window_bench.py)."""
    raw = os.environ.get("GOP_VOWEL_WINDOW", "").strip().lower()
    if raw in ("", "wide"):
        return None
    if raw == "narrow":
        return NARROW_PAD
    if raw.isdigit():
        return int(raw)
    log.warning("GOP_VOWEL_WINDOW=%r is not wide, narrow or a frame count: using wide", raw)
    return None


@dataclass
class Alignment:
    total_logp: float           # log-prob of the best CTC path
    spans: list[tuple[int, int]]  # [start, end) frames where each target token is emitted


def ctc_viterbi(logp: np.ndarray, tokens: list[int], blank: int) -> Alignment | None:
    """Best CTC path that emits exactly `tokens`. Returns None if the audio is too short."""
    T, _ = logp.shape
    N = len(tokens)
    if N == 0 or T < N:
        return None
    S = 2 * N + 1
    ext = np.array([blank if s % 2 == 0 else tokens[s // 2] for s in range(S)])
    # skip transition s-2 -> s allowed for non-blank states whose label differs from s-2
    can_skip = np.zeros(S, dtype=bool)
    can_skip[2:] = (ext[2:] != blank) & (ext[2:] != ext[:-2])

    dp = np.full((T, S), NEG)
    bp = np.zeros((T, S), dtype=np.int8)  # 0 = stay, 1 = from s-1, 2 = from s-2
    dp[0, 0] = logp[0, blank]
    dp[0, 1] = logp[0, ext[1]]
    for t in range(1, T):
        prev = dp[t - 1]
        stay = prev
        one = np.concatenate(([NEG], prev[:-1]))
        two = np.where(can_skip, np.concatenate(([NEG, NEG], prev[:-2])), NEG)
        stacked = np.stack([stay, one, two])
        choice = stacked.argmax(0)
        dp[t] = stacked[choice, np.arange(S)] + logp[t, ext]
        bp[t] = choice

    # path must end in the last token or the trailing blank
    end = S - 1 if dp[T - 1, S - 1] >= dp[T - 1, S - 2] else S - 2
    total = float(dp[T - 1, end])
    if total <= NEG / 2:
        return None

    states = np.empty(T, dtype=np.int64)
    s = end
    for t in range(T - 1, -1, -1):
        states[t] = s
        s -= int(bp[t, s])

    spans: list[list[int]] = [[-1, -1] for _ in range(N)]
    for t, st in enumerate(states):
        if st % 2 == 1:
            i = st // 2
            if spans[i][0] < 0:
                spans[i][0] = t
            spans[i][1] = t + 1
    return Alignment(total, [tuple(x) for x in spans])


@dataclass
class TokenScore:
    score: float      # 0..100
    gop: float        # log ratio target vs strongest competitor, <= 0
    peak: float       # max log-posterior of the target inside its window
    competitor: int | None  # strongest non-target, non-blank token in the window (None: nothing, or a sound we do not name)
    deleted: bool     # target barely present and nothing else either
    realised: int | None = None  # a class target (several correct realisations): the one said
    other: bool = False          # the strongest competitor is a sound outside the named set
    comp_peak: float = NEG       # max log-posterior of the strongest competitor (named or not) in the window
    comp_frames: int = 0         # frames where that competitor clears deleted_comp (duration evidence)
    window: tuple[int, int] = (0, 0)  # [start, end) frames the target and its competitors were read from


@dataclass
class GopConfig:
    deleted_peak: float = float(np.log(0.05))    # target this weak ...
    deleted_comp: float = float(np.log(0.20))    # ... and no strong competitor -> deletion
    confident_peak: float = 0.30                 # target posterior at which the peak no longer lowers the score
    # Softens the target/competitor ratio: at 0.5 a competitor twice as likely still scores 71, not 50.
    # The zero-shot model is noisy on accented speech, so a linear ratio punishes its own uncertainty.
    leniency: float = 0.5
    # How the target/competitor ratio pools the window's frames (docs/gop-pooling.md). The peak behind
    # `confidence` and `deleted` stays the max, so a peaky CTC emission is never diluted by its blanks.
    ratio_pool: str = field(default_factory=env_pool)  # max | mean | topk | lse | energy | gated (GOP_RATIO_POOL)
    pool_k: int = 2              # topk: frames averaged per column
    pool_tau: float = 0.5        # lse: temperature (0 -> max, 1 -> mean)
    energy_gamma: float = 1.0    # energy: frame weight = (RMS / loudest in the window) ** gamma
    energy_gate: float = 0.3     # gated: frames quieter than this fraction of the window's loudest are dropped
    energy_spread: int = 1       # frames the energy is max-filtered over (CTC peaks lead/lag the acoustics)
    pool_vowels_only: bool = True  # consonants keep the max (bursts and voiceless sounds are short and quiet)
    # Consonants need some surrounding blank for a CTC peak that leads/lags the forced span, but must not inherit an
    # arbitrarily long pause up to the next token (e.g. a 760 ms window across a sentence break).
    consonant_window: int | None = 4
    # A vowel's competitors are read from its aligned span +- this many frames (None: the wide window consonants
    # use). Its presence (`peak`, `deleted`) is still read from the wide window. GOP_VOWEL_WINDOW sets it.
    vowel_window: int | None = field(default_factory=env_vowel_window)


def pool_frames(seg: np.ndarray, how: str = "max", k: int = 2, tau: float = 0.5,
                weight: np.ndarray | None = None) -> np.ndarray:
    """Pool a window's (n, V) log-posteriors over time into one log-score per column.

    max: the single best frame. topk: log of the mean of each column's k best frames. lse: the power mean
    tau * log(mean(p ** (1/tau))), between the max (tau -> 0) and the mean (tau = 1). mean / energy / gated:
    log of the weighted mean posterior, `weight` (uniform when None) normalised over the window.
    """
    n = len(seg)
    if how == "max" or n == 1:
        return seg.max(0)
    if how == "topk":
        k = max(1, min(k, n))
        return np.logaddexp.reduce(np.partition(seg, n - k, axis=0)[n - k:], axis=0) - np.log(k)
    if how == "lse":
        return tau * (np.logaddexp.reduce(seg / tau, axis=0) - np.log(n))
    w = np.ones(n) if weight is None else np.asarray(weight, dtype=np.float64)
    w = w / w.sum() if w.sum() > 0 else np.full(n, 1.0 / n)
    return np.logaddexp.reduce(seg + np.log(np.maximum(w, 1e-300))[:, None], axis=0)


def energy_weights(energy: np.ndarray, how: str, gamma: float = 1.0, gate: float = 0.3) -> np.ndarray:
    """Frame weights of one window from its (already smoothed) RMS: soft (`energy`) or a 0/1 voice gate (`gated`)."""
    e = energy / max(float(energy.max()), 1e-12)
    return (e >= gate).astype(np.float64) if how == "gated" else e ** gamma


def spread_energy(energy: np.ndarray, spread: int) -> np.ndarray:
    """Max-filter over +-spread frames: a CTC emission a frame off the loud nucleus still gets its weight."""
    out = energy.astype(np.float64).copy()
    for s in range(1, spread + 1):
        out[s:] = np.maximum(out[s:], energy[:-s])
        out[:-s] = np.maximum(out[:-s], energy[s:])
    return out


def token_window(spans: list[tuple[int, int]], i: int, T: int, pad: int | None = None) -> tuple[int, int]:
    """[start, end) frames token i is read from. Wide (pad None): from the end of token i-1 to the start of token i+1,
    the blanks around the emission where a substituted consonant peaks. Narrow: its own span +- `pad` frames, inside
    the wide window (a vowel is its syllable's loud nucleus, not a sound hiding in the blanks)."""
    a = spans[i - 1][1] if i > 0 else 0
    b = spans[i + 1][0] if i + 1 < len(spans) else T
    a, b = min(a, spans[i][0]), max(b, spans[i][1])
    if pad is None:
        return a, b
    return max(a, spans[i][0] - pad), min(b, spans[i][1] + pad)


def compute_token_gop(logp: np.ndarray, spans: list[tuple[int, int]], i: int, tokens: list[int], blank: int,
                      cfg: GopConfig, vowel: bool = False, accept: list[set[int] | None] | None = None,
                      n_real: int | None = None, named: set[int] | None = None, energy: np.ndarray | None = None,
                      edge: tuple[set[int], set[int]] | None = None, pooled: bool | None = None) -> TokenScore:
    """GOP of token i (see score_tokens), its window and pooling chosen by its class.

    Presence is read from the wide window for every token: `peak` (behind the confidence) and `deleted` (target under
    deleted_peak and no competitor over deleted_comp), so a vowel aligned to a single frame is still found deleted.
    The competitor ratio of a vowel is read from its narrow window (cfg.vowel_window) and pooled with cfg.ratio_pool;
    a consonant is capped at its span +- cfg.consonant_window and keeps the max unless cfg.pool_vowels_only is off.
    `pooled` overrides the latter.
    `edge`: ids that are no competitor on the frames between the token's span and its left / right neighbour (a vowel
    gliding into j or w passes through i or u: coarticulation, not a substitution). `energy` is already spread.
    """
    T = logp.shape[0]
    accept = accept or [None] * len(tokens)
    a, b = token_window(spans, i, T, cfg.consonant_window) if not vowel else token_window(spans, i, T)
    ra, rb = token_window(spans, i, T, cfg.vowel_window) if vowel and cfg.vowel_window is not None else (a, b)
    tok, (s0, s1) = tokens[i], spans[i]
    seg = logp[a:b]
    peak = float(seg[:, tok].max())
    members = accept[i] or set()
    realised = max(members, key=lambda m: seg[:, m].max()) if members else None

    cols = (seg[:, :n_real] if n_real else seg).copy()
    if edge:
        for ids, lo, hi in ((edge[0], a, s0), (edge[1], s1, b)):
            ids = [x for x in ids if x < cols.shape[1]]
            if ids and hi > lo:
                cols[lo - a:hi - a, ids] = NEG
    mask = np.zeros(cols.shape[1], dtype=bool)
    mask[blank] = True
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(tokens):
            for x in {tokens[j], *(accept[j] or ())}:
                if x < len(mask):
                    mask[x] = True
    deleted = bool(peak < cfg.deleted_peak and float(np.where(mask, NEG, cols.max(0)).max()) < cfg.deleted_comp)

    how = cfg.ratio_pool if (pooled if pooled is not None else vowel or not cfg.pool_vowels_only) else "max"
    weight = None
    if how in ("energy", "gated"):
        if energy is not None:
            weight = energy_weights(energy[ra:rb], how, cfg.energy_gamma, cfg.energy_gate)
            weight[max(0, s0 - ra):max(0, s1 - ra)] = 1.0   # the frames the token was emitted on count in full
        how = "mean"
    rcols = cols[ra - a:rb - a]
    maxed = rcols.max(0)
    ratio = maxed.copy() if how == "max" else pool_frames(rcols, how, cfg.pool_k, cfg.pool_tau, weight)
    ratio[mask] = NEG
    comp = int(ratio.argmax())
    comp_peak = float(maxed[comp]) if ratio[comp] > NEG / 2 else NEG
    # A lone CTC spike can be a breath or microphone onset rather than a produced consonant. Keep duration
    # evidence separately from the peak so pronunciation policy can distinguish transients from substitutions.
    comp_frames = int(np.count_nonzero(rcols[:, comp] >= cfg.deleted_comp)) if ratio[comp] > NEG / 2 else 0
    tseg = logp[ra:rb, [tok]]
    target = float(tseg.max()) if how == "max" else float(pool_frames(tseg, how, cfg.pool_k, cfg.pool_tau, weight)[0])

    gop = target - max(target, float(ratio[comp]))
    confidence = min(1.0, float(np.exp(peak)) / cfg.confident_peak)
    score = 100.0 * float(np.exp(gop * cfg.leniency)) * float(np.sqrt(confidence))
    present = comp_peak > NEG / 2
    other = bool(present and named is not None and comp not in named)
    return TokenScore(round(score, 1), round(gop, 3), round(peak, 3), comp if present and not other else None,
                      deleted, realised, other, round(comp_peak, 3), comp_frames, (int(ra), int(rb)))


def score_tokens(logp: np.ndarray, align: Alignment, tokens: list[int], blank: int,
                 cfg: GopConfig | None = None, accept: list[set[int] | None] | None = None,
                 n_real: int | None = None, named: set[int] | None = None,
                 energy: np.ndarray | None = None, pooled: list[bool] | None = None,
                 vowels: list[bool] | None = None,
                 edges: list[tuple[set[int], set[int]] | None] | None = None) -> list[TokenScore]:
    """Per-token GOP (compute_token_gop for each token).

    The neighbours' own labels are excluded from the competitor set (coarticulation is not an error).
    A class token (a virtual column pooling its correct realisations, `accept[i]` their ids) is scored on the
    pooled column; its members are not competitors. Competitors come from the first `n_real` columns only, and
    one outside `named` is reported as `other` rather than by name.

    `vowels` marks the tokens that are vowels (narrow window, pooled ratio). Without it every token is read from the
    wide window and pooled, unless `pooled` marks which are. `energy` is the per-frame RMS the energy pools need;
    without it they fall back to the plain mean. `edges[i]`: see compute_token_gop.
    """
    cfg = cfg or GopConfig()
    if energy is not None:
        energy = spread_energy(np.asarray(energy)[:logp.shape[0]], cfg.energy_spread)
    return [compute_token_gop(logp, align.spans, i, tokens, blank, cfg, bool(vowels and vowels[i]), accept, n_real,
                              named, energy, edges[i] if edges else None,
                              (pooled[i] if pooled is not None else None if vowels is not None else True))
            for i in range(len(tokens))]


def greedy_decode(logp: np.ndarray, blank: int) -> list[int]:
    ids = logp.argmax(1)
    out, prev = [], None
    for x in ids:
        x = int(x)
        if x != prev and x != blank:
            out.append(x)
        prev = x
    return out


def edit_distance(a: list, b: list, same=None) -> int:
    same = same or (lambda x, y: x == y)
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (not same(x, y)))
    return d[len(b)]
