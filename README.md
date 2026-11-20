# vagus

## ZSM 폐쇄 루프 자율 복구 — 규칙 · 계획기 · 학습 정책은 각각 무엇을 푸는가

> **vagus** — 미주신경. 자율신경계에서 의식적 판단을 거치지 않고 장기의 상태를 감지하고
> 조절하는 경로다. 관측 → 진단 → 조치 → 검증을 사람 없이 닫는다는 점에서 이 시스템이 하는
> 일과 같다.

---

## 한 줄 요약

ETSI ZSM 폐쇄 루프(관측 → 분석 → 결정 → 실행)를 단일 관리 도메인에 구현하고, 링크 혼잡을 사람 없이
복구할 때 **실패가 어디서 생기고 무엇이 그것을 푸는지**를 계층별로 분해한 연구 프로젝트다. 4노드와
Abilene(11노드) 시뮬레이션에서 규칙 · 모델 기반 계획기 · 강화학습 정책을 같은 조건으로 비교했다.

**결론: 진단 실패는 링크 단위 관측이, 조치 실패는 라우팅 모델 탐색이, 모델 불일치는 관측 기반 재추정이
풀었다. 학습 정책(MAML, PPO)은 두 규모 모두에서 규칙 수준을 넘지 못했다.**

---

## 연구 질문

이 리포지토리는 양우석·이재오, "ZSM/ENI 기반의 자율 네트워크 및 서비스 관리"(KNOM Review 27(2), 2024)가
정리한 참조 구조 중 **ZSM 단일 관리 도메인의 폐쇄 루프**를 구현한다.

> 규칙 기반 분석 계층이 근본 원인을 특정하지 못하거나 정해진 조치가 통하지 않는 조건에서,
> 학습 기반 지능 계층이 복구에 기여하는가?

| 가설 | 결과 |
| --- | --- |
| H1 단일 혼잡·용량 제약 없음 → 규칙만으로 충분하다 | 지지 |
| H2 용량 제약·다중 혼잡 → 풀이가 있는데도 규칙이 실패하는 시나리오가 있다 | 지지 |
| H3 그 구간에서 학습 정책이 규칙·계획기보다 낫다 | 기각 (이 설정에서) |

---

## 핵심 결과

수치는 안정적으로 복구한 수다. 용량·이중 혼잡과 Abilene은 오라클이 풀이를 찾은 것만, 수요 변화는 변화
뒤에도 풀이가 있는 시나리오의 전체 에피소드를 센다. 표는 결과 파일에서 생성된다.

<!-- AUTO:headline -->
| 실험 | 모델 없는 규칙 | 모델 기반 계획기 | 학습 정책 |
| --- | --- | --- | --- |
| 4노드 단일 혼잡 (50 ep) | 성공 100 %, TTR 7.08 | — | MAML 단독 14 % |
| 4노드 용량·이중 혼잡 (풀이 있는 51 시나리오) | v1 18, v2 26 | 49 | — |
| 4노드 수요 변화 (450 ep) | 89 | 고정 229 · 적응형 432 | — |
| Abilene (풀이 있는 187 ep) | 155 | 183 | PPO 21.0 (사전 등록) · 152.4 (사후 변형) |
<!-- /AUTO:headline -->

| 실패 | 원인 | 푼 것 |
| --- | --- | --- |
| 진단 실패 — 엉뚱한 링크 격리 | 노드 평균 지표로는 고장 링크가 가려지지 않는다 | 링크 단위 관측 (규칙 v1 → v2) |
| 조치 실패 — 격리하자 우회 링크가 포화 | 정답이 여러 링크 cost의 동시 변경이다 | 라우팅 모델 + 탐색 (계획기) |
| 모델 불일치 — 수요가 바뀌었는데 계획기가 멈춤 | 계획기의 수요 추정이 낡았다 | 관측 부하로 재추정 (적응형 계획기) |

학습 정책은 사전 등록 설정에서 실패했고(보상이 "아무것도 안 함"과 규칙을 구분하지 못했다), 보상을
평가 지표에 맞춘 사후 변형에서도 규칙 수준이었다. 계획기가 놓친 에피소드를 푼 학습 정책은 없다.

**상세 — 조건, 표, 해석, 한계: [`autonomous-network-mgmt/cowork/RESULTS.md`](autonomous-network-mgmt/cowork/RESULTS.md)**

---

## 시스템 — ZSM 폐쇄 루프

```
[Observe]   노드 메트릭(대역폭·지연·손실) + 링크 메트릭(이용률·손실·용량) 수집
     ↓
[Orient]    SLA 규칙으로 위반 노드 판정 → 근본 원인 링크 분석 (ZSM 분석 서비스)
     ↓
[Decide]    조치 결정 (ZSM 지능 서비스) — 아래 표의 결정 방식 중 하나
     ↓
[Act]       OSPF cost 변경 (ZSM 오케스트레이션)
     ↓
[Evaluate]  배포된 AI 모델 성능 추적 (ModelPerformanceTracker)
```

| 결정 방식 | 진단 | 조치 | 구현 |
| --- | --- | --- | --- |
| 규칙 v1 | 노드 지표로 링크 추정 | 원인 링크 cost 100 | `anomaly_detector.root_cause_analysis` |
| 규칙 v2 | 링크 직접 관측 (fault / overload / draining) | 고장 링크만 cost 100 | `root_cause_analysis_v2` |
| 계획기 v3 | v2와 같음 | 라우팅 모델로 cost 배치 탐색 | `routing_model.py` (4노드 전수), `scale_control.Planner` (국소 탐색) |
| 적응형 계획기 v4 | v2와 같음 | v3 + 관측 부하가 모델과 어긋나면 수요 재추정 | 위와 같음 |
| 학습 정책 | — | MAML few-shot (4노드), PPO (Abilene) | `agents/few_shot_agent.py`, `experiments/train_scale_ppo.py` |

**두 가지 실행 경로.** 4노드 실험은 HTTP 폐쇄 루프(`POST /auto-step`, Flask 가상 장비 + FastAPI 엔진)로,
Abilene 실험은 같은 물리를 쓰는 in-process 하네스(`simulation/netsim.py`)로 돌린다. 하네스는 HTTP 폐쇄
루프의 4노드 255 에피소드를 그대로 재생한다(`experiments/scale_benchmark.py --validate-k4`).

---

## 실행

```bash
# 의존성 (Python 3.11+)
pip install -r autonomous-network-mgmt/ai-engine/requirements.txt flask
```

```bash
# 자가 테스트 — 모듈 테스트, 문서 표와 결과 파일 대조 포함
autonomous-network-mgmt/scripts/selftest.sh --quick
```

```bash
# 4노드 HTTP 폐쇄 루프 — 터미널 1: 가상 장비(:5001)
cd autonomous-network-mgmt/simulation && SIM_CAPACITY=finite python3 mock_snmp_agent.py
```

```bash
# 터미널 2: AI 엔진(:8000)
cd autonomous-network-mgmt/ai-engine && python3 api_server.py
```

```bash
# 폐쇄 루프 한 사이클
curl -X POST "http://127.0.0.1:8000/auto-step?disable_maml=true&analytics_version=2"
```

```bash
# Abilene 비교 (서버 불필요)
cd autonomous-network-mgmt && python3 experiments/scale_benchmark.py --validate-k4
```

실험별 재현 명령은 [`RESULTS.md` §9](autonomous-network-mgmt/cowork/RESULTS.md), 런타임 구성(포트, Java
서비스, Docker)은 [`PROJECT_OVERVIEW.md`](autonomous-network-mgmt/cowork/PROJECT_OVERVIEW.md)에 있다.

---

## 프로젝트 구조

```
autonomous-network-mgmt/
├── simulation/
│   ├── metric_generator.py   # 4노드 가상 네트워크 (링크 스트레스, 최단경로, 선택적 용량 모델)
│   ├── netsim.py             # 임의 토폴로지 시뮬레이터 (같은 물리) — 4노드, Abilene
│   └── mock_snmp_agent.py    # 가상 장비 REST API (:5001)
├── ai-engine/
│   ├── api_server.py         # FastAPI (:8000) — 폐쇄 루프 /auto-step
│   ├── anomaly_detector.py   # SLA 진단, 근본 원인 분석 v1/v2 (보안 탐지기도 여기 — SECURITY.md)
│   ├── routing_model.py      # 4노드 계획기 (전수 탐색, 수요 재추정)
│   ├── scale_control.py      # 규모 확장 제어기: 규칙 v2, 국소 탐색 계획기, 탐색 오라클
│   ├── environment/          # 학습 환경 (network_env: 4노드, scale_env: Abilene)
│   └── agents/               # MAML / PPO 체크포인트 (scale/: Abilene PPO)
├── experiments/
│   ├── capacity_probe.py     # 용량·이중 혼잡
│   ├── demand_shift_probe.py # 수요 변화
│   ├── scale_benchmark.py    # Abilene 비교 + 4노드 재생 검증
│   ├── train_scale_ppo.py    # Abilene PPO 학습
│   ├── make_results_tables.py# 문서 표 생성·대조
│   └── results/              # 결과 JSON (측정 조건·seed·커밋 포함)
├── cowork/                   # 문서 (아래 문서 지도)
└── collector-service/, orchestrator-service/   # Java 마이크로서비스 경로 (연구 결과에는 쓰이지 않음)
```

---

## API (폐쇄 루프)

| 포트 | 메서드 | 경로 | 설명 |
| ---- | ------ | ---- | ---- |
| 8000 | `POST` | `/auto-step` | 폐쇄 루프 1 사이클. `?analytics_version=1~4`, `?disable_maml=`, `?disable_analytics=` |
| 8000 | `GET` | `/diagnose` | 분석 결과(근본 원인 포함) 단독 조회 |
| 8000 | `POST` | `/reset-buffer` | 에피소드 상태 초기화 |
| 5001 | `GET` | `/metrics`, `/metrics/links`, `/ospf/costs` | 노드 · 링크 관측, 현재 cost (순수 조회) |
| 5001 | `POST` | `/debug/tick`, `/debug/reset` | 시뮬레이션 시간 1스텝, 에피소드 리셋 (`{"seed": N}`) |
| 5001 | `POST` · `PUT` | `/debug/congestion/{link}`, `/debug/capacity/{link}`, `/debug/demand/{pair}` | 혼잡 · 용량 · 수요 주입 |

시간은 `POST /debug/tick`으로만 흐른다(`SIM_CLOCK=lockstep`, 기본) — 조회가 시뮬레이션을 가속하지 않는다.
`/debug/*`·`/reset-buffer`는 인증이 없고 CORS `*`, `0.0.0.0` 바인딩이다. 연구 데모 전용이며 외부 노출을
전제하지 않는다. 전체 엔드포인트는 `PROJECT_OVERVIEW.md` §4.

---

## 한계

시뮬레이션 결과이며 실환경(Mininet + FRR) 검증은 하지 않았다. 토폴로지는 두 개(4노드, Abilene), 학습
정책은 MAML과 PPO(MLP, 300k 스텝) 두 가지다. Abilene의 "풀이 없음"은 탐색 기반 추정이다. ENI 기능 블록과
다중 도메인은 구현하지 않았다. 상세는 [`RESULTS.md` §8](autonomous-network-mgmt/cowork/RESULTS.md).

---

## 문서 지도

| 문서 | 내용 |
| --- | --- |
| [`cowork/RESULTS.md`](autonomous-network-mgmt/cowork/RESULTS.md) | **결과 요약** — 연구 질문, 실험 4개, 실패 분해, 한계, 재현. 9월 측정과 이전 README의 서술은 부록에 원문 보존 |
| [`cowork/PROJECT_OVERVIEW.md`](autonomous-network-mgmt/cowork/PROJECT_OVERVIEW.md) | 코드 구조 · 런타임 · API 설명서 |
| [`cowork/ROADMAP.md`](autonomous-network-mgmt/cowork/ROADMAP.md) | 고도화 계획(§0~§5)과 실험 노트(§6, 날짜순) |
| [`cowork/CHANGELOG.md`](autonomous-network-mgmt/cowork/CHANGELOG.md) | 개발 일지 |
| [`cowork/SECURITY.md`](autonomous-network-mgmt/cowork/SECURITY.md) | 보안 탐지(OSPF LSA 위조, DDoS/포트스캔, CICDDoS2019 검증) — **연구 범위 밖 부속 구현** |
| `cowork/AUDIT_2026-09-09.md`, `FIX_PLAN.md`, `VISUALIZATION_PLAN.md`, `experiments/experiment_report.md` | 보관 문서 — 당시 연구 질문 기준 |

---

## 관련 표준

- 양우석·이재오, "ZSM/ENI 기반의 자율 네트워크 및 서비스 관리", KNOM Review 27(2), 2024,
  [doi:10.22670/knom.2024.27.2.12](https://doi.org/10.22670/knom.2024.27.2.12) — 이 프로젝트가
  구현 대상으로 삼는 참조 구조. 문서의 절 번호(§3.1.1.2 등)는 이 논문 기준이다
- [ETSI GS ZSM 002](https://www.etsi.org/deliver/etsi_gs/ZSM/001_099/002/) — Zero-touch network and Service Management; Reference Architecture
- [ETSI GS ENI 005](https://www.etsi.org/deliver/etsi_gs/ENI/001_099/005/) — Experiential Networked Intelligence; System Architecture
