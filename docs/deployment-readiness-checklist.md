# 배포 준비 체크리스트

작성일: 2026-07-27  
판정: 현재 상태는 팀 일반 배포 준비 완료가 아니다. 제한된 Windows 테스트 배포 전에도 보안, 설정, 의존성, 회귀 테스트 작업이 필요하다.

상태 표기:

- `[x]` 코드에서 확인됨
- `[ ]` 미완료
- `[?]` 코드만으로 확정 불가

## 1. 즉시 보안 조치

- [x] 루트 `.env`의 `ANTHROPIC_API_KEY`와 `TYPECAST_API_KEY`가 비어 있지 않음을 확인했다. 값은 출력하지 않았다.
- [ ] 해당 키를 노출된 것으로 간주할지 판단하고, 원본 저장소/백업/공유 이력을 확인한다.
- [ ] 공유 또는 커밋 가능성이 있었다면 공급자 콘솔에서 키를 회전한다.
- [ ] `.gitignore`를 추가해 최소 `.env`, `data/`, `dist/`, `__pycache__/`, `*.pyc`, 작업 로그, 생성 에셋, 로컬 DB를 제외한다.
- [ ] `.env.example`에는 빈 값과 설명만 남긴다.
- [ ] 배포 artifact에서 문자열/엔트로피 기반 secret scan을 수행한다.
- [ ] 설치 로그와 오류 보고서에서 Authorization, API 키, 전체 프롬프트/응답을 마스킹한다.

주의: `.env`의 `SHORTLY_LOCAL_API_KEY`도 비어 있지 않지만 로컬 서버용 placeholder일 수 있다. 값의 성격은 별도 확인이 필요하다.

## 2. 비밀 저장 방식

현재 상태:

- 웹 UI는 키 값을 GET 응답으로 돌려주지 않고 설정 여부만 반환한다.
- POST된 키는 사용자 `.env`에 평문으로 저장한다.
- 개발 웹 서버는 기본 `0.0.0.0` 바인딩이고 LAN 전용 IP 필터만 있다. 사용자 인증, CSRF 보호, TLS가 없다.

출시 조건:

- [ ] 팀 설치형 기본 host를 `127.0.0.1`로 강제한다.
- [ ] Windows Credential Manager를 사용하는 `SecretStore`를 구현하거나 검증된 keyring 사용을 결정한다.
- [ ] macOS 지원 시 Keychain 구현을 별도로 둔다.
- [ ] `.env`는 개발 모드 폴백으로만 허용하고 파일 권한/위험을 안내한다.
- [ ] 설정 API에 origin/CSRF 방어 또는 localhost 전용 정책을 적용한다.
- [ ] 오류 객체에 SDK 원문이 포함될 때 키/요청 헤더가 노출되지 않는지 테스트한다.
- [ ] 프롬프트/스크립트 로그 보존 수준과 개인정보 정책을 정한다.

## 3. 배포 제외 파일

팀 배포물에 포함하지 않아야 한다.

- [ ] 루트 `.env`
- [ ] `data/**`의 다운로드 영상, VTT, 분석, 생성 대본, TTS, 보고서, 캐시
- [ ] 기존 `dist/**`
- [ ] `__pycache__`, `*.pyc`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`
- [ ] `_tmp_*`, `_plan_*`, `_make_*`, `_tts_once.py`
- [ ] 개인 템플릿/이미지 및 특정 채널 자산
- [ ] SFX fingerprint pickle/NPZ와 개인 SFX 라이브러리
- [ ] 사용자 로그와 crash dump
- [ ] 로컬 모델 파일, Ollama/LM Studio 캐시

현재 `portable` 빌더는 `shortly/` 패키지만 복사하므로 루트 `data`와 임시 스크립트는 설치형에는 직접 포함되지 않는다. 그러나 Git/소스 공유에 대한 ignore 정책이 없고, `shortly/` 전체를 복사하므로 팀판에 불필요한 개인 기능 코드가 포함된다.

## 4. 설정 검증

- [ ] 지원 provider 목록과 팀 허용 목록을 분리한다.
- [ ] 모델명을 코드 상수 대신 배포 manifest/관리자 설정으로 둔다.
- [ ] 시작 시 API 키 존재, SDK 설치, 모델 설정, FFmpeg/ffprobe, 쓰기 권한, 디스크 여유를 검사한다.
- [ ] CapCut 설치 여부, 프로젝트 루트, 템플릿 프로젝트, 자막/오디오 샘플, 폰트를 사전 검사한다.
- [ ] Typecast 키가 없을 때 “TTS 없이 내보내기”와 “키 입력 후 재시도”를 명확히 구분한다.
- [ ] 연구/팩트체크가 네트워크를 사용하고 추가 비용/시간이 들 수 있음을 표시한다.
- [ ] 지원되지 않는 local provider가 팀 UI/API에서 선택되지 않게 한다.
- [ ] 개인 기본 TTS/SFX 경로와 CapCut 템플릿명을 제거한다.
- [ ] `SHORTLY_*` 환경변수와 `.env.example`의 완전한 대응표를 만든다.
- [ ] 설정 파일 문법 오류와 쓰기 실패를 비개발자용 한국어 메시지로 바꾼다.

## 5. 네트워크 및 API 안전

- [ ] 모든 외부 도메인을 문서화한다: YouTube, 검색, LLM 공급자, Typecast, Python/PyPI/FFmpeg(빌드 시).
- [ ] 요청 timeout을 모든 공급자에 명시한다.
- [ ] 재시도 횟수와 backoff를 중앙화한다.
- [ ] 인증 실패, quota, rate limit, 일시 장애를 구분한다.
- [ ] API 비용이 발생하기 전 작업 종류/공급자/예상 범위를 표시한다.
- [ ] 취소 버튼이 단순 UI 취소인지 공급자 요청 취소인지 명확히 한다.
- [ ] 프록시/사내 방화벽/인증서 환경을 테스트한다.
- [ ] 외부 검색 결과와 웹 콘텐츠를 신뢰하지 않는 입력으로 취급한다.

## 6. 사용자 데이터와 개인정보

- [ ] YouTube 영상, 자막, 화면 캡처, 대본, 조사 결과의 보존 기간을 정한다.
- [ ] 팀 PC별 사용자 데이터 위치와 삭제 방법을 제공한다.
- [ ] 프로젝트 export/import 시 비밀과 개인 경로를 제거한다.
- [ ] 다운로드 콘텐츠의 이용 권한과 팀 내부 사용 정책을 확인한다.
- [ ] 로그와 지원 번들에 원문 대본/프롬프트 포함 여부를 사용자가 선택하게 한다.
- [ ] 제거 기능은 복구 가능 범위와 삭제 대상 파일을 사전에 보여준다.
- [ ] pickle 캐시는 신뢰된 로컬 파일만 로드하도록 경계를 명시한다.

## 7. Windows 지원

현재 확인:

- [x] Windows AMD64 임베디드 Python 3.12.10을 사용한다.
- [x] Windows FFmpeg 번들을 다운로드한다.
- [x] Inno Setup/VBS/BAT/Windows 폰트/CapCut AppData 경로를 사용한다.
- [x] 설치 권한은 `PrivilegesRequired=lowest`다.
- [x] 사용자 데이터는 `%LOCALAPPDATA%\Shortly`다.

출시 전:

- [ ] 지원 Windows 버전과 x64/ARM64 범위를 명시한다.
- [ ] 깨끗한 VM에서 설치, 첫 실행, 종료, 재실행, 업그레이드, 제거를 테스트한다.
- [ ] 설치 파일에 코드 서명을 적용한다.
- [ ] 백신 오탐과 SmartScreen UX를 검토한다.
- [ ] `taskkill /F /IM pythonw.exe` 전역 종료 스크립트를 앱 PID 기반 종료로 교체한다.
- [ ] 경로에 한글/공백/긴 경로가 있는 계정에서 테스트한다.
- [ ] 권한이 낮은 회사 PC에서 `%LOCALAPPDATA%` 쓰기와 브라우저 열기를 테스트한다.
- [ ] 포트 충돌 시 자동 대체 또는 명확한 안내를 제공한다.

## 8. macOS 지원

현재 판정: 지원되지 않는다.

- [ ] macOS Python 런타임/앱 번들/코드 서명/notarization 설계
- [ ] FFmpeg universal 또는 아키텍처별 번들
- [ ] CapCut macOS 프로젝트 위치와 스키마 실측
- [ ] macOS 폰트와 파일 권한/Sandbox 검증
- [ ] Keychain 기반 비밀 저장
- [ ] 브라우저 런처와 종료/업데이트 방식
- [ ] Intel/Apple Silicon 지원 정책

팀이 macOS를 요구하지 않는다면 1차 릴리스는 Windows 전용이라고 명시하는 것이 안전하다.

## 9. CapCut 의존성

- [x] 현재 구현은 기존 CapCut 프로젝트를 템플릿으로 복제한다.
- [x] 기본 템플릿명이 개인 환경에 맞춰져 있다.
- [x] 공식 안정 스키마가 아니라 설치된 CapCut의 실제 JSON을 재사용한다.
- [ ] 지원 CapCut 버전 범위를 정한다.
- [ ] 앱 시작/내보내기 전 CapCut 실행 여부와 템플릿 준비 절차를 안내한다.
- [ ] 템플릿 생성 마법사 또는 검증 도구를 제공한다.
- [ ] 원본 프로젝트 백업과 원자적 쓰기를 보장한다.
- [ ] 생성 프로젝트가 열리는지 자동 구조 검증 후 사람이 smoke test한다.
- [ ] CapCut 실행 중 파일 쓰기 충돌 정책을 정한다.
- [ ] macOS 또는 CapCut 미설치 환경에서는 중립 `capcut_draft.json` 다운로드를 제공한다.

## 10. 의존성과 공급망

- [ ] `pyproject.toml`에 런타임/optional dependencies를 선언한다.
- [ ] 팀 basic/full과 personal dependency group을 분리한다.
- [ ] 버전 상한 또는 lock file과 해시를 도입한다.
- [ ] Python/FFmpeg/get-pip 다운로드 artifact의 SHA-256을 검증한다.
- [ ] SBOM과 제3자 라이선스 목록을 생성한다.
- [ ] yt-dlp, FFmpeg, OpenCV, Whisper, OCR, LLM SDK의 라이선스를 검토한다.
- [ ] 배포 artifact에서 불필요한 패키지 테스트, 캐시, 헤더, 개발 파일을 제거한다.
- [ ] 빌드 네트워크 실패 시 부분 artifact를 성공으로 오인하지 않게 한다.
- [ ] `verify_runtime` 실패를 경고로 계속 진행하지 말고 release 빌드에서는 실패 처리한다.

현재 `verify_runtime`은 `fastapi`, `anthropic`, `cv2`와 FFmpeg만 검사한다. yt-dlp, whisper, OCR, Pillow, DDGS, Uvicorn, CapCut/TTS 경로까지 포함한 기능별 진단이 필요하다.

## 11. 테스트와 품질 게이트

- [ ] 설정/경로 단위 테스트
- [ ] LLM/TTS/Search fake provider 계약 테스트
- [ ] `analysis.json`, `profile.json`, `guide.json` round-trip 테스트
- [ ] 대본/가이드/중립 draft golden 테스트
- [ ] CapCut 익명 fixture golden 테스트
- [ ] 템플릿 원본 불변 및 실패 롤백 테스트
- [ ] API 입력 검증, path traversal, download root 테스트
- [ ] localhost/LAN 접근 정책 테스트
- [ ] 작업 취소, 재시작, TTL/메모리 정리 테스트
- [ ] 외부 API가 없는 오프라인 오류 UX 테스트
- [ ] 깨끗한 Windows VM 설치 smoke test
- [ ] 빌드 artifact에 금지 파일/비밀/개인 경로가 없는지 검사

현재 앱 테스트는 발견되지 않았다. 제품 코드 변경 전 최소한 계약/golden 테스트부터 필요하다.

## 12. 첫 실행 UX

권장 순서:

1. 지원 OS/디스크/네트워크 확인
2. 저장 위치 안내
3. LLM 공급자와 키 입력
4. 키를 노출하지 않는 짧은 연결 테스트(사용자 동의 후)
5. 선택적 Typecast 설정
6. FFmpeg/분석 기능 진단
7. CapCut 설치/템플릿 준비 진단
8. 샘플 없는 빈 상태 UI
9. “채널 학습 → 주제/대본 → 가이드 → CapCut” 단계별 안내

오류 메시지는 다음 구조가 좋다.

```text
무엇이 실패했는지
현재 작업에 미치는 영향
사용자가 할 수 있는 1~3개 조치
기술 지원용 오류 코드(비밀 제외)
```

스택 트레이스를 기본 UI에 그대로 보여주지 않고, 민감정보를 제거한 진단 파일로 분리한다.

## 13. 릴리스 게이트

### 내부 알파

- [ ] 비밀 회전/ignore/개인 경로 제거
- [ ] Windows localhost 실행
- [ ] 핵심 contract/golden 테스트
- [ ] 팀 필수 기능 목록 확정
- [ ] CapCut 템플릿 검증과 실패 복구

### 제한된 팀 베타

- [ ] 서명된 설치 파일 또는 사내 승인된 전달 방식
- [ ] 깨끗한 PC 2대 이상에서 설치
- [ ] API 비용/오류/로그 정책
- [ ] 업데이트/롤백 절차
- [ ] 사용자 가이드와 지원 채널

### 일반 팀 배포

- [ ] 재현 가능한 빌드와 artifact manifest
- [ ] 의존성/라이선스/SBOM
- [ ] 자동 회귀 테스트
- [ ] 지원 OS/CapCut 버전 표
- [ ] 데이터 삭제/백업/복구 절차
- [ ] 알려진 제한과 장애 대응 절차

## 14. 최소 배포 범위

첫 팀 배포는 다음만 포함하는 것이 안전하다.

- Windows x64
- localhost 단일 사용자 웹 UI
- Anthropic 또는 승인된 API LLM 한 계열
- 채널 학습에 실제로 필요한 분석 기능
- 주제/대본 입력, 조사 선택, 대본/가이드/컷·자막 추천
- Typecast 선택적 TTS
- 검증된 단일 CapCut 템플릿 방식
- 사용자 데이터 분리, 설정 진단, 비개발자 오류 안내

로컬 Qwen, SFX 고급 분석, 생성형 이미지/영상, 멀티 에이전트, 자동 업데이트는 첫 팀 배포 범위에서 제외한다.

## 15. 구현 전 결정해야 할 사항

- 팀에서 허용할 LLM/TTS 공급자와 키 소유 방식
- OCR/ASR이 팀 기본 번들에 반드시 필요한지
- 지원 Windows/CapCut 버전 및 macOS 필요 여부
- 설치 파일 서명과 배포 채널
- 사용자 데이터 보존/삭제/백업 정책
- 조사 기능의 인터넷 사용과 콘텐츠/저작권 정책
- CapCut 템플릿을 팀 공용으로 제공할지 사용자가 만들지
- 팀판에서 모델 선택을 허용할지 관리자가 고정할지

