"""Uplifting "background music for a presentation", synthesized here so it has no license to worry about:
piano chord pulses over a warm pad, a soft four-on-the-floor kick with hats, then claps and a bright plucked
arpeggio; C–G–Am–F at 110 BPM, stereo, with a light reverb.

    python scripts/demo_video/music.py <out.wav> <seconds>
"""
import sys, wave
import numpy as np

SR, BPM = 44100, 110
BEAT = 60 / BPM
BAR = 4 * BEAT
rng = np.random.default_rng(11)
hz = lambda midi: 440 * 2 ** ((midi - 69) / 12)

CHORDS = [[60, 64, 67], [59, 62, 67], [57, 60, 64], [57, 60, 65]]      # C  G/B  Am  F  (right hand)
ROOTS = [36, 43, 45, 41]                                                 # C2 G2 A2 F2
ARP = [[72, 76, 79, 84], [71, 74, 79, 83], [72, 76, 81, 84], [72, 77, 81, 84]]
# when each part plays, by bar: the beat builds up, and the last two bars thin out to an ending
INTRO, BUILD = 2, 4


def env(n, attack, decay):
    t = np.arange(n) / SR
    return np.minimum(1, t / attack) * np.exp(-t / decay)


def piano(f, dur=1.8):
    n = int(dur * SR); t = np.arange(n) / SR
    tone = sum(a * np.sin(2 * np.pi * f * k * (1 + 0.0004 * k * k) * t) * np.exp(-t * (1.2 + 0.9 * k))
               for k, a in enumerate([1, 0.45, 0.22, 0.12, 0.06, 0.03], start=1))
    return tone * env(n, 0.004, 1.4)


def pad(freqs, dur):
    n = int(dur * SR); t = np.arange(n) / SR
    left = sum(np.sin(2 * np.pi * f * (1 - 0.003) * t) + 0.3 * np.sin(4 * np.pi * f * t) for f in freqs)
    right = sum(np.sin(2 * np.pi * f * (1 + 0.003) * t) + 0.3 * np.sin(4 * np.pi * f * t) for f in freqs)
    shape = np.minimum(1, t / 0.5) * np.minimum(1, (dur - t) / 0.4).clip(0)
    return np.stack([left, right], 1) * shape[:, None]


def pluck(f, dur=0.35):
    n = int(dur * SR); t = np.arange(n) / SR
    return (np.sin(2 * np.pi * f * t) + 0.3 * np.sign(np.sin(2 * np.pi * f * t)) * np.exp(-t * 30)) * env(n, 0.002, 0.09)


def kick():
    n = int(0.3 * SR); t = np.arange(n) / SR
    freq = 45 + 85 * np.exp(-t * 28)
    return np.sin(2 * np.pi * np.cumsum(freq) / SR) * env(n, 0.001, 0.12)


def noise_hit(dur, decay, hp=True):
    n = int(dur * SR)
    x = rng.uniform(-1, 1, n + 1)
    x = np.diff(x) if hp else x[:n]
    return x * env(n, 0.001, decay)


def clap():
    hit = np.zeros(int(0.25 * SR))
    for off in (0, 0.011, 0.022):                                      # a clap is a few hits close together
        h = noise_hit(0.2, 0.05, hp=False); i = int(off * SR)
        hit[i:i + len(h)] += h[: len(hit) - i]
    return np.convolve(hit, [1, 0, -1], "same")                          # thins the low end


def bass(f, dur):
    n = int(dur * SR); t = np.arange(n) / SR
    return (np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)) * np.minimum(1, t / 0.01) \
        * np.minimum(1, (dur - t) / 0.05).clip(0)


def place(track, sig, at, gain=1.0, pan=0.0):
    i = int(at * SR)
    if i >= len(track):
        return
    if sig.ndim == 1:
        sig = np.stack([sig * (1 - max(pan, 0)), sig * (1 + min(pan, 0))], 1)
    j = min(len(track), i + len(sig))
    track[i:j] += gain * sig[: j - i]


def reverb(x, seconds=1.6, wet=0.18):
    n = int(seconds * SR)
    out = np.empty_like(x)
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    for ch in range(2):
        ir = rng.standard_normal(n) * np.exp(-np.arange(n) / SR * 4.0)
        ir /= np.sqrt(np.sum(ir ** 2))
        out[:, ch] = np.fft.irfft(np.fft.rfft(x[:, ch], size) * np.fft.rfft(ir, size), size)[: len(x)]
    return x + wet * out


def render(seconds):
    n = int(seconds * SR) + 2 * SR
    keys, pads, drums, low, arps = (np.zeros((n, 2)) for _ in range(5))
    bars = int(np.ceil(seconds / BAR))
    duck = np.ones(n)                                                    # the bass and pad dip after each kick
    notes = {m: piano(hz(m)) for c in CHORDS for m in c} | {m: piano(hz(m)) for m in ROOTS}
    plucks = {m: pluck(hz(m)) for a in ARP for m in a}
    k, cl = kick(), clap()
    for bar in range(bars):
        s, ci = bar * BAR, bar % 4
        ending = bar >= bars - 2
        # piano: root on 1 and 3, chord pulses on the eighths
        place(keys, notes[ROOTS[ci]], s, 0.35); place(keys, notes[ROOTS[ci]], s + 2 * BEAT, 0.25)
        for e in range(8):
            for v, m in enumerate(CHORDS[ci]):
                place(keys, notes[m], s + e * BEAT / 2 + v * 0.004, 0.11 if e % 2 else 0.15, pan=(v - 1) * 0.3)
        place(pads, pad([hz(m - 12) for m in CHORDS[ci]], BAR + 0.3), s, 0.05)
        if bar >= INTRO and not ending:
            for b in range(4):
                place(drums, k, s + b * BEAT, 0.55)
                i = int((s + b * BEAT) * SR); j = min(n, i + int(0.22 * SR))
                duck[i:j] = np.minimum(duck[i:j], 0.45 + 0.55 * np.linspace(0, 1, j - i))
                place(drums, noise_hit(0.06, 0.018), s + (b + 0.5) * BEAT, 0.09, pan=0.3)
            place(low, bass(hz(ROOTS[ci]), BAR - 0.05), s, 0.32)
        if bar >= BUILD and not ending:
            place(drums, cl, s + BEAT, 0.22); place(drums, cl, s + 3 * BEAT, 0.22)
            for x in range(16):
                m = ARP[ci][[0, 1, 2, 3, 2, 1, 2, 3][x % 8]]
                place(arps, plucks[m], s + x * BEAT / 4, 0.07, pan=0.35 if x % 2 else -0.35)
    mix = keys + pads * duck[:, None] + low * duck[:, None] + arps + drums
    mix = reverb(mix)[: int(seconds * SR)]
    fade_in, fade_out = int(0.3 * SR), int(3 * SR)
    mix[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    mix[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None] ** 1.5
    return mix / np.max(np.abs(mix)) * 0.85


if __name__ == "__main__":
    out, secs = sys.argv[1], float(sys.argv[2])
    pcm = (render(secs) * 32767).astype(np.int16)
    with wave.open(out, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
    print(out, f"{secs:.1f}s")
