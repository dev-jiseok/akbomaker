"""Opt-in offline separation strategy experiments; not a production default.

Independent prompts all see the original mix. Their outputs may overlap and do
not form an additive partition. This module deliberately does not normalize,
clip, rank, or claim accuracy for either strategy without reference stems.
"""
import math
import random
import threading

import numpy as np

from .config import INSTRUMENTS, SAMPLE_RATE

STRATEGIES = ("sequential", "independent")
MAX_SECONDS = 600
MAX_INPUT_BYTES = 128 * 1024 * 1024


class ExperimentCancelled(Exception):
    """An explicitly cancelled comparison retains its completed artifacts."""


def check_cancel(event):
    if event.is_set():
        raise ExperimentCancelled("Separation comparison was cancelled")


def validate_audio(audio, *, normalized=True):
    """Check a bounded mono float waveform without cropping or normalization."""
    if (not isinstance(audio, np.ndarray) or audio.ndim != 1 or audio.dtype.kind != "f"
            or not 0 < len(audio) <= MAX_SECONDS * SAMPLE_RATE):
        raise ValueError("Audio must be a nonempty mono floating-point waveform of at most 600 seconds")
    if not np.isfinite(audio).all():
        raise ValueError("Audio contains nonfinite samples")
    if normalized and float(np.max(np.abs(audio))) > 1.:
        raise ValueError("Input audio must already be normalized to [-1, 1]; no automatic scaling is applied")


def seed_rng(seed, *, torch_module=None):
    """Reset the same seed before each paired instrument; no torch import here."""
    random.seed(seed)
    np.random.seed(seed)
    if torch_module is not None:
        torch_module.manual_seed(seed)


def run_strategy(audio, strategy, extract, *, event=None, progress=None, emit=None,
                 seed=0, seed_callback=None):
    """Stream six stems using the existing ENGINE.extract callback contract.

    ``extract(request, instrument, event, progress_fraction)`` is called exactly
    once per instrument on a successful run, in INSTRUMENTS order. It receives
    an isolated writable float32 copy, and mutating that request is rejected.
    ``emit(instrument, samples, index)`` receives each completed validated stem;
    it may write it to disk instead of retaining all twelve large waveforms.
    ``progress(instrument, index, fraction)`` includes cancellation checks.

    Sequential subtraction reuses each exact extracted target and returns the
    final residual. Independent returns None: its overlap is neither removed
    nor turned into an invented additive residual. No extraction is rerun.
    """
    if strategy not in STRATEGIES:
        raise ValueError("Strategy must be sequential or independent")
    if type(seed) is not int or not 0 <= seed <= 2 ** 32 - 1:
        raise ValueError("Seed must be an integer in 0..2^32-1")
    validate_audio(audio)
    event = event if event is not None else threading.Event()
    progress = progress if progress is not None else lambda *_: None
    emit = emit if emit is not None else lambda *_: None
    seed_callback = seed_callback if seed_callback is not None else seed_rng
    original = np.array(audio, dtype=np.float32, copy=True, order="C")
    residual = original.copy() if strategy == "sequential" else None
    for index, instrument in enumerate(INSTRUMENTS):
        check_cancel(event)
        source = residual if strategy == "sequential" else original
        request = source.copy()
        instrument_seed = (seed + index) % 2 ** 32
        seed_callback(instrument_seed)
        last_fraction = 0.

        def report(fraction):
            nonlocal last_fraction
            check_cancel(event)
            if (isinstance(fraction, bool) or not isinstance(fraction, (int, float, np.floating))
                    or not math.isfinite(fraction) or not 0 <= fraction <= 1):
                raise ValueError("Extraction progress must be a finite fraction in [0, 1]")
            if fraction < last_fraction:
                raise ValueError("Extraction progress must not move backwards")
            last_fraction = float(fraction)
            progress(instrument, index, last_fraction)

        report(0.)
        target = extract(request, instrument, event, report)
        check_cancel(event)
        if not np.array_equal(request, source):
            raise ValueError("Extraction unexpectedly mutated its input waveform")
        if (not isinstance(target, np.ndarray) or target.shape != original.shape
                or target.dtype.kind != "f" or not np.isfinite(target).all()):
            raise ValueError("Extraction returned an invalid mono waveform or sample count")
        # A view/alias returned by an injected extractor must not allow emit to
        # corrupt request/source data. Float WAVs retain values outside [-1, 1].
        with np.errstate(over="ignore", invalid="ignore"):
            target = np.array(target, dtype=np.float32, copy=True, order="C")
        if not np.isfinite(target).all():
            raise ValueError("Extraction cannot be represented as finite float32 samples")
        if strategy == "sequential":
            with np.errstate(over="ignore", invalid="ignore"):
                residual = source - target
            if not np.isfinite(residual).all():
                raise ValueError("Sequential subtraction produced a nonfinite residual")
        # Subtract before emit, so even a caller modifying its own emitted array
        # cannot change the next sequential input. Production save paths do not
        # mutate samples, but the injected API should maintain this isolation.
        emit(instrument, target, index)
        report(1.)
    return residual
