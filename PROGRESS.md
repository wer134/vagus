# 연구 진행 기록

## 2026-11-04

- 사용자 지정 `AGENTS.md` 작성. 기존 구조 가이드는 `docs/PROJECT_GUIDE.md`로 보존.
- Git 로컬 작성자 `wer134 <87606833+wer134@users.noreply.github.com>`, hooksPath `.githooks` 설정.
- 제공된 후속 연구의 전체 원본은 현재 checkout 및 연결된 브랜치에서 찾지 못함. 새 구현 출처를 명시하기로 결정.
- 구현·평가 계획 및 후보 비교군 고정. 원본 시뮬레이터/모델/역사 결과는 유지.
- 시뮬레이터, 제어기, 평가기, 보고서/도판 구현 완료.
- 최초 고정 평가 전에 안전한 detour만 남은 상태도 완료로 인정하던 평가기 문제를 회귀 테스트와 함께 수정.
- v1 고정 평가 완료 후 타입 표기만 수정해 v2 재실행. 정책·시나리오·임계값 변경 없이 원시 결과와 집계가 완전히 동일함을 확인.
- dev 216회, test 1,080회 평가. JSON/manifest/한국어 보고서/SVG/PNG 생성 및 도판 시각 확인 완료.
- 통합 selftest 17개 통과, 실패 0, 생략 2. Ruff 통과. basedpyright 오류 0, 경고 118은 별도 공개.
- 독립 방법론 검토에서 blocking correctness 결함 없음. 결과·한계·부정 결과를 FINDINGS.md에 기록.
- 사용자 규칙에 따라 feat/recovery-research 브랜치 사용. 커밋·push·히스토리 재작성은 수행하지 않음.

## 후속 배포 요청

- 사용자가 지정 작성자·메시지 규칙에 따른 커밋과 현재 브랜치 push를 승인했다.
- 작업 규칙, 구현, 결과·문서를 분리해 커밋하고 각 커밋의 author/committer와 메시지를 확인한다.
- 동결 소스 해시의 checkout 재현성을 위해 연구 파일의 LF 줄바꿈을 `.gitattributes`로 고정한다.
