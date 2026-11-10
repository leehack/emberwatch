"""Synthesizes the game's sound effects as 16-bit mono WAV files.

Everything is generated from noise and oscillators, so the sounds carry no
third-party license. Usage: python3 tool/synth_audio.py [output_dir]
"""
import math
import os
import random
import struct
import sys
import wave

RATE = 44100
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(__file__), "..", "assets", "audio")
rng = random.Random(7)


def render(seconds, fn):
    """Samples fn(t) for `seconds`, where fn returns a float near -1..1."""
    return [fn(i / RATE) for i in range(int(seconds * RATE))]


class LowPass:
    """One-pole low-pass whose cutoff can change per sample."""

    def __init__(self):
        self.y = 0.0

    def __call__(self, x, cutoff):
        a = 1 - math.exp(-2 * math.pi * cutoff / RATE)
        self.y += a * (x - self.y)
        return self.y


def noise():
    return rng.uniform(-1, 1)


def sweep(t, f0, f1, rate):
    """Phase of a sine gliding exponentially from f0 to f1."""
    return 2 * math.pi * (f1 * t + (f0 - f1) * (1 - math.exp(-rate * t)) / rate)


def write(name, samples, gain, loop=False):
    peak = max(1e-9, max(abs(s) for s in samples))
    # Fade the last few milliseconds so a one-shot never ends on a click.
    fade = 0 if loop else min(len(samples), int(0.008 * RATE))
    for i in range(fade):
        samples[-1 - i] *= i / fade
    # A one-shot that starts away from zero opens with a click too.
    attack = 0 if loop else min(len(samples), int(0.0015 * RATE))
    for i in range(attack):
        samples[i] *= i / attack
    data = b"".join(
        struct.pack("<h", int(max(-1, min(1, s / peak * gain)) * 32767))
        for s in samples)
    with wave.open(os.path.join(OUT, name + ".wav"), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(data)


def shot():
    lp = LowPass()

    def fn(t):
        crack = lp(noise(), 9000 * math.exp(-t * 22) + 500) * math.exp(-t * 26)
        thump = math.sin(sweep(t, 190, 55, 30)) * math.exp(-t * 18)
        return math.tanh(3.2 * (crack * 1.3 + thump * 0.8))

    return render(0.3, fn)


def explosion():
    lp, lp2 = LowPass(), LowPass()

    def fn(t):
        body = lp(noise(), 2400 * math.exp(-t * 5) + 90) * math.exp(-t * 2.6)
        crackle = lp2(noise(), 6000) * math.exp(-t * 14) * 0.6
        sub = math.sin(sweep(t, 75, 30, 4)) * math.exp(-t * 3.2)
        attack = min(1.0, t / 0.004)
        return math.tanh(3.5 * attack * (body * 2.2 + crackle + sub * 0.9))

    return render(1.6, fn)


def zap():
    lp = LowPass()
    state = {"hold": 0.0, "until": 0.0}

    def fn(t):
        # A stepped random pitch gives the buzz its unstable, electric edge.
        if t >= state["until"]:
            state["hold"] = rng.uniform(0.6, 1.4)
            state["until"] = t + rng.uniform(0.002, 0.012)
        tone = math.sin(sweep(t, 2600, 420, 9) * state["hold"])
        square = 1.0 if tone > 0 else -1.0
        hiss = noise() - lp(noise(), 3000)
        return (square * 0.5 + hiss * 0.6) * math.exp(-t * 9)

    return render(0.38, fn)


def squish():
    lp = LowPass()

    def fn(t):
        wet = lp(noise(), 1800 * math.exp(-t * 20) + 200) * math.exp(-t * 30)
        pop = math.sin(sweep(t, 320, 80, 40)) * math.exp(-t * 34)
        return wet * 1.4 + pop

    return render(0.17, fn)


def ember():
    def fn(t):
        env = math.exp(-t * 22) * min(1.0, t / 0.003)
        return env * (math.sin(sweep(t, 1250, 1900, 30)) +
                      0.35 * math.sin(sweep(t, 2500, 3800, 30)))

    return render(0.16, fn)


def levelup():
    notes = [440.0, 554.37, 659.25, 880.0, 1108.73]

    def fn(t):
        out = 0.0
        for i, f in enumerate(notes):
            start = i * 0.085
            if t < start:
                continue
            u = t - start
            env = math.exp(-u * 4.5) * min(1.0, u / 0.004)
            out += env * (math.sin(2 * math.pi * f * u) +
                          0.3 * math.sin(2 * math.pi * f * 2.01 * u))
        return out

    return render(1.3, fn)


def hurt():
    lp = LowPass()

    def fn(t):
        thud = math.sin(sweep(t, 130, 50, 16)) * math.exp(-t * 14)
        grit = lp(noise(), 900) * math.exp(-t * 22)
        return math.tanh(2.5 * (thud + grit * 0.8))

    return render(0.3, fn)


def pulse():
    lp, lp2 = LowPass(), LowPass()

    def fn(t):
        # Band-passed noise sweeping down reads as a whoosh.
        band = lp(noise(), 3500 * math.exp(-t * 5) + 150)
        band -= lp2(band, 120)
        env = min(1.0, t / 0.02) * math.exp(-t * 4.5)
        sub = math.sin(sweep(t, 110, 40, 5)) * math.exp(-t * 5)
        return band * env * 2 + sub * 0.9

    return render(0.9, fn)


def growl():
    lp = LowPass()

    def fn(t):
        f = 46 + 6 * math.sin(2 * math.pi * 0.7 * t)
        phase = (t * f) % 1.0
        saw = lp(2 * phase - 1, 500)
        tremolo = 0.6 + 0.4 * math.sin(2 * math.pi * 17 * t)
        env = min(1.0, t / 0.25) * min(1.0, (2.2 - t) / 0.7)
        return math.tanh(2.2 * saw * tremolo) * env

    return render(2.2, fn)


def ambience():
    """A loopable night bed: wind plus a few crickets."""
    length = 12.0
    lp, lp2 = LowPass(), LowPass()
    crickets = [(4300, 0.75, 0.0), (4750, 1.0, 0.31), (3900, 1.5, 0.6)]

    def fn(t):
        # Gust periods divide the loop length, so the level matches at the seam.
        gust = 0.55 + 0.3 * math.sin(2 * math.pi * t / 6) + \
            0.15 * math.sin(2 * math.pi * t / 4 + 1)
        wind = lp(lp2(noise(), 900), 320 + 180 * gust) * gust * 2.4
        chirps = 0.0
        for freq, period, offset in crickets:
            u = (t + offset) % period
            # Three short pulses, then silence until the next period.
            if u < 0.21 and (u % 0.07) < 0.035:
                chirps += math.sin(2 * math.pi * freq * t) * \
                    math.sin(math.pi * (u % 0.07) / 0.035)
        return wind + chirps * 0.05

    samples = render(length + 1.0, fn)
    # Crossfade the extra second over the start so the filter state loops.
    n = int(length * RATE)
    tail = samples[n:]
    for i, s in enumerate(tail):
        k = i / len(tail)
        samples[i] = samples[i] * k + s * (1 - k)
    return samples[:n]


os.makedirs(OUT, exist_ok=True)
for name, build, gain in [
    ("shot", shot, 0.9), ("explosion", explosion, 0.95), ("zap", zap, 0.8),
    ("squish", squish, 0.8), ("ember", ember, 0.6), ("levelup", levelup, 0.8),
    ("hurt", hurt, 0.9), ("pulse", pulse, 0.9), ("growl", growl, 0.9),
    ("ambience", ambience, 0.7),
]:
    samples = build()
    write(name, samples, gain, loop=name == "ambience")
    print(name, f"{len(samples) / RATE:.2f}s")
