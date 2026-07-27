# 배포 준비 체크리스트

작성일: 2026-07-27  
판정: 현재 빌드는 **Windows x64 내부 시험용**에 가깝다. 확정된 첫 배포 목표는
**Windows 11 x64 portable ZIP**이다. 팀 배포 전에는 개인 기본값 제거, 의존성 프로파일
분리, ZIP manifest와 CapCut 회귀 검증이 필요하다.

제품 정책은 [`product-decisions.md`](product-decisions.md), 다음 구현 범위는
[`implementation-roadmap.md`](implementation-roadmap.md)를 우선한다.

## 1. 판정 기준

- `[x]` 코드·Git·로컬 파일에서 확인
- `[ ]` 미완료
- `[?]` 코드만으로 확정할 수 없어 실제 환경 검증 필요

비밀값 자체는 이 문서에 기록하지 않는다.

## 2. Git 및 비밀정보 검사

### 확인 결과

- [x] 현재 브랜치는 `main`이고 `origin/main`을 추적한다.
- [x] 원격은 `https://github.com/preisters/shortly.git`이다.
- [x] 태그는 없다.
- [x] 제품 결정 작업 시작 시 `main`에는 직전 검토의 문서 4개 변경만 남아 있었고
  애플리케이션 변경은 없었다. 변경을 보존한 채 `docs/product-decisions-20260727`
  브랜치로 이동했다.
- [x] 루트 `.env`는 존재하지만 `.gitignore`에 의해 제외되어 추적되지 않는다.
- [x] 로컬 `.env`에는 값이 설정된 `SHORTLY_LOCAL_API_KEY`, `ANTHROPIC_API_KEY`, `TYPECAST_API_KEY` 항목이 있다. 값은 출력하지 않았다.
- [x] `GEMINI_API_KEY`, `OPENAI_API_KEY` 항목은 현재 로컬 파일에서 비어 있다.
- [x] 현재 세 커밋의 전체 추적 파일과 이력에서 `.env`, `data/**`, `dist/**`는 추적된 적이 없다.
- [x] 현재 세 커밋을 대상으로 한 credential 형태 문자열 검사에서는 비밀값으로 판정할 패턴을 발견하지 못했다.
- [x] `.gitignore`는 `.env`, `data/`, `dist/`, Python 캐시, 루트 임시 스크립트 등을 제외한다.
- [x] 추적된 코드·예제에는 개인 절대 경로와 특정 채널·브랜드명이 남아 있다.

최근 커밋:

```text
8add3d6 docs: document architecture and edition split strategy
c168bbb feat: add automated YouTube Shorts production pipeline
8a7d03b chore: protect local secrets and generated artifacts
```

### 추적된 개인화 항목

| 위치 | 내용 유형 | 조치 |
|---|---|---|
| `.env.example` 42, 44, 50행 부근 | 개인 TTS·템플릿·SFX 절대 경로 또는 이름 | 빈 값과 설명형 예제로 교체 |
| `README.md` 25행 부근 | 개인 SFX 경로 | 일반 사용자 경로 예제로 교체 |
| `shortly/config.py` 136, 138, 148, 216, 217, 223행 부근 | 개인 TTS·SFX 경로와 템플릿 기본값 | portable 중립 기본값과 personal override로 분리 |
| `assets/templates/make_templates.py` 25~28행 부근 | 특정 브랜드명 | 개인판으로 이동하거나 입력 인자로 변경 |
| `assets/templates/template_*.png` 네 개 | 특정 채널 이름이 포함된 템플릿 | 팀 공용 자산인지 소유자가 결정; 개인용이면 배포 제외 |
| `shortly/generate/capcut_export.py` 1449행 부근 | 개인 템플릿 기본명 | 설정 필수값 또는 중립값으로 변경 |

추적된 PNG 네 개에는 검사 시 EXIF와 별도 metadata key가 없었다. 다만 이미지 내용과 상표·사용 권한은 코드로 판단할 수 없다.

### 필요한 조치

- [ ] 현재 로컬 키의 과거 공유·백업·화면 노출 여부를 소유자가 확인한다.
- [ ] 노출 가능성이 있으면 각 공급자 콘솔에서 키를 회전한다.
- [ ] 릴리스 artifact마다 비밀·개인 경로 문자열 검사를 실패 조건으로 실행한다.
- [ ] GitHub secret scanning과 push protection 사용 가능 여부를 private 저장소 설정에서 확인한다.
- [ ] `.env.example`에는 빈 값과 설명만 남긴다.

## 3. 비밀 저장과 로그

### 현재 확인

- [x] `shortly/config.py`는 개발 루트 `.env`, `env.defaults`, portable 사용자 `.env`를 읽는다.
- [x] 설정 UI는 API 키 값을 GET 응답으로 반환하지 않고 설정 여부만 보여준다.
- [x] 설정 저장은 사용자 `.env`에 평문으로 기록한다.
- [x] 개발 서버 기본 바인딩은 `0.0.0.0:8756`이고 사설망 IP 필터만 있다. 인증, TLS, CSRF 방어는 없다.
- [x] portable launcher는 localhost로 실행한다.

### 출시 조건

- [ ] 팀판 기본 host를 `127.0.0.1`로 고정한다.
- [x] 초기 버전은 기존 사용자 `.env` 저장 방식을 유지하기로 결정했다.
- [ ] `.env` 파일 권한, key 비노출, backup 제외를 검증한다.
- [ ] Windows Credential Manager 또는 keyring 전환은 후속 보안 단계에서 재검토한다.
- [ ] macOS 지원 시 별도 Keychain 구현을 둔다.
- [ ] `.env`는 개발 모드 대안으로만 허용하고 저장 위치·권한·위험을 안내한다.
- [ ] SDK 예외, 요청 헤더, 프롬프트, 사용자 대본의 로그 노출을 테스트한다.
- [ ] 진단 bundle은 키와 프롬프트를 마스킹하고 사용자가 내용을 확인한 뒤 내보내게 한다.
- [ ] 원격 브라우저 접근이 필요하면 인증·origin·CSRF·TLS 정책을 새로 설계한다.

## 4. 배포에서 제외할 파일

- [x] 루트 `.env`
- [x] `data/**`의 영상, 자막, 프레임, 분석 결과, TTS, 보고서와 캐시
- [x] 기존 `dist/**`
- [x] Python·테스트·타입 검사 캐시
- [x] 루트 `_tmp_*`, `_plan_*`, `_make_*`, `_tts_once.py`
- [x] 로컬 모델 파일과 Ollama·LM Studio 캐시
- [x] SFX fingerprint·CLAP cache와 개인 SFX 라이브러리
- [ ] 개인 CapCut 템플릿과 브랜드 PNG의 팀 공용 여부를 결정하고 빌드 manifest에 반영한다.
- [ ] crash dump와 사용자 로그의 보존·제외 규칙을 추가한다.

**[확인]** 현재 portable builder는 루트 `data`나 임시 스크립트를 복사하지 않지만 `shortly/` 전체를 복사한다. 따라서 실행 시 비활성화된 로컬·SFX 코드와 그 import 경로가 팀 artifact에 남는다.

## 5. 설정 검증

- [ ] 팀판과 개인판의 허용 provider·모델 목록을 분리한다.
- [ ] 시작 시 API 키, SDK, 모델명, FFmpeg·ffprobe, 쓰기 권한, 디스크 여유를 검사한다.
- [ ] CapCut 설치 여부, 프로젝트 루트, 템플릿, 폰트, TTS 설정을 사전 검사한다.
- [ ] Typecast 키가 없을 때 “TTS 없이 계속”과 “설정 후 재시도”를 명확히 구분한다.
- [ ] YouTube·검색·LLM처럼 비용·네트워크를 쓰는 단계에 사전 안내를 표시한다.
- [ ] 지원하지 않는 local provider는 팀 UI와 API에서 선택할 수 없게 한다.
- [ ] 개인 기본 경로와 브랜드 템플릿명을 제거한다.
- [ ] 모든 `SHORTLY_*` 변수의 단일 설정표를 만든다.
- [ ] 설정 문법 오류를 비개발자용 한국어 메시지로 바꾼다.
- [ ] 동일 제목 생성 시 기존 폴더 재사용·덮어쓰기 동작을 유지할지 변경할지 결정한다.

## 6. 의존성과 빌드

### 현재 확인

- [x] `requirements.txt`에는 FastAPI, Uvicorn, NumPy, SciPy, Pillow, yt-dlp, OpenCV, scenedetect, faster-whisper, easyocr, anthropic, ddgs가 있다.
- [x] OpenAI SDK와 Google 생성 SDK는 주석 처리되어 있다.
- [x] `shortly/sfx/clap_match.py`는 별도로 `torch`, `torchaudio`, `transformers`, `demucs`와 Hugging Face CLAP 모델을 요구한다.
- [x] 위 SFX 의존성은 현재 requirements에 완전하게 선언되지 않았다.
- [x] requirements 주석의 “Demucs가 없으면 FFmpeg vocal attenuation 폴백”은 실제 코드에서 확인되지 않았다. 현재는 분석 불가 결과로 끝난다.
- [x] `service.py`가 SFX 분석 모듈을 eager import하므로 파일만 제외하는 팀 빌드는 현재 깨질 수 있다.
- [x] portable requirements는 OCR·Whisper 등 무거운 분석 의존성을 그대로 포함한다.
- [x] 로컬 `dist`는 약 2.65GB, 약 34,000개 파일로 관찰됐다. 이는 릴리스 크기 보장이 아니라 현 로컬 산출물 측정값이다.
- [x] runtime 검증 실패는 installer 생성 과정에서 경고로 처리될 수 있어 불완전한 artifact가 성공처럼 남을 수 있다.
- [x] `pyproject.toml`에는 런타임 dependencies나 optional dependency group이 없다.

### 출시 조건

- [ ] `basic`, `analysis`, `personal` 의존성 프로파일을 확정한다.
- [ ] SFX 코드와 무거운 의존성을 personal 전용 import 경계 뒤로 옮긴 후 팀 빌드에서 제외한다.
- [ ] 버전 상한·lock·hash 전략을 정한다.
- [ ] Python·FFmpeg·get-pip artifact의 SHA-256을 검증한다.
- [ ] SBOM과 제3자 라이선스 목록을 생성한다.
- [ ] `verify_runtime` 실패를 release 빌드 실패로 처리한다.
- [ ] yt-dlp, Whisper, OCR, Pillow, DDGS, Uvicorn과 기능별 경로까지 검증한다.
- [ ] 팀 artifact에서 personal source, 모델 SDK, 캐시가 실제로 없는지 검사한다.

## 7. 네트워크와 외부 서비스

- [ ] YouTube, 검색, Anthropic/OpenAI/Google, Typecast, Python/PyPI/FFmpeg 빌드 경로의 도메인을 문서화한다.
- [ ] 모든 외부 요청에 timeout을 둔다.
- [ ] 공급자 오류를 인증, quota, rate limit, 일시 장애로 구분한다.
- [ ] 중앙 재시도 정책과 최대 횟수·비용·시간을 정한다.
- [ ] API 비용 발생 전 작업과 예상 범위를 알린다.
- [ ] UI 취소와 실제 공급자 요청 취소의 차이를 표시한다.
- [ ] 회사 방화벽·프록시·인증서 환경에서 시험한다.
- [ ] 외부 검색 결과는 신뢰할 수 없는 입력으로 취급한다.

## 8. 사용자 데이터와 캐시

- [ ] YouTube 영상, 자막, 화면 캡처, 대본, 조사 결과의 보존 기간을 정한다.
- [ ] 사용자별 데이터 위치와 “삭제”의 실제 범위를 문서화한다.
- [ ] export/import에서 비밀과 개인 절대 경로를 제거한다.
- [ ] 다운로드 콘텐츠와 생성 에셋의 사용 권한 정책을 확인한다.
- [ ] prompt·대본 로그 보존을 사용자가 선택하게 한다.
- [ ] pickle은 신뢰하는 로컬 cache만 읽도록 경계를 유지한다.
- [ ] 캐시 정리 중 사용 중인 프로젝트와 복구 가능한 데이터를 보호한다.

## 9. 운영체제와 CapCut

### Windows

- [x] Windows AMD64 embedded Python 3.12.10을 사용한다.
- [x] Windows FFmpeg bundle, Inno Setup, VBS, BAT를 사용한다.
- [x] 설치 권한은 `PrivilegesRequired=lowest`이고 사용자 데이터는 `%LOCALAPPDATA%\Shortly` 아래에 둔다.
- [x] CapCut 프로젝트와 글꼴 탐색도 Windows 경로에 맞춰져 있다.
- [ ] 지원 Windows 버전과 x64·ARM64 범위를 명시한다.
- [ ] 깨끗한 VM에서 설치, 첫 실행, 종료, 재실행, 업그레이드, 제거를 시험한다.
- [ ] installer 서명, SmartScreen UX, 회사 PC 권한을 검증한다.
- [ ] 전역 `taskkill /F /IM pythonw.exe` 종료 방식을 Shortly PID 기반으로 바꾼다.
- [ ] 한글·공백·긴 경로와 포트 충돌을 시험한다.

### macOS

- [x] 현재 빌드·설치·CapCut 경로는 macOS를 지원하지 않는다.
- [ ] universal 또는 아키텍처별 Python·FFmpeg, 코드 서명, notarization을 설계한다.
- [ ] CapCut macOS 프로젝트 경로와 스키마를 조사한다.
- [ ] Keychain, 폰트, 파일 권한, Apple Silicon·Intel 범위를 검증한다.

첫 팀 릴리스는 Windows 11 x64 전용이라고 명시한다. Windows ARM64와 macOS는 초기
지원 범위가 아니다.

### CapCut

- [x] 실제 exporter는 기존 프로젝트를 템플릿으로 복제한다.
- [x] 공식 안정 스키마가 아니라 설치된 CapCut 프로젝트 JSON을 재사용한다.
- [x] 별도로 중립 `shortly_schema: capcut-draft-v0` 출력도 제공한다.
- [ ] 지원 CapCut 버전을 확정한다.
- [ ] 익명화한 템플릿 fixture와 결과 golden을 만든다.
- [ ] 원본 불변, 실패 시 복구, 프로젝트 ID·material 참조 무결성을 검사한다.
- [ ] CapCut 실행 중 파일 충돌 정책을 정한다.
- [ ] macOS나 CapCut 미설치 환경에는 중립 draft 다운로드를 제공한다.

## 10. 최소 회귀 테스트

이번 재검토에서 외부 API를 호출하지 않는 안전한 smoke 검사를 수행했다.

- [x] `dist`·`.git` 제외 로컬 Python 50개(추적 44개, Git 제외 개인 스크립트 6개) AST parse: 오류 없음
- [x] source path를 명시한 CLI help 로드: 8개 command 확인
- [x] 휴리스틱 script → guide → `capcut-draft-v0` 생성
- [x] FastAPI 앱 import와 route 24개 확인
- [?] YouTube 다운로드·분석, 외부 LLM, Typecast, 로컬 모델, 실제 CapCut E2E는 비용·환경 변경 위험 때문에 실행하지 않음

릴리스 전 자동화해야 할 최소 목록:

1. 모든 Python parse·핵심 import smoke
2. CLI help와 기존 8개 명령
3. dev·portable 설정 우선순위와 API key 비노출
4. LLM factory의 role별 provider 선택과 unavailable 오류
5. 외부 API 없는 휴리스틱 script 생성 계약
6. guide 필수 필드, 세그먼트·컷 수, 추천 필드 계약
7. 중립 `capcut-draft-v0` golden
8. 웹 24개 route와 download path traversal 방어
9. Typecast payload와 cache key 결정성(fake·무네트워크)
10. 기존 guide JSON round-trip과 같은 제목 경로 동작
11. `.env`, `data`, `dist`, 개인 절대 경로의 Git·artifact hygiene
12. 익명화한 CapCut fixture 기반 원본 불변, ID 재생성, material 참조, TTS 타이밍, 루트 등록 golden

## 11. 현재 정상 기준선과 태그 제안

- **[확인]** `c168bbb`가 현재 애플리케이션 코드를 도입한 커밋이고, `8add3d6`은 그 위에 문서만 추가했다.
- **[확인]** `c168bbb..8add3d6` 사이 애플리케이션 source 차이는 없다.
- **[추정]** 제한된 무네트워크 smoke 검사 기준으로 `c168bbb`를 “분리 전 정상 코드 기준선” 후보로 판단한다.
- **[확인 필요]** 실제 YouTube·LLM·TTS·CapCut E2E 통과 기록이 없어 운영 정상 버전으로 확정할 수는 없다.

태그는 아직 만들지 않는다.

- 권장 대상: `c168bbbeababccd7c02018bc3d8e80b07322f334`
- 권장 이름: `baseline/pre-split-2026-07-27`
- 문서까지 포함해야 한다면 차선 대상은 `8add3d6cb8519f3e45b54c25ffb30931d3024ec3`, 이름은 `baseline/pre-split-docs-2026-07-27`

태그 전에는 소유자가 현 PC에서 핵심 E2E 한 건과 익명화된 CapCut 결과를 확인해야 한다.

## 12. 팀판 최소 배포 범위

- Windows 11 x64 portable ZIP, localhost 단일 사용자 UI
- 승인된 상용 LLM 공급자와 사용자 API 키
- 채널 학습에 실제 필요한 분석 기능
- 주제·대본 입력, 조사 선택, 대사·자막·컷 추천
- 선택적 Typecast TTS
- 검증된 한 가지 CapCut 템플릿 방식과 중립 draft
- 사용자 데이터 분리, 설정 진단, 비개발자용 오류 안내

첫 팀판에서 제외:

- 로컬 Qwen·Ollama·LM Studio
- SFX CLAP·Demucs 고급 분석
- 생성 이미지·영상
- 개인 멀티 에이전트·작업 그래프
- 자동 업데이트와 macOS

## 13. 구현 전 결정할 사항

중요도순:

1. 지원 CapCut 버전과 익명화한 기준 template
2. portable ZIP 코드 서명과 전달 채널
3. 브랜드 PNG와 CapCut 템플릿이 팀 공용 자산인지 개인 자산인지
4. 임시 파일을 Documents와 LocalAppData 중 어디에 둘지
5. 로그 보존 기간과 최대 크기
6. OCR·ASR를 계속 기본 포함할지 별도 bundle로 둘지
7. 동일 제목 생성 시 기존 출력 폴더 재사용·덮어쓰기 정책
8. 사용자 영상·대본·캐시의 보존·삭제 정책
9. 검색 기능의 인터넷 사용과 콘텐츠 권리 정책
10. 코드 서명 도입 시점과 인증서 소유자

## 14. 최종 요약

- 가장 먼저 할 작업은 기준선 E2E 기록과 무네트워크 계약 테스트 추가다.
- 지금 당장 건드리면 위험한 부분은 CapCut 템플릿 복제·ID·TTS 타이밍·루트 등록 로직이다.
- 팀 배포 최소 작업은 Windows 11 localhost 프로파일, 개인 기본값 제거, 의존성 분리,
  portable ZIP 검증과 CapCut 검증이다. 초기 key 저장 방식은 유지하되 로그 노출을 막는다.
- 개인 자동화판의 첫 구현은 `CutManifest`와 수동 승인 흐름이며 생성 API 연결은 그 다음이다.
- 예상 기술 부채는 eager import, 단일 서비스 모듈, 무스키마 dict, 인메모리 작업 상태, 미고정 의존성, 비공식 CapCut 스키마다.
- 반드시 추가 확인할 사항은 팀의 실제 필수 분석 범위, 자산 소유권, CapCut 지원 버전, 운영 E2E 기준선이다.
