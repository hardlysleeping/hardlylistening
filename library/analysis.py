"""Tempo detection, server-side and silent.

Decodes the file with ffmpeg to mono 22.05 kHz PCM, builds an energy envelope,
turns it into an onset signal (positive energy jumps only — that is where drum
hits are), then fits a beat grid to that signal across 60–190 BPM.

Why a grid fit and not plain autocorrelation: autocorrelation finds *a* period,
and a kick on every beat plus a snare on every other beat is equally well
explained by half the real tempo. Fitting a grid with subdivisions, weighted by
a soft prior around 130 BPM, resolves that ambiguity far more reliably — a
half-tempo grid has to explain empty subdivision slots and loses.

No numpy on purpose: this needs to run on the system python of a Boring Atomic
box, and the envelope is a few thousand frames, so pure python is fast enough
(~0.5 s per track).
"""

from __future__ import annotations

import array
import math
import subprocess
from pathlib import Path

RATE = 22050
HOP = 512                       # ~23 ms per frame
MIN_BPM, MAX_BPM = 60.0, 190.0
PRIOR_CENTRE = 132.0            # dance/pop -> centre of mass
PRIOR_WIDTH = 0.55              # in octaves; wide enough to keep 70 and 180 alive


def _pcm(path: Path) -> array.array:
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1",
           "-ar", str(RATE), "-f", "s16le", "-"]
    out = subprocess.run(cmd, capture_output=True, timeout=300)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError("ffmpeg could not decode that file")
    samples = array.array("h")
    samples.frombytes(out.stdout[: len(out.stdout) // 2 * 2])
    return samples


def envelope(samples: array.array, hop: int = HOP) -> list[float]:
    """Root-mean-square energy per frame."""
    frames = []
    for start in range(0, len(samples) - hop, hop):
        block = samples[start:start + hop]
        total = 0
        for value in block:
            total += value * value
        frames.append((total / len(block)) ** 0.5)
    return frames


def onset(frames: list[float]) -> list[float]:
    """Positive change in energy: a drum hit, not a sustained pad."""
    out = [0.0]
    for index in range(1, len(frames)):
        delta = frames[index] - frames[index - 1]
        out.append(delta if delta > 0 else 0.0)
    return out


def _grid_score(signal: list[float], period: float, subdivisions: int = 4) -> tuple[float, int]:
    """How well a beat grid of `period` frames lines up with the onsets.

    Phase is searched in eighths of a beat — enough to land the grid on the kick.
    Returns (score, best_phase_in_frames).
    """
    if period < 2:
        return 0.0, 0
    step = period / subdivisions
    span = max(1, int(period))
    stride = max(1, span // 8)
    best, best_phase = 0.0, 0
    for phase in range(0, span, stride):
        total = 0.0
        count = 0
        position = float(phase)
        while position < len(signal):
            index = int(position)
            hit = signal[index]
            if index + 1 < len(signal):
                hit += 0.5 * signal[index + 1]
            total += hit
            count += 1
            position += step
        if count:
            score = total / count
            if score > best:
                best, best_phase = score, phase
    return best, best_phase


def detect(path: Path) -> dict:
    """{'bpm', 'confidence', 'phase'} — bpm 0.0 when nothing was found.

    `phase` is the offset of the first beat from the start of the file, in
    seconds, so the mixer can snap a cue to the grid. `confidence` is the margin
    over the next candidates (0..1), not a probability.
    """
    empty = {"bpm": 0.0, "confidence": 0.0, "phase": 0.0}
    try:
        samples = _pcm(path)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return empty
    if len(samples) < RATE * 8:                     # under eight seconds: too little to judge
        return empty
    signal = onset(envelope(samples))
    peak = max(signal) if signal else 0.0
    if peak <= 0:
        return empty
    signal = [value / peak for value in signal]

    hop_seconds = HOP / RATE
    scored = []
    for bpm in range(int(MIN_BPM), int(MAX_BPM) + 1):
        period = 60.0 / (bpm * hop_seconds)
        score, phase = _grid_score(signal, period)
        prior = math.exp(-0.5 * (math.log2(bpm / PRIOR_CENTRE) / PRIOR_WIDTH) ** 2)
        scored.append((score * prior, bpm, phase))
    scored.sort(key=lambda item: -item[0])

    best, bpm, phase = scored[0]
    rivals = [item[0] for item in scored[1:4]]
    margin = (best - sum(rivals) / len(rivals)) / best if best and rivals else 0.0
    return {
        "bpm": round(float(bpm), 1),
        "confidence": round(max(0.0, min(1.0, margin * 4)), 2),
        "phase": round(phase * hop_seconds, 3),
    }
