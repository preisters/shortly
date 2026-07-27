# ADR 002: Windows 11 x64 팀 portable 배포

- 상태: 부분 승인
- 날짜: 2026-07-27

## 결정 내용

초기 팀판은 Windows 11 x64용 portable ZIP으로 배포한다. 자동 업데이트, macOS,
local LLM, personal SFX·생성 기능은 포함하지 않는다. 현재 portable의 핵심 제작 흐름과
API 키·Typecast 설정 방식을 유지한다.

## 배경

현재 빌드는 Windows AMD64 embedded Python, Windows FFmpeg, Inno Setup, VBS/BAT,
Windows CapCut 경로와 글꼴에 의존한다. 사용자는 4명이고 대부분 비개발자이며 주 작업은
주제 기반 대본 생성이다.

## 검토한 대안

- Inno Setup 설치 파일
- portable ZIP
- 웹 SaaS
- Windows와 macOS 동시 지원

## 선택 이유

ZIP은 초기 사용자 수와 업데이트 요구에 비해 운영 부담이 작고, 현재 폴더형 portable
산출물을 활용할 수 있다. macOS 동시 지원은 CapCut과 패키징을 별도로 구현해야 한다.

## 장점

- 설치 권한이 제한된 PC에서도 배포하기 쉽다.
- 자동 업데이트 서버와 installer 서명 의존성을 미룰 수 있다.
- 파일 단위 artifact 감사를 수행하기 쉽다.

## 단점

- 다운로드·압축 해제와 바로가기 안내를 수동 제공해야 한다.
- 코드 서명이 없으면 SmartScreen 경고 가능성이 있다.
- 업데이트와 이전 버전 정리는 사용자가 수행한다.

## 기술적 영향

- build는 팀 source·dependency allowlist를 가져야 한다.
- 실행·설정·cache는 `%LOCALAPPDATA%\Shortly`, 사용자 artifact와 로그는 Windows
  Documents의 `Shortly`를 사용한다는 방향을 채택한다.
- local model route와 personal 정적 asset도 artifact에서 제외한다.
- 현재 평문 사용자 `.env`는 초기 호환 방식으로 유지하지만 key 값을 로그·API 응답에
  노출하지 않는다.

## 미확정 사항

- 코드 서명과 배포 채널
- 임시 파일의 Documents 대 LocalAppData 최종 위치
- 로그 보존 기간
- 지원 CapCut 버전
- OCR·ASR 기본 bundle 유지 여부

## 재검토 조건

- 사용자 수가 늘거나 빈번한 보안 업데이트가 필요할 때
- SmartScreen·회사 보안 정책으로 ZIP 실행이 어려울 때
- macOS 사용자가 실제 필요해질 때
- 수동 업데이트 오류가 반복될 때
