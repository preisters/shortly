# ADR 003: 승인 기반 개인 생성 에셋 작업 그래프

- 상태: 부분 승인
- 날짜: 2026-07-27

## 결정 내용

개인판은 별도 personal 엔트리포인트와 데이터 root를 사용한다. 컷 명세, 유료 생성 승인,
영속 job·비용·선택 기록, PrepareExport/CommitExport, 읽기 전용 Qwen 최종 검수를
결정론적 작업 그래프로 구성한다.

## 배경

현재 코드는 추천 문자열과 자리표시자 이미지만 만들며 asset provider, manifest, 영속
queue, 비용 원장, 승인 UI가 없다. 웹 job은 메모리에만 있고 CapCut writer는 TTS와
template 복제까지 한 모듈에 결합돼 있다.

## 검토한 대안

- `service.py`에 생성 API를 직접 추가
- 자유 행동형 멀티 에이전트
- `guide.json`에 provider 상태를 계속 추가
- versioned manifest와 결정론적 DAG

## 선택 이유

manifest와 상태 머신은 중복 과금·재시작·승인·수동 교체를 추적할 수 있다. 결정론적
DAG는 LLM이 파일 쓰기와 비용 지출 순서를 자율적으로 바꾸지 못하게 한다.

## 장점

- 유료 요청 전에 예상 비용과 범위를 검토할 수 있다.
- 공급자 장애 시 자동 유료 폴백을 막는다.
- 생성 실패가 CapCut 프로젝트를 부분 변경하지 않는다.
- 선택 기록을 향후 추천 개선에 사용할 수 있다.

## 단점

- manifest와 영속 상태 설계가 선행돼 초기 개발량이 늘어난다.
- 영구 기록은 backup·삭제·개인정보 정책이 필요하다.
- 검수 모델 결과가 품질을 보장하지 않으므로 사람 확인이 계속 필요하다.

## 기술적 영향

- `CutManifest`, `UsageRecord`, `Approval`, `ReviewReport` 계약이 필요하다.
- 유료 node에는 approval token이 없으면 provider port를 호출할 수 없다.
- 검수 node에는 읽기 port만 주입하고 mutation·CapCut writer를 주입하지 않는다.
- 사실 컷은 초기에는 `MANUAL_REQUIRED`다.
- 후보 1개와 자동 재생성 1회로 최대 두 번의 유료 시도를 기록한다.
- 자동 재시도는 rate limit·timeout·일시 장애·download 무결성 실패에만 허용하고,
  인증·quota·moderation·예산 초과는 사람에게 반환한다.
- 첫 이미지 adapter는 `gpt-image-2`, 첫 영상 평가 adapter는
  `gemini-omni-flash-preview`다. 영상 모델은 preview이므로 personal pilot에 한정한다.

## 미확정 사항

- model snapshot·가격표 유효일
- preview 영상 모델의 production 채택 여부
- 로컬 Qwen model ID와 vision capability
- 선택 기록 backup·export·삭제 방식

## 재검토 조건

- 첫 10개 영상의 비용·채택률·실패율을 확보했을 때
- 후보 1개로 검수 품질이 부족할 때
- 영상 생성 시간이 편집 흐름을 과도하게 지연할 때
- Qwen 검수와 사람 판단의 일치율이 낮을 때
