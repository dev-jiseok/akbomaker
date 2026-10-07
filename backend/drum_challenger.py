"""Contracts for optional, independent drum-model research (never auto-enabled).

STRUM lanes cannot distinguish open/closed/pedal hats, rim technique, or exact
tom tuning. Representative GM pitches are transport labels, not articulation
claims. Existing scores and recognizer defaults do not call this module.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

METHOD = 'strum-v14-v12c-raw-v1'
SOURCE_REVISION = '9f420cb6550284d15188e9b69f27614ee62fa731'
MODEL_REVISION = '6f8997f53c6eb04e0cb38f679c593d27ae89ab7f'
SOURCE_FILES = {
    'src/models/drums_v13.py': '47bd26ccf9e375aace5b09a7362dca4f9325a4d93feff97cd662a743c0331340',
    'src/models/onset_classifier.py': 'd204e3cf208929cd200762fa6f6648d97f3446050f8ac5bed6f553fff47cb6ba',
}
WEIGHTS = {
    'v14.pt': ('drums/drums_v14/best.pt', 508568320, '91b1b35c006b961b829e0727fbdbc3bae09955c3d0821f57717b91472def2850'),
    'v12c.pt': ('drums_classifier_ensemble/onset_classifier_v12_clean/best_f1.pt', 56004551, 'fc2c8b2541aa22fd9490c3882fb744ef66849f2f43780e05693c625cc68b53bc'),
}
CLASS_NAMES = ('kick', 'snare', 'hihat-unspecified', 'high-tom', 'ride', 'low-tom', 'crash', 'floor-tom')
REPRESENTATIVE_GM = (36, 38, 42, 50, 51, 47, 49, 45)
# Five-family evaluation is the honest common denominator with human MDB
# labels. Unsupported percussion stays as-is and cannot silently disappear.
FAMILY_GM = {35: 36, 36: 36, 37: 38, 38: 38, 40: 38,
             42: 42, 44: 42, 46: 42, 41: 45, 43: 45, 45: 45,
             47: 45, 48: 45, 50: 45, 49: 49, 51: 49, 52: 49,
             53: 49, 55: 49, 57: 49, 59: 49}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def verify_assets(source, weights):
    """Hash before importing model source or deserializing any checkpoint."""
    checked = {}
    for name, expected in SOURCE_FILES.items():
        path = Path(source) / name
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f'Unverified STRUM source: {name}')
        checked[name] = expected
    for name, (_, size, expected) in WEIGHTS.items():
        path = Path(weights) / name
        if not path.is_file() or path.stat().st_size != size or sha256(path) != expected:
            raise ValueError(f'Unverified STRUM weights: {name}')
        checked[name] = expected
    return checked


def segment_starts(total_frames, segment_frames, hop_frames):
    """Overlapping inference must cover the final frame, including short tails."""
    if any(type(v) is not int or v <= 0 for v in (total_frames, segment_frames, hop_frames)):
        raise ValueError('Frame counts must be positive integers')
    if hop_frames > segment_frames:
        raise ValueError('Inference windows must not leave gaps')
    if total_frames <= segment_frames:
        return [0]
    starts = list(range(0, total_frames - segment_frames + 1, hop_frames))
    if starts[-1] + segment_frames < total_frames:
        starts.append(starts[-1] + hop_frames)
    return starts


def representative_events(onsets, probabilities, duration, threshold=.5):
    """Multi-label acoustic decisions only; no forced class, grid, or fill."""
    if not math.isfinite(duration) or duration <= 0 or not 0 < threshold <= 1:
        raise ValueError('Invalid duration or probability threshold')
    if len(onsets) != len(probabilities):
        raise ValueError('Onset and class probability rows differ')
    events, evidence = [], []
    for start, row in zip(onsets, probabilities):
        if len(row) != 8 or not math.isfinite(start) or not 0 <= start < duration:
            raise ValueError('Invalid onset or class vector')
        if any(not math.isfinite(p) or not 0 <= p <= 1 for p in row):
            raise ValueError('Class probabilities must be finite in [0,1]')
        classes = []
        for index, value in enumerate(row):
            if value >= threshold:
                # No velocity prediction in this challenger; fixed amplitude
                # avoids disguising classification probabilities as dynamics.
                events.append((float(start), min(duration, start + .05), REPRESENTATIVE_GM[index], .8))
                classes.append(CLASS_NAMES[index])
        evidence.append({'onset_seconds': float(start), 'probabilities': [float(v) for v in row], 'classes': classes})
    return sorted(events), evidence


def family_events(events):
    """Do not deduplicate folded simultaneous events or drop unknown classes."""
    return [(a, b, FAMILY_GM.get(p, p), v) for a, b, p, v in events]
