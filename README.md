# 악보 메이커

음악 파일이나 YouTube 영상 링크를 가져와 SAM Audio로 여섯 악기를 차례로 분리하고, 악기별 음원을 악보로 변환하는 웹 작업실입니다. React + TypeScript 프런트엔드와 Python 3.11 FastAPI API로 구성했습니다.

## 지금 사용할 수 있는 기능

- 파일 선택·드래그 앤 드롭·업로드 전 미리듣기. MP3, WAV, FLAC, M4A, AAC, OGG, OPUS, MP4, MOV, WEBM, MKV, AIFF 지원. FFmpeg로 동영상의 오디오 트랙도 추출합니다.
- YouTube 개별 영상 URL 입력, yt-dlp 다운로드. 기본 200MB·10분 제한. 로그인·쿠키·라이브·재생목록·보호된 영상은 지원하지 않습니다.
- SAM Audio 순차 분리, 작업 진행률, 중단, 브라우저 새로고침 후 작업 복원. 긴 음악은 20초 구간과 2초 겹침으로 처리합니다.
- 악기별 WAV, 마지막 잔여 음원, 파형, 솔로·음소거·볼륨·재생 위치, 원본 비교.
- Basic Pitch 채보 → 16분음표 단위 정리 → MusicXML·MIDI. 드럼은 별도의 온셋·주파수 기반 실험적 리듬 채보입니다.
- 실제 MusicXML 악보 렌더링, 음표 크기·보표 간격·마디 번호, 브라우저 인쇄/PDF 저장, 전체 ZIP 다운로드.
- 이 브라우저의 최근 20개 프로젝트, 모바일 화면, 키보드 조작, 오류 및 엔진 연결 상태 안내.
- 직접 합성한 원본 샘플 음원과 실제 WAV·MIDI·MusicXML 파일로 작업실 체험. **샘플은 SAM Audio 분리 결과가 아닙니다.**

## 로컬 실행

Node 22 이상, Python 3.11, FFmpeg가 필요합니다. macOS는 `brew install ffmpeg`, Ubuntu는 `sudo apt-get install ffmpeg libsndfile1`로 설치할 수 있습니다.

```sh
npm ci
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
```

두 터미널에서 각각 실행합니다.

```sh
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

```sh
npm run dev
```

웹앱: http://127.0.0.1:5173 · API 문서: http://127.0.0.1:8000/docs

ML 패키지가 없는 환경에서도 샘플 작업실은 동작합니다. 실제 분리는 사용할 수 없는 상태로 표시되며, 샘플이나 원본 음원을 분리된 음원으로 위장하지 않습니다.

## 실제 SAM Audio 엔진 연결

NVIDIA CUDA GPU가 있는 Python 3.11 서버에 이 프로젝트를 실행하세요. PyTorch·Torchaudio·TorchCodec은 서로 호환되는 CUDA 버전으로 설치해야 합니다. [SAM Audio 공식 설치 안내](https://github.com/facebookresearch/sam-audio)와 [TorchCodec 호환표](https://github.com/pytorch/torchcodec)를 따라 서버 환경을 먼저 준비하세요.

```sh
.venv/bin/pip install -r backend/requirements-ml.txt
```

[사용할 Hugging Face 모델](https://huggingface.co/facebook/sam-audio-base)에 접근을 요청해 승인받고, 해당 서버에서 `hf auth login`으로 인증하거나 `.env`에 `HF_TOKEN`을 설정하세요. `.env.example`의 나머지 설정도 사용할 수 있습니다. 토큰은 백엔드에서만 읽고 프런트엔드나 Git에 포함하지 않습니다.

```dotenv
SAM_MODEL=facebook/sam-audio-base
SAM_DEVICE=cuda
SAM_CHUNK_SECONDS=20
MAX_AUDIO_SECONDS=600
MAX_UPLOAD_MB=200
```

모델 경로를 `SAM_MODEL`로 지정하면 로컬 체크포인트도 사용할 수 있습니다. 첫 실제 작업은 모델 다운로드와 초기화 때문에 오래 걸릴 수 있습니다. GPU 메모리가 부족하면 구간 길이를 줄이거나 작은 SAM 모델을 선택하세요. 모델·패키지·GPU·인증 상태는 `/api/health`와 화면의 연결 상태에서 확인합니다. 여기서 '사용 가능'은 실행 전제 조건의 확인이며, 모델 다운로드·접근 승인·실제 추론 성공을 보증하지는 않습니다.

순차 분리의 핵심은 다음과 같습니다. `extract` 결과는 한 번만 계산합니다. 48kHz 모노 부동소수점 WAV로 중간 결과를 저장하여 단계별 클리핑을 피합니다.

```python
residual = audio.copy()
for inst in ["vocal", "bass", "drums", "synthesizer", "guitar", "piano"]:
    target = extract(residual, inst)
    outputs[inst] = target
    residual = residual - target
# sum(outputs.values()) + residual ≈ audio
```

이 구조는 앞 단계의 분리 오류가 다음 단계에 영향을 줄 수 있습니다. 모든 곡에 여섯 악기가 실제로 존재하는 것은 아니며, 비슷한 음색의 악기를 모델이 혼동할 수 있습니다. 해당 악기 소리가 매우 작으면 화면에 안내합니다.

## 채보와 악보 스타일

SAM Audio는 소리를 분리하고, [Spotify Basic Pitch](https://github.com/spotify/basic-pitch)는 분리된 음원의 음정·시작·종료를 추정합니다. 피아노 등 화음이 있는 악기도 지원하며, 악기별 단일 음원에 적용합니다. 드럼은 음정 채보 대신 킥·스네어·하이햇의 대략적인 리듬을 추정합니다.

현재 채보는 사용자가 지정한 BPM, 4/4박자, 16분음표 격자를 사용합니다. **자동 완성 악보가 아니라 확인·수정이 필요한 초안**입니다. 박자·템포 자동 추정, 조성 추정, 보컬 가사, 기타 TAB, 드럼 세부 분류, 피아노 양손 분리, 음표 직접 편집은 아직 구현하지 않았습니다. MusicXML은 MuseScore 같은 편집기에서 수정할 수 있습니다. 취향에 맞는 악보 스타일은 `ScoreViewer`와 MusicXML 내보내기 계층을 확장하여 추가할 수 있습니다.

## 검증

```sh
npm run build
npm test
npm run test:api
```

테스트는 입력 URL·크기 제한, 순차 추출 호출 수, 소리 합산 보존, 구간 끝 커버리지, 중단, MusicXML 마디 길이·타이·타악기, MIDI, 실제 샘플 파일·오디오 범위 요청·ZIP, 파일 업로드와 API 채보 연결을 확인합니다. GPU 없는 CI의 분리·채보 통합 테스트에서는 모델만 대체합니다. **SAM Audio의 실제 추론·품질은 승인된 모델과 CUDA 서버에서 별도로 검증해야 합니다.**

## 서버 배치

```sh
npm run build
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

빌드한 `dist/`가 있으면 API가 프런트엔드도 같은 출처로 제공합니다. 기본 개발 서버는 로컬에만 바인딩합니다. 이 버전은 개인 작업실용이며 계정 인증·사용자별 권한 분리를 포함하지 않습니다. 공개 서비스로 운영하려면 인증, 사용자별 저장소, 요청 제한, 보관 정책, HTTPS 프록시, GPU 작업 큐를 추가해야 합니다. `uvicorn --workers 1`로 실행해야 단일 모델·작업 큐·파일 저장소가 일관되게 동작합니다.

작업과 파일은 `.data/<job-id>/`에 보관되고 프로젝트 목록은 브라우저 localStorage에 저장됩니다. 원본과 결과 파일은 자동으로 삭제하지 않습니다. 서버 재시작 때 미완료 작업은 중단 상태로 바뀌며 완료된 파일은 보존됩니다. ZIP은 디스크에 생성한 뒤 스트리밍합니다. 대용량 오디오가 다수 쌓이는 공개 서비스에는 디스크 용량 관리와 보관 정책이 추가로 필요합니다.
