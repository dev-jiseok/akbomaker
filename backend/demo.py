"""An original synthetic example; never used as a substitute for SAM inference."""
import numpy as np

from .config import INSTRUMENTS, SAMPLE_RATE

BPM = 108
DURATION = 32 * 60 / BPM
TITLE = "Sunday, softly"


def generate() -> tuple[dict, dict]:
    stems, score_events = {}, {}
    rng = np.random.default_rng(42)
    quarter = 60 / BPM
    chords = [(60, 64, 67), (57, 60, 64), (53, 57, 60), (55, 59, 62)] * 2
    melody = [72, 76, 79, 76, 74, 72, 69, 72, 69, 72, 77, 76, 74, 71, 67, 71] * 2
    length = int(DURATION * SAMPLE_RATE)
    for inst in INSTRUMENTS:
        events = []
        samples = np.zeros(length, dtype=np.float32)
        for bar, chord in enumerate(chords):
            start = bar * 4 * quarter
            if inst == "vocal":
                events.extend((start + beat * quarter, start + (beat + 0.82) * quarter, melody[bar * 4 + beat], 0.75) for beat in range(4))
            elif inst == "bass":
                events.extend((start + beat * quarter, start + (beat + 0.8) * quarter, chord[0] - 24, 0.85) for beat in [0, 1, 2, 3])
            elif inst == "piano":
                events.extend((start + beat * quarter / 2, start + (beat + 0.8) * quarter / 2, chord[beat % 3], 0.7) for beat in range(8))
            elif inst == "guitar":
                events.extend((start + beat * quarter, start + (beat + 0.8) * quarter, pitch + 12, 0.45) for beat in [0, 2] for pitch in chord)
            elif inst == "synthesizer":
                events.extend((start, start + 3.8 * quarter, pitch, 0.4) for pitch in chord)
            else:
                events.extend((start + beat * quarter, start + (beat + 0.18) * quarter, 36 if beat % 2 == 0 else 38, 0.7) for beat in range(4))
                events.extend((start + beat * quarter / 2, start + (beat + 0.15) * quarter / 2, 42, 0.4) for beat in range(8))
        for start, end, pitch, amplitude in events:
            a, b = int(start * SAMPLE_RATE), min(length, int(end * SAMPLE_RATE))
            t = np.arange(b - a) / SAMPLE_RATE
            frequency = 440 * 2 ** ((pitch - 69) / 12)
            if inst == "drums":
                if pitch == 36:
                    tone = np.sin(2 * np.pi * (60 * t + 40 * 0.03 * (1 - np.exp(-t / 0.03)))) * np.exp(-t * 28)
                else:
                    noise = rng.standard_normal(len(t))
                    tone = (noise - np.roll(noise, 1) * 0.8) * np.exp(-t * (85 if pitch == 42 else 30))
            else:
                tone = np.sin(2 * np.pi * frequency * t)
                if inst in {"piano", "guitar", "bass"}:
                    tone += 0.3 * np.sin(2 * np.pi * frequency * 2 * t)
                    tone *= np.exp(-t * (5 if inst == "guitar" else 3))
                elif inst == "vocal":
                    tone = np.sin(2 * np.pi * frequency * t + 0.12 * np.sin(2 * np.pi * 5 * t))
                envelope = np.minimum(t / 0.01, 1) * np.minimum((len(t) / SAMPLE_RATE - t) / 0.03, 1)
                tone *= envelope
            samples[a:b] += (tone * amplitude * (0.07 if inst == "synthesizer" else 0.11)).astype(np.float32)
        stems[inst], score_events[inst] = samples, events
    return stems, score_events
