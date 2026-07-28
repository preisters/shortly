# 구현 로드맵

작성일: 2026-07-27
기준 문서: [`product-decisions.md`](product-decisions.md),
[`feature-matrix.md`](feature-matrix.md),
[`team-personal-split-plan.md`](team-personal-split-plan.md)

> 구현 상태(2026-07-27): 기준선 테스트, 제품 profile/path policy, composition
> root, local provider 경계, 개인 `CutManifest`, 유료 생성 gate, SQLite 원장,
> 이미지·영상 adapter, 후처리, 읽기 전용 Qwen 검수, 결정론적 workflow,
> CapCut prepare/commit 기준선까지 구현했다. 상세 결과와 남은 수동 검증은
> [`personal-automation-implementation-report.md`](personal-automation-implementation-report.md)를
> 기준으로 한다. 포터블 완성도 개선은 사용자 결정에 따라 후순위다.
> 실제 OpenAI·Google·Anthropic 및 CapCut 정적 smoke 결과는
> [`provider-capcut-smoke-report.md`](provider-capcut-smoke-report.md)를 참조한다.

## 1. 구현 원칙

- `main`의 현 동작을 먼저 테스트로 고정한다.
- 기존 public 함수, CLI, route와 JSON을 호환 facade 뒤에 유지한다.
- 한 PR에서 테스트 기준선과 구조 변경을 함께 하지 않는다.
- 팀 빌드 제외는 UI 숨김이 아니라 import·source·dependency allowlist로 검증한다.
- CapCut exporter는 익명 fixture 없이 리팩터링하지 않는다.
- 외부 API를 쓰는 자동 테스트는 기본 test suite에 넣지 않는다.

## 2. 다음 구현 단계: PR 1A 기준선 계약 테스트

다음에 수행할 하나의 제한된 작업은 **제품 코드 변경 없는 무네트워크 계약 테스트
추가**다.

### 목표

- 현재 CLI, 설정, 휴리스틱 생성, 중립 draft와 웹 route 계약을 재현한다.
- 이후 team/personal composition root 작업의 회귀 기준선을 만든다.
- `c168bbb`의 잠정 기준선을 실행 가능한 테스트로 보강한다.

### 정확한 예상 파일

```text
tests/__init__.py
tests/support.py
tests/fixtures/profile.min.json
tests/fixtures/script.min.json
tests/fixtures/guide.min.json
tests/fixtures/capcut_draft.expected.json
tests/test_cli_smoke.py
tests/test_settings_contract.py
tests/test_llm_factory_contract.py
tests/test_generation_contract.py
tests/test_capcut_draft_contract.py
tests/test_web_contract.py
tests/test_repository_hygiene.py
```

현재 `data/**`를 복사하지 않고 fixture를 새로 작성한다. 실제 CapCut fixture는 별도 승인과
익명화 후 후속 PR에 추가한다.

### 생성할 인터페이스

없다. 이 PR은 현재 함수와 dict 계약만 관찰한다.

### 변경하지 않을 파일

```text
shortly/**
run_shortly.py
pyproject.toml
requirements.txt
.env.example
README.md
assets/**
.env
data/**
dist/**
```

### 변경하지 않을 동작

- CLI 8개 명령과 인자
- FastAPI route와 응답 필드
- `.env`·`env.defaults`·portable 사용자 `.env` 우선순위
- 기존 채널·output 경로와 JSON 필드
- LLM 미사용 시 휴리스틱 폴백
- `shortly_schema: capcut-draft-v0`
- CapCut 템플릿 복제, ID, TTS 타이밍, root 등록
- 현재 사용자 데이터와 cache

같은 제목의 신규 가이드 폴더 재사용과 무효인 `separate_vocals`는 관찰 결과로 기록하되
영구 제품 계약으로 고정하지 않는다.

### 필요한 테스트

1. 추적 Python 파일 AST parse와 핵심 import
2. CLI help와 8개 subcommand
3. dev·portable 설정 우선순위와 key 값 비노출
4. LLM factory role별 provider·unavailable
5. 외부 모델 없는 script 필수 필드
6. guide와 컷 추천 필드·개수
7. `capcut-draft-v0` golden
8. FastAPI route 24개와 download path 제한
9. Typecast payload와 cache key 결정성
10. 기존 JSON 읽기와 guide path traversal 방어
11. Git ignore·tracked personal path guard

### 완료 조건

- 새 패키지 설치 없이 표준 `unittest`로 실행된다.
- 네트워크와 사용자 `.env` 값 없이 성공한다.
- 임시 산출물은 test 임시 디렉터리 밖에 남지 않는다.
- 실제 key, 대본, 영상, CapCut 프로젝트가 fixture에 없다.
- 제품 코드 diff가 없다.

### 롤백

이 PR에서 추가된 `tests/**`만 되돌리면 된다. 사용자 데이터와 앱 동작에는 영향이 없어야
한다.

### 후속 단계와의 관계

PR 1A가 통과해야 product profile과 엔트리포인트 작업을 시작한다. branch protection의
필수 test도 이 suite부터 등록한다.

## 3. PR 1B 제품 profile과 경로 계약

PR 1A 다음의 제한된 구조 작업이다. 비밀 저장 방식과 provider 구현은 바꾸지 않는다.

예상 파일:

```text
# 새 파일
shortly/product_profile.py
shortly/path_policy.py
tests/test_product_profile.py
tests/test_path_policy.py

# 기존 파일 수정
shortly/config.py
shortly/portable/launcher.py
.env.example
README.md
tests/test_settings_contract.py
```

생성 인터페이스:

```text
ProductKind(TEAM, PERSONAL, LEGACY)
FeatureManifest
PathPolicy
```

완료 조건:

- legacy 실행 경로와 설정 우선순위가 동일하다.
- team과 personal 기본 root가 다르다.
- Windows Documents는 Known Folder 결과를 사용한다.
- 아직 route나 provider를 제거하지 않는다.
- team profile에서 personal setting을 읽더라도 활성화하지 않는다.

롤백은 새 profile이 없을 때 기존 `Settings.load`로 가는 호환 경로다.

## 4. 후속 PR 순서

### PR 2: composition root

- `shortly/apps/team`과 `shortly/apps/personal` 추가
- 기존 `web.app`은 legacy facade 유지
- 동일 core service를 주입받는 app factory 도입
- route manifest golden 작성

구현 상태(2026-07-27): 제품별 앱 팩터리·런처와 요청 단위 서비스 주입 경계를
구현했다. personal은 legacy 24개 route를 유지하고, team은 다음 단계에서 금지된
route를 제외할 수 있는 별도 앱 객체를 사용한다. 기존 `python -m shortly web`은
호환 경로로 유지한다.

### PR 3: local provider 경계

- local OpenAI-compatible adapter와 model discovery를 personal registry로 이동
- 팀 API와 정적 UI에서 local 기능을 제거
- 팀 source allowlist 테스트
- OpenAI 상용 adapter와 local adapter의 공통 부분만 transport 수준에서 공유

구현 상태(2026-07-27): team registry는 Anthropic만 등록하고 personal/legacy
registry는 기존 상용 provider와 local 별칭을 유지하도록 분리했다. team 앱은
`/api/local-models`를 등록하지 않고 local 설정 HTML을 렌더링하지 않는다. 포터블
빌드는 personal 앱, Gemini/OpenAI adapter와 공통 OpenAI transport를 제외하며,
생성된 HTML/JavaScript에서도 local 탐색 UI를 제거한다. 산출물 import smoke와
source allowlist 검사를 회귀 테스트에 포함했다.

### PR 4: SFX 경계

- fingerprint와 Demucs+CLAP 중 정식 경로 결정
- personal 전용 지연 import와 dependency group
- 팀 import smoke에서 torch·demucs 없이 시작되는지 확인
- `separate_vocals`는 결정에 따라 제거하거나 다시 구현

### PR 5: Windows 11 portable ZIP

- 재현 가능한 dependency manifest와 ZIP file manifest
- 사용자 artifact/log 경로와 migration 안내
- 깨끗한 Windows 11 x64 VM smoke
- 자동 업데이트는 넣지 않음

### PR 6: CapCut 안전 경계

- 익명화한 지원 버전 fixture 확보
- 중립 draft와 Windows template writer 분리
- 자동 탐색 실패 시 직접 지정
- 임시 폴더 작성, 검증 후 commit, 원본 불변 테스트

### PR 7: personal CutManifest

- 기존 guide를 versioned `CutManifest`로 변환
- 규칙 기반 `AssetType`
- 컷별 생성 체크와 수동 asset 연결
- 외부 생성 API는 아직 호출하지 않음

### PR 8: personal 유료 생성

- 이미지 provider 하나와 fake 계약
- quote·approval·budget·usage·영속 job
- 후보 1개, 자동 재생성 1회
- 사실 컷은 `MANUAL_REQUIRED`

### PR 9: PrepareExport/CommitExport와 최종 검수

- 승인 asset 후에만 CapCut에 삽입
- Qwen `ReviewReport` 읽기 전용 단계
- mutation port를 검수 노드에 주입하지 않음
- 실패·중단·재시작 E2E

## 5. 릴리스 gate

팀 첫 ZIP:

- Windows 11 x64 깨끗한 VM에서 압축 해제·첫 실행·종료·재실행
- 주제 입력에서 대본·가이드 생성까지 1건
- 허용된 provider key 저장·오류 메시지
- local/SFX/personal source와 dependency 부재
- 사용자 데이터가 bundle 폴더에 기록되지 않음
- CapCut은 지원 버전 fixture와 실제 PC에서 각각 확인

personal 첫 생성 확장:

- 모든 유료 요청 전에 quote와 승인
- 중복 요청·재시작 시 중복 과금 방지
- 예산 초과 차단
- 실패 시 `MANUAL_REQUIRED`
- 선택 기록과 사용량 원장 일치
- 검수 모델이 파일·manifest·CapCut을 수정할 수 없음
