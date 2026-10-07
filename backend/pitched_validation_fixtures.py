"""Frozen synthetic validation cases, independent of development fixtures.

Designed before inspecting candidate-method predictions. These analytical
waveforms are NOT realistic recordings or evidence of real-music accuracy.
Never change their notes, timbres, or envelopes to improve a model's results;
introduce a new fixture version when the evaluation specification changes.
"""
import numpy as np

from .config import SAMPLE_RATE

FIXTURE_ID = "pitched-validation-v1"
DURATION = 6.
LIMITATIONS = (
    "Self-authored harmonic oscillators, not real instrument recordings or real-song accuracy.",
    "Frozen separately from development fixtures; not a claim about exclusion from model training data.",
    "No source separation, acoustic room, performer variability, or annotated real music is represented.",
    "Reference times are nominal oscillator/envelope boundaries; small detuning and vocal vibrato retain nominal MIDI labels.",
    "True octave doubling is intentional and must not be removed as if all upper harmonics were false notes.",
)


def _case(case_id, instrument, description, events, *, harmonics, attack=.006,
          release=.035, decay=0., detune=(0.,), modulation_depth=0., modulation_hz=1.7,
          vibrato_cents=0., vibrato_hz=5.4):
    return {
        "id": case_id, "instrument": instrument, "description": description,
        "duration": DURATION, "sample_rate": SAMPLE_RATE, "events": events,
        "render": {"harmonics": harmonics, "attack_seconds": attack,
                   "release_seconds": release, "decay_rate": decay,
                   "detune_cents": detune, "modulation_depth": modulation_depth,
                   "modulation_hz": modulation_hz, "vibrato_cents": vibrato_cents,
                   "vibrato_hz": vibrato_hz, "gain": .095, "harmonic_phase_step": .19},
    }


def validation_cases():
    """Return fresh fixed case specifications, before rendering/inference."""
    return [
        _case("guitar_true_octaves", "guitar", "Three genuine octave dyads; protect octave doubling.",
              [(start, start + 1.05, pitch, .78)
               for start, pitches in ((.4, (45, 57)), (2., (50, 62)), (3.6, (52, 64))) for pitch in pitches],
              harmonics=(1., .42, .24, .16, .09), decay=1.8),
        _case("guitar_detuned_strums", "guitar", "Transposed six-string chords with staggered attacks and small detuning.",
              [(start + index * .021, start + 1.3, pitch, .72)
               for start, chord in ((.5, (42, 49, 54, 58, 61, 66)), (3., (43, 50, 55, 59, 62, 67)))
               for index, pitch in enumerate(chord)],
              harmonics=(1., .23, .39, .11, .07), decay=2.3, detune=(-4., 3., -2., 4., -3., 2.)),
        _case("guitar_high_and_repeated", "guitar", "Isolated high notes plus five same-pitch attacks separated by 60 ms rests.",
              [(.4, .85, 76, .75), (1.3, 1.9, 81, .7)]
              + [(start, start + .31, 69, .8) for start in (2.35, 2.72, 3.09, 3.46, 3.83)]
              + [(4.65, 5.45, 88, .7)],
              harmonics=(1., .36, .19, .08, .05), attack=.004, release=.018, decay=2.),
        _case("piano_true_octave_stacks", "piano", "Genuine four-octave stacks across both hands.",
              [(start, start + 1.1, pitch, .68)
               for start, pitches in ((.45, (38, 50, 62, 74)), (2.4, (43, 55, 67, 79)), (4.35, (40, 52, 64, 76)))
               for pitch in pitches],
              harmonics=(1., .28, .18, .11, .065, .035), attack=.005, decay=.65),
        _case("piano_transposed_inner_voices", "piano", "Six-note transposed chords with inner voices and detuned partial fundamentals.",
              [(start, start + 1.55, pitch, .65)
               for start, chord in ((.5, (37, 49, 56, 61, 65, 68)), (2.8, (42, 54, 61, 66, 70, 73)))
               for pitch in chord],
              harmonics=(1., .18, .31, .09, .06), attack=.007, decay=.9, detune=(3., -3., 2., -2., 1., -1.)),
        _case("piano_repeated_and_high", "piano", "Repeated notes with 60 ms gaps followed by isolated high-register notes.",
              [(start, start + .28, 67, .78) for start in (.4, .74, 1.08, 1.42, 1.76)]
              + [(2.5, 3.1, 84, .75), (3.4, 4., 96, .75), (4.5, 5.1, 88, .75)],
              harmonics=(1., .32, .14, .08, .04), attack=.004, release=.02, decay=1.15),
        _case("synth_true_octave_chords", "synthesizer", "Sustained chords containing genuine octave doubling.",
              [(start, end, pitch, .67)
               for start, end, chord in ((.5, 2.3, (46, 58, 65, 70)), (3.1, 5.3, (51, 63, 70, 75)))
               for pitch in chord],
              harmonics=(1., .17, .48, .12, .19, .06), attack=.045, release=.07,
              detune=(-3., 3., -2., 2.)),
        _case("synth_amplitude_varying_sustain", "synthesizer", "One four-note chord with deep nonzero tremolo, no repeated attacks.",
              [(.5, 5.4, pitch, .64) for pitch in (54, 61, 66, 70)],
              harmonics=(1., .15, .4, .1, .18, .07), attack=.06, release=.09,
              modulation_depth=.8, modulation_hz=1.6, detune=(-4., 2., 4., -2.)),
        _case("bass_low_octave_leaps", "bass", "Low-register notes and actual octave leaps, all separated by rests.",
              [(start, start + .58, pitch, .85)
               for start, pitch in zip((.45, 1.25, 2.05, 2.85, 3.65, 4.45), (25, 37, 30, 42, 25, 37))],
              harmonics=(1., .48, .21, .13), attack=.009, release=.035, decay=.7),
        _case("vocal_vibrato_and_repeat", "vocal", "Moderate vibrato, a repeated nominal pitch, and harmonic-rich lead tones.",
              [(start, start + .59, pitch, .76)
               for start, pitch in zip((.45, 1.22, 1.99, 2.76, 3.53, 4.3), (59, 62, 65, 65, 70, 74))],
              harmonics=(1., .35, .55, .1, .06), attack=.023, release=.045,
              vibrato_cents=25., vibrato_hz=5.4, modulation_depth=.15, modulation_hz=3.1),
    ]


def render_validation_case(case):
    """Render one frozen specification; no fitting, randomness, or model output."""
    sr, settings = case["sample_rate"], case["render"]
    audio = np.zeros(round(case["duration"] * sr), dtype=np.float32)
    weights = settings["harmonics"]
    for note_index, (start, end, pitch, amplitude) in enumerate(case["events"]):
        a, b = round(start * sr), round(end * sr)
        t = np.arange(b - a) / sr
        detune = settings["detune_cents"][note_index % len(settings["detune_cents"])]
        frequency = 440 * 2 ** ((pitch - 69) / 12) * 2 ** (detune / 1200)
        if settings["vibrato_cents"]:
            instantaneous = frequency * 2 ** (settings["vibrato_cents"] * np.sin(2 * np.pi * settings["vibrato_hz"] * t) / 1200)
            phase = 2 * np.pi * (np.cumsum(instantaneous) - instantaneous[0]) / sr
        else:
            phase = 2 * np.pi * frequency * t
        tone = np.zeros(len(t), dtype=np.float64)
        for harmonic, weight in enumerate(weights, start=1):
            tone += weight * np.sin(harmonic * phase + (harmonic - 1) * settings["harmonic_phase_step"])
        tone /= sum(abs(weight) for weight in weights)
        envelope = np.minimum(t / settings["attack_seconds"], 1.)
        envelope *= np.minimum(((b - a) / sr - t) / settings["release_seconds"], 1.)
        envelope *= np.exp(-settings["decay_rate"] * t)
        envelope *= 1 - settings["modulation_depth"] / 2 + settings["modulation_depth"] / 2 * np.cos(2 * np.pi * settings["modulation_hz"] * t)
        audio[a:b] += np.asarray(tone * envelope * amplitude * settings["gain"], dtype=np.float32)
    return audio


def generate_validation_cases():
    """Yield specifications with an added mono float32 `audio` array."""
    for case in validation_cases():
        yield {**case, "audio": render_validation_case(case)}
