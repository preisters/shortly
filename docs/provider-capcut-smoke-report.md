# 실제 공급자·CapCut smoke 보고서

실행일: 2026-07-28

대상 브랜치: `agent/provider-capcut-smoke`

## 범위와 안전 조건

- 로컬 `.env`의 키 값은 출력하거나 문서·DB·Git에 저장하지 않았다.
- 세 키는 비어 있지 않은지만 먼저 검사했다.
- OpenAI 이미지 1건, Google 영상 1건, Anthropic 최소 텍스트 1건만 호출했다.
- 자동 재시도와 유료 provider fallback은 사용하지 않았다.
- 기존 CapCut 프로젝트는 변경하지 않고 새 smoke 프로젝트만 생성했다.
- 비용 원장의 값은 실제 청구액이 아니라 사전 승인한 보수적 예상액이다.

## 결과

| 대상 | 결과 | 확인 내용 |
|---|---|---|
| OpenAI `gpt-image-2` | 성공 | PNG 원본, 1080×1920 JPEG 준비본, SQLite job·승인·비용 기록 |
| Google `gemini-omni-flash-preview` | 성공 | H.264 MP4, 720×1280, 24fps, 약 10초, 약 2.79MB |
| Anthropic `claude-opus-4-8` | 성공 | 최소 2자 텍스트 health check |
| CapCut prepare/commit | 성공 | 이미지와 영상 각 1개 삽입, 새 프로젝트 root 등록 |
| CapCut 정적 사후 검증 | 성공 | 9:16 canvas, 10초 timeline, 모든 material reference 해석, 두 asset 파일 존재 |

생성물 위치:

```text
%USERPROFILE%\Documents\Shortly Personal\generated-assets\
```

CapCut smoke 프로젝트:

```text
Shortly Provider Smoke 20260728
```

## smoke에서 반영한 보강

1. 실제 검증한 Google Gen AI SDK 계열을 사용하도록 개인 요구사항을
   `google-genai>=2.14.0,<3.0.0`으로 제한했다.
2. SDK에 `interactions.create`가 없을 때 명확한
   `ProviderUnavailableError`를 반환한다.
3. CapCut commit 직후 다음 항목을 자동 검사한다.
   - `draft_content.json` 파싱
   - 9:16 canvas와 양수 duration
   - track의 모든 `material_id` 해석
   - 삽입된 `cut_*` 파일 존재
   - 선택 asset 수와 실제 삽입 수 일치
4. 사후 검증 실패 시 프로젝트 생성 사실을 숨기지 않고 API 결과를 실패로 바꾸며
   validation 오류를 함께 반환한다.

## 남은 수동 확인

정적 구조와 파일은 검증됐지만 CapCut 렌더러 동작은 애플리케이션 내부에서 관찰할 수
없다. 사용자가 CapCut에서 `Shortly Provider Smoke 20260728`을 열어 다음을 확인해야
한다.

- 첫 5초에 생성 이미지가 표시되는지
- 다음 5초에 생성 영상이 재생되는지
- 이미지 crop과 영상 비율에 왜곡이 없는지
- 자막과 제목 track이 정상 표시되는지
- 저장 후 다시 열어도 asset 연결이 유지되는지

실제 청구액은 각 provider dashboard에서 확인해야 한다. 원장의 0.05달러와 1달러는
승인 시 사용한 예상액이며 공급자가 반환한 확정 청구액이 아니다.
