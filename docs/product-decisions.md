# 제품 결정 기록

작성일: 2026-07-27
상태: 승인된 사용자 결정과 기술 검증 결과
적용 대상: 팀 배포판과 개인 자동화판

이 문서는 제품 정책의 기준 문서다. 현재 구현 사실은
[`current-architecture-analysis.md`](current-architecture-analysis.md), 분리 과정은
[`team-personal-split-plan.md`](team-personal-split-plan.md), 생성 에셋 설계는
[`generative-assets-extension-plan.md`](generative-assets-extension-plan.md), 출시 조건은
[`deployment-readiness-checklist.md`](deployment-readiness-checklist.md)를 따른다.

## 1. 상태 표기

- **확정**: 사용자가 명시했거나 “Codex 추천안 적용”으로 선택을 위임해 이번 문서에서 확정한 사항
- **기술 보정**: 사용자 목적을 유지하면서 현재 코드와 안전 요구에 맞게 범위를 구체화한 사항
- **권장·미확정**: 사용자가 `미정`으로 남겼거나 외부 서비스 검증이 필요한 사항
- **현재 사실**: 저장소 코드에서 직접 확인한 사항

외부 생성 API는 호출하지 않았다. 모델 선택에 필요한 현재성만 2026-07-27에 공식
문서로 확인했다. OpenAI는 `gpt-image-2`, Google은
`gemini-omni-flash-preview`를 문서화하고 있다. preview 상태와 가격은 바뀔 수 있으므로
model ID와 단가는 구현·릴리스 직전에 다시 확인한다.

## 2. 핵심 제품 결정

### 2.1 공통 제품 구조

**확정**

- 하나의 저장소와 공통 코어를 사용한다.
- 팀판과 개인판을 장기 브랜치나 복사본으로 분기하지 않는다.
- 제품 차이는 엔트리포인트, provider registry, 설정 profile, 의존성 group, 빌드
  allowlist로 표현한다.
- 개인 기능 구현과 의존성은 팀 artifact에 포함하지 않는다.
- 기존 `service.py`, CLI, JSON 형식은 전환 기간 호환 facade로 유지한다.
- 공통 코어에는 제품별 정책이나 공급자 SDK를 넣지 않는다.

**기술 보정**

현재 `service.py`의 eager import와 `portable/build.py`의 `shortly/` 전체 복사 때문에
즉시 여러 installable package로 나누지 않는다. 먼저 현재 `shortly` 패키지 안에서
경계를 만들고, import·계약 테스트가 확보된 뒤 물리적 패키지 분리를 재검토한다.

### 2.2 팀 배포판

**확정**

- 공식 지원 범위는 **Windows 11 x64**다. 현재 embedded Python이 AMD64이므로 ARM64는
  포함하지 않는다.
- 초기 배포물은 포터블 폴더를 압축한 ZIP이다.
- 자동 업데이트는 초기 버전에 넣지 않는다.
- 예상 사용자는 4명이며, 3명은 비개발자다.
- 주 사용 목적은 주제에 맞는 대본 생성이다.
- 초기 팀판은 현재 portable에서 제공하는 기존 흐름을 유지하고, 이번 분리 이후의
  개인 자동화 기능은 넣지 않는다.
- 로컬 Qwen과 로컬 LLM 구현·의존성·UI·API route는 팀 artifact에서 제외한다.
  공통 코어에는 모델 포트만 남긴다.
- LLM 공급자, 기본 모델, API 키 입력·저장·검사, Typecast 사용은 초기에는 현 portable
  동작을 유지한다.
- 사용량·비용 집계는 앱에 추가하지 않고 각 API 공급자 대시보드에서 확인한다.
- CapCut은 Windows 데스크톱을 지원하고 자동 탐색 후 실패 시 사용자가 경로를
  지정하게 한다.
- 첫 실행 마법사와 고급 설정 분리는 추가하지 않는다.
- 일반 오류 메시지와 상세 로그를 분리한다.
- 원격 텔레메트리는 수집하지 않는다.

여기서 “기존 기능 유지”는 현재 portable 사용자에게 노출되는 채널 학습, 프로필·가이드
조회, 주제/대본 입력, 조사·팩트체크 설정, 대본·가이드 생성·수정, 중립 draft, Typecast와
CapCut 내보내기를 뜻한다. SFX와 로컬 모델은 사용자의 더 구체적인 제외 결정이 우선한다.

**기술 보정**

- 현재 portable UI는 로컬 설정을 숨기지만 `/api/local-models` route와 로컬 구현
  source는 남는다. “빌드에서 제외”를 충족하려면 team provider registry와 빌드 allowlist가
  필요하다.
- 현재 portable 데이터와 TTS는 `%LOCALAPPDATA%\Shortly` 아래다. 사용자가 지정한
  `문서\Shortly` 저장은 현재 동작 유지가 아니라 경로 변경이다.
- Windows 문서 폴더는 `%USERPROFILE%\Documents`를 문자열로 조합하지 않고 Windows
  Known Folder API로 찾는다. OneDrive와 폴더 리디렉션을 고려해야 한다.
- 사용자에게 보이는 TTS·이미지·영상·로그는 `문서\Shortly`에 둔다. 설정, 캐시, 다운로드
  중간 파일, 잠금 파일은 안정성과 정리를 위해 `%LOCALAPPDATA%\Shortly`에 유지하는 것을
  권장한다. “임시 파일도 문서 폴더”라는 입력과 충돌하므로 최종 위치는 아직 미확정이다.
- API 키의 일부 문자도 로그에 남기지 않는다. 사용된 키 구분은
  `provider=anthropic`, `credential_slot=team-default`와 비가역 fingerprint로 기록한다.
  이는 “어느 키인지 구분” 목적을 충족하면서 실제 secret 조각 노출을 막는다.
- 프롬프트·대본·파일 경로의 상세 로그는 사용자가 허용했지만 민감정보가 될 수 있다.
  로그는 로컬 전용, 명시적 보존 기간, 접근 권한과 삭제 기능이 필요하다.

### 2.3 개인 자동화판

**확정**

- 초기 실행 형태는 같은 저장소의 별도 personal 로컬 웹 엔트리포인트다.
- 공통 UI shell과 기본 화면은 공유할 수 있지만 personal panel과 정적 asset은 별도
  모듈로 두며 팀 artifact에는 복사하지 않는다.
- 로컬 서버 실행 방식과 현재 LLM routing은 유지한다.
- 로컬 Qwen은 기존 역할에 더해 최종 결과에 대한 **읽기 전용 검수자**로 사용한다.
  검수 결과는 수정 명령이 아닌 `ReviewReport`만 반환한다.
- 이미지·영상 생성과 공급자 전용 고난도 작업은 local 모델 사용을 금지할 수 있는
  capability 정책을 둔다.
- 공급자 실패나 한도 초과 시 자동으로 다른 유료 공급자로 넘어가지 않는다. 오류와
  예상 비용을 보여 주고 사용자 선택을 기다린다.
- 컷당 기본 후보는 1개다.
- 자동 재생성은 최대 1회다. 즉 최초 생성과 재생성을 합쳐 최대 2회의 유료 시도가
  가능하므로 두 시도 모두 예산에 포함한다.
- 사실성이 중요한 컷은 초기 버전에서 생성형 에셋을 만들지 않고
  `MANUAL_REQUIRED`로 둔다.
- 모든 유료 생성은 실행 전에 사람이 승인한다.
- 실패 후에는 수동 필요 상태로 전환한다.
- 모델별 요청, 사용량, 예상·확정 비용을 기록한다.
- 팀판과 개인판 데이터 저장소를 분리한다.
- 선택·교체·거절·재생성 기록은 최대한 상세히 영구 보존하고 향후 추천 개선에 사용할
  수 있다.

**기술 보정**

“CapCut 내보내기 시 생성”은 한 트랜잭션에서 바로 CapCut 파일을 쓰는 의미로 구현하지
않는다. 사용자가 내보내기를 누르면 다음 두 단계로 나눈다.

1. `PrepareExport`: 선택된 컷의 예상 비용을 계산하고 승인받아 에셋을 생성·검수한다.
2. `CommitExport`: 필요한 에셋이 준비된 뒤에만 CapCut 프로젝트 복제와 등록을 실행한다.

이 방식은 사용자 흐름을 유지하면서 생성 실패로 반쯤 작성된 CapCut 프로젝트가 생기는
것을 막는다.

초기 personal 작업 그래프는 실제 편집 가능한 CapCut 프로젝트에 도달하는 범위로
제한한다.

```text
기존 대본 생성
→ CutManifest 작성·사람 확인
→ 컷별 asset 유형·생성 체크
→ 유료 quote·승인
→ 생성 또는 MANUAL_REQUIRED
→ 결정론적 후처리·파일 검증
→ 사람의 asset 확인·교체
→ CommitExport
→ Qwen 읽기 전용 ReviewReport
→ 사람 최종 확인
```

주제 자동 선정, 자유로운 조사 에이전트, 자동 업로드는 이 첫 그래프에 새로 추가하지
않는다. 현재 조사·대본 기능은 입력 단계로 재사용한다. 자동 재시도는 rate limit·timeout·
일시적 provider 장애 또는 다운로드 무결성 실패에만 최대 1회 허용한다. 인증·quota·
moderation·예산 초과는 재시도하지 않고 사람에게 돌려보낸다. 이전 단계로 돌아가는
행동도 사람이 `regenerate` 또는 `replace`를 선택했을 때만 수행한다.

### 2.4 생성 공급자와 비용

**확정된 정책**

- 첫 이미지 adapter는 OpenAI `gpt-image-2`를 대상으로 한다. alias 대신 재현 가능한
  snapshot 사용 여부는 릴리스 때 결정한다.
- 첫 영상 **평가 adapter**는 Google `gemini-omni-flash-preview`를 대상으로 한다.
  공식 문서상 9:16, text/image-to-video를 지원하고 720p 출력은 약 USD 0.10/초로
  안내된다. preview 모델이므로 팀이나 무인 production 기본값으로 고정하지 않고
  personal 승인형 pilot에서만 사용한다.
- Veo 3.1 Fast/Lite는 특정 기능·가격·안정성 비교를 위한 수동 선택 대안이다. 자동
  폴백 대상으로 사용하지 않는다.
- 완성 결과 검수의 로컬 후보는 Qwen 3.6 계열이다. 실제 로컬 model ID, vision 지원,
  컨텍스트 길이와 처리 속도는 설치된 서버에서 확인한다.
- 유료 작업은 예산 안이어도 매번 사용자 승인을 받는다.
- 공급자 예상 비용을 계산할 수 없으면 실행을 차단하고 사용자가 확인하게 한다.

**초기 권장 soft limit**

| 범위 | 권장 한도 | 동작 |
|---|---:|---|
| 영상 한 편의 이미지 생성 | USD 2 | 초과 예상 시 승인 화면에서 차단 |
| 영상 한 편의 영상 생성 | USD 10 | 초과 예상 시 승인 화면에서 차단 |
| 하루 전체 유료 생성 | USD 15 | 추가 승인 없이는 실행하지 않음 |
| 월 전체 유료 생성 | USD 100 | 추가 승인 없이는 실행하지 않음 |

위 금액은 공급자 가격 주장이나 실제 청구 예측이 아니라 초기 운영 안전 한도다. 첫 10편의
실측 비용·채택률을 본 뒤 재검토한다. 통화와 한도는 personal 설정에서 변경 가능해야 한다.

## 3. 현재 코드 적용성 검증

| 결정 | 적용성·구조 변경 | 영향 파일 | 기존 기능 영향 | 난이도 | 배포·보안 위험 / 추가 결정 |
|---|---|---|---|---|---|
| Windows 11 x64만 지원 | 부분 가능; 진단·문서에서 범위 고정 | `portable/*`, README | 기존 AMD64 경로 유지 | 낮음 | 실제 Windows 11 VM 확인 |
| portable ZIP | 부분 가능; 폴더 build 뒤 ZIP manifest | `portable/build.py`, packaging | installer는 유지하되 초기 릴리스에 미사용 | 중간 | 서명·전달 채널 미정 |
| 자동 업데이트 없음 | 바로 가능; 기능 추가 안 함 | 없음 | 영향 없음 | 낮음 | 수동 업데이트 안내 필요 |
| 팀 기존 portable 기능 유지 | 가능; 계약 테스트 선행 | `service.py`, `web/*` | 현 route·JSON·UI 보존 | 중간 | E2E 기준 시나리오 필요 |
| 팀 artifact에서 local 제외 | 현재 불가; registry·route·정적 UI·allowlist 분리 | `llm/*`, `web/*`, `portable/*` | team만 local 선택 불가, personal은 보존 | 높음 | source 누출·import 파손 |
| 현 Anthropic·Typecast 설정 유지 | 가능; 저장·검사 동작 유지 | `config.py`, `web/app.py` | 사용자 입력 흐름 동일 | 낮음 | 평문 key 권한·backup 검사 |
| Documents에 artifact·로그 | 현재 불일치; Known Folder `PathPolicy` | `config.py`, launcher | 기존 TTS/log 위치 migration 필요 | 중간 | temp 위치·보존 기간 미정 |
| CapCut 자동 탐색 후 직접 지정 | 부분 가능; override 설정·UI 연결 | `capcut_export.py`, `web/*` | 자동 탐색 우선순위 유지 | 중간 | 지원 CapCut 버전 미정 |
| 일반 오류와 상세 로그 분리 | 현재 미흡; 오류 코드·redaction 정책 | `web/app.py`, adapters | 사용자 메시지는 단순화 | 중간 | prompt·경로 개인정보와 log 보존 |
| personal 별도 엔트리포인트 | 현재 없음; app composition root | 신규 `apps/*` | legacy entrypoint 유지 | 중간 | 잘못된 registry 조합 방지 |
| personal UI만 별도 포함 | 현재 불가; 정적 module·allowlist | `web/static/*` | 공통 화면 동작 보존 | 높음 | 팀 ZIP에 personal asset 누출 |
| Qwen 읽기 전용 최종 검수 | 현재 없음; `ReviewReport`, mutation port 미주입 | personal 신규 모듈 | 기존 생성물 수정 없음 | 중간 | model ID·vision capability 미정 |
| 생성 전 유료 승인 | 현재 없음; quote·approval·job 상태 | personal 신규 모듈 | 기존 추천 흐름은 유지 | 높음 | 중복 과금·승인 replay 방지 |
| 비용·사용량 기록 | 현재 없음; `UsageRecord` 영속 저장 | personal 신규 모듈 | 팀에는 영향 없음 | 높음 | 가격표 유효일·환율 정책 필요 |
| team/personal 데이터 분리 | 현재 없음; product별 app ID·root | `config.py`, launcher | legacy 데이터 자동 이동 금지 | 중간 | migration·backup 정책 필요 |
| 영구 선택 기록 | 현재 없음; export·delete 포함 저장소 | personal 신규 모듈 | 기존 결과물에 영향 없음 | 중간 | 보존은 확정, 삭제·backup은 미정 |

## 4. 확정 제품 구성

### 4.1 공통 코어

- 채널·프로필·대본·가이드·컷 명세 도메인 계약
- 채널 학습과 대본·가이드 생성 유스케이스의 공급자 독립 부분
- LLM, TTS, 검색, 저장소, CapCut exporter, asset provider 포트
- 기존 JSON 직렬화 호환
- 비용·사용량 데이터 형식. 개인판만 실제로 기록한다.
- 결정론적 오류 분류와 진행 상태

공통 코어에는 FastAPI route, 로컬 모델 서버 주소, 공급자 SDK, Windows 경로, CapCut
직접 파일 쓰기, personal 작업 그래프를 두지 않는다.

### 4.2 팀판 전용

- Windows 11 x64 portable launcher와 ZIP packaging
- localhost FastAPI API와 팀용 정적 UI
- Anthropic·Typecast 등 승인된 API adapter 등록
- Windows Known Folder, CapCut 탐색·직접 지정, 사용자용 진단
- 팀 설정 profile과 로컬 상세 로그

### 4.3 개인판 전용

- local OpenAI-compatible adapter와 모델 탐색 UI
- SFX·CLAP·Demucs
- `CutManifest`, asset 분류·생성·검수·승인
- 이미지·영상 provider adapter와 후처리
- 비용 원장, 영속 job, 선택 기록
- 작업 그래프와 읽기 전용 Qwen 최종 검수

## 5. 엔트리포인트와 설정 스키마

권장 composition root:

```text
shortly.apps.team.api:create_app
shortly.apps.team.launcher:main
shortly.apps.personal.api:create_app
shortly.apps.personal.launcher:main
```

전환 기간에는 현 `shortly.web.app:app`, `python -m shortly web`,
`shortly.portable.launcher`가 기존 personal 또는 legacy composition을 호출한다.

공통 설정:

```text
schema_version, product, data_root, artifact_root, log_root
host, port, research, factcheck
provider role별 model ref, timeout
CapCut locator와 template name
```

팀 설정:

```text
product=team
allowed_providers, API credential slot, Typecast
Windows Known Folder, portable bundle version
local provider와 personal feature 설정은 스키마에서 거절
```

개인 설정:

```text
product=personal
local server와 role routing
asset provider, budget, retries, approval policy
job DB, selection history, personal CapCut
```

초기 schema의 현재 환경변수 매핑:

| 설정 영역 | 팀판 | 개인판 | 검증 정책 |
|---|---|---|---|
| product | launcher가 `team` 고정 | launcher가 `personal` 고정 | 사용자 UI에서 변경 불가 |
| host | `127.0.0.1` 고정 | 기본 localhost, 현 개발 옵션 호환 | 팀판 외부 bind 거절 |
| 분석·생성 provider | Anthropic 현 portable 값 | 기존 Gemini/OpenAI/Anthropic/local | 등록되지 않은 provider 거절 |
| 분석·생성 model | 현 portable 상수 | 기존 role별 model | 빈 값·provider 불일치 검사 |
| API key | `ANTHROPIC_API_KEY` | 기존 공급자별 key | 값은 응답·로그에서 비공개 |
| Typecast | 기존 `TYPECAST_*` | 기존 `TYPECAST_*` | 선택 기능, 내보내기 전 검사 |
| data root | 현 `%LOCALAPPDATA%\Shortly\data` 호환 | legacy repo data override 허용 | team/personal 기본 root 분리 |
| artifact·log root | Windows Documents의 `Shortly` | Documents의 `Shortly Personal` | 쓰기 권한과 여유 공간 검사 |
| CapCut | template + 신규 root override | 같은 계약 | 자동 탐색 실패 시 직접 지정 |
| local server | 스키마에 없음 | 기존 URL·key·no-think | team 입력은 저장하지 않음 |
| SFX | 스키마에 없음 | 기존 SFX 경로 | team 입력은 저장하지 않음 |
| 생성 asset·예산 | 스키마에 없음 | provider·USD 한도·retry·approval | team 입력은 거절 |

초기 키 저장은 사용자 결정에 따라 현 `.env` 방식을 유지한다. 설정 API는 값을 반환하지
않고 설정 여부만 반환해야 한다. OS credential store 전환은 후속 보안 결정이다.

## 6. 의존성·빌드·데이터·보안 경계

### 팀판 포함 후보

- FastAPI, Uvicorn, Anthropic, Typecast의 표준 HTTP 경로
- yt-dlp, FFmpeg, OpenCV, PySceneDetect
- OCR·ASR은 기존 기능 보존을 위해 초기에는 포함하되 실제 팀 사용 측정 후
  `team-full-analysis` 선택 bundle로 분리할 수 있다.

### 팀판 제외

- OpenAI-compatible local adapter와 local model 탐색
- torch·torchaudio·transformers·demucs·CLAP 모델을 요구하는 personal SFX 경로
- 이미지·영상 생성 SDK, personal job DB·검수 UI
- 개인 브랜드 템플릿과 개인 절대 경로

### 개인판

개발자 관리 로컬 설치를 우선한다. 고급 의존성과 모델은 personal extra 또는 별도 설치
manifest로 관리한다. 초기에는 portable 배포를 목표로 하지 않는다.

### 데이터 root

```text
팀 내부 상태:    %LOCALAPPDATA%\Shortly\
팀 사용자 파일:  <Windows Documents>\Shortly\
개인 내부 상태:  %LOCALAPPDATA%\ShortlyPersonal\
개인 사용자 파일:<Windows Documents>\Shortly Personal\
```

정확한 하위 폴더와 기존 데이터 migration은 구현 전에 확정한다. 두 제품은 서로의
기본 root를 자동 탐색하거나 쓰지 않는다.

### 기능 플래그 허용 범위

기능 플래그는 같은 artifact 안에서 안전하게 켜고 끌 수 있는 조사, 팩트체크, OCR 같은
동작 정책에만 사용한다. local provider, SFX, 생성 에셋처럼 코드·의존성·비밀 권한이 다른
기능은 플래그만으로 숨기지 않고 build manifest에서 제외한다.

## 7. Git과 릴리스 운영

**확정**

- `main`은 공통 통합 브랜치다.
- team/personal 장기 브랜치는 만들지 않는다.
- 모든 변경은 짧은 작업 브랜치와 PR을 거친다.
- 앱 코드 PR은 최소 계약 테스트 통과 전 병합하지 않는다.
- team과 personal 버전은 독립적으로 관리한다.

권장 브랜치:

```text
docs/<topic>
feat/core-<topic>
feat/team-<topic>
feat/personal-<topic>
fix/<topic>
refactor/core-<topic>
release/team-<version>       # 안정화가 필요할 때만 단기 사용
release/personal-<version>   # 안정화가 필요할 때만 단기 사용
```

권장 태그:

```text
team-vMAJOR.MINOR.PATCH
personal-vMAJOR.MINOR.PATCH
baseline/pre-split-YYYY-MM-DD
```

PR은 최소 한 명 검토를 권장하되 현재 개발자가 한 명이면 문서·CI 검증 후 self-merge를
허용할 수 있다. branch protection과 필수 CI는 테스트 기반이 생기는 첫 구현 PR 이후
활성화한다. 자동 병합은 사용하지 않는다.

## 8. 충돌 및 수정이 필요한 결정

1. **현재 방식 유지와 문서 폴더 저장은 동시에 사실일 수 없다.** 초기 위치를 바꾸려면
   migration·fallback 정책이 필요하다.
2. **로컬 UI 숨김만으로 빌드 제외가 되지 않는다.** route, adapter, requirements,
   source allowlist를 함께 분리해야 한다.
3. **상세 로그와 보안은 긴장 관계가 있다.** 프롬프트·대본·경로는 허용하되 API 키
   문자를 부분 마스킹해 남기는 방식은 금지하고 provider+fingerprint로 대체한다.
4. **CapCut 내보내기 중 생성은 직접 파일 쓰기와 분리해야 한다.** 준비와 commit 두
   단계가 필요하다.
5. **선택 기록 영구 보존은 삭제·백업·export 정책 없이는 위험하다.** 개인판 전용
   저장소와 명시적 삭제 기능이 필요하다.
6. **Gemini Omni Flash는 현재 preview다.** 평가 adapter는 확정하지만 production
   고정은 품질·가격·rate limit 실측 뒤 결정한다.

## 9. 아직 미정인 결정

- portable ZIP 코드 서명과 배포 채널
- Windows 11 ARM64 지원 여부
- 임시 파일을 Documents에 둘지 LocalAppData에 둘지
- 로그 보존 기간과 최대 크기
- API 키의 OS credential store 이전 시점
- 지원 CapCut 버전과 테스트 template
- 팀 OCR·ASR를 계속 기본 포함할지 선택 bundle로 만들지
- 프로젝트·가이드 공유 방식
- 생성 model alias/snapshot 고정과 가격표 유효일
- personal 데이터 backup, export, 삭제 범위
- PR 필수 승인자 수와 GitHub branch protection 세부 규칙

관련 ADR:

- [`adr/001-product-structure.md`](adr/001-product-structure.md)
- [`adr/002-team-distribution.md`](adr/002-team-distribution.md)
- [`adr/003-personal-automation.md`](adr/003-personal-automation.md)
- [`adr/004-git-and-release.md`](adr/004-git-and-release.md)

공식 확인 자료:

- [OpenAI GPT Image 2 모델 문서](https://developers.openai.com/api/docs/models/gpt-image-2)
- [Google Gemini Omni Flash 문서](https://ai.google.dev/gemini-api/docs/omni)
- [Google Gemini API 가격 문서](https://ai.google.dev/gemini-api/docs/pricing)
