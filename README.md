# Autonomous Network Management

## ZSM/ENI 기반 자율 네트워크 관리 시스템

---

## 한 줄 요약

ETSI ZSM/ENI 표준을 기반으로 AI가 네트워크 장애를 스스로 감지·분석·복구하는 폐쇄 루프 자동화 시스템

---

## 연구 배경 및 동기

현재 통신망 운용은 사람이 장애를 탐지하고 대응 절차를 수동으로 수행한다. 5G/6G 시대에 네트워크 복잡도가 기하급수적으로 증가함에 따라 ETSI는 두 가지 표준을 제정했다.

- **ETSI GS ZSM 002**: Zero-touch network and Service Management — 폐쇄 루프 자동화 아키텍처
- **ETSI GS ENI 007**: Experiential Networked Intelligence — 경험 기반 AI 적응 학습

그러나 이 표준 아키텍처를 실제로 구현하고 정량 검증한 사례는 드물다.

**핵심 연구 질문**

> MAML 기반 few-shot 에이전트가 ZSM Analytics 계층과 결합했을 때, 기존 DRL 기준선 대비 네트워크 복구 시간(TTR)을 얼마나 단축할 수 있는가?

---

## 시스템 아키텍처

### ZSM OODA 루프 매핑

```
[Observe]   SNMP 수집 → Kafka → Collector Service
     ↓
[Orient]    IsolationForest 이상 감지
            인접도 점수 기반 근본 원인 분석 (RCA)      ← ZSM Analytics 계층 (Clause 3.1.1.2)
     ↓
[Decide]    MAML few-shot 에이전트 adapt_and_predict()  ← ENI Intelligence 계층 (Clause 3.1.1.3)
            Analytics override: 고신뢰 RCA → Intelligence 결정 보정
     ↓
[Act]       OSPF cost 조정 → Mininet 라우터에 적용
     ↓
[Evaluate]  ModelPerformanceTracker 자가 진단           ← ZSM AI Model Evaluation (Clause 3.1.1.4)
```

### Analytics-Intelligence 계층 분리 (ZSM 핵심 설계 원칙)

| 계층                  | 담당 단계 | 구현                                            |
| --------------------- | --------- | ----------------------------------------------- |
| **Analytics 계층**    | Orient    | IsolationForest 이상 감지 + 인접도 기반 RCA     |
| **Intelligence 계층** | Decide    | MAML few-shot 에이전트 (inner-loop 실시간 적응) |

고신뢰 조건(`nodes_sharing_root ≥ 2`)을 만족하는 근본 원인이 식별되면 Analytics가 Intelligence 결정을 override하여 탐색 비용을 제거한다.

### 상태·행동 공간

- **상태 벡터** (14차원): `[대역폭×4, 지연×4, OSPF_cost×6]`
- **행동 공간** (30차원): `{10, 20, 50, 100, 200} × 6링크` 이산 공간

---

## 서비스 구성

```
autonomous-network-mgmt/
├── simulation/          # Mininet 가상 네트워크 (라우터 4개, 링크 6개)
│   ├── topology.py      # 네트워크 토폴로지 정의
│   ├── metric_generator.py  # 메트릭 시뮬레이션
│   └── mock_snmp_agent.py   # SNMP REST API (Flask)
│
├── collector-service/   # Java/Spring — SNMP 수집 → Kafka 발행
│   └── src/main/java/com/anm/collector/
│       ├── service/NetworkMetricCollector.java
│       ├── kafka/MetricPublisher.java
│       └── client/SnmpClient.java
│
├── orchestrator-service/ # Java/Spring — AI 결정을 Mininet에 실행
│   └── src/main/java/com/anm/orchestrator/
│       ├── service/OrchestrationService.java
│       ├── client/AiEngineClient.java
│       └── client/MininetClient.java
│
├── ai-engine/           # Python — 핵심 AI 엔진
│   ├── api_server.py    # Flask REST API
│   ├── anomaly_detector.py  # IsolationForest 이상 감지 + RCA
│   ├── reward.py        # 보상 함수 정의
│   ├── environment/
│   │   └── network_env.py   # Gym 네트워크 환경
│   └── agents/
│       ├── baseline_drl.py  # Baseline PPO
│       └── few_shot_agent.py # MAML few-shot 에이전트
│
├── experiments/         # 실험 스크립트 및 결과
│   ├── run_experiment.py
│   ├── ablation_study.py
│   ├── stress_test.py
│   └── results/
│
└── docker-compose.yml   # Kafka, PostgreSQL, Redis, Simulation 통합
```

### 인프라 구성 (docker-compose)

- **Kafka + Zookeeper**: 메트릭 스트리밍 파이프라인
- **PostgreSQL**: 메트릭 및 실험 결과 저장
- **Redis**: 캐싱
- **Simulation**: Mininet 기반 가상 네트워크 (privileged 컨테이너)

---

## AI 알고리즘

### MAML (Model-Agnostic Meta-Learning)

"빠르게 적응할 수 있는 초기값"을 meta-학습한다. 네트워크 환경에서 링크별 혼잡 시나리오를 task로 정의하고, inner-loop에서 support buffer(최대 32 transition)로 실시간 적응한다.

### IsolationForest 기반 RCA

근본 원인 점수: `score(link) = (-shared_violated_nodes, ospf_cost)`

SLA를 위반한 양 엔드포인트를 공유하는 링크를 근본 원인으로 판별한다.

### 혼잡 시뮬레이션 모델

- 링크 스트레스: `s_{t+1} = s_t × 0.9 + load_s + cong_s + noise`
- 우회 임계값: OSPF cost ≥ 100 → `cong_s = 0` (트래픽 우회)
- SLA 기준: 지연 < 50ms, 패킷손실 < 1%

---

## 실험 결과

### 주요 비교 (50 에피소드)

| 시스템                               | Avg TTR  | 성공률   | 비고                  |
| ------------------------------------ | -------- | -------- | --------------------- |
| Baseline PPO                         | 200.0    | 0%       | ~50,000 에피소드 필요 |
| MAML v1 (Analytics 미적용)           | 12.41    | 96.7%    |                       |
| **본 시스템 (MAML + ZSM Analytics)** | **3.78** | **100%** | ~100 에피소드         |

- Baseline PPO 대비 **98.1% TTR 단축**
- MAML v1 대비 **69.5% TTR 단축**

### 일반화 검증

| 링크  | 그룹  | Avg TTR |
| ----- | ----- | ------- |
| r1-r4 | TEST  | 3.43    |
| r3-r4 | TEST  | 3.94    |
| r1-r2 | TRAIN | 3.80    |
| r2-r3 | TRAIN | 3.62    |

학습에 없던 TEST 링크와 TRAIN 링크의 TTR이 동일 수준 → **제로 일반화 격차**

### Ablation Study

| 모드                 | Avg TTR | 성공률 |
| -------------------- | ------- | ------ |
| Analytics only       | 3.93    | 100%   |
| MAML only            | 13.13   | 27%    |
| Combined (본 시스템) | 4.20    | 100%   |

**Analytics 계층이 핵심 성능 동인**임을 정량 실증.

### TTR 이론적 하한

OSPF cost 변경 후 스트레스 감소는 물리적 시정수 `τ = -1/ln(0.9) ≈ 9.5 step`에 의해 결정된다. N=3에서 지연 < 50ms SLA를 달성하므로 **물리적 복구 하한이 존재**한다.

---

## 핵심 기여

1. **ZSM Analytics-Intelligence 공동 최적화**: Analytics의 고신뢰 근본 원인이 Intelligence를 override하여 항상 최적 1차 행동을 보장
2. **샘플 효율**: ~100 에피소드로 ~50,000 에피소드가 필요한 기준선 능가
3. **완전한 일반화**: 학습에 없던 링크(TEST)에서 TRAIN과 동일한 TTR 달성
4. **100% 근본 원인 정확도**: 첫 번째 OODA 사이클에서 혼잡 링크 완벽 식별
5. **표준 완전 구현**: ETSI ZSM 002 Clause 3.1.1.2~3.1.1.4를 코드 레벨로 구현 및 검증

---

## 한계 및 향후 연구

### 한계

- Mininet 시뮬레이션 기반 — 실제 하드웨어 라우터 검증 필요
- 단일 혼잡 주입 시나리오 — 다중 동시 장애 미검증
- MAML inner-loop의 실질 기여는 지속 버퍼 환경에서만 유효

### 향후 연구 방향

- 다중 도메인 ZSM 시나리오 확장
- Graph Neural Network 기반 상태 표현으로 대규모 토폴로지 적용
- 실망 OSPF 환경에서의 검증

---

## 관련 표준

- [ETSI GS ZSM 002](https://www.etsi.org/deliver/etsi_gs/ZSM/001_099/002/) — Zero-touch network and Service Management
- [ETSI GS ENI 007](https://www.etsi.org/deliver/etsi_gs/ENI/001_099/007/) — Experiential Networked Intelligence

---

## 실험 상세 보고서

[experiments/experiment_report.md](experiments/experiment_report.md) 참조
