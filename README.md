# shortly

특정 유튜브 채널의 **쇼츠 스타일을 학습**해서, 이후 주제/대본을 입력하면 **같은 느낌의 제작 가이드**(대본 + TTS 설정 + 컷/자막/전환 지시 + 효과음 추천)와 **CapCut 초안**을 만들어 주는 로컬 툴입니다.

- 채널 쇼츠를 내려받아 대사(자막/ASR/OCR), 영상 편집(컷·줌·전환·모션), 효과음 사용 패턴을 분석
- 참조 효과음 라이브러리와 오디오 지문 매칭으로 "어떤 효과음이 어느 상황에 쓰였는지" 파악
- 분석을 종합한 **채널 스타일 프로파일**로 새 쇼츠 제작 가이드를 생성
- 브라우저 로컬 웹 UI로 입출력 관리

## 설치

```bash
pip install -r requirements.txt
```

- `ffmpeg`, `ffprobe` 가 시스템 PATH 에 있어야 합니다(오디오 디코딩/길이 측정).
- 무거운 분석 의존성(opencv, faster-whisper, easyocr, demucs, LLM SDK)은 **선택**입니다. 없으면 해당 단계만 자동으로 건너뜁니다.

## 설정

`.env.example` 을 `.env` 로 복사한 뒤 값을 채웁니다.

- `SHORTLY_LLM_PROVIDER`: `gemini` | `openai` | `anthropic` | `local` | `none`
- 각 제공자 API 키(`GEMINI_API_KEY` 등). 없으면 분석/생성은 휴리스틱으로 대체됩니다.
- `SHORTLY_SFX_DIR`: 개인/legacy 환경의 참조 효과음 폴더

### 제품 프로파일과 저장 위치

일반 개발 실행은 기존 동작을 보존하는 `legacy` 프로파일입니다. 팀·개인 전용 launcher는
`SHORTLY_PRODUCT`를 각각 `team`, `personal`로 고정하며 설정 화면에서 제품 종류를
바꾸지 않습니다.

| 프로파일 | 내부 상태·설정 | 사용자 결과물·로그 |
|---|---|---|
| `legacy` | 저장소와 기존 환경변수 경로 | 기존 경로 유지 |
| `team` | `%LOCALAPPDATA%\Shortly\` | Windows Documents의 `Shortly\` |
| `personal` | `%LOCALAPPDATA%\ShortlyPersonal\` | Windows Documents의 `Shortly Personal\` |

`SHORTLY_USER_DATA`, `SHORTLY_DATA_DIR`, `SHORTLY_ARTIFACT_DIR`,
`SHORTLY_LOG_DIR`, `SHORTLY_TTS_OUTPUT_DIR`로 명시적으로 재정의할 수 있습니다.
기존 데이터는 제품 프로파일 사이에서 자동 이동하거나 자동 탐색하지 않습니다.

제품별 실행 엔트리포인트:

```bash
# 기존 호환 명령(로컬에서는 personal composition root 사용)
python -m shortly web

# 개인 자동화판 로컬 웹 앱
python -m shortly.apps.personal.launcher

# 팀판은 portable 패키지의 기존 시작 파일이 team composition root를 사용
```

팀판의 LLM 레지스트리는 현재 Anthropic만 등록합니다. 개인판과 호환 실행은
Gemini, OpenAI, Anthropic 및 OpenAI-compatible 로컬 서버 연결을 유지합니다.
팀 포터블 빌드는 개인판 앱과 로컬 LLM 어댑터를 산출물에서 제외합니다.

### 개인 자동 생성판

개인판에서 이미지·영상 생성까지 사용하려면 선택 의존성을 추가로 설치합니다.

```bash
python -m pip install -r requirements.txt -r requirements-personal.txt
python -m shortly.apps.personal.launcher
```

브라우저의 기존 설정 화면에서 OpenAI 및 Google API 키를 저장한 뒤
`http://localhost:8756/personal/assets`에서 기존 guide JSON을 컷 명세로 변환하고
컷별 에셋 유형을 검토할 수 있습니다. 유료 생성은 비용 확인, 명시적 승인, 실행의
세 단계로 분리되며 승인 토큰은 한 번만 사용할 수 있습니다. 생성물과 사용량 DB는
개인판 데이터·결과물 폴더에 저장됩니다.

실제 API 가격은 바뀔 수 있으므로 생성 전 공급자 대시보드의 현재 가격을 확인하세요.
영상 공급자인 Gemini Omni Flash는 현재 preview이므로 중요한 작업은 결과와 과금을
별도로 확인해야 합니다.

### 로컬 모델 사용 (Ollama / LM Studio)

`provider=local` 로 OpenAI 호환 로컬 서버에 붙습니다. `pip install openai` 가 필요합니다.

- `SHORTLY_LOCAL_BASE_URL`: Ollama `http://localhost:11434/v1`, LM Studio `http://localhost:1234/v1`
- `SHORTLY_TEXT_MODEL`: 텍스트 모델 (예: Ollama `qwen3.6:27b`)
- `SHORTLY_VISION_MODEL`: 비전 모델 (예: Ollama `qwen3-vl:8b-instruct-q8_0`)
- `SHORTLY_LOCAL_NO_THINK`: Qwen3 등 reasoning 모델의 thinking 자동 끔 (기본 `1`)

Ollama 예시:

```bash
ollama pull qwen3.6:27b            # 텍스트
ollama pull qwen3-vl:8b            # 이미지(프레임) 분석용 비전 모델
```

> 참고: 텍스트는 `qwen3.6:27b` 로 충분하지만, 프레임 이미지 분석은 Ollama 에서 Qwen3.6 비전이 아직 안 올라오므로
> 별도의 비전 모델(`qwen3-vl` 등)을 `SHORTLY_VISION_MODEL` 로 지정하거나, LM Studio 로 mmproj 를 올려 사용하세요.

LM Studio 로 27B 비전까지 쓰기(최고 품질):

- Qwen3.6-27B GGUF(예: `IQ4_XS`/`Q4_K_M`)와 **짝이 되는 `mmproj-*.gguf`** 를 같은 폴더에 두면 LM Studio 가 자동 인식(모델명 옆 눈 아이콘).
- LM Studio 서버 실행 후:

```
SHORTLY_LLM_PROVIDER=local
SHORTLY_LOCAL_BASE_URL=http://localhost:1234/v1
SHORTLY_TEXT_MODEL=<LM Studio 모델 id>
# 비전 모델을 비우면 위 텍스트 모델(27B)이 이미지까지 처리합니다
```

> VRAM 16GB(RTX 5080 등)면 27B 는 `IQ4_XS`/`Q4_K_M` 권장. 일부 CPU 오프로딩으로 느려질 수 있습니다.
> 일부 MTP 전용 GGUF 는 최신 llama.cpp(b9180+) 가 필요해 LM Studio 에서 안 열릴 수 있으니, 일반(non-MTP) GGUF 를 받으세요.

## 사용법 (CLI)

```bash
# 1) 효과음 지문 인덱스 구축 (최초 1회)
python -m shortly build-sfx

# 2) 채널 학습 (기본: 조회수 높은 쇼츠부터 학습)
python -m shortly learn "@채널핸들" --max 20
#    조회수 조건 추가 (예: 50만 뷰 이상만)
python -m shortly learn "@채널핸들" --max 20 --min-views 500000

# 3) 제작 가이드 생성 (주제 입력)
python -m shortly guide "@채널핸들" "고양이가 갑자기 사라진 이유"

#    완성 대본을 스타일에 맞게 보정
python -m shortly guide "@채널핸들" --file my_script.txt --script

# 학습된 채널 목록 / 인덱스 상태
python -m shortly list
python -m shortly status

# 팀용 설치형 빌드 (Python·ffmpeg 포함 → 팀원 추가 설치 불필요)
python -m shortly build-installer
# Setup.exe 없이 폴더 구조만:  --skip-compile
# 런타임 재다운로드 생략(테스트): --skip-runtime
```

## 팀용 설치형 앱

`build-installer` 는 **Python·ffmpeg 를 폴더 안에 넣습니다.** 팀원은 따로 설치할 필요 없습니다.

- 학습 AI: Claude Sonnet 5 고정 / 생성 AI: Claude Opus 4.8 고정
- 설정: Anthropic API 키만 입력
- 효과음 기능 없음
- 결과물:
  - `dist/shortly-app/` — `Shortly 시작.vbs` 더블클릭
  - `dist/ShortlySetup.exe` — Inno Setup 이 설치돼 있으면 자동 생성 (관리자 권한 불필요)
- 내부 상태·API 설정: `%LOCALAPPDATA%\Shortly\` (업데이트해도 유지)
- TTS·사용자 결과물·로그: Windows Documents의 `Shortly\`

## 사용법 (웹 UI)

```bash
python -m shortly web
# 이 PC:  http://localhost:8756
# 같은 공유기의 다른 PC: http://<이 PC의 LAN IP>:8756  (실행 시 주소를 안내합니다)
```

로컬 `web` 명령은 개인판을 실행하므로 저장된 가이드의 생성 에셋 검토·자동 배치
기능도 함께 제공합니다. 비용이 발생하는 개인 자동화 API는 이 PC의 loopback
접속에서만 허용됩니다.

기본으로 `0.0.0.0` 에 바인딩되어 **같은 공유기(LAN)의 다른 PC/휴대폰에서도 접속**할 수 있습니다. 다른 기기는 화면만 볼 뿐, **다운로드·분석 등 모든 처리는 이 PC(서버)에서** 수행됩니다.

- 접속이 안 되면 Windows 방화벽에서 포트를 한 번 열어주세요(관리자 PowerShell):

```powershell
New-NetFirewallRule -DisplayName "shortly" -Direction Inbound -LocalPort 8756 -Protocol TCP -Action Allow
```

- **보안**: 기본적으로 사설망(LAN)·로컬에서만 접속을 허용하고 외부(공인) IP 는 차단합니다(`SHORTLY_LAN_ONLY=1`). 혹시 포트포워딩 등으로 외부에 노출돼도 외부 접속은 403 으로 막힙니다.
- 이 PC에서만 쓰려면 `.env` 의 `SHORTLY_HOST=127.0.0.1` 로 바꾸세요.

브라우저 화면에서 바로:

- 설정: 사용할 AI(Gemini/OpenAI/Claude) 선택 + API 토큰 입력 (없으면 자동 규칙으로 동작)
- 효과음 준비 → 채널 학습(학습할 개수·최소 조회수 지정, 기본은 조회수 높은 순) → 리포트 확인 → 가이드 생성/다운로드

용어 안내(개인용, 쉽게):

- "영상 속 자막 글자도 읽기": 화면에 박힌 자막 텍스트까지 인식(OCR)
- "효과음 찾기 정확도 높이기": 배경음악·목소리를 걷어내고 효과음만 대조(느림)
- "이미 한 것도 다시 분석": 이전 분석 캐시를 무시하고 처음부터 재분석

## 결과물 위치

- `data/sfx_index/` : 효과음 카탈로그·지문 DB
- `data/channels/<채널>/profile.json` : 채널 스타일 프로파일
- `data/channels/<채널>/report.md` : 사람이 읽는 스타일 리포트
- `data/channels/<채널>/outputs/<제목>/` : 생성된 대본·가이드·CapCut 초안

## 구조

```
shortly/
  cli.py            # CLI (learn/guide/build-sfx/web/list/status)
  service.py        # 공용 서비스 계층 (웹·CLI 공유)
  config.py         # 환경설정
  llm/              # LLM/비전 제공자 추상화 (gemini/openai/anthropic)
  sfx/              # 효과음 카탈로그 + 오디오 지문 매칭
  ingest/           # yt-dlp 수집
  analyze/          # transcript / video_cv / audio_sfx / vision / style_profile
  generate/         # script_gen / guide / capcut_export
  web/              # FastAPI 앱 + 단일 페이지 프론트엔드
```

## 참고/주의

- CapCut 초안은 편집 설계도(초안) 형식입니다. 효과음 이름을 `sfx_sound` 폴더 파일과 매칭해 배치하고, 나레이션 트랙에 생성한 TTS 를 올려 사용하세요.
- 개인 학습/제작 보조 용도로 로컬에서만 동작하며, 수집물은 재배포하지 않습니다.
