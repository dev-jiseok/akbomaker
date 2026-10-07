"""Known-note analytical tones for diagnostics, never real-music accuracy.

These five fixtures intentionally exercise weaknesses hidden by the simpler
demo: repeated low notes, a six-string strum, inner chord voices, and sustained
polyphony. They are synthetic oscillators, not realistic instrument recordings.
Their exact notes/envelopes reproduce the 2026-10-06 local diagnostic experiment.
"""
import numpy as np

from .config import SAMPLE_RATE

FIXTURE_ID = "pitched-challenges-v1"
DURATION = 8.
BPM = 120  # Display grid only, not a beat annotation or inference ground truth.
INSTRUMENTS = ("vocal", "bass", "guitar", "piano", "synthesizer")
LIMITATIONS = (
    "Self-authored analytical tones, not recordings or realistic instrument simulations.",
    "Synthetic-only diagnostics; not real-song accuracy or a held-out music benchmark.",
    "Isolated inputs without source separation; onsets/offsets are nominal oscillator/envelope boundaries.",
    "BPM is a display grid only; raw note times are the reference before score quantization.",
    "Possible pitch substitutions are heuristic unmatched-note pairings, not causal classifier errors.",
)


def challenge_events():
    """Return fresh exact reference events, without inferred/rounded labels."""
    vocal = [(a, a + .32, pitch, .8)
             for a, pitch in zip((.5, 1, 1.5, 2, 3, 3.5, 4, 4.5), (60, 60, 67, 67, 72, 72, 69, 69))]
    bass = [(a, a + .42, pitch, .85)
            for a, pitch in zip((.5, 1.25, 2, 2.75, 3.5, 4.25, 5, 5.75), (28, 28, 35, 35, 40, 40, 43, 43))]
    guitar = [(start + index * .015, start + .85, pitch, .6)
              for start in (.5, 2, 3.5, 5)
              for index, pitch in enumerate((40, 47, 52, 56, 59, 64))]
    piano = [(start, start + .85, pitch, .65)
             for start in (.5, 2, 3.5, 5)
             for pitch in (36, 48, 55, 60, 64, 67)]
    synth = [(start, start + 2, pitch, .6)
             for start, chord in ((.5, (48, 60, 64, 67)), (3, (45, 57, 60, 64)), (5.5, (43, 55, 59, 62)))
             for pitch in chord]
    return {"vocal": vocal, "bass": bass, "guitar": guitar, "piano": piano, "synthesizer": synth}


def render_challenge(events, instrument):
    """Render the fixed 8-second diagnostic envelope and harmonic mixture."""
    if instrument not in INSTRUMENTS:
        raise ValueError("Unsupported pitched challenge instrument")
    audio = np.zeros(round(DURATION * SAMPLE_RATE), dtype=np.float32)
    for start, end, pitch, amplitude in events:
        a, b = round(start * SAMPLE_RATE), round(end * SAMPLE_RATE)
        t = np.arange(b - a) / SAMPLE_RATE
        frequency = 440 * 2 ** ((pitch - 69) / 12)
        phase = 2 * np.pi * frequency * t
        if instrument == "vocal":
            phase += .1 * np.sin(2 * np.pi * 5 * t)
        tone = np.sin(phase) + .3 * np.sin(2 * phase)
        if instrument in {"bass", "guitar", "piano"}:
            tone *= np.exp(-t * (3 if instrument == "guitar" else 1.2))
        attack = .03 if instrument == "synthesizer" else .008
        envelope = np.minimum(t / attack, 1) * np.minimum(((b - a) / SAMPLE_RATE - t) / .025, 1)
        audio[a:b] += np.asarray(tone * envelope * amplitude * .08, dtype=np.float32)
    return audio


def generate_challenges():
    """Return isolated audio and its exact reference notes, like demo.generate."""
    events = challenge_events()
    return {inst: render_challenge(events[inst], inst) for inst in INSTRUMENTS}, events
