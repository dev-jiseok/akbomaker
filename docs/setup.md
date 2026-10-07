# 설치·서버 운영

[프로젝트 홈](../README.md) · [문서 모음](README.md)

모든 명령은 저장소 루트 기준입니다. 환경에 맞는 실행 방식을 선택하세요.

| 목적 | 환경 | 실제 악기 분리 |
| --- | --- | --- |
| 전체 엔진 서버 | Linux NVIDIA·Python 3.11·Node 22·승인된 SAM 모델 | 가능 — 초기화와 GPU 준비 필요 |
| 샘플/개발·원본 악보 작업 | Python 3.11·Node 22·FFmpeg | 개발 모드 자체는 실제 분리를 제공하지 않음 |

목차: [GPU 서버](#gpu-server) · [개발 실행](#development) · [CPU 분석·가사](#cpu-only) · [SAM·GPU 설정](#sam-gpu) · [PDF 도구](#pdf-tools) · [배포·보관](#operations)

<a id="gpu-server"></a>
## GPU 서버 실행

### 사전 준비

- nvm, uv, Git, FFmpeg/ffprobe, NVIDIA 드라이버를 운영자가 설치합니다.
- Ubuntu 미디어 패키지: `sudo apt-get install git ffmpeg libsndfile1`.
- 서버 잠금 파일은 **Python 3.11 / Linux NVIDIA / PyTorch 2.8 / CUDA 12.8 / TorchCodec 0.7** 조합입니다. CUDA 12.8 호환 드라이버가 필요합니다.
- [SAM Audio base](https://huggingface.co/facebook/sam-audio-base) 접근 승인을 받은 계정으로 `hf auth login`하거나, 서버의 `.env`에 `HF_TOKEN`을 설정합니다. 승인 자체는 자동화하지 않습니다.
- 토큰을 Git이나 `VITE_*` 프런트엔드 환경변수에 넣지 마세요. 설정 목록은 [`.env.example`](../.env.example)을 참고하세요.

### 실행

```sh
./start.sh --host 127.0.0.1 --port 8000
```

접속: <http://127.0.0.1:8000>. 인자를 생략해도 기본 바인딩은 `127.0.0.1:8000`입니다. 다른 기기에서 접속하려면 운영자가 서버 인터페이스·방화벽·접근 제어를 정한 뒤 `--host`를 지정하세요.

스크립트가 수행하는 작업:

1. nvm으로 Node 22를 선택/설치합니다.
2. uv로 Python 3.11 `.venv`와 고정 서버 의존성을 준비합니다.
3. SAM Audio·CUDA PyTorch·Basic Pitch·Whisper 의존성을 설치합니다.
4. 프런트엔드를 빌드하고 웹앱/API를 같은 출처로 실행합니다.

패키지와 모델 캐시는 재사용하지만 프런트엔드는 매번 빌드합니다. `./start.sh --setup-only`는 의존성 설치와 빌드까지만 수행하며 모델 준비 성공을 뜻하지 않습니다. 기존 `.venv`가 Python 3.11이 아니면 중단되므로 기존 환경을 임의로 지우지 말고 운영자가 확인하세요.

### 시작 검사와 실패 확인

일반 서버는 FFmpeg, 저장소 쓰기 권한, GPU 연산, TorchCodec, SAM 모델, Basic Pitch ONNX 모델, Whisper 모델을 확인·초기화한 뒤 요청을 받습니다.

- 첫 실행에는 모델 다운로드가 발생할 수 있습니다.
- 모델 접근·다운로드·초기화 실패 시 단계와 원인을 출력하고 시작을 중단합니다.
- 추가 모델 승인이 필요하면 로그에 표시된 모델도 확인하세요.
- 업로드 이후 오류는 화면의 처리 단계·원인·작업 번호와 서버 로그를 함께 확인합니다.
- `/api/health`와 화면의 연결 상태는 엔진 준비 상태를 보여주며 음악 분리 품질을 보증하지 않습니다.

### 의존성 유지보수

수동 서버 환경 설치:

```sh
GIT_LFS_SKIP_SMUDGE=1 uv pip install --python .venv/bin/python --torch-backend cu128 -r backend/requirements-server.lock
```

잠금 파일 갱신은 호환성 검증 후 별도로 수행합니다. [TorchCodec 호환표](https://github.com/meta-pytorch/torchcodec), SAM/Basic Pitch의 protobuf 제약, resampy의 `pkg_resources`에 필요한 setuptools 제약을 함께 확인하세요.

```sh
GIT_LFS_SKIP_SMUDGE=1 uv pip compile backend/requirements-server.in --python-version 3.11 --torch-backend cu128 -o backend/requirements-server.lock
```

현재 서버 잠금 파일에는 PDF 좌표 분석용 `pdfplumber`가 포함되지 않으므로 PDF 기능을 사용할 서버는 아래 [추가 설치](#pdf-tools)도 수행해야 합니다. 이 문서 정리는 잠금 파일이나 런타임을 변경하지 않습니다.

<a id="development"></a>
## 샘플·개발 실행

Node 22 이상, Python 3.11, uv, FFmpeg가 필요합니다. macOS는 `brew install ffmpeg`, Ubuntu는 `sudo apt-get install ffmpeg libsndfile1`로 미디어 도구를 준비할 수 있습니다.

```sh
source "$HOME/.nvm/nvm.sh"
nvm use
npm ci
uv venv --python 3.11
uv pip install -r backend/requirements-dev.txt
```

터미널 1:

```sh
AKBO_ALLOW_DEGRADED=1 .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

터미널 2:

```sh
npm run dev
```

웹: <http://127.0.0.1:5173> · API 문서: <http://127.0.0.1:8000/docs>

`AKBO_ALLOW_DEGRADED=1`은 **개발용 엔진 준비 검사 생략**입니다. ML 패키지 없이 샘플 화면을 볼 수 있지만 실제 분리는 사용할 수 없는 상태로 표시합니다. 샘플·원본을 분리 결과로 위장하지 않으며 일반 서버의 실패를 숨기는 용도로 사용하면 안 됩니다.

<a id="cpu-only"></a>
## GPU 없는 원본 분석·가사

홈의 `분리 없이 원본만 분석`은 SAM을 호출하거나 악기별 WAV를 만들지 않습니다. 원본 BPM/박 분석, 가사 초안, 악기별 빈 악보와 수동 편집을 사용할 수 있습니다. 파일/YouTube 제한과 이용 권한 안내는 일반 업로드와 같습니다.

가사 인식 선택 의존성:

```sh
uv pip install --python .venv/bin/python -r backend/requirements-asr.txt
```

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)의 다국어 `base` 모델을 CPU INT8로 사용합니다.
- `LYRICS_MODEL`로 모델을 지정하며 첫 다운로드는 `.data/models/whisper/`에 보관합니다. 이후 추론은 로컬이며 업로드 음원을 외부 ASR 서비스에 보내지 않습니다.
- `lyrics.available`은 패키지 존재 여부이지 모델 다운로드/실제 노래 인식 성공 보증이 아닙니다.
- 가사 타임라인에서 원본/분리 보컬·언어를 선택하고 시간/텍스트를 수정한 뒤 `초안 저장 → 전체 파트에 적용`합니다. 음표·운지·메모·최초 생성본은 유지합니다.
- 적용한 공통 가이드는 새 악보/재채보에도 배치합니다. 현재 편집기를 저장하고 닫은 후 적용해야 하며 revision 충돌을 검사합니다.

노래·코러스·강한 반주·무보컬 구간에는 가사 누락이나 없는 단어가 생길 수 있습니다. 단어 시간은 ASR 추정이지 음악적인 음절/멜리스마 정렬이 아닙니다. 합성 샘플에는 실제 가사가 없어 자동 인식에서 제외합니다. 초안 임시 보관·미저장 이탈 경고도 제공합니다.

템포 분석은 원본의 처음 최대 180초를 사용합니다. 첫 검출 박이 마디 첫 박이라는 보장은 없고 박 규칙성도 정확도 점수가 아닙니다. 반속/배속·BPM·악보 첫 박의 원본 위치를 직접 확인하세요. 설정은 다음 채보/빈 악보에만 적용합니다. 오프셋 이전 음표·가사는 제외되므로 인트로도 필요하면 0초를 유지하세요.

<a id="sam-gpu"></a>
## SAM 모델·GPU 설정

```dotenv
SAM_MODEL=facebook/sam-audio-base
SAM_DEVICE=cuda
SAM_MIN_FREE_GB=12
SAM_MAX_GPU_UTILIZATION=10
SAM_CHUNK_SECONDS=20
MAX_AUDIO_SECONDS=600
MAX_UPLOAD_MB=200
```

`SAM_MODEL`에는 로컬 체크포인트도 지정할 수 있습니다. GPU 메모리가 부족하면 모델/구간 길이를 조정하되 시작 검사만으로 긴 곡의 메모리나 분리 품질을 보증하지는 못합니다. 설치 근거는 [SAM Audio 공식 안내](https://github.com/facebookresearch/sam-audio)를 참고하세요.

| 설정/상태 | 동작 |
| --- | --- |
| `SAM_DEVICE=cuda` 또는 `auto` | 작업 직전 `nvidia-smi`로 사용률 ≤10%, 여유 메모리 ≥12GiB인 장치 중 가장 여유로운 GPU 선택. 기준값 변경 가능 |
| `SAM_DEVICE=cuda:1` | 해당 CUDA 장치에 고정 |
| `CUDA_VISIBLE_DEVICES` | 제한/순서를 UUID로 매칭 |
| 선택 가능한 GPU 없음 | 시작 검사 또는 작업 실패·재시도 안내. 자동 대기하지 않음 |
| 한 작업의 여섯 악기 | 동일 GPU 사용. 다음 작업에서 다시 선택 |
| 작업 종료·실패·취소 | SAM을 CPU RAM으로 이동하고 CUDA 할당 캐시 반환 |

유휴 상태에도 CUDA 컨텍스트·라이브러리 메모리는 남을 수 있습니다. 다음 요청은 CPU에 보관한 모델을 GPU로 옮기므로 다운로드는 반복하지 않지만 전송 시간이 추가됩니다. 조회 결과가 다른 사용자의 GPU 예약을 막지는 않으므로 선택 직후 자원이 부족해질 수도 있습니다.

`/api/health`의 `engine.selected_device`는 선택 장치(유휴 시 `null`), `engine.gpu_resident`는 SAM의 GPU 상주 여부, `ready`는 시작 검사 통과 여부입니다.

앱은 텍스트 프롬프트·후보 1개·구간 예측 비활성 설정으로 ImageBind/CLAP/Judge 재평가 모델과 구간 예측 모델을 로드하지 않습니다. 시작 시 4초 무음으로 실제 분리를 실행해 처리기·추론·파형 반환을 확인합니다. 여러 GPU에 모델을 분산하거나 실제 음악 품질을 평가하는 검사는 아닙니다.

분리 알고리즘과 별도 ADT 환경은 [악기별 엔진](audio-engines.md)에 설명했습니다. SAM의 GPU 자동 선택 정책은 별도 드럼 워커에 적용되지 않습니다.

<a id="pdf-tools"></a>
## PDF·이미지 기능 추가 설치

MusicXML 원본 보존에는 Audiveris가 필요하지 않습니다. PDF/이미지의 오선 인식과 벡터 TAB 검수는 요구 도구가 다릅니다.

| 기능 | 준비할 것 |
| --- | --- |
| MusicXML/XML/MXL 스타일·편집 | 앱 기본 의존성 |
| PDF 벡터 TAB 숫자·리듬 후보 검수 | Poppler(`pdfinfo`, `pdftoppm`), Pillow, pdfplumber |
| PDF/이미지 오선 인식 | 위 도구 + Audiveris + 선택한 언어의 legacy 호환 tessdata |

운영자가 직접 준비합니다. 앱이 도구를 자동 설치하거나 이용 약관을 대신 수락하지 않습니다.

1. **오선 인식을 사용할 경우에만** [Audiveris 5.11.0](https://github.com/Audiveris/audiveris/releases/tag/5.11.0)의 플랫폼별 배포를 설치합니다. AGPL-3.0 조건과 공개 서비스 배포 의무를 검토하세요. macOS 번들은 JRE를 포함하며 OCR 언어는 별도입니다. TAB 직접 검수만 사용하면 1·3·4번은 생략합니다.
2. Poppler의 `pdfinfo`, `pdftoppm`을 PATH에 설치하고 앱 환경에 기본 의존성을 갱신합니다.

   ```sh
   uv pip install --python .venv/bin/python -r backend/requirements.txt
   ```

3. [`.env.example`](../.env.example)의 `AKBO_OMR_EXECUTABLE`에 신뢰할 수 있는 실행 파일의 절대 경로를 지정합니다. 인수가 포함된 셸 문자열은 받지 않으며 필요한 경우 신뢰할 수 있는 로컬 래퍼를 사용합니다.
4. `AKBO_OMR_TIMEOUT_SECONDS` 기본값은 900초(범위 30~3600초), `AKBO_OMR_LANGUAGES` 기본값은 `eng`입니다. `eng+kor` 등의 언어를 쓰려면 해당 legacy OCR 파일을 설치하고 필요 시 `TESSDATA_PREFIX`를 지정합니다.
5. `/api/score-omr/status`에서 도구 구성을 확인한 후 이용 권한이 있는 악보로 실측합니다. 구성 확인은 실제 인식 성공/정확도 보증이 아닙니다.

Audiveris의 TAB 무시, 드럼 음자리표/매핑, 한글 가사 및 원본 대조 제한은 [악보 가져오기](score-import.md)를 꼭 확인하세요.

<a id="operations"></a>
## 배포·데이터 보관

**GitHub 머지와 서버 배포는 별개입니다.** 서버 권한이 있는 담당자가 배포 절차를 수행해야 합니다.

배포 시 확인할 항목:

- 기존 `.env`, 모델 캐시, `AKBO_DATA_DIR`의 업로드·프로젝트 데이터를 보존합니다. 기본 경로는 `.data/`입니다.
- 코드·의존성·프런트 빌드를 반영하고 운영 중인 서비스 방식에 맞게 재시작합니다. 기존 인스턴스와 중복 실행하지 마세요.
- **API 프로세스는 `uvicorn --workers 1`**로 실행합니다. 현재 단일 모델·메모리 작업 큐·파일 저장소를 전제로 합니다.
- `/api/health`, 파일 업로드, 기존 프로젝트 재열기, 저장/출력, 필요한 선택형 엔진 상태를 확인합니다.
- 서버 재시작 시 미완료 작업은 중단 상태로 바뀌며 완료 파일은 보존됩니다.

빌드된 `dist/`가 있으면 API가 프런트엔드도 제공합니다. 빌드 시 기존 `dist/assets/`를 보존해 열린 탭의 이전 악보 렌더러 로드를 지원하며 HTML은 `Cache-Control: no-cache`로 재검증합니다. 과거 자산 정리는 기존 탭의 작업이 끝난 유지보수 시점에 하세요. 이전에 삭제된 자산을 요청하는 탭은 편집 내용을 보관한 뒤 새로고침해야 합니다.

| 데이터 | 저장 위치/정책 |
| --- | --- |
| 일반 작업·원본·결과 | `AKBO_DATA_DIR/<job-id>/` |
| PDF 인식 작업 | `AKBO_DATA_DIR/score-omr/<id>/` |
| 최근 프로젝트 목록 | 브라우저 localStorage |
| 원본·결과의 자동 삭제 | 없음 — 운영자가 디스크 용량과 보관 정책 관리 |
| ZIP | 디스크에 생성한 뒤 스트리밍 |

이 버전은 개인 작업실용이며 계정 인증·사용자별 권한 분리를 포함하지 않습니다. 공개 서비스에는 인증, 사용자별 저장소, 요청/용량 제한, HTTPS 프록시, GPU 작업 큐, 문서 처리 격리·악성 문서 방어, 보관 정책·모니터링이 추가로 필요합니다. 실행 시간/크기 제한만으로 완전한 샌드박스가 되는 것은 아닙니다.
