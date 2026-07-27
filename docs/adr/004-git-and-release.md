# ADR 004: trunk 기반 Git과 제품별 독립 버전

- 상태: 승인
- 날짜: 2026-07-27

## 결정 내용

`main`을 공통 통합 브랜치로 사용하고 모든 변경은 짧은 작업 브랜치와 PR을 거친다.
team과 personal 장기 브랜치는 만들지 않는다. 두 제품은 같은 commit에서도 독립 버전과
태그를 가질 수 있다.

## 배경

제품별 브랜치가 오래 유지되면 공통 코어의 수정과 보안 패치를 양쪽에 반복 적용해야
한다. 반면 팀과 personal은 배포 주기와 포함 기능이 다르다.

## 검토한 대안

- `team`·`personal` 장기 브랜치
- 제품별 별도 저장소
- 모든 제품에 하나의 버전
- trunk와 제품별 tag

## 선택 이유

trunk는 공통 코어의 단일 진실 공급원을 유지하고, 제품별 tag는 배포 주기의 독립성을
제공한다.

## 장점

- merge drift가 줄어든다.
- 모든 제품 변경이 같은 계약 테스트를 거친다.
- 특정 commit에서 team과 personal artifact를 각각 재현할 수 있다.

## 단점

- build profile과 version metadata가 제품별로 필요하다.
- PR 검증이 어떤 제품에 영향을 주는지 명시해야 한다.

## 기술적 영향

- `docs/*`, `feat/core-*`, `feat/team-*`, `feat/personal-*`, `fix/*`,
  `refactor/core-*` 규칙을 사용한다.
- tag는 `team-vX.Y.Z`, `personal-vX.Y.Z`를 사용한다.
- 앱 코드 병합에는 계약 테스트를 필수로 한다.
- release branch는 안정화가 필요할 때만 짧게 사용한다.

## 재검토 조건

- 서로 다른 릴리스의 hotfix가 장기간 병행될 때
- 제품별 권한·감사 요구가 달라 단일 저장소가 부적절해질 때
- 독립 package publication이 운영상 필수가 될 때
