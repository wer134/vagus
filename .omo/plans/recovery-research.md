# recovery-research - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 한국어 작업 규칙, 복구 완료·원상복귀 제어 연구 코드, 고정된 짝지은 평가, 재현 명령, 실제 결과 보고서와 도판.

**Why this approach:** 관측과 실제 상태를 분리하고 보정·예측·대기 조건을 대조해 효과의 원인을 구분한다. 원본 미확보 부분은 새 구현으로 명시한다.

**What it will NOT do:** 기존 모델·실험 결과를 덮어쓰거나 논문 수치 재현·표준 전체 준수·새로움이 입증됐다고 주장하지 않는다.

**Effort:** Large
**Risk:** Medium - 관측 누출, 정책별 난수 소비 차이, 종료 기준의 혼동을 회귀 테스트로 차단한다.
**Decisions to sanity-check:** 독립 합성 시뮬레이션, 별도 완료/원상복귀 지표, 다중 seed, 모든 성패 공개.

사용자의 이번 요청으로 계획과 구현 실행이 승인되었다. 추가 승인 대기 없이 구현·검증한다.

---

> TL;DR (machine): approved-execution; research harness and truthful paired evaluation; no push/history rewrite.

## Scope
### Must have
- 사용자 지정 AGENTS.md 및 로컬 Git 설정. 기존 구조 가이드는 docs/PROJECT_GUIDE.md로 보존.
- recovery_control: 불변 typed observation/scenario, 지연 명령과 잔류 경로, action-independent exogenous seed schedule.
- recovery_lifecycle/recovery_completion: fixed/verified/hold10 및 forecast, calibrated variants. 제어기에 truth 접근 불가.
- recovery_research: cycle1/2/3, 보정 유효성 검사, manifest/config/source SHA 고정, dev/test 분리, paired per-seed 불확실성.
- 보고서/도판은 저장 JSON에서 생성. 모든 실패·timeout·보정 무효 조건 포함.
### Must NOT have (guardrails, anti-slop, scope boundaries)
- 숨은 정답 수요 사용, test 결과를 본 후 정책 선택/수정, 부정 결과 누락, 기존 historical 결과 수정.
- 실제 장비/외부 서비스 조치, 기존 서버 종료, push/rebase/브랜치 삭제.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD + 프로젝트 관례의 실행 가능한 assertion self-tests (pytest 도입하지 않음).
- Evidence: <attemptDir>/task-<N>-recovery-research.<ext> (attemptDir = currentAttemptDir from 'omo ulw-loop status --json', .omo/evidence/ulw/<session>/<goalId>/a<attempt>; outside ulw-loop use .omo/evidence/)

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. 사용자 규칙과 Git 설정 반영
  References: 사용자 제공 전문; 기존 AGENTS.md.
  Acceptance: 로컬 user.name/email/hooksPath가 지정 값, 원문 규칙 보존.
  QA: Get-Content AGENTS.md, git -c safe.directory=E:/network/vagus config --local --get-regexp '^(user\.|core\.hooksPath)'.
  Commit: N (별도 커밋 요청 없음).
- [x] 2. 관측/진실 분리 시뮬레이터와 회귀 테스트
  References: 사용자 SourceCutMixin 스니펫; research_design 검토; 기존 simulation/metric_generator.py의 tick 계약.
  Acceptance: 흐름 보존, ECMP, 지연 적용/잔류 경로, seed 반복성, stale/missing/reordered 관측 검사.
  QA: python -m recovery_control.test_simulation; 정상 보존 및 잘못된 경로/지연 실패 사례. Evidence .omo/evidence/simulation.txt.
  Commit: N.
- [x] 3. 제어기와 보정/완료 기준 회귀 테스트
  References: 사용자 calibrated_*.py/cycle1.py; 독립 설계 검토의 SourceCut 유효 조건.
  Acceptance: 관측 기반 의사결정, invalid 보정 fallback, 동일 calibration 비교군, 오래된 관측으로 완료 불가.
  QA: python -m recovery_research.test_calibration 및 test_controllers; source transit/초기표본누락/잔류경로 사례. Evidence .omo/evidence/controllers.txt.
  Commit: N.
- [x] 4. 고정 평가와 통계·보고서 생성
  References: experiments/_resultmeta.py; 기존 ablation의 paired 목적; frozen manifest.
  Acceptance: test 이전 manifest 저장, source/config SHA 검증, 비교군마다 같은 scenario, 모든 seed/timeout 보존, paired bootstrap.
  QA: python -m recovery_research.test_runner; 반복결과 동일성, manifest 변경/중복실행/누락모드 거부. Evidence .omo/evidence/runner.txt.
  Commit: N.
- [x] 5. 재현 스크립트와 실제 평가
  References: scripts/selftest.sh; 이번 연구 패키지 CLI.
  Acceptance: 연구 selftests와 dev/test 평가 성공, 실패가 있으면 그대로 기록. 기존 서버/모델 변경 없음.
  QA: reproduce_research.sh 및 Windows Python CLI; 결과로 report/plot 생성. Evidence .omo/evidence/reproduction.txt.
  Commit: N.
- [x] 6. 한국어 연구 문서와 독립 최종 검토
  References: PDF 양우석/이재오2024, ETSI ZSM009-1/ENI005, Reitblatt2012, 생성된 결과.
  Acceptance: FINDINGS.md, PAPER_OUTLINE.md, PLAN.md, PROGRESS.md, docs/DESIGN_EVOLUTION.md, RELATED_WORK_RESEARCH.md, README에 근거/한계/재현법.
  QA: 링크/명령/수치 대조; 별도 검토자가 코드 및 결과 검증. Evidence .omo/evidence/final-review.md.
  Commit: N.

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit — 관측/truth 분리, 사전 manifest, 전 비교군·실패 포함 확인.
- [x] F2. Code quality review — 독립 방법론 검토 blocker 없음; Ruff 통과, 타입 오류 0. 명시성 경고 118은 FINDINGS에 공개.
- [x] F3. Real manual QA — 재현 CLI v1/v2 실제 실행, 17개 통합 테스트, 결과 PNG 직접 확인.
- [x] F4. Scope fidelity — 기존 코드 변경은 README와 selftest 통합뿐. 모델·기존 결과 유지. 원본 미확보 출처와 불리한 결과 공개.

## Commit strategy
커밋·push는 수행하지 않는다. 요청된 작성자 설정과 금지 규칙을 유지한다.

## Success criteria
실제 실행 결과로 판단 가능한 연구 산출물, 재현 가능한 비교, 실패를 숨기지 않는 한글 결론, 사용자 규칙 준수.
