"""A track's sound, as a fixed-length vector (HL-205).

The fingerprint is what makes "sounds like this" possible at all: two recordings of the same
tune, or two tracks in the same corner of a genre, share a shape even when their tags, bitrates
and masters do not.

**This module is the only place in the tree that imports numpy**, and it does so directly — a
real dependency of this half by the owner's decision (2026-11-14), with no guarded probe and no
interpreted fallback. Everything else stays stdlib, so there is still no build step.

The vector is 60 numbers: 48 log-mel band means and 12 chroma (pitch-class) means, each block
centred and scaled on its own so the loudness of a master cannot decide what a track sounds
like, then the whole thing L2-normalised so cosine similarity is a dot product. Decoding is
ffmpeg, the same binary the tempo detector already uses, to mono 22.05 kHz.

Silence is not a fingerprint. A file that decodes to near-zero energy returns `None` rather
than a zero vector, because a zero vector scores 0 against everything and would quietly make a
broken file look like it belongs nowhere instead of being marked unanalysed.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np

RATE = 22050
N_FFT = 1024
HOP = 512                       # ~23 ms per frame, the same frame the tempo detector uses
MEL_BANDS = 48
CHROMA = 12
DIMS = MEL_BANDS + CHROMA
VERSION = 1                     # bump when the maths changes: stored vectors become stale
F_MIN, F_MAX = 40.0, 8000.0
CHROMA_LOW, CHROMA_HIGH = 65.0, 2000.0

#: Frames quieter than this fraction of the loudest frame are dropped before averaging, so a
#: spoken intro or a fade cannot outvote the part of the track that has the actual sound.
FLOOR = 1e-3

#: Below this peak RMS (16-bit scale, normalised to ±1) the file is silent for our purposes.
SILENT = 1e-5


class FingerprintError(ValueError):
    """The file could not be turned into a fingerprint. Never guessed around."""


def _pcm(path: Path) -> np.ndarray:
    """Decode with ffmpeg to mono float32 in [-1, 1] at `RATE`."""
    command = ['ffmpeg', '-v', 'error', '-i', str(path), '-ac', '1',
               '-ar', str(RATE), '-f', 's16le', '-']
    try:
        completed = subprocess.run(command, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise FingerprintError(f'ffmpeg could not run: {exc}') from exc
    if completed.returncode != 0 or not completed.stdout:
        raise FingerprintError('ffmpeg could not decode that file')
    body = completed.stdout[:len(completed.stdout) // 2 * 2]
    samples = np.frombuffer(body, dtype='<i2')
    if samples.size < N_FFT * 2:
        raise FingerprintError('the file is too short to fingerprint')
    return samples.astype(np.float32) / 32768.0


@lru_cache(maxsize=1)
def _mel_bank() -> np.ndarray:
    """Triangular mel filters over the rfft bins (48 × 513)."""
    frequencies = np.fft.rfftfreq(N_FFT, 1.0 / RATE)

    def mel(value):
        return 2595.0 * np.log10(1.0 + value / 700.0)

    def inverse(value):
        return 700.0 * (10.0 ** (value / 2595.0) - 1.0)

    edges = inverse(np.linspace(mel(F_MIN), mel(F_MAX), MEL_BANDS + 2))
    bank = np.zeros((MEL_BANDS, frequencies.size), dtype=np.float32)
    for band in range(MEL_BANDS):
        low, middle, high = edges[band], edges[band + 1], edges[band + 2]
        rising = (frequencies - low) / max(middle - low, 1e-9)
        falling = (high - frequencies) / max(high - middle, 1e-9)
        bank[band] = np.clip(np.minimum(rising, falling), 0.0, None)
    return bank


@lru_cache(maxsize=1)
def _pitch_classes() -> tuple[np.ndarray, np.ndarray]:
    """Which rfft bins fall in the chroma range, and the pitch class of each."""
    frequencies = np.fft.rfftfreq(N_FFT, 1.0 / RATE)
    valid = np.flatnonzero((frequencies >= CHROMA_LOW) & (frequencies <= CHROMA_HIGH))
    classes = np.round(12.0 * np.log2(frequencies[valid] / 440.0)).astype(int) % 12
    return valid, classes


def _centred(block: np.ndarray) -> np.ndarray:
    """Shape, not level: subtract the mean, scale the spread, never divide by zero."""
    centred = block - float(block.mean())
    spread = float(centred.std())
    return centred / spread if spread > 1e-9 else centred


def vector(path: str | Path) -> list[float] | None:
    """The fingerprint of one file, or `None` when there is no sound in it.

    Deterministic: the same bytes produce the same list, so a stored vector and a fresh one
    can be compared for equality. Raises `FingerprintError` when the file cannot be decoded.
    """
    samples = _pcm(Path(path))
    frames = np.lib.stride_tricks.sliding_window_view(samples, N_FFT)[::HOP]
    energy = np.sqrt(np.mean(frames.astype(np.float32) ** 2, axis=1))
    loudest = float(energy.max()) if energy.size else 0.0
    if loudest < SILENT:
        return None
    frames = frames[energy >= loudest * FLOOR]
    if frames.shape[0] < 4:
        return None

    window = np.hanning(N_FFT).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(frames * window, axis=1)).astype(np.float32)

    # Each frame's own shape first: subtracting the frame's mean makes the vector describe
    # where the energy sits, not how loud the master is — a quiet master and a loud one of the
    # same recording then fingerprint alike.
    log_mel = np.log(np.maximum(spectrum @ _mel_bank().T, 1e-10))
    log_mel -= log_mel.mean(axis=1, keepdims=True)
    mel = log_mel.mean(axis=0)

    valid, classes = _pitch_classes()
    chroma_frames = np.zeros((spectrum.shape[0], CHROMA), dtype=np.float32)
    for pitch_class in range(CHROMA):
        chroma_frames[:, pitch_class] = spectrum[:, valid][:, classes == pitch_class].sum(axis=1)
    log_chroma = np.log(np.maximum(chroma_frames, 1e-10))
    log_chroma -= log_chroma.mean(axis=1, keepdims=True)
    chroma = log_chroma.mean(axis=0)

    combined = np.concatenate([_centred(mel), _centred(chroma)]).astype(np.float32)
    norm = float(np.linalg.norm(combined))
    if norm < 1e-9:
        return None
    return (combined / norm).tolist()