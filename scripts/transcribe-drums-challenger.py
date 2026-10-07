"""Offline-only STRUM V14+V12c raw acoustic challenger, NOT its game pipeline.

Requires explicitly prepared local source/weights and isolated torch/librosa.
No download, external service, original-score overwrite, rhythmic filling,
reference-derived shift, or auto-selection is performed. Output is review JSON,
not a ready-to-use articulation score. Published STRUM metrics do not describe
this deliberately minimal two-model configuration.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.drum_challenger import (METHOD, SOURCE_REVISION, MODEL_REVISION, CLASS_NAMES,
                                    sha256, verify_assets, segment_starts, representative_events)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--weights', type=Path, required=True)
    parser.add_argument('--audio', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve() == args.audio.resolve():
        parser.error('Output must be a new research artifact; existing files are never overwritten')
    if not 1 <= args.threads <= 16:
        parser.error('Use 1..16 CPU threads')
    implementation_files = ('scripts/transcribe-drums-challenger.py', 'backend/drum_challenger.py')
    implementation_sha = {name: sha256(ROOT / name) for name in implementation_files}
    assets = verify_assets(args.source, args.weights)
    import importlib.metadata
    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio
    from scipy.signal import find_peaks

    info = sf.info(args.audio)
    if not 1 <= info.channels <= 2 or not 0 < info.duration <= 60:
        parser.error('Research adapter accepts mono/stereo excerpts up to 60 seconds')
    started = time.monotonic()
    torch.set_num_threads(args.threads)
    torch.manual_seed(0)
    input_sha = sha256(args.audio)
    y, sr = librosa.load(args.audio, sr=44100, mono=True)
    if not np.isfinite(y).all():
        raise ValueError('Nonfinite audio')
    duration = len(y) / sr
    # Only numeric NumPy scalar metadata is allowed beyond torch's safe loader.
    # No weights_only=False / arbitrary checkpoint object execution fallback.
    numeric_globals = [(np.core.multiarray.scalar, 'numpy._core.multiarray.scalar'),
                       np.dtype, type(np.dtype('float64')), type(np.dtype('float32'))]
    with torch.serialization.safe_globals(numeric_globals):
        onset_checkpoint = torch.load(args.weights / 'v14.pt', map_location='cpu', weights_only=True)
        classifier_checkpoint = torch.load(args.weights / 'v12c.pt', map_location='cpu', weights_only=True)
    onset_module = load_module('akbo_strum_onset', args.source / 'src/models/drums_v13.py')
    classifier_module = load_module('akbo_strum_classifier', args.source / 'src/models/onset_classifier.py')
    config = dict(onset_checkpoint['config']['model'])
    for key in ('type', 'sample_rate', 'hop_length', 'n_fft'):
        config.pop(key)
    config['dropout'] = 0.
    onset_model = onset_module.TwoStageDrumsCRNN(**config).eval()
    onset_model.load_state_dict(onset_checkpoint['model_state_dict'], strict=True)
    config = dict(classifier_checkpoint['config']['model'])
    config['dropout'] = 0.
    classifier = classifier_module.OnsetClassifier(**config).eval()
    classifier.load_state_dict(classifier_checkpoint['model_state_dict'], strict=True)
    del onset_checkpoint, classifier_checkpoint
    mel = torchaudio.transforms.MelSpectrogram(sample_rate=sr, n_fft=2048, hop_length=512, n_mels=128)
    fine = torchaudio.transforms.MelSpectrogram(sample_rate=sr, n_fft=1024, hop_length=256, n_mels=128)
    coarse = torchaudio.transforms.MelSpectrogram(sample_rate=sr, n_fft=4096, hop_length=512, n_mels=128)
    with torch.inference_mode():
        features = torch.log(mel(torch.from_numpy(y)[None]) + 1e-8)
        frames = features.shape[-1]
        width, hop = int(10 * sr / 512), int(int(10 * sr / 512) * .5)
        starts = segment_starts(frames, width, hop)
        onset_probs, counts = np.zeros(frames), np.zeros(frames)
        for start in starts:
            end = min(start + width, frames)
            chunk = torch.nn.functional.pad(features[:, :, start:end], (0, width - (end - start)))
            probs = onset_model(chunk[None])['onset_probs'][0, :end-start, 0].numpy()
            onset_probs[start:end] += probs
            counts[start:end] += 1
        if (counts == 0).any():
            raise RuntimeError('Uncovered audio frames')
        onset_probs /= counts
        # Use checkpoint's published onset default .5. No local tuning against
        # these references. A .5 independent sigmoid rule is fixed for classes.
        peaks, _ = find_peaks(onset_probs, height=.5, distance=max(1, int(.020 * sr / 512)))
        peaks = peaks[peaks * 512 / sr < duration]
        onsets = peaks * 512 / sr
        cqt = np.log(np.abs(librosa.cqt(y=y, sr=sr, hop_length=512, fmin=30,
                                       n_bins=144, bins_per_octave=24)) + 1e-8)[:128].astype('float32')
        n = len(onsets)
        windows_fine = np.zeros((n, 1, 128, 87), dtype='float32')
        windows_coarse = np.zeros((n, 1, 128, 44), dtype='float32')
        windows_cqt = np.zeros((n, 1, 128, 44), dtype='float32')
        boundary_count = 0
        for index, onset in enumerate(onsets):
            center = int(onset * sr)
            left, right = center - 4410, center + 17640
            waveform = np.zeros(22050, dtype='float32')
            source_left, source_right = max(0, left), min(len(y), right)
            waveform[source_left-left:source_right-left] = y[source_left:source_right]
            boundary_count += int(left < 0 or right > len(y))
            wave_tensor = torch.from_numpy(waveform)[None]
            windows_fine[index] = torch.log(fine(wave_tensor) + 1e-8).numpy()[:, :, :87]
            windows_coarse[index] = torch.log(coarse(wave_tensor) + 1e-8).numpy()[:, :, :44]
            frame_left = left // 512
            src_left, src_right = max(0, frame_left), min(cqt.shape[-1], frame_left + 44)
            windows_cqt[index, 0, :, src_left-frame_left:src_right-frame_left] = cqt[:, src_left:src_right]
        class_probs = np.zeros((n, 8), dtype='float32')
        # Same two-pass neighbor-context idea as source; 1 model, NOT an ensemble.
        for context_pass in range(2):
            context = np.zeros((n, 64), dtype='float32')
            if context_pass:
                for index in range(n):
                    neighbors = list(range(index-4, index)) + list(range(index+1, index+5))
                    for slot, other in enumerate(neighbors):
                        if 0 <= other < n:
                            context[index, slot*8:(slot+1)*8] = class_probs[other]
            for start in range(0, n, 32):
                stop = min(n, start + 32)
                logits = classifier(torch.from_numpy(windows_fine[start:stop]),
                                    torch.from_numpy(windows_coarse[start:stop]),
                                    torch.from_numpy(context[start:stop]),
                                    mel_lowfreq=torch.from_numpy(windows_cqt[start:stop]))
                class_probs[start:stop] = torch.sigmoid(logits).numpy()
    events, evidence = representative_events(onsets, class_probs, duration)
    report = {
        'method': METHOD, 'research_only': True, 'accuracy_validated': False,
        'source_revision': SOURCE_REVISION, 'model_revision': MODEL_REVISION,
        'source_url': 'https://github.com/opria123/strum',
        'model_url': 'https://huggingface.co/opria123/strum',
        'license': 'Upstream source README and model card declare MIT; dependencies/data require separate review',
        'assets_sha256': assets, 'audio_sha256': input_sha, 'audio_path': str(args.audio.resolve()),
        'sample_rate': sr, 'duration': duration, 'device': 'cpu', 'threads': args.threads,
        'class_names': CLASS_NAMES, 'events': events, 'onset_evidence': evidence,
        'frame_onset_probabilities': onset_probs.tolist(),
        'configuration': {'onset_threshold': .5, 'class_threshold': .5, 'context_passes': 2,
                          'segment_frames': width, 'hop_frames': hop, 'segment_starts': starts,
                          'complete_frame_coverage': True, 'boundary_windows_zero_padded': boundary_count,
                          'normalization': 'none; librosa mono arithmetic mean', 'background_subtraction': False,
                          'reference_shift_seconds': 0, 'rhythm_filling': False, 'quantization': False},
        'limitations': ['Not the complete STRUM game-chart pipeline or its reported accuracy',
                       'Hi-hat opening/pedal, snare technique, exact tom tuning and velocity not recognized',
                       'Independent sigmoids may emit implausible simultaneous classes; preserved, not silently pruned',
                       'Compared with upstream: complete tail coverage and zero-padded boundary windows'],
        'packages': {name: importlib.metadata.version(name) for name in ('torch', 'torchaudio', 'numpy', 'librosa', 'scipy')},
        'implementation_sha256': implementation_sha,
        'elapsed_seconds': round(time.monotonic()-started, 3),
    }
    if input_sha != sha256(args.audio):
        raise RuntimeError('Input changed during inference')
    if implementation_sha != {name: sha256(ROOT / name) for name in implementation_files}:
        raise RuntimeError('Inference implementation changed during inference')
    if assets != verify_assets(args.source, args.weights):
        raise RuntimeError('Model assets changed during inference')
    content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as output:
        output.write(content)
    print(json.dumps({'events': len(events), 'onsets': len(onsets), 'elapsed_seconds': report['elapsed_seconds']}))


if __name__ == '__main__':
    main()
