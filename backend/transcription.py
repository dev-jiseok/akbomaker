"""Instrument-aware audio analysis. Output is a reviewable draft, not a score oracle."""
import math
import json
import os
from pathlib import Path

import numpy as np
import soundfile as sf

PROFILES = {
    "vocal": {"range": (36, 96), "minimum_ms": 90, "onset": .55, "frame": .35},
    "bass": {"range": (23, 84), "minimum_ms": 80, "onset": .5, "frame": .3},
    "guitar": {"range": (28, 103), "minimum_ms": 85, "onset": .6, "frame": .35},
    "piano": {"range": (21, 108), "minimum_ms": 80, "onset": .55, "frame": .35},
    "synthesizer": {"range": (21, 108), "minimum_ms": 120, "onset": .6, "frame": .4},
}


def hz(pitch):
    return 440 * 2 ** ((pitch - 69) / 12)


def clean_events(events, duration, minimum=.04):
    """Reject malformed/inaudible events; never clamp a late note onto the tail."""
    result = []
    for start, end, pitch, amplitude, *_ in events:
        if not all(math.isfinite(float(v)) for v in (start, end, pitch, amplitude)):
            continue
        start, end = max(0., float(start)), min(duration, float(end))
        if end - start < minimum or amplitude <= 0 or int(pitch) != pitch or not 0 <= pitch <= 127:
            continue
        result.append((start, end, int(pitch), min(1., float(amplitude))))
    return sorted(set(result))


def melody_segments(frequencies, voiced, probabilities, rms, hop_seconds, duration, *, onsets=()):
    """Stabilize vibrato without creating chords; preserve silence and re-attacks."""
    from scipy.ndimage import median_filter
    frequencies = np.asarray(frequencies)
    valid = np.asarray(voiced, dtype=bool) & np.isfinite(frequencies) & (frequencies > 0)
    valid &= np.asarray(probabilities) >= .12
    valid &= np.asarray(rms) >= max(1e-5, float(np.max(rms, initial=0)) * .008)
    pitches = np.full(len(frequencies), -1, dtype=int)
    pitches[valid] = np.rint(69 + 12 * np.log2(frequencies[valid] / 440)).astype(int)
    # Smooth only inside voiced runs. A rest must never become an invented note.
    edges = np.flatnonzero(np.diff(np.r_[False, valid, False]))
    for a, b in zip(edges[::2], edges[1::2]):
        if b - a >= 5:
            pitches[a:b] = median_filter(pitches[a:b], size=5, mode="nearest")
    attacks = {int(round(t / hop_seconds)) for t in onsets}
    events, start = [], 0
    for frame in range(1, len(pitches) + 1):
        changed = frame == len(pitches) or pitches[frame] != pitches[start]
        reattack = frame in attacks and (frame - start) * hop_seconds >= .12
        if not changed and not reattack:
            continue
        if pitches[start] >= 0:
            a, b = start * hop_seconds, min(duration, frame * hop_seconds)
            if b - a >= .07:
                strength = float(np.median(rms[start:frame])) / max(float(np.max(rms)), 1e-8)
                events.append((a, b, int(pitches[start]), min(1., max(.25, strength))))
        start = frame
    return events


def melody_events(path: Path, inst: str, *, details=None):
    """CPU pYIN fundamental tracking for a single lead voice or bass line."""
    import librosa
    from .audio_channels import mono_with_cancellation_guard
    from .melody_decoding import supported_reattacks, trim_release_tails
    channels, original_sr = librosa.load(path, sr=None, mono=False)
    samples, preprocessing = mono_with_cancellation_guard(channels.T)
    # Keep the old order (downmix, then resample) for ordinary recordings.
    sr = 22050
    samples = librosa.resample(samples, orig_sr=original_sr, target_sr=sr)
    if details is not None:
        details.update(engine_description(inst))
        details["preprocessing"] = preprocessing["method"]
        details["channel_preprocessing"] = preprocessing
        if preprocessing["used_channel_fallback"]:
            details["warning"] += " 좌우 채널을 합치면 소리가 상쇄돼 원본 한 채널로 분석했어요. 반대 채널의 독립적인 연주는 누락될 수 있으니 확인해주세요."
    duration, hop = len(samples) / sr, 220
    frame_length = 4096 if inst == "bass" else 2048
    low, high = (23, 79) if inst == "bass" else (36, 96)
    events = []
    # Bound Viterbi memory on a ten-minute upload; retain context on both sides.
    chunk, margin = 12 * sr, sr // 3
    for offset in range(0, len(samples), chunk):
        end = min(len(samples), offset + chunk)
        a, b = max(0, offset - margin), min(len(samples), end + margin)
        part = samples[a:b]
        if np.sqrt(np.mean(part.astype(float) ** 2)) < 1e-5:
            continue
        f0, voiced, probability = librosa.pyin(part, sr=sr, fmin=hz(low), fmax=hz(high),
                                             frame_length=frame_length, hop_length=hop,
                                             n_thresholds=32, resolution=.2)
        rms = librosa.feature.rms(y=part, frame_length=frame_length, hop_length=hop)[0][:len(f0)]
        raw_attacks = librosa.onset.onset_detect(y=part, sr=sr, hop_length=hop, units="time", backtrack=False)
        silence_floor = max(1e-5, float(np.max(rms, initial=0)) * .008)
        attacks = supported_reattacks(raw_attacks, part, sr, silence_floor=silence_floor)
        current = melody_segments(f0, voiced, probability, rms, hop / sr, len(part) / sr, onsets=attacks)
        current = trim_release_tails(current, np.setdiff1d(raw_attacks, attacks), part, sr,
                                     silence_floor=silence_floor)
        for start, stop, pitch, amp in current:
            start, stop = max(offset / sr, start + a / sr), min(end / sr, stop + a / sr)
            if stop <= start:
                continue
            # Join only the artificial chunk boundary, never a real re-attack.
            if events and start == offset / sr and events[-1][2] == pitch and abs(events[-1][1] - start) < 1e-5:
                old = events.pop()
                events.append((old[0], stop, pitch, max(old[3], amp)))
            else:
                events.append((start, stop, pitch, amp))
    return clean_events(events, duration)


def multiband_drums(path: Path):
    """Lightweight fallback: independent kick/snare/hat attacks, not a neural ADT."""
    import librosa
    from scipy.ndimage import median_filter
    from scipy.signal import find_peaks
    samples, sr = librosa.load(path, sr=22050, mono=True)
    if len(samples) < 1024:
        return []
    duration = len(samples) / sr
    hop, window = 220, 1024
    # A hit at t=0 needs a silent predecessor for the flux calculation.
    samples = np.pad(samples, (window, 0))
    spectrum = np.abs(librosa.stft(samples, n_fft=window, hop_length=hop))
    frequencies = librosa.fft_frequencies(sr=sr, n_fft=window)
    power = spectrum ** 2
    flux = np.maximum(np.diff(spectrum, axis=1, prepend=spectrum[:, :1]), 0)
    events = []
    for pitch, low, high, ratio in [(36, 30, 190, .12), (38, 190, 5000, .2), (42, 6000, 11025, .02)]:
        band = (frequencies >= low) & (frequencies < high)
        envelope = flux[band].sum(axis=0)
        energy = power[band].sum(axis=0)
        scale = float(np.percentile(envelope, 95))
        if scale < 1e-5:
            continue
        baseline = median_filter(envelope, size=101, mode="nearest")
        peaks, _ = find_peaks(envelope, height=np.maximum(baseline * 2.5, scale * .25),
                              prominence=scale * .15, distance=4)
        for peak in peaks:
            section = slice(max(0, peak - 1), min(power.shape[1], peak + 3))
            fraction = float(power[band, section].sum() / max(power[:, section].sum(), 1e-12))
            # Spectral redistribution when a sample ends can resemble an onset.
            # Require a rise in that band's energy, not just positive FFT bins.
            rising = energy[min(len(energy) - 1, peak + 1)] > energy[max(0, peak - 2)] * 1.1
            if fraction < ratio or not rising:
                continue
            start = max(0., (peak * hop - window / 4 - window) / sr)
            amp = min(1., max(.25, float(envelope[peak] / max(scale * 2, 1e-8))))
            events.append((start, min(duration, start + .1), pitch, amp))
    return clean_events(events, duration, minimum=.005)


def engine_description(inst, profile="instrument", *, engine="standard"):
    if engine == "adaptive":
        return {"engine": "basic-pitch-adaptive-v1", "profile": profile,
                "warning": "온셋·지속음 보강의 실험적 초안입니다. 음 시작을 확인한 뒤 음별 지속 기준을 적용해요. 약한 시작음·패드가 누락될 수 있고 옥타브 겹침은 실제 화음일 수도 있어요."}
    if inst == "drums":
        neural = bool(os.getenv("AKBO_DRUM_WORKER"))
        return {"engine": "adt-str" if neural else "multiband-onsets-v2", "profile": profile,
                "warning": "드럼 전용 모델의 추정입니다. 동시 타격·필인·심벌 종류를 확인해주세요." if neural else
                "킥·스네어·하이햇의 동시 타격을 추정한 실험적 초안입니다. 탐·크래시·라이드 자동 인식에는 드럼 전용 모델 연결이 필요해요."}
    if inst in {"vocal", "bass"} and profile == "instrument":
        return {"engine": "pyin-monophonic-v1", "profile": profile,
                "postprocessing": "waveform-supported-boundaries-v1",
                "warning": "한 번에 한 음의 주선율 초안입니다. 코러스·겹친 보컬은 주선율을 잘못 고를 수 있어요." if inst == "vocal" else
                "베이스 기본음을 추정한 단선율 초안입니다. 더블스톱·화음은 ‘화음 포함’으로 다시 채보해주세요."}
    return {"engine": "basic-pitch-instrument-v2", "profile": profile,
            "warning": "악기별 음역·짧은 음 검출 기준을 적용한 화음 채보 초안입니다. 배음·잔향·실제 운지와 음표를 확인해주세요."}


def transcribe_drums(path, duration, *, engine="auto", artifacts=None):
    """Explicit drum route with auditable raw output. No silent neural fallback."""
    if engine not in {"auto", "neural", "spectral", "hybrid", "consensus"}:
        raise ValueError("지원하지 않는 드럼 채보 엔진이에요.")
    neural = engine in {"neural", "hybrid", "consensus"} or (engine == "auto" and bool(os.getenv("AKBO_DRUM_WORKER")))
    if neural:
        from .drum_worker import transcribe_external, worker_status
        if not worker_status()["paths_ready"]:
            raise ValueError("드럼 전용 모델의 별도 실행 환경·체크포인트 설정을 확인해주세요.")
        details = {"engine": "adt-str", "profile": "instrument", "warning": "전용 AI의 채보 초안입니다. 스네어·필인·심벌 종류와 박자 위치를 확인해주세요."}
        options = {"normalization": "peak", "decoding": "greedy", "context_passes": 1} if engine == "hybrid" else {}
        if engine == "consensus":
            options = {"normalization": "peak", "decoding": "greedy", "context_passes": 3}
        events = transcribe_external(path, duration, artifacts=artifacts, details=details, **options)
        if engine == "consensus" or details.get("context_passes") == 3:
            details["engine"] = "adt-str-consensus-v1"
            details["warning"] = "3회 문맥 교차검증의 실험적 초안입니다. 두 번 이상 일치한 종류·시각만 남겨 오인식을 줄이지만 실제 약한 타격도 빠질 수 있어요. 같은 모델의 일치는 정확도 보증이 아닙니다."
        # Verify neural kick candidates against this exact input waveform. Keep
        # the model MIDI/events untouched; only the notation input is filtered.
        from .drum_evidence import suppress_unsupported_kicks
        samples, sample_rate = sf.read(path, dtype="float32")
        if abs(len(samples) / sample_rate - duration) > 1 / sample_rate:
            raise ValueError("드럼 채보 원본 음원 길이가 일치하지 않아요.")
        events, kick_evidence = suppress_unsupported_kicks(events, samples, sample_rate)
        details["kick_evidence"] = {k: v for k, v in kick_evidence.items() if k != "candidates"}
        details["postprocessing"] = kick_evidence["method"]
        if engine == "hybrid":
            from .drum_fusion import recover_hihats
            silent = bool((details.get("conditioning") or {}).get("silent_input"))
            events, recovery = recover_hihats(events, [] if silent else multiband_drums(path))
            details["engine"] = "adt-str-hybrid-v1"
            details["recovery"] = {k: v for k, v in recovery.items() if k != "added_events"}
            details["warning"] = "보강 AI의 실험적 초안입니다. 하이햇 누락을 보완하지만 오픈 하이햇·잔향·다른 악기를 닫힌 하이햇으로 잘못 추가할 수 있어요."
            if artifacts is not None:
                review_file = Path(artifacts) / "drums.transcription.json"
                raw = json.loads(review_file.read_text())
                # Keep original model MIDI/events untouched and make every
                # post-processing addition auditable before quantization.
                raw["recovery"] = recovery
                raw["score_events"] = events
                review_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2))
        if kick_evidence["rejected_count"]:
            details["warning"] += f' 저음 타격 근거가 부족한 킥 후보 {kick_evidence["rejected_count"]}개를 제외했어요. 약한 실제 킥도 제외될 수 있으니 원시 MIDI와 비교해주세요.'
        if artifacts is not None:
            review_file = Path(artifacts) / "drums.transcription.json"
            raw = json.loads(review_file.read_text())
            raw["kick_evidence"] = kick_evidence
            raw["score_events"] = events
            review_file.write_text(json.dumps(raw, ensure_ascii=False, allow_nan=False, indent=2))
        review = details["review"]
        if review["unsupported_count"]:
            details["warning"] += f' 악보에 표시하지 못한 타악기 {review["unsupported_count"]}개는 원본 타격 MIDI·검토 JSON에 보관했어요.'
        if review["simplified_counts"]:
            details["warning"] += " 세부 GM 변종은 편집기 드럼 종류로 통합했어요. 원본 MIDI에는 유지됩니다."
        if any(details.get("decode_review", {}).values()):
            details["warning"] += " 일부 구간의 모델 출력 형식이 불완전해요. 검토 JSON의 해당 시간대를 확인해주세요."
        if details.get("sampling_review", {}).get("budget_limited_chunks"):
            details["warning"] += " 일부 구간은 출력 한도에 도달해 타격이 누락됐을 수 있어요. 검토 JSON의 sampling.budget_limited 시간대를 확인해주세요."
        recovery = details.get("recovery_review", {})
        if recovery.get("replaced_chunks"):
            details["warning"] += f' 불완전한 분석 {recovery["replaced_chunks"]}구간을 제한 디코더로 재분석했어요. 형식 복구는 타격 정확도 보증이 아니며 원래 출력도 검토 JSON에 보존했어요.'
        if recovery.get("unresolved_chunks"):
            details["warning"] += " 재분석 후에도 불완전한 구간이 남아 있어요. 해당 구간의 누락·추가 타격을 확인해주세요."
        return events, details
    return multiband_drums(path), {"engine": "multiband-onsets-v2", "profile": "instrument",
                                   "warning": "경량 검출은 킥·스네어·하이햇 3종만 추정하며 스네어 누락·오인식 한계가 커요. 전용 AI와 비교해보세요."}


def transcribe_instrument(path, inst, model_factory, profile="instrument", *, engine="standard", details=None, artifacts=None):
    if inst not in {*PROFILES, "drums"} or profile not in {"instrument", "polyphonic"}:
        raise ValueError("지원하지 않는 채보 악기·방식이에요.")
    if engine not in {"standard", "adaptive"} or (engine == "adaptive" and inst not in {"guitar", "piano", "synthesizer"}):
        raise ValueError("보강 채보는 기타·피아노·신디사이저에서만 사용할 수 있어요.")
    samples, sr = sf.read(path, dtype="float32")
    if not np.isfinite(samples).all():
        raise ValueError("음원에 유효하지 않은 샘플이 포함되어 있어요.")
    duration = len(samples) / sr
    if engine == "adaptive":
        from .pitched_decoder import decode_pitched
        from .pitched_review import write_pitched_review
        description = engine_description(inst, profile, engine=engine)
        baseline, events, review = [], [], {"silent_input": True, "events": [], "activations_are_confidence": False}
        if len(samples) and np.sqrt(np.mean(samples.astype(float) ** 2)) >= 1e-5:
            from basic_pitch.inference import run_inference
            from basic_pitch.note_creation import model_output_to_notes
            from basic_pitch.constants import AUDIO_SAMPLE_RATE, FFT_HOP
            output = run_inference(str(path), model_factory())
            options = PROFILES[inst]
            # Decode the SAME inference with the old rule for transparent review.
            # Upstream frequency limiting mutates arrays, so isolate those copies.
            _, old = model_output_to_notes({k: v.copy() for k, v in output.items()},
                onset_thresh=options["onset"], frame_thresh=options["frame"],
                min_note_len=round(options["minimum_ms"] / 1000 * AUDIO_SAMPLE_RATE / FFT_HOP),
                min_freq=hz(options["range"][0]), max_freq=hz(options["range"][1]), include_pitch_bends=False)
            baseline = clean_events(old, duration, minimum=options["minimum_ms"] / 1000)
            events, review = decode_pitched(output, onset_threshold=options["onset"], frame_threshold=options["frame"],
                                          minimum_ms=options["minimum_ms"], min_pitch=options["range"][0],
                                          max_pitch=options["range"][1], duration=duration)
        description["pitched_postprocessing"] = write_pitched_review(artifacts, instrument=inst, source=path, duration=duration,
                                                                    baseline=baseline, events=events, decoding=review,
                                                                    description=description)
        description["pitched_review"] = artifacts is not None
        if details is not None:
            details.update(description)
        return events
    if not len(samples) or np.sqrt(np.mean(samples.astype(float) ** 2)) < 1e-5:
        return []
    if inst == "drums":
        events, description = transcribe_drums(path, duration, artifacts=artifacts)
        if details is not None:
            details.update(description)
        return events
    if inst in {"vocal", "bass"} and profile == "instrument":
        return melody_events(path, inst) if details is None else melody_events(path, inst, details=details)
    from basic_pitch.inference import predict
    options = PROFILES[inst]
    _, _, events = predict(str(path), model_or_model_path=model_factory(),
                           minimum_frequency=hz(options["range"][0]), maximum_frequency=hz(options["range"][1]),
                           onset_threshold=options["onset"], frame_threshold=options["frame"],
                           minimum_note_length=options["minimum_ms"])
    return clean_events(events, duration, minimum=options["minimum_ms"] / 1000)
