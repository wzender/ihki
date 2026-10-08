"""CTC forced alignment and goodness-of-pronunciation (GOP), in plain numpy.

Inputs are frame-level log-posteriors `logp` of shape (T, V) from a CTC phoneme model.
Nothing here depends on torch, so it is unit-testable with synthetic emissions.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

NEG = -1e30


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
    competitor: int | None  # strongest non-target, non-blank token in the window
    deleted: bool     # target barely present and nothing else either


@dataclass
class GopConfig:
    deleted_peak: float = float(np.log(0.05))    # target this weak ...
    deleted_comp: float = float(np.log(0.20))    # ... and no strong competitor -> deletion
    confident_peak: float = 0.30                 # target posterior at which the peak no longer lowers the score


def score_tokens(logp: np.ndarray, align: Alignment, tokens: list[int], blank: int,
                 cfg: GopConfig = GopConfig()) -> list[TokenScore]:
    """Per-token GOP.

    The window of token i runs from the end of token i-1 to the start of token i+1, so it covers
    the blank frames around the emission where a substituted phoneme typically peaks.
    The neighbours' own labels are excluded from the competitor set (coarticulation is not an error).
    """
    T = logp.shape[0]
    out: list[TokenScore] = []
    for i, tok in enumerate(tokens):
        a = align.spans[i - 1][1] if i > 0 else 0
        b = align.spans[i + 1][0] if i + 1 < len(tokens) else T
        a, b = min(a, align.spans[i][0]), max(b, align.spans[i][1])
        seg = logp[a:b]
        peak = float(seg[:, tok].max())

        masked = seg.max(0).copy()
        masked[blank] = NEG
        masked[tok] = NEG
        for j in (i - 1, i + 1):
            if 0 <= j < len(tokens):
                masked[tokens[j]] = NEG
        comp = int(masked.argmax())
        comp_peak = float(masked[comp])

        gop = peak - max(peak, comp_peak)
        deleted = bool(peak < cfg.deleted_peak and comp_peak < cfg.deleted_comp)
        confidence = min(1.0, float(np.exp(peak)) / cfg.confident_peak)
        score = 100.0 * float(np.exp(gop)) * float(np.sqrt(confidence))
        out.append(TokenScore(round(score, 1), round(gop, 3), round(peak, 3),
                              comp if comp_peak > NEG / 2 else None, deleted))
    return out


def greedy_decode(logp: np.ndarray, blank: int) -> list[int]:
    ids = logp.argmax(1)
    out, prev = [], None
    for x in ids:
        x = int(x)
        if x != prev and x != blank:
            out.append(x)
        prev = x
    return out


def edit_distance(a: list, b: list) -> int:
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (x != y))
    return d[len(b)]
