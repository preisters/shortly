# ADR 001: 단일 저장소와 모듈형 공통 코어

- 상태: 승인
- 날짜: 2026-07-27

## 결정 내용

팀판과 개인판은 하나의 `main`과 공통 코어를 사용한다. 먼저 현재 `shortly` 패키지
안에서 core, adapter, integration, app composition 경계를 만들고, 여러 installable
package는 후속 필요가 입증될 때만 도입한다.

## 배경

현재 CLI와 FastAPI는 `service.py`를 공유한다. `service.py`는 분석·SFX·생성·CapCut을
eager import하며 portable builder는 `shortly/` 전체를 복사한다. 즉시 저장소나 package를
나누면 import와 기존 JSON·CapCut 동작의 회귀 범위가 커진다.

## 검토한 대안

- 팀·개인 별도 저장소
- 장기 제품 브랜치
- 즉시 여러 Python package/workspace
- 현 구조와 기능 플래그만 유지

## 선택 이유

기존 동작을 facade로 유지하면서 경계를 점진적으로 검증할 수 있고, 공통 변경을 한 번만
수정할 수 있다. 기능 플래그만으로는 개인 source와 의존성을 팀 artifact에서 제거할 수
없으므로 build allowlist를 함께 사용한다.

## 장점

- 코어 버그 수정과 보안 패치가 한 곳에 있다.
- 테스트 기준선을 만든 뒤 작은 PR로 전환할 수 있다.
- 향후 물리적 package 분리 경로를 막지 않는다.

## 단점

- 전환 기간에는 한 source tree에 legacy와 새 경계가 공존한다.
- 빌드 allowlist가 완성되기 전에는 팀 artifact의 물리적 분리가 불완전하다.

## 기술적 영향

- `service.py`, `cli.py`, 기존 JSON은 호환 facade로 유지한다.
- composition root에서 provider와 feature를 등록한다.
- core는 SDK·Windows 경로·FastAPI를 import하지 않는다.

## 재검토 조건

- 팀 artifact가 personal source를 allowlist만으로 안정적으로 제외하지 못할 때
- 공통 코어 릴리스 주기와 제품 릴리스 주기가 운영상 충돌할 때
- 독립 설치·의존성 해석이 반복적으로 깨질 때
