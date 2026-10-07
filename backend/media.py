import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import MAX_AUDIO_SECONDS, MAX_UPLOAD_BYTES, SAMPLE_RATE

EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".mp4", ".mov", ".webm", ".mkv", ".aiff", ".aif"}


def youtube_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("https로 시작하는 유튜브 영상 링크를 입력해주세요.")
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        video = parsed.path.strip("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            video = parse_qs(parsed.query).get("v", [""])[0]
        else:
            match = re.fullmatch(r"/(?:shorts|embed|live)/([\w-]{11})/?", parsed.path)
            video = match.group(1) if match else ""
    else:
        raise ValueError("유튜브 또는 youtu.be 영상 링크만 지원해요.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
        raise ValueError("재생목록 대신 개별 유튜브 영상 링크를 입력해주세요.")
    return f"https://www.youtube.com/watch?v={video}"


def download_youtube(url: str, folder: Path) -> tuple[Path, str]:
    canonical = youtube_url(url)
    common = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--no-cache-dir", "--socket-timeout", "15"]
    try:
        info = subprocess.run(common + ["--dump-single-json", "--skip-download", canonical], capture_output=True, text=True, timeout=60, check=True)
        metadata = json.loads(info.stdout)
        duration = metadata.get("duration")
        if metadata.get("is_live") or not duration or duration > MAX_AUDIO_SECONDS:
            raise ValueError(f"라이브 영상은 지원하지 않으며, {MAX_AUDIO_SECONDS // 60}분 이내 영상만 사용할 수 있어요.")
        if (metadata.get("filesize") or metadata.get("filesize_approx") or 0) > MAX_UPLOAD_BYTES:
            raise ValueError("영상의 크기가 업로드 제한을 초과해요.")
        result = subprocess.run(common + ["-f", "bestaudio/best", "--max-filesize", str(MAX_UPLOAD_BYTES), "--print", "after_move:filepath", "-o", str(folder / "source.%(ext)s"), canonical], capture_output=True, text=True, timeout=180, check=True)
        path = Path(result.stdout.strip().splitlines()[-1]).resolve()
        if path.parent != folder.resolve() or not path.is_file() or path.stat().st_size > MAX_UPLOAD_BYTES:
            raise ValueError("영상 다운로드 결과를 확인할 수 없어요.")
        return path, metadata.get("title", "YouTube 음악")
    except (subprocess.SubprocessError, ValueError, IndexError) as error:
        if isinstance(error, ValueError):
            raise
        raise ValueError("유튜브 음원을 가져오지 못했어요. 비공개·지역 제한 영상인지 확인하거나 파일로 업로드해주세요.") from error


def _stereo_cancellation_evidence(path: Path, audit: dict) -> None:
    """Measure decoded stereo in fixed-size blocks without loading a song.

    The temporary decoder writes float32, so squaring in float64 is safe even
    for extreme finite input samples. No audio is changed during diagnosis.
    """
    import numpy as np
    import soundfile as sf
    count, channel_power, mean_power = 0, np.zeros(2, dtype=np.float64), 0.
    with sf.SoundFile(path) as decoded:
        if decoded.channels != 2 or decoded.samplerate != SAMPLE_RATE:
            raise ValueError("스테레오 음원 확인 결과가 예상과 다릅니다.")
        if decoded.frames > (MAX_AUDIO_SECONDS + 1) * SAMPLE_RATE:
            raise ValueError("스테레오 음원의 분석 길이가 제한을 초과했어요.")
        for block in decoded.blocks(blocksize=65536, dtype="float64", always_2d=True):
            if not np.isfinite(block).all():
                raise ValueError("음원에 유효하지 않은 샘플이 포함되어 있어요.")
            channel_power += np.sum(block * block, axis=0)
            mixed = np.mean(block, axis=1)
            mean_power += float(np.sum(mixed * mixed))
            count += len(block)
    audit["diagnostic_status"] = "checked"
    if not count:
        audit.update(strongest_channel_rms=0., downmix_rms=0.)
        return
    selected = int(np.argmax(channel_power))
    strongest = float(channel_power[selected])
    rms = math.sqrt(strongest / count)
    audit.update(strongest_channel_rms=rms, downmix_rms=math.sqrt(mean_power / count),
                 cancellation_power_ratio=mean_power / strongest if strongest else None)
    if rms >= 1e-5 and strongest and mean_power / strongest <= .01:
        audit.update(used_channel_fallback=True, selected_channel=selected)


def normalize(source: Path, target: Path, *, details=None) -> float:
    """Decode to the existing mono format, guarding near-total stereo loss.

    Normal inputs use the exact previous FFmpeg command. Only audible stereo
    whose average has <=1% of the strongest channel's power selects an original
    channel, before resampling. Multi-channel surround keeps legacy behavior;
    choosing one surround channel would not preserve its intended mix.
    """
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_format", "-show_streams", "-of", "json", str(source)], capture_output=True, text=True, check=True, timeout=30)
        metadata = json.loads(result.stdout)
        audio_stream = next((s for s in metadata.get("streams", []) if s.get("codec_type") == "audio"), None)
        if audio_stream is None:
            raise ValueError("이 파일에는 오디오 트랙이 없어요.")
        duration = float(metadata.get("format", {}).get("duration") or 0)
        if duration > MAX_AUDIO_SECONDS:
            raise ValueError(f"최대 {MAX_AUDIO_SECONDS // 60}분 길이의 음악을 업로드해주세요.")
        channels = audio_stream.get("channels")
        channels = channels if type(channels) is int and channels > 0 else None
        audit = {"method": "preserve-upload-stereo-cancellation-v1", "input_channels": channels,
                 "used_channel_fallback": False, "selected_channel": None,
                 "diagnostic_status": "mono" if channels == 1 else
                     "unsupported-channel-count" if channels else "unknown-channel-count",
                 "strongest_channel_rms": None, "downmix_rms": None,
                 "cancellation_power_ratio": None, "maximum_cancellation_power_ratio": .01}
        prefix = ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", "0:a:0", "-vn"]
        suffix = ["-ar", str(SAMPLE_RATE), "-t", str(MAX_AUDIO_SECONDS + 1), "-c:a", "pcm_f32le", "-y"]
        if channels == 2:
            # Bound the diagnostic by the same duration/sample-rate as the
            # final input. A temporary WAV avoids a ten-minute stereo array.
            with tempfile.TemporaryDirectory(prefix=".channel-check-", dir=target.parent) as temporary:
                decoded = Path(temporary) / "stereo.wav"
                subprocess.run(prefix + ["-ac", "2"] + suffix + [str(decoded)], capture_output=True, check=True, timeout=90)
                _stereo_cancellation_evidence(decoded, audit)
        selected_filter = (["-af", f"pan=mono|c0=c{audit['selected_channel']}"]
                           if audit["used_channel_fallback"] else [])
        subprocess.run(prefix + selected_filter + ["-ac", "1"] + suffix + [str(target)], capture_output=True, check=True, timeout=90)
        import soundfile as sf
        actual = sf.info(target).duration
        if not 0.2 <= actual <= MAX_AUDIO_SECONDS:
            raise ValueError(f"0.2초 이상, {MAX_AUDIO_SECONDS // 60}분 이내 음악을 사용해주세요.")
        if details is not None:
            details.update(audit)
        return actual
    except FileNotFoundError as error:
        raise ValueError("서버에 FFmpeg가 설치되어 있지 않아요.") from error
    except (subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise ValueError("음악 파일을 읽을 수 없어요. 파일이 손상되었거나 지원하지 않는 형식이에요.") from error
